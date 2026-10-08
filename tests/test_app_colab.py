"""Downloads para o Colab preservam a execução contextual escolhida.

O download não carrega o E5, não executa inferência e não altera o banco.
"""

import copy
import io
import json
import sqlite3
import unittest
from unittest.mock import patch
import zipfile

from werkzeug.datastructures import MultiDict

import test_app_vetorizacao as embedding_helpers
from app import create_app
from contratos_vetorizacao import hash_json
from exportacao_colab import validar_pacote_colab


class ColabDownloadIntegrationTests(unittest.TestCase):
    # Reutilizamos somente a montagem da origem; não herdamos os testes da 09.
    helper = embedding_helpers.EmbeddingIntegrationTests
    INSTANTE = helper.INSTANTE
    ANTERIORES = helper.ANTERIORES
    VETORES = helper.VETORES
    ORIGINAL = helper.ORIGINAL
    setUp = helper.setUp
    rows = helper.rows
    snapshot = helper.snapshot
    submit = helper.submit
    earlier = helper.earlier
    source = helper.source

    def export(self, document_id, execution_id=None, *, suffix="zip", client=None, query=None):
        selector = {} if execution_id is None else {"contexto_execucao_id": execution_id}
        return (client or self.client).get(
            f"/envios/{document_id}/colab.{suffix}",
            query_string=selector if query is None else query,
        )

    def test_exact_origin_downloads_need_no_model_and_write_nothing(self):
        document_id, contextualized = self.source(normalizar_crlf="on")
        before = self.snapshot()
        with patch("app.anotar_segmentacao") as morphology, patch("app.analisar_sintaxe_entidades") as syntax, patch("app.aplicar_regras_linguisticas") as rules, patch("app.construir_unidades_contexto") as contexts:
            package = self.export(document_id, contextualized["execucao_id"])
            notebook = self.export(document_id, contextualized["execucao_id"], suffix="ipynb")
        for mocked in (morphology, syntax, rules, contexts):
            mocked.assert_not_called()
        self.assertEqual(package.status_code, 200, package.get_data(as_text=True) if package.status_code != 200 else "")
        self.assertEqual(package.mimetype, "application/zip")
        self.assertIn("attachment", package.headers["Content-Disposition"])
        self.assertEqual(validar_pacote_colab(package.data)["origem"]["documento_id"], document_id)
        self.assertEqual(notebook.status_code, 200)
        self.assertEqual(notebook.mimetype, "application/x-ipynb+json")
        self.assertIn("attachment", notebook.headers["Content-Disposition"])
        self.assertEqual(json.loads(notebook.data)["nbformat"], 4)
        with zipfile.ZipFile(io.BytesIO(package.data)) as archive:
            origin = json.loads(archive.read("contexto.json"))
            manifest = json.loads(archive.read("manifesto.json"))
            self.assertEqual(origin, contextualized)
            self.assertEqual(manifest["origem"]["documento_id"], document_id)
            self.assertEqual(manifest["origem"]["contexto_execucao_id"], contextualized["execucao_id"])
            self.assertEqual(manifest["origem"]["contexto_sha256"], hash_json(contextualized))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.factory_calls, [])
        self.assertEqual(self.generator.chamadas_geracao, [])

    def test_history_survives_new_preparation_and_restart(self):
        document_id, first = self.source()
        second = self.earlier(document_id, "contextos", raio_anterior=0, raio_seguinte=0)
        self.assertNotEqual(first["execucao_id"], second["execucao_id"])
        self.earlier(document_id, "preparacoes", normalizar_crlf=True)
        self.earlier(document_id, "segmentacoes")
        restarted = create_app({**self.config, "EMBEDDING_GENERATOR_FACTORY": lambda _: self.fail("Exportação não pode carregar o E5.")}).test_client()
        for contextualized in (first, second):
            with self.subTest(execution_id=contextualized["execucao_id"]):
                response = self.export(document_id, contextualized["execucao_id"], client=restarted)
                self.assertEqual(response.status_code, 200)
                with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                    self.assertEqual(json.loads(archive.read("contexto.json")), contextualized)
        self.assertEqual(self.factory_calls, [])

    def test_selector_required_and_strict(self):
        document_id, contextualized = self.source()
        execution_id = contextualized["execucao_id"]
        invalid = ({}, {"contexto_execucao_id": ""}, {"contexto_execucao_id": " "},
                   {"contexto_execucao_id": execution_id, "extra": "x"},
                   {"vetorizacao_execucao_id": execution_id},
                   MultiDict([("contexto_execucao_id", execution_id)] * 2))
        before = self.snapshot()
        for suffix in ("zip", "ipynb"):
            for query in invalid:
                with self.subTest(suffix=suffix, query=query):
                    response = self.export(document_id, suffix=suffix, query=query)
                    self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
                    self.assertIn("erro", response.get_json())
        self.assertEqual(self.snapshot(), before)

    def test_missing_document_context_and_foreign_origin_return_404(self):
        first_id = self.submit()
        second_id, contextualized = self.source()
        before = self.snapshot()
        for suffix in ("zip", "ipynb"):
            for document_id, execution_id in ((999, "qualquer"), (first_id, "ausente"),
                                               (first_id, contextualized["execucao_id"]),
                                               (second_id, "não-existe")):
                with self.subTest(suffix=suffix, document_id=document_id, execution_id=execution_id):
                    response = self.export(document_id, execution_id, suffix=suffix)
                    self.assertEqual(response.status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_tampered_context_is_rejected_before_export(self):
        document_id, contextualized = self.source()
        changed = copy.deepcopy(contextualized)
        changed["unidades"][0]["foco"]["texto"] += " adulterado"
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE context_runs SET record_json = ? WHERE execution_id = ?", (
                json.dumps(changed, ensure_ascii=False), contextualized["execucao_id"],
            ))
        before = self.snapshot()
        with patch("app.exportar_notebook") as notebook, patch("app.exportar_pacote_colab") as package:
            for suffix in ("zip", "ipynb"):
                response = self.export(document_id, contextualized["execucao_id"], suffix=suffix)
                self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
                self.assertIn("erro", response.get_json())
        notebook.assert_not_called()
        package.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_tampered_ancestor_is_rejected_even_if_context_row_is_intact(self):
        document_id, contextualized = self.source()
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE submissions SET content = ? WHERE id = ?", ("Texto substituído.", document_id))
        for suffix in ("zip", "ipynb"):
            response = self.export(document_id, contextualized["execucao_id"], suffix=suffix)
            self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertEqual(self.factory_calls, [])

    def test_interface_links_select_historical_context_before_embedding(self):
        document_id, first = self.source()
        second = self.earlier(document_id, "contextos", raio_anterior=0, raio_seguinte=0)
        from urllib.parse import parse_qs, urlsplit
        import html
        import re
        response = self.client.get(f"/envios/{document_id}", query_string={"contexto_execucao_id": first["execucao_id"]})
        self.assertEqual(response.status_code, 200)
        links = [html.unescape(link) for link in re.findall(r'href="([^"]+)"', response.get_data(as_text=True))]
        exports = {urlsplit(link).path.rsplit("/", 1)[-1]: parse_qs(urlsplit(link).query)
                   for link in links if urlsplit(link).path.endswith(("/colab.zip", "/colab.ipynb"))}
        self.assertEqual(set(exports), {"colab.zip", "colab.ipynb"})
        for query in exports.values():
            self.assertEqual(query, {"contexto_execucao_id": [first["execucao_id"]]})
            self.assertNotIn(second["execucao_id"], repr(query))
        self.assertEqual(self.rows("embedding_runs"), [])
        self.assertEqual(self.factory_calls, [])


if __name__ == "__main__":
    unittest.main()
