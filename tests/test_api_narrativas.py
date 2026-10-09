"""Pedidos e falhas dos provedores são simulados, sem tráfego externo."""

import copy
import io
import json
import os
import socket
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

import api_narrativas as api
from narrativas import composicao_padrao


class PromptNarrativaTests(unittest.TestCase):
    def contexto(self, indice=1, **alteracoes):
        contexto = {
            "indice": indice,
            "anteriores": [{"id": numero, "label": "Anterior", "texto": f"Versão atual completa {numero}."}
                           for numero in range(1, indice)],
            "instrucoes": "Preserve a carta sem explicar o segredo.",
            "intensidade": 4,
        }
        contexto.update(alteracoes)
        return contexto

    def test_introducao_nasce_das_instrucoes_sem_planejamento(self):
        mensagens = api.construir_mensagens(**self.contexto(instrucoes="Voltei à casa onde escondi uma carta."))
        sistema = mensagens[0]["content"]
        self.assertIn("correspondente a Introdução", sistema)
        self.assertIn("instruções adicionais do primeiro movimento como ponto de partida", sistema)
        self.assertEqual(sistema.count(api._INSTRUCOES_LINGUISTICAS), 1)
        self.assertNotIn("planejamento", sistema)
        self.assertNotIn("ideia geral", sistema)
        dados = json.loads(mensagens[1]["content"])
        self.assertEqual(dados["campo"], 1)
        self.assertEqual(dados["intensidade_dramatica"], 4)
        self.assertEqual(dados["instrucoes_adicionais_deste_campo"], "Voltei à casa onde escondi uma carta.")
        self.assertEqual(dados["todos_os_paragrafos_anteriores_atuais"], [])
        self.assertNotIn("ideia_inicial", dados)
        self.assertNotIn("planejamento_integral_atual", dados)
        sem_instrucoes = api.construir_mensagens(**self.contexto(instrucoes=""))
        self.assertIn("Se não houver instruções, invente um episódio ficcional", sem_instrucoes[0]["content"])

    def test_todos_anteriores_atuais_e_instrucoes_editadas_sao_enviados(self):
        contexto = self.contexto(5, instrucoes="Instruções editadas pelo usuário.")
        for paragrafo in contexto["anteriores"]:
            paragrafo["texto"] = f"Versão editada pelo usuário do parágrafo {paragrafo['id']}."
        antes = copy.deepcopy(contexto)
        mensagens = api.construir_mensagens(**contexto)
        dados = json.loads(mensagens[1]["content"])
        self.assertEqual([item["texto"] for item in dados["todos_os_paragrafos_anteriores_atuais"]],
                         [item["texto"] for item in contexto["anteriores"]])
        self.assertEqual(dados["campo"], 5)
        self.assertEqual(dados["instrucoes_adicionais_deste_campo"], contexto["instrucoes"])
        self.assertEqual(contexto, antes)

    def test_regras_completas_e_funcao_especifica_estao_em_cada_movimento(self):
        for indice, funcao in ((1, "situação inicial"), (2, "conflito narrativo"),
                               (3, "situações recorrentes"), (4, "tensão"), (5, "sem resolução")):
            with self.subTest(indice=indice):
                sistema = api.construir_mensagens(**self.contexto(indice))[0]["content"]
                self.assertEqual(sistema.count(api._INSTRUCOES_LINGUISTICAS), 1)
                for requisito in (f"somente o parágrafo {indice}", funcao, "exatamente um parágrafo",
                                  "exatamente cinco períodos", "A extensão dos períodos é livre",
                                  "primeira pessoa", "português brasileiro", "dois-pontos", "ponto e vírgula",
                                  "reticências", "travessões de diálogo", "abreviações com ponto",
                                  "terminologia psicanalítica", "diagnósticos", "explicações psicológicas prontas",
                                  "todos os parágrafos anteriores", "Nunca reescreva", "intensidade dramática solicitada é 4"):
                    self.assertIn(requisito, sistema)
                self.assertNotIn("entre 20 e 36 palavras", sistema)
                self.assertNotIn("180 palavras", sistema)

    def test_todas_opcoes_funcionam_sem_ativar_os_outros_controles(self):
        opcoes = {
            "encadeamento": {
                "progressivo": "informações avançarem progressivamente",
                "causal": "relações de causa e consequência",
                "temporal": "relações cronológicas claras",
                "retomada": "Retome informações, objetos ou personagens",
            },
            "sintaxe": {
                "afirmacao_negacao": "restrições, ressalvas, oposições ou recusas",
                "contraste": "contrastes sintáticos",
                "paralelismo": "construções sintáticas paralelas",
                "inversao": "inversões da ordem habitual",
                "subordinacao": "orações subordinadas",
            },
            "ritmo": {
                "regular": "sem exigir contagem idêntica de palavras",
                "crescente": "progressivamente mais longos",
                "decrescente": "progressivamente mais curtos",
                "alternado": "Alterne períodos relativamente longos e curtos",
                "irregular": "sem padrão fixo nem alternância obrigatória",
            },
        }
        for nome, tipos in opcoes.items():
            for tipo, trecho in tipos.items():
                with self.subTest(controle=nome, tipo=tipo):
                    composicao = composicao_padrao()
                    composicao[nome].update(ativo=True, tipo=tipo)
                    mensagens = api.construir_mensagens(**self.contexto(composicao=composicao))
                    sistema = mensagens[0]["content"]
                    dados = json.loads(mensagens[1]["content"])
                    self.assertIn(trecho, sistema)
                    self.assertEqual(list(dados["controles_de_composicao_ativos"]), [nome])
                    self.assertEqual(dados["controles_de_composicao_ativos"][nome]["tipo"], tipo)
                    for outro in set(opcoes) - {nome}:
                        self.assertNotIn(f"{outro.capitalize()} ativo", sistema)
                    if tipo == "afirmacao_negacao":
                        self.assertIn("Evite repetições mecânicas", sistema)

    def test_desligados_ignoram_tipos_e_instrucoes_sem_modificar_configuracao(self):
        composicao = composicao_padrao()
        for nome, tipo in (("encadeamento", "retomada"), ("sintaxe", "inversao"), ("ritmo", "irregular")):
            composicao[nome].update(tipo=tipo, instrucoes=f"Instrução desativada de {nome}.")
        antes = copy.deepcopy(composicao)
        padrao = api.construir_mensagens(**self.contexto())
        desligados = api.construir_mensagens(**self.contexto(composicao=composicao))
        self.assertEqual(desligados, padrao)
        self.assertNotIn("controles_de_composicao_ativos", json.loads(desligados[1]["content"]))
        self.assertEqual(composicao, antes)

    def test_customizacoes_complementam_controles_ativos_e_regras_gerais(self):
        composicao = composicao_padrao()
        for nome in composicao:
            composicao[nome].update(ativo=True, instrucoes=f"Instruções particulares de {nome}.\nPreserve minha escolha.")
        antes = copy.deepcopy(composicao)
        mensagens = api.construir_mensagens(**self.contexto(4, composicao=composicao))
        sistema = mensagens[0]["content"]
        dados = json.loads(mensagens[1]["content"])
        self.assertIn("sem substituir as regras gerais da narrativa", sistema)
        self.assertIn("Os cinco movimentos formam uma única história", sistema)
        self.assertIn("Preserve a correção gramatical", sistema)
        self.assertIn("exatamente cinco períodos", sistema)
        self.assertIn("primeira pessoa", sistema)
        for nome, controle in composicao.items():
            self.assertEqual(dados["controles_de_composicao_ativos"][nome]["instrucoes_personalizadas"], controle["instrucoes"])
            self.assertEqual(sistema.count(f"{nome.capitalize()} ativo"), 1)
        self.assertEqual(composicao, antes)

    def test_correcao_inclui_alvo_erros_e_preserva_contexto(self):
        composicao = composicao_padrao()
        composicao["ritmo"].update(ativo=True, tipo="alternado")
        contexto = self.contexto(3, texto_atual="Parágrafo que precisa de correção.",
                                 erros=["Há um período em vez de cinco."], composicao=composicao)
        antes = copy.deepcopy(contexto)
        mensagens = api.construir_mensagens(**contexto)
        dados = json.loads(mensagens[1]["content"])
        self.assertEqual(dados["operacao"], "corrigir_apenas_campo_atual")
        self.assertEqual(dados["texto_atual_a_corrigir"], contexto["texto_atual"])
        self.assertEqual(dados["erros_de_validacao"], contexto["erros"])
        self.assertEqual(len(dados["todos_os_paragrafos_anteriores_atuais"]), 2)
        self.assertIn("Não altere os parágrafos anteriores", mensagens[0]["content"])
        self.assertEqual(mensagens[0]["content"].count(api._INSTRUCOES_LINGUISTICAS), 1)
        self.assertIn("Alterne períodos relativamente longos e curtos", mensagens[0]["content"])
        self.assertEqual(contexto, antes)

    def test_regeneracao_inclui_versao_atual_sem_tratar_como_correcao(self):
        composicao = composicao_padrao()
        composicao["sintaxe"].update(ativo=True, tipo="subordinacao")
        contexto = self.contexto(texto_atual="Minha versão editada.", composicao=composicao)
        antes = copy.deepcopy(contexto)
        mensagens = api.construir_mensagens(**contexto)
        dados = json.loads(mensagens[1]["content"])
        self.assertEqual(dados["operacao"], "gerar_apenas_campo_atual")
        self.assertEqual(dados["versao_atual_do_campo_para_regeneracao"], "Minha versão editada.")
        self.assertNotIn("texto_atual_a_corrigir", dados)
        self.assertEqual(mensagens[0]["content"].count(api._INSTRUCOES_LINGUISTICAS), 1)
        self.assertIn("Use orações subordinadas", mensagens[0]["content"])
        self.assertEqual(contexto, antes)

    def test_contexto_incompleto_e_parametros_invalidos_sao_rejeitados(self):
        alteracoes = ({"indice": 0}, {"indice": 6}, {"indice": True}, {"intensidade": 0}, {"intensidade": 6},
                      {"intensidade": True}, {"anteriores": ["extra"]},
                      {"erros": ["erro"], "texto_atual": ""}, {"erros": [None]}, {"instrucoes": None},
                      {"composicao": {}}, {"composicao": "inválida"})
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
            "indice": 1, "anteriores": [], "instrucoes": self.TEXTO_PRIVADO, "intensidade": 3,
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
        anteriores = [f"Parágrafo {indice} editado pelo usuário." for indice in range(1, 5)]
        self.assertEqual(self.gerar(indice=5, anteriores=anteriores), "Um texto completo.")
        self.cliente.open.assert_called_once()
        pedido = self.cliente.open.call_args.args[0]
        corpo = json.loads(pedido.data)
        self.assertEqual(pedido.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(pedido.method, "POST")
        self.assertEqual(pedido.get_header("Authorization"), f"Bearer {self.CHAVE_FICTICIA}")
        self.assertEqual(corpo["model"], "openai/gpt-4.1-mini")
        self.assertFalse(corpo["stream"])
        self.assertEqual(corpo["n"], 1)
        self.assertNotIn("max_tokens", corpo)
        self.assertNotIn(self.CHAVE_FICTICIA, pedido.data.decode())
        self.assertIn(self.TEXTO_PRIVADO, corpo["messages"][1]["content"])
        contexto = json.loads(corpo["messages"][1]["content"])
        self.assertEqual(contexto["campo"], 5)
        self.assertEqual([item["texto"] for item in contexto["todos_os_paragrafos_anteriores_atuais"]], anteriores)
        self.assertIn("Produza somente o parágrafo 5", corpo["messages"][0]["content"])
        self.assertNotIn("ideia_inicial", contexto)
        self.assertNotIn("planejamento_integral_atual", contexto)
        self.assertEqual(self.cliente.open.call_args.kwargs["timeout"], api.TIMEOUT_SEGUNDOS)
        self.resposta.read.assert_called_once_with(api.LIMITE_RESPOSTA_BYTES + 1)

    def test_messages_api_sao_os_mesmos_da_previa_com_controles_ativos(self):
        composicao = composicao_padrao()
        composicao["encadeamento"].update(ativo=True, tipo="causal", instrucoes="Explique a relação dos acontecimentos.")
        composicao["sintaxe"].update(ativo=True, tipo="contraste", instrucoes="Contraste o que eu digo e faço.")
        composicao["ritmo"].update(ativo=True, tipo="crescente", instrucoes="Amplie a cadência no final.")
        for edicao in ({}, {"texto_atual": "Texto em sua versão editada."},
                       {"texto_atual": "Texto em sua versão editada.", "erros": ["Há menos de cinco períodos."]}):
            with self.subTest(edicao=edicao):
                contexto = {
                    "indice": 2, "anteriores": ["Primeiro parágrafo editado."], "instrucoes": self.TEXTO_PRIVADO,
                    "intensidade": 3, "composicao": composicao, **edicao,
                }
                previa = api.construir_mensagens(**contexto)
                self.cliente.open.reset_mock()
                self.gerar(**contexto)
                self.cliente.open.assert_called_once()
                corpo = json.loads(self.cliente.open.call_args.args[0].data)
                self.assertEqual(corpo["messages"], previa)

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
        self.verificar_erro_seguro(lambda: self.gerar(instrucoes="x" * api.LIMITE_PEDIDO_BYTES), "excedeu")
        self.verificar_erro_seguro(lambda: self.gerar(indice=0), "entre 1 e 5")
        self.verificar_erro_seguro(lambda: self.gerar(ideia_inicial="campo removido"), "contexto")
        self.verificar_erro_seguro(lambda: self.gerar(planejamento="campo removido"), "contexto")
        self.verificar_erro_seguro(lambda: api.gerar_texto(indice=1), "contexto")
        self.cliente.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
