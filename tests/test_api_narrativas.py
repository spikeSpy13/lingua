"""Pedidos e falhas dos provedores são simulados, sem tráfego externo."""

import io
import json
import os
import socket
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

import api_narrativas as api


class PromptNarrativaTests(unittest.TestCase):
    def contexto(self, indice=1, **alteracoes):
        contexto = {
            "indice": indice,
            "ideia_inicial": "Voltei à casa onde escondi uma carta.",
            "planejamento": "Personagem Vera, casa antiga, carta mantida em segredo.",
            "anteriores": [{"id": numero, "label": "Anterior", "texto": f"Versão atual completa {numero}."}
                           for numero in range(1, indice)],
            "instrucoes": "Preserve a carta sem explicar o segredo.",
            "intensidade": 4,
        }
        contexto.update(alteracoes)
        return contexto

    def test_planejamento_tem_todos_os_componentes_e_nao_gera_narrativa(self):
        mensagens = api.construir_mensagens(**self.contexto(0, planejamento=""))
        sistema = mensagens[0]["content"]
        for componente in ("personagem principal", "contexto", "conflito central", "desenvolvimento previsto",
                           "recorrência relevante", "contradição principal", "questão que permanecerá aberta"):
            self.assertIn(componente, sistema)
        self.assertIn("não se aplicam ao planejamento", sistema)
        self.assertIn("Não escreva os cinco parágrafos", sistema)
        dados = json.loads(mensagens[1]["content"])
        self.assertEqual(dados["campo"], 0)
        self.assertEqual(dados["intensidade_dramatica"], 4)

    def test_todos_anteriores_atuais_e_planejamento_integral_sao_enviados(self):
        contexto = self.contexto(5)
        mensagens = api.construir_mensagens(**contexto)
        dados = json.loads(mensagens[1]["content"])
        self.assertEqual(dados["planejamento_integral_atual"], contexto["planejamento"])
        self.assertEqual([item["texto"] for item in dados["todos_os_paragrafos_anteriores_atuais"]],
                         [item["texto"] for item in contexto["anteriores"]])
        self.assertEqual(dados["campo"], 5)
        self.assertEqual(dados["instrucoes_adicionais_deste_campo"], contexto["instrucoes"])

    def test_regras_completas_e_funcao_especifica_estao_em_cada_movimento(self):
        for indice, funcao in ((1, "situação inicial"), (2, "conflito narrativo"),
                               (3, "situações recorrentes"), (4, "tensão"), (5, "sem resolução")):
            with self.subTest(indice=indice):
                sistema = api.construir_mensagens(**self.contexto(indice))[0]["content"]
                for requisito in (f"somente o parágrafo {indice}", funcao, "exatamente um parágrafo",
                                  "exatamente cinco períodos", "entre 20 e 36 palavras", "no máximo 180 palavras",
                                  "primeira pessoa", "português brasileiro", "dois-pontos", "ponto e vírgula",
                                  "reticências", "travessões de diálogo", "abreviações com ponto",
                                  "terminologia psicanalítica", "diagnósticos", "explicações psicológicas prontas",
                                  "todos os parágrafos anteriores", "Nunca reescreva", "intensidade dramática solicitada é 4"):
                    self.assertIn(requisito, sistema)

    def test_correcao_inclui_alvo_erros_e_preserva_contexto(self):
        contexto = self.contexto(3, texto_atual="Parágrafo que precisa de correção.",
                                 erros=["Há um período em vez de cinco."])
        mensagens = api.construir_mensagens(**contexto)
        dados = json.loads(mensagens[1]["content"])
        self.assertEqual(dados["operacao"], "corrigir_apenas_campo_atual")
        self.assertEqual(dados["texto_atual_a_corrigir"], contexto["texto_atual"])
        self.assertEqual(dados["erros_de_validacao"], contexto["erros"])
        self.assertEqual(len(dados["todos_os_paragrafos_anteriores_atuais"]), 2)
        self.assertIn("Não altere o planejamento nem os parágrafos anteriores", mensagens[0]["content"])

    def test_regeneracao_inclui_versao_atual_sem_tratar_como_correcao(self):
        mensagens = api.construir_mensagens(**self.contexto(texto_atual="Minha versão editada."))
        dados = json.loads(mensagens[1]["content"])
        self.assertEqual(dados["operacao"], "gerar_apenas_campo_atual")
        self.assertEqual(dados["versao_atual_do_campo_para_regeneracao"], "Minha versão editada.")
        self.assertNotIn("texto_atual_a_corrigir", dados)

    def test_contexto_incompleto_e_parametros_invalidos_sao_rejeitados(self):
        alteracoes = ({"indice": 6}, {"indice": True}, {"intensidade": 0}, {"intensidade": 6},
                      {"intensidade": True}, {"planejamento": ""}, {"anteriores": ["extra"]},
                      {"erros": ["erro"], "texto_atual": ""}, {"erros": [None]}, {"instrucoes": None})
        for valores in alteracoes:
            with self.subTest(valores=valores), self.assertRaises(api.ErroAPINarrativa):
                api.construir_mensagens(**self.contexto(**valores))
        with self.assertRaises(api.ErroAPINarrativa):
            api.construir_mensagens(**self.contexto(4, anteriores=["Somente o último."]))


