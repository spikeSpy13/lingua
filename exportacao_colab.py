"""Pacotes portáteis para executar somente a etapa 09 no Google Colab.

Exportar não baixa modelos nem carrega dependências de inferência. A origem 08
é revalidada, e o notebook confere os hashes do código antes de importá-lo.
"""

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from contratos_vetorizacao import (
    ErroConfiguracao, ErroEntrada, ErroLimite, canonico, configuracao_vetorizacao,
    hash_json,
)
from unidades_contexto import ErroContexto, validar_unidades_contexto


FORMATO = "lingua_etapa09_colab"
VERSAO = "1.0.0"
MAX_BYTES_ZIP = 64 * 1024 * 1024
MAX_BYTES_ARQUIVO = 96 * 1024 * 1024
MAX_BYTES_PACOTE = 128 * 1024 * 1024
NOME_NOTEBOOK = "lingua_etapa09_colab.ipynb"
MODULOS = (
    "preparacao.py", "segmentacao.py", "anotacao.py", "sintaxe_entidades.py",
    "catalogo_regras.py", "regras_detectores.py", "regras_linguisticas.py",
    "unidades_contexto.py", "contratos_vetorizacao.py", "vetorizacao.py",
    "embeddings_e5.py", "persistencia_vetores.py", "exportacao_colab.py",
)
ARQUIVOS_PACOTE = frozenset((*MODULOS, "requirements-embeddings.txt", NOME_NOTEBOOK,
                            "contexto.json", "configuracao.json", "LEIA-ME.txt", "manifesto.json"))
_RAIZ = Path(__file__).resolve().parent
_CAMPOS_CONFIG = ("perfil", "max_tokens", "tamanho_lote", "agregar")


class ErroPacoteColab(ValueError):
    """O pacote não passou nas verificações de estrutura e integridade."""


def _json_bytes(dados):
    def objeto(pares):
        valor = {}
        for chave, conteudo in pares:
            if chave in valor:
                raise ErroPacoteColab("JSON contém chave duplicada.")
            valor[chave] = conteudo
        return valor

    def constante(_valor):
        raise ErroPacoteColab("JSON contém número não finito.")

    try:
        return json.loads(dados.decode("utf-8"), object_pairs_hook=objeto,
                          parse_constant=constante)
    except (UnicodeError, ValueError, TypeError, RecursionError) as erro:
        if isinstance(erro, ErroPacoteColab):
            raise
        raise ErroPacoteColab("Arquivo JSON inválido ou fora dos limites.") from erro


