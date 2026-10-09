import copy
import json
import unittest

from narrativas import (
    COMPOSICAO_OPCOES,
    ErroNarrativa,
    aplicar_geracao,
    aprovar,
    composicao_padrao,
    conferir_composicao,
    herdar_composicao,
    migrar_estado,
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

    def test_contagens_palavras_por_periodo_sao_apenas_informativas(self):
        for quantidade in (1, 19, 20, 36, 37, 300):
            with self.subTest(palavras=quantidade):
                resultado = validar_movimento(com_palavras(quantidade))
                self.assertTrue(resultado["valido"])
                self.assertEqual(resultado["erros"], [])
                self.assertEqual(resultado["palavras_por_periodo"], [quantidade] * 5)
                self.assertEqual(resultado["total_palavras"], quantidade * 5)

    def test_limites_quantidade_periodos(self):
        for quantidade in (4, 5, 6):
            with self.subTest(periodos=quantidade):
                resultado = validar_movimento(com_palavras(20, quantidade))
                self.assertEqual(resultado["quantidade_periodos"], quantidade)
                self.assertEqual(resultado["valido"], quantidade == 5)

    def test_paragrafo_acima_180_palavras_e_permitido(self):
        resultado = validar_movimento(com_palavras(100))
        self.assertEqual(resultado["total_palavras"], 500)
        self.assertEqual(resultado["quantidade_periodos"], 5)
        self.assertTrue(resultado["valido"])
        self.assertEqual(resultado["erros"], [])

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
    def estado_antigo(self, estado=None):
        antigo = copy.deepcopy(novo_estado() if estado is None else estado)
        antigo["schema_version"] = "1.0.0"
        antigo["ideia_inicial"] = "  Uma irmã retorna à antiga casa da família.\n"
        antigo["planejamento"] = {
            "instrucoes": "Preserve as perguntas em aberto.",
            "texto_gerado": "Sinopse originalmente gerada: reencontro; conflito...",
            "texto_atual": " \nSinopse editada: reencontro; conflito...\n\nQuestão aberta.  ",
            "aprovado": False,
            "modelo_utilizado": "modelo-do-planejamento",
            "historico": [{"indice": 0, "operacao": "edicao_manual", "texto_anterior": "Outra sinopse."}],
        }
        antigo["movimentos"][0]["label"] = "Contextualização"
        for movimento in antigo["movimentos"]:
            movimento.pop("composicao", None)
        antigo["historico_alteracoes"].append({"indice": 0, "operacao": "edicao_manual", "texto_anterior": "Outra sinopse."})
        return antigo

    def cinco_aprovados(self):
        estado = novo_estado()
        for indice in range(1, 6):
            texto = PARAGRAFO.replace("medo", f"lembrança{indice}")
            estado = aplicar_geracao(estado, indice, texto, "modelo-teste")
            estado = aprovar(estado, indice)
        return estado

    def test_estado_padrao_serializavel_e_independente(self):
        estado = novo_estado()
        self.assertEqual(estado["schema_version"], "3.0.0")
        self.assertEqual(estado["intensidade"], 3)
        self.assertEqual(estado["provedor"], "openrouter")
        self.assertEqual(estado["modelo"], "openai/gpt-4.1-mini")
        self.assertEqual([item["id"] for item in estado["movimentos"]], [1, 2, 3, 4, 5])
        self.assertEqual(estado["movimentos"][0]["label"], "Introdução")
        self.assertNotIn("ideia_inicial", estado)
        self.assertNotIn("planejamento", estado)
        self.assertNotIn("legado", estado)
        self.assertEqual(json.loads(json.dumps(estado, ensure_ascii=False)), estado)
        estado["movimentos"][0]["historico"].append({"manual": True})
        self.assertEqual(estado["movimentos"][1]["historico"], [])
        self.assertEqual(novo_estado()["movimentos"][0]["historico"], [])

    def test_operacoes_nao_mutam_entrada(self):
        estado = novo_estado()
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

    def test_ideia_planejamento_e_legado_nao_sao_campos_editaveis(self):
        for campos in (
            {"ideia_inicial": "Uma nova ideia."},
            {"planejamento": {"texto_atual": "Um planejamento inteiramente revisado."}},
            {"legado": {"ideia_inicial": "Não sobrescrever o arquivo antigo."}},
        ):
            with self.subTest(campos=campos):
                estado = self.cinco_aprovados()
                snapshot = copy.deepcopy(estado)
                with self.assertRaises(ErroNarrativa):
                    salvar_campos(estado, campos)
                self.assertEqual(estado, snapshot)

    def test_configuracoes_e_instrucoes_nao_revogam_aprovacoes(self):
        estado = montar_narrativa(self.cinco_aprovados())
        atualizado = salvar_campos(estado, {
            "intensidade": 5, "provedor": "openai", "modelo": "gpt-4.1-mini",
            "movimentos": [{"id": 1, "instrucoes": "Outra direção para uma futura versão."}],
        })
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

    def test_geracao_comeca_na_introducao_e_exige_anteriores_aprovados(self):
        estado = novo_estado()
        self.assertEqual(preparar_geracao(estado, 1)["indice"], 1)
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
        self.assertEqual(set(contexto), {"indice", "anteriores", "instrucoes", "composicao", "intensidade", "texto_atual", "erros"})
        self.assertEqual([item["id"] for item in contexto["anteriores"]], [1, 2, 3, 4])
        self.assertEqual(contexto["anteriores"][0]["texto"], anterior)
        self.assertEqual([item["texto"] for item in contexto["anteriores"]], [item["texto_atual"] for item in estado["movimentos"][:4]])
        self.assertIsNone(contexto["erros"])

    def test_introducao_pode_ser_gerada_sem_semente_ou_instrucoes(self):
        estado = novo_estado()
        contexto = preparar_geracao(estado, 1)
        self.assertEqual(contexto, {
            "indice": 1, "anteriores": [], "instrucoes": "", "intensidade": 3,
            "composicao": composicao_padrao(), "texto_atual": "", "erros": None,
        })
        gerado = aplicar_geracao(estado, 1, PARAGRAFO, "modelo-teste")
        self.assertEqual(gerado["movimentos"][0]["texto_atual"], PARAGRAFO)
        self.assertFalse(gerado["movimentos"][0]["aprovado"])
        self.assertTrue(gerado["movimentos"][0]["validacao"]["valido"])
        aprovado = aprovar(gerado, 1)
        self.assertTrue(aprovado["movimentos"][0]["aprovado"])
        self.assertEqual(preparar_geracao(aprovado, 2)["anteriores"][0]["texto"], PARAGRAFO)

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
        estado = aplicar_geracao(novo_estado(), 1, "Um texto curto.", "modelo-teste")
        self.assertFalse(estado["movimentos"][0]["validacao"]["valido"])
        self.assertEqual(estado["movimentos"][0]["texto_atual"], "Um texto curto.")
        with self.assertRaises(ErroNarrativa):
            aprovar(estado, 1)
        contexto = preparar_geracao(estado, 1, corrigir=True)
        self.assertEqual(contexto["texto_atual"], "Um texto curto.")
        self.assertTrue(contexto["erros"])
        self.assertTrue(all(isinstance(erro, str) for erro in contexto["erros"]))

    def test_validar_nao_aprova_e_preserva_texto(self):
        estado = salvar_campos(novo_estado(), {"movimentos": [{"id": 1, "texto_atual": PARAGRAFO}]})
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

    def test_montagem_preserva_textos_exatos(self):
        estado = novo_estado()
        for indice in range(1, 6):
            texto = " \n" + PARAGRAFO.replace("medo", f"lembrança{indice}") + " \n"
            estado = salvar_campos(estado, {"movimentos": [{"id": indice, "texto_atual": texto}]})
            estado = aprovar(estado, indice)
        esperado = "\n\n".join(item["texto_atual"] for item in estado["movimentos"])
        resultado = montar_narrativa(estado)
        self.assertEqual(resultado["narrativa_final"], esperado)
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
        ):
            with self.subTest(modificar=modificar):
                estado = self.cinco_aprovados()
                modificar(estado)
                with self.assertRaises(ErroNarrativa):
                    montar_narrativa(estado)

    def test_migracao_preserva_textos_aprovacoes_configuracoes_e_historicos(self):
        completo = montar_narrativa(self.cinco_aprovados())
        completo = salvar_campos(completo, {"intensidade": 5, "modelo": "outro-modelo"})
        antigo = self.estado_antigo(completo)
        antigo["movimentos"][1]["historico"].append({
            "operacao": "edicao_manual", "texto_anterior": "  Um texto de outra versão.\r\n",
        })
        snapshot = copy.deepcopy(antigo)
        migrado = migrar_estado(antigo)
        self.assertEqual(antigo, snapshot)
        self.assertEqual(migrado["schema_version"], "3.0.0")
        self.assertNotIn("ideia_inicial", migrado)
        self.assertNotIn("planejamento", migrado)
        self.assertEqual(migrado["movimentos"][0]["label"], "Introdução")
        for anterior, atual in zip(antigo["movimentos"], migrado["movimentos"]):
            esperado = copy.deepcopy(anterior)
            esperado["composicao"] = composicao_padrao()
            if esperado["id"] == 1:
                esperado["label"] = "Introdução"
            self.assertEqual(atual, esperado)
        for nome in ("intensidade", "provedor", "modelo", "narrativa_final", "historico_alteracoes"):
            self.assertEqual(migrado[nome], antigo[nome])
        self.assertEqual(migrado["legado"], {
            "schema_version": "1.0.0",
            "ideia_inicial": antigo["ideia_inicial"],
            "planejamento": antigo["planejamento"],
        })
        exportado = json.loads(json.dumps(migrado, ensure_ascii=False))
        self.assertEqual(exportado, migrado)
        self.assertEqual(exportado["narrativa_final"], antigo["narrativa_final"])
        self.assertNotIn(antigo["planejamento"]["texto_atual"], exportado["narrativa_final"])

    def test_migracao_e_idempotente_e_nao_compartilha_dados_mutaveis(self):
        antigo = self.estado_antigo(self.cinco_aprovados())
        migrado = migrar_estado(antigo)
        remigrado = migrar_estado(migrado)
        self.assertEqual(remigrado, migrado)
        remigrado["legado"]["planejamento"]["historico"].append({"nova": True})
        remigrado["movimentos"][0]["historico"].append({"nova": True})
        self.assertNotEqual(remigrado["legado"], migrado["legado"])
        self.assertNotEqual(remigrado["movimentos"][0]["historico"], migrado["movimentos"][0]["historico"])
        self.assertEqual(migrado["legado"]["planejamento"], antigo["planejamento"])
        self.assertNotIn("legado", antigo)
        self.assertEqual(migrar_estado(novo_estado()), novo_estado())

    def test_rascunho_antigo_sem_planejamento_aprovado_inicia_introducao(self):
        antigo = self.estado_antigo()
        antigo["ideia_inicial"] = ""
        antigo["planejamento"]["texto_atual"] = ""
        antigo["planejamento"]["aprovado"] = False
        contexto = preparar_geracao(antigo, 1)
        self.assertEqual(contexto, preparar_geracao(novo_estado(), 1))
        self.assertNotIn("ideia_inicial", contexto)
        self.assertNotIn("planejamento", contexto)
        self.assertNotIn("legado", contexto)
        gerado = aplicar_geracao(antigo, 1, PARAGRAFO, "modelo-teste")
        self.assertTrue(aprovar(gerado, 1)["movimentos"][0]["aprovado"])
        self.assertEqual(gerado["legado"]["planejamento"], antigo["planejamento"])

    def test_todas_operacoes_de_estado_aceitam_rascunhos_antigos(self):
        completo = montar_narrativa(self.cinco_aprovados())
        antigo = self.estado_antigo(completo)
        snapshot = copy.deepcopy(antigo)
        for operacao in (
            lambda: salvar_campos(antigo, {"modelo": "novo-modelo"}),
            lambda: aplicar_geracao(antigo, 1, PARAGRAFO, "novo-modelo"),
            lambda: validar(antigo, 1),
            lambda: aprovar(antigo, 1),
            lambda: montar_narrativa(antigo),
        ):
            with self.subTest(operacao=operacao):
                atualizado = operacao()
                self.assertEqual(atualizado["schema_version"], "3.0.0")
                self.assertEqual(atualizado["legado"]["planejamento"], antigo["planejamento"])
                self.assertEqual(atualizado["movimentos"][4]["texto_atual"], antigo["movimentos"][4]["texto_atual"])
                self.assertNotIn("ideia_inicial", atualizado)
                self.assertNotIn("planejamento", atualizado)
                self.assertEqual(antigo, snapshot)
        contexto = preparar_geracao(antigo, 5)
        self.assertEqual(len(contexto["anteriores"]), 4)
        self.assertNotIn("legado", contexto)
        self.assertEqual(montar_narrativa(antigo)["narrativa_final"], antigo["narrativa_final"])

    def test_rascunho_migrado_mantem_pendencia_coerencia_e_aprovacao(self):
        estado = self.cinco_aprovados()
        estado = salvar_campos(estado, {"movimentos": [{"id": 1, "texto_atual": PARAGRAFO}]})
        antigo = self.estado_antigo(estado)
        migrado = migrar_estado(antigo)
        self.assertFalse(migrado["movimentos"][0]["aprovado"])
        self.assertTrue(all(item["revisao_coerencia"] for item in migrado["movimentos"][1:]))
        with self.assertRaises(ErroNarrativa):
            preparar_geracao(antigo, 5)
        with self.assertRaises(ErroNarrativa):
            montar_narrativa(antigo)
        for indice in range(1, 6):
            migrado = aprovar(migrado, indice)
        self.assertTrue(montar_narrativa(migrado)["narrativa_final"])

    def test_migracao_recusa_estado_antigo_incompleto_e_versao_desconhecida(self):
        for estado in (None, [], {}, {"schema_version": "4.0.0"}):
            with self.subTest(estado=estado):
                with self.assertRaises(ErroNarrativa):
                    migrar_estado(estado)
        for remover in ("movimentos", "ideia_inicial", "planejamento"):
            with self.subTest(remover=remover):
                estado = self.estado_antigo()
                del estado[remover]
                with self.assertRaises(ErroNarrativa):
                    migrar_estado(estado)

    def test_migracao_schema2_preserva_legado_e_adiciona_controles_independentes(self):
        antigo = migrar_estado(self.estado_antigo(montar_narrativa(self.cinco_aprovados())))
        antigo["schema_version"] = "2.0.0"
        for movimento in antigo["movimentos"]:
            del movimento["composicao"]
        snapshot = copy.deepcopy(antigo)
        migrado = migrar_estado(antigo)
        self.assertEqual(antigo, snapshot)
        self.assertEqual(migrado["schema_version"], "3.0.0")
        for nome in ("legado", "narrativa_final", "historico_alteracoes", "modelo", "provedor", "intensidade"):
            self.assertEqual(migrado[nome], antigo[nome])
        for anterior, atual in zip(antigo["movimentos"], migrado["movimentos"]):
            self.assertEqual(atual, {**anterior, "composicao": composicao_padrao()})
        migrado["movimentos"][0]["composicao"]["ritmo"]["instrucoes"] = "Cadência singular."
        self.assertEqual(migrado["movimentos"][1]["composicao"], composicao_padrao())
        self.assertEqual(migrar_estado(migrado), migrado)

    def test_migracao_reavalia_validacoes_antigas_sem_alterar_aprovacoes_ou_final(self):
        for versao in ("1.0.0", "2.0.0"):
            with self.subTest(versao=versao):
                antigo = self.estado_antigo(self.cinco_aprovados())
                if versao == "2.0.0":
                    del antigo["ideia_inicial"]
                    del antigo["planejamento"]
                    antigo["schema_version"] = versao
                    antigo["legado"] = {"rascunho": "  Dados anteriores.\r\n"}
                for indice, movimento in enumerate(antigo["movimentos"]):
                    texto = "  " + com_palavras(1 if indice % 2 else 80) + "  "
                    movimento["texto_atual"] = texto
                    movimento["texto_gerado"] = "Versão gerada anterior distinta."
                    movimento["validacao"] = {
                        "valido": False,
                        "erros": ["Período com 1 palavra; permitido de 20 a 36.", "Máximo de 180 palavras."],
                        "total_palavras": 999,
                    }
                antigo["narrativa_final"] = "\n\n".join(item["texto_atual"] for item in antigo["movimentos"])
                snapshot = copy.deepcopy(antigo)
                migrado = migrar_estado(antigo)
                self.assertEqual(antigo, snapshot)
                self.assertEqual(migrado["narrativa_final"], antigo["narrativa_final"])
                self.assertEqual(migrado["historico_alteracoes"], antigo["historico_alteracoes"])
                for anterior, atual in zip(antigo["movimentos"], migrado["movimentos"]):
                    self.assertTrue(atual["validacao"]["valido"])
                    self.assertEqual(atual["validacao"], validar_movimento(atual["texto_atual"]))
                    for nome in ("texto_atual", "texto_gerado", "aprovado", "historico", "modelo_utilizado", "revisao_coerencia"):
                        self.assertEqual(atual[nome], anterior[nome])
                self.assertEqual(montar_narrativa(migrado)["narrativa_final"], antigo["narrativa_final"])

    def test_migracao_preserva_validacao_none_e_nao_aprova_implicitamente(self):
        antigo = self.estado_antigo()
        antigo["movimentos"][0]["texto_atual"] = com_palavras(1)
        migrado = migrar_estado(antigo)
        self.assertIsNone(migrado["movimentos"][0]["validacao"])
        self.assertFalse(migrado["movimentos"][0]["aprovado"])
        aprovado = aprovar(migrado, 1)
        self.assertTrue(aprovado["movimentos"][0]["aprovado"])
        self.assertTrue(aprovado["movimentos"][0]["validacao"]["valido"])

    def test_migracao_tolera_validacao_ausente_em_rascunho_schema2(self):
        antigo = novo_estado()
        antigo["schema_version"] = "2.0.0"
        for movimento in antigo["movimentos"]:
            del movimento["composicao"]
            del movimento["validacao"]
        migrado = migrar_estado(antigo)
        self.assertEqual(migrado["schema_version"], "3.0.0")
        self.assertFalse(migrado["movimentos"][0]["aprovado"])
        self.assertIsNone(migrado["movimentos"][0].get("validacao"))
        self.assertEqual(migrado["movimentos"][0]["composicao"], composicao_padrao())

    def test_movimentos_curto_e_longo_podem_aprovar_encadear_e_montar(self):
        estado = novo_estado()
        for indice, tamanho in enumerate((1, 10, 37, 100, 300), 1):
            texto = com_palavras(tamanho)
            estado = aplicar_geracao(estado, indice, texto, "modelo-teste")
            estado = aprovar(estado, indice)
        resultado = montar_narrativa(estado)
        self.assertEqual(resultado["narrativa_final"], "\n\n".join(item["texto_atual"] for item in estado["movimentos"]))
        self.assertTrue(all(item["aprovado"] for item in resultado["movimentos"]))
        self.assertEqual(resultado["movimentos"][-1]["validacao"]["total_palavras"], 1500)

    def test_catalogo_e_controles_padrao_sao_independentes(self):
        self.assertEqual(set(COMPOSICAO_OPCOES), {"encadeamento", "sintaxe", "ritmo"})
        self.assertEqual(set(COMPOSICAO_OPCOES["encadeamento"]), {"progressivo", "causal", "temporal", "retomada"})
        self.assertEqual(set(COMPOSICAO_OPCOES["sintaxe"]), {"afirmacao_negacao", "contraste", "paralelismo", "inversao", "subordinacao"})
        self.assertEqual(set(COMPOSICAO_OPCOES["ritmo"]), {"regular", "crescente", "decrescente", "alternado", "irregular"})
        primeiro = composicao_padrao()
        segundo = composicao_padrao()
        primeiro["ritmo"]["instrucoes"] = "  Cadência diferente.\n"
        primeiro["ritmo"]["ativo"] = True
        self.assertEqual(segundo["ritmo"], {"ativo": False, "tipo": "regular", "instrucoes": ""})
        self.assertFalse(primeiro["sintaxe"]["ativo"])
        estado = novo_estado()
        estado["movimentos"][0]["composicao"]["ritmo"]["ativo"] = True
        self.assertFalse(estado["movimentos"][1]["composicao"]["ritmo"]["ativo"])
        self.assertFalse(novo_estado()["movimentos"][0]["composicao"]["ritmo"]["ativo"])

    def test_conferir_composicao_aceita_tipos_catalogados_preserva_instrucoes_e_copia(self):
        for nome, opcoes in COMPOSICAO_OPCOES.items():
            for tipo in opcoes:
                with self.subTest(controle=nome, tipo=tipo):
                    composicao = composicao_padrao()
                    composicao[nome] = {"ativo": True, "tipo": tipo, "instrucoes": " \nUma escolha com ação e cadência.\r\n "}
                    validada = conferir_composicao(composicao)
                    self.assertEqual(validada, composicao)
                    validada[nome]["instrucoes"] = "Outra instrução."
                    self.assertNotEqual(validada[nome]["instrucoes"], composicao[nome]["instrucoes"])

    def test_conferir_composicao_recusa_formato_campos_e_valores_invalidos(self):
        invalidos = [None, [], {}, {**composicao_padrao(), "extra": {}}]
        sem_controle = composicao_padrao()
        del sem_controle["ritmo"]
        invalidos.append(sem_controle)
        for valor in (None, {}, {"ativo": False, "tipo": "regular"}, {"ativo": False, "tipo": "regular", "instrucoes": "", "extra": True}):
            invalidos.append({**composicao_padrao(), "ritmo": valor})
        for nome, valores in (
            ("ativo", (None, 0, 1, "false", [])),
            ("tipo", (None, 1, "desconhecido", "causal", [])),
            ("instrucoes", (None, True, 1, [], "\ud800")),
        ):
            for valor in valores:
                composicao = composicao_padrao()
                composicao["ritmo"][nome] = valor
                invalidos.append(composicao)
        for valor in invalidos:
            with self.subTest(composicao=repr(valor)):
                snapshot = copy.deepcopy(valor)
                with self.assertRaises(ErroNarrativa):
                    conferir_composicao(valor)
                self.assertEqual(valor, snapshot)

    def test_salvar_composicao_nao_altera_textos_aprovacao_ou_posteriores(self):
        estado = montar_narrativa(self.cinco_aprovados())
        composicao = composicao_padrao()
        composicao["sintaxe"] = {"ativo": True, "tipo": "contraste", "instrucoes": "  Preserve a ressalva.\n"}
        atualizado = salvar_campos(estado, {"movimentos": [{"id": 2, "composicao": composicao}]})
        self.assertEqual(atualizado["narrativa_final"], estado["narrativa_final"])
        self.assertEqual(atualizado["movimentos"][1]["composicao"], composicao)
        for anterior, atual in zip(estado["movimentos"], atualizado["movimentos"]):
            for nome in ("texto_atual", "texto_gerado", "aprovado", "validacao", "modelo_utilizado", "revisao_coerencia", "instrucoes"):
                self.assertEqual(atual[nome], anterior[nome])
            if anterior["id"] != 2:
                self.assertEqual(atual, anterior)
        evento = atualizado["movimentos"][1]["historico"][-1]
        self.assertEqual(evento["operacao"], "alteracao_composicao")
        self.assertEqual(evento["antes"], composicao_padrao())
        self.assertEqual(evento["depois"], composicao)
        composicao["sintaxe"]["instrucoes"] = "Modificação externa."
        self.assertNotEqual(atualizado["movimentos"][1]["composicao"], composicao)
        self.assertEqual(salvar_campos(atualizado, {"movimentos": [{"id": 2, "composicao": atualizado["movimentos"][1]["composicao"]}]}), atualizado)

    def test_desativar_controle_preserva_suas_instrucoes(self):
        composicao = composicao_padrao()
        composicao["ritmo"] = {"ativo": True, "tipo": "crescente", "instrucoes": "Aumente gradualmente as pausas."}
        estado = salvar_campos(novo_estado(), {"movimentos": [{"id": 1, "composicao": composicao}]})
        composicao["ritmo"]["ativo"] = False
        atualizado = salvar_campos(estado, {"movimentos": [{"id": 1, "composicao": composicao}]})
        self.assertFalse(atualizado["movimentos"][0]["composicao"]["ritmo"]["ativo"])
        self.assertEqual(atualizado["movimentos"][0]["composicao"]["ritmo"]["instrucoes"], "Aumente gradualmente as pausas.")

    def test_preparar_geracao_copia_apenas_composicao_do_alvo(self):
        estado = self.cinco_aprovados()
        alvo = composicao_padrao()
        alvo["encadeamento"] = {"ativo": True, "tipo": "retomada", "instrucoes": "Retome o caderno."}
        anterior = composicao_padrao()
        anterior["ritmo"] = {"ativo": True, "tipo": "decrescente", "instrucoes": "Configuração só do anterior."}
        estado = salvar_campos(estado, {"movimentos": [{"id": 4, "composicao": anterior}, {"id": 5, "composicao": alvo}]})
        contexto = preparar_geracao(estado, 5)
        self.assertEqual(contexto["composicao"], alvo)
        self.assertEqual(len(contexto["anteriores"]), 4)
        self.assertTrue(all(set(item) == {"id", "label", "texto"} for item in contexto["anteriores"]))
        contexto["composicao"]["encadeamento"]["tipo"] = "temporal"
        self.assertEqual(estado["movimentos"][4]["composicao"], alvo)

    def test_heranca_e_snapshot_e_preserva_demais_dados(self):
        estado = montar_narrativa(self.cinco_aprovados())
        composicao = composicao_padrao()
        composicao["encadeamento"] = {"ativo": True, "tipo": "causal", "instrucoes": "Ligue as ações."}
        composicao["sintaxe"] = {"ativo": False, "tipo": "inversao", "instrucoes": "Guardar para ativar depois."}
        composicao["ritmo"] = {"ativo": True, "tipo": "alternado", "instrucoes": "  Alterne a cadência.\r\n"}
        estado = salvar_campos(estado, {"movimentos": [{"id": 2, "composicao": composicao, "instrucoes": "Descrição anterior, não herdar."}]})
        snapshot = copy.deepcopy(estado)
        herdado = herdar_composicao(estado, 3)
        self.assertEqual(estado, snapshot)
        self.assertEqual(herdado["movimentos"][2]["composicao"], composicao)
        self.assertEqual(herdado["narrativa_final"], estado["narrativa_final"])
        for anterior, atual in zip(estado["movimentos"], herdado["movimentos"]):
            for nome in ("texto_atual", "texto_gerado", "aprovado", "validacao", "modelo_utilizado", "revisao_coerencia", "instrucoes"):
                self.assertEqual(atual[nome], anterior[nome])
            if anterior["id"] != 3:
                self.assertEqual(atual, anterior)
        evento = herdado["movimentos"][2]["historico"][-1]
        self.assertEqual(evento["operacao"], "heranca_composicao")
        self.assertEqual(evento["movimento_origem"], 2)
        self.assertEqual(evento["depois"], composicao)
        nova = composicao_padrao()
        nova["ritmo"]["tipo"] = "irregular"
        alterado = salvar_campos(herdado, {"movimentos": [{"id": 2, "composicao": nova}]})
        self.assertEqual(alterado["movimentos"][2]["composicao"], composicao)
        personalizado = salvar_campos(herdado, {"movimentos": [{"id": 3, "composicao": nova}]})
        self.assertEqual(personalizado["movimentos"][1]["composicao"], composicao)
        personalizado["movimentos"][2]["composicao"]["ritmo"]["ativo"] = False
        self.assertTrue(personalizado["movimentos"][1]["composicao"]["ritmo"]["ativo"])

    def test_heranca_nao_exige_aprovacao_e_recusa_introducao_ou_indice_invalido(self):
        herdado = herdar_composicao(novo_estado(), 2)
        self.assertEqual(herdado["movimentos"][1]["composicao"], composicao_padrao())
        self.assertFalse(herdado["movimentos"][1]["aprovado"])
        for indice in (1, 0, 6, None, True, "2"):
            with self.subTest(indice=indice):
                with self.assertRaises(ErroNarrativa):
                    herdar_composicao(novo_estado(), indice)

    def test_heranca_em_estado_antigo_migra_sem_apagar_textos(self):
        antigo = self.estado_antigo(self.cinco_aprovados())
        herdado = herdar_composicao(antigo, 4)
        self.assertEqual(herdado["schema_version"], "3.0.0")
        self.assertEqual(herdado["legado"]["planejamento"], antigo["planejamento"])
        self.assertEqual([item["texto_atual"] for item in herdado["movimentos"]], [item["texto_atual"] for item in antigo["movimentos"]])
        self.assertTrue(all(item["aprovado"] for item in herdado["movimentos"]))

    def test_recusa_campos_protegidos_indices_duplicados_e_configuracoes_invalidas(self):
        for campos in (
            {"narrativa_final": "injetado"},
            {"planejamento": {"aprovado": True}},
            {"movimentos": [{"id": 1, "texto_gerado": "injetado"}]},
            {"movimentos": [{"id": 1, "composicao": None}]},
            {"movimentos": [{"id": 1, "composicao": {"ritmo": {"ativo": True}}}]},
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
        for indice in (None, False, True, -1, 0, 6, "1", 1.0):
            with self.subTest(indice=indice):
                with self.assertRaises(ErroNarrativa):
                    preparar_geracao(novo_estado(), indice)
                with self.assertRaises(ErroNarrativa):
                    aprovar(novo_estado(), indice)
                with self.assertRaises(ErroNarrativa):
                    validar(novo_estado(), indice)
                with self.assertRaises(ErroNarrativa):
                    aplicar_geracao(novo_estado(), indice, PARAGRAFO, "modelo-teste")
        for estado in (None, {}, [], {"schema_version": "4.0.0"}):
            with self.subTest(estado=estado):
                with self.assertRaises(ErroNarrativa):
                    salvar_campos(estado, {})
        estado = novo_estado()
        del estado["movimentos"]
        with self.assertRaises(ErroNarrativa):
            salvar_campos(estado, {})
        with self.assertRaises(ErroNarrativa):
            aprovar(novo_estado(), 0)
        with self.assertRaises(ErroNarrativa):
            validar(novo_estado(), 0)
        with self.assertRaises(ErroNarrativa):
            preparar_geracao(novo_estado(), 1, corrigir=True)
        with self.assertRaises(ErroNarrativa):
            preparar_geracao(novo_estado(), 1, corrigir="true")
        for texto, modelo in ((" ", "modelo"), (PARAGRAFO, ""), (None, "modelo")):
            with self.assertRaises(ErroNarrativa):
                aplicar_geracao(novo_estado(), 1, texto, modelo)


if __name__ == "__main__":
    unittest.main()
