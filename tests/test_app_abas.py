"""Navegação por etapa sem executar o processamento nem trocar a origem.

Os contratos observados são os links GET, os destinos após POST e a seleção
acessível de um único painel. O CSS não participa das expectativas.
"""

from html.parser import HTMLParser
from pathlib import Path
import sqlite3
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from werkzeug.datastructures import MultiDict

from app import create_app
from fixtures_vetorizacao import GeradorSimulado


class WorkflowMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tabs = {}
        self.panels = {}
        self.inputs = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("role") == "tab":
            self.tabs[values["id"]] = values
        if values.get("role") == "tabpanel":
            self.panels[values["id"]] = values
        if tag == "input" and values.get("name"):
            self.inputs.setdefault(values["name"], []).append(values.get("value"))


class WorkflowTabsIntegrationTests(unittest.TestCase):
    STAGES = (
        "texto", "preparacao", "segmentacao", "morfologia", "sintaxe",
        "regras", "contexto", "vetorizacao",
    )
    TABLES = (
        "submissions", "preparations", "segmentations", "annotations", "analyses",
        "rule_runs", "context_runs", "embedding_runs", "embedding_artifacts",
        "embedding_run_artifacts", "embedding_vectors",
    )
    ORIGINAL = "João não chegou hoje. Maria pode sair amanhã.\r\nEla está feliz."

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "textos.sqlite3"
        self.generator = GeradorSimulado()
        self.factory_calls = []

        def factory(options):
            self.factory_calls.append(options)
            return self.generator

        self.config = {
            "TESTING": True, "DATABASE": str(self.database),
            "EMBEDDING_GENERATOR_FACTORY": factory,
        }
        self.client = create_app(self.config).test_client()

    def snapshot(self):
        with sqlite3.connect(self.database) as connection:
            return {table: connection.execute(f"SELECT * FROM {table}").fetchall()
                    for table in self.TABLES}

    def assert_active(self, response, stage):
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        markup = WorkflowMarkup(response.get_data(as_text=True))
        self.assertEqual(set(markup.tabs), {f"tab-{item}" for item in self.STAGES})
        self.assertEqual(set(markup.panels), {f"panel-{item}" for item in self.STAGES})
        self.assertEqual(
            [name for name, values in markup.tabs.items() if values["aria-selected"] == "true"],
            [f"tab-{stage}"],
        )
        self.assertEqual(
            [name for name, values in markup.panels.items() if "hidden" not in values],
            [f"panel-{stage}"],
        )
        for item in self.STAGES:
            with self.subTest(stage=item):
                self.assertEqual(markup.tabs[f"tab-{item}"]["aria-controls"], f"panel-{item}")
                self.assertEqual(markup.panels[f"panel-{item}"]["aria-labelledby"], f"tab-{item}")
        return markup

    def submit(self):
        response = self.client.post("/envios", data={"content": self.ORIGINAL})
        self.assertEqual(response.status_code, 303)
        location = urlsplit(response.headers["Location"])
        self.assertEqual(location.query, "")
        self.assert_active(self.client.get(response.headers["Location"]), "preparacao")
        return int(location.path.rsplit("/", 1)[-1])

    def source(self):
        document_id = self.submit()
        for endpoint in ("segmentacoes", "anotacoes", "analises", "regras", "contextos"):
            response = self.client.post(f"/envios/{document_id}/{endpoint}", json={})
            self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return document_id, response.get_json()

    def test_initial_and_invalid_submission_keep_only_text_panel_open(self):
        initial = self.assert_active(self.client.get("/"), "texto")
        for stage in self.STAGES[1:]:
            self.assertEqual(initial.tabs[f"tab-{stage}"]["aria-disabled"], "true")
            self.assertNotIn("href", initial.tabs[f"tab-{stage}"])
        response = self.client.post("/envios", data={"content": " \t\r\n"})
        self.assertEqual(response.status_code, 400)
        markup = WorkflowMarkup(response.get_data(as_text=True))
        self.assertNotIn("hidden", markup.panels["panel-texto"])
        self.assertEqual(self.snapshot()["submissions"], [])

    def test_requested_available_tab_and_blocked_fallback_do_not_process(self):
        document_id = self.submit()
        before = self.snapshot()
        self.assert_active(self.client.get(f"/envios/{document_id}"), "preparacao")
        self.assert_active(self.client.get(f"/envios/{document_id}", query_string={"aba": "texto"}), "texto")
        markup = self.assert_active(
            self.client.get(f"/envios/{document_id}", query_string={"aba": "segmentacao"}), "segmentacao",
        )
        self.assertIn("href", markup.tabs["tab-segmentacao"])
        self.assert_active(
            self.client.get(f"/envios/{document_id}", query_string={"aba": "vetorizacao"}), "preparacao",
        )
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.factory_calls, [])

    def test_tab_selector_rejects_unknown_and_repeated_values(self):
        document_id = self.submit()
        for path in ("/", f"/envios/{document_id}"):
            for query in ({"aba": "desconhecida"}, {"aba": ""},
                          MultiDict([("aba", "texto"), ("aba", "preparacao")])):
                with self.subTest(path=path, query=query):
                    response = self.client.get(path, query_string=query)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("erro", response.get_json())

    def test_each_successful_form_returns_to_the_stage_it_created(self):
        document_id = self.submit()
        prepared = self.client.post(f"/envios/{document_id}/preparacoes", data={})
        self.assertEqual(prepared.status_code, 303)
        preparation_query = parse_qs(urlsplit(prepared.headers["Location"]).query)
        self.assertEqual(preparation_query["aba"], ["preparacao"])
        self.assert_active(self.client.get(prepared.headers["Location"]), "preparacao")
        current_id = preparation_query["preparacao_id"][0]
        steps = (
            ("segmentacoes", "segmentacao", "preparacao_id", "segmentacao_id"),
            ("anotacoes", "morfologia", "segmentacao_id", "anotacao_id"),
            ("analises", "sintaxe", "anotacao_id", "analise_id"),
            ("regras", "regras", "analise_id", "execucao_id"),
            ("contextos", "contexto", "regras_execucao_id", "contexto_execucao_id"),
            ("vetorizacoes", "vetorizacao", "contexto_execucao_id", "vetorizacao_execucao_id"),
        )
        for endpoint, stage, selector, result_field in steps:
            with self.subTest(stage=stage):
                response = self.client.post(f"/envios/{document_id}/{endpoint}", data={selector: current_id})
                self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
                query = parse_qs(urlsplit(response.headers["Location"]).query)
                self.assertEqual(query["aba"], [stage])
                current_id = query[result_field][0]
                self.assert_active(self.client.get(response.headers["Location"]), stage)
        self.assertEqual(len(self.factory_calls), 1)

    def test_all_available_get_links_only_select_panels(self):
        document_id, _contextualized = self.source()
        markup = self.assert_active(self.client.get(f"/envios/{document_id}"), "contexto")
        before = self.snapshot()
        for stage in self.STAGES:
            with self.subTest(stage=stage):
                self.assert_active(self.client.get(markup.tabs[f"tab-{stage}"]["href"]), stage)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.factory_calls, [])

    def test_tab_links_preserve_the_exact_historical_context_after_restart(self):
        document_id, contextualized = self.source()
        old_preparation = contextualized["regras"]["preparacao_id"]
        response = self.client.post(f"/envios/{document_id}/preparacoes", json={"normalizar_crlf": True})
        self.assertEqual(response.status_code, 201)
        self.assertNotEqual(response.get_json()["preparacao_id"], old_preparation)
        restarted = create_app(self.config).test_client()
        self.assert_active(restarted.get(f"/envios/{document_id}"), "preparacao")
        markup = self.assert_active(restarted.get(
            f"/envios/{document_id}",
            query_string={"contexto_execucao_id": contextualized["execucao_id"], "aba": "texto"},
        ), "texto")
        before = self.snapshot()
        for stage in self.STAGES:
            with self.subTest(stage=stage):
                link = markup.tabs[f"tab-{stage}"]["href"]
                self.assertEqual(parse_qs(urlsplit(link).query)["contexto_execucao_id"], [contextualized["execucao_id"]])
                selected = self.assert_active(restarted.get(link), stage)
                self.assertIn(old_preparation, selected.inputs["preparacao_id"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.factory_calls, [])


if __name__ == "__main__":
    unittest.main()
