"""Etapa 08 integrada ao Flask e SQLite: política, origem exata e histórico.

O modelo participa somente das etapas anteriores. As seleções contextuais
esperadas são definidas aqui pela ordem dos períodos, sem usar o construtor.
"""

import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from werkzeug.datastructures import MultiDict

import app as app_module
from app import create_app
from regras_linguisticas import validar_regras_linguisticas
from unidades_contexto import ErroContexto, ErroLimite, validar_unidades_contexto


class ContextIntegrationTests(unittest.TestCase):
    INSTANTE = "2026-10-08T10:30:00-03:00"
    TABELAS = ("submissions", "preparations", "segmentations", "annotations", "analyses", "rule_runs", "context_runs")
    ORIGINAL = "João não chegou hoje. Maria pode sair amanhã.\r\n\r\nJoão chegou. Maria saiu."

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "textos.sqlite3"
        self.config = {"TESTING": True, "DATABASE": str(self.database)}
        self.client = create_app(self.config).test_client()

    def rows(self, table):
        self.assertIn(table, self.TABELAS)
        with sqlite3.connect(self.database) as connection:
            return connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()

    def snapshot(self):
        return {table: self.rows(table) for table in self.TABELAS}

    def submit(self, content=None, **options):
        response = self.client.post("/envios", data={"content": self.ORIGINAL if content is None else content, **options})
        self.assertEqual(response.status_code, 303)
        return int(urlsplit(response.headers["Location"]).path.rsplit("/", 1)[-1])

    def earlier(self, document_id, stage, *, status=201, **options):
        response = self.client.post(f"/envios/{document_id}/{stage}", json=options)
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        return response.get_json()

    def source(self, **options):
        document_id = self.submit(**options)
        self.earlier(document_id, "segmentacoes")
        self.earlier(document_id, "anotacoes")
        self.earlier(document_id, "analises")
        ruled = self.earlier(document_id, "regras")
        validar_regras_linguisticas(ruled)
        return document_id, ruled

    def run_context(self, document_id, **options):
        response = self.client.post(f"/envios/{document_id}/contextos", json=options)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        contextualized = response.get_json()
        validar_unidades_contexto(contextualized)
        return contextualized

    def download(self, document_id, execution_id=None, client=None):
        query = {} if execution_id is None else {"contexto_execucao_id": execution_id}
        return (client or self.client).get(f"/envios/{document_id}/contexto.json", query_string=query)

    def unit(self, document_id, execution_id, **selector):
        return self.client.get(
            f"/envios/{document_id}/unidade-contexto.json",
            query_string={"contexto_execucao_id": execution_id, **selector},
        )

    def replace_json(self, execution_id, contextualized):
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE context_runs SET record_json = ? WHERE execution_id = ?",
                (json.dumps(contextualized, ensure_ascii=False, allow_nan=False), execution_id),
            )

    def assert_conflict(self, document_id, execution_id):
        for path in (f"/envios/{document_id}/contexto.json", f"/envios/{document_id}"):
            response = self.client.get(path, query_string={"contexto_execucao_id": execution_id})
            self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
            self.assertIn("erro", response.get_json())
        response = self.unit(document_id, execution_id, unidade_id="qualquer")
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))

    def test_earlier_stages_do_not_build_context_automatically(self):
        document_id = self.submit()
        self.assertNotIn("Construir unidades de contexto", self.client.get(f"/envios/{document_id}").get_data(as_text=True))
        for stage in ("segmentacoes", "anotacoes", "analises"):
            self.earlier(document_id, stage)
            self.assertEqual(self.rows("context_runs"), [])
        self.assertNotIn("Construir unidades de contexto", self.client.get(f"/envios/{document_id}").get_data(as_text=True))
        ruled = self.earlier(document_id, "regras")
        html = self.client.get(f"/envios/{document_id}").get_data(as_text=True)
        self.assertIn("Construir unidades de contexto", html)
        self.assertIn(f'name="regras_execucao_id" value="{ruled["execucao_id"]}"', html)
        self.assertNotIn("Baixar contexto JSON", html)
        self.assertEqual(self.rows("context_runs"), [])
        self.assertEqual(self.download(document_id).status_code, 404)

    def test_real_chain_is_preserved_without_running_models_or_rules_again(self):
        document_id, ruled = self.source(normalizar_crlf="on")
        before = self.snapshot()
        with patch("app.anotar_segmentacao") as morphology, patch("app.analisar_sintaxe_entidades") as syntax, patch("app.aplicar_regras_linguisticas") as rules:
            contextualized = self.run_context(
                document_id, regras_execucao_id=ruled["execucao_id"],
                execucao_id="contexto/estável", registrado_em=self.INSTANTE,
            )
        for mocked in (morphology, syntax, rules):
            mocked.assert_not_called()
        self.assertEqual(contextualized["execucao_id"], "contexto/estável")
        self.assertEqual(contextualized["registrado_em"], self.INSTANTE)
        self.assertEqual(contextualized["regras_execucao_id"], ruled["execucao_id"])
        self.assertEqual(contextualized["regras"], ruled)
        for table in self.TABELAS[:-1]:
            self.assertEqual(self.rows(table), before[table])
        self.assertTrue(contextualized["validacao"]["pronto_para_etapa_09"])
        downloaded = self.download(document_id, contextualized["execucao_id"])
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.get_json(), contextualized)
        self.assertIn("attachment", downloaded.headers["Content-Disposition"])

    def test_default_policy_and_cross_paragraph_selection_preserve_separators(self):
        document_id, ruled = self.source()
        default = self.run_context(document_id)
        periods = ruled["analise"]["anotacao"]["segmentacao"]["periodos"]
        self.assertEqual(len(periods), 4)
        ids = [period["id"] for period in periods]
        self.assertEqual([unit["janela"]["periodo_ids"] for unit in default["unidades"]], [ids[:2], ids[:2], ids[2:], ids[2:]])
        expected = ["João não chegou hoje. Maria pode sair amanhã."] * 2 + ["João chegou. Maria saiu."] * 2
        self.assertEqual([unit["janela"]["texto"] for unit in default["unidades"]], expected)
        crossed = self.run_context(document_id, raio_anterior=0, raio_seguinte=2, atravessar_paragrafos=True)
        self.assertEqual([unit["janela"]["periodo_ids"] for unit in crossed["unidades"]], [ids[:3], ids[1:], ids[2:], ids[3:]])
        self.assertEqual(crossed["unidades"][0]["janela"]["texto"], "João não chegou hoje. Maria pode sair amanhã.\r\n\r\nJoão chegou.")
        zero = self.run_context(document_id, raio_anterior=0, raio_seguinte=0)
        self.assertEqual([unit["janela"]["periodo_ids"] for unit in zero["unidades"]], [[identifier] for identifier in ids])

    def test_form_redirect_and_html_expose_focus_neighbors_policy_and_provenance(self):
        document_id, ruled = self.source(content="João não chegou hoje. Maria pode sair amanhã. <script>alert('texto')</script>.")
        response = self.client.post(f"/envios/{document_id}/contextos", data={
            "regras_execucao_id": ruled["execucao_id"], "raio_anterior": "2", "raio_seguinte": "0", "atravessar_paragrafos": "on",
        })
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        query = parse_qs(urlsplit(response.headers["Location"]).query)
        self.assertEqual(set(query), {"contexto_execucao_id", "aba"})
        self.assertEqual(query["aba"], ["contexto"])
        detail = self.client.get(response.headers["Location"])
        self.assertEqual(detail.status_code, 200, detail.get_data(as_text=True))
        html = detail.get_data(as_text=True)
        for label in ("Unidades de contexto", "foco destacado", "<mark>", "Vizinhos selecionados", "Política aplicada", "Pode atravessar parágrafos", "Pendências herdadas", "Parágrafo completo do foco", "Documento completo de origem", "Baixar contexto JSON", "Hash SHA-256", "pronto para a etapa 09"):
            self.assertIn(label, html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("não resolve automaticamente ambiguidades", html)

    def test_query_requires_explicit_execution_and_one_selector(self):
        document_id, ruled = self.source()
        contextualized = self.run_context(document_id)
        first = contextualized["unidades"][0]
        for selector in ({"unidade_id": first["id"]}, {"periodo_id": first["periodo_foco_id"]}):
            response = self.unit(document_id, contextualized["execucao_id"], **selector)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json(), first)
        invalid = ({}, {"periodo_id": first["periodo_foco_id"]}, {"contexto_execucao_id": contextualized["execucao_id"]},
                   {"contexto_execucao_id": contextualized["execucao_id"], "unidade_id": first["id"], "periodo_id": first["periodo_foco_id"]},
                   {"contexto_execucao_id": "", "unidade_id": first["id"]},
                   {"contexto_execucao_id": contextualized["execucao_id"], "periodo_id": ""},
                   {"contexto_execucao_id": contextualized["execucao_id"], "periodo_id": first["periodo_foco_id"], "extra": "x"},
                   MultiDict([("contexto_execucao_id", contextualized["execucao_id"]), ("contexto_execucao_id", contextualized["execucao_id"]), ("periodo_id", first["periodo_foco_id"])]))
        for query in invalid:
            with self.subTest(query=query):
                self.assertEqual(self.client.get(f"/envios/{document_id}/unidade-contexto.json", query_string=query).status_code, 400)
        self.assertEqual(self.unit(document_id, contextualized["execucao_id"], unidade_id="ausente").status_code, 404)
        self.assertEqual(self.unit(document_id, "ausente", periodo_id=first["periodo_foco_id"]).status_code, 404)
        self.assertEqual(self.unit(document_id, contextualized["execucao_id"], periodo_id=ruled["execucao_id"]).status_code, 404)

    def test_same_period_is_queried_in_distinct_historical_policies(self):
        document_id, ruled = self.source()
        first = self.run_context(document_id, execucao_id="foco", raio_anterior=0, raio_seguinte=0)
        second = self.run_context(document_id, execucao_id="ampla", raio_anterior=3, raio_seguinte=3, atravessar_paragrafos=True)
        period_id = ruled["analise"]["anotacao"]["segmentacao"]["periodos"][0]["id"]
        self.assertEqual(self.unit(document_id, first["execucao_id"], periodo_id=period_id).get_json()["janela"]["texto"], "João não chegou hoje.")
        self.assertEqual(self.unit(document_id, second["execucao_id"], periodo_id=period_id).get_json()["janela"]["texto"], self.ORIGINAL)

    def test_historical_runs_keep_logical_identity_and_exact_origin_after_restart(self):
        document_id, ruled = self.source()
        first = self.run_context(document_id, execucao_id="primeiro", registrado_em=self.INSTANTE)
        second = self.run_context(document_id, execucao_id="segundo")
        self.assertNotEqual(first["unidades"][0]["id"], second["unidades"][0]["id"])
        self.assertEqual([unit["janela_logica_id"] for unit in first["unidades"]], [unit["janela_logica_id"] for unit in second["unidades"]])
        self.assertEqual(self.download(document_id).get_json(), second)
        newer = self.earlier(document_id, "regras")
        html = self.client.get(f"/envios/{document_id}").get_data(as_text=True)
        self.assertNotIn("Baixar contexto JSON", html)
        self.run_context(document_id)
        self.earlier(document_id, "preparacoes", normalizar_crlf=True)
        self.earlier(document_id, "segmentacoes")
        self.earlier(document_id, "anotacoes")
        self.earlier(document_id, "analises")
        restarted = create_app(self.config).test_client()
        self.assertEqual(self.download(document_id, first["execucao_id"], restarted).get_json(), first)
        detail = restarted.get(f"/envios/{document_id}", query_string={"contexto_execucao_id": first["execucao_id"]})
        self.assertEqual(detail.status_code, 200)
        html = detail.get_data(as_text=True)
        self.assertIn("Execuções de contexto salvas (2)", html)
        for field in ("preparacao_id", "segmentacao_id", "anotacao_id", "analise_id"):
            self.assertIn(f'name="{field}" value="{ruled[field]}"', html)
        self.assertIn(f'name="regras_execucao_id" value="{ruled["execucao_id"]}"', html)
        for query in ({"execucao_id": newer["execucao_id"]}, {"analise_id": "outra"}, {"anotacao_id": "outra"}, {"segmentacao_id": "outra"}, {"preparacao_id": "outra"}):
            with self.subTest(query=query):
                response = restarted.get(f"/envios/{document_id}", query_string={"contexto_execucao_id": first["execucao_id"], **query})
                self.assertEqual(response.status_code, 400)

    def test_unready_rule_diagnostic_is_never_promoted_or_saved(self):
        document_id, _ruled = self.source()
        diagnostic = self.earlier(document_id, "regras", status=422, regras_habilitadas=[])
        self.assertFalse(validar_regras_linguisticas(diagnostic)["pronto_para_etapa_08"])
        before = self.snapshot()
        response = self.client.post(f"/envios/{document_id}/contextos", json={"regras_execucao_id": diagnostic["execucao_id"]})
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertEqual(self.snapshot(), before)
        html = self.client.get(f"/envios/{document_id}").get_data(as_text=True)
        self.assertNotIn('>Construir unidades de contexto', html)
        self.assertIn("Conclua as regras desta execução", html)

    def test_invalid_payload_policy_types_and_dates_write_nothing(self):
        document_id, _ruled = self.source()
        before = self.snapshot()
        payloads = ([], None, "texto", {"texto": "substituir"}, {"regras_execucao_id": None}, {"regras_execucao_id": 1},
                    {"execucao_id": ""}, {"execucao_id": 1}, {"registrado_em": None}, {"registrado_em": "2026-10-08"},
                    {"raio_anterior": -1}, {"raio_seguinte": True}, {"raio_anterior": False}, {"raio_anterior": 1.0},
                    {"raio_seguinte": "1"}, {"raio_seguinte": None}, {"atravessar_paragrafos": 1}, {"atravessar_paragrafos": 0},
                    {"atravessar_paragrafos": "false"}, {"atravessar_paragrafos": None})
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.client.post(f"/envios/{document_id}/contextos", data=json.dumps(payload), content_type="application/json")
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
                self.assertIn("erro", response.get_json())
        self.assertEqual(self.client.post(f"/envios/{document_id}/contextos", data="{", content_type="application/json").status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_form_and_execution_queries_reject_duplicates_and_unknown_fields(self):
        document_id, ruled = self.source()
        before = self.snapshot()
        forms = ({}, {"regras_execucao_id": ""}, {"regras_execucao_id": ruled["execucao_id"], "execucao_id": "extra"},
                 {"regras_execucao_id": ruled["execucao_id"], "raio_anterior": "-1"},
                 {"regras_execucao_id": ruled["execucao_id"], "raio_seguinte": "1.0"},
                 {"regras_execucao_id": ruled["execucao_id"], "atravessar_paragrafos": "false"},
                 MultiDict([("regras_execucao_id", ruled["execucao_id"]), ("regras_execucao_id", ruled["execucao_id"])]))
        for form in forms:
            with self.subTest(form=form):
                self.assertEqual(self.client.post(f"/envios/{document_id}/contextos", data=form).status_code, 400)
        self.assertEqual(self.snapshot(), before)
        for query in ({"contexto_execucao_id": ""}, MultiDict([("contexto_execucao_id", "x"), ("contexto_execucao_id", "x")])):
            for path in (f"/envios/{document_id}", f"/envios/{document_id}/contexto.json"):
                with self.subTest(query=query, path=path):
                    self.assertEqual(self.client.get(path, query_string=query).status_code, 400)
        contextualized = self.run_context(document_id)
        for field in ("execucao_id", "analise_id", "anotacao_id", "segmentacao_id", "preparacao_id"):
            value = ruled[field]
            query = MultiDict([("contexto_execucao_id", contextualized["execucao_id"]), (field, value), (field, value)])
            with self.subTest(field=field):
                self.assertEqual(self.client.get(f"/envios/{document_id}", query_string=query).status_code, 400)
        self.assertEqual(self.client.get(f"/envios/{document_id}/contexto.json", query_string={"execucao_id": contextualized["execucao_id"]}).status_code, 400)

    def test_duplicate_identifier_never_overwrites_prior_runs(self):
        document_id, _ruled = self.source()
        self.run_context(document_id, execucao_id="único")
        before = self.snapshot()
        response = self.client.post(f"/envios/{document_id}/contextos", json={"execucao_id": "único"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_missing_documents_or_rules_and_foreign_execution_return_404(self):
        self.assertEqual(self.client.post("/envios/999/contextos", json={}).status_code, 404)
        self.assertEqual(self.download(999).status_code, 404)
        first_id = self.submit()
        self.assertEqual(self.client.post(f"/envios/{first_id}/contextos", json={}).status_code, 404)
        second_id, second_rules = self.source()
        contextualized = self.run_context(second_id)
        before = self.snapshot()
        self.assertEqual(self.client.post(f"/envios/{first_id}/contextos", json={"regras_execucao_id": second_rules["execucao_id"]}).status_code, 404)
        self.assertEqual(self.download(first_id, contextualized["execucao_id"]).status_code, 404)
        self.assertEqual(self.client.get(f"/envios/{first_id}", query_string={"contexto_execucao_id": contextualized["execucao_id"]}).status_code, 404)
        self.assertEqual(self.unit(first_id, contextualized["execucao_id"], unidade_id=contextualized["unidades"][0]["id"]).status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_malformed_saved_json_is_refused_by_every_context_reader(self):
        document_id, _ruled = self.source()
        contextualized = self.run_context(document_id)
        for malformed in ("{", "null", "[]"):
            with self.subTest(malformed=malformed):
                with sqlite3.connect(self.database) as connection:
                    connection.execute("UPDATE context_runs SET record_json = ?", (malformed,))
                self.assert_conflict(document_id, contextualized["execucao_id"])

    def test_tampered_hash_neighbors_logical_identity_and_readiness_are_refused(self):
        document_id, _ruled = self.source()
        contextualized = self.run_context(document_id)
        mutations = (
            lambda item: item["unidades"][0]["foco"].__setitem__("sha256_texto", "0" * 64),
            lambda item: item["unidades"][0]["janela"].__setitem__("texto", "texto substituído"),
            lambda item: item["unidades"][0].__setitem__("seguintes", [item["unidades"][-1]["periodo_foco_id"]]),
            lambda item: item["unidades"][0].__setitem__("janela_logica_id", "0" * 64),
            lambda item: item["unidades"][0]["foco"]["anotacoes"]["morfologia"][0].__setitem__("token_id", "outro-registro"),
            lambda item: item["validacao"].__setitem__("pronto_para_etapa_09", False),
        )
        for number, mutation in enumerate(mutations):
            with self.subTest(number=number):
                changed = copy.deepcopy(contextualized)
                mutation(changed)
                self.replace_json(contextualized["execucao_id"], changed)
                self.assert_conflict(document_id, contextualized["execucao_id"])

    def test_timestamp_and_embedded_origin_must_match_stored_rows(self):
        document_id, _ruled = self.source()
        contextualized = self.run_context(document_id)
        changed = copy.deepcopy(contextualized)
        changed["registrado_em"] = self.INSTANTE
        validar_unidades_contexto(changed)
        self.replace_json(contextualized["execucao_id"], changed)
        self.assert_conflict(document_id, contextualized["execucao_id"])
        changed = copy.deepcopy(contextualized)
        changed["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]["metadados_origem"]["fonte"] = "metadado posterior"
        validar_unidades_contexto(changed)
        self.replace_json(contextualized["execucao_id"], changed)
        self.assert_conflict(document_id, contextualized["execucao_id"])
        self.replace_json(contextualized["execucao_id"], contextualized)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE context_runs SET execution_id = ?", ("substituído",))
        self.assertEqual(self.download(document_id, contextualized["execucao_id"]).status_code, 404)
        self.assert_conflict(document_id, "substituído")

    def test_database_foreign_key_cannot_redirect_or_reassign_execution(self):
        document_id, ruled = self.source()
        contextualized = self.run_context(document_id)
        newer = self.earlier(document_id, "regras")
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE context_runs SET rule_execution_id = ?", (newer["execucao_id"],))
        self.assert_conflict(document_id, contextualized["execucao_id"])
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE context_runs SET rule_execution_id = ?", (ruled["execucao_id"],))
        second_id = self.submit()
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE context_runs SET submission_id = ?", (second_id,))
        self.assertEqual(self.download(document_id, contextualized["execucao_id"]).status_code, 404)
        self.assert_conflict(second_id, contextualized["execucao_id"])

    def test_corrupted_or_missing_rule_origin_blocks_reads_and_new_runs(self):
        document_id, ruled = self.source()
        contextualized = self.run_context(document_id)
        before = self.rows("context_runs")
        changed = copy.deepcopy(ruled)
        changed["ocorrencias"][0]["nucleo_token_ids"] = ["inexistente"]
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE rule_runs SET record_json = ?", (json.dumps(changed),))
        self.assert_conflict(document_id, contextualized["execucao_id"])
        self.assertEqual(self.client.post(f"/envios/{document_id}/contextos", json={}).status_code, 409)
        self.assertEqual(self.rows("context_runs"), before)
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM rule_runs")
        self.assert_conflict(document_id, contextualized["execucao_id"])

    def test_additive_schema_upgrade_preserves_previous_stages(self):
        document_id, ruled = self.source()
        before = {table: self.rows(table) for table in self.TABELAS[:-1]}
        with sqlite3.connect(self.database) as connection:
            connection.execute("DROP TABLE context_runs")
        self.client = create_app(self.config).test_client()
        for table, rows in before.items():
            self.assertEqual(self.rows(table), rows)
        self.assertEqual(self.rows("context_runs"), [])
        self.assertEqual(self.run_context(document_id)["regras"], ruled)

    def test_failure_after_insert_rolls_back_and_limit_returns_explicit_error(self):
        document_id, _ruled = self.source()
        self.run_context(document_id)
        before = self.snapshot()
        save = app_module.save_context_run

        def insert_then_fail(connection, document, ruled, **options):
            save(connection, document, ruled, **options)
            raise ErroContexto("Falha após a inserção.")

        with patch("app.save_context_run", side_effect=insert_then_fail), patch("app.anotar_segmentacao") as fallback:
            response = self.client.post(f"/envios/{document_id}/contextos", json={})
        self.assertEqual(response.status_code, 400)
        fallback.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        with patch("app.construir_unidades_contexto", side_effect=ErroLimite("Limite contextual excedido.")):
            response = self.client.post(f"/envios/{document_id}/contextos", json={})
        self.assertEqual(response.status_code, 413)
        self.assertIn("Limite contextual", response.get_json()["erro"])
        self.assertEqual(self.snapshot(), before)

    def test_existing_global_context_route_still_crosses_paragraphs(self):
        document_id, ruled = self.source()
        segmented = ruled["analise"]["anotacao"]["segmentacao"]
        response = self.client.get(f"/envios/{document_id}/contexto-periodo.json", query_string={
            "segmentacao_id": segmented["segmentacao_id"], "periodo_id": segmented["periodos"][1]["id"],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["seguinte"]["id"], segmented["periodos"][2]["id"])
        default = self.run_context(document_id)
        self.assertEqual(default["unidades"][1]["seguintes"], [])


if __name__ == "__main__":
    unittest.main()
