"""Limites do relato, preservação Unicode e fluxo assíncrono da página local."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agente_analista.app import ServicoAnalista, criar_app
from agente_analista.entrada import ErroEntrada, contar_relato, validar_relato


class EntradaTests(unittest.TestCase):
    def test_vazio_e_paragrafos_invalidos(self):
        for texto, trecho in (("", "Cole"), (" \t\r\n", "Cole"), ("um", "1"),
                               ("um\n\ndois\n\ntrês", "3")):
            with self.subTest(texto=texto), self.assertRaises(ErroEntrada) as erro:
                validar_relato(texto)
            self.assertIn(trecho, str(erro.exception))

    def test_dois_paragrafos_sem_minimo(self):
        self.assertEqual(validar_relato("a\n\nb")["palavras"], 2)

    def test_400_e_401_palavras(self):
        texto = " ".join(["a"] * 200) + "\n \t\n" + " ".join(["b"] * 200)
        self.assertEqual(validar_relato(texto)["palavras"], 400)
        with self.assertRaisesRegex(ErroEntrada, "401"):
            validar_relato(texto + " c")

    def test_original_e_offsets_unicode_preservados(self):
        texto = " \tNão é sonho 😀; a lembrança\tvoltou.\r\n\t \r\n\r\n  Não desejo esquecê-la.  \n"
        relato = validar_relato(texto)
        self.assertEqual(relato["texto"], texto)
        self.assertEqual([p["id"] for p in relato["paragrafos"]], ["P1", "P2"])
        for p in relato["paragrafos"]:
            self.assertEqual(texto[p["inicio"]:p["fim"]], p["texto"])
        self.assertEqual(relato["paragrafos"][0]["inicio"], 2)
        self.assertIn("😀", relato["paragrafos"][0]["texto"])

    def test_mesma_regra_whitespace_unicode(self):
        texto = "a\u0085b\u001Cc\u00A0d\u2028e\n\nf\u2003g"
        self.assertEqual(validar_relato(texto)["palavras"], 7)

    def test_todas_quebras_e_linhas_vazias(self):
        for quebra in ("\n", "\r\n", "\r"):
            texto = "um" + quebra + " \t" + quebra + quebra + "dois"
            with self.subTest(quebra=quebra):
                self.assertEqual(len(contar_relato(texto)["paragrafos"]), 2)

    def test_crlf_unico_nao_e_linha_em_branco(self):
        self.assertEqual(len(contar_relato("uma linha\r\noutra linha")["paragrafos"]), 1)
        texto = "uma linha\r\noutra linha\r\n \t\r\nsegundo parágrafo"
        relato = validar_relato(texto)
        self.assertEqual(relato["paragrafos"][0]["texto"], "uma linha\r\noutra linha")


class ServicoTeste:
    def __init__(self):
        self.iniciou = Event()
        self.liberar = Event()
        self.relato = None
        self.credenciais = None
        self.pedidos = []
        self.falhar = False

    def status(self):
        return {"pronto": True, "problemas": []}

    def executar(self, relato, progresso, *, provedor, modelo, chave_api):
        self.relato = relato
        self.credenciais = {"provedor": provedor, "modelo": modelo, "chave_api": chave_api}
        self.pedidos.append(self.credenciais.copy())
        self.iniciou.set()
        progresso("Conferindo citações")
        if not self.liberar.wait(5):
            raise RuntimeError("Teste não liberou a tarefa.")
        if self.falhar:
            raise RuntimeError("conteúdo privado que não deve ser exibido")
        return {"relato": relato, "ligacoes": [], "mensagem": "Nenhuma ligação sustentada."}


class PaginaTests(unittest.TestCase):
    def setUp(self):
        self.servico = ServicoTeste()
        self.app = criar_app({"TESTING": True}, servico=self.servico)
        self.client = self.app.test_client()

    def tearDown(self):
        self.servico.liberar.set()
        self.app.extensions["agente_analista"]["executor"].shutdown(wait=True)

    def aguardar(self, id):
        for _ in range(100):
            resposta = self.client.get(f"/api/buscas/{id}").get_json()
            if resposta["estado"] != "executando":
                return resposta
            time.sleep(.01)
        self.fail("A busca não terminou.")

    def pedido(self, texto="a\n\nb", **alteracoes):
        dados = {"texto": texto, "provedor": "openrouter", "modelo": "openai/modelo-teste",
                 "chave_api": "credencial-somente-pagina"}
        dados.update(alteracoes)
        return dados

    def test_pagina_e_acoes(self):
        resposta = self.client.get("/")
        self.assertEqual(resposta.status_code, 200)
        pagina = resposta.get_data(as_text=True)
        for acao in ("Buscar ligações", "Limpar", "Copiar tabela", "Exportar JSON"):
            self.assertIn(acao, pagina)
        self.assertIn("no-store", resposta.headers["Cache-Control"])

    def test_entrada_invalida_nao_inicia(self):
        self.assertEqual(self.client.post("/api/buscas", json={"texto": "um"}).status_code, 400)
        self.assertFalse(self.servico.iniciou.is_set())
        self.assertEqual(self.client.post("/api/buscas", json={"texto": []}).status_code, 400)
        self.assertEqual(self.client.post("/api/buscas", data="texto").status_code, 415)

    def test_progresso_preservacao_e_envio_duplicado(self):
        texto = "Não sonho 😀.\r\n \t\r\nLembro da infância."
        resposta = self.client.post("/api/buscas", json=self.pedido(texto))
        self.assertEqual(resposta.status_code, 202)
        id = resposta.get_json()["id"]
        self.assertTrue(self.servico.iniciou.wait(1))
        progresso = self.client.get(f"/api/buscas/{id}").get_json()
        self.assertEqual(progresso["estado"], "executando")
        self.assertEqual(self.client.post("/api/buscas", json=self.pedido(texto)).status_code, 409)
        self.servico.liberar.set()
        resultado = self.aguardar(id)
        self.assertEqual(resultado["estado"], "concluido")
        self.assertEqual(resultado["resultado"]["relato"]["texto"], texto)

    def test_falha_segura(self):
        self.servico.falhar = True
        self.servico.liberar.set()
        id = self.client.post("/api/buscas", json=self.pedido()).get_json()["id"]
        resposta = self.aguardar(id)
        self.assertEqual(resposta["estado"], "erro")
        self.assertNotIn("privado", resposta["erro"])

    def test_origem_e_host_locais(self):
        self.assertEqual(self.client.post("/api/buscas", json={"texto": "a\n\nb"},
                                         headers={"Origin": "https://externo.example"}).status_code, 403)
        self.assertEqual(self.client.get("/", headers={"Host": "externo.example"}).status_code, 403)

    def test_falta_configuracao_acao_visivel(self):
        self.servico.status = lambda: {"pronto": False, "problemas": ["Prepare o modelo E5."]}
        resposta = self.client.post("/api/buscas", json=self.pedido())
        self.assertEqual(resposta.status_code, 503)
        self.assertIn("E5", resposta.get_json()["erro"])

    def test_credenciais_ausentes_invalidas_nao_iniciam_nem_usam_env(self):
        casos = []
        for campo in ("chave_api", "provedor", "modelo"):
            ausente = self.pedido()
            del ausente[campo]
            casos.append(ausente)
            for valor in (None, "", "   ", [], 42):
                casos.append(self.pedido(**{campo: valor}))
        casos.extend(self.pedido(provedor=valor) for valor in ("invalido", "OpenRouter", "https://externo.example"))
        casos.extend(self.pedido(chave_api=valor) for valor in ("não-ascii", "chave com espaços", "chave\r\nHeader:valor", "chave\x7f"))
        casos.extend(self.pedido(modelo=valor) for valor in ("x" * 201, "modelo\r\ninjetado"))
        with patch.dict(os.environ, {"NARRATIVA_API_KEY": "credencial-do-servidor",
                                     "AGENTE_ANALISTA_PROVEDOR": "openai",
                                     "AGENTE_ANALISTA_MODELO": "modelo-servidor"}):
            for dados in casos:
                with self.subTest(dados=dados):
                    resposta = self.client.post("/api/buscas", json=dados)
                    self.assertEqual(resposta.status_code, 400)
                    serializado = resposta.get_data(as_text=True)
                    self.assertNotIn("credencial-do-servidor", serializado)
                    self.assertNotIn("credencial-somente-pagina", serializado)
                    self.assertFalse(self.servico.iniciou.is_set())
                    self.assertEqual(self.app.extensions["agente_analista"]["tarefas"], {})

    def test_recebe_credenciais_da_pagina_sem_guardar_em_tarefas_ou_resposta(self):
        self.servico.liberar.set()
        dados = self.pedido()
        resposta = self.client.post("/api/buscas", json=dados)
        self.assertEqual(resposta.status_code, 202)
        resultado = self.aguardar(resposta.get_json()["id"])
        self.assertEqual(self.servico.credenciais, {k: dados[k] for k in ("provedor", "modelo", "chave_api")})
        self.assertNotIn(dados["chave_api"], json.dumps(resultado))
        self.assertNotIn(dados["chave_api"], json.dumps(self.app.extensions["agente_analista"]["tarefas"]))
        self.assertNotIn("chave_api", json.dumps(resultado))

    def test_buscas_sucessivas_credenciais_independentes_sem_alterar_env(self):
        self.servico.liberar.set()
        with patch.dict(os.environ, {"NARRATIVA_API_KEY": "credencial-servidor-intacta",
                                     "AGENTE_ANALISTA_PROVEDOR": "openrouter",
                                     "AGENTE_ANALISTA_MODELO": "modelo-servidor-intacto"}):
            ambiente = os.environ.copy()
            primeiro = self.pedido(chave_api="credencial-primeira", modelo="fabricante/modelo-um")
            segundo = self.pedido(provedor="openai", chave_api="credencial-segunda", modelo="modelo-dois")
            for dados in (primeiro, segundo):
                resposta = self.client.post("/api/buscas", json=dados)
                self.assertEqual(resposta.status_code, 202)
                tarefa = self.aguardar(resposta.get_json()["id"])
                self.assertEqual(tarefa["estado"], "concluido")
                self.assertNotIn(dados["chave_api"], json.dumps(tarefa))
            self.assertEqual(self.servico.pedidos, [
                {k: dados[k] for k in ("provedor", "modelo", "chave_api")} for dados in (primeiro, segundo)])
            self.assertEqual(os.environ.copy(), ambiente)

    def test_erros_conhecidos_nao_revelam_chave_recebida(self):
        from agente_analista.ligacoes import ErroLigacoes

        def falhar(relato, progresso, *, provedor, modelo, chave_api):
            raise ErroLigacoes("Falha ao usar " + chave_api)

        self.servico.executar = falhar
        dados = self.pedido()
        resposta = self.client.post("/api/buscas", json=dados)
        tarefa = self.aguardar(resposta.get_json()["id"])
        self.assertEqual(tarefa["estado"], "erro")
        self.assertNotIn(dados["chave_api"], json.dumps(tarefa))
        self.assertNotIn(dados["chave_api"], json.dumps(self.app.extensions["agente_analista"]["tarefas"]))

    def test_status_nunca_retorna_chave(self):
        with patch.dict(os.environ, {"NARRATIVA_API_KEY": "credencial-privada-no-ambiente"}):
            resposta = self.client.get("/api/status")
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.get_json()["pronto"])
        self.assertNotIn("credencial-privada-no-ambiente", resposta.get_data(as_text=True))

    def test_id_inexistente(self):
        self.assertEqual(self.client.get("/api/buscas/nao-existe").status_code, 404)

    def test_resultado_expirado_e_removido(self):
        tarefas = self.app.extensions["agente_analista"]["tarefas"]
        tarefas["expirada"] = {"id": "expirada", "criada": time.monotonic() - 3601,
                                "estado": "concluido", "resultado": {"relato": "privado"}}
        self.assertEqual(self.client.get("/api/buscas/expirada").status_code, 404)
        self.assertNotIn("expirada", tarefas)


class ConfiguracaoTests(unittest.TestCase):
    def test_servico_encaminha_credenciais_da_busca_sem_inclui_las_no_resultado(self):
        relato = validar_relato("Sonho com a casa.\n\nA lembrança retorna.")
        recuperacao = {"consultas": [], "candidatos": []}
        with patch.dict(os.environ, {"NARRATIVA_API_KEY": "credencial-servidor"}):
            servico = ServicoAnalista()
            servico._corpus = Mock(return_value=object())
            servico.buscador = Mock()
            servico.buscador.buscar.return_value = recuperacao
            with patch("agente_analista.ligacoes.avaliar_ligacoes", return_value={"ligacoes": []}) as avaliar:
                resultado = servico.executar(relato, Mock(), provedor="openai", modelo="modelo-pagina",
                                             chave_api="credencial-pagina")
            avaliar.assert_called_once_with(relato, recuperacao, provedor="openai", modelo="modelo-pagina",
                                             chave_api="credencial-pagina")
            self.assertEqual(resultado["justificativas"], {"provedor": "openai", "modelo": "modelo-pagina"})
            self.assertNotIn("credencial-pagina", json.dumps(resultado))
            self.assertNotIn("credencial-servidor", json.dumps(resultado))
            self.assertEqual(os.environ["NARRATIVA_API_KEY"], "credencial-servidor")

    def test_configuracao_malformada_mostra_acao_sem_falhar_pagina(self):
        with TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "config.json"
            servico = ServicoAnalista(caminho_config=caminho)
            servico._corpus = lambda: SimpleNamespace(fragmentos=[], blocos={}, manifesto={})
            for dados in ("[]", "null", "{", "{}"):
                caminho.write_text(dados, encoding="utf-8")
                with self.subTest(dados=dados), patch.dict(os.environ, {"NARRATIVA_API_KEY": "segredo-teste"}):
                    status = servico.status()
                    self.assertFalse(status["pronto"])
                    self.assertIn("preparar_e5_mac_intel.sh", " ".join(status["problemas"]))
                    self.assertNotIn("segredo-teste", json.dumps(status))

    def test_status_pronto_sem_chave_api_ou_modelo_do_provedor_no_ambiente(self):
        with TemporaryDirectory() as pasta, patch.dict(os.environ, {}, clear=True):
            cache = Path(pasta) / "cache"
            os.environ["HF_HUB_CACHE"] = str(cache)
            manifesto = {"modelo": "fabricante/e5", "revisao": "revisao-fixa"}
            snapshot = cache / "models--fabricante--e5/snapshots/revisao-fixa"
            snapshot.mkdir(parents=True)
            for nome in ("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json"):
                (snapshot / nome).touch()
            caminho = Path(pasta) / "config.json"
            caminho.write_text(json.dumps({"modelo_id": manifesto["modelo"], "revisao": manifesto["revisao"],
                "tokenizador_id": manifesto["modelo"], "tokenizador_revisao": manifesto["revisao"],
                "local_files_only": True, "dispositivo": "cpu", "precisao": "float32", "limite_tokens": 512}),
                encoding="utf-8")
            servico = ServicoAnalista(caminho_config=caminho)
            servico._corpus = lambda: SimpleNamespace(fragmentos=[{}], blocos={"B1": {}}, manifesto=manifesto)
            for extras in ({}, {"AGENTE_ANALISTA_PROVEDOR": "invalido", "AGENTE_ANALISTA_MODELO": ""}):
                with self.subTest(extras=extras), patch.dict(os.environ, extras):
                    status = servico.status()
                    self.assertTrue(status["pronto"], status["problemas"])
                    self.assertEqual(status["problemas"], [])
                    self.assertEqual(status["corpus"], {"fragmentos": 1, "blocos": 1})


@unittest.skipUnless(os.environ.get("AGENTE_ANALISTA_TESTE_E5_REAL") == "1", "Inferência real E5 opcional")
class IntegracaoE5Tests(unittest.TestCase):
    def test_busca_da_pagina_com_e5_real_e_provedor_simulado(self):
        def transporte(**pedido):
            self.assertEqual(pedido["chave_api"], "chave-somente-pagina")
            self.assertEqual(pedido["provedor"], "openrouter")
            self.assertEqual(pedido["modelo"], "openai/modelo-teste")
            contexto = pedido["contexto"]
            relato = contexto["relato"]
            fonte = contexto["recuperacao"]["candidatos"][0]
            p = relato["paragrafos"][0]
            f = fonte["fragmentos"][0]
            fim = min(f["fim"], f["inicio"] + 120)
            ligacao = {"paragrafo": p["id"], "relato": {k: p[k] for k in ("inicio", "fim", "texto")},
                       "observacao": "Resposta simulada de teste, sem avaliação interpretativa real.",
                       "conceito": "", "bloco_id": fonte["bloco_id"], "fragmentos_ids": [f["id"]],
                       "freud": {"inicio": f["inicio"], "fim": fim, "texto": fonte["texto"][f["inicio"]:fim]},
                       "ligacao": "Ligação simulada para conferir a integração.",
                       "justificativa": "Conferência técnica da associação de textos e fontes.",
                       "limites": "O provedor foi simulado neste teste.", "alternativas": [], "situacao": "parcial"}
            inventada = {**ligacao, "bloco_id": "fonte-inexistente"}
            return json.dumps({"ligacoes": [ligacao, inventada]}, ensure_ascii=False)

        with patch.dict(os.environ, {"NARRATIVA_API_KEY": "chave-somente-teste"}), \
                patch("agente_analista.ligacoes._enviar_mensagens", side_effect=transporte):
            app = criar_app({"TESTING": True})
            try:
                client = app.test_client()
                self.assertTrue(client.get("/api/status").get_json()["pronto"])
                texto = "Sonho e desejo.\n \t\nVolto a pensar no sonho."
                resposta = client.post("/api/buscas", json={"texto": texto, "provedor": "openrouter",
                                      "modelo": "openai/modelo-teste", "chave_api": "chave-somente-pagina"})
                self.assertEqual(resposta.status_code, 202)
                id = resposta.get_json()["id"]
                limite = time.monotonic() + 90
                while time.monotonic() < limite:
                    tarefa = client.get(f"/api/buscas/{id}").get_json()
                    if tarefa["estado"] != "executando":
                        break
                    time.sleep(.05)
                self.assertEqual(tarefa["estado"], "concluido", tarefa.get("erro"))
                resultado = tarefa["resultado"]
                self.assertEqual(resultado["relato"]["texto"], texto)
                self.assertEqual(resultado["justificativas"], {"provedor": "openrouter", "modelo": "openai/modelo-teste"})
                self.assertNotIn("chave-somente-pagina", json.dumps(resultado))
                self.assertTrue(resultado["ligacoes"][0]["conferencias"]["freud_literal"])
                self.assertEqual(len(resultado["rejeitadas"]), 1)
                self.assertEqual(len(resultado["candidatos"]), 12)
                self.assertEqual(len(resultado["consultas"]), 3)
            finally:
                app.extensions["agente_analista"]["executor"].shutdown(wait=True)
