import copy
import json
import unittest

from narrativas import (
    ErroNarrativa,
    aplicar_geracao,
    aprovar,
    montar_narrativa,
    novo_estado,
    preparar_geracao,
    salvar_campos,
    validar,
    validar_movimento,
)


FRASE = (
    "Eu lembrei daquela manhã quando caminhava sozinho pela rua e ainda "
    "acreditava que poderia conversar com minha irmã sem medo."
)
PARAGRAFO = " ".join([FRASE] * 5)


def com_palavras(quantidade, quantidade_periodos=5):
    frase = " ".join(["Eu"] + ["lembrei"] * (quantidade - 1)) + "."
    return " ".join([frase] * quantidade_periodos)


class ValidacaoNarrativaTests(unittest.TestCase):
    def test_texto_valido_expoe_contagens_e_intervalos(self):
        resultado = validar_movimento(PARAGRAFO)
        self.assertTrue(resultado["valido"])
        self.assertEqual(resultado["quantidade_paragrafos"], 1)
        self.assertEqual(resultado["quantidade_periodos"], 5)
        self.assertEqual(resultado["palavras_por_periodo"], [20] * 5)
        self.assertEqual(resultado["total_palavras"], 100)
        self.assertEqual(resultado["erros"], [])
        self.assertEqual(resultado["pontuacao_proibida"], [])
        for periodo in resultado["periodos"]:
            self.assertEqual(PARAGRAFO[periodo["inicio"]:periodo["fim"]], periodo["texto"])
            self.assertTrue(periodo["terminado"])

    def test_limites_palavras_por_periodo(self):
        for quantidade, valido in ((19, False), (20, True), (36, True), (37, False)):
            with self.subTest(palavras=quantidade):
                resultado = validar_movimento(com_palavras(quantidade))
                self.assertEqual(resultado["valido"], valido)
                self.assertEqual(resultado["palavras_por_periodo"], [quantidade] * 5)
                self.assertEqual(resultado["total_palavras"], quantidade * 5)

    def test_limites_quantidade_periodos(self):
        for quantidade in (4, 5, 6):
            with self.subTest(periodos=quantidade):
                resultado = validar_movimento(com_palavras(20, quantidade))
                self.assertEqual(resultado["quantidade_periodos"], quantidade)
                self.assertEqual(resultado["valido"], quantidade == 5)

    def test_maximo_180_independe_de_contagem_por_periodo(self):
        resultado = validar_movimento(com_palavras(36, 6))
        self.assertEqual(resultado["total_palavras"], 216)
        self.assertTrue(any("máximo permitido de 180" in erro for erro in resultado["erros"]))

    def test_paragrafos_separados_por_linha_em_branco_crlf_e_unicode(self):
        for separador in ("\n\n", "\r\n\r\n", "\r\r", "\n \t\n", "\u2029"):
            with self.subTest(separador=repr(separador)):
                texto = PARAGRAFO.replace(". ", "." + separador, 1)
                resultado = validar_movimento(texto)
                self.assertEqual(resultado["quantidade_paragrafos"], 2)
                self.assertFalse(resultado["valido"])

    def test_quebra_visual_simples_e_espacos_externos_sao_preservados(self):
        texto = " \n" + PARAGRAFO.replace(". ", ".\n", 1) + " \n"
        resultado = validar_movimento(texto)
        self.assertTrue(resultado["valido"])
        self.assertEqual(resultado["quantidade_paragrafos"], 1)
        self.assertEqual(texto, " \n" + PARAGRAFO.replace(". ", ".\n", 1) + " \n")

    def test_pontuacao_proibida_expoe_tipo_trecho_e_posicao(self):
        for sinal, tipo in (
            (":", "dois_pontos"),
            (";", "ponto_e_virgula"),
            ("...", "reticencias"),
            ("…", "reticencias"),
            ("—", "travessao"),
            ("―", "travessao"),
        ):
            with self.subTest(sinal=sinal):
                texto = PARAGRAFO.replace("manhã", "manhã" + sinal, 1)
                resultado = validar_movimento(texto)
                self.assertFalse(resultado["valido"])
                ocorrencia = next(item for item in resultado["pontuacao_proibida"] if item["tipo"] == tipo)
                self.assertEqual(ocorrencia["trecho"], sinal)
                self.assertEqual(texto[ocorrencia["inicio"]:ocorrencia["fim"]], sinal)

    def test_abreviacoes_nao_inventam_periodos_e_sao_rejeitadas(self):
        for abreviacao in ("Dr.", "Dra.", "Sr.", "etc.", "p. ex.", "U.S.A.", "1.º"):
            with self.subTest(abreviacao=abreviacao):
                texto = PARAGRAFO.replace("irmã", abreviacao + " irmã", 1)
                resultado = validar_movimento(texto)
                self.assertEqual(resultado["quantidade_periodos"], 5)
                self.assertFalse(resultado["valido"])
                self.assertTrue(any(item["tipo"] == "abreviacao_com_ponto" for item in resultado["pontuacao_proibida"]))

    def test_abreviacao_sem_espaco_e_inicial_de_nome(self):
        for trecho in ("Dr.José", "J. Silva"):
            with self.subTest(trecho=trecho):
                resultado = validar_movimento(PARAGRAFO.replace("irmã", trecho, 1))
                self.assertEqual(resultado["quantidade_periodos"], 5)
                self.assertFalse(resultado["valido"])

    def test_vogais_minusculas_no_fim_nao_sao_iniciais_de_nome(self):
        texto = PARAGRAFO.replace("medo.", "e.", 1)
        self.assertEqual(validar_movimento(texto)["quantidade_periodos"], 5)
        self.assertEqual(validar_movimento(texto)["pontuacao_proibida"], [])

    def test_fragmento_final_sem_pontuacao_e_rejeitado(self):
        resultado = validar_movimento(PARAGRAFO[:-1])
        self.assertEqual(resultado["quantidade_periodos"], 5)
        self.assertFalse(resultado["valido"])
        self.assertFalse(resultado["periodos"][-1]["terminado"])
        self.assertTrue(any("falta pontuação" in erro for erro in resultado["erros"]))

    def test_perguntas_exclamacoes_e_sequencias_contam_um_terminador(self):
        texto = PARAGRAFO.replace("medo.", "medo?!", 1).replace("medo.", "medo!", 1)
        self.assertTrue(validar_movimento(texto)["valido"])
        self.assertEqual(validar_movimento(texto)["quantidade_periodos"], 5)

    def test_decimal_nao_encerra_periodo(self):
        texto = PARAGRAFO.replace("manhã", "2.5", 1)
        resultado = validar_movimento(texto)
        self.assertTrue(resultado["valido"])
        self.assertEqual(resultado["quantidade_periodos"], 5)

    def test_compostos_apostrofos_unicode_e_numeros(self):
        texto = PARAGRAFO.replace("manhã", "guarda-chuva", 1).replace("irmã", "d’água", 1).replace("medo", "2026", 1)
        resultado = validar_movimento(texto)
        self.assertTrue(resultado["valido"])
        self.assertEqual(resultado["total_palavras"], 100)

    def test_vazio_e_rejeitado_com_contagens_zero(self):
        for texto in ("", " \t\n ", "...", "!?", "—"):
            with self.subTest(texto=repr(texto)):
                resultado = validar_movimento(texto)
                self.assertFalse(resultado["valido"])
                self.assertEqual(resultado["quantidade_periodos"], 0)
                self.assertEqual(resultado["total_palavras"], 0)

    def test_tipo_invalido_e_unicode_invalido(self):
        for texto in (None, 1, False, [], {}, b"texto", "\ud800"):
            with self.subTest(texto=repr(texto)):
                with self.assertRaises(ErroNarrativa):
                    validar_movimento(texto)


