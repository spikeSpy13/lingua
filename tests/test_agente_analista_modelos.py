"""Capacidades consultadas com HTTPS simulado, sem chave ou chamadas pagas."""

import io
import json
import os
import ssl
import unittest
from http.client import IncompleteRead
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler

from agente_analista import modelos
from api_narrativas import ErroAPINarrativa


MODELO_NVIDIA = "nvidia/nemotron-3-ultra-550b-a55b:free"
SEGREDO = "chave-e-relato-privados-nao-exibir"


class AmbienteSemChave(dict):
    def get(self, nome, padrao=None):
        if nome == "NARRATIVA_API_KEY":
            raise AssertionError("A consulta pública não pode ler uma chave de API.")
        return super().get(nome, padrao)

    def __getitem__(self, nome):
        if nome == "NARRATIVA_API_KEY":
            raise AssertionError("A consulta pública não pode ler uma chave de API.")
        return super().__getitem__(nome)


class ModelosTests(unittest.TestCase):
    def setUp(self):
        with modelos._lock_cache:
            modelos._cache.clear()
        self.contexto = ssl.create_default_context()
        self.cliente = MagicMock()
        self.resposta = self.cliente.open.return_value.__enter__.return_value

    def tearDown(self):
        with modelos._lock_cache:
            modelos._cache.clear()

    def catalogo(self, parametros, modelo=MODELO_NVIDIA, **extras):
        return {"data": [{"id": modelo, "supported_parameters": parametros, **extras}]}

    def escolher(self, catalogo=None, *, dados=None, provedor="openrouter", modelo=MODELO_NVIDIA,
                 erro=None, erro_contexto=None):
        if dados is None:
            dados = json.dumps(catalogo if catalogo is not None else self.catalogo([])).encode("utf-8")
        self.resposta.read.return_value = dados
        self.cliente.open.side_effect = erro
        ambiente = AmbienteSemChave({"NARRATIVA_API_KEY": SEGREDO})
        with (
            patch.object(os, "environ", ambiente),
            patch.object(modelos, "_contexto_https", return_value=self.contexto, side_effect=erro_contexto) as contexto,
            patch.object(modelos, "build_opener", return_value=self.cliente) as fabrica,
        ):
            formato = modelos.escolher_formato_resposta(provedor, modelo)
        return formato, fabrica, contexto

    def erro_seguro(self, **kwargs):
        with self.assertRaises(modelos.ErroModelo) as levantado:
            self.escolher(**kwargs)
        self.assertNotIn(SEGREDO, str(levantado.exception))
        self.assertTrue(levantado.exception.__suppress_context__ or levantado.exception.__context__ is None)
        with modelos._lock_cache:
            self.assertNotIn(MODELO_NVIDIA, modelos._cache)
        return str(levantado.exception)

    def test_modelo_nvidia_pode_ter_cada_capacidade_sem_inferir_pelo_identificador(self):
        casos = [(["structured_outputs", "response_format"], "json_schema"),
                 (["structured_outputs"], "json_schema"),
                 (["response_format"], "json_object"),
                 (["temperature", "max_tokens"], "texto"), ([], "texto")]
        for parametros, esperado in casos:
            with self.subTest(parametros=parametros):
                with modelos._lock_cache:
                    modelos._cache.clear()
                formato, _, _ = self.escolher(self.catalogo(parametros))
                self.assertEqual(formato, esperado)

    def test_consulta_get_fixa_sem_chave_corpo_relato_ou_redirecionamento(self):
        formato, fabrica, contexto = self.escolher(self.catalogo(["response_format"], descricao=SEGREDO))
        self.assertEqual(formato, "json_object")
        self.cliente.open.assert_called_once()
        pedido = self.cliente.open.call_args.args[0]
        self.assertEqual(pedido.full_url, "https://openrouter.ai/api/v1/models")
        self.assertEqual(pedido.get_method(), "GET")
        self.assertIsNone(pedido.data)
        self.assertIsNone(pedido.get_header("Authorization"))
        self.assertNotIn(SEGREDO, repr(pedido.header_items()))
        self.assertNotIn(MODELO_NVIDIA, pedido.full_url)
        self.assertEqual(self.cliente.open.call_args.kwargs["timeout"], 20)
        self.resposta.read.assert_called_once_with(4 * 1024 * 1024 + 1)
        contexto.assert_called_once_with()
        handlers = fabrica.call_args.args
        self.assertIsInstance(handlers[0], modelos._SemRedirecionamento)
        self.assertIsInstance(handlers[1], HTTPSHandler)
        self.assertIs(handlers[1]._context, self.contexto)
        self.assertEqual(handlers[1]._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(handlers[1]._context.check_hostname)
        self.assertIsNone(handlers[0].redirect_request(pedido, None, 302, "", {}, "https://privado.invalid"))
        with modelos._lock_cache:
            self.assertNotIn(SEGREDO, repr(modelos._cache))

    def test_openai_schema_nao_consulta_rede_certificados_ou_chave(self):
        formato, fabrica, contexto = self.escolher(provedor="openai", modelo="gpt-4.1-mini")
        self.assertEqual(formato, "json_schema")
        fabrica.assert_not_called()
        contexto.assert_not_called()
        self.cliente.open.assert_not_called()
        with modelos._lock_cache:
            self.assertFalse(modelos._cache)

    def test_id_exato_inclui_sufixo_free_e_nao_confunde_modelo_base(self):
        catalogo = {"data": [
            {"id": MODELO_NVIDIA.removesuffix(":free"), "supported_parameters": ["structured_outputs"]},
            {"id": MODELO_NVIDIA, "supported_parameters": ["response_format"]},
        ]}
        self.assertEqual(self.escolher(catalogo)[0], "json_object")

    def test_modelo_ausente_orienta_identificador_sem_mostrar_corpo(self):
        mensagem = self.erro_seguro(catalogo={"data": [{"id": "outro/modelo", "descricao": SEGREDO}]})
        self.assertIn("identificador exato", mensagem)

    def test_modelo_ausente_em_lista_vazia_nao_assume_texto(self):
        self.assertIn("não consta", self.erro_seguro(catalogo={"data": []}))

    def test_capacidades_ausentes_nulas_ou_corrompidas_nao_assumem_texto(self):
        casos = [{"data": [{"id": MODELO_NVIDIA}]}, self.catalogo(None), self.catalogo("response_format"),
                 self.catalogo({"response_format": True}), self.catalogo([42]), self.catalogo([None]),
                 self.catalogo(["response_format", False]), self.catalogo([""])]
        for catalogo in casos:
            with self.subTest(catalogo=catalogo):
                self.assertIn("capacidades válidas", self.erro_seguro(catalogo=catalogo))

    def test_catalogo_malformado_nao_presume_capacidades(self):
        casos = [None, [], {}, {"error": {"message": SEGREDO}}, {"data": None}, {"data": {}},
                 {"data": [None]}, {"data": [{"supported_parameters": []}]}, {"data": [{"id": 42}]},
                 {"data": [{"id": ""}]}]
        for catalogo in casos:
            with self.subTest(catalogo=catalogo):
                dados = json.dumps(catalogo).encode("utf-8")
                self.assertIn("dados inválidos", self.erro_seguro(dados=dados))

    def test_json_invalido_utf8_constantes_e_campos_repetidos_sao_recusados(self):
        casos = [b"", b"not JSON", b"\xff", b'{"data":[],"data":[]}',
                 b'{"data":[{"id":"x","id":"y"}]}', b'{"data":NaN}',
                 b'{"data":Infinity}']
        for dados in casos:
            with self.subTest(dados=dados[:30]):
                self.assertIn("JSON inválido", self.erro_seguro(dados=dados))

    def test_json_profundo_rejeitado_sem_expor_conteudo(self):
        dados = (b"[" * 2000) + (b"]" * 2000)
        self.assertIn("inválido", self.erro_seguro(dados=dados))

    def test_modelo_repetido_no_catalogo_recusa_capacidade_ambigua(self):
        catalogo = self.catalogo(["structured_outputs"])
        catalogo["data"].append({"id": MODELO_NVIDIA, "supported_parameters": []})
        self.assertIn("conflitantes", self.erro_seguro(catalogo=catalogo))

    def test_resposta_acima_de_quatro_mib_recusada(self):
        self.assertIn("tamanho permitido", self.erro_seguro(dados=b"x" * (modelos.LIMITE_CATALOGO_BYTES + 1)))

    def test_limite_exato_de_quatro_mib_aceito(self):
        dados = json.dumps(self.catalogo(["response_format"])).encode("utf-8")
        dados += b" " * (modelos.LIMITE_CATALOGO_BYTES - len(dados))
        self.assertEqual(self.escolher(dados=dados)[0], "json_object")

    def test_erros_http_sao_fechados_e_nao_expoem_corpo_ou_mensagem(self):
        for status in (400, 401, 403, 404, 429, 500):
            with self.subTest(status=status):
                corpo = io.BytesIO(SEGREDO.encode())
                erro = HTTPError("https://privado.invalid", status, SEGREDO, {}, corpo)
                mensagem = self.erro_seguro(erro=erro)
                self.assertIn(f"HTTP {status}", mensagem)
                self.assertTrue(corpo.closed)
                self.assertNotIn("privado.invalid", mensagem)

    def test_redirect_recusado_sem_ler_ou_exibir_corpo(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                erro = HTTPError("https://privado.invalid", status, SEGREDO, {}, io.BytesIO(SEGREDO.encode()))
                self.assertIn("redirecionamento foi recusado", self.erro_seguro(erro=erro))

    def test_dns_tls_proxy_timeout_e_erro_de_leitura_nao_expoem_segredos(self):
        erros = [URLError(SEGREDO), URLError(ssl.SSLCertVerificationError(1, SEGREDO)),
                 TimeoutError(SEGREDO), OSError(SEGREDO), IncompleteRead(SEGREDO.encode())]
        for erro in erros:
            with self.subTest(erro=type(erro).__name__):
                self.assertIn("catálogo público", self.erro_seguro(erro=erro))
        self.resposta.read.side_effect = OSError(SEGREDO)
        self.assertIn("catálogo público", self.erro_seguro())

    def test_certificados_invalidos_nao_fazem_consulta_ou_expoem_caminho(self):
        mensagem = self.erro_seguro(erro_contexto=ErroAPINarrativa(SEGREDO))
        self.assertIn("certificados HTTPS", mensagem)
        self.cliente.open.assert_not_called()

    def test_cache_cinco_minutos_guarda_so_identificador_formato_e_validade(self):
        with patch.object(modelos, "monotonic", return_value=100):
            self.assertEqual(self.escolher(self.catalogo(["response_format"], descricao=SEGREDO))[0], "json_object")
        with patch.object(modelos, "monotonic", return_value=399):
            self.assertEqual(self.escolher(self.catalogo(["structured_outputs"]))[0], "json_object")
        self.cliente.open.assert_called_once()
        with modelos._lock_cache:
            self.assertEqual(modelos._cache, {MODELO_NVIDIA: (400, "json_object")})
        with patch.object(modelos, "monotonic", return_value=400):
            self.assertEqual(self.escolher(self.catalogo(["structured_outputs"]))[0], "json_schema")
        self.assertEqual(self.cliente.open.call_count, 2)

    def test_cache_nao_confunde_identificadores_de_modelos(self):
        self.escolher(self.catalogo(["response_format"]))
        self.assertEqual(self.escolher(self.catalogo([], modelo="outro/modelo"), modelo="outro/modelo")[0], "texto")
        self.assertEqual(self.cliente.open.call_count, 2)
        with modelos._lock_cache:
            self.assertEqual(set(modelos._cache), {MODELO_NVIDIA, "outro/modelo"})

    def test_erro_nao_fica_em_cache_e_nova_consulta_pode_funcionar(self):
        self.erro_seguro(catalogo=self.catalogo(None))
        self.assertEqual(self.escolher(self.catalogo(["response_format"]))[0], "json_object")
        self.assertEqual(self.cliente.open.call_count, 2)

    def test_provedor_e_identificador_invalidos_nao_fazem_consulta(self):
        casos = [("outro", MODELO_NVIDIA), ("https://privado.invalid", MODELO_NVIDIA),
                 ("openrouter", None), ("openrouter", ""), ("openrouter", " modelo"),
                 ("openrouter", "modelo "), ("openrouter", "../modelo?privado"),
                 ("openrouter", "a" * 201), ("openai", "")]
        for provedor, modelo in casos:
            with self.subTest(provedor=provedor, modelo=modelo):
                self.erro_seguro(provedor=provedor, modelo=modelo)
        self.cliente.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
