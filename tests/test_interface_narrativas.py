from pathlib import Path
from html.parser import HTMLParser
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from api_narrativas import ErroAPINarrativa
from app import create_app


def paragrafo(rotulo="memória"):
    periodo = " ".join(["Eu", rotulo] + ["recordo"] * 18) + "."
    return " ".join([periodo] * 5)


class TextareaValues(HTMLParser):
    """Leia o valor inicial segundo a regra HTML de remoção do primeiro LF."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values = {}
        self.elements = []
        self.current = None
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))
        if tag == "textarea":
            self.current = dict(attrs).get("id")
            self.chunks = []

    def handle_data(self, data):
        if self.current is not None:
            self.chunks.append(data)

    def handle_endtag(self, tag):
        if tag == "textarea" and self.current is not None:
            value = "".join(self.chunks).replace("\r\n", "\n").replace("\r", "\n")
            self.values[self.current] = value[1:] if value.startswith("\n") else value
            self.current = None


class NarrativeInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "textos.sqlite3"
        self.calls = []

        def generator(**context):
            self.calls.append(context)
            return paragrafo("gerada")

        self.config = {"TESTING": True, "DATABASE": str(self.database), "NARRATIVE_GENERATOR": generator}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        response = self.client.post("/narrativas")
        self.assertEqual(response.status_code, 303)
        self.url = response.headers["Location"].split("?", 1)[0]
        self.identifier = self.url.rsplit("/", 1)[1]

    def row(self):
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM narrative_drafts WHERE id = ?", (self.identifier,)).fetchone()
            return dict(row)

    def state(self):
        return json.loads(self.row()["state_json"])

    def fields(self):
        row = self.row()
        state = json.loads(row["state_json"])
        form = {
            "csrf_token": row["csrf_token"], "revision": str(row["revision"]),
            "intensidade": str(state["intensidade"]), "aba": "1",
            "provedor": state["provedor"], "modelo": state["modelo"],
        }
        for movement in state["movimentos"]:
            index = movement["id"]
            form[f"instrucoes_{index}"] = movement["instrucoes"]
            form[f"texto_{index}"] = movement["texto_atual"]
        return form

    def action(self, action, **edits):
        fields = self.fields()
        if ":" in action:
            fields["aba"] = action.split(":", 1)[1]
        fields.update(edits)
        fields["acao"] = action
        return self.client.post(self.url, data=fields)

    def approve_all(self):
        paragraphs = ["\n " + paragrafo(f"memória{index}") + "  " for index in range(1, 6)]
        self.assertEqual(self.action("salvar", **{f"texto_{i}": text for i, text in enumerate(paragraphs, 1)}).status_code, 200)
        for index in range(1, 6):
            self.assertEqual(self.action(f"aprovar:{index}").status_code, 200)
        return paragraphs

    def markup(self, response):
        parser = TextareaValues()
        parser.feed(response.get_data(as_text=True))
        return parser

    def assert_five_tabs(self, response, active=1):
        self.assertEqual(response.status_code, 200)
        parser = self.markup(response)
        tabs = [attrs for _, attrs in parser.elements if attrs.get("role") == "tab"]
        panels = [attrs for _, attrs in parser.elements if attrs.get("role") == "tabpanel"]
        self.assertEqual(len(tabs), 5)
        self.assertEqual(len(panels), 5)
        for index, (tab, panel) in enumerate(zip(tabs, panels), 1):
            self.assertEqual(tab["aria-controls"], panel["id"])
            self.assertEqual(panel["aria-labelledby"], tab["id"])
            self.assertEqual(tab.get("aria-selected"), "true" if index == active else "false")
            self.assertEqual(tab.get("tabindex"), "0" if index == active else "-1")
            self.assertEqual("hidden" in panel, index != active)
        return parser

    def test_exactly_five_accessible_tabs_without_idea_or_planning_fields(self):
        for index in range(1, 6):
            response = self.client.get(self.url + f"?aba={index}")
            parser = self.assert_five_tabs(response, active=index)
            names = {attrs.get("name") for _, attrs in parser.elements}
            self.assertNotIn("ideia_inicial", names)
            self.assertNotIn("texto_0", names)
            self.assertNotIn("instrucoes_0", names)
            self.assertTrue({f"texto_{i}" for i in range(1, 6)} <= names)
            self.assertTrue({f"instrucoes_{i}" for i in range(1, 6)} <= names)
        self.assertNotIn("ideia_inicial", self.state())
        self.assertNotIn("planejamento", self.state())

    def test_first_movement_generates_directly_from_its_instructions(self):
        response = self.action("gerar:1", instrucoes_1="Eu retorno à casa da minha irmã para acertar nossa herança.")
        self.assert_five_tabs(response, active=1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["indice"], 1)
        self.assertEqual(self.calls[0]["anteriores"], [])
        self.assertEqual(self.calls[0]["instrucoes"], "Eu retorno à casa da minha irmã para acertar nossa herança.")
        self.assertNotIn("planejamento", self.calls[0])
        self.assertNotIn("ideia_inicial", self.calls[0])
        self.assertEqual(self.state()["movimentos"][0]["texto_atual"], paragrafo("gerada"))

    def test_tab_selection_keeps_all_other_edited_fields_and_returns_selected_tab(self):
        response = self.action("salvar", aba="4", texto_1="Rascunho na primeira aba.", texto_4="Rascunho da contradição.", instrucoes_5="Deixe uma pergunta aberta.")
        self.assert_five_tabs(response, active=4)
        response = self.client.get(self.url + "?aba=2")
        parser = self.assert_five_tabs(response, active=2)
        self.assertEqual(parser.values["texto_1"], "Rascunho na primeira aba.")
        self.assertEqual(parser.values["texto_4"], "Rascunho da contradição.")
        self.assertEqual(parser.values["instrucoes_5"], "Deixe uma pergunta aberta.")
        self.assertEqual(self.calls, [])

    def test_cannot_generate_later_movement_until_previous_is_approved(self):
        response = self.action("gerar:2", texto_1=paragrafo(), instrucoes_2="Revele o conflito.")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.state()["movimentos"][0]["texto_atual"], paragrafo())
        self.assertEqual(self.state()["movimentos"][1]["instrucoes"], "Revele o conflito.")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.action("aprovar:1").status_code, 200)
        self.assertEqual(self.action("gerar:2").status_code, 200)

    def test_navigation_and_draft_persist_after_restart(self):
        self.assertIn("Gerar narrativa", self.client.get("/").get_data(as_text=True))
        content = "\n  Meu parágrafo atual.\n" + "</textarea><script>alert('oi')</script>"
        self.assertEqual(self.action("salvar", texto_1=content, instrucoes_3="Uma lembrança antiga.").status_code, 200)
        restarted = create_app(self.config).test_client()
        response = restarted.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<script>alert", response.get_data(as_text=True))
        self.assertIn("&lt;script&gt;", response.get_data(as_text=True))
        self.assertEqual(self.state()["movimentos"][0]["texto_atual"], content)
        self.assertEqual(self.state()["movimentos"][2]["instrucoes"], "Uma lembrança antiga.")
        self.assertEqual(self.calls, [])
        parser = TextareaValues()
        parser.feed(response.get_data(as_text=True))
        self.assertEqual(parser.values["texto_1"], content)

    def test_generation_updates_only_target_and_uses_all_current_previous_texts(self):
        first = paragrafo("anterior")
        second = paragrafo("atual")
        self.assertEqual(self.action("aprovar:1", texto_1=first).status_code, 200)
        self.assertEqual(self.action("aprovar:2", texto_2=second).status_code, 200)
        untouched = "Último movimento ainda em elaboração manual."
        response = self.action("gerar:3", texto_5=untouched, instrucoes_3="Recorde a infância.", intensidade="5")
        self.assertEqual(response.status_code, 200)
        state = self.state()
        self.assertEqual(state["movimentos"][0]["texto_atual"], first)
        self.assertEqual(state["movimentos"][1]["texto_atual"], second)
        self.assertTrue(state["movimentos"][0]["aprovado"])
        self.assertTrue(state["movimentos"][1]["aprovado"])
        self.assertEqual(state["movimentos"][2]["texto_atual"], paragrafo("gerada"))
        self.assertFalse(state["movimentos"][2]["aprovado"])
        self.assertEqual(state["movimentos"][4]["texto_atual"], untouched)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["indice"], 3)
        self.assertEqual([item["texto"] for item in self.calls[0]["anteriores"]], [first, second])
        self.assertNotIn("planejamento", self.calls[0])
        self.assertNotIn("ideia_inicial", self.calls[0])
        self.assertEqual(self.calls[0]["intensidade"], 5)
        self.assertEqual(self.calls[0]["instrucoes"], "Recorde a infância.")

    def test_regeneration_requires_explicit_confirmation_and_preserves_manual_edits(self):
        edited = paragrafo("editada")
        response = self.action("gerar:1", texto_1=edited)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.state()["movimentos"][0]["texto_atual"], edited)
        self.assertEqual(self.calls, [])
        response = self.action("gerar:1", substituir_1="on")
        self.assertEqual(response.status_code, 200)
        movement = self.state()["movimentos"][0]
        self.assertEqual(movement["texto_atual"], paragrafo("gerada"))
        self.assertEqual(movement["texto_gerado"], paragrafo("gerada"))
        self.assertEqual(len(self.calls), 1)

    def test_api_failure_retains_all_edits_and_releases_generation_lock(self):

        def failing(**_context):
            raise ErroAPINarrativa("O provedor atingiu o limite de solicitações.")

        self.app.config["NARRATIVE_GENERATOR"] = failing
        response = self.action("gerar:1", instrucoes_1="Apresente minha irmã.", texto_4="Esboço manual posterior.")
        self.assertEqual(response.status_code, 502)
        state = self.state()
        self.assertEqual(state["movimentos"][0]["instrucoes"], "Apresente minha irmã.")
        self.assertEqual(state["movimentos"][3]["texto_atual"], "Esboço manual posterior.")
        self.assertEqual(state["movimentos"][0]["texto_atual"], "")
        self.assertIsNone(self.row()["busy_since"])
        self.assertEqual(self.action("salvar").status_code, 200)

    def test_missing_api_key_does_not_erase_edits(self):
        self.app.config["NARRATIVE_GENERATOR"] = None
        with patch.dict("os.environ", {"NARRATIVA_API_KEY": ""}):
            response = self.action("gerar:1", instrucoes_1="Minha primeira noite.")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.state()["movimentos"][0]["instrucoes"], "Minha primeira noite.")
        self.assertIsNone(self.row()["busy_since"])

    def test_stale_revision_and_invalid_csrf_cannot_mutate_or_generate(self):
        stale = self.fields()
        self.assertEqual(self.action("salvar", instrucoes_1="As instruções salvas na outra aba.").status_code, 200)
        stale["acao"] = "gerar:1"
        response = self.client.post(self.url, data=stale)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.state()["movimentos"][0]["instrucoes"], "As instruções salvas na outra aba.")
        fields = self.fields()
        fields.update({"acao": "gerar:1", "csrf_token": "inválido"})
        self.assertEqual(self.client.post(self.url, data=fields).status_code, 400)
        self.assertEqual(self.calls, [])

    def test_editing_previous_paragraph_preserves_later_texts_and_requires_review(self):
        originals = self.approve_all()
        self.assertEqual(self.action("montar").status_code, 200)
        changed = paragrafo("mudança")
        self.assertEqual(self.action("salvar", texto_2=changed).status_code, 200)
        state = self.state()
        self.assertTrue(state["movimentos"][0]["aprovado"])
        self.assertFalse(state["movimentos"][1]["aprovado"])
        for index in range(2, 5):
            self.assertEqual(state["movimentos"][index]["texto_atual"], originals[index])
            self.assertFalse(state["movimentos"][index]["aprovado"])
            self.assertTrue(state["movimentos"][index]["revisao_coerencia"])
        self.assertEqual(state["narrativa_final"], "")
        self.assertEqual(self.action("montar").status_code, 400)
        self.assertEqual(self.client.get(self.url + "/exportar.txt").status_code, 400)
        self.assertEqual(self.calls, [])

    def test_validation_and_approval_are_separate_and_structural_errors_block_approval(self):
        self.assertEqual(self.action("validar:1", texto_1="Texto curto.").status_code, 200)
        state = self.state()
        self.assertFalse(state["movimentos"][0]["validacao"]["valido"])
        self.assertFalse(state["movimentos"][0]["aprovado"])
        self.assertEqual(self.action("aprovar:1").status_code, 400)
        self.assertEqual(self.action("validar:1", texto_1=paragrafo()).status_code, 200)
        self.assertTrue(self.state()["movimentos"][0]["validacao"]["valido"])
        self.assertFalse(self.state()["movimentos"][0]["aprovado"])
        self.assertEqual(self.action("aprovar:1").status_code, 200)
        self.assertTrue(self.state()["movimentos"][0]["aprovado"])
        self.assertEqual(self.calls, [])

    def test_assembly_and_txt_export_preserve_approved_text_exactly_without_api(self):
        paragraphs = self.approve_all()
        self.assertEqual(self.client.get(self.url + "/exportar.txt").status_code, 400)
        self.assertEqual(self.action("montar").status_code, 200)
        final = "\n\n".join(paragraphs)
        self.assertEqual(self.state()["narrativa_final"], final)
        response = self.client.get(self.url + "/exportar.txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(), final.encode("utf-8"))
        self.assertEqual(self.calls, [])
        parser = TextareaValues()
        parser.feed(self.client.get(self.url).get_data(as_text=True))
        self.assertEqual(parser.values["texto-final"], final)
        for index, paragraph in enumerate(paragraphs, 1):
            self.assertEqual(parser.values[f"texto_{index}"], paragraph)

    def test_browser_crlf_transport_preserves_approvals_exact_text_and_assembled_narrative(self):
        paragraphs = self.approve_all()
        self.assertEqual(self.action("montar").status_code, 200)
        before = self.state()
        fields = self.fields()
        for name, value in fields.items():
            if name.startswith(("texto_", "instrucoes_")):
                fields[name] = value.replace("\n", "\r\n")
        fields["acao"] = "salvar"
        self.assertEqual(self.client.post(self.url, data=fields).status_code, 200)
        after = self.state()
        self.assertEqual(after, before)
        self.assertTrue(all(movement["aprovado"] for movement in after["movimentos"]))
        self.assertEqual([movement["texto_atual"] for movement in after["movimentos"]], paragraphs)
        self.assertEqual(self.client.get(self.url + "/exportar.txt").get_data(), "\n\n".join(paragraphs).encode("utf-8"))

    def test_json_export_separates_generated_and_edited_text_and_excludes_credentials(self):
        self.assertEqual(self.action("gerar:1").status_code, 200)
        edited = paragrafo("editada")
        self.assertEqual(self.action("salvar", texto_1=edited).status_code, 200)
        key = "test-secret-value-not-for-export"
        with patch.dict("os.environ", {"NARRATIVA_API_KEY": key}):
            response = self.client.get(self.url + "/exportar.json")
            html = self.client.get(self.url).get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        content = response.get_data(as_text=True)
        state = json.loads(content)
        movement = state["movimentos"][0]
        self.assertEqual(movement["texto_gerado"], paragrafo("gerada"))
        self.assertEqual(movement["texto_atual"], edited)
        self.assertEqual(movement["modelo_utilizado"], "openai/gpt-4.1-mini")
        self.assertIn("validacao", movement)
        self.assertFalse(movement["aprovado"])
        self.assertNotIn(self.row()["csrf_token"], content)
        self.assertNotIn(key, content)
        self.assertNotIn(key, html)
        self.assertNotIn("csrf_token", state)
        self.assertNotIn("NARRATIVA_API_KEY", content)

    def test_add_model_preserves_texts_and_approvals_and_persists_after_restart(self):
        self.assertEqual(self.action("aprovar:1", texto_1=paragrafo("aprovada")).status_code, 200)
        before = self.state()["movimentos"][0]
        model = "fornecedor/modelo-personalizado:free"
        response = self.action("adicionar_modelo", novo_modelo=model, aba="3", texto_3="Um texto que ainda estou editando.")
        self.assert_five_tabs(response, active=3)
        state = self.state()
        self.assertEqual(state["modelo"], model)
        self.assertEqual(state["movimentos"][0], before)
        self.assertEqual(state["movimentos"][2]["texto_atual"], "Um texto que ainda estou editando.")
        self.assertEqual(self.calls, [])
        restarted = create_app(self.config).test_client()
        response = restarted.get(self.url)
        self.assertEqual(response.status_code, 200)
        options = [attrs for tag, attrs in self.markup(response).elements if tag == "option" and attrs.get("value") == model]
        self.assertEqual(len(options), 1)
        self.assertIn("selected", options[0])
        other = restarted.post("/narrativas")
        other_response = restarted.get(other.headers["Location"])
        self.assertIn(model, other_response.get_data(as_text=True))
        self.assertEqual(self.action("gerar:2").status_code, 200)
        self.assertEqual(self.calls[0]["modelo"], model)
        self.assertEqual(self.state()["movimentos"][1]["modelo_utilizado"], model)
        self.assertEqual(self.state()["movimentos"][0], before)

    def test_invalid_model_addition_does_not_mutate_selected_model_or_generate(self):
        previous = self.state()["modelo"]
        response = self.action("adicionar_modelo", novo_modelo="   ", texto_3="Minha edição manual.")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.state()["modelo"], previous)
        self.assertEqual(self.state()["movimentos"][2]["texto_atual"], "Minha edição manual.")
        self.assertEqual(self.calls, [])

    def test_legacy_draft_keeps_text_and_approval_but_never_sends_planning_to_generator(self):
        paragraphs = self.approve_all()
        self.assertEqual(self.action("montar").status_code, 200)
        original = self.state()
        legacy = dict(original)
        legacy["schema_version"] = "1.0.0"
        legacy["ideia_inicial"] = "Ideia antiga exclusiva não usada em geração."
        legacy["planejamento"] = {
            "instrucoes": "Instruções antigas exclusivas.", "texto_gerado": "Planejamento antigo exclusivo.",
            "texto_atual": "Planejamento antigo exclusivo.", "aprovado": True,
            "modelo_utilizado": "modelo-antigo", "historico": [],
        }
        legacy["movimentos"] = [dict(movement) for movement in legacy["movimentos"]]
        legacy["movimentos"][0]["label"] = "Contextualização"
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE narrative_drafts SET state_json = ? WHERE id = ?", (json.dumps(legacy, ensure_ascii=False), self.identifier))
            connection.commit()
        response = self.client.get(self.url + "?aba=5")
        parser = self.assert_five_tabs(response, active=5)
        self.assertNotIn("texto_0", parser.values)
        self.assertNotIn("ideia_inicial", parser.values)
        for index, paragraph in enumerate(paragraphs, 1):
            self.assertEqual(parser.values[f"texto_{index}"], paragraph)
        exported = self.client.get(self.url + "/exportar.json").get_json()
        self.assertNotIn("ideia_inicial", exported)
        self.assertNotIn("planejamento", exported)
        self.assertEqual(exported["legado"]["ideia_inicial"], legacy["ideia_inicial"])
        self.assertEqual(exported["legado"]["planejamento"], legacy["planejamento"])
        self.assertEqual([movement["texto_atual"] for movement in exported["movimentos"]], paragraphs)
        self.assertTrue(all(movement["aprovado"] for movement in exported["movimentos"]))
        self.assertEqual(self.client.get(self.url + "/exportar.txt").get_data(), "\n\n".join(paragraphs).encode("utf-8"))
        self.assertEqual(self.action("gerar:1", substituir_1="on").status_code, 200)
        context = json.dumps(self.calls[0], ensure_ascii=False)
        self.assertNotIn(legacy["ideia_inicial"], context)
        self.assertNotIn(legacy["planejamento"]["texto_atual"], context)
        self.assertNotIn("ideia_inicial", self.calls[0])
        self.assertNotIn("planejamento", self.calls[0])


if __name__ == "__main__":
    unittest.main()