def _validar_zip_bruto(dados):
    """Somente stdlib; esta função também é incorporada ao notebook confiável."""
    if type(dados) is not bytes or len(dados) > MAX_BYTES_ZIP:
        raise ErroPacoteColab("ZIP deve conter bytes e respeitar o limite de 64 MiB.")
    try:
        with ZipFile(BytesIO(dados)) as arquivo:
            membros = arquivo.infolist()
            nomes = [m.filename for m in membros]
            if len(nomes) != len(set(nomes)):
                raise ErroPacoteColab("ZIP contém membros duplicados.")
            if set(nomes) != ARQUIVOS_PACOTE:
                raise ErroPacoteColab("ZIP contém arquivos ausentes, extras ou caminhos inválidos.")
            tamanho = 0
            for membro in membros:
                nome = membro.filename
                modo = stat.S_IFMT(membro.external_attr >> 16)
                if ("/" in nome or "\\" in nome or nome in (".", "..")
                        or membro.is_dir() or modo not in (0, stat.S_IFREG)
                        or membro.flag_bits & 1
                        or membro.compress_type not in (ZIP_STORED, ZIP_DEFLATED)):
                    raise ErroPacoteColab("ZIP contém caminho, tipo ou compressão não permitidos.")
                if membro.file_size > MAX_BYTES_ARQUIVO:
                    raise ErroPacoteColab("Arquivo do ZIP excede 96 MiB.")
                tamanho += membro.file_size
                if tamanho > MAX_BYTES_PACOTE:
                    raise ErroPacoteColab("ZIP descompactado excede 128 MiB.")
            payloads = {m.filename: arquivo.read(m) for m in membros}
            if any(len(payloads[m.filename]) != m.file_size for m in membros):
                raise ErroPacoteColab("Tamanho real diverge do diretório do ZIP.")
    except (BadZipFile, OSError, EOFError, RuntimeError, NotImplementedError) as erro:
        raise ErroPacoteColab("ZIP inválido ou não legível.") from erro
    manifesto = _json_bytes(payloads["manifesto.json"])
    if (type(manifesto) is not dict
            or set(manifesto) != {"formato", "versao", "origem", "arquivos"}
            or manifesto["formato"] != FORMATO or manifesto["versao"] != VERSAO):
        raise ErroPacoteColab("Contrato do manifesto não reconhecido.")
    origem = manifesto["origem"]
    if (type(origem) is not dict
            or set(origem) != {"documento_id", "contexto_execucao_id", "contexto_sha256"}
            or not ((type(origem["documento_id"]) is str and origem["documento_id"].strip())
                    or (type(origem["documento_id"]) is int and origem["documento_id"] >= 0))
            or type(origem["contexto_execucao_id"]) is not str or not origem["contexto_execucao_id"].strip()
            or type(origem["contexto_sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", origem["contexto_sha256"]) is None):
        raise ErroPacoteColab("Identificação da origem ausente ou inválida.")
    hashes = manifesto["arquivos"]
    if type(hashes) is not dict or set(hashes) != ARQUIVOS_PACOTE - {"manifesto.json"}:
        raise ErroPacoteColab("Manifesto não cobre todos os arquivos do pacote.")
    for nome, metadados in hashes.items():
        conteudo = payloads[nome]
        if (type(metadados) is not dict or set(metadados) != {"bytes", "sha256"}
                or type(metadados["bytes"]) is not int or metadados["bytes"] != len(conteudo)
                or type(metadados["sha256"]) is not str
                or metadados["sha256"] != sha256(conteudo).hexdigest()):
            raise ErroPacoteColab(f"Tamanho ou hash divergente: {nome}.")
    return payloads, manifesto


def _origem_validada(contexto):
    try:
        relatorio = validar_unidades_contexto(contexto)
        if relatorio.get("pronto_para_etapa_09") is not True:
            raise ErroEntrada("A origem da etapa 08 não está pronta para a etapa 09.")
    except (ErroContexto, ValueError, TypeError, KeyError, RecursionError) as erro:
        if isinstance(erro, ErroEntrada):
            raise
        raise ErroEntrada(f"Origem da etapa 08 inválida: {erro}") from erro


def _configuracao(configuracao=None):
    normalizada = configuracao_vetorizacao(configuracao)
    return {campo: normalizada[campo] for campo in _CAMPOS_CONFIG}


def _validar_payloads(payloads, manifesto):
    contexto = _json_bytes(payloads["contexto.json"])
    try:
        _origem_validada(contexto)
    except ErroEntrada as erro:
        raise ErroPacoteColab(f"Origem inválida no pacote: {erro}") from erro
    origem = manifesto["origem"]
    if (contexto["documento_id"] != origem["documento_id"]
            or contexto["execucao_id"] != origem["contexto_execucao_id"]
            or hash_json(contexto) != origem["contexto_sha256"]):
        raise ErroPacoteColab("A origem contextual diverge do manifesto.")
    configuracao = _json_bytes(payloads["configuracao.json"])
    if type(configuracao) is not dict or set(configuracao) != set(_CAMPOS_CONFIG):
        raise ErroPacoteColab("configuracao.json exige os quatro campos de execução.")
    try:
        normalizada = _configuracao(configuracao)
    except ErroConfiguracao as erro:
        raise ErroPacoteColab(f"Configuração inválida no pacote: {erro}") from erro
    if normalizada != configuracao:
        raise ErroPacoteColab("A configuração do pacote não está normalizada.")
    return contexto, configuracao


def validar_pacote_colab(dados):
    """Valida os membros, hashes, configuração e toda a origem 08 sem modelo."""
    payloads, manifesto = _validar_zip_bruto(dados)
    _validar_payloads(payloads, manifesto)
    return deepcopy(manifesto)


def _extrair_payloads(payloads, destino):
    destino = Path(destino)
    if destino.exists() or destino.is_symlink() or not destino.parent.is_dir():
        raise ErroPacoteColab("Destino deve ser novo, em uma pasta existente.")
    temporario = Path(tempfile.mkdtemp(prefix=".lingua-colab-", dir=destino.parent))
    try:
        for nome, conteudo in payloads.items():
            # Os nomes são planos e pertencem à lista fechada já validada.
            (temporario / nome).write_bytes(conteudo)
        if destino.exists() or destino.is_symlink():
            raise ErroPacoteColab("Destino deixou de estar disponível.")
        os.rename(temporario, destino)
    finally:
        if temporario.exists():
            shutil.rmtree(temporario)


def extrair_pacote_colab(dados, destino):
    """Escreve somente após revalidar tudo; nunca usa ZipFile.extractall."""
    payloads, manifesto = _validar_zip_bruto(dados)
    _validar_payloads(payloads, manifesto)
    _extrair_payloads(payloads, destino)
    return deepcopy(manifesto)


def _zip_payloads(payloads):
    destino = BytesIO()
    with ZipFile(destino, "w", compression=ZIP_DEFLATED) as arquivo:
        for nome in sorted(payloads):
            membro = ZipInfo(nome, date_time=(1980, 1, 1, 0, 0, 0))
            membro.compress_type = ZIP_DEFLATED
            membro.create_system = 3
            membro.external_attr = (stat.S_IFREG | 0o644) << 16
            arquivo.writestr(membro, payloads[nome])
    return destino.getvalue()


def _celula(tipo, texto):
    celula = {"cell_type": tipo, "metadata": {}, "source": texto.splitlines(keepends=True)}
    if tipo == "code":
        celula.update(execution_count=None, outputs=[])
    return celula


def exportar_notebook():
    """Notebook genérico, sem documento, tokens privados ou revisões inventadas."""
    hashes_codigo = {nome: sha256((_RAIZ / nome).read_bytes()).hexdigest()
                     for nome in (*MODULOS, "requirements-embeddings.txt")}
    bootstrap = (
        "from hashlib import sha256\nfrom io import BytesIO\n"
        "import json, os, re, shutil, stat, sys, tempfile\n"
        "from pathlib import Path\n"
        "from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile\n"
        "from google.colab import files\n\n"
        "class ErroPacoteColab(ValueError):\n    pass\n\n"
        f"FORMATO = {FORMATO!r}\nVERSAO = {VERSAO!r}\n"
        f"MAX_BYTES_ZIP = {MAX_BYTES_ZIP}\nMAX_BYTES_ARQUIVO = {MAX_BYTES_ARQUIVO}\n"
        f"MAX_BYTES_PACOTE = {MAX_BYTES_PACOTE}\n"
        f"ARQUIVOS_PACOTE = frozenset({sorted(ARQUIVOS_PACOTE)!r})\n"
        f"HASHES_CODIGO_CONFIAVEIS = {hashes_codigo!r}\n\n"
        + inspect.getsource(_json_bytes) + "\n" + inspect.getsource(_validar_zip_bruto) + "\n"
        + inspect.getsource(_extrair_payloads) + "\n"
        + "enviados = files.upload()\n"
        "if len(enviados) != 1:\n    raise ValueError('Envie somente o ZIP exportado pelo Língua.')\n"
        "nome_zip, dados_zip = next(iter(enviados.items()))\n"
        "payloads, manifesto = _validar_zip_bruto(dados_zip)\n"
        "for nome, hash_esperado in HASHES_CODIGO_CONFIAVEIS.items():\n"
        "    if sha256(payloads[nome]).hexdigest() != hash_esperado:\n"
        "        raise ValueError('Código diferente do notebook confiável: ' + nome + "
        "'. Baixe novamente notebook e ZIP da mesma versão do Língua.')\n"
        "PASTA_PACOTE = Path(tempfile.mkdtemp(prefix='lingua-colab-')) / 'pacote'\n"
        "_extrair_payloads(payloads, PASTA_PACOTE)\n"
        "sys.path.insert(0, str(PASTA_PACOTE))\n"
        f"for nome_modulo in {[Path(nome).stem for nome in MODULOS]!r}:\n"
        "    sys.modules.pop(nome_modulo, None)\n"
        "from exportacao_colab import validar_pacote_colab, executar_pacote_colab, exportar_resultado_colab\n"
        "validar_pacote_colab(dados_zip)\n"
        "del dados_zip, enviados, payloads\n"
        "print('Origem validada:', manifesto['origem']['contexto_execucao_id'])\n"
    )
    parametros = """# Escolha CPU ou GPU sem alterar a origem da etapa 08.
DISPOSITIVO = "auto"  # "auto", "cpu" ou "cuda"
PRECISAO = "float32"  # "float16" somente com GPU
INDICE_TORCH_CUDA = "https://download.pytorch.org/whl/cu124"

# A finalidade, o lote e a agregação podem ser ajustados antes da inferência.
CONFIGURACAO = json.loads((PASTA_PACOTE / "configuracao.json").read_text(encoding="utf-8"))
# CONFIGURACAO["perfil"] = "recuperacao"  # "similaridade" ou "consulta"
# CONFIGURACAO["tamanho_lote"] = 4
# CONFIGURACAO["max_tokens"] = 512
# CONFIGURACAO["agregar"] = True
print(CONFIGURACAO)
"""
    instalacao = """import importlib.metadata
import subprocess

if DISPOSITIVO not in ("auto", "cpu", "cuda"):
    raise ValueError("DISPOSITIVO deve ser auto, cpu ou cuda.")
if PRECISAO not in ("float32", "float16"):
    raise ValueError("PRECISAO deve ser float32 ou float16.")
gpu_disponivel = False
if shutil.which("nvidia-smi"):
    gpu_disponivel = subprocess.run(
        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10,
    ).returncode == 0
dispositivo_efetivo = ("cuda" if gpu_disponivel else "cpu") if DISPOSITIVO == "auto" else DISPOSITIVO
if dispositivo_efetivo == "cuda" and not gpu_disponivel:
    raise ValueError("GPU solicitada mas indisponível. Selecione CPU ou ative GPU no Colab.")
if dispositivo_efetivo == "cpu" and PRECISAO != "float32":
    raise ValueError("CPU exige PRECISAO=float32.")
indice_torch = INDICE_TORCH_CUDA if dispositivo_efetivo == "cuda" else "https://download.pytorch.org/whl/cpu"
def versao_instalada(nome):
    try:
        return importlib.metadata.version(nome)
    except importlib.metadata.PackageNotFoundError:
        return ""

# Transformers pode importar torchvision mesmo com um modelo apenas de texto.
# Ajuste opcionais já presentes para versões compatíveis com o PyTorch fixado.
versoes_torch = {"torch": "2.6.0"}
for nome, versao in (("torchvision", "0.21.0"), ("torchaudio", "2.6.0")):
    if versao_instalada(nome):
        versoes_torch[nome] = versao
precisa_instalar = any(versao_instalada(nome).split("+")[0] != versao
                      for nome, versao in versoes_torch.items())
precisa_instalar |= dispositivo_efetivo == "cuda" and "+cpu" in versao_instalada("torch")
if precisa_instalar:
    if any(nome in sys.modules for nome in versoes_torch):
        raise RuntimeError("Reinicie a sessão do Colab antes de instalar as versões fixadas de PyTorch e opcionais.")
    subprocess.run([sys.executable, "-m", "pip", "install",
                    *[nome + "==" + versao for nome, versao in versoes_torch.items()],
                    "--index-url", indice_torch], check=True)
subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(PASTA_PACOTE / "requirements-embeddings.txt")], check=True)
import torch
from transformers import AutoModel, AutoTokenizer
if dispositivo_efetivo == "cuda" and not torch.cuda.is_available():
    raise RuntimeError("PyTorch não disponibilizou CUDA. Verifique o índice CUDA ou selecione CPU.")
print("PyTorch carregado:", torch.__version__)
print("Dependências preparadas; dispositivo:", dispositivo_efetivo)
"""
    modelo = """from embeddings_e5 import preparar_configuracao, criar_gerador_padrao

# "main" é resolvida agora para o SHA oficial; a inferência usa somente esse SHA.
config_modelo = preparar_configuracao(
    modelo_id="intfloat/multilingual-e5-large", revisao="main",
    dispositivo=dispositivo_efetivo, precisao=PRECISAO, baixar=True,
    caminho_config=PASTA_PACOTE / "instance" / "embeddings_modelo.json",
)
gerador = criar_gerador_padrao(caminho_config=PASTA_PACOTE / "instance" / "embeddings_modelo.json")
print("Revisão oficial fixa:", config_modelo["revisao"])
"""
    inferencia = """# Revalida toda a origem 08. Não executa novamente as etapas 01–08.
resultado = executar_pacote_colab(PASTA_PACOTE, gerador=gerador, configuracao=CONFIGURACAO)
from vetorizacao import validar_vetorizacao
validar_vetorizacao(resultado)  # Validação integral, sem executar o modelo.
print(resultado["validacao"])
"""
    download = """PASTA_RESULTADOS = PASTA_PACOTE.parent / "resultados"
PASTA_RESULTADOS.mkdir(exist_ok=True)
json_resultado = PASTA_RESULTADOS / "vetorizacao.json"
zip_resultado = PASTA_RESULTADOS / "vetorizacao-colab.zip"
json_resultado.write_text(json.dumps(resultado, ensure_ascii=False, indent=2, allow_nan=False) + "\\n", encoding="utf-8")
zip_resultado.write_bytes(exportar_resultado_colab(resultado))
files.download(str(zip_resultado))  # Inclui JSON completo, manifesto e vetores binários.
# Para baixar também o JSON separado: files.download(str(json_resultado))
"""
    notebook = {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {"colab": {"name": NOME_NOTEBOOK, "provenance": []},
                     "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                     "language_info": {"name": "python"}},
        "cells": [
            _celula("markdown", "# Língua — vetorização da etapa 09 no Google Colab\n\n"
                    "1. Baixe o notebook e o ZIP da mesma versão do Língua. Abra este notebook no Colab.\n"
                    "2. Execute as células em ordem e envie o ZIP exportado pelo aplicativo.\n"
                    "3. CPU funciona; para GPU, use **Ambiente de execução → Alterar tipo de ambiente de execução**.\n\n"
                    "O ZIP contém o texto e a origem linguística completos: o upload envia esses dados ao Google Colab. "
                    "Execute somente o notebook recebido do seu Língua; hashes verificam integridade, não a confiança no autor. "
                    "O modelo será baixado do Hugging Face e precisa de internet, memória e espaço disponíveis. "
                    "Esta execução não refaz as etapas 01–08.\n"),
            _celula("markdown", "## 1. Enviar e validar o pacote\n\nNenhum módulo do ZIP é importado antes de conferir estrutura, tamanhos e hashes do código confiável.\n"),
            _celula("code", bootstrap),
            _celula("markdown", "## 2. Configurar execução\n\nEscolha o dispositivo e ajuste a finalidade, o lote ou a agregação. GPU é opcional.\n"),
            _celula("code", parametros),
            _celula("markdown", "## 3. Instalar dependências opcionais\n\nA distribuição CPU evita baixar bibliotecas CUDA quando não forem usadas.\n"),
            _celula("code", instalacao),
            _celula("markdown", "## 4. Preparar o E5 em uma revisão oficial fixa\n\nO download pode levar alguns minutos. Nenhuma revisão provisória é usada.\n"),
            _celula("code", modelo),
            _celula("markdown", "## 5. Vetorizar e validar\n\nGera vetores do foco, da janela, de cada parágrafo e do documento integral. São preservadas a origem integral, as seleções contextuais, os blocos e a configuração efetiva.\n"),
            _celula("code", inferencia),
            _celula("markdown", "## 6. Baixar os resultados\n\nO ZIP contém `vetorizacao.json` completo, vetores float32 e manifestos com hashes. A sessão do Colab é temporária: baixe os resultados antes de encerrá-la.\n"),
            _celula("code", download),
        ],
    }
    for indice, celula in enumerate(notebook["cells"]):
        celula["id"] = f"lingua-colab-{indice:02d}"
    return (json.dumps(notebook, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def exportar_pacote_colab(contexto, *, configuracao=None):
    """Exporta origem e fechamento de módulos stdlib, sem Flask ou spaCy."""
    _origem_validada(contexto)
    config = _configuracao(configuracao)
    payloads = {nome: (_RAIZ / nome).read_bytes() for nome in (*MODULOS, "requirements-embeddings.txt")}
    payloads.update({
        NOME_NOTEBOOK: exportar_notebook(),
        "contexto.json": canonico(contexto).encode("utf-8"),
        "configuracao.json": canonico(config).encode("utf-8"),
        "LEIA-ME.txt": (
            "Língua — etapa 09 no Google Colab\n\n"
            "Abra lingua_etapa09_colab.ipynb no Google Colab e execute as células em ordem.\n"
            "Na primeira célula, envie este ZIP completo. Não é necessário clonar o GitHub.\n"
            "CPU é suficiente para executar; GPU é opcional e pode acelerar a inferência.\n"
            "O modelo E5 e as dependências são baixados no Colab, em uma revisão oficial fixa.\n"
            "O texto completo e a origem linguística serão enviados ao Google Colab.\n"
            "As etapas 01–08 não serão executadas novamente.\n"
            "Baixe vetorizacao-colab.zip ao concluir. Ele contém origem, JSON e bytes vetoriais.\n"
            "Os resultados não são importados automaticamente no banco local do aplicativo.\n"
        ).encode("utf-8"),
    })
    if (any(len(conteudo) > MAX_BYTES_ARQUIVO for conteudo in payloads.values())
            or sum(map(len, payloads.values())) > MAX_BYTES_PACOTE):
        raise ErroLimite("Pacote Colab excede os limites de tamanho; nenhuma origem foi omitida.")
    manifesto = {
        "formato": FORMATO, "versao": VERSAO,
        "origem": {"documento_id": contexto["documento_id"],
                   "contexto_execucao_id": contexto["execucao_id"], "contexto_sha256": hash_json(contexto)},
        "arquivos": {nome: {"bytes": len(conteudo), "sha256": sha256(conteudo).hexdigest()}
                     for nome, conteudo in sorted(payloads.items())},
    }
    payloads["manifesto.json"] = canonico(manifesto).encode("utf-8")
    if sum(map(len, payloads.values())) > MAX_BYTES_PACOTE:
        raise ErroLimite("Pacote Colab excede 128 MiB descompactado.")
    dados = _zip_payloads(payloads)
    if len(dados) > MAX_BYTES_ZIP:
        raise ErroLimite("ZIP Colab excede 64 MiB; nenhuma origem foi omitida.")
    return dados


def executar_pacote_colab(diretorio, *, gerador, configuracao=None):
    """Reconfere os arquivos extraídos e vetoriza apenas a origem 08 validada."""
    diretorio = Path(diretorio)
    if diretorio.is_symlink() or not diretorio.is_dir():
        raise ErroPacoteColab("Diretório do pacote inválido ou simbólico.")
    tamanho = 0
    for nome in ARQUIVOS_PACOTE:
        caminho = diretorio / nome
        if caminho.is_symlink() or not caminho.is_file():
            raise ErroPacoteColab("Arquivo extraído ausente, inválido ou simbólico: " + nome)
        tamanho_arquivo = caminho.stat().st_size
        if tamanho_arquivo > MAX_BYTES_ARQUIVO:
            raise ErroPacoteColab("Arquivo extraído excede o limite de tamanho.")
        tamanho += tamanho_arquivo
        if tamanho > MAX_BYTES_PACOTE:
            raise ErroPacoteColab("Pacote extraído excede o limite total de tamanho.")
    payloads = {}
    tamanho = 0
    for nome in ARQUIVOS_PACOTE:
        caminho = diretorio / nome
        with caminho.open("rb") as arquivo:
            conteudo = arquivo.read(MAX_BYTES_ARQUIVO + 1)
        if len(conteudo) > MAX_BYTES_ARQUIVO:
            raise ErroPacoteColab("Arquivo extraído excede o limite de tamanho.")
        tamanho += len(conteudo)
        if tamanho > MAX_BYTES_PACOTE:
            raise ErroPacoteColab("Pacote extraído excede o limite total de tamanho.")
        payloads[nome] = conteudo
    # Valida também a estrutura do manifesto e todos os hashes, sem extrair.
    dados = _zip_payloads(payloads)
    payloads, manifesto = _validar_zip_bruto(dados)
    contexto, config = _validar_payloads(payloads, manifesto)
    config = config if configuracao is None else _configuracao(configuracao)
    from vetorizacao import vetorizar_unidades_contexto
    return vetorizar_unidades_contexto(contexto, gerador=gerador, configuracao=config)


def exportar_resultado_colab(registro):
    """ZIP compatível com o app, acrescido do JSON completo e hashes dos payloads."""
    from persistencia_vetores import exportar_zip
    from vetorizacao import validar_vetorizacao
    validar_vetorizacao(registro)
    with ZipFile(BytesIO(exportar_zip(registro))) as arquivo:
        payloads = {m.filename: arquivo.read(m) for m in arquivo.infolist()}
    payloads["vetorizacao.json"] = (json.dumps(registro, ensure_ascii=False, indent=2,
                                              allow_nan=False) + "\n").encode("utf-8")
    integridade = {
        "formato": "lingua_etapa09_colab_resultado", "versao": VERSAO,
        "execucao_id": registro["execucao_id"], "contexto_execucao_id": registro["contexto_execucao_id"],
        "arquivos": {nome: {"bytes": len(conteudo), "sha256": sha256(conteudo).hexdigest()}
                     for nome, conteudo in sorted(payloads.items())},
    }
    payloads["integridade.json"] = canonico(integridade).encode("utf-8")
    return _zip_payloads(payloads)