class EstadoNarrativaTests(unittest.TestCase):
    def planejamento_aprovado(self):
        estado = salvar_campos(novo_estado(), {
            "ideia_inicial": "Uma irmã retorna à antiga casa da família.",
            "planejamento": {"texto_atual": "Eu reencontro minha irmã e nossa antiga casa. Ficam perguntas abertas."},
        })
        return aprovar(estado, 0)

    def cinco_aprovados(self):
        estado = self.planejamento_aprovado()
        for indice in range(1, 6):
            texto = PARAGRAFO.replace("medo", f"lembrança{indice}")
            estado = aplicar_geracao(estado, indice, texto, "modelo-teste")
            estado = aprovar(estado, indice)
        return estado

    def test_estado_padrao_serializavel_e_independente(self):
        estado = novo_estado()
        self.assertEqual(estado["intensidade"], 3)
        self.assertEqual(estado["provedor"], "openrouter")
        self.assertEqual(estado["modelo"], "openai/gpt-4.1-mini")
        self.assertEqual([item["id"] for item in estado["movimentos"]], [1, 2, 3, 4, 5])
        self.assertEqual(json.loads(json.dumps(estado, ensure_ascii=False)), estado)
        estado["movimentos"][0]["historico"].append({"manual": True})
        self.assertEqual(estado["movimentos"][1]["historico"], [])
        self.assertEqual(novo_estado()["movimentos"][0]["historico"], [])

    def test_operacoes_nao_mutam_entrada(self):
        estado = self.planejamento_aprovado()
        snapshot = copy.deepcopy(estado)
        editado = salvar_campos(estado, {"movimentos": [{"id": 1, "texto_atual": PARAGRAFO}]})
        self.assertEqual(estado, snapshot)
        snapshot_editado = copy.deepcopy(editado)
        validar(editado, 1)
        preparar_geracao(editado, 1, corrigir=True)
        aprovar(editado, 1)
        aplicar_geracao(editado, 1, PARAGRAFO, "modelo-teste")
        self.assertEqual(editado, snapshot_editado)
        completo = self.cinco_aprovados()
        snapshot_completo = copy.deepcopy(completo)
        montar_narrativa(completo)
        self.assertEqual(completo, snapshot_completo)

    def test_patch_parcial_preserva_todos_textos_nao_enviados(self):
        estado = self.cinco_aprovados()
        atualizado = salvar_campos(estado, {"movimentos": [{"id": 2, "instrucoes": "Explore o reencontro."}]})
        for indice in range(5):
            self.assertEqual(atualizado["movimentos"][indice]["texto_atual"], estado["movimentos"][indice]["texto_atual"])
            self.assertEqual(atualizado["movimentos"][indice]["texto_gerado"], estado["movimentos"][indice]["texto_gerado"])
            self.assertTrue(atualizado["movimentos"][indice]["aprovado"])
        self.assertEqual(atualizado["movimentos"][1]["instrucoes"], "Explore o reencontro.")

    def test_edicao_invalida_atual_e_posteriores_sem_apagar(self):
        estado = montar_narrativa(self.cinco_aprovados())
        texto = "  " + estado["movimentos"][1]["texto_atual"].replace("rua", "praça") + "  "
        atualizado = salvar_campos(estado, {"movimentos": [{"id": 2, "texto_atual": texto}]})
        self.assertTrue(atualizado["movimentos"][0]["aprovado"])
        self.assertFalse(atualizado["movimentos"][1]["aprovado"])
        self.assertEqual(atualizado["movimentos"][1]["texto_atual"], texto)
        self.assertEqual(atualizado["movimentos"][1]["texto_gerado"], estado["movimentos"][1]["texto_gerado"])
        for movimento in atualizado["movimentos"][2:]:
            self.assertTrue(movimento["revisao_coerencia"])
            self.assertFalse(movimento["aprovado"])
            self.assertEqual(movimento["texto_atual"], estado["movimentos"][movimento["id"] - 1]["texto_atual"])
        self.assertEqual(atualizado["narrativa_final"], "")
        self.assertEqual(atualizado["movimentos"][1]["historico"][-1]["operacao"], "edicao_manual")

    def test_edicao_identica_e_no_op(self):
        estado = montar_narrativa(self.cinco_aprovados())
        atualizado = salvar_campos(estado, {"movimentos": [{"id": 1, "texto_atual": estado["movimentos"][0]["texto_atual"]}]})
        self.assertEqual(atualizado, estado)

    def test_alteracao_planejamento_ou_ideia_exige_novas_aprovacoes(self):
        for campos in (
            {"ideia_inicial": "Uma nova ideia."},
            {"planejamento": {"texto_atual": "Um planejamento inteiramente revisado."}},
        ):
            with self.subTest(campos=campos):
                estado = self.cinco_aprovados()
                atualizado = salvar_campos(estado, campos)
                self.assertFalse(atualizado["planejamento"]["aprovado"])
                self.assertTrue(all(item["revisao_coerencia"] for item in atualizado["movimentos"]))
                self.assertTrue(all(not item["aprovado"] for item in atualizado["movimentos"]))
                self.assertEqual([item["texto_atual"] for item in atualizado["movimentos"]], [item["texto_atual"] for item in estado["movimentos"]])

    def test_configuracoes_e_instrucoes_nao_revogam_aprovacoes(self):
        estado = montar_narrativa(self.cinco_aprovados())
        atualizado = salvar_campos(estado, {
            "intensidade": 5, "provedor": "openai", "modelo": "gpt-4.1-mini",
            "planejamento": {"instrucoes": "Outra direção para uma futura versão."},
        })
        self.assertTrue(atualizado["planejamento"]["aprovado"])
        self.assertTrue(all(item["aprovado"] for item in atualizado["movimentos"]))
        self.assertEqual(atualizado["narrativa_final"], estado["narrativa_final"])
        self.assertEqual(atualizado["movimentos"][0]["modelo_utilizado"], "modelo-teste")

    def test_patch_fora_de_ordem_ainda_sinaliza_todos_posteriores(self):
        estado = self.cinco_aprovados()
        atualizado = salvar_campos(estado, {"movimentos": [
            {"id": 4, "texto_atual": PARAGRAFO},
            {"id": 1, "texto_atual": PARAGRAFO},
        ]})
        self.assertTrue(atualizado["movimentos"][3]["revisao_coerencia"])
        self.assertTrue(all(not item["aprovado"] for item in atualizado["movimentos"]))

    def test_geracao_precisa_planejamento_e_anteriores_aprovados(self):
        with self.assertRaises(ErroNarrativa):
            preparar_geracao(novo_estado(), 1)
        estado = self.planejamento_aprovado()
        with self.assertRaises(ErroNarrativa):
            preparar_geracao(estado, 2)
        estado = aplicar_geracao(estado, 1, PARAGRAFO, "modelo-teste")
        with self.assertRaises(ErroNarrativa):
            preparar_geracao(estado, 2)
        estado = aprovar(estado, 1)
        self.assertEqual(preparar_geracao(estado, 2)["indice"], 2)

    def test_contexto_inclui_todos_anteriores_atuais_e_nenhum_posterior(self):
        estado = self.cinco_aprovados()
        anterior = "  " + estado["movimentos"][0]["texto_atual"].replace("rua", "ponte") + "  "
        estado = salvar_campos(estado, {"movimentos": [{"id": 1, "texto_atual": anterior}]})
        for indice in range(1, 5):
            estado = aprovar(estado, indice)
        contexto = preparar_geracao(estado, 5)
        self.assertEqual(contexto["planejamento"], estado["planejamento"]["texto_atual"])
        self.assertEqual([item["id"] for item in contexto["anteriores"]], [1, 2, 3, 4])
        self.assertEqual(contexto["anteriores"][0]["texto"], anterior)
        self.assertEqual([item["texto"] for item in contexto["anteriores"]], [item["texto_atual"] for item in estado["movimentos"][:4]])
        self.assertIsNone(contexto["erros"])

    def test_planejamento_precisa_ideia_e_nao_e_validado_como_paragrafo(self):
        with self.assertRaises(ErroNarrativa):
            preparar_geracao(novo_estado(), 0)
        estado = salvar_campos(novo_estado(), {"ideia_inicial": "Ideia: reencontro; conflito..."})
        estado = aplicar_geracao(estado, 0, "Sinopse: reencontro; conflito...\n\nQuestão aberta.", "modelo-teste")
        self.assertFalse(estado["planejamento"]["aprovado"])
        self.assertTrue(aprovar(estado, 0)["planejamento"]["aprovado"])
        self.assertEqual(preparar_geracao(estado, 0)["anteriores"], [])

    def test_regeneracao_preserva_edicao_manual_no_historico_e_demais_campos(self):
        estado = self.cinco_aprovados()
        manual = PARAGRAFO.replace("rua", "praça")
        estado = salvar_campos(estado, {"movimentos": [{"id": 2, "texto_atual": manual}]})
        novo_texto = PARAGRAFO.replace("rua", "avenida")
        atualizado = aplicar_geracao(estado, 2, novo_texto, "novo-modelo")
        alvo = atualizado["movimentos"][1]
        self.assertEqual(alvo["texto_atual"], novo_texto)
        self.assertEqual(alvo["texto_gerado"], novo_texto)
        self.assertEqual(alvo["modelo_utilizado"], "novo-modelo")
        self.assertFalse(alvo["aprovado"])
        self.assertEqual(alvo["historico"][-1]["texto_anterior"], manual)
        self.assertEqual(alvo["historico"][-1]["texto_gerado_anterior"], estado["movimentos"][1]["texto_gerado"])
        self.assertEqual(alvo["historico"][-1]["operacao"], "regeneracao")
        self.assertEqual(atualizado["movimentos"][0], estado["movimentos"][0])
        for indice in range(2, 5):
            self.assertEqual(atualizado["movimentos"][indice]["texto_atual"], estado["movimentos"][indice]["texto_atual"])
            self.assertTrue(atualizado["movimentos"][indice]["revisao_coerencia"])

    def test_geracao_invalida_e_preservada_para_corrigir_sem_aprovacao(self):
        estado = aplicar_geracao(self.planejamento_aprovado(), 1, "Um texto curto.", "modelo-teste")
        self.assertFalse(estado["movimentos"][0]["validacao"]["valido"])
        self.assertEqual(estado["movimentos"][0]["texto_atual"], "Um texto curto.")
        with self.assertRaises(ErroNarrativa):
            aprovar(estado, 1)
        contexto = preparar_geracao(estado, 1, corrigir=True)
        self.assertEqual(contexto["texto_atual"], "Um texto curto.")
        self.assertTrue(contexto["erros"])
        self.assertTrue(all(isinstance(erro, str) for erro in contexto["erros"]))

    def test_validar_nao_aprova_e_preserva_texto(self):
        estado = salvar_campos(self.planejamento_aprovado(), {"movimentos": [{"id": 1, "texto_atual": PARAGRAFO}]})
        atualizado = validar(estado, 1)
        self.assertTrue(atualizado["movimentos"][0]["validacao"]["valido"])
        self.assertFalse(atualizado["movimentos"][0]["aprovado"])
        self.assertEqual(atualizado["movimentos"][0]["texto_atual"], PARAGRAFO)
        self.assertEqual(atualizado["movimentos"][0]["texto_gerado"], "")

    def test_revisao_requer_aprovacao_sequencial_explicita(self):
        estado = self.cinco_aprovados()
        estado = salvar_campos(estado, {"movimentos": [{"id": 1, "texto_atual": PARAGRAFO}]})
        with self.assertRaises(ErroNarrativa):
            aprovar(estado, 3)
        estado = aprovar(estado, 1)
        self.assertTrue(estado["movimentos"][1]["revisao_coerencia"])
        self.assertFalse(estado["movimentos"][1]["aprovado"])
        estado = aprovar(estado, 2)
        self.assertFalse(estado["movimentos"][1]["revisao_coerencia"])
        self.assertTrue(estado["movimentos"][1]["aprovado"])
        self.assertTrue(estado["movimentos"][2]["revisao_coerencia"])

    def test_montagem_preserva_textos_exatos_e_exclui_planejamento(self):
        estado = self.planejamento_aprovado()
        for indice in range(1, 6):
            texto = " \n" + PARAGRAFO.replace("medo", f"lembrança{indice}") + " \n"
            estado = salvar_campos(estado, {"movimentos": [{"id": indice, "texto_atual": texto}]})
            estado = aprovar(estado, indice)
        esperado = "\n\n".join(item["texto_atual"] for item in estado["movimentos"])
        resultado = montar_narrativa(estado)
        self.assertEqual(resultado["narrativa_final"], esperado)
        self.assertNotIn(estado["planejamento"]["texto_atual"], resultado["narrativa_final"])
        self.assertTrue(resultado["narrativa_final"].startswith(" \n"))
        self.assertTrue(resultado["narrativa_final"].endswith(" \n"))
        exportado = json.loads(json.dumps(resultado, ensure_ascii=False))
        self.assertEqual(exportado, resultado)
        self.assertEqual(exportado["movimentos"][0]["texto_gerado"], "")

    def test_montagem_recusa_aprovacoes_pendentes_revisao_e_validacao_adulterada(self):
        for modificar in (
            lambda estado: estado["movimentos"][3].update(aprovado=False),
            lambda estado: estado["movimentos"][3].update(revisao_coerencia=True),
            lambda estado: estado["movimentos"][3].update(texto_atual="Texto inválido."),
            lambda estado: estado["planejamento"].update(aprovado=False),
        ):
            with self.subTest(modificar=modificar):
                estado = self.cinco_aprovados()
                modificar(estado)
                with self.assertRaises(ErroNarrativa):
                    montar_narrativa(estado)

    def test_recusa_campos_protegidos_indices_duplicados_e_configuracoes_invalidas(self):
        for campos in (
            {"narrativa_final": "injetado"},
            {"planejamento": {"aprovado": True}},
            {"movimentos": [{"id": 1, "texto_gerado": "injetado"}]},
            {"movimentos": [{"id": 1}, {"id": 1}]},
            {"movimentos": [{"id": 0}]},
            {"movimentos": [{"id": True}]},
            {"movimentos": {}},
            {"planejamento": None},
            {"intensidade": True},
            {"intensidade": 0},
            {"intensidade": 6},
            {"provedor": "desconhecido"},
            {"modelo": "  "},
            {"ideia_inicial": None},
        ):
            with self.subTest(campos=campos):
                estado = novo_estado()
                snapshot = copy.deepcopy(estado)
                with self.assertRaises(ErroNarrativa):
                    salvar_campos(estado, campos)
                self.assertEqual(estado, snapshot)

    def test_recusa_indices_invalidos_estados_incompletos_e_operacoes_vazias(self):
        for indice in (None, False, True, -1, 6, "1", 1.0):
            with self.subTest(indice=indice):
                with self.assertRaises(ErroNarrativa):
                    preparar_geracao(novo_estado(), indice)
                with self.assertRaises(ErroNarrativa):
                    aprovar(novo_estado(), indice)
        for estado in (None, {}, [], {"schema_version": "2.0.0"}):
            with self.subTest(estado=estado):
                with self.assertRaises(ErroNarrativa):
                    salvar_campos(estado, {})
        estado = novo_estado()
        del estado["planejamento"]
        with self.assertRaises(ErroNarrativa):
            salvar_campos(estado, {})
        with self.assertRaises(ErroNarrativa):
            aprovar(novo_estado(), 0)
        with self.assertRaises(ErroNarrativa):
            validar(novo_estado(), 0)
        with self.assertRaises(ErroNarrativa):
            preparar_geracao(self.planejamento_aprovado(), 1, corrigir=True)
        with self.assertRaises(ErroNarrativa):
            preparar_geracao(self.planejamento_aprovado(), 1, corrigir="true")
        for texto, modelo in ((" ", "modelo"), (PARAGRAFO, ""), (None, "modelo")):
            with self.assertRaises(ErroNarrativa):
                aplicar_geracao(self.planejamento_aprovado(), 1, texto, modelo)


if __name__ == "__main__":
    unittest.main()
