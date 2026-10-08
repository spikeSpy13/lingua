"""Etapa 09 via Flask/SQLite com embeddings explicitamente simulados.

As etapas linguísticas fornecem uma origem real validada; a inferência da 09 é
substituída por um adaptador controlado. Os testes observam o histórico, a
integridade de leitura e a ausência de transações durante a geração.
"""

import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import zipfile

from werkzeug.datastructures import MultiDict

import app as app_module
from app import create_app
from fixtures_vetorizacao import GeradorSimulado
from unidades_contexto import validar_unidades_contexto


class EmbeddingIntegrationTests(unittest.TestCase):
    INSTANTE = "2026-10-08T11:30:00-03:00"
    ANTERIORES = (
        "submissions", "preparations", "segmentations", "annotations",
        "analyses", "rule_runs", "context_runs",
    )
    VETORES = ("embedding_runs", "embedding_artifacts", "embedding_run_artifacts", "embedding_vectors")
    ORIGINAL = "João não chegou hoje. Maria pode sair amanhã.\r\n\r\nJoão chegou. Maria saiu."

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "textos.sqlite3"
        self.generator = GeradorSimulado()
        self.factory_calls = []

        def factory(options):
            self.factory_calls.append(copy.deepcopy(options))
            return self.generator

        self.config = {
            "TESTING": True, "DATABASE": str(self.database),
            "EMBEDDING_GENERATOR_FACTORY": factory,
        }
        self.application = create_app(self.config)
        self.client = self.application.test_client()

    def rows(self, table):
        self.assertIn(table, self.ANTERIORES + self.VETORES)
        with sqlite3.connect(self.database) as connection:
            ordering = "id" if table in self.ANTERIORES or table == "embedding_runs" else ("artifact_id" if table == "embedding_artifacts" else "execution_id")
            return connection.execute(f"SELECT * FROM {table} ORDER BY {ordering}").fetchall()

    def snapshot(self):
        return {table: self.rows(table) for table in self.ANTERIORES + self.VETORES}

    def submit(self, content=None, **options):
        response = self.client.post("/envios", data={
            "content": self.ORIGINAL if content is None else content, **options,
        })
        self.assertEqual(response.status_code, 303)
        return int(urlsplit(response.headers["Location"]).path.rsplit("/", 1)[-1])

    def earlier(self, document_id, stage, *, status=201, **options):
        response = self.client.post(f"/envios/{document_id}/{stage}", json=options)
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        return response.get_json()

    def source(self, **options):
        document_id = self.submit(**options)
        for stage in ("segmentacoes", "anotacoes", "analises", "regras"):
            self.earlier(document_id, stage)
        contextualized = self.earlier(document_id, "contextos")
        self.assertTrue(validar_unidades_contexto(contextualized)["pronto_para_etapa_09"])
        return document_id, contextualized

    def run_embedding(self, document_id, **options):
        response = self.client.post(f"/envios/{document_id}/vetorizacoes", json=options)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()

    def download(self, document_id, execution_id=None, *, client=None, suffix="json"):
        query = {} if execution_id is None else {"vetorizacao_execucao_id": execution_id}
        return (client or self.client).get(f"/envios/{document_id}/vetorizacao.{suffix}", query_string=query)

    def representation(self, document_id, execution_id, representation_id):
        return self.client.get(f"/envios/{document_id}/representacao-vetorial.json", query_string={
            "vetorizacao_execucao_id": execution_id, "representacao_id": representation_id,
        })

    def replace_json(self, execution_id, record):
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE embedding_runs SET record_json = ? WHERE execution_id = ?", (
                json.dumps(record, ensure_ascii=False, allow_nan=False), execution_id,
            ))

    def assert_conflict(self, document_id, execution_id):
        for suffix in ("json", "zip"):
            response = self.download(document_id, execution_id, suffix=suffix)
            self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
            self.assertIn("erro", response.get_json())
        detail = self.client.get(f"/envios/{document_id}", query_string={"vetorizacao_execucao_id": execution_id})
        self.assertEqual(detail.status_code, 409, detail.get_data(as_text=True))
        self.assertEqual(self.representation(document_id, execution_id, "qualquer").status_code, 409)

    def test_earlier_stages_never_generate_vectors_automatically(self):
        document_id = self.submit()
        for stage in ("segmentacoes", "anotacoes", "analises", "regras", "contextos"):
            self.earlier(document_id, stage)
            self.assertEqual(self.rows("embedding_runs"), [])
        self.assertEqual(self.factory_calls, [])
        self.assertEqual(self.generator.chamadas_geracao, [])
        self.assertEqual(self.download(document_id).status_code, 404)

    def test_generation_preserves_previous_stages_and_never_reexecutes_them(self):
        document_id, contextualized = self.source(normalizar_crlf="on")
        before = {table: self.rows(table) for table in self.ANTERIORES}
        with patch("app.anotar_segmentacao") as morphology, patch("app.analisar_sintaxe_entidades") as syntax, patch("app.aplicar_regras_linguisticas") as rules, patch("app.construir_unidades_contexto") as contexts:
            record = self.run_embedding(document_id, contexto_execucao_id=contextualized["execucao_id"],
                              execucao_id="vetorização/estável", registrado_em=self.INSTANTE)
        for mocked in (morphology, syntax, rules, contexts):
            mocked.assert_not_called()
        self.assertEqual(record["execucao_id"], "vetorização/estável")
        self.assertEqual(record["registrado_em"], self.INSTANTE)
        self.assertEqual(record["contexto_execucao_id"], contextualized["execucao_id"])
        self.assertEqual(record["contexto"], contextualized)
        for table in self.ANTERIORES:
            self.assertEqual(self.rows(table), before[table])
        self.assertTrue(self.generator.chamadas_geracao)
        self.assertEqual(record["modelo"]["natureza"], "simulado_teste")
        downloaded = self.download(document_id, record["execucao_id"])
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.get_json(), record)
        self.assertIn("attachment", downloaded.headers["Content-Disposition"])

    def test_inference_holds_no_sqlite_write_transaction(self):
        document_id, _contextualized = self.source()
        generate = self.generator.gerar
        observed = []

        def check_database_then_generate(batch):
            with sqlite3.connect(self.database, timeout=0.05) as connection:
                connection.execute("BEGIN IMMEDIATE")
                observed.append(connection.execute("SELECT COUNT(*) FROM context_runs").fetchone()[0])
                connection.rollback()
            return generate(batch)

        with patch.object(self.generator, "gerar", side_effect=check_database_then_generate):
            self.run_embedding(document_id)
        self.assertTrue(observed)
        self.assertTrue(all(count == 1 for count in observed))

    def test_history_and_downloads_survive_restart_without_loading_generator(self):
        document_id, contextualized = self.source()
        first = self.run_embedding(document_id, execucao_id="primeiro", registrado_em=self.INSTANTE)
        second = self.run_embedding(document_id, execucao_id="segundo")
        self.assertNotEqual(first["execucao_id"], second["execucao_id"])
        self.assertEqual(self.download(document_id).get_json(), second)
        self.earlier(document_id, "contextos", raio_anterior=0, raio_seguinte=0)
        self.earlier(document_id, "preparacoes", normalizar_crlf=True)
        self.earlier(document_id, "segmentacoes")
        self.earlier(document_id, "anotacoes")
        restarted_config = {**self.config, "EMBEDDING_GENERATOR_FACTORY": lambda _options: self.fail("Leitura não deve carregar o gerador.")}
        restarted = create_app(restarted_config).test_client()
        for record in (first, second):
            self.assertEqual(self.download(document_id, record["execucao_id"], client=restarted).get_json(), record)
            exported = self.download(document_id, record["execucao_id"], client=restarted, suffix="zip")
            self.assertEqual(exported.status_code, 200)
            self.assertTrue(zipfile.is_zipfile(io.BytesIO(exported.data)))
        response = restarted.get(f"/envios/{document_id}", query_string={"vetorizacao_execucao_id": first["execucao_id"]})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn(contextualized["execucao_id"], response.get_data(as_text=True))

    def test_duplicate_execution_never_overwrites_history(self):
        document_id, _contextualized = self.source()
        first = self.run_embedding(document_id, execucao_id="único")
        before = self.snapshot()
        response = self.client.post(f"/envios/{document_id}/vetorizacoes", json={"execucao_id": "único"})
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.download(document_id, "único").get_json(), first)

    def test_missing_documents_sources_and_foreign_runs_return_404(self):
        self.assertEqual(self.client.post("/envios/999/vetorizacoes", json={}).status_code, 404)
        self.assertEqual(self.download(999).status_code, 404)
        first_id = self.submit()
        self.assertEqual(self.client.post(f"/envios/{first_id}/vetorizacoes", json={}).status_code, 404)
        second_id, contextualized = self.source()
        record = self.run_embedding(second_id)
        before = self.snapshot()
        response = self.client.post(f"/envios/{first_id}/vetorizacoes", json={"contexto_execucao_id": contextualized["execucao_id"]})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.download(first_id, record["execucao_id"]).status_code, 404)
        self.assertEqual(self.client.get(f"/envios/{first_id}", query_string={"vetorizacao_execucao_id": record["execucao_id"]}).status_code, 404)
        self.assertEqual(self.representation(first_id, record["execucao_id"], "qualquer").status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_payloads_write_nothing_and_do_not_load_generator(self):
        document_id, _contextualized = self.source()
        before = self.snapshot()
        payloads = ([], None, "texto", {"texto": "substituir"}, {"contexto_execucao_id": None},
                    {"contexto_execucao_id": 1}, {"execucao_id": ""}, {"execucao_id": 1},
                    {"registrado_em": None}, {"registrado_em": "2026-10-08"},
                    {"configuracao": []}, {"configuracao": None}, {"configuracao": {"desconhecida": True}})
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.client.post(f"/envios/{document_id}/vetorizacoes", data=json.dumps(payload), content_type="application/json")
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
                self.assertIn("erro", response.get_json())
        self.assertEqual(self.client.post(f"/envios/{document_id}/vetorizacoes", data="{", content_type="application/json").status_code, 400)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.factory_calls, [])

    def test_execution_queries_reject_duplicates_empty_and_unknown_fields(self):
        document_id, _contextualized = self.source()
        record = self.run_embedding(document_id)
        invalid = ({"vetorizacao_execucao_id": ""}, {"execucao_id": record["execucao_id"]},
                   {"vetorizacao_execucao_id": record["execucao_id"], "extra": "x"},
                   MultiDict([("vetorizacao_execucao_id", record["execucao_id"])] * 2))
        for query in invalid:
            for suffix in ("json", "zip"):
                with self.subTest(query=query, suffix=suffix):
                    response = self.client.get(f"/envios/{document_id}/vetorizacao.{suffix}", query_string=query)
                    self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        for query in ({"vetorizacao_execucao_id": ""}, MultiDict([("vetorizacao_execucao_id", record["execucao_id"])] * 2)):
            self.assertEqual(self.client.get(f"/envios/{document_id}", query_string=query).status_code, 400)

    def test_representation_query_requires_execution_and_one_representation(self):
        document_id, _contextualized = self.source()
        record = self.run_embedding(document_id)
        invalid = ({}, {"representacao_id": "qualquer"}, {"vetorizacao_execucao_id": record["execucao_id"]},
                   {"vetorizacao_execucao_id": "", "representacao_id": "qualquer"},
                   {"vetorizacao_execucao_id": record["execucao_id"], "representacao_id": ""},
                   {"vetorizacao_execucao_id": record["execucao_id"], "representacao_id": "qualquer", "extra": "x"},
                   MultiDict([("vetorizacao_execucao_id", record["execucao_id"]), ("representacao_id", "x"), ("representacao_id", "x")]))
        for query in invalid:
            with self.subTest(query=query):
                self.assertEqual(self.client.get(f"/envios/{document_id}/representacao-vetorial.json", query_string=query).status_code, 400)
        self.assertEqual(self.representation(document_id, record["execucao_id"], "ausente").status_code, 404)
        self.assertEqual(self.representation(document_id, "ausente", "ausente").status_code, 404)

    def test_malformed_saved_json_is_rejected_by_all_readers(self):
        document_id, _contextualized = self.source()
        record = self.run_embedding(document_id)
        for malformed in ("{", "null", "[]"):
            with self.subTest(malformed=malformed):
                with sqlite3.connect(self.database) as connection:
                    connection.execute("UPDATE embedding_runs SET record_json = ?", (malformed,))
                self.assert_conflict(document_id, record["execucao_id"])

    def test_corrupt_context_is_rejected_before_inference_and_on_reads(self):
        document_id, contextualized = self.source()
        record = self.run_embedding(document_id)
        before = self.snapshot()
        changed = copy.deepcopy(contextualized)
        changed["unidades"][0]["foco"]["sha256_texto"] = "0" * 64
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE context_runs SET record_json = ? WHERE execution_id = ?", (
                json.dumps(changed, ensure_ascii=False), contextualized["execucao_id"],
            ))
        self.assert_conflict(document_id, record["execucao_id"])
        calls = len(self.generator.chamadas_geracao)
        response = self.client.post(f"/envios/{document_id}/vetorizacoes", json={})
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertEqual(len(self.generator.chamadas_geracao), calls)
        for table in self.VETORES:
            self.assertEqual(self.rows(table), before[table])

    def test_exact_reexecution_reuses_artifacts_but_keeps_two_origins(self):
        document_id, _contextualized = self.source()
        first = self.run_embedding(document_id, execucao_id="cache-primeiro")
        count = len(self.generator.entradas_geradas)
        physical_artifacts = self.rows("embedding_artifacts")
        second = self.run_embedding(document_id, execucao_id="cache-segundo")
        self.assertEqual(len(self.generator.entradas_geradas), count)
        self.assertEqual(self.rows("embedding_artifacts"), physical_artifacts)
        self.assertEqual(len(self.rows("embedding_runs")), 2)
        self.assertEqual([item["id"] for item in first["artefatos"]], [item["id"] for item in second["artefatos"]])
        self.assertNotEqual([item["id"] for item in first["representacoes"]], [item["id"] for item in second["representacoes"]])
        for record in (first, second):
            self.assertEqual(self.download(document_id, record["execucao_id"]).get_json(), record)

    def test_recomputed_numeric_variant_keeps_both_historical_blobs(self):
        document_id, _contextualized = self.source()
        self.application.config["EMBEDDING_CACHE_MAX_ARTIFACTS"] = 0
        first = self.run_embedding(document_id, execucao_id="variante-original")
        first_artifacts = self.rows("embedding_artifacts")
        original_generate = self.generator.gerar

        def slightly_changed(batch):
            vectors = original_generate(batch)
            for vector in vectors:
                vector[0] += 0.00001
                norm = sum(value * value for value in vector) ** 0.5
                vector[:] = [value / norm for value in vector]
            return vectors

        self.generator.gerar = slightly_changed
        second = self.run_embedding(document_id, execucao_id="variante-recalculada")
        self.assertEqual([item["id"] for item in first["artefatos"]], [item["id"] for item in second["artefatos"]])
        self.assertNotEqual([item["armazenamento"]["sha256_bytes"] for item in first["artefatos"]],
                            [item["armazenamento"]["sha256_bytes"] for item in second["artefatos"]])
        self.assertEqual(len(self.rows("embedding_artifacts")), 2 * len(first_artifacts))
        restarted_config = {key: value for key, value in self.config.items() if key != "EMBEDDING_GENERATOR_FACTORY"}
        restarted = create_app(restarted_config).test_client()
        with patch("app.criar_gerador_padrao", side_effect=AssertionError("Leitura não carrega o modelo")):
            for record in (first, second):
                response = self.download(document_id, record["execucao_id"], client=restarted)
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
                self.assertEqual(response.get_json(), record)

    def test_zip_contains_exact_vector_bytes_with_independent_hashes(self):
        document_id, _contextualized = self.source(normalizar_crlf="on")
        record = self.run_embedding(document_id)
        response = self.download(document_id, record["execucao_id"], suffix="zip")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        originals = {
            ("artefato", item["id"]): base64.b64decode(item["armazenamento"]["base64"], validate=True)
            for item in record["artefatos"]
        }
        originals.update({
            ("representacao", item["id"]): base64.b64decode(item["vetor"]["base64"], validate=True)
            for item in record["representacoes"] if item["vetor"] is not None
        })
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            manifest = json.loads(archive.read("manifesto.json"))
            self.assertEqual(manifest["formato"], "lingua_etapa09_zip")
            self.assertEqual(manifest["registro"]["contexto"], record["contexto"])
            self.assertEqual(len(manifest["arquivos"]), len(originals))
            for item in manifest["arquivos"]:
                raw = archive.read(item["caminho"])
                self.assertEqual(raw, originals[(item["tipo"], item["id"])])
                self.assertEqual(len(raw), item["bytes"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), item["sha256_bytes"])
                values = struct.unpack("<" + "f" * self.generator.dimensao, raw)
                self.assertAlmostEqual(sum(value * value for value in values), 1.0, places=5)

    def test_representation_endpoint_returns_exact_association_from_execution(self):
        document_id, _contextualized = self.source()
        first = self.run_embedding(document_id, execucao_id="consulta-primeiro")
        second = self.run_embedding(document_id, execucao_id="consulta-segundo")
        for item in first["representacoes"]:
            response = self.representation(document_id, first["execucao_id"], item["id"])
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertEqual(response.get_json(), item)
            self.assertEqual(self.representation(document_id, second["execucao_id"], item["id"]).status_code, 404)

    def test_tampered_or_missing_vector_blobs_are_rejected(self):
        document_id, _contextualized = self.source()
        record = self.run_embedding(document_id)
        with sqlite3.connect(self.database) as connection:
            saved = connection.execute("SELECT * FROM embedding_vectors LIMIT 1").fetchone()
            connection.execute("UPDATE embedding_vectors SET vector_bytes = ? WHERE execution_id = ? AND representation_id = ?", (
                b"\x00", saved[0], saved[1],
            ))
        self.assert_conflict(document_id, record["execucao_id"])
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE embedding_vectors SET vector_bytes = ? WHERE execution_id = ? AND representation_id = ?", (
                saved[2], saved[0], saved[1],
            ))
            connection.execute("DELETE FROM embedding_vectors WHERE execution_id = ? AND representation_id = ?", (saved[0], saved[1]))
        self.assert_conflict(document_id, record["execucao_id"])

    def test_tampered_cache_artifact_blocks_reads_and_reuse(self):
        document_id, _contextualized = self.source()
        record = self.run_embedding(document_id)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE embedding_artifacts SET vector_bytes = ?", (b"\x00",))
        self.assert_conflict(document_id, record["execucao_id"])
        calls = len(self.generator.chamadas_geracao)
        runs = self.rows("embedding_runs")
        response = self.client.post(f"/envios/{document_id}/vetorizacoes", json={})
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertEqual(self.rows("embedding_runs"), runs)
        self.assertEqual(len(self.generator.chamadas_geracao), calls)

    def test_manifest_checksum_and_timestamp_cannot_be_changed(self):
        document_id, _contextualized = self.source()
        record = self.run_embedding(document_id)
        with sqlite3.connect(self.database) as connection:
            saved = connection.execute("SELECT record_json FROM embedding_runs WHERE execution_id = ?", (record["execucao_id"],)).fetchone()[0]
            connection.execute("UPDATE embedding_runs SET sha256_manifest = ?", ("0" * 64,))
        self.assert_conflict(document_id, record["execucao_id"])
        manifest = json.loads(saved)
        manifest["registrado_em"] = self.INSTANTE
        encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE embedding_runs SET record_json = ?, sha256_manifest = ?", (
                encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            ))
        self.assert_conflict(document_id, record["execucao_id"])

    def test_database_foreign_key_cannot_reassign_contextual_origin(self):
        document_id, contextualized = self.source()
        record = self.run_embedding(document_id)
        newer = self.earlier(document_id, "contextos", raio_anterior=0, raio_seguinte=0)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE embedding_runs SET context_execution_id = ?", (newer["execucao_id"],))
        self.assert_conflict(document_id, record["execucao_id"])
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE embedding_runs SET context_execution_id = ?", (contextualized["execucao_id"],))
        second_id = self.submit()
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE embedding_runs SET submission_id = ?", (second_id,))
        self.assertEqual(self.download(document_id, record["execucao_id"]).status_code, 404)
        self.assert_conflict(second_id, record["execucao_id"])

    def test_additive_schema_upgrade_keeps_all_earlier_records(self):
        document_id, contextualized = self.source()
        before = {table: self.rows(table) for table in self.ANTERIORES}
        with sqlite3.connect(self.database) as connection:
            for table in ("embedding_vectors", "embedding_run_artifacts", "embedding_artifacts", "embedding_runs"):
                connection.execute(f"DROP TABLE {table}")
        self.client = create_app(self.config).test_client()
        for table in self.ANTERIORES:
            self.assertEqual(self.rows(table), before[table])
        for table in self.VETORES:
            self.assertEqual(self.rows(table), [])
        self.assertEqual(self.run_embedding(document_id)["contexto"], contextualized)

    def test_late_batch_failure_has_diagnostic_and_no_partial_vectors(self):
        document_id, _contextualized = self.source()
        generate = self.generator.gerar
        calls = []

        def fail_second_batch(batch):
            calls.append(batch)
            if len(calls) == 2:
                raise app_module.ErroVetorizacaoInferencia("Falha controlada no segundo lote.")
            return generate(batch)

        with patch.object(self.generator, "gerar", side_effect=fail_second_batch):
            response = self.client.post(f"/envios/{document_id}/vetorizacoes", json={
                "execucao_id": "falha-observável", "configuracao": {"tamanho_lote": 1},
            })
        self.assertEqual(response.status_code, 503, response.get_data(as_text=True))
        diagnostic = response.get_json()
        self.assertEqual(len(calls), 2)
        self.assertEqual(diagnostic["estado"], "falhou")
        self.assertEqual(diagnostic["representacoes"], [])
        self.assertEqual(diagnostic["artefatos"], [])
        self.assertIn("Falha controlada", diagnostic["erro"]["mensagem"])
        self.assertEqual(len(self.rows("embedding_runs")), 1)
        for table in self.VETORES[1:]:
            self.assertEqual(self.rows(table), [])
        self.assertEqual(self.download(document_id, diagnostic["execucao_id"]).get_json(), diagnostic)
        self.assertEqual(self.download(document_id, diagnostic["execucao_id"], suffix="zip").status_code, 409)
        self.assertEqual(self.representation(document_id, diagnostic["execucao_id"], "qualquer").status_code, 409)
        retry = self.run_embedding(document_id, execucao_id="nova-tentativa")
        self.assertEqual(len(self.rows("embedding_runs")), 2)
        self.assertTrue(retry["representacoes"])
        self.assertEqual(self.download(document_id, diagnostic["execucao_id"]).get_json(), diagnostic)

    def test_failure_after_insert_rolls_back_all_vectors_and_associations(self):
        document_id, _contextualized = self.source()
        before = self.snapshot()
        save = app_module.salvar_execucao_vetorial

        def insert_then_fail(connection, record):
            save(connection, record)
            raise app_module.ErroPersistenciaVetores("Falha após a inserção dos vetores.")

        with patch("app.salvar_execucao_vetorial", side_effect=insert_then_fail):
            response = self.client.post(f"/envios/{document_id}/vetorizacoes", json={})
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertEqual(self.snapshot(), before)

    def test_origin_changed_during_generation_prevents_final_commit(self):
        document_id, contextualized = self.source()
        generate = self.generator.gerar
        changed = copy.deepcopy(contextualized)
        changed["registrado_em"] = self.INSTANTE
        mutated = []

        def mutate_origin_then_generate(batch):
            if not mutated:
                with sqlite3.connect(self.database) as connection:
                    connection.execute("UPDATE context_runs SET registered_at = ?, record_json = ? WHERE execution_id = ?", (
                        self.INSTANTE, json.dumps(changed, ensure_ascii=False), contextualized["execucao_id"],
                    ))
                mutated.append(True)
            return generate(batch)

        with patch.object(self.generator, "gerar", side_effect=mutate_origin_then_generate):
            response = self.client.post(f"/envios/{document_id}/vetorizacoes", json={})
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertIn("mudou", response.get_json()["erro"])
        for table in self.VETORES:
            self.assertEqual(self.rows(table), [])

    def test_request_configuration_controls_profiles_blocks_and_cache_identity(self):
        document_id, contextualized = self.source()
        passage = self.run_embedding(document_id)
        count = len(self.generator.entradas_geradas)
        query = self.run_embedding(document_id, configuracao={"perfil": "consulta"})
        self.assertEqual(query["configuracao"]["perfil"], "consulta")
        self.assertGreater(len(self.generator.entradas_geradas), count)
        self.assertTrue(all(item["entrada_modelo"]["texto"].startswith("query: ") for item in query["artefatos"]))
        self.assertNotEqual(passage["compatibilidade"], query["compatibilidade"])
        blocked = self.run_embedding(document_id, configuracao={"max_tokens": 16, "agregar": False})
        self.assertTrue(any(item["construcao"] == "blocos" for item in blocked["representacoes"]))
        self.assertEqual(blocked["contexto"], contextualized)
        self.assertEqual(blocked["representacoes"][-1]["texto"], self.ORIGINAL)
        for representation in blocked["representacoes"]:
            self.assertEqual("".join(block["texto"] for block in representation["blocos"]), representation["texto"])
            if representation["construcao"] == "blocos":
                self.assertIsNone(representation["vetor"])

    def test_invalid_configuration_types_are_rejected_without_inference(self):
        document_id, _contextualized = self.source()
        before = self.snapshot()
        configurations = ({"perfil": "desconhecido"}, {"max_tokens": 7}, {"max_tokens": True},
                          {"max_tokens": 16.0}, {"max_tokens": "16"}, {"tamanho_lote": 0},
                          {"tamanho_lote": False}, {"tamanho_lote": "2"},
                          {"agregar": "false"}, {"agregar": 1}, {"agregar": None})
        for configuration in configurations:
            with self.subTest(configuration=configuration):
                response = self.client.post(f"/envios/{document_id}/vetorizacoes", json={"configuracao": configuration})
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.factory_calls, [])

    def test_form_redirect_preserves_selected_context_and_configuration(self):
        document_id, contextualized = self.source()
        response = self.client.post(f"/envios/{document_id}/vetorizacoes", data={
            "contexto_execucao_id": contextualized["execucao_id"], "perfil": "similaridade",
            "max_tokens": "32", "tamanho_lote": "2", "agregar": "on",
        })
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        query = parse_qs(urlsplit(response.headers["Location"]).query)
        self.assertEqual(set(query), {"vetorizacao_execucao_id"})
        record = self.download(document_id, query["vetorizacao_execucao_id"][0]).get_json()
        self.assertEqual(record["contexto"], contextualized)
        for field, expected in (("perfil", "similaridade"), ("max_tokens", 32), ("tamanho_lote", 2), ("agregar", True)):
            self.assertEqual(record["configuracao"][field], expected)
        detail = self.client.get(response.headers["Location"])
        self.assertEqual(detail.status_code, 200, detail.get_data(as_text=True))
        html = detail.get_data(as_text=True)
        for label in ("Vetorização", "Modelo:", "Baixar vetorização JSON", "Baixar vetores ZIP",
                      "Embeddings simulados de teste", "Identidade lógica", "Hash dos bytes do vetor"):
            self.assertIn(label, html)

    def test_historical_detail_rejects_mixed_origin_selectors(self):
        document_id, contextualized = self.source()
        record = self.run_embedding(document_id)
        newer = self.earlier(document_id, "contextos", raio_anterior=0, raio_seguinte=0)
        fields = ("preparacao_id", "segmentacao_id", "anotacao_id", "analise_id", "execucao_id", "contexto_execucao_id")
        for field in fields:
            with self.subTest(field=field):
                response = self.client.get(f"/envios/{document_id}", query_string={
                    "vetorizacao_execucao_id": record["execucao_id"], field: "outra-origem",
                })
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        response = self.client.get(f"/envios/{document_id}", query_string={
            "vetorizacao_execucao_id": record["execucao_id"], "contexto_execucao_id": newer["execucao_id"],
        })
        self.assertEqual(response.status_code, 400)
        matching = {"vetorizacao_execucao_id": record["execucao_id"], "contexto_execucao_id": contextualized["execucao_id"]}
        self.assertEqual(self.client.get(f"/envios/{document_id}", query_string=matching).status_code, 200)

    def test_form_rejects_unknown_duplicate_and_malformed_fields(self):
        document_id, contextualized = self.source()
        base = {"contexto_execucao_id": contextualized["execucao_id"]}
        before = self.snapshot()
        forms = ({}, {"contexto_execucao_id": ""}, {**base, "execucao_id": "extra"},
                 {**base, "perfil": "desconhecido"}, {**base, "max_tokens": "7"},
                 {**base, "max_tokens": "1.0"}, {**base, "tamanho_lote": "0"},
                 {**base, "agregar": "false"},
                 MultiDict([("contexto_execucao_id", contextualized["execucao_id"])] * 2))
        for form in forms:
            with self.subTest(form=form):
                response = self.client.post(f"/envios/{document_id}/vetorizacoes", data=form)
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.factory_calls, [])


if __name__ == "__main__":
    unittest.main()
