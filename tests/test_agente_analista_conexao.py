"""Diagnóstico público seguro, sem tráfego nem leitura de credenciais."""

import errno
from contextlib import redirect_stdout
import io
import os
import socket
import ssl
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler

from agente_analista import diagnosticar_conexao as diagnostico


class AmbienteSemChave(dict):
    def get(self, nome, padrao=None):
        if nome == "NARRATIVA_API_KEY":
            raise AssertionError("O diagnóstico não pode ler a chave de API.")
        return super().get(nome, padrao)


class DiagnosticarConexaoTests(unittest.TestCase):
    def setUp(self):
        self.contexto = ssl.create_default_context()
        self.cliente = MagicMock()
        self.resposta = self.cliente.open.return_value.__enter__.return_value
        self.resposta.getcode.return_value = 200

    def executar(self, erro=None, ambiente=None, erro_contexto=None):
        if erro is not None:
            self.cliente.open.side_effect = erro
        saida = io.StringIO()
        ambiente = AmbienteSemChave(ambiente or {})
        ambiente["NARRATIVA_API_KEY"] = "chave-privada-nunca-exibir"
        with (
            patch.object(os, "environ", ambiente),
            patch.object(diagnostico.api_narrativas, "_contexto_https", return_value=self.contexto, side_effect=erro_contexto, create=True) as contexto,
            patch.object(diagnostico, "build_opener", return_value=self.cliente) as construir,
            redirect_stdout(saida),
        ):
            codigo = diagnostico.main()
        self.assertNotIn("chave-privada-nunca-exibir", saida.getvalue())
        return codigo, saida.getvalue(), construir, contexto

    def test_consulta_publica_get_sem_chave_corpo_ou_leitura_da_resposta(self):
        codigo, saida, construir, contexto = self.executar()
        self.assertEqual(codigo, 0)
        self.assertIn("openrouter.ai", saida)
        self.assertIn("HTTP 200", saida)
        pedido = self.cliente.open.call_args.args[0]
        self.assertEqual(pedido.full_url, "https://openrouter.ai/api/v1/models")
        self.assertEqual(pedido.get_method(), "GET")
        self.assertIsNone(pedido.data)
        self.assertIsNone(pedido.get_header("Authorization"))
        self.assertEqual(self.cliente.open.call_args.kwargs["timeout"], 20)
        self.resposta.read.assert_not_called()
        contexto.assert_called_once_with()
        handlers = construir.call_args.args
        self.assertIsInstance(handlers[0], diagnostico.api_narrativas._SemRedirecionamento)
        self.assertIsInstance(handlers[1], HTTPSHandler)
        self.assertIs(handlers[1]._context, self.contexto)
        self.assertEqual(handlers[1]._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(handlers[1]._context.check_hostname)
        self.assertIsNone(handlers[0].redirect_request(pedido, None, 302, "", {}, "https://outro.invalid"))

    def test_openai_401_confirma_conexao_sem_autenticacao(self):
        erro = HTTPError("https://url-privada.invalid", 401, "mensagem-privada", {}, None)
        codigo, saida, _, _ = self.executar(erro, {"AGENTE_ANALISTA_PROVEDOR": "openai"})
        self.assertEqual(codigo, 0)
        self.assertIn("HTTP 401 é esperado", saida)
        self.assertIn("api.openai.com", saida)
        self.assertNotIn("url-privada", saida)
        self.assertNotIn("mensagem-privada", saida)
        pedido = self.cliente.open.call_args.args[0]
        self.assertEqual(pedido.full_url, "https://api.openai.com/v1/models")

    def test_resposta_http_distingue_servidor_alcancado_de_falha_de_rede(self):
        for status in (302, 401, 402, 403, 404, 429, 500):
            with self.subTest(status=status):
                erro = HTTPError("https://privado.invalid", status, "segredo", {}, None)
                codigo, saida, _, _ = self.executar(erro)
                self.assertEqual(codigo, 0)
                self.assertIn(f"HTTP {status}", saida)
                self.assertNotIn("segredo", saida)
                if status == 302:
                    self.assertIn("não o seguiu", saida)

    def test_certificado_invalido_tem_instrucao_mac_sem_expor_erro(self):
        erro = URLError(ssl.SSLCertVerificationError(1, "certificado-privado.invalid"))
        with patch.object(diagnostico.platform, "system", return_value="Darwin"), patch("pathlib.Path.is_file", return_value=True):
            codigo, saida, _, _ = self.executar(erro)
        self.assertEqual(codigo, 1)
        self.assertIn("certificado_tls", saida)
        self.assertIn('open "/Applications/Python 3.12/Install Certificates.command"', saida)
        self.assertNotIn("certificado-privado", saida)
        self.assertIn("Mantenha a validação HTTPS ativada", saida)

    def test_certificados_mac_ausentes_nao_recomenda_comando_inexistente(self):
        with patch.object(diagnostico.platform, "system", return_value="Darwin"), patch("pathlib.Path.is_file", return_value=False):
            codigo, saida, _, _ = self.executar(ssl.SSLCertVerificationError(1, "privado"))
        self.assertEqual(codigo, 1)
        self.assertNotIn("Install Certificates.command", saida)
        self.assertIn("certificados da instalação", saida)

    def test_configuracao_de_certificados_invalida_falha_sem_traceback_ou_rede(self):
        erro = diagnostico.api_narrativas.ErroAPINarrativa("segredo https://usuario:senha@privado.invalid")
        with patch.object(diagnostico.platform, "system", return_value="Darwin"), patch("pathlib.Path.is_file", return_value=True):
            codigo, saida, construir, contexto = self.executar(erro_contexto=erro)
        self.assertEqual(codigo, 1)
        self.assertIn("configuracao_certificados", saida)
        self.assertIn("caminhos de certificados configurados", saida)
        self.assertIn("Install Certificates.command", saida)
        construir.assert_not_called()
        self.cliente.open.assert_not_called()
        contexto.assert_called_once_with()
        for privado in ("usuario", "senha", "segredo", "Traceback"):
            self.assertNotIn(privado, saida)

    def test_negociacao_tls_e_certificado_sao_falhas_distintas(self):
        codigo, saida, _, _ = self.executar(URLError(ssl.SSLError(1, "detalhe-privado")))
        self.assertEqual(codigo, 1)
        self.assertIn("negociacao_tls", saida)
        self.assertNotIn("certificado_tls", saida)
        self.assertNotIn("detalhe-privado", saida)

    def test_dns_e_classificado_sem_imprimir_host_de_proxy(self):
        codigo, saida, _, _ = self.executar(URLError(socket.gaierror(-2, "proxy-privado.invalid")))
        self.assertEqual(codigo, 1)
        self.assertIn("dns", saida)
        self.assertIn("configurações de DNS", saida)
        self.assertNotIn("proxy-privado", saida)

    def test_timeout_direto_ou_encapsulado(self):
        for erro in (TimeoutError("privado"), URLError(socket.timeout("privado"))):
            with self.subTest(tipo=type(erro).__name__):
                codigo, saida, _, _ = self.executar(erro)
                self.assertEqual(codigo, 1)
                self.assertIn("tempo_esgotado", saida)
                self.assertNotIn("privado", saida)

    def test_conexao_recusada_direta_ou_encapsulada(self):
        for erro in (ConnectionRefusedError(errno.ECONNREFUSED, "privado"), URLError(OSError(errno.ECONNREFUSED, "privado"))):
            with self.subTest(tipo=type(erro).__name__):
                codigo, saida, _, _ = self.executar(erro)
                self.assertEqual(codigo, 1)
                self.assertIn("conexao_recusada", saida)
                self.assertNotIn("privado", saida)

    def test_proxy_recusado_nao_conta_como_servidor_do_provedor_alcancado(self):
        erro = URLError(OSError("Tunnel connection failed: 407 segredo https://usuario:senha@privado.invalid"))
        codigo, saida, _, _ = self.executar(erro, {"HTTPS_PROXY": "https://usuario:senha@proxy-privado.invalid"})
        self.assertEqual(codigo, 1)
        self.assertIn("proxy recusou", saida)
        self.assertIn("HTTP 407", saida)
        self.assertIn("valores foram omitidos", saida)
        for privado in ("usuario", "senha", "proxy-privado", "segredo"):
            self.assertNotIn(privado, saida)

    def test_erro_de_conexao_generico_nao_vaza_mensagem(self):
        codigo, saida, _, _ = self.executar(URLError("https://usuario:senha@privado.invalid"))
        self.assertEqual(codigo, 1)
        self.assertIn("Falha de conexão: conexao", saida)
        self.assertNotIn("usuario", saida)
        self.assertNotIn("senha", saida)

    def test_configuracao_invalida_falha_antes_da_rede_sem_expor_valores(self):
        for ambiente in (
            {"AGENTE_ANALISTA_PROVEDOR": "https://usuario:senha@privado.invalid"},
            {"AGENTE_ANALISTA_MODELO": "\nsegredo\n"},
            {"AGENTE_ANALISTA_MODELO": ""},
        ):
            with self.subTest(campos=list(ambiente)):
                codigo, saida, construir, contexto = self.executar(ambiente=ambiente)
                self.assertEqual(codigo, 1)
                self.assertIn("Configuração inválida", saida)
                construir.assert_not_called()
                contexto.assert_not_called()
                for privado in ("usuario", "senha", "segredo"):
                    self.assertNotIn(privado, saida)


if __name__ == "__main__":
    unittest.main()
