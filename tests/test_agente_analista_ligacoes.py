"""Conferência literal e avaliação estruturada, sem tráfego para provedores."""

import copy
import json
import os
import unittest
from unittest.mock import Mock, patch

import api_narrativas as api
from api_narrativas import ErroAPINarrativa
from agente_analista import ligacoes


class LigacoesTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        p1 = "Não disse que desejava voltar à casa 🏠."
        p2 = "Recordei a carta, mas hesitei."
        texto = " \t" + p1 + "\n \t\n" + p2 + "\n"
        inicio1, inicio2 = texto.index(p1), texto.index(p2)
        self.relato = {"texto": texto, "palavras": len(texto.split()), "paragrafos": [
            {"id": "P1", "inicio": inicio1, "fim": inicio1 + len(p1), "texto": p1},
            {"id": "P2", "inicio": inicio2, "fim": inicio2 + len(p2), "texto": p2},
        ]}
        primeiro = "A lembrança retorna."
        texto_fonte = primeiro + " O contexto não comprova um desejo."
        self.candidato = {
            "bloco_id": "B1", "texto": texto_fonte, "fragmentos_ids": ["F1", "F2"],
            "fragmentos": [{"id": "F1", "inicio": 0, "fim": len(primeiro)},
                           {"id": "F2", "inicio": len(primeiro), "fim": len(texto_fonte)}],
            "referencia": {"obra": "obra-real", "volume": 1, "secao": None, "paginas": [12],
                           "nota_paginas": "páginas registradas no índice; numeração impressa não conferida"},
            "pontuacoes": [{"metodo": "bm25", "pontuacao": 2.0, "consulta_id": "P1"}], "rrf": 0.02,
        }
        self.recuperacao = {"consultas": [{"id": "Q1", "origem": "P1", "texto": p1}],
                            "candidatos": [self.candidato], "metodo_fusao": {"nome": "RRF", "k": 60}}
        self.item = {
            "paragrafo": "P1", "relato": dict(self.relato["paragrafos"][0]),
            "observacao": "O relato nega ter declarado esse desejo.", "conceito": "Lembrança",
            "bloco_id": "B1", "fragmentos_ids": ["F1"],
            "freud": {"inicio": 0, "fim": len(primeiro), "texto": primeiro},
            "ligacao": "A recorrência da lembrança sugere uma aproximação possível.",
            "justificativa": "O trecho descreve a retomada da lembrança, sem provar um desejo do narrador.",
            "limites": "A fonte não autoriza converter a negação do relato em desejo.",
            "alternativas": ["Uma lembrança comum pode explicar a passagem."], "situacao": "parcial",
        }

    def avaliar(self, itens=None, **kwargs):
        itens = [self.item] if itens is None else itens
        transporte = kwargs.pop("transporte", Mock(return_value=json.dumps({"ligacoes": itens}, ensure_ascii=False)))
        return ligacoes.avaliar_ligacoes(self.relato, self.recuperacao, transporte=transporte, **kwargs)

    def test_proposta_validada_usa_metadados_reais_e_nao_modifica_entrada(self):
        self.item.update(referencia={"obra": "inventada", "paginas": [999]}, pontuacoes=[{"metodo": "inventado"}], rrf=100)
        antes = copy.deepcopy((self.relato, self.recuperacao, self.item))
        resultado = self.avaliar()
        self.assertEqual(len(resultado["ligacoes"]), 1)
        linha = resultado["ligacoes"][0]
        self.assertEqual(linha["id"], "L1")
        self.assertEqual(linha["referencia"], self.candidato["referencia"])
        self.assertEqual(linha["pontuacoes"], self.candidato["pontuacoes"])
        self.assertEqual(linha["rrf"], self.candidato["rrf"])
        self.assertEqual(linha["contexto"], self.candidato["texto"])
        self.assertTrue(all(linha["conferencias"].values()))
        self.assertIn("precisa de revisão", resultado["mensagem"])
        self.assertEqual(resultado["avaliacao"]["blocos_avaliados_ids"], ["B1"])
        self.assertEqual(resultado["avaliacao"]["blocos_nao_avaliados_ids"], [])
        self.assertIsNone(resultado["avaliacao"]["motivo_limite_contexto"])
        self.assertGreater(resultado["avaliacao"]["bytes_pedido"], 0)
        linha["referencia"]["paginas"].append(99)
        self.assertEqual((self.relato, self.recuperacao, self.item), antes)

    def test_prompt_preserva_original_contexto_e_negacao_sem_prompts_narrativos(self):
        mensagens = ligacoes.construir_mensagens(relato=self.relato, recuperacao=self.recuperacao)
        sistema = mensagens[0]["content"]
        dados = json.loads(mensagens[1]["content"])
        self.assertEqual(dados["relato_original"], self.relato["texto"])
        self.assertEqual(dados["fontes_recuperadas"][0]["texto"], self.candidato["texto"])
        self.assertIn("Preserve negações", sistema)
        self.assertIn("dados para examinar", sistema)
        self.assertIn("não são probabilidades", sistema)
        self.assertNotIn("cinco movimentos", sistema)
        self.assertNotIn("intensidade dramática", sistema)
        for paragrafo in dados["paragrafos"]:
            for passagem in paragrafo["passagens_disponiveis"]:
                self.assertEqual(self.relato["texto"][passagem["inicio"]:passagem["fim"]], passagem["texto"])
        for passagem in dados["fontes_recuperadas"][0]["passagens_disponiveis"]:
            self.assertEqual(self.candidato["texto"][passagem["inicio"]:passagem["fim"]], passagem["texto"])

    def test_unicode_no_segundo_paragrafo_usa_posicoes_em_codepoints(self):
        self.item["paragrafo"] = "P2"
        self.item["relato"] = dict(self.relato["paragrafos"][1])
        self.assertEqual(len(self.avaliar()["ligacoes"]), 1)
        self.item["relato"]["inicio"] += 1  # UTF-16 contaria o emoji anterior como duas unidades.
        resultado = self.avaliar()
        self.assertFalse(resultado["ligacoes"])
        self.assertIn("posições", resultado["rejeitadas"][0]["motivo"])

    def test_ids_citacoes_posicoes_e_campos_invalidos_sao_rejeitados_individualmente(self):
        mudancas = [
            {"bloco_id": "B-INVENTADO"}, {"paragrafo": "P3"},
            {"fragmentos_ids": ["F-INVENTADO"]}, {"fragmentos_ids": ["F2"]},
            {"fragmentos_ids": ["F1", "F1"]}, {"fragmentos_ids": ["F1", "F2"]},
            {"freud": {"inicio": 0, "fim": 19, "texto": "A lembranca retorna."}},
            {"freud": {"inicio": -1, "fim": 19, "texto": "A lembrança retorna."}},
            {"freud": {"inicio": True, "fim": 19, "texto": "A lembrança retorna."}},
            {"freud": {"inicio": 0, "fim": 10000, "texto": "A lembrança retorna."}},
            {"freud": {"inicio": 0, "fim": 0, "texto": ""}},
            {"relato": self.relato["paragrafos"][1]},
            {"situacao": "certeza"}, {"justificativa": ""}, {"conceito": 42},
            {"alternativas": "outra leitura"}, {"alternativas": [None]},
        ]
        for mudanca in mudancas:
            with self.subTest(mudanca=mudanca):
                invalida = {**self.item, **mudanca}
                resultado = self.avaliar([invalida, self.item])
                self.assertEqual(len(resultado["ligacoes"]), 1)
                self.assertEqual(len(resultado["rejeitadas"]), 1)
                self.assertEqual(resultado["rejeitadas"][0]["indice_resposta"], 1)
                self.assertNotIn("freud", resultado["rejeitadas"][0])

    def test_citacao_pode_cruzar_fragmentos_cobertos_mas_nao_lacunas(self):
        self.item["freud"] = {"inicio": 0, "fim": len(self.candidato["texto"]), "texto": self.candidato["texto"]}
        self.item["fragmentos_ids"] = ["F1", "F2"]
        self.assertEqual(len(self.avaliar()["ligacoes"]), 1)
        self.candidato["fragmentos"][1]["inicio"] += 1
        self.assertEqual(len(self.avaliar()["rejeitadas"]), 1)
        self.candidato["fragmentos"][1]["inicio"] -= 2  # Sobreposição real não invalida a cobertura.
        self.assertEqual(len(self.avaliar()["ligacoes"]), 1)

    def test_descartadas_ficam_separadas_e_zero_nao_conclui_ausencia_global(self):
        self.item["situacao"] = "descartada"
        resultado = self.avaliar()
        self.assertFalse(resultado["ligacoes"])
        self.assertEqual(len(resultado["descartadas"]), 1)
        self.assertIn("não demonstra ausência", resultado["mensagem"])
        vazio = self.avaliar([])
        self.assertFalse(vazio["ligacoes"])
        self.assertFalse(vazio["descartadas"])
        self.assertIn("não demonstra ausência", vazio["mensagem"])

    def test_ligacao_repetida_e_eliminada(self):
        resultado = self.avaliar([self.item, copy.deepcopy(self.item)])
        self.assertEqual(len(resultado["ligacoes"]), 1)
        self.assertIn("repete", resultado["rejeitadas"][0]["motivo"])

    def test_conceito_candidato_pode_estar_ausente_quando_nao_pertinente(self):
        for conceito in ("", None):
            with self.subTest(conceito=conceito):
                self.item["conceito"] = conceito
                self.item["situacao"] = "descartada"
                resultado = self.avaliar()
                self.assertEqual(resultado["descartadas"][0]["conceito"], "")
                self.assertFalse(resultado["rejeitadas"])
        self.item.pop("conceito")
        self.assertEqual(self.avaliar()["descartadas"][0]["conceito"], "")

    def test_sem_candidatos_nao_chama_provedor(self):
        self.recuperacao["candidatos"] = []
        transporte = Mock()
        resultado = self.avaliar(transporte=transporte)
        transporte.assert_not_called()
        self.assertFalse(resultado["ligacoes"])
        self.assertIn("Nenhum candidato", resultado["mensagem"])
        self.assertEqual(resultado["avaliacao"]["blocos_avaliados_ids"], [])
        self.assertEqual(resultado["avaliacao"]["bytes_pedido"], 0)

    def test_status_nao_expoe_credencial_e_modelo_depende_do_provedor(self):
        self.assertFalse(ligacoes.status_configuracao()["chave_configurada"])
        with patch.dict(os.environ, {"AGENTE_ANALISTA_PROVEDOR": "openai", "NARRATIVA_API_KEY": "credencial-privada"}):
            estado = ligacoes.status_configuracao()
            self.assertTrue(estado["chave_configurada"])
            self.assertEqual(estado["modelo"], "gpt-4.1-mini")
            self.assertNotIn("credencial-privada", json.dumps(estado))
        with patch.dict(os.environ, {"AGENTE_ANALISTA_MODELO": "modelo/configurado"}):
            self.assertEqual(ligacoes.status_configuracao()["modelo"], "modelo/configurado")

    def test_credencial_ausente_bloqueia_transporte_real_com_acao_especifica(self):
        with patch("agente_analista.ligacoes._enviar_mensagens") as enviar:
            with self.assertRaisesRegex(ligacoes.ErroLigacoes, "Configure NARRATIVA_API_KEY"):
                ligacoes.avaliar_ligacoes(self.relato, self.recuperacao)
            enviar.assert_not_called()

    def test_status_informa_configuracao_invalida_sem_levantar_erro_ou_expor_chave(self):
        for dados in ({"AGENTE_ANALISTA_PROVEDOR": "invalido"}, {"AGENTE_ANALISTA_MODELO": ""}):
            with self.subTest(dados=dados), patch.dict(os.environ, {**dados, "NARRATIVA_API_KEY": "credencial-privada"}):
                estado = ligacoes.status_configuracao()
                self.assertTrue(estado["erro"])
                self.assertTrue(estado["chave_configurada"])
                self.assertNotIn("credencial-privada", json.dumps(estado))

    def test_transporte_e_injetavel_e_recebe_prompt_proprio(self):
        transporte = Mock(return_value='{"ligacoes":[]}')
        self.avaliar(transporte=transporte, provedor="openai", modelo="modelo-escolhido")
        dados = transporte.call_args.kwargs
        self.assertEqual(dados["provedor"], "openai")
        self.assertEqual(dados["modelo"], "modelo-escolhido")
        self.assertIs(dados["construtor"], ligacoes.construir_mensagens)
        self.assertEqual(dados["contexto"]["relato"], self.relato)
        self.assertEqual(dados["formato_resposta"], ligacoes._formato_resposta())

    def test_esquema_exige_campos_e_objetos_sem_propriedades_extras(self):
        formato = ligacoes._formato_resposta()
        self.assertEqual(formato["type"], "json_schema")
        self.assertIs(formato["json_schema"]["strict"], True)
        esquema = formato["json_schema"]["schema"]

        def conferir_objetos(valor):
            if isinstance(valor, dict):
                if valor.get("type") == "object":
                    self.assertIs(valor["additionalProperties"], False)
                    self.assertEqual(set(valor["required"]), set(valor["properties"]))
                for filho in valor.values():
                    conferir_objetos(filho)
            elif isinstance(valor, list):
                for filho in valor:
                    conferir_objetos(filho)

        conferir_objetos(esquema)
        self.assertEqual(set(esquema["properties"]), {"ligacoes"})
        item = esquema["properties"]["ligacoes"]["items"]
        self.assertEqual(set(item["properties"]), set(self.item))
        self.assertEqual(set(item["properties"]["paragrafo"]["enum"]), {"P1", "P2"})
        self.assertEqual(set(item["properties"]["situacao"]["enum"]), ligacoes._SITUACOES)
        for campo in ("relato", "freud"):
            passagem = item["properties"][campo]["properties"]
            self.assertEqual(set(passagem), {"inicio", "fim", "texto"})
            self.assertEqual(passagem["inicio"]["type"], "integer")
            self.assertEqual(passagem["fim"]["type"], "integer")
            self.assertEqual(passagem["texto"]["type"], "string")

    def test_pedido_https_usa_esquema_e_contagem_exata_de_bytes(self):
        resposta = {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"ligacoes": [self.item]}, ensure_ascii=False),
        }}]}
        for provedor, modelo in (("openrouter", "openai/gpt-4.1-mini"), ("openai", "gpt-4.1-mini")):
            with self.subTest(provedor=provedor), \
                    patch.dict(os.environ, {api.VARIAVEL_CHAVE: "credencial-ficticia"}), \
                    patch("api_narrativas.build_opener") as fabrica:
                cliente = fabrica.return_value
                cliente.open.return_value.__enter__.return_value.read.return_value = json.dumps(resposta).encode("utf-8")
                resultado = ligacoes.avaliar_ligacoes(self.relato, self.recuperacao, provedor=provedor, modelo=modelo)
                cliente.open.assert_called_once()
                pedido = cliente.open.call_args.args[0]
                corpo = json.loads(pedido.data)
                self.assertEqual(corpo["response_format"], ligacoes._formato_resposta())
                self.assertEqual(resultado["avaliacao"]["bytes_pedido"], len(pedido.data))
                self.assertLessEqual(len(pedido.data), api.LIMITE_PEDIDO_BYTES)
                self.assertEqual(len(resultado["ligacoes"]), 1)
                if provedor == "openrouter":
                    self.assertEqual(corpo["provider"], {"require_parameters": True})
                else:
                    self.assertNotIn("provider", corpo)

    def test_json_puro_ou_uma_cerca_inteira_preservam_citacoes_literais(self):
        conteudo = json.dumps({"ligacoes": [self.item]}, ensure_ascii=False)
        formatos = [conteudo, "\n  " + conteudo + "\t\n",
                    "```json\n" + conteudo + "\n```",
                    "```\n" + conteudo + "\n```",
                    " \t\n```json\r\n" + conteudo + "\r\n```\n "]
        for formato in formatos:
            with self.subTest(formato=formato[:20]):
                transporte = Mock(return_value=formato)
                resultado = self.avaliar(transporte=transporte)
                transporte.assert_called_once()
                self.assertEqual(len(resultado["ligacoes"]), 1)
                self.assertEqual(resultado["ligacoes"][0]["relato"]["texto"], self.item["relato"]["texto"])
                self.assertEqual(resultado["ligacoes"][0]["freud"]["texto"], self.item["freud"]["texto"])

    def test_cerca_json_nao_corrige_citacao_alterada_ou_identificador_inventado(self):
        for mudanca, motivo in (({"freud": {**self.item["freud"], "texto": "A lembranca retorna."}}, "literalmente"),
                                ({"bloco_id": "B-INVENTADO"}, "não pertence"),
                                ({"fragmentos_ids": ["F-INVENTADO"]}, "não pertencem")):
            with self.subTest(mudanca=mudanca):
                invalida = {**copy.deepcopy(self.item), **mudanca}
                transporte = Mock(return_value="```json\n" + json.dumps({"ligacoes": [invalida]}) + "\n```")
                resultado = self.avaliar(transporte=transporte)
                transporte.assert_called_once()
                self.assertFalse(resultado["ligacoes"])
                self.assertEqual(len(resultado["rejeitadas"]), 1)
                self.assertIn(motivo, resultado["rejeitadas"][0]["motivo"])

    def test_limite_do_pedido_inclui_esquema_e_roteamento_antes_de_chamar_provedor(self):
        mensagens = ligacoes.construir_mensagens(relato=self.relato, recuperacao=self.recuperacao)
        comum = api._serializar_pedido(provedor="openrouter", modelo="gpt-4.1-mini", mensagens=mensagens)
        estruturado = api._serializar_pedido(provedor="openrouter", modelo="gpt-4.1-mini", mensagens=mensagens,
                                            formato_resposta=ligacoes._formato_resposta())
        sem_roteamento = api._serializar_pedido(provedor="openai", modelo="gpt-4.1-mini", mensagens=mensagens,
                                               formato_resposta=ligacoes._formato_resposta())
        self.assertGreater(len(estruturado), len(sem_roteamento))
        self.assertGreater(len(sem_roteamento), len(comum))
        # O pedido comum caberia; o esquema e o roteamento ultrapassam o limite.
        transporte = Mock(return_value='{"ligacoes":[]}')
        with patch.object(ligacoes, "LIMITE_PEDIDO_BYTES", len(comum)):
            with self.assertRaisesRegex(ligacoes.ErroLigacoes, "um único bloco excede"):
                self.avaliar(transporte=transporte, modelo="gpt-4.1-mini")
        transporte.assert_not_called()

    def test_json_malformado_e_formato_invalido_nao_geram_ligacoes_fabricadas(self):
        casos = [None, "não é JSON", "[]", "{}",
                 '{"ligacoes":null}', '{"ligacoes":[],"ligacoes":[]}', '{"ligacoes":[],"valor":NaN}',
                 '{"ligacoes":[],"valor":Infinity}', '{"ligacoes":[',
                 '{"ligacoes":[]} {"ligacoes":[]}',
                 'Comentário\n{"ligacoes":[]}', '{"ligacoes":[]}\nComentário',
                 '```json\n{"ligacoes":[]}\n```\nComentário',
                 'Comentário\n```json\n{"ligacoes":[]}\n```',
                 '```json\n{"ligacoes":[]}\n```\n```json\n{"ligacoes":[]}\n```',
                 '```python\n{"ligacoes":[]}\n```',
                 '```json\n{"ligacoes":[],"ligacoes":[]}\n```',
                 '```json\n{"ligacoes":[],"valor":NaN}\n```',
                 '```json\n{"ligacoes":[\n```',
                 json.dumps({"ligacoes": [self.item] * 25})]
        for conteudo in casos:
            with self.subTest(conteudo=str(conteudo)[:60]):
                transporte = Mock(return_value=conteudo)
                with self.assertRaises(ligacoes.ErroLigacoes):
                    self.avaliar(transporte=transporte)
                transporte.assert_called_once()

    def test_falha_transporte_tem_mensagem_segura(self):
        with self.assertRaisesRegex(ligacoes.ErroLigacoes, "limite de solicitações"):
            self.avaliar(transporte=Mock(side_effect=ErroAPINarrativa("O provedor atingiu o limite de solicitações.")))
        segredo = "segredo que não pode aparecer na página"
        with self.assertRaises(ligacoes.ErroLigacoes) as capturado:
            self.avaliar(transporte=Mock(side_effect=OSError(segredo)))
        self.assertNotIn(segredo, str(capturado.exception))

    def test_contexto_excessivo_e_rejeitado_sem_truncar_citacao(self):
        self.candidato["texto"] = "Uma fonte extensa. " * 10000
        self.candidato["fragmentos"] = [{"id": "F1", "inicio": 0, "fim": len(self.candidato["texto"])}]
        self.candidato["fragmentos_ids"] = ["F1"]
        transporte = Mock()
        with self.assertRaisesRegex(ligacoes.ErroLigacoes, "um único bloco excede"):
            self.avaliar(transporte=transporte)
        transporte.assert_not_called()

    def test_contexto_excessivo_remove_so_final_ranking_sem_truncar_e_rejeita_fonte_nao_enviada(self):
        removido = copy.deepcopy(self.candidato)
        removido.update(bloco_id="B2", texto="Uma fonte extensa. " * 10000,
                        fragmentos_ids=["F3"])
        removido["fragmentos"] = [{"id": "F3", "inicio": 0, "fim": len(removido["texto"])}]
        self.recuperacao["candidatos"].append(removido)
        antes = copy.deepcopy(self.recuperacao)
        proposta_removida = {**self.item, "bloco_id": "B2", "fragmentos_ids": ["F3"],
                            "freud": {"inicio": 0, "fim": len("Uma fonte extensa."), "texto": "Uma fonte extensa."}}
        transporte = Mock(return_value=json.dumps({"ligacoes": [self.item, proposta_removida]}))
        resultado = self.avaliar(transporte=transporte)
        self.assertEqual(len(resultado["ligacoes"]), 1)
        self.assertEqual(len(resultado["rejeitadas"]), 1)
        self.assertIn("fontes enviadas", resultado["rejeitadas"][0]["motivo"])
        self.assertEqual(resultado["avaliacao"]["blocos_avaliados_ids"], ["B1"])
        self.assertEqual(resultado["avaliacao"]["blocos_nao_avaliados_ids"], ["B2"])
        self.assertTrue(resultado["avaliacao"]["motivo_limite_contexto"])
        self.assertLessEqual(resultado["avaliacao"]["bytes_pedido"], ligacoes.LIMITE_PEDIDO_BYTES)
        self.assertIn("1 de 2 candidatos", resultado["mensagem"])
        enviada = transporte.call_args.kwargs["contexto"]["recuperacao"]
        self.assertEqual(enviada["candidatos"], [self.candidato])
        self.assertEqual(enviada["candidatos"][0]["texto"], antes["candidatos"][0]["texto"])
        self.assertEqual(self.recuperacao, antes)

    def test_configuracao_e_contexto_internos_invalidos_tem_erros_seguros(self):
        for provedor in ("outro", [], None):
            if provedor is None:
                continue
            with self.subTest(provedor=provedor), self.assertRaises(ligacoes.ErroLigacoes):
                self.avaliar(provedor=provedor)
        for modelo in ("", 123, "a" * 201):
            with self.subTest(modelo=modelo), self.assertRaises(ligacoes.ErroLigacoes):
                self.avaliar(modelo=modelo)
        self.relato["paragrafos"][0]["texto"] = "Texto que não existe no original."
        with self.assertRaisesRegex(ligacoes.ErroLigacoes, "posições dos parágrafos"):
            self.avaliar()


if __name__ == "__main__":
    unittest.main()