class ClienteNarrativaTests(unittest.TestCase):
    CHAVE_FICTICIA = "credencial-ficticia-exclusiva-deste-teste"
    TEXTO_PRIVADO = "dados narrativos privados que não devem aparecer em erros"

    def setUp(self):
        self.env = patch.dict(os.environ, {api.VARIAVEL_CHAVE: self.CHAVE_FICTICIA})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.construir_cliente = patch("api_narrativas.build_opener")
        self.opener_factory = self.construir_cliente.start()
        self.addCleanup(self.construir_cliente.stop)
        self.cliente = self.opener_factory.return_value
        self.resposta = self.cliente.open.return_value.__enter__.return_value
        self.conteudo({"choices": [{"finish_reason": "stop", "message": {"content": "  Um texto completo.  "}}]})

    def conteudo(self, payload):
        self.resposta.read.return_value = json.dumps(payload).encode("utf-8")

    def gerar(self, **extras):
        valores = {
            "indice": 1, "ideia_inicial": self.TEXTO_PRIVADO, "planejamento": "Plano atual aprovado.",
            "anteriores": [], "instrucoes": "Minha instrução adicional.", "intensidade": 3,
        }
        valores.update(extras)
        return api.gerar_texto(**valores)

    def verificar_erro_seguro(self, funcao, mensagem=None):
        with self.assertRaises(api.ErroAPINarrativa) as capturado:
            funcao()
        erro = str(capturado.exception)
        self.assertNotIn(self.CHAVE_FICTICIA, erro)
        self.assertNotIn(self.TEXTO_PRIVADO, erro)
        if mensagem:
            self.assertIn(mensagem, erro)
        return capturado.exception

    def test_openrouter_padrao_um_pedido_com_chave_no_header_e_sem_stream(self):
        self.assertEqual(self.gerar(), "Um texto completo.")
        self.cliente.open.assert_called_once()
        pedido = self.cliente.open.call_args.args[0]
        corpo = json.loads(pedido.data)
        self.assertEqual(pedido.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(pedido.method, "POST")
        self.assertEqual(pedido.get_header("Authorization"), f"Bearer {self.CHAVE_FICTICIA}")
        self.assertEqual(corpo["model"], "openai/gpt-4.1-mini")
        self.assertFalse(corpo["stream"])
        self.assertEqual(corpo["n"], 1)
        self.assertNotIn(self.CHAVE_FICTICIA, pedido.data.decode())
        self.assertIn(self.TEXTO_PRIVADO, corpo["messages"][1]["content"])
        self.assertEqual(self.cliente.open.call_args.kwargs["timeout"], api.TIMEOUT_SEGUNDOS)
        self.resposta.read.assert_called_once_with(api.LIMITE_RESPOSTA_BYTES + 1)

    def test_openai_tem_endpoint_fixo_e_modelo_escolhido(self):
        self.gerar(provedor="openai", modelo="gpt-4.1-mini")
        pedido = self.cliente.open.call_args.args[0]
        self.assertEqual(pedido.full_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(json.loads(pedido.data)["model"], "gpt-4.1-mini")

    def test_provedor_arbitrario_e_modelo_vazio_nao_fazem_pedidos(self):
        self.verificar_erro_seguro(lambda: self.gerar(provedor="https://outro.test/api"))
        self.verificar_erro_seguro(lambda: self.gerar(modelo=" "))
        self.cliente.open.assert_not_called()

    def test_status_configuracao_informa_presenca_sem_expor_chave(self):
        status = api.status_configuracao()
        self.assertTrue(status["chave_configurada"])
        self.assertEqual(status["variavel_chave"], "NARRATIVA_API_KEY")
        self.assertEqual(status["provedor"], "openrouter")
        self.assertNotIn(self.CHAVE_FICTICIA, repr(status))
        self.assertEqual(api.status_configuracao("openai")["modelo_padrao"], "gpt-4.1-mini")
        with patch.dict(os.environ, {api.VARIAVEL_CHAVE: "  "}):
            self.assertFalse(api.status_configuracao()["chave_configurada"])

    def test_credencial_ausente_ou_header_invalido_nao_faz_pedido(self):
        for chave in ("", "  ", "credencial\r\nOutra: valor", "chave com espaço", "chave-ç"):
            with self.subTest(chave=chave), patch.dict(os.environ, {api.VARIAVEL_CHAVE: chave}):
                self.verificar_erro_seguro(self.gerar, "NARRATIVA_API_KEY")
        self.cliente.open.assert_not_called()

    def test_redirecionamento_e_bloqueado_antes_de_novo_pedido(self):
        self.gerar()
        handler = self.opener_factory.call_args.args[0]
        self.assertIsInstance(handler, api._SemRedirecionamento)
        pedido = Request("https://openrouter.ai/api/v1/chat/completions", headers={
            "Authorization": f"Bearer {self.CHAVE_FICTICIA}",
        })
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.assertIsNone(handler.redirect_request(pedido, None, status, "redirect", {}, "https://outro.test"))

    def test_erros_http_nao_expoem_credenciais_corpo_ou_justificativa(self):
        for status, mensagem in ((401, "autenticação"), (403, "autenticação"), (402, "créditos"),
                                 (429, "limite de solicitações"), (302, "redirecionar"),
                                 (400, "modelo"), (404, "modelo"), (500, "não concluiu")):
            with self.subTest(status=status):
                self.cliente.open.reset_mock()
                self.cliente.open.side_effect = HTTPError(
                    "https://openrouter.ai/api/v1/chat/completions", status,
                    self.CHAVE_FICTICIA, {}, io.BytesIO(self.TEXTO_PRIVADO.encode()),
                )
                erro = self.verificar_erro_seguro(self.gerar, mensagem)
                self.assertTrue(erro.__suppress_context__)
                self.cliente.open.assert_called_once()

    def test_timeout_e_conexao_nao_repetem_pedido_nem_expoem_detalhes(self):
        for falha, mensagem in ((socket.timeout(self.CHAVE_FICTICIA), "demorou"),
                                 (URLError(socket.timeout(self.CHAVE_FICTICIA)), "demorou"),
                                 (URLError(self.CHAVE_FICTICIA), "conectar"),
                                 (OSError(self.TEXTO_PRIVADO), "conectar")):
            with self.subTest(falha=type(falha).__name__):
                self.cliente.open.reset_mock()
                self.cliente.open.side_effect = falha
                erro = self.verificar_erro_seguro(self.gerar, mensagem)
                self.assertTrue(erro.__suppress_context__)
                self.cliente.open.assert_called_once()

    def test_respostas_incompletas_recusadas_ou_invalidas_sao_rejeitadas(self):
        payloads = [
            {}, [], {"error": {"message": self.CHAVE_FICTICIA}},
            {"choices": []}, {"choices": [None]},
            {"choices": [{"finish_reason": "stop", "message": None}]},
            {"choices": [{"finish_reason": "length", "message": {"content": "incompleto"}}]},
            {"choices": [{"finish_reason": "content_filter", "message": {"content": "bloqueado"}}]},
            {"choices": [{"finish_reason": "stop", "message": {"refusal": self.CHAVE_FICTICIA, "content": "não"}}]},
            {"choices": [{"finish_reason": "tool_calls", "message": {"content": "ferramenta"}}]},
            {"choices": [{"message": {"content": "sem confirmação de conclusão"}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": " "}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": ["texto"]}}]},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                self.conteudo(payload)
                self.verificar_erro_seguro(self.gerar)
        for dados in (b"not JSON", b"\xff", b"[" * 10000 + b"]" * 10000,
                      b"x" * (api.LIMITE_RESPOSTA_BYTES + 1)):
            with self.subTest(tamanho=len(dados)):
                self.resposta.read.return_value = dados
                self.verificar_erro_seguro(self.gerar)

    def test_contexto_grande_e_incompleto_nao_faz_pedido(self):
        self.verificar_erro_seguro(lambda: self.gerar(ideia_inicial="x" * api.LIMITE_PEDIDO_BYTES), "excedeu")
        self.verificar_erro_seguro(lambda: api.gerar_texto(indice=1), "contexto")
        self.cliente.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
