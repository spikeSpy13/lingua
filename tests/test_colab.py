"""Pacotes portáveis para a etapa 09, sem baixar ou executar o modelo E5.

Os testes de execução utilizam exclusivamente o gerador simulado declarado
nas fixtures. Validar e extrair um pacote não pode importar seu código.
"""

import copy
import hashlib
import io
import inspect
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from contratos_vetorizacao import ErroConfiguracao, ErroEntrada, ErroLimite, hash_json
from fixtures_vetorizacao import GeradorSimulado, construir_contexto
from vetorizacao import validar_vetorizacao
import exportacao_colab as colab


def serializar(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def abrir_pacote(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def escrever_pacote(files, *, extra=None):
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
            if extra is not None:
                info, content = extra
                archive.writestr(info, content)
    return stream.getvalue()


def atualizar_hash(files, name, content):
    """Adulteração com novo hash declara corretamente os novos bytes."""
    files = dict(files)
    manifest = json.loads(files["manifesto.json"])
    files[name] = content
    manifest["arquivos"][name] = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    files["manifesto.json"] = serializar(manifest)
    return files


class ColabBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = construir_contexto(normalizar=True)
        cls.package = colab.exportar_pacote_colab(cls.context)
        cls.files = abrir_pacote(cls.package)

    def assert_rejected(self, data):
        with self.assertRaises(colab.ErroPacoteColab):
            colab.validar_pacote_colab(data)

    def test_archive_has_exact_source_and_verifiable_payload_bytes(self):
        manifest = colab.validar_pacote_colab(self.package)
        self.assertEqual(manifest["formato"], "lingua_etapa09_colab")
        self.assertEqual(manifest["versao"], "1.0.0")
        self.assertEqual(json.loads(self.files["contexto.json"]), self.context)
        self.assertEqual(manifest["origem"]["documento_id"], self.context["documento_id"])
        self.assertEqual(manifest["origem"]["contexto_execucao_id"], self.context["execucao_id"])
        self.assertEqual(manifest["origem"]["contexto_sha256"], hash_json(self.context))
        self.assertEqual(set(manifest["arquivos"]), set(self.files) - {"manifesto.json"})
        for name, content in self.files.items():
            self.assertNotIn("/", name)
            self.assertNotIn("\\", name)
            self.assertNotIn("fixtures", name)
            if name != "manifesto.json":
                self.assertEqual(manifest["arquivos"][name], {
                    "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                })
        for name in ("vetorizacao.py", "embeddings_e5.py", "unidades_contexto.py",
                     "exportacao_colab.py", "requirements-embeddings.txt", "LEIA-ME.txt"):
            self.assertIn(name, self.files)
        self.assertNotIn("app.py", self.files)
        self.assertNotIn("textos.sqlite3", self.files)
        self.assertNotIn("instance/embeddings_modelo.json", self.files)

    def test_notebook_is_valid_python_and_is_same_inside_zip(self):
        standalone = colab.exportar_notebook()
        self.assertEqual(standalone, self.files["lingua_etapa09_colab.ipynb"])
        notebook = json.loads(standalone)
        self.assertEqual(notebook["nbformat"], 4)
        self.assertGreaterEqual(len(notebook["cells"]), 4)
        code_count = 0
        for index, cell in enumerate(notebook["cells"]):
            if cell["cell_type"] == "code":
                source = cell["source"]
                source = "".join(source) if isinstance(source, list) else source
                compile(source, f"colab-cell-{index}", "exec")
                self.assertEqual(cell.get("outputs"), [])
                self.assertIsNone(cell.get("execution_count"))
                code_count += 1
        self.assertGreaterEqual(code_count, 4)
        code = "\n".join("".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"]
                         for cell in notebook["cells"] if cell["cell_type"] == "code")
        self.assertIn("intfloat/multilingual-e5-large", code)
        self.assertIn("cpu", code)
        self.assertIn("cuda", code)
        self.assertIn("files.upload", code)
        self.assertIn("files.download", code)

    def run_notebook_bootstrap(self, package, root, *, simulate=False):
        """Executa o notebook real com upload/download controlados, usando -S.

        -I -S elimina site-packages e o repositório do caminho de importação.
        Instalação e preparação do E5 são substituídas explicitamente pela
        fixture; as células de upload, configuração, inferência e download
        são as próprias células exportadas.
        """
        archive_path = Path(root) / "origem.zip"
        archive_path.write_bytes(package)
        generator_source = inspect.getsource(GeradorSimulado)
        script = """import copy, hashlib, json, math, sys, tempfile, types, zipfile
from pathlib import Path
root = Path(sys.argv[1]).parent
tempfile.tempdir = str(root)
google = types.ModuleType('google')
google_colab = types.ModuleType('google.colab')
downloaded = []
files = types.SimpleNamespace(
    upload=lambda: {'origem.zip': Path(sys.argv[1]).read_bytes()},
    download=lambda path: downloaded.append(str(path)),
)
google_colab.files = files
google.colab = google_colab
sys.modules['google'] = google
sys.modules['google.colab'] = google_colab
with zipfile.ZipFile(sys.argv[1]) as archive:
    notebook = json.loads(archive.read('lingua_etapa09_colab.ipynb'))
cells = [''.join(c['source']) if isinstance(c['source'], list) else c['source']
         for c in notebook['cells'] if c['cell_type'] == 'code']
namespace = {'__name__': '__main__'}
try:
    exec(compile(cells[0], 'notebook-upload', 'exec'), namespace)
except ValueError as error:
    print(json.dumps({'erro': str(error), 'extraido': any(p.is_dir() for p in root.iterdir())}))
    sys.exit(0)
if sys.argv[2] == 'simular':
    exec(compile(cells[1], 'notebook-config', 'exec'), namespace)
    namespace.update(REVISAO='a' * 40, deepcopy=copy.deepcopy, hashlib=hashlib, math=math)
    exec(sys.argv[3], namespace)
    namespace['gerador'] = namespace['GeradorSimulado']()
    exec(compile(cells[-2], 'notebook-inferencia', 'exec'), namespace)
    exec(compile(cells[-1], 'notebook-download', 'exec'), namespace)
    result = namespace['resultado']
    paths = [Path(path) for path in downloaded]
    print(json.dumps({'natureza': result['modelo']['natureza'],
                      'pronto': result['validacao']['pronto_para_uso'],
                      'contexto_execucao_id': result['contexto_execucao_id'],
                      'downloads': [path.name for path in paths],
                      'zips_validos': all(zipfile.is_zipfile(path) for path in paths),
                      'torch_importado': 'torch' in sys.modules,
                      'spacy_importado': 'spacy' in sys.modules}))
else:
    print(json.dumps({'origem': namespace['manifesto']['origem']}))
"""
        process = subprocess.run(
            [sys.executable, "-I", "-S", "-c", script, str(archive_path),
             "simular" if simulate else "validar", generator_source],
            cwd=root, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        return json.loads(process.stdout.splitlines()[-1])

    def test_notebook_real_cells_work_with_controlled_upload_and_simulated_inference(self):
        with tempfile.TemporaryDirectory() as root:
            result = self.run_notebook_bootstrap(self.package, root, simulate=True)
        self.assertEqual(result["natureza"], "simulado_teste")
        self.assertFalse(result["pronto"])
        self.assertEqual(result["contexto_execucao_id"], self.context["execucao_id"])
        self.assertEqual(result["downloads"], ["vetorizacao-colab.zip"])
        self.assertTrue(result["zips_validos"])
        self.assertFalse(result["torch_importado"])
        self.assertFalse(result["spacy_importado"])

    def test_notebook_rejects_changed_code_even_with_recomputed_manifest_hash(self):
        files = atualizar_hash(self.files, "vetorizacao.py", b"raise AssertionError('untrusted code ran')")
        with tempfile.TemporaryDirectory() as root:
            result = self.run_notebook_bootstrap(escrever_pacote(files), root)
        self.assertIn("Código diferente do notebook confiável", result["erro"])
        self.assertFalse(result["extraido"])

    def test_configuration_is_explicit_and_source_remains_unchanged(self):
        source = copy.deepcopy(self.context)
        config = {"perfil": "similaridade", "max_tokens": 128, "tamanho_lote": 2, "agregar": False}
        package = colab.exportar_pacote_colab(source, configuracao=config)
        self.assertEqual(json.loads(abrir_pacote(package)["configuracao.json"]), config)
        self.assertEqual(source, self.context)
        self.assertEqual(config, {"perfil": "similaridade", "max_tokens": 128, "tamanho_lote": 2, "agregar": False})
        default = json.loads(self.files["configuracao.json"])
        self.assertEqual(set(default), {"perfil", "max_tokens", "tamanho_lote", "agregar"})

    def test_export_rejects_invalid_configuration_and_context(self):
        for invalid in ({"gpu": True}, {"max_tokens": 0}, {"tamanho_lote": True}, []):
            with self.subTest(config=invalid), self.assertRaises(ErroConfiguracao):
                colab.exportar_pacote_colab(self.context, configuracao=invalid)
        changed = copy.deepcopy(self.context)
        changed["unidades"][0]["janela"]["texto"] += " adulteração"
        with self.assertRaises((ErroEntrada, ValueError)):
            colab.exportar_pacote_colab(changed)

    def test_corrupt_or_empty_zip_is_rejected(self):
        for data in (b"", b"not a zip", self.package[:40], self.package[:-30]):
            with self.subTest(size=len(data)):
                self.assert_rejected(data)

    def test_size_limits_reject_before_unpacking_or_partial_export(self):
        limits = (("MAX_BYTES_ZIP", len(self.package) - 1),
                  ("MAX_BYTES_ARQUIVO", max(map(len, self.files.values())) - 1),
                  ("MAX_BYTES_PACOTE", sum(map(len, self.files.values())) - 1))
        for name, limit in limits:
            with self.subTest(limit=name), patch.object(colab, name, limit):
                self.assert_rejected(self.package)
        before = copy.deepcopy(self.context)
        with patch.object(colab, "MAX_BYTES_ARQUIVO", 1), self.assertRaises(ErroLimite):
            colab.exportar_pacote_colab(self.context)
        self.assertEqual(self.context, before)

    def test_changed_payload_without_new_hash_is_rejected(self):
        for name in ("contexto.json", "configuracao.json", "vetorizacao.py", "lingua_etapa09_colab.ipynb"):
            files = dict(self.files)
            files[name] += b" "
            with self.subTest(name=name):
                self.assert_rejected(escrever_pacote(files))

    def test_missing_or_extra_members_are_rejected(self):
        files = dict(self.files)
        del files["contexto.json"]
        self.assert_rejected(escrever_pacote(files))
        files = dict(self.files)
        files["unexpected.py"] = b"raise AssertionError('do not execute')"
        self.assert_rejected(escrever_pacote(files))

    def test_duplicate_member_is_rejected(self):
        self.assert_rejected(escrever_pacote(self.files, extra=("contexto.json", self.files["contexto.json"])))

    def test_paths_and_symlinks_are_rejected(self):
        for name in ("../outside.py", "/tmp/outside.py", "pasta/arquivo.py", "pasta\\arquivo.py", "C:\\outside.py"):
            with self.subTest(name=name):
                self.assert_rejected(escrever_pacote(self.files, extra=(name, b"code")))
        link = zipfile.ZipInfo("contexto.json")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        files = dict(self.files)
        del files["contexto.json"]
        self.assert_rejected(escrever_pacote(files, extra=(link, b"/etc/passwd")))

    def test_manifest_must_describe_exact_member_sizes_hashes_and_origin(self):
        mutations = (
            lambda m: m["arquivos"]["contexto.json"].update(bytes=1),
            lambda m: m["arquivos"]["contexto.json"].update(sha256="0" * 64),
            lambda m: m["origem"].update(contexto_execucao_id="outra execução"),
            lambda m: m["origem"].update(documento_id="outro documento"),
            lambda m: m["origem"].update(contexto_sha256="0" * 64),
            lambda m: m.update(formato="outro formato"),
            lambda m: m.update(versao="999.0"),
        )
        for mutate in mutations:
            files = dict(self.files)
            manifest = json.loads(files["manifesto.json"])
            mutate(manifest)
            files["manifesto.json"] = serializar(manifest)
            with self.subTest(manifest=manifest["origem"]):
                self.assert_rejected(escrever_pacote(files))

    def test_invalid_json_or_duplicate_keys_are_rejected_even_with_new_hash(self):
        for name, payload in (("configuracao.json", b"{"), ("configuracao.json", b' {"perfil":"recuperacao","perfil":"similaridade"}'),
                              ("contexto.json", b"\xff"), ("contexto.json", b"[]")):
            files = atualizar_hash(self.files, name, payload)
            with self.subTest(name=name, payload=payload):
                self.assert_rejected(escrever_pacote(files))

    def test_source_semantics_are_validated_beyond_recalculated_hashes(self):
        changed = copy.deepcopy(self.context)
        changed["unidades"][0]["foco"]["texto"] += " substituição"
        files = atualizar_hash(self.files, "contexto.json", serializar(changed))
        manifest = json.loads(files["manifesto.json"])
        manifest["origem"]["contexto_sha256"] = hash_json(changed)
        files["manifesto.json"] = serializar(manifest)
        self.assert_rejected(escrever_pacote(files))

    def test_extraction_creates_only_validated_files_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "pacote"
            manifest = colab.extrair_pacote_colab(self.package, destination)
            self.assertEqual(manifest, colab.validar_pacote_colab(self.package))
            self.assertEqual({path.name for path in destination.iterdir()}, set(self.files))
            for name, content in self.files.items():
                self.assertEqual((destination / name).read_bytes(), content)
            with self.assertRaises(colab.ErroPacoteColab):
                colab.extrair_pacote_colab(self.package, destination)
            self.assertEqual((destination / "contexto.json").read_bytes(), self.files["contexto.json"])

    def test_invalid_archive_creates_no_destination_or_import_side_effects(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "pacote"
            files = dict(self.files)
            files["vetorizacao.py"] = b"raise AssertionError('embedded code was imported')"
            with self.assertRaises(colab.ErroPacoteColab):
                colab.extrair_pacote_colab(escrever_pacote(files), destination)
            self.assertFalse(destination.exists())

    def test_extracted_package_runs_using_explicitly_simulated_generator(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "pacote"
            colab.extrair_pacote_colab(self.package, destination)
            before = {name: (destination / name).read_bytes() for name in self.files}
            generator = GeradorSimulado()
            record = colab.executar_pacote_colab(destination, gerador=generator)
            report = validar_vetorizacao(record)
            self.assertEqual(record["contexto"], self.context)
            paragraphs = [r for r in record["representacoes"] if r["tipo"] == "paragrafo"]
            source = self.context["regras"]["analise"]["anotacao"]["segmentacao"]["paragrafos"]
            self.assertEqual([r["paragrafo_id"] for r in paragraphs], [p["id"] for p in source])
            self.assertEqual([r["texto"] for r in paragraphs], [p["texto"] for p in source])
            self.assertTrue(all(r["vetor"] is not None for r in paragraphs))
            self.assertEqual(record["contexto_execucao_id"], self.context["execucao_id"])
            self.assertEqual(record["modelo"]["natureza"], "simulado_teste")
            self.assertFalse(report["pronto_para_uso"])
            self.assertTrue(generator.chamadas_geracao)
            for name, content in before.items():
                self.assertEqual((destination / name).read_bytes(), content)
            exported = colab.exportar_resultado_colab(record)
            self.assertTrue(zipfile.is_zipfile(io.BytesIO(exported)))
            from importacao_vetores import ler_resultado_zip
            self.assertEqual(ler_resultado_zip(exported, permitir_simulado=True), record)
            result_files = abrir_pacote(exported)
            records = [json.loads(payload) for name, payload in result_files.items() if name.endswith(".json")]
            self.assertIn(record, records)

    def test_execution_revalidates_extracted_files_before_loading_generator(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "pacote"
            colab.extrair_pacote_colab(self.package, destination)
            path = destination / "contexto.json"
            path.write_bytes(path.read_bytes() + b" ")
            generator = GeradorSimulado()
            with self.assertRaises(colab.ErroPacoteColab):
                colab.executar_pacote_colab(destination, gerador=generator)
            self.assertEqual(generator.chamadas_tokenizacao, [])
            self.assertEqual(generator.chamadas_geracao, [])

    def test_result_export_rejects_corrupt_vector_and_keeps_input_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "pacote"
            colab.extrair_pacote_colab(self.package, destination)
            record = colab.executar_pacote_colab(destination, gerador=GeradorSimulado())
        changed = copy.deepcopy(record)
        changed["artefatos"][0]["armazenamento"]["sha256_bytes"] = "0" * 64
        before = copy.deepcopy(changed)
        with self.assertRaises((ErroEntrada, ValueError)):
            colab.exportar_resultado_colab(changed)
        self.assertEqual(changed, before)


if __name__ == "__main__":
    unittest.main()
