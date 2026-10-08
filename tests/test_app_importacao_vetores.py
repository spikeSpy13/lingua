"""Importação do resultado 09 vinculado à origem local, sem inferência."""

import hashlib
import html
import io
import re
import sqlite3
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import zipfile

from werkzeug.datastructures import MultiDict

import app as app_module
from app import create_app
from fixtures_vetorizacao import GeradorSimulado
from persistencia_vetores import ErroPersistenciaVetores, exportar_zip
import test_app_vetorizacao as integration_fixture
from unidades_contexto import construir_unidades_contexto
from vetorizacao import ErroInferencia, vetorizar_unidades_contexto


class ImportacaoVetoresIntegrationTests(unittest.TestCase):
    # Reutiliza somente preparação da origem e observação do SQLite.
    # Não herda casos de teste da integração de inferência.
    INSTANTE = integration_fixture.EmbeddingIntegrationTests.INSTANTE
    ANTERIORES = integration_fixture.EmbeddingIntegrationTests.ANTERIORES
    VETORES = integration_fixture.EmbeddingIntegrationTests.VETORES
    ORIGINAL = integration_fixture.EmbeddingIntegrationTests.ORIGINAL
    setUp = integration_fixture.EmbeddingIntegrationTests.setUp
    rows = integration_fixture.EmbeddingIntegrationTests.rows
    submit = integration_fixture.EmbeddingIntegrationTests.submit
    earlier = integration_fixture.EmbeddingIntegrationTests.earlier
    source = integration_fixture.EmbeddingIntegrationTests.source
    download = integration_fixture.EmbeddingIntegrationTests.download

    def snapshot(self):
        result = {table: self.rows(table) for table in self.ANTERIORES + self.VETORES}
        with sqlite3.connect(self.database) as connection:
            result["embedding_imports"] = connection.execute("SELECT * FROM embedding_imports ORDER BY execution_id").fetchall()
        return result

    def external_result(self, context, *, execution_id="resultado-colab", **options):
        return vetorizar_unidades_contexto(
            context, gerador=GeradorSimulado(), execucao_id=execution_id,
            registrado_em=self.INSTANTE, **options,
        )

    def import_result(self, document_id, context_id, package, *, form=False, client=None):
        endpoint = f"/envios/{document_id}/vetorizacoes/importar"
        active_client = client or self.client
        if form:
            return active_client.post(endpoint, data={
                "contexto_execucao_id": context_id,
                "arquivo": (io.BytesIO(package), "vetorizacao-colab.zip"),
            })
        return active_client.post(
            endpoint, query_string={"contexto_execucao_id": context_id},
            data=package, content_type="application/zip",
        )

    @staticmethod
    def repack(package, *, padding=0):
        result = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(package)) as source:
            with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_STORED) as target:
                for name in source.namelist():
                    content = source.read(name)
                    if name == "manifesto.json":
                        content += b" " * padding
                    target.writestr(name, content)
        return result.getvalue()

    def test_api_import_preserves_entire_record_origin_and_provenance_without_inference(self):
        document_id, context = self.source(normalizar_crlf="on")
        record = self.external_result(context)
        package = exportar_zip(record)
        before = self.snapshot()
        with patch("app.criar_gerador_padrao") as default_factory, patch("app.vetorizar_unidades_contexto") as inference, patch("app.anotar_segmentacao") as annotation, patch("app.construir_unidades_contexto") as contextualize:
            response = self.import_result(document_id, context["execucao_id"], package)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertEqual(response.get_json(), {
            "importada": True, "vetorizacao_execucao_id": record["execucao_id"],
            "contexto_execucao_id": context["execucao_id"], "documento_id": document_id,
        })
        self.assertEqual(self.factory_calls, [])
        for mocked in (default_factory, inference, annotation, contextualize):
            mocked.assert_not_called()
        for table in self.ANTERIORES:
            self.assertEqual(self.rows(table), before[table])
        self.assertEqual(self.download(document_id, record["execucao_id"]).get_json(), record)
        metadata = self.snapshot()["embedding_imports"]
        self.assertEqual(len(metadata), 1)
        self.assertEqual(metadata[0][0], record["execucao_id"])
        self.assertEqual(metadata[0][1], hashlib.sha256(package).hexdigest())
        self.assertEqual(metadata[0][3], "lingua_etapa09_zip")
        self.assertNotEqual(metadata[0][2], record["registrado_em"])

    def test_form_redirect_restart_and_old_context_preserve_exact_historical_origin(self):
        document_id, old_context = self.source()
        record = self.external_result(old_context)
        self.earlier(document_id, "contextos", raio_anterior=0, raio_seguinte=0)
        self.earlier(document_id, "preparacoes", normalizar_crlf=True)
        config = {**self.config, "EMBEDDING_GENERATOR_FACTORY": lambda _: self.fail("Importação e leitura não carregam o modelo")}
        restarted = create_app(config).test_client()
        response = self.import_result(document_id, old_context["execucao_id"], exportar_zip(record), form=True, client=restarted)
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        query = parse_qs(urlsplit(response.headers["Location"]).query)
        self.assertEqual(query, {"aba": ["vetorizacao"], "vetorizacao_execucao_id": [record["execucao_id"]]})
        self.assertEqual(self.download(document_id, record["execucao_id"], client=restarted).get_json(), record)
        detail = restarted.get(response.headers["Location"])
        self.assertEqual(detail.status_code, 200, detail.get_data(as_text=True))
        self.assertIn(old_context["execucao_id"], detail.get_data(as_text=True))
        second = self.import_result(document_id, old_context["execucao_id"], exportar_zip(record), form=True, client=restarted)
        self.assertEqual(second.status_code, 303)
        self.assertEqual(second.headers["Location"], response.headers["Location"])

    def test_idempotence_compares_records_not_archive_bytes_and_keeps_first_metadata(self):
        document_id, context = self.source()
        record = self.external_result(context)
        package = exportar_zip(record)
        self.assertEqual(self.import_result(document_id, context["execucao_id"], package).status_code, 201)
        before = self.snapshot()
        repacked = self.repack(package)
        self.assertNotEqual(repacked, package)
        response = self.import_result(document_id, context["execucao_id"], repacked)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertFalse(response.get_json()["importada"])
        self.assertEqual(self.snapshot(), before)

    def test_same_execution_id_with_different_valid_content_is_a_conflict(self):
        document_id, context = self.source()
        first = self.external_result(context)
        second = self.external_result(context, configuracao={"agregar": False})
        self.assertEqual(self.import_result(document_id, context["execucao_id"], exportar_zip(first)).status_code, 201)
        before = self.snapshot()
        response = self.import_result(document_id, context["execucao_id"], exportar_zip(second))
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertEqual(self.snapshot(), before)

    def test_existing_execution_id_on_other_document_or_failed_run_is_never_overwritten(self):
        first_id, first_context = self.source()
        second_id, second_context = self.source()
        first = self.external_result(first_context)
        second = self.external_result(second_context)
        self.assertEqual(self.import_result(first_id, first_context["execucao_id"], exportar_zip(first)).status_code, 201)
        before = self.snapshot()
        self.assertEqual(self.import_result(second_id, second_context["execucao_id"], exportar_zip(second)).status_code, 409)
        self.assertEqual(self.snapshot(), before)
        with patch.dict(self.application.config, {"EMBEDDING_CACHE_MAX_ARTIFACTS": 0}), patch.object(self.generator, "gerar", side_effect=ErroInferencia("Falha controlada")):
            failed = self.client.post(f"/envios/{second_id}/vetorizacoes", json={"execucao_id": "execucao-falhou"})
        self.assertEqual(failed.status_code, 503)
        record = self.external_result(second_context, execution_id="execucao-falhou")
        before = self.snapshot()
        self.assertEqual(self.import_result(second_id, second_context["execucao_id"], exportar_zip(record)).status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_document_and_selected_context_must_match_zip(self):
        document_id, context = self.source()
        other_id, other_context = self.source()
        package = exportar_zip(self.external_result(context))
        before = self.snapshot()
        self.assertEqual(self.import_result(other_id, context["execucao_id"], package).status_code, 409)
        self.assertEqual(self.import_result(document_id, other_context["execucao_id"], package).status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_valid_coherent_context_with_same_id_but_different_selection_is_rejected(self):
        document_id, context = self.source()
        modified = construir_unidades_contexto(
            context["regras"], execucao_id=context["execucao_id"],
            registrado_em=context["registrado_em"], raio_anterior=0, raio_seguinte=0,
        )
        self.assertNotEqual(modified, context)
        record = self.external_result(modified)
        before = self.snapshot()
        response = self.import_result(document_id, context["execucao_id"], exportar_zip(record))
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertEqual(self.snapshot(), before)

    def test_missing_source_returns_404_and_never_restores_previous_stages(self):
        document_id, context = self.source()
        package = exportar_zip(self.external_result(context))
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM context_runs WHERE execution_id = ?", (context["execucao_id"],))
        before = self.snapshot()
        response = self.import_result(document_id, context["execucao_id"], package)
        self.assertEqual(response.status_code, 404, response.get_data(as_text=True))
        self.assertEqual(self.snapshot(), before)
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM submissions WHERE id = ?", (document_id,))
        before = self.snapshot()
        self.assertEqual(self.import_result(document_id, context["execucao_id"], package).status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_corrupted_stored_source_is_rejected_without_vector_writes(self):
        document_id, context = self.source()
        package = exportar_zip(self.external_result(context))
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE context_runs SET record_json = '{}' WHERE execution_id = ?", (context["execucao_id"],))
        before = self.snapshot()
        response = self.import_result(document_id, context["execucao_id"], package)
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertEqual(self.snapshot(), before)

    def test_invalid_archives_and_request_contracts_write_nothing(self):
        document_id, context = self.source()
        endpoint = f"/envios/{document_id}/vetorizacoes/importar"
        package = exportar_zip(self.external_result(context))
        before = self.snapshot()
        responses = [
            self.import_result(document_id, context["execucao_id"], b"nao eh ZIP"),
            self.client.post(endpoint, json={"contexto_execucao_id": context["execucao_id"]}),
            self.client.post(endpoint, data=package, content_type="application/zip"),
            self.client.post(endpoint, query_string=MultiDict([("contexto_execucao_id", context["execucao_id"]), ("contexto_execucao_id", context["execucao_id"])]), data=package, content_type="application/zip"),
            self.client.post(endpoint, query_string={"contexto_execucao_id": context["execucao_id"], "extra": "x"}, data=package, content_type="application/zip"),
            self.client.post(endpoint, query_string={"contexto_execucao_id": " "}, data=package, content_type="application/zip"),
            self.client.post(endpoint, data={"contexto_execucao_id": context["execucao_id"]}),
            self.client.post(endpoint, data=MultiDict([("contexto_execucao_id", context["execucao_id"]), ("arquivo", (io.BytesIO(package), "um.zip")), ("arquivo", (io.BytesIO(package), "dois.zip"))])),
            self.client.post(endpoint, data=MultiDict([("contexto_execucao_id", context["execucao_id"]), ("contexto_execucao_id", context["execucao_id"]), ("arquivo", (io.BytesIO(package), "um.zip"))])),
        ]
        for response in responses:
            self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.factory_calls, [])

    def test_form_error_is_readable_html_with_safe_return_link(self):
        document_id, context = self.source()
        response = self.import_result(document_id, context["execucao_id"], b"ZIP invalido", form=True)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.mimetype, "text/html")
        self.assertIn(f"/envios/{document_id}?aba=vetorizacao", response.get_data(as_text=True))
        self.assertEqual(self.rows("embedding_runs"), [])

    def test_invalid_zip_return_link_preserves_old_context_after_newer_execution(self):
        document_id, old_context = self.source()
        new_context = self.earlier(document_id, "contextos", raio_anterior=0, raio_seguinte=0)
        self.assertNotEqual(old_context["execucao_id"], new_context["execucao_id"])
        before = self.snapshot()
        response = self.import_result(document_id, old_context["execucao_id"], b"ZIP invalido", form=True)
        self.assertEqual(response.status_code, 400)
        match = re.search(r'<a class="workflow-previous" href="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match)
        return_url = html.unescape(match.group(1))
        self.assertEqual(parse_qs(urlsplit(return_url).query), {
            "aba": ["vetorizacao"], "contexto_execucao_id": [old_context["execucao_id"]],
        })
        detail = self.client.get(return_url)
        self.assertEqual(detail.status_code, 200)
        self.assertIn(f'name="contexto_execucao_id" value="{old_context["execucao_id"]}"', detail.get_data(as_text=True))
        self.assertEqual(self.snapshot(), before)

    def test_zip_larger_than_text_limit_is_accepted_and_text_limit_stays_two_mib(self):
        document_id, context = self.source()
        package = self.repack(exportar_zip(self.external_result(context)), padding=2 * 1024 * 1024)
        self.assertGreater(len(package), 2 * 1024 * 1024)
        response = self.import_result(document_id, context["execucao_id"], package, form=True)
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        before = self.snapshot()
        text = self.client.post("/envios", data={"content": "x" * (2 * 1024 * 1024)})
        self.assertEqual(text.status_code, 413)
        self.assertEqual(self.snapshot(), before)

    def test_request_upload_limit_has_import_message_and_atomic_no_writes(self):
        document_id, context = self.source()
        package = exportar_zip(self.external_result(context))
        before = self.snapshot()
        restricted = create_app({**self.config, "IMPORT_MAX_CONTENT_LENGTH": 128}).test_client()
        for form in (False, True):
            response = self.import_result(document_id, context["execucao_id"], package, form=form, client=restricted)
            self.assertEqual(response.status_code, 413)
            self.assertIn("64 MiB", response.get_data(as_text=True))
            self.assertNotIn("texto menor", response.get_data(as_text=True))
        self.assertEqual(self.snapshot(), before)

    def test_persistence_failure_rolls_back_run_blobs_and_import_metadata(self):
        document_id, context = self.source()
        record = self.external_result(context)
        before = self.snapshot()
        save = app_module.salvar_execucao_vetorial

        def fail_after_save(connection, value):
            save(connection, value)
            raise ErroPersistenciaVetores("Falha controlada depois dos BLOBs")

        with patch("app.salvar_execucao_vetorial", side_effect=fail_after_save):
            response = self.import_result(document_id, context["execucao_id"], exportar_zip(record))
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertEqual(self.snapshot(), before)

    def test_source_changed_before_short_write_transaction_is_rejected(self):
        document_id, context = self.source()
        package = exportar_zip(self.external_result(context))
        original_snapshot = app_module.context_origin_snapshot
        calls = []

        def snapshot_after_concurrent_change(connection, document_id, contextualized):
            calls.append(True)
            if len(calls) == 2:
                connection.execute("UPDATE submissions SET content = content || ' modificado' WHERE id = ?", (document_id,))
            return original_snapshot(connection, document_id, contextualized)

        before = self.snapshot()
        with patch("app.context_origin_snapshot", side_effect=snapshot_after_concurrent_change):
            response = self.import_result(document_id, context["execucao_id"], package)
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.snapshot(), before)

    def test_simulated_result_is_rejected_outside_testing_without_loading_model(self):
        document_id, context = self.source()
        record = self.external_result(context)
        production = create_app({"DATABASE": str(self.database)}).test_client()
        before = self.snapshot()
        with patch("app.criar_gerador_padrao") as factory, patch("app.vetorizar_unidades_contexto") as inference:
            response = self.import_result(document_id, context["execucao_id"], exportar_zip(record), client=production)
        self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        factory.assert_not_called()
        inference.assert_not_called()
        self.assertEqual(self.snapshot(), before)
