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
        self.current = None
        self.chunks = []

    def handle_starttag(self, tag, attrs):
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
            return "Planejamento gerado." if context["indice"] == 0 else paragrafo("gerada")

        self.config = {"TESTING": True, "DATABASE": str(self.database), "NARRATIVE_GENERATOR": generator}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        response = self.client.post("/narrativas", data={"ideia_inicial": "Uma lembrança da minha família."})
        self.assertEqual(response.status_code, 303)
        self.url = response.headers["Location"]
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
            "ideia_inicial": state["ideia_inicial"], "intensidade": str(state["intensidade"]),
            "provedor": state["provedor"], "modelo": state["modelo"],
            "instrucoes_0": state["planejamento"]["instrucoes"],
            "texto_0": state["planejamento"]["texto_atual"],
        }
        for movement in state["movimentos"]:
            index = movement["id"]
            form[f"instrucoes_{index}"] = movement["instrucoes"]
            form[f"texto_{index}"] = movement["texto_atual"]
        return form

    def action(self, action, **edits):
        fields = self.fields()
        fields.update(edits)
        fields["acao"] = action
        return self.client.post(self.url, data=fields)

    def approve_planning(self):
        response = self.action("aprovar:0", texto_0="Eu volto à cidade para um encontro familiar e deixo uma pergunta aberta.")
        self.assertEqual(response.status_code, 200)

    def approve_all(self):
        self.approve_planning()
        paragraphs = ["\n " + paragrafo(f"memória{index}") + "  " for index in range(1, 6)]
        self.assertEqual(self.action("salvar", **{f"texto_{i}": text for i, text in enumerate(paragraphs, 1)}).status_code, 200)
        for index in range(1, 6):
            self.assertEqual(self.action(f"aprovar:{index}").status_code, 200)
        return paragraphs

    def test_navigation_and_draft_persist_after_restart(self):
        self.assertIn("Gerar narrativa", self.client.get("/").get_data(as_text=True))
        content = "\n  Meu planejamento atual.\n" + "</textarea><script>alert('oi')</script>"
        self.assertEqual(self.action("salvar", texto_0=content, instrucoes_3="Uma lembrança antiga.").status_code, 200)
        restarted = create_app(self.config).test_client()
        response = restarted.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<script>alert", response.get_data(as_text=True))
        self.assertIn("&lt;script&gt;", response.get_data(as_text=True))
        self.assertEqual(self.state()["planejamento"]["texto_atual"], content)
        self.assertEqual(self.state()["movimentos"][2]["instrucoes"], "Uma lembrança antiga.")
        self.assertEqual(self.calls, [])
        parser = TextareaValues()
        parser.feed(response.get_data(as_text=True))
        self.assertEqual(parser.values["texto_0"], content)

    def test_generation_updates_only_target_and_uses_all_current_previous_texts(self):
        self.approve_planning()
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
        self.assertEqual(self.calls[0]["planejamento"], state["planejamento"]["texto_atual"])
        self.assertEqual(self.calls[0]["intensidade"], 5)
        self.assertEqual(self.calls[0]["instrucoes"], "Recorde a infância.")

    def test_regeneration_requires_explicit_confirmation_and_preserves_manual_edits(self):
        self.approve_planning()
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
        self.approve_planning()

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
        self.approve_planning()
        self.app.config["NARRATIVE_GENERATOR"] = None
        with patch.dict("os.environ", {"NARRATIVA_API_KEY": ""}):
            response = self.action("gerar:1", instrucoes_1="Minha primeira noite.")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.state()["movimentos"][0]["instrucoes"], "Minha primeira noite.")
        self.assertIsNone(self.row()["busy_since"])

    def test_stale_revision_and_invalid_csrf_cannot_mutate_or_generate(self):
        stale = self.fields()
        self.assertEqual(self.action("salvar", ideia_inicial="A ideia salva na outra aba.").status_code, 200)
        stale["acao"] = "gerar:0"
        response = self.client.post(self.url, data=stale)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.state()["ideia_inicial"], "A ideia salva na outra aba.")
        fields = self.fields()
        fields.update({"acao": "gerar:0", "csrf_token": "inválido"})
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
        self.approve_planning()
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
            if name.startswith(("texto_", "instrucoes_")) or name == "ideia_inicial":
                fields[name] = value.replace("\n", "\r\n")
        fields["acao"] = "salvar"
        self.assertEqual(self.client.post(self.url, data=fields).status_code, 200)
        after = self.state()
        self.assertEqual(after, before)
        self.assertTrue(all(movement["aprovado"] for movement in after["movimentos"]))
        self.assertEqual([movement["texto_atual"] for movement in after["movimentos"]], paragraphs)
        self.assertEqual(self.client.get(self.url + "/exportar.txt").get_data(), "\n\n".join(paragraphs).encode("utf-8"))

    def test_json_export_separates_generated_and_edited_text_and_excludes_credentials(self):
        self.approve_planning()
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


if __name__ == "__main__":
    unittest.main()
