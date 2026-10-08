"""SQLite e exportação de vetores; nenhuma inferência ocorre neste módulo.

O manifesto conserva o contrato JSON da etapa 09, separando somente os bytes
base64 em BLOBs. A leitura reconstitui o registro antes de sua validação.
"""

import base64
import copy
from hashlib import sha256
from io import BytesIO
import json
from zipfile import ZIP_DEFLATED, ZipFile


class ErroPersistenciaVetores(ValueError):
    """O manifesto ou seus BLOBs não correspondem ao registro armazenado."""


def json_canonico(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _metadados_fisicos(artifact):
    # A origem do cálculo pertence à associação desta execução. O mesmo BLOB
    # pode ter sido gerado em uma execução e reutilizado em outra.
    return json_canonico({key: value for key, value in artifact.items() if key != "origem_calculo"})


def criar_tabelas(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS embedding_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        execution_id TEXT NOT NULL UNIQUE,
        context_execution_id TEXT NOT NULL REFERENCES context_runs(execution_id),
        submission_id INTEGER NOT NULL REFERENCES submissions(id),
        registered_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('concluida', 'falhou')),
        record_json TEXT NOT NULL,
        sha256_manifest TEXT NOT NULL
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS embedding_runs_context ON embedding_runs (submission_id, context_execution_id, id)")
    connection.execute("""CREATE TABLE IF NOT EXISTS embedding_artifacts (
        artifact_id TEXT PRIMARY KEY,
        logical_artifact_id TEXT NOT NULL,
        metadata_json TEXT NOT NULL,
        vector_bytes BLOB NOT NULL,
        sha256_bytes TEXT NOT NULL
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS embedding_artifacts_logical ON embedding_artifacts (logical_artifact_id)")
    connection.execute("""CREATE TABLE IF NOT EXISTS embedding_run_artifacts (
        execution_id TEXT NOT NULL REFERENCES embedding_runs(execution_id),
        artifact_id TEXT NOT NULL REFERENCES embedding_artifacts(artifact_id),
        PRIMARY KEY (execution_id, artifact_id)
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS embedding_vectors (
        execution_id TEXT NOT NULL REFERENCES embedding_runs(execution_id),
        representation_id TEXT NOT NULL,
        vector_bytes BLOB NOT NULL,
        sha256_bytes TEXT NOT NULL,
        PRIMARY KEY (execution_id, representation_id)
    )""")


def _bytes(storage):
    try:
        raw = base64.b64decode(storage["base64"], validate=True)
    except (ValueError, KeyError, TypeError) as error:
        raise ErroPersistenciaVetores("Vetor base64 inválido.") from error
    if len(raw) != storage["bytes"] or sha256(raw).hexdigest() != storage["sha256_bytes"]:
        raise ErroPersistenciaVetores("Os bytes do vetor não correspondem à sua dimensão ou hash.")
    return raw


def _restaurar(storage, raw, checksum):
    raw = bytes(raw)
    digest = sha256(raw).hexdigest()
    if digest != checksum or digest != storage["sha256_bytes"] or len(raw) != storage["bytes"]:
        raise ErroPersistenciaVetores("BLOB de vetor adulterado ou incompleto.")
    storage["base64"] = base64.b64encode(raw).decode("ascii")


def salvar_execucao(connection, registro):
    """Persiste atomicamente dentro da transação curta escolhida pelo chamador."""
    manifest = copy.deepcopy(registro)
    artifacts = []
    vectors = []
    for artifact in manifest["artefatos"]:
        raw = _bytes(artifact["armazenamento"])
        artifact["armazenamento"].pop("base64")
        artifacts.append((artifact, raw))
    for representation in manifest["representacoes"]:
        if representation["vetor"] is not None:
            raw = _bytes(representation["vetor"])
            representation["vetor"].pop("base64")
            vectors.append((representation, raw))
    encoded = json_canonico(manifest)
    connection.execute("""INSERT INTO embedding_runs
        (execution_id, context_execution_id, submission_id, registered_at, status, record_json, sha256_manifest)
        VALUES (?, ?, ?, ?, 'concluida', ?, ?)""", (
        registro["execucao_id"], registro["contexto_execucao_id"], registro["documento_id"],
        registro["registrado_em"], encoded, sha256(encoded.encode("utf-8")).hexdigest(),
    ))
    for artifact, raw in artifacts:
        metadata = _metadados_fisicos(artifact)
        physical_id = artifact["id"] + ":" + artifact["armazenamento"]["sha256_bytes"]
        existing = connection.execute("SELECT * FROM embedding_artifacts WHERE artifact_id = ?", (physical_id,)).fetchone()
        if existing is None:
            connection.execute("INSERT INTO embedding_artifacts VALUES (?, ?, ?, ?, ?)", (
                physical_id, artifact["id"], metadata, raw, artifact["armazenamento"]["sha256_bytes"],
            ))
        elif existing["logical_artifact_id"] != artifact["id"] or existing["metadata_json"] != metadata or bytes(existing["vector_bytes"]) != raw or existing["sha256_bytes"] != artifact["armazenamento"]["sha256_bytes"]:
            raise ErroPersistenciaVetores("Um artefato já armazenado possui conteúdo diferente para a mesma identidade.")
        connection.execute("INSERT INTO embedding_run_artifacts VALUES (?, ?)", (registro["execucao_id"], physical_id))
    for representation, raw in vectors:
        connection.execute("INSERT INTO embedding_vectors VALUES (?, ?, ?, ?)", (
            registro["execucao_id"], representation["id"], raw, representation["vetor"]["sha256_bytes"],
        ))


def salvar_falha(connection, registro):
    encoded = json_canonico(registro)
    connection.execute("""INSERT INTO embedding_runs
        (execution_id, context_execution_id, submission_id, registered_at, status, record_json, sha256_manifest)
        VALUES (?, ?, ?, ?, 'falhou', ?, ?)""", (
        registro["execucao_id"], registro["contexto_execucao_id"], registro["documento_id"],
        registro["registrado_em"], encoded, sha256(encoded.encode("utf-8")).hexdigest(),
    ))


def ler_execucao(connection, row):
    try:
        encoded = row["record_json"]
        if sha256(encoded.encode("utf-8")).hexdigest() != row["sha256_manifest"]:
            raise ErroPersistenciaVetores("O manifesto da vetorização foi adulterado.")
        record = json.loads(encoded)
        if row["status"] == "falhou":
            if record.get("estado") != "falhou" or record.get("representacoes") != [] or record.get("artefatos") != []:
                raise ErroPersistenciaVetores("O diagnóstico da execução com falha é inválido.")
            return record
        artifact_rows = connection.execute("""SELECT a.* FROM embedding_artifacts a
            JOIN embedding_run_artifacts r ON r.artifact_id = a.artifact_id
            WHERE r.execution_id = ?""", (row["execution_id"],)).fetchall()
        artifacts = {item["logical_artifact_id"]: item for item in artifact_rows}
        if len(artifacts) != len(artifact_rows) or set(artifacts) != {item["id"] for item in record["artefatos"]}:
            raise ErroPersistenciaVetores("As associações dos artefatos não correspondem ao manifesto.")
        for artifact in record["artefatos"]:
            item = artifacts[artifact["id"]]
            physical_id = artifact["id"] + ":" + artifact["armazenamento"]["sha256_bytes"]
            if item["artifact_id"] != physical_id or _metadados_fisicos(artifact) != item["metadata_json"]:
                raise ErroPersistenciaVetores("Os metadados do artefato não correspondem ao manifesto.")
            _restaurar(artifact["armazenamento"], item["vector_bytes"], item["sha256_bytes"])
        vector_rows = connection.execute("SELECT * FROM embedding_vectors WHERE execution_id = ?", (row["execution_id"],)).fetchall()
        vectors = {item["representation_id"]: item for item in vector_rows}
        if set(vectors) != {item["id"] for item in record["representacoes"] if item["vetor"] is not None}:
            raise ErroPersistenciaVetores("As associações dos vetores não correspondem ao manifesto.")
        for representation in record["representacoes"]:
            if representation["vetor"] is not None:
                item = vectors[representation["id"]]
                _restaurar(representation["vetor"], item["vector_bytes"], item["sha256_bytes"])
        return record
    except (ValueError, KeyError, TypeError) as error:
        if isinstance(error, ErroPersistenciaVetores):
            raise
        raise ErroPersistenciaVetores("O registro vetorial armazenado é inválido.") from error


def carregar_cache(connection, *, max_artefatos=2000, max_bytes=32 * 1024 * 1024):
    """Carrega um cache opcional limitado; ausências serão calculadas novamente.

    Os limites não reduzem a cobertura dos resultados. Somente restringem a
    memória ocupada pelo reaproveitamento do histórico antes da inferência.
    """
    if type(max_artefatos) is not int or type(max_bytes) is not int or min(max_artefatos, max_bytes) < 0:
        raise ErroPersistenciaVetores("Os limites de memória do cache devem ser inteiros não negativos.")
    cache = {}
    used = 0
    for row in connection.execute("SELECT * FROM embedding_artifacts ORDER BY rowid DESC LIMIT ?", (max_artefatos,)):
        size = len(row["metadata_json"].encode("utf-8")) + len(row["vector_bytes"]) * 2
        if used + size > max_bytes:
            break
        used += size
        try:
            artifact = json.loads(row["metadata_json"])
            if artifact["id"] != row["logical_artifact_id"] or row["artifact_id"] != artifact["id"] + ":" + artifact["armazenamento"]["sha256_bytes"]:
                raise ErroPersistenciaVetores("Identidade inválida no cache de embeddings.")
            _restaurar(artifact["armazenamento"], row["vector_bytes"], row["sha256_bytes"])
            artifact["origem_calculo"] = "reutilizado"
            cache.setdefault(artifact["id"], artifact)
        except (ValueError, KeyError, TypeError) as error:
            if isinstance(error, ErroPersistenciaVetores):
                raise
            raise ErroPersistenciaVetores("O cache vetorial armazenado é inválido.") from error
    return cache


def exportar_zip(registro):
    """Pacote portátil: manifesto, origem e bytes float32 little-endian."""
    manifest = copy.deepcopy(registro)
    files = []
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for index, artifact in enumerate(manifest["artefatos"]):
            storage = artifact["armazenamento"]
            raw = _bytes(storage)
            storage.pop("base64")
            path = f"artefatos/{index:06d}.float32.bin"
            archive.writestr(path, raw)
            files.append({"tipo": "artefato", "id": artifact["id"], "caminho": path, **storage})
        for index, representation in enumerate(manifest["representacoes"]):
            storage = representation["vetor"]
            if storage is not None:
                raw = _bytes(storage)
                storage.pop("base64")
                path = f"representacoes/{index:06d}.float32.bin"
                archive.writestr(path, raw)
                files.append({"tipo": "representacao", "id": representation["id"], "caminho": path, **storage})
        archive.writestr("manifesto.json", json.dumps({
            "formato": "lingua_etapa09_zip", "versao": "1.0.0", "registro": manifest, "arquivos": files,
        }, ensure_ascii=False, indent=2, allow_nan=False))
    return buffer.getvalue()
