"""Resultados portáteis com dados controlados e gerador explicitamente simulado.

Importação não executa inferência e não comprova os metadados de um modelo.
"""

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import stat
import struct
import unittest
from unittest.mock import patch
import warnings
from zipfile import ZIP_BZIP2, ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from exportacao_colab import exportar_resultado_colab
from fixtures_contexto import INSTANTE
from fixtures_vetorizacao import GeradorSimulado, construir_contexto
import importacao_vetores as importacao
from persistencia_vetores import exportar_zip
from vetorizacao import validar_vetorizacao, vetorizar_unidades_contexto


def json_bytes(valor):
    return json.dumps(valor, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def abrir_zip(dados):
    with ZipFile(BytesIO(dados)) as arquivo:
        return {m.filename: arquivo.read(m) for m in arquivo.infolist()}


def escrever_zip(payloads, *, extra=None, compressao=ZIP_DEFLATED):
    buffer = BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with ZipFile(buffer, "w", compression=compressao) as arquivo:
            for nome, dados in payloads.items():
                arquivo.writestr(nome, dados)
            if extra:
                arquivo.writestr(*extra)
    return buffer.getvalue()


def atualizar_integridade(payloads):
    payloads = dict(payloads)
    integridade = json.loads(payloads["integridade.json"])
    integridade["arquivos"] = {
        nome: {"bytes": len(dados), "sha256": sha256(dados).hexdigest()}
        for nome, dados in payloads.items() if nome != "integridade.json"
    }
    payloads["integridade.json"] = json_bytes(integridade)
    return payloads


class ImportacaoVetoresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gerador = GeradorSimulado()
        cls.registro = vetorizar_unidades_contexto(
            construir_contexto(normalizar=True), gerador=cls.gerador,
            execucao_id="importacao-vetores-fixture", registrado_em=INSTANTE,
        )
        cls.zip_app = exportar_zip(cls.registro)
        cls.zip_colab = exportar_resultado_colab(cls.registro)
        cls.app_files = abrir_zip(cls.zip_app)
        cls.colab_files = abrir_zip(cls.zip_colab)

    def ler(self, dados):
        return importacao.ler_resultado_zip(dados, permitir_simulado=True)

    def rejeitar(self, dados, *, limite=False):
        erro = importacao.ErroLimiteImportacao if limite else importacao.ErroImportacaoVetores
        with self.assertRaises(erro):
            self.ler(dados)

    def adulterar_manifesto(self, mutacao, *, colab=False):
        payloads = dict(self.colab_files if colab else self.app_files)
        manifesto = json.loads(payloads["manifesto.json"])
        mutacao(manifesto)
        payloads["manifesto.json"] = json_bytes(manifesto)
        if colab:
            payloads = atualizar_integridade(payloads)
        return escrever_zip(payloads)

    def test_restauracao_exata_app_e_colab_sem_inferencia_extracao_ou_mutacao(self):
        antes = deepcopy(self.registro)
        chamadas = len(self.gerador.chamadas_geracao)
        with patch("vetorizacao.vetorizar_unidades_contexto", side_effect=AssertionError("inferência")), \
                patch.object(GeradorSimulado, "gerar", side_effect=AssertionError("inferência")), \
                patch.object(ZipFile, "extract", side_effect=AssertionError("extração")), \
                patch.object(ZipFile, "extractall", side_effect=AssertionError("extração")):
            for dados in (self.zip_app, self.zip_colab):
                with self.subTest(colab=dados == self.zip_colab):
                    restaurado = self.ler(dados)
                    self.assertEqual(restaurado, antes)
                    self.assertEqual(validar_vetorizacao(restaurado)["estado"], "valido")
                    self.assertFalse(restaurado["validacao"]["inferencia_real"])
                    self.assertIsNot(restaurado, self.registro)
        self.assertEqual(self.registro, antes)
        self.assertEqual(len(self.gerador.chamadas_geracao), chamadas)

    def test_simulados_exigem_permissao_booleana_explicita(self):
        with self.assertRaises(importacao.ErroImportacaoVetores):
            importacao.ler_resultado_zip(self.zip_colab)
        for valor in (1, "true", None):
            with self.subTest(valor=valor), self.assertRaises(importacao.ErroImportacaoVetores):
                importacao.ler_resultado_zip(self.zip_colab, permitir_simulado=valor)

    def test_conteudo_zip_precisa_ser_bytes_e_zip_legivel(self):
        for valor in (None, "arquivo", bytearray(self.zip_app), b"", b"arquivo invalido", self.zip_app[:60]):
            with self.subTest(tipo=type(valor).__name__):
                self.rejeitar(valor)

    def test_zip_stored_e_manifesto_com_whitespace_acima_2_mib(self):
        payloads = dict(self.app_files)
        payloads["manifesto.json"] += b" " * (2300 * 1024)
        dados = escrever_zip(payloads, compressao=ZIP_STORED)
        self.assertGreater(len(dados), 2 * 1024 * 1024)
        self.assertEqual(self.ler(dados), self.registro)

    def test_limites_zip_individual_total_e_membros(self):
        limites = (("MAX_BYTES_ZIP", len(self.zip_colab) - 1),
                   ("MAX_BYTES_ARQUIVO", max(map(len, self.colab_files.values())) - 1),
                   ("MAX_BYTES_PACOTE", sum(map(len, self.colab_files.values())) - 1),
                   ("MAX_MEMBROS", len(self.colab_files) - 1))
        for nome, valor in limites:
            with self.subTest(limite=nome), patch.object(importacao, nome, valor):
                self.rejeitar(self.zip_colab, limite=True)

    def test_bomba_declarada_e_recusada_antes_de_descompactar(self):
        dados = bytearray(self.zip_app)
        cabecalho = dados.index(b"PK\x01\x02")
        struct.pack_into("<I", dados, cabecalho + 24, importacao.MAX_BYTES_ARQUIVO + 1)
        with patch.object(ZipFile, "open", side_effect=AssertionError("descompactação antecipada")):
            self.rejeitar(bytes(dados), limite=True)

    def test_contagem_real_do_diretorio_e_limitada_antes_de_criar_zipfile(self):
        dados = bytearray(self.zip_app)
        fim = dados.rfind(b"PK\x05\x06")
        # EOCD mente sobre a quantidade: o preflight deve contar o diretório,
        # pois ZipFile não usa esse campo para limitar suas próprias alocações.
        struct.pack_into("<HH", dados, fim + 8, 1, 1)
        with patch.object(importacao, "MAX_MEMBROS", 2), \
                patch.object(importacao, "ZipFile", side_effect=AssertionError("alocação antecipada")):
            self.rejeitar(bytes(dados), limite=True)

    def test_diretorio_truncado_contagem_divergente_e_multidisco_recusados(self):
        for offset, formato, valor in ((8, "<H", 1), (4, "<H", 1), (12, "<I", 0)):
            dados = bytearray(self.zip_app)
            fim = dados.rfind(b"PK\x05\x06")
            struct.pack_into(formato, dados, fim + offset, valor)
            with self.subTest(offset=offset):
                self.rejeitar(bytes(dados))
        self.rejeitar(self.zip_app + b"dados anexados")

    def test_zip64_de_contagem_emitido_pelo_exportador_e_aceito(self):
        # Reduzir o limiar do próprio zipfile produz o mesmo ZIP64 de contagem
        # sem criar 65 mil membros ou usar vetores/modelos grandes.
        with patch("zipfile.ZIP_FILECOUNT_LIMIT", 1):
            dados = escrever_zip(self.app_files)
        self.assertIn(b"PK\x06\x06", dados)
        self.assertEqual(self.ler(dados), self.registro)

    def test_leitura_de_cada_membro_respeita_o_tamanho_declarado(self):
        abrir_original = ZipFile.open
        pedidos = []
        def abrir_limitado(arquivo, membro, *args, **kwargs):
            origem = abrir_original(arquivo, membro, *args, **kwargs)
            ler_original = origem.read
            def ler_limitado(tamanho=-1):
                pedidos.append((membro.filename, tamanho, membro.file_size))
                self.assertEqual(tamanho, membro.file_size + 1)
                return ler_original(tamanho)
            origem.read = ler_limitado
            return origem
        with patch.object(ZipFile, "open", abrir_limitado):
            self.assertEqual(self.ler(self.zip_colab), self.registro)
        self.assertTrue(pedidos)
        self.assertTrue(any(nome.endswith(".bin") and tamanho < 100 for nome, tamanho, _ in pedidos))

    def test_stream_com_bytes_ocultos_por_declaracao_menor_e_recusado(self):
        nome = "artefatos/000000.float32.bin"
        original = self.app_files[nome]
        casos = [(compressao, extra) for compressao in (ZIP_STORED, ZIP_DEFLATED)
                 for extra in (1, 64, 5 * 1024 * 1024)]
        for compressao, excesso in casos:
            payloads = dict(self.app_files)
            payloads[nome] += b"\x00" * excesso
            dados = bytearray(escrever_zip(payloads, compressao=compressao))
            with ZipFile(BytesIO(dados)) as arquivo:
                membro = arquivo.getinfo(nome)
                local = membro.header_offset
            # CRC e tamanho do prefixo são consistentes com o manifesto. Sem
            # conferir o término do stream, ZipExtFile devolveria só o prefixo.
            from zlib import crc32
            struct.pack_into("<I", dados, local + 14, crc32(original))
            struct.pack_into("<I", dados, local + 22, len(original))
            posicao = dados.index(b"PK\x01\x02")
            while True:
                tamanho_nome, extra, comentario = struct.unpack_from("<3H", dados, posicao + 28)
                nome_atual = bytes(dados[posicao + 46:posicao + 46 + tamanho_nome]).decode("ascii")
                if nome_atual == nome:
                    struct.pack_into("<I", dados, posicao + 16, crc32(original))
                    struct.pack_into("<I", dados, posicao + 24, len(original))
                    break
                posicao += 46 + tamanho_nome + extra + comentario
            with self.subTest(compressao=compressao, excesso=excesso):
                self.rejeitar(bytes(dados))

    def test_limites_de_artefatos_representacoes_e_blocos(self):
        manifesto = json.loads(self.app_files["manifesto.json"])
        registro = manifesto["registro"]
        limites = (("MAX_BLOCOS", len(registro["artefatos"]) - 1),
                   ("MAX_REPRESENTACOES", len(registro["representacoes"]) - 1))
        for nome, valor in limites:
            with self.subTest(limite=nome), patch.object(importacao, nome, valor):
                self.rejeitar(self.zip_app, limite=True)
        def muitos_blocos(manifesto):
            registro = manifesto["registro"]
            registro["representacoes"][0]["blocos"] *= importacao.MAX_BLOCOS + 1
        self.rejeitar(self.adulterar_manifesto(muitos_blocos), limite=True)

    def test_arquivo_ausente_extra_binario_sem_origem_e_codigo_recusados(self):
        payloads = dict(self.app_files)
        del payloads["manifesto.json"]
        self.rejeitar(escrever_zip(payloads))
        payloads = dict(self.app_files)
        del payloads["artefatos/000000.float32.bin"]
        self.rejeitar(escrever_zip(payloads))
        for nome in ("artefatos/999999.float32.bin", "codigo.py", "LEIA-ME.txt"):
            with self.subTest(nome=nome):
                self.rejeitar(escrever_zip(self.app_files, extra=(nome, b"raise AssertionError('executou')")))

    def test_caminhos_invalidos_e_membro_nulo(self):
        for nome in ("../manifesto.json", "/manifesto.json", "artefatos/../000000.float32.bin",
                     "artefatos\\000000.float32.bin", "artefatos/00000.float32.bin",
                     "artefatos/000000.float32.bin/", "./manifesto.json"):
            with self.subTest(nome=nome):
                self.rejeitar(escrever_zip(self.app_files, extra=(nome, b"invalido")))
        # O ZipInfo conserva orig_filename, mesmo quando filename é truncado
        # no NUL. Ambos os cabeçalhos são alterados para exercitar essa defesa.
        dados = self.zip_app.replace(b"manifesto.json", b"manifesto\x00json")
        self.rejeitar(dados)

    def test_membros_duplicados_diretorios_e_links(self):
        self.rejeitar(escrever_zip(self.app_files, extra=("manifesto.json", self.app_files["manifesto.json"])))
        for modo in (stat.S_IFLNK, stat.S_IFDIR, stat.S_IFIFO, stat.S_IFSOCK):
            info = ZipInfo("artefatos/000000.float32.bin")
            info.create_system = 3
            info.external_attr = (modo | 0o644) << 16
            payloads = dict(self.app_files)
            conteudo = payloads.pop(info.filename)
            with self.subTest(modo=modo):
                self.rejeitar(escrever_zip(payloads, extra=(info, conteudo)))

    def test_criptografia_e_compressao_nao_permitidas(self):
        dados = bytearray(self.zip_app)
        cabecalho = dados.index(b"PK\x01\x02")
        flags = struct.unpack_from("<H", dados, cabecalho + 8)[0]
        struct.pack_into("<H", dados, cabecalho + 8, flags | 1)
        self.rejeitar(bytes(dados))
        self.rejeitar(escrever_zip(self.app_files, compressao=ZIP_BZIP2))

    def test_contrato_e_versao_do_manifesto_e_registro(self):
        mutacoes = (lambda m: m.update(formato="outro"),
                    lambda m: m.update(versao="2.0.0"),
                    lambda m: m.update(extra=True),
                    lambda m: m["registro"].update(schema_version="2.0.0"),
                    lambda m: m["registro"].update(etapa="08_contexto"),
                    lambda m: m["registro"]["validacao"].update(estado="falhou"))
        for indice, mutacao in enumerate(mutacoes):
            with self.subTest(mutacao=indice):
                self.rejeitar(self.adulterar_manifesto(mutacao))

    def test_json_duplicado_nao_finito_unicode_invalido_e_recursivo(self):
        valores = (b'{"formato": 1, "formato": 2}', b'{"x":NaN}', b'{"x":Infinity}',
                   b'{"x":-Infinity}', b'{"x":1e9999}', b'\xff', b'{', b'[]')
        for valor in valores:
            payloads = dict(self.app_files)
            payloads["manifesto.json"] = valor
            with self.subTest(valor=valor):
                self.rejeitar(escrever_zip(payloads))
        payloads["manifesto.json"] = b"[" * 65 + b"0" + b"]" * 65
        self.rejeitar(escrever_zip(payloads), limite=True)

    def test_json_completo_e_integridade_tambem_sao_estritos(self):
        for nome in ("vetorizacao.json", "integridade.json"):
            for conteudo in (b'{"x":1,"x":2}', b'{"x":1e9999}', b"[" * 65 + b"0" + b"]" * 65):
                payloads = dict(self.colab_files)
                payloads[nome] = conteudo
                if nome == "vetorizacao.json":
                    payloads = atualizar_integridade(payloads)
                with self.subTest(nome=nome, conteudo=conteudo[:20]):
                    self.rejeitar(escrever_zip(payloads))

    def test_metadados_indice_ordem_e_ids_precisam_corresponder(self):
        mutacoes = (lambda m: m["arquivos"].reverse(),
                    lambda m: m["arquivos"].pop(),
                    lambda m: m["arquivos"][0].update(id="outro"),
                    lambda m: m["arquivos"][0].update(caminho="artefatos/999999.float32.bin"),
                    lambda m: m["arquivos"][0].update(tipo="representacao"),
                    lambda m: m["arquivos"][0].update(bytes=True),
                    lambda m: m["arquivos"][0].update(extra="campo"),
                    lambda m: m["registro"]["artefatos"][1].update(id=m["registro"]["artefatos"][0]["id"]),
                    lambda m: m["registro"]["representacoes"][1].update(id=m["registro"]["representacoes"][0]["id"]))
        for indice, mutacao in enumerate(mutacoes):
            with self.subTest(mutacao=indice):
                self.rejeitar(self.adulterar_manifesto(mutacao))

    def test_armazenamento_manifesto_sem_base64_com_tipo_e_formato_exatos(self):
        valores = ({"formato": "float64_le"}, {"bytes": True}, {"dimensao": True},
                   {"sha256_bytes": "f" * 63}, {"sha256_bytes": "g" * 64}, {"base64": "AAAA"})
        for valor in valores:
            with self.subTest(valor=valor):
                self.rejeitar(self.adulterar_manifesto(
                    lambda m: m["registro"]["artefatos"][0]["armazenamento"].update(valor)))

    def test_binario_corrompido_truncado_ou_com_hash_divergente(self):
        original = self.app_files["artefatos/000000.float32.bin"]
        for binario in (original[:-1], b"\x00" + original[1:], original + b"\x00"):
            payloads = dict(self.app_files)
            payloads["artefatos/000000.float32.bin"] = binario
            with self.subTest(bytes=len(binario)):
                self.rejeitar(escrever_zip(payloads))
        self.rejeitar(self.adulterar_manifesto(
            lambda m: m["registro"]["artefatos"][0]["armazenamento"].update(sha256_bytes="0" * 64)))

    def test_binarios_com_hash_atualizado_ainda_exigem_finitude_e_norma(self):
        for valor in (float("inf"), float("nan"), 2.0, 0.0):
            payloads = dict(self.app_files)
            manifesto = json.loads(payloads["manifesto.json"])
            binario = struct.pack("<ffff", valor, 0.0, 0.0, 0.0)
            payloads["artefatos/000000.float32.bin"] = binario
            manifesto["registro"]["artefatos"][0]["armazenamento"]["sha256_bytes"] = sha256(binario).hexdigest()
            manifesto["arquivos"][0]["sha256_bytes"] = sha256(binario).hexdigest()
            payloads["manifesto.json"] = json_bytes(manifesto)
            with self.subTest(valor=valor):
                self.rejeitar(escrever_zip(payloads))

    def test_vetor_normalizado_modificado_exige_recalcular_agregacao(self):
        payloads = dict(self.app_files)
        manifesto = json.loads(payloads["manifesto.json"])
        caminho = next(a["caminho"] for a in manifesto["arquivos"]
                       if a["id"] == self.registro["representacoes"][-1]["id"])
        binario = struct.pack("<ffff", 1.0, 0.0, 0.0, 0.0)
        payloads[caminho] = binario
        manifesto["registro"]["representacoes"][-1]["vetor"]["sha256_bytes"] = sha256(binario).hexdigest()
        next(a for a in manifesto["arquivos"] if a["caminho"] == caminho)["sha256_bytes"] = sha256(binario).hexdigest()
        payloads["manifesto.json"] = json_bytes(manifesto)
        self.rejeitar(escrever_zip(payloads))

    def test_origem_08_coordenadas_perfis_e_relatorio_sao_revalidados(self):
        mutacoes = (lambda r: r.update(contexto_sha256="0" * 64),
                    lambda r: r.update(contexto_execucao_id="outra-origem"),
                    lambda r: r.update(documento_id="outro"),
                    lambda r: r["contexto"].update(execucao_id="origem-adulterada"),
                    lambda r: r["compatibilidade"].update(sha256="0" * 64),
                    lambda r: r["validacao"].update(pronto_para_uso=True),
                    lambda r: r["representacoes"][0].update(texto="texto-adulterado"),
                    lambda r: r["modelo"].update(natureza="inferencia_real"))
        for indice, mutacao in enumerate(mutacoes):
            with self.subTest(mutacao=indice):
                self.rejeitar(self.adulterar_manifesto(lambda m: mutacao(m["registro"])))

    def test_json_completo_divergente_recusado_mesmo_com_integridade_atualizada(self):
        payloads = dict(self.colab_files)
        registro = json.loads(payloads["vetorizacao.json"])
        registro["registrado_em"] = "2025-01-01T00:00:00+00:00"
        payloads["vetorizacao.json"] = json_bytes(registro)
        self.rejeitar(escrever_zip(atualizar_integridade(payloads)))

    def test_integridade_exige_cobertura_completa_sem_extras_e_tipo_exato(self):
        mutacoes = (lambda i: i["arquivos"].pop("manifesto.json"),
                    lambda i: i["arquivos"].update(outro={"bytes": 0, "sha256": "0" * 64}),
                    lambda i: i["arquivos"]["manifesto.json"].update(bytes=True),
                    lambda i: i["arquivos"]["manifesto.json"].update(sha256="0" * 64),
                    lambda i: i.update(execucao_id="outro"),
                    lambda i: i.update(contexto_execucao_id="outro"),
                    lambda i: i.update(versao="2.0.0"),
                    lambda i: i.update(extra=True))
        for indice, mutacao in enumerate(mutacoes):
            payloads = dict(self.colab_files)
            integridade = json.loads(payloads["integridade.json"])
            mutacao(integridade)
            payloads["integridade.json"] = json_bytes(integridade)
            with self.subTest(mutacao=indice):
                self.rejeitar(escrever_zip(payloads))

    def test_colab_json_e_integridade_precisam_ser_enviados_juntos(self):
        for nome in ("vetorizacao.json", "integridade.json"):
            payloads = dict(self.colab_files)
            del payloads[nome]
            with self.subTest(ausente=nome):
                self.rejeitar(escrever_zip(payloads))

    def test_corrupcao_crc_e_campos_malformados_nunca_expoem_excecao_crua(self):
        dados = bytearray(self.zip_app)
        cabecalho = dados.index(b"PK\x01\x02")
        struct.pack_into("<I", dados, cabecalho + 16, 0)
        self.rejeitar(bytes(dados))
        mutacoes = (lambda r: r.update(contexto=None), lambda r: r.update(artefatos=None),
                    lambda r: r.update(representacoes=[1]), lambda r: r.update(modelo=None),
                    lambda r: r.update(configuracao=[]), lambda r: r.update(geracao=None),
                    lambda r: r["representacoes"][0].update(blocos=[None]),
                    lambda r: r["representacoes"][0]["blocos"][0].update(entrada_modelo=1))
        for indice, mutacao in enumerate(mutacoes):
            with self.subTest(mutacao=indice):
                self.rejeitar(self.adulterar_manifesto(lambda m: mutacao(m["registro"])))


if __name__ == "__main__":
    unittest.main()
