"""Leitura estrita de resultados portáteis da etapa 09, sem inferência.

O conteúdo do ZIP é tratado somente como dados. Nenhum membro é extraído,
executado ou importado. Os hashes confirmam integridade, não autoria nem a
execução efetiva do modelo declarado nos metadados.
"""

import base64
from hashlib import sha256
from io import BytesIO
import json
import math
import re
import stat
import struct
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from contratos_vetorizacao import canonico
from vetorizacao import (
    ETAPA, MAX_BLOCOS, MAX_REPRESENTACOES, SCHEMA_VERSION,
    validar_vetorizacao,
)


MAX_BYTES_ZIP = 64 * 1024 * 1024
MAX_BYTES_ARQUIVO = 96 * 1024 * 1024
MAX_BYTES_PACOTE = 128 * 1024 * 1024
MAX_MEMBROS = MAX_BLOCOS + MAX_REPRESENTACOES + 3
MAX_PROFUNDIDADE_JSON = 64
_JSONS = frozenset({"manifesto.json", "vetorizacao.json", "integridade.json"})
_BINARIO = re.compile(r"(?:artefatos|representacoes)/[0-9]{6}\.float32\.bin\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CAMPOS_ARMAZENAMENTO = frozenset({"formato", "dimensao", "bytes", "sha256_bytes"})


class ErroImportacaoVetores(ValueError):
    """O pacote não corresponde a um resultado íntegro da etapa 09."""


class ErroLimiteImportacao(ErroImportacaoVetores):
    """Um limite de tamanho ou complexidade foi excedido, sem truncamento."""


class _InflacaoContada:
    """Conta a saída do zlib antes de ZipExtFile truncar pela declaração."""

    def __init__(self, decompressor, limite):
        self._decompressor = decompressor
        self.limite = limite
        self.total = 0

    def __getattr__(self, nome):
        return getattr(self._decompressor, nome)

    def _contar(self, dados):
        self.total += len(dados)
        _exigir(self.total <= self.limite,
                "Deflate produz mais bytes que o tamanho declarado do membro.")
        return dados

    def decompress(self, *args, **kwargs):
        return self._contar(self._decompressor.decompress(*args, **kwargs))

    def flush(self, *args, **kwargs):
        return self._contar(self._decompressor.flush(*args, **kwargs))


def _exigir(condicao, mensagem):
    if not condicao:
        raise ErroImportacaoVetores(mensagem)


def _json_estrito(dados):
    """Rejeita ambiguidades e profundidade excessiva antes de montar objetos."""
    texto = dados.decode("utf-8")
    profundidade, em_string, escape = 0, False, False
    for caractere in texto:
        if em_string:
            if escape:
                escape = False
            elif caractere == "\\":
                escape = True
            elif caractere == '"':
                em_string = False
        elif caractere == '"':
            em_string = True
        elif caractere in "[{":
            profundidade += 1
            if profundidade > MAX_PROFUNDIDADE_JSON:
                raise ErroLimiteImportacao("JSON excede o limite de profundidade.")
        elif caractere in "]}":
            profundidade -= 1

    def objeto(pares):
        valor = {}
        for chave, conteudo in pares:
            _exigir(chave not in valor, "JSON contém chave duplicada.")
            valor[chave] = conteudo
        return valor

    def constante(_valor):
        raise ErroImportacaoVetores("JSON contém número não finito.")

    def decimal(valor):
        numero = float(valor)
        _exigir(math.isfinite(numero), "JSON contém número não finito.")
        return numero

    return json.loads(texto, object_pairs_hook=objeto,
                      parse_constant=constante, parse_float=decimal)


def _diretorio_limitado(dados):
    """Conta o diretório antes de ZipFile alocar um objeto por membro.

    A contagem declarada no EOCD pode mentir: percorremos também os registros
    reais. São aceitos ZIPs comuns e o ZIP64 de contagem emitido pelo Python;
    arquivos multidisco, executáveis anexados e extensões desconhecidas não
    pertencem ao formato portátil do Língua.
    """
    fim = dados.rfind(b"PK\x05\x06", max(0, len(dados) - 65557))
    _exigir(fim >= 0 and fim + 22 <= len(dados), "Diretório do ZIP ausente ou inválido.")
    _, disco, disco_diretorio, neste_disco, declarados, tamanho, inicio, comentario = \
        struct.unpack_from("<4s4H2IH", dados, fim)
    _exigir(fim + 22 + comentario == len(dados) and disco == disco_diretorio == 0
            and neste_disco == declarados, "ZIP multidisco, anexado ou incompleto não permitido.")
    fim_diretorio = fim
    zip64 = fim >= 20 and dados[fim - 20:fim - 16] == b"PK\x06\x07"
    _exigir(zip64 or (tamanho != 0xffffffff and inicio != 0xffffffff),
            "Localizador ZIP64 ausente.")
    if zip64:
        declarados32, tamanho32, inicio32 = declarados, tamanho, inicio
        localizador = fim - 20
        _exigir(localizador >= 0, "Localizador ZIP64 inválido.")
        assinatura, disco64, posicao64, discos64 = struct.unpack_from("<4sIQI", dados, localizador)
        _exigir(assinatura == b"PK\x06\x07" and disco64 == 0 and discos64 == 1
                and posicao64 + 56 == localizador, "ZIP64 fora do formato portátil suportado.")
        assinatura, extensao, _, _, disco64, diretorio64, neste_disco, declarados, tamanho, inicio = \
            struct.unpack_from("<4sQ2H2I4Q", dados, posicao64)
        _exigir(assinatura == b"PK\x06\x06" and extensao == 44
                and disco64 == diretorio64 == 0 and neste_disco == declarados,
                "Diretório ZIP64 inválido.")
        _exigir(declarados32 == min(declarados, 65535)
                and tamanho32 == min(tamanho, 0xffffffff)
                and inicio32 == min(inicio, 0xffffffff),
                "Diretórios ZIP e ZIP64 divergentes.")
        fim_diretorio = posicao64
    if declarados > MAX_MEMBROS:
        raise ErroLimiteImportacao("ZIP excede o limite de membros da etapa 09.")
    _exigir(inicio + tamanho == fim_diretorio, "Posição ou tamanho do diretório ZIP inválido.")
    posicao, quantidade = inicio, 0
    while posicao < fim_diretorio:
        _exigir(posicao + 46 <= fim_diretorio and dados[posicao:posicao + 4] == b"PK\x01\x02",
                "Registro do diretório ZIP inválido.")
        nome, extra, observacao = struct.unpack_from("<3H", dados, posicao + 28)
        posicao += 46 + nome + extra + observacao
        _exigir(posicao <= fim_diretorio, "Registro do diretório ZIP incompleto.")
        quantidade += 1
        if quantidade > MAX_MEMBROS:
            raise ErroLimiteImportacao("ZIP excede o limite real de membros da etapa 09.")
    _exigir(quantidade == declarados, "Contagem do diretório ZIP divergente.")
    _exigir(not quantidade or dados.startswith(b"PK\x03\x04"),
            "Dados prefixados ao ZIP não permitidos.")


def _membros(arquivo):
    membros = arquivo.infolist()
    if len(membros) > MAX_MEMBROS:
        raise ErroLimiteImportacao("ZIP excede o limite de membros da etapa 09.")
    nomes = set()
    tamanho = 0
    for membro in membros:
        nome = membro.filename
        _exigir(nome not in nomes, "ZIP contém membros duplicados.")
        nomes.add(nome)
        _exigir(nome == membro.orig_filename and
                (nome in _JSONS or _BINARIO.fullmatch(nome) is not None),
                "ZIP contém arquivo extra ou caminho inválido.")
        modo = stat.S_IFMT(membro.external_attr >> 16)
        _exigir(not membro.is_dir() and not membro.external_attr & 0x10
                and modo in (0, stat.S_IFREG)
                and not membro.flag_bits & (1 | 64 | 8192)
                and membro.compress_type in (ZIP_STORED, ZIP_DEFLATED),
                "ZIP contém tipo, criptografia ou compressão não permitidos.")
        _exigir(membro.compress_type != ZIP_STORED or membro.compress_size == membro.file_size,
                "Membro sem compressão declara tamanhos divergentes.")
        if membro.file_size > MAX_BYTES_ARQUIVO:
            raise ErroLimiteImportacao("Arquivo do ZIP excede 96 MiB.")
        tamanho += membro.file_size
        if tamanho > MAX_BYTES_PACOTE:
            raise ErroLimiteImportacao("ZIP descompactado excede 128 MiB.")
    _exigir("manifesto.json" in nomes, "ZIP não contém manifesto.json.")
    _exigir(("vetorizacao.json" in nomes) == ("integridade.json" in nomes),
            "Resultado Colab exige JSON completo e integridade juntos.")
    return {membro.filename: membro for membro in membros}


def _ler(arquivo, membro):
    with arquivo.open(membro) as origem:
        # O pedido também deve respeitar a declaração já limitada. Um deflate
        # adulterado pode produzir muito mais que file_size antes de ZipFile
        # truncar a resposta; pedir 96 MiB para um vetor pequeno amplificaria
        # o consumo de memória apesar dos limites do diretório.
        if membro.compress_type == ZIP_DEFLATED:
            # ZipExtFile trunca pela declaração antes de expor a resposta.
            # Contar a saída interna do zlib detecta também excedentes menores
            # que o bloco mínimo de leitura de ZipExtFile. Mudanças nessa
            # interface interna falham explicitamente, em vez de aceitar dados.
            decompressor = getattr(origem, "_decompressor", None)
            _exigir(decompressor is not None, "Leitor deflate não permite conferir o tamanho real.")
            contador = _InflacaoContada(decompressor, membro.file_size)
            origem._decompressor = contador
        dados = origem.read(membro.file_size + 1)
        if membro.compress_type == ZIP_DEFLATED:
            _exigir(contador.total == membro.file_size and getattr(decompressor, "eof", False)
                    and getattr(decompressor, "unused_data", None) == b""
                    and getattr(decompressor, "unconsumed_tail", None) == b""
                    and getattr(origem, "_compress_left", None) == 0,
                    "Fluxo deflate não termina no tamanho declarado do membro.")
    if len(dados) > MAX_BYTES_ARQUIVO:
        raise ErroLimiteImportacao("Arquivo do ZIP excede 96 MiB.")
    _exigir(len(dados) == membro.file_size,
            "Tamanho real diverge do diretório do ZIP.")
    return dados


def _armazenamento(valor, dimensao):
    _exigir(type(valor) is dict and set(valor) == _CAMPOS_ARMAZENAMENTO,
            "Manifesto precisa separar os bytes base64 dos metadados vetoriais.")
    _exigir(valor["formato"] == "float32_le"
            and type(valor["dimensao"]) is int and valor["dimensao"] == dimensao
            and type(valor["bytes"]) is int and valor["bytes"] == dimensao * 4
            and type(valor["sha256_bytes"]) is str
            and _SHA256.fullmatch(valor["sha256_bytes"]) is not None,
            "Formato, dimensão, tamanho ou hash binário inválido.")


def _plano(manifesto, permitir_simulado):
    _exigir(type(manifesto) is dict
            and set(manifesto) == {"formato", "versao", "registro", "arquivos"}
            and manifesto["formato"] == "lingua_etapa09_zip"
            and manifesto["versao"] == "1.0.0", "Contrato do manifesto não suportado.")
    registro = manifesto["registro"]
    _exigir(type(registro) is dict and registro.get("schema_version") == SCHEMA_VERSION
            and registro.get("etapa") == ETAPA, "Contrato da etapa 09 não suportado.")
    _exigir(type(registro.get("validacao")) is dict
            and registro["validacao"].get("estado") == "valido",
            "Somente resultados concluídos e válidos podem ser importados.")
    modelo = registro.get("modelo")
    _exigir(type(modelo) is dict and type(modelo.get("dimensao")) is int
            and 1 <= modelo["dimensao"] <= 8192, "Dimensão do modelo inválida.")
    natureza = modelo.get("natureza")
    _exigir(natureza == "inferencia_real" or
            (permitir_simulado and natureza == "simulado_teste"),
            "Resultado simulado não pode ser importado fora dos testes.")
    artefatos, representacoes = registro.get("artefatos"), registro.get("representacoes")
    _exigir(type(artefatos) is list and type(representacoes) is list,
            "Listas de artefatos e representações inválidas.")
    if len(artefatos) > MAX_BLOCOS or len(representacoes) > MAX_REPRESENTACOES:
        raise ErroLimiteImportacao("Resultado excede os limites de artefatos ou representações.")
    plano, ids_artefatos, ids_representacoes, blocos = [], set(), set(), 0
    for indice, artefato in enumerate(artefatos):
        _exigir(type(artefato) is dict and type(artefato.get("id")) is str
                and bool(artefato["id"].strip()) and artefato["id"] not in ids_artefatos,
                "Identidade de artefato inválida ou duplicada.")
        ids_artefatos.add(artefato["id"])
        valor = artefato.get("armazenamento")
        _armazenamento(valor, modelo["dimensao"])
        plano.append(("artefato", artefato["id"], f"artefatos/{indice:06d}.float32.bin", valor))
    for indice, representacao in enumerate(representacoes):
        _exigir(type(representacao) is dict and type(representacao.get("id")) is str
                and bool(representacao["id"].strip()) and representacao["id"] not in ids_representacoes,
                "Identidade de representação inválida ou duplicada.")
        ids_representacoes.add(representacao["id"])
        _exigir(type(representacao.get("blocos")) is list and "vetor" in representacao,
                "Representação sem blocos ou vetor declarado.")
        blocos += len(representacao["blocos"])
        if blocos > MAX_BLOCOS:
            raise ErroLimiteImportacao("Resultado excede o limite de blocos.")
        valor = representacao["vetor"]
        if valor is not None:
            _armazenamento(valor, modelo["dimensao"])
            plano.append(("representacao", representacao["id"],
                          f"representacoes/{indice:06d}.float32.bin", valor))
    esperado = [{"tipo": tipo, "id": identificacao, "caminho": caminho, **valor}
                for tipo, identificacao, caminho, valor in plano]
    _exigir(canonico(manifesto["arquivos"]) == canonico(esperado),
            "Índice de arquivos diverge dos IDs, caminhos, metadados ou ordem do registro.")
    return registro, plano


def _integridade(valor, registro, membros, arquivo):
    _exigir(type(valor) is dict
            and set(valor) == {"formato", "versao", "execucao_id", "contexto_execucao_id", "arquivos"}
            and valor["formato"] == "lingua_etapa09_colab_resultado"
            and valor["versao"] == "1.0.0"
            and valor["execucao_id"] == registro["execucao_id"]
            and valor["contexto_execucao_id"] == registro["contexto_execucao_id"],
            "Contrato ou origem da integridade Colab divergente.")
    hashes = valor["arquivos"]
    _exigir(type(hashes) is dict and set(hashes) == set(membros) - {"integridade.json"},
            "Integridade Colab não cobre exatamente os arquivos do resultado.")
    for nome, metadados in hashes.items():
        dados = _ler(arquivo, membros[nome])
        _exigir(type(metadados) is dict and set(metadados) == {"bytes", "sha256"}
                and type(metadados["bytes"]) is int and metadados["bytes"] == len(dados)
                and type(metadados["sha256"]) is str
                and metadados["sha256"] == sha256(dados).hexdigest(),
                "Hash ou tamanho divergente na integridade Colab.")


def ler_resultado_zip(dados: bytes, *, permitir_simulado=False):
    """Reconstitui e revalida um registro 09; nunca carrega um modelo.

    Aceita o ZIP do aplicativo ou o resultado ampliado do Colab. A permissão
    para simulados é exclusiva de testes controlados e deve ser deliberada.
    """
    _exigir(type(dados) is bytes, "ZIP deve ser enviado como bytes.")
    _exigir(type(permitir_simulado) is bool, "Permissão para simulados deve ser booleana.")
    if len(dados) > MAX_BYTES_ZIP:
        raise ErroLimiteImportacao("ZIP excede 64 MiB.")
    try:
        _diretorio_limitado(dados)
        with ZipFile(BytesIO(dados)) as arquivo:
            membros = _membros(arquivo)
            manifesto = _json_estrito(_ler(arquivo, membros["manifesto.json"]))
            registro, plano = _plano(manifesto, permitir_simulado)
            nomes = {"manifesto.json", *(caminho for _, _, caminho, _ in plano)}
            colab = "integridade.json" in membros
            if colab:
                nomes.update({"vetorizacao.json", "integridade.json"})
            _exigir(set(membros) == nomes,
                    "ZIP contém binários ausentes, extras ou caminhos divergentes do manifesto.")
            if colab:
                integridade = _json_estrito(_ler(arquivo, membros["integridade.json"]))
                _integridade(integridade, registro, membros, arquivo)
            for _, _, caminho, armazenamento in plano:
                binario = _ler(arquivo, membros[caminho])
                _exigir(len(binario) == armazenamento["bytes"]
                        and sha256(binario).hexdigest() == armazenamento["sha256_bytes"],
                        "Vetor binário diverge de seu tamanho ou hash.")
                armazenamento["base64"] = base64.b64encode(binario).decode("ascii")
            if colab:
                completo = _json_estrito(_ler(arquivo, membros["vetorizacao.json"]))
                _exigir(canonico(completo) == canonico(registro),
                        "JSON completo diverge do manifesto e dos binários reconstituídos.")
            validar_vetorizacao(registro)
            return registro
    except ErroImportacaoVetores:
        raise
    except MemoryError as erro:
        raise ErroLimiteImportacao("Memória disponível insuficiente para validar o ZIP.") from erro
    except Exception as erro:
        # Fronteira de dados externos: erros do ZIP, JSON ou de campos de
        # contratos anteriores viram um diagnóstico estável, sem execução.
        raise ErroImportacaoVetores("ZIP ou registro vetorial inválido; importação recusada.") from erro
