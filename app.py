"""Recebimento de textos e persistência local, independente do projeto anterior."""

from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4
from zoneinfo import ZoneInfo

from flask import Flask, Response, abort, redirect, render_template, request, url_for

from preparacao import ErroPreparacao, preparar_texto, validar_registro
from segmentacao import ErroSegmentacao, contexto_periodo, segmentar_preparacao, validar_segmentacao
from anotacao import (
    ErroAlinhamento, ErroAnotacao, ErroLimite, ErroModelo, anotar_segmentacao, validar_anotacao,
)
from sintaxe_entidades import (
    ErroAnalise,
    ErroEntrada as ErroAnaliseEntrada,
    ErroModelo as ErroAnaliseModelo,
    ErroAlinhamento as ErroAnaliseAlinhamento,
    ErroLimite as ErroAnaliseLimite,
    ErroPrevisao as ErroAnalisePrevisao,
    analisar_sintaxe_entidades,
    validar_analise_sintaxe_entidades,
)
from regras_linguisticas import (
    ErroRegras,
    ErroEntrada as ErroRegrasEntrada,
    aplicar_regras_linguisticas,
    validar_regras_linguisticas,
)
from unidades_contexto import (
    ErroContexto,
    ErroEntrada as ErroContextoEntrada,
    ErroLimite as ErroContextoLimite,
    construir_unidades_contexto,
    consultar_unidade_contexto,
    validar_unidades_contexto,
)
from vetorizacao import (
    ErroVetorizacao,
    ErroEntrada as ErroVetorizacaoEntrada,
    ErroConfiguracao as ErroVetorizacaoConfiguracao,
    ErroModelo as ErroVetorizacaoModelo,
    ErroInferencia as ErroVetorizacaoInferencia,
    ErroLimite as ErroVetorizacaoLimite,
    vetorizar_unidades_contexto,
    validar_vetorizacao,
    consultar_representacao,
)
from embeddings_e5 import criar_gerador_padrao
from persistencia_vetores import (
    ErroPersistenciaVetores,
    criar_tabelas as criar_tabelas_vetores,
    carregar_cache as carregar_cache_vetores,
    salvar_execucao as salvar_execucao_vetorial,
    salvar_falha as salvar_falha_vetorial,
    ler_execucao as ler_execucao_vetorial,
    exportar_zip as exportar_vetores_zip,
    json_canonico,
)


BASE = Path(__file__).resolve().parent


def connect_database(database):
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def form_crlf_option():
    values = request.form.getlist("normalizar_crlf")
    if not values:
        return False
    if values == ["on"]:
        return True
    raise ErroPreparacao("A opção CRLF deve ser enviada por uma única caixa de seleção.")


def save_preparation(connection, document, **options):
    """Registra uma nova preparação sem atualizar o documento ou versões anteriores."""
    prepared = preparar_texto(
        document["content"], document["id"],
        metadados={"documento_id": document["id"], "recebido_em": document["created_at"]},
        **options,
    )
    connection.execute(
        "INSERT INTO preparations (preparation_id, submission_id, record_json) VALUES (?, ?, ?)",
        (prepared["preparacao_id"], document["id"], json.dumps(prepared, ensure_ascii=False, allow_nan=False)),
    )
    return prepared


def latest_preparation(connection, document, preparation_id=None):
    sql = "SELECT preparation_id, record_json FROM preparations WHERE submission_id = ?"
    parameters = [document["id"]]
    if preparation_id is not None:
        sql += " AND preparation_id = ?"
        parameters.append(preparation_id)
    row = connection.execute(sql + " ORDER BY id DESC LIMIT 1", parameters).fetchone()
    if row is None:
        return None
    try:
        prepared = json.loads(row["record_json"])
    except (ValueError, TypeError) as error:
        raise ErroPreparacao("O JSON da preparação armazenada é inválido.") from error
    validar_registro(prepared)
    if (
        prepared["documento_id"] != document["id"]
        or prepared["preparacao_id"] != row["preparation_id"]
        or prepared["original"]["texto"] != document["content"]
        or prepared["metadados_origem"].get("documento_id") != document["id"]
        or prepared["metadados_origem"].get("recebido_em") != document["created_at"]
    ):
        raise ErroPreparacao("A preparação não corresponde ao documento original armazenado.")
    return prepared


def save_segmentation(connection, document, prepared, **options):
    """Acrescenta uma execução vinculada à preparação efetivamente processada."""
    segmented = segmentar_preparacao(prepared, **options)
    connection.execute(
        """INSERT INTO segmentations
           (segmentation_id, preparation_id, submission_id, registered_at, record_json)
           VALUES (?, ?, ?, ?, ?)""",
        (
            segmented["segmentacao_id"], prepared["preparacao_id"], document["id"],
            segmented["registrado_em"], json.dumps(segmented, ensure_ascii=False, allow_nan=False),
        ),
    )
    return segmented


def latest_segmentation(connection, document, preparation_id=None, segmentation_id=None):
    sql = "SELECT * FROM segmentations WHERE submission_id = ?"
    parameters = [document["id"]]
    if preparation_id is not None:
        sql += " AND preparation_id = ?"
        parameters.append(preparation_id)
    if segmentation_id is not None:
        sql += " AND segmentation_id = ?"
        parameters.append(segmentation_id)
    row = connection.execute(sql + " ORDER BY id DESC LIMIT 1", parameters).fetchone()
    if row is None:
        return None
    try:
        segmented = json.loads(row["record_json"])
    except (ValueError, TypeError) as error:
        raise ErroSegmentacao("O JSON da segmentação armazenada é inválido.") from error
    validar_segmentacao(segmented)
    prepared = latest_preparation(connection, document, row["preparation_id"])
    if (
        prepared is None
        or segmented["documento_id"] != document["id"]
        or segmented["segmentacao_id"] != row["segmentation_id"]
        or segmented["preparacao_id"] != row["preparation_id"]
        or segmented["registrado_em"] != row["registered_at"]
        or segmented["preparacao"] != prepared
    ):
        raise ErroSegmentacao("A segmentação não corresponde à preparação original armazenada.")
    return segmented


def save_annotation(connection, document, segmented, **options):
    """Acrescenta anotações da segmentação escolhida, sem substituir o histórico."""
    annotated = anotar_segmentacao(segmented, **options)
    connection.execute(
        """INSERT INTO annotations
           (annotation_id, segmentation_id, submission_id, registered_at, record_json)
           VALUES (?, ?, ?, ?, ?)""",
        (
            annotated["anotacao_id"], segmented["segmentacao_id"], document["id"],
            annotated["registrado_em"], json.dumps(annotated, ensure_ascii=False, allow_nan=False),
        ),
    )
    return annotated


def latest_annotation(connection, document, segmentation_id=None, annotation_id=None):
    sql = "SELECT * FROM annotations WHERE submission_id = ?"
    parameters = [document["id"]]
    if segmentation_id is not None:
        sql += " AND segmentation_id = ?"
        parameters.append(segmentation_id)
    if annotation_id is not None:
        sql += " AND annotation_id = ?"
        parameters.append(annotation_id)
    row = connection.execute(sql + " ORDER BY id DESC LIMIT 1", parameters).fetchone()
    if row is None:
        return None
    try:
        annotated = json.loads(row["record_json"])
    except (ValueError, TypeError) as error:
        raise ErroAnotacao("O JSON da anotação armazenada é inválido.") from error
    validar_anotacao(annotated)
    segmented = latest_segmentation(connection, document, segmentation_id=row["segmentation_id"])
    if (
        segmented is None
        or annotated["documento_id"] != document["id"]
        or annotated["anotacao_id"] != row["annotation_id"]
        or annotated["segmentacao_id"] != row["segmentation_id"]
        or annotated["registrado_em"] != row["registered_at"]
        or annotated["segmentacao"] != segmented
    ):
        raise ErroAnotacao("A anotação não corresponde à segmentação original armazenada.")
    return annotated


def save_analysis(connection, document, annotated, **options):
    """Acrescenta sintaxe e entidades da anotação escolhida, preservando as versões anteriores."""
    analyzed = analisar_sintaxe_entidades(annotated, **options)
    connection.execute(
        """INSERT INTO analyses
           (analysis_id, annotation_id, submission_id, registered_at, record_json)
           VALUES (?, ?, ?, ?, ?)""",
        (
            analyzed["analise_id"], annotated["anotacao_id"], document["id"],
            analyzed["registrado_em"], json.dumps(analyzed, ensure_ascii=False, allow_nan=False),
        ),
    )
    return analyzed


def latest_analysis(connection, document, annotation_id=None, analysis_id=None):
    sql = "SELECT * FROM analyses WHERE submission_id = ?"
    parameters = [document["id"]]
    if annotation_id is not None:
        sql += " AND annotation_id = ?"
        parameters.append(annotation_id)
    if analysis_id is not None:
        sql += " AND analysis_id = ?"
        parameters.append(analysis_id)
    row = connection.execute(sql + " ORDER BY id DESC LIMIT 1", parameters).fetchone()
    if row is None:
        return None
    try:
        analyzed = json.loads(row["record_json"])
    except (ValueError, TypeError) as error:
        raise ErroAnalise("O JSON da análise armazenada é inválido.") from error
    validar_analise_sintaxe_entidades(analyzed)
    annotated = latest_annotation(connection, document, annotation_id=row["annotation_id"])
    if (
        annotated is None
        or analyzed["documento_id"] != document["id"]
        or analyzed["analise_id"] != row["analysis_id"]
        or analyzed["anotacao_id"] != row["annotation_id"]
        or analyzed["registrado_em"] != row["registered_at"]
        or analyzed["anotacao"] != annotated
    ):
        raise ErroAnalise("A análise não corresponde à anotação original armazenada.")
    return analyzed


def save_rule_run(connection, document, analyzed, **options):
    """Acrescenta regras da análise exata, inclusive diagnósticos incompletos."""
    ruled = aplicar_regras_linguisticas(analyzed, **options)
    validar_regras_linguisticas(ruled)
    if ruled["analise"] != analyzed or ruled["documento_id"] != document["id"]:
        raise ErroRegras("A execução de regras não corresponde à análise selecionada.")
    connection.execute(
        """INSERT INTO rule_runs
           (execution_id, analysis_id, submission_id, registered_at, record_json)
           VALUES (?, ?, ?, ?, ?)""",
        (
            ruled["execucao_id"], analyzed["analise_id"], document["id"],
            ruled["registrado_em"], json.dumps(ruled, ensure_ascii=False, allow_nan=False),
        ),
    )
    return ruled


def latest_rule_run(connection, document, analysis_id=None, execution_id=None):
    sql = "SELECT * FROM rule_runs WHERE submission_id = ?"
    parameters = [document["id"]]
    if analysis_id is not None:
        sql += " AND analysis_id = ?"
        parameters.append(analysis_id)
    if execution_id is not None:
        sql += " AND execution_id = ?"
        parameters.append(execution_id)
    row = connection.execute(sql + " ORDER BY id DESC LIMIT 1", parameters).fetchone()
    if row is None:
        return None
    try:
        ruled = json.loads(row["record_json"])
    except (ValueError, TypeError) as error:
        raise ErroRegras("O JSON das regras armazenadas é inválido.") from error
    validar_regras_linguisticas(ruled)
    analyzed = latest_analysis(connection, document, analysis_id=row["analysis_id"])
    if (
        analyzed is None
        or ruled["documento_id"] != document["id"]
        or ruled["execucao_id"] != row["execution_id"]
        or ruled["analise_id"] != row["analysis_id"]
        or ruled["registrado_em"] != row["registered_at"]
        or ruled["analise"] != analyzed
    ):
        raise ErroRegras("As regras não correspondem à análise original armazenada.")
    return ruled


def save_context_run(connection, document, ruled, **options):
    """Acrescenta contexto da execução exata de regras, sem substituir a origem."""
    contextualized = construir_unidades_contexto(ruled, **options)
    validar_unidades_contexto(contextualized)
    if contextualized["regras"] != ruled or contextualized["documento_id"] != document["id"]:
        raise ErroContexto("O contexto não corresponde à execução de regras selecionada.")
    connection.execute(
        """INSERT INTO context_runs
           (execution_id, rule_execution_id, submission_id, registered_at, record_json)
           VALUES (?, ?, ?, ?, ?)""",
        (
            contextualized["execucao_id"], ruled["execucao_id"], document["id"],
            contextualized["registrado_em"], json.dumps(contextualized, ensure_ascii=False, allow_nan=False),
        ),
    )
    return contextualized


def latest_context_run(connection, document, rule_execution_id=None, execution_id=None):
    sql = "SELECT * FROM context_runs WHERE submission_id = ?"
    parameters = [document["id"]]
    if rule_execution_id is not None:
        sql += " AND rule_execution_id = ?"
        parameters.append(rule_execution_id)
    if execution_id is not None:
        sql += " AND execution_id = ?"
        parameters.append(execution_id)
    row = connection.execute(sql + " ORDER BY id DESC LIMIT 1", parameters).fetchone()
    if row is None:
        return None
    try:
        contextualized = json.loads(row["record_json"])
    except (ValueError, TypeError) as error:
        raise ErroContexto("O JSON das unidades de contexto armazenadas é inválido.") from error
    validar_unidades_contexto(contextualized)
    ruled = latest_rule_run(connection, document, execution_id=row["rule_execution_id"])
    if (
        ruled is None
        or contextualized["documento_id"] != document["id"]
        or contextualized["execucao_id"] != row["execution_id"]
        or contextualized["regras_execucao_id"] != row["rule_execution_id"]
        or contextualized["registrado_em"] != row["registered_at"]
        or contextualized["regras"] != ruled
    ):
        raise ErroContexto("O contexto não corresponde à execução de regras original armazenada.")
    return contextualized


def latest_embedding_run(connection, document, context_execution_id=None, execution_id=None):
    sql = "SELECT * FROM embedding_runs WHERE submission_id = ?"
    parameters = [document["id"]]
    if context_execution_id is not None:
        sql += " AND context_execution_id = ?"
        parameters.append(context_execution_id)
    if execution_id is not None:
        sql += " AND execution_id = ?"
        parameters.append(execution_id)
    row = connection.execute(sql + " ORDER BY id DESC LIMIT 1", parameters).fetchone()
    if row is None:
        return None
    vectorized = ler_execucao_vetorial(connection, row)
    contextualized = latest_context_run(connection, document, execution_id=row["context_execution_id"])
    if (
        contextualized is None or vectorized["contexto"] != contextualized
        or vectorized["documento_id"] != document["id"]
        or vectorized["execucao_id"] != row["execution_id"]
        or vectorized["contexto_execucao_id"] != row["context_execution_id"]
        or vectorized["registrado_em"] != row["registered_at"]
    ):
        raise ErroPersistenciaVetores("A vetorização não corresponde à execução contextual original armazenada.")
    if row["status"] == "concluida":
        validar_vetorizacao(vectorized)
    elif not validar_unidades_contexto(contextualized)["pronto_para_etapa_09"]:
        raise ErroPersistenciaVetores("O diagnóstico de vetorização possui uma origem sem prontidão.")
    return vectorized


def context_origin_snapshot(connection, document_id, contextualized):
    """Lê as linhas exatas já validadas para a conferência curta após inferir."""
    selections = (
        ("submissions", "id", document_id),
        ("preparations", "preparation_id", contextualized["preparacao_id"]),
        ("segmentations", "segmentation_id", contextualized["segmentacao_id"]),
        ("annotations", "annotation_id", contextualized["anotacao_id"]),
        ("analyses", "analysis_id", contextualized["analise_id"]),
        ("rule_runs", "execution_id", contextualized["regras_execucao_id"]),
        ("context_runs", "execution_id", contextualized["execucao_id"]),
    )
    snapshot = {}
    for table, field, identifier in selections:
        row = connection.execute(f"SELECT * FROM {table} WHERE {field} = ?", (identifier,)).fetchone()
        snapshot[table] = None if row is None else dict(row)
    return snapshot


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.environ.get("ANALISE_DB", str(BASE / "instance" / "textos.sqlite3")),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        EMBEDDING_OPTIONS={},
        EMBEDDING_CONFIGURATION={},
        EMBEDDING_CACHE_MAX_ARTIFACTS=2000,
        EMBEDDING_CACHE_MAX_BYTES=32 * 1024 * 1024,
    )
    if config:
        app.config.update(config)
    if app.config.get("EMBEDDING_GENERATOR_FACTORY") is not None and not app.config["TESTING"]:
        raise ValueError("O gerador injetado de embeddings é permitido somente em TESTING.")

    @app.template_filter("horario_brasilia")
    def display_time(value):
        try:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if instant.utcoffset() is None:
                return "Data indisponível"
            return instant.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y às %H:%M:%S")
        except (AttributeError, TypeError, ValueError):
            return "Data indisponível"

    database = Path(app.config["DATABASE"]).expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    app.config["DATABASE"] = str(database)
    with closing(connect_database(database)) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS preparations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                preparation_id TEXT NOT NULL UNIQUE,
                submission_id INTEGER NOT NULL REFERENCES submissions(id),
                record_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS segmentations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                segmentation_id TEXT NOT NULL UNIQUE,
                preparation_id TEXT NOT NULL REFERENCES preparations(preparation_id),
                submission_id INTEGER NOT NULL REFERENCES submissions(id),
                registered_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS segmentations_preparation ON segmentations (submission_id, preparation_id, id)"
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS annotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                annotation_id TEXT NOT NULL UNIQUE,
                segmentation_id TEXT NOT NULL REFERENCES segmentations(segmentation_id),
                submission_id INTEGER NOT NULL REFERENCES submissions(id),
                registered_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS annotations_segmentation ON annotations (submission_id, segmentation_id, id)"
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id TEXT NOT NULL UNIQUE,
                annotation_id TEXT NOT NULL REFERENCES annotations(annotation_id),
                submission_id INTEGER NOT NULL REFERENCES submissions(id),
                registered_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS analyses_annotation ON analyses (submission_id, annotation_id, id)"
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS rule_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id TEXT NOT NULL UNIQUE,
                analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),
                submission_id INTEGER NOT NULL REFERENCES submissions(id),
                registered_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS rule_runs_analysis ON rule_runs (submission_id, analysis_id, id)"
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS context_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id TEXT NOT NULL UNIQUE,
                rule_execution_id TEXT NOT NULL REFERENCES rule_runs(execution_id),
                submission_id INTEGER NOT NULL REFERENCES submissions(id),
                registered_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS context_runs_rules ON context_runs (submission_id, rule_execution_id, id)"
        )
        criar_tabelas_vetores(connection)
        connection.commit()

    @app.get("/")
    def index():
        return render_template("index.html", content="", error=None, record=None)

    @app.post("/envios")
    def submit_text():
        content = request.form.get("content", "")
        if not content.strip():
            return render_template(
                "index.html", content=content,
                error="Digite um texto antes de registrar.", record=None,
            ), 400

        created_at = datetime.now(timezone.utc).isoformat()
        try:
            normalizar_crlf = form_crlf_option()
            with closing(connect_database(app.config["DATABASE"])) as connection:
                cursor = connection.execute(
                    "INSERT INTO submissions (content, created_at) VALUES (?, ?)",
                    (content, created_at),
                )
                record_id = cursor.lastrowid
                save_preparation(
                    connection, {"id": record_id, "content": content, "created_at": created_at},
                    normalizar_crlf=normalizar_crlf,
                    registrado_em=created_at,
                )
                connection.commit()
        except ErroPreparacao as error:
            return render_template("index.html", content=content, error=str(error), record=None), 400
        return redirect(url_for("submission", record_id=record_id), code=303)

    @app.get("/envios/<int:record_id>")
    def submission(record_id):
        with closing(connect_database(app.config["DATABASE"])) as connection:
            row = connection.execute(
                "SELECT id, content, created_at FROM submissions WHERE id = ?",
                (record_id,),
            ).fetchone()
            if row is None:
                abort(404)
            try:
                preparation_id = request.args.get("preparacao_id")
                segmentation_id = request.args.get("segmentacao_id")
                annotation_id = request.args.get("anotacao_id")
                analysis_id = request.args.get("analise_id")
                execution_id = request.args.get("execucao_id")
                context_execution_id = request.args.get("contexto_execucao_id")
                embedding_execution_id = request.args.get("vetorizacao_execucao_id")
                contextualized = None
                vectorized = None
                if embedding_execution_id is not None:
                    if len(request.args.getlist("vetorizacao_execucao_id")) != 1 or not embedding_execution_id.strip():
                        return {"erro": "Selecione uma única execução de vetorização."}, 400
                    for field in ("preparacao_id", "segmentacao_id", "anotacao_id", "analise_id", "execucao_id", "contexto_execucao_id"):
                        if field in request.args and (len(request.args.getlist(field)) != 1 or not request.args[field].strip()):
                            return {"erro": f"Selecione um único {field} para consultar a vetorização."}, 400
                    vectorized = latest_embedding_run(connection, row, execution_id=embedding_execution_id)
                    if vectorized is None:
                        abort(404)
                    if context_execution_id is not None and context_execution_id != vectorized["contexto_execucao_id"]:
                        return {"erro": "A vetorização pertence a outra execução contextual."}, 400
                    context_execution_id = vectorized["contexto_execucao_id"]
                if execution_id is not None and (len(request.args.getlist("execucao_id")) != 1 or not execution_id.strip()):
                    return {"erro": "Selecione uma única execução de regras."}, 400
                if context_execution_id is not None:
                    if "contexto_execucao_id" in request.args and (len(request.args.getlist("contexto_execucao_id")) != 1 or not context_execution_id.strip()):
                        return {"erro": "Selecione uma única execução de contexto."}, 400
                    for field in ("preparacao_id", "segmentacao_id", "anotacao_id", "analise_id"):
                        if field in request.args and (len(request.args.getlist(field)) != 1 or not request.args[field].strip()):
                            return {"erro": f"Selecione um único {field} para consultar o contexto."}, 400
                    contextualized = latest_context_run(connection, row, execution_id=context_execution_id)
                    if contextualized is None:
                        abort(404)
                    if execution_id is not None and execution_id != contextualized["regras_execucao_id"]:
                        return {"erro": "O contexto pertence a outra execução de regras."}, 400
                    execution_id = contextualized["regras_execucao_id"]
                if execution_id is not None:
                    ruled = latest_rule_run(connection, row, execution_id=execution_id)
                    if ruled is None:
                        abort(404)
                    if (
                        analysis_id is not None and analysis_id != ruled["analise_id"]
                        or annotation_id is not None and annotation_id != ruled["anotacao_id"]
                        or segmentation_id is not None and segmentation_id != ruled["segmentacao_id"]
                        or preparation_id is not None and preparation_id != ruled["preparacao_id"]
                    ):
                        return {"erro": "As regras pertencem a outra análise ou origem."}, 400
                    analysis_id = ruled["analise_id"]
                    annotation_id = ruled["anotacao_id"]
                    segmentation_id = ruled["segmentacao_id"]
                    preparation_id = ruled["preparacao_id"]
                if analysis_id is not None:
                    analyzed = latest_analysis(connection, row, analysis_id=analysis_id)
                    if analyzed is None:
                        abort(404)
                    if (
                        annotation_id is not None and annotation_id != analyzed["anotacao_id"]
                        or segmentation_id is not None and segmentation_id != analyzed["segmentacao_id"]
                        or preparation_id is not None and preparation_id != analyzed["preparacao_id"]
                    ):
                        return {"erro": "A análise pertence a outra anotação, segmentação ou preparação."}, 400
                    annotation_id = analyzed["anotacao_id"]
                    segmentation_id = analyzed["segmentacao_id"]
                    preparation_id = analyzed["preparacao_id"]
                if annotation_id is not None:
                    annotated = latest_annotation(connection, row, annotation_id=annotation_id)
                    if annotated is None:
                        abort(404)
                    if (
                        segmentation_id is not None and segmentation_id != annotated["segmentacao_id"]
                        or preparation_id is not None and preparation_id != annotated["preparacao_id"]
                    ):
                        return {"erro": "A anotação pertence a outra segmentação ou preparação."}, 400
                    segmentation_id = annotated["segmentacao_id"]
                    preparation_id = annotated["preparacao_id"]
                if segmentation_id is not None:
                    segmented = latest_segmentation(connection, row, segmentation_id=segmentation_id)
                    if segmented is None:
                        abort(404)
                    if preparation_id is not None and preparation_id != segmented["preparacao_id"]:
                        return {"erro": "A segmentação pertence a outra preparação."}, 400
                    preparation_id = segmented["preparacao_id"]
                prepared = latest_preparation(connection, row, preparation_id)
                if preparation_id is not None and prepared is None:
                    abort(404)
                if segmentation_id is None:
                    segmented = latest_segmentation(connection, row, prepared["preparacao_id"]) if prepared else None
                if annotation_id is None:
                    annotated = latest_annotation(connection, row, segmented["segmentacao_id"]) if segmented else None
                if analysis_id is None:
                    analyzed = latest_analysis(connection, row, annotated["anotacao_id"]) if annotated else None
                if execution_id is None:
                    ruled = latest_rule_run(connection, row, analysis_id=analyzed["analise_id"]) if analyzed else None
                if context_execution_id is None:
                    contextualized = latest_context_run(connection, row, rule_execution_id=ruled["execucao_id"]) if ruled else None
                if embedding_execution_id is None:
                    vectorized = latest_embedding_run(connection, row, context_execution_id=contextualized["execucao_id"]) if contextualized else None
                embedding_history = connection.execute(
                    """SELECT id, execution_id AS vetorizacao_execucao_id, registered_at AS registrado_em, status AS estado
                       FROM embedding_runs WHERE submission_id = ? AND context_execution_id = ? ORDER BY id DESC""",
                    (record_id, contextualized["execucao_id"]),
                ).fetchall() if contextualized else []
                context_history = connection.execute(
                    """SELECT id, execution_id AS contexto_execucao_id, registered_at AS registrado_em
                       FROM context_runs WHERE submission_id = ? AND rule_execution_id = ? ORDER BY id DESC""",
                    (record_id, ruled["execucao_id"]),
                ).fetchall() if ruled else []
                rule_history = connection.execute(
                    """SELECT id, execution_id AS execucao_id, registered_at AS registrado_em
                       FROM rule_runs WHERE submission_id = ? AND analysis_id = ? ORDER BY id DESC""",
                    (record_id, analyzed["analise_id"]),
                ).fetchall() if analyzed else []
                analysis_history = connection.execute(
                    """SELECT id, analysis_id AS analise_id, registered_at AS registrado_em
                       FROM analyses WHERE submission_id = ? AND annotation_id = ? ORDER BY id DESC""",
                    (record_id, annotated["anotacao_id"]),
                ).fetchall() if annotated else []
                annotation_history = connection.execute(
                    """SELECT id, annotation_id AS anotacao_id, registered_at AS registrado_em
                       FROM annotations WHERE submission_id = ? AND segmentation_id = ? ORDER BY id DESC""",
                    (record_id, segmented["segmentacao_id"]),
                ).fetchall() if segmented else []
                history = connection.execute(
                    """SELECT segmentation_id AS segmentacao_id, registered_at AS registrado_em
                       FROM segmentations WHERE submission_id = ? AND preparation_id = ? ORDER BY id DESC""",
                    (record_id, prepared["preparacao_id"]),
                ).fetchall() if prepared else []
                preparation_history = connection.execute(
                    "SELECT id, preparation_id AS preparacao_id FROM preparations WHERE submission_id = ? ORDER BY id DESC",
                    (record_id,),
                ).fetchall()
            except (ErroPreparacao, ErroSegmentacao, ErroAnotacao, ErroAnalise, ErroRegras, ErroContexto, ErroVetorizacao, ErroPersistenciaVetores) as error:
                return {"erro": str(error)}, 409
        record = dict(row)
        record["created_at"] = datetime.fromisoformat(record["created_at"]).astimezone(
            ZoneInfo("America/Sao_Paulo")
        ).strftime("%d/%m/%Y às %H:%M:%S")
        return render_template(
            "index.html", content=row["content"], error=None, record=record, prepared=prepared,
            segmented=segmented, segmentation_history=[dict(item) for item in history],
            preparation_history=[dict(item) for item in preparation_history],
            annotated=annotated, annotation_history=[dict(item) for item in annotation_history],
            analyzed=analyzed, analysis_history=[dict(item) for item in analysis_history],
            ruled=ruled, rule_history=[dict(item) for item in rule_history],
            contextualized=contextualized, context_history=[dict(item) for item in context_history],
            vectorized=vectorized, embedding_history=[dict(item) for item in embedding_history],
        )

    @app.post("/envios/<int:record_id>/preparacoes")
    def prepare_submission(record_id):
        if request.is_json:
            payload = request.get_json(silent=True)
            allowed = {"normalizar_crlf", "preparacao_id", "registrado_em"}
            if not isinstance(payload, dict) or payload.keys() - allowed:
                return {"erro": "Envie um objeto JSON com normalizar_crlf, preparacao_id ou registrado_em."}, 400
            options = payload
        else:
            try:
                options = {"normalizar_crlf": form_crlf_option()}
            except ErroPreparacao as error:
                return {"erro": str(error)}, 400
        try:
            with closing(connect_database(app.config["DATABASE"])) as connection:
                document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
                if document is None:
                    abort(404)
                prepared = save_preparation(connection, document, **options)
                connection.commit()
        except ErroPreparacao as error:
            return {"erro": str(error)}, 400
        except sqlite3.IntegrityError:
            return {"erro": "Esse identificador de preparação já existe. Crie uma nova preparação."}, 409
        if request.is_json:
            return Response(json.dumps(prepared, ensure_ascii=False, allow_nan=False), status=201, mimetype="application/json")
        return redirect(url_for("submission", record_id=record_id), code=303)

    @app.get("/envios/<int:record_id>/preparacao.json")
    def preparation_json(record_id):
        with closing(connect_database(app.config["DATABASE"])) as connection:
            document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
            if document is None:
                abort(404)
            try:
                prepared = latest_preparation(connection, document, request.args.get("preparacao_id"))
            except ErroPreparacao as error:
                return {"erro": str(error)}, 409
        if prepared is None:
            return {"erro": "Nenhuma preparação encontrada. Gere uma preparação para este documento."}, 404
        return Response(
            json.dumps(prepared, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="preparacao-{record_id}.json"'},
        )

    @app.post("/envios/<int:record_id>/segmentacoes")
    def segment_submission(record_id):
        if request.is_json:
            payload = request.get_json(silent=True)
            allowed = {"preparacao_id", "segmentacao_id", "registrado_em"}
            if not isinstance(payload, dict) or payload.keys() - allowed:
                return {"erro": "Envie um objeto JSON com preparacao_id, segmentacao_id ou registrado_em."}, 400
            options = dict(payload)
        else:
            if set(request.form) != {"preparacao_id"} or len(request.form.getlist("preparacao_id")) != 1:
                return {"erro": "Selecione uma única preparação para segmentar."}, 400
            options = {"preparacao_id": request.form["preparacao_id"]}
        preparation_id = options.pop("preparacao_id", None)
        if "preparacao_id" in (payload if request.is_json else request.form) and (
            not isinstance(preparation_id, str) or not preparation_id.strip()
        ):
            return {"erro": "preparacao_id deve ser um identificador textual não vazio."}, 400
        try:
            with closing(connect_database(app.config["DATABASE"])) as connection:
                document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
                if document is None:
                    abort(404)
                prepared = latest_preparation(connection, document, preparation_id)
                if prepared is None:
                    return {"erro": "Preparação não encontrada. Prepare este documento antes de segmentar."}, 404
                segmented = save_segmentation(connection, document, prepared, **options)
                connection.commit()
        except ErroPreparacao as error:
            return {"erro": str(error)}, 409
        except ErroSegmentacao as error:
            return {"erro": str(error)}, 400
        except sqlite3.IntegrityError:
            return {"erro": "Esse identificador de segmentação já existe. Gere uma nova execução."}, 409
        if request.is_json:
            return Response(json.dumps(segmented, ensure_ascii=False, allow_nan=False), status=201, mimetype="application/json")
        return redirect(
            url_for("submission", record_id=record_id, preparacao_id=prepared["preparacao_id"], segmentacao_id=segmented["segmentacao_id"]),
            code=303,
        )

    @app.get("/envios/<int:record_id>/segmentacao.json")
    def segmentation_json(record_id):
        with closing(connect_database(app.config["DATABASE"])) as connection:
            document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
            if document is None:
                abort(404)
            try:
                segmented = latest_segmentation(connection, document, segmentation_id=request.args.get("segmentacao_id"))
            except (ErroPreparacao, ErroSegmentacao) as error:
                return {"erro": str(error)}, 409
        if segmented is None:
            return {"erro": "Nenhuma segmentação encontrada. Tokenize e segmente uma preparação deste documento."}, 404
        return Response(
            json.dumps(segmented, ensure_ascii=False, indent=2, allow_nan=False), mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="segmentacao-{record_id}.json"'},
        )

    @app.post("/envios/<int:record_id>/anotacoes")
    def annotate_submission(record_id):
        if request.is_json:
            payload = request.get_json(silent=True)
            allowed = {"segmentacao_id", "anotacao_id", "registrado_em"}
            if not isinstance(payload, dict) or payload.keys() - allowed:
                return {"erro": "Envie um objeto JSON com segmentacao_id, anotacao_id ou registrado_em."}, 400
            options = dict(payload)
        else:
            if set(request.form) != {"segmentacao_id"} or len(request.form.getlist("segmentacao_id")) != 1:
                return {"erro": "Selecione uma única segmentação para anotar."}, 400
            options = {"segmentacao_id": request.form["segmentacao_id"]}
        for field in ("segmentacao_id", "anotacao_id", "registrado_em"):
            if field in options and (not isinstance(options[field], str) or not options[field].strip()):
                return {"erro": f"{field} deve ser uma string não vazia."}, 400
        segmentation_id = options.pop("segmentacao_id", None)
        try:
            with closing(connect_database(app.config["DATABASE"])) as connection:
                document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
                if document is None:
                    abort(404)
                segmented = latest_segmentation(connection, document, segmentation_id=segmentation_id)
                if segmented is None:
                    return {"erro": "Segmentação não encontrada. Segmente uma preparação antes de anotar."}, 404
                annotated = save_annotation(connection, document, segmented, **options)
                connection.commit()
        except (ErroPreparacao, ErroSegmentacao) as error:
            return {"erro": str(error)}, 409
        except ErroModelo as error:
            return {"erro": str(error)}, 503
        except ErroAlinhamento as error:
            return {"erro": str(error)}, 422
        except ErroLimite as error:
            return {"erro": str(error)}, 413
        except ErroAnotacao as error:
            return {"erro": str(error)}, 400
        except sqlite3.IntegrityError:
            return {"erro": "Esse identificador de anotação já existe. Gere uma nova execução."}, 409
        if request.is_json:
            return Response(json.dumps(annotated, ensure_ascii=False, allow_nan=False), status=201, mimetype="application/json")
        return redirect(
            url_for(
                "submission", record_id=record_id, preparacao_id=annotated["preparacao_id"],
                segmentacao_id=annotated["segmentacao_id"], anotacao_id=annotated["anotacao_id"],
            ),
            code=303,
        )

    @app.get("/envios/<int:record_id>/anotacao.json")
    def annotation_json(record_id):
        with closing(connect_database(app.config["DATABASE"])) as connection:
            document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
            if document is None:
                abort(404)
            try:
                annotated = latest_annotation(connection, document, annotation_id=request.args.get("anotacao_id"))
            except (ErroPreparacao, ErroSegmentacao, ErroAnotacao) as error:
                return {"erro": str(error)}, 409
        if annotated is None:
            return {"erro": "Nenhuma anotação encontrada. Anote uma segmentação deste documento."}, 404
        return Response(
            json.dumps(annotated, ensure_ascii=False, indent=2, allow_nan=False), mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="anotacao-{record_id}.json"'},
        )

    @app.post("/envios/<int:record_id>/analises")
    def analyze_submission(record_id):
        if request.is_json:
            payload = request.get_json(silent=True)
            allowed = {"anotacao_id", "analise_id", "registrado_em"}
            if not isinstance(payload, dict) or payload.keys() - allowed:
                return {"erro": "Envie um objeto JSON com anotacao_id, analise_id ou registrado_em."}, 400
            options = dict(payload)
        else:
            if set(request.form) != {"anotacao_id"} or len(request.form.getlist("anotacao_id")) != 1:
                return {"erro": "Selecione uma única anotação para analisar."}, 400
            options = {"anotacao_id": request.form["anotacao_id"]}
        for field in ("anotacao_id", "analise_id", "registrado_em"):
            if field in options and (not isinstance(options[field], str) or not options[field].strip()):
                return {"erro": f"{field} deve ser uma string não vazia."}, 400
        annotation_id = options.pop("anotacao_id", None)
        try:
            with closing(connect_database(app.config["DATABASE"])) as connection:
                document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
                if document is None:
                    abort(404)
                annotated = latest_annotation(connection, document, annotation_id=annotation_id)
                if annotated is None:
                    return {"erro": "Anotação não encontrada. Anote uma segmentação antes de analisar."}, 404
                analyzed = save_analysis(connection, document, annotated, **options)
                connection.commit()
        except (ErroPreparacao, ErroSegmentacao, ErroAnotacao, ErroAnaliseEntrada) as error:
            return {"erro": str(error)}, 409
        except ErroAnaliseModelo as error:
            return {"erro": str(error)}, 503
        except (ErroAnaliseAlinhamento, ErroAnalisePrevisao) as error:
            return {"erro": str(error)}, 422
        except ErroAnaliseLimite as error:
            return {"erro": str(error)}, 413
        except ErroAnalise as error:
            return {"erro": str(error)}, 400
        except sqlite3.IntegrityError:
            return {"erro": "Esse identificador de análise já existe. Gere uma nova execução."}, 409
        if request.is_json:
            return Response(json.dumps(analyzed, ensure_ascii=False, allow_nan=False), status=201, mimetype="application/json")
        return redirect(
            url_for(
                "submission", record_id=record_id, preparacao_id=analyzed["preparacao_id"],
                segmentacao_id=analyzed["segmentacao_id"], anotacao_id=analyzed["anotacao_id"],
                analise_id=analyzed["analise_id"],
            ),
            code=303,
        )

    @app.get("/envios/<int:record_id>/analise.json")
    def analysis_json(record_id):
        with closing(connect_database(app.config["DATABASE"])) as connection:
            document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
            if document is None:
                abort(404)
            try:
                analyzed = latest_analysis(connection, document, analysis_id=request.args.get("analise_id"))
            except (ErroPreparacao, ErroSegmentacao, ErroAnotacao, ErroAnalise) as error:
                return {"erro": str(error)}, 409
        if analyzed is None:
            return {"erro": "Nenhuma análise encontrada. Analise a sintaxe e as entidades de uma anotação deste documento."}, 404
        return Response(
            json.dumps(analyzed, ensure_ascii=False, indent=2, allow_nan=False), mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="analise-{record_id}.json"'},
        )

    @app.post("/envios/<int:record_id>/regras")
    def apply_submission_rules(record_id):
        if request.is_json:
            payload = request.get_json(silent=True)
            allowed = {"analise_id", "execucao_id", "registrado_em", "regras_habilitadas"}
            if not isinstance(payload, dict) or payload.keys() - allowed:
                return {"erro": "Envie um objeto JSON com analise_id, execucao_id, registrado_em ou regras_habilitadas."}, 400
            options = dict(payload)
        else:
            if set(request.form) != {"analise_id"} or len(request.form.getlist("analise_id")) != 1:
                return {"erro": "Selecione uma única análise para aplicar as regras."}, 400
            options = {"analise_id": request.form["analise_id"]}
        for field in ("analise_id", "execucao_id", "registrado_em"):
            if field in options and (not isinstance(options[field], str) or not options[field].strip()):
                return {"erro": f"{field} deve ser uma string não vazia."}, 400
        if "regras_habilitadas" in options and not isinstance(options["regras_habilitadas"], list):
            return {"erro": "regras_habilitadas deve ser uma lista de identificadores de regras."}, 400
        analysis_id = options.pop("analise_id", None)
        try:
            with closing(connect_database(app.config["DATABASE"])) as connection:
                document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
                if document is None:
                    abort(404)
                analyzed = latest_analysis(connection, document, analysis_id=analysis_id)
                if analyzed is None:
                    return {"erro": "Análise não encontrada. Analise a sintaxe e as entidades antes de aplicar as regras."}, 404
                ruled = save_rule_run(connection, document, analyzed, **options)
                connection.commit()
        except (ErroPreparacao, ErroSegmentacao, ErroAnotacao, ErroAnalise, ErroRegrasEntrada) as error:
            return {"erro": str(error)}, 409
        except ErroRegras as error:
            return {"erro": str(error)}, 400
        except sqlite3.IntegrityError:
            return {"erro": "Esse identificador de execução já existe. Aplique as regras em uma nova execução."}, 409
        if request.is_json:
            status = 201 if ruled["validacao"]["pronto_para_etapa_08"] else 422
            return Response(json.dumps(ruled, ensure_ascii=False, allow_nan=False), status=status, mimetype="application/json")
        return redirect(
            url_for(
                "submission", record_id=record_id, preparacao_id=ruled["preparacao_id"],
                segmentacao_id=ruled["segmentacao_id"], anotacao_id=ruled["anotacao_id"],
                analise_id=ruled["analise_id"], execucao_id=ruled["execucao_id"],
            ),
            code=303,
        )

    @app.get("/envios/<int:record_id>/regras.json")
    def rules_json(record_id):
        execution_id = request.args.get("execucao_id")
        if execution_id is not None and (len(request.args.getlist("execucao_id")) != 1 or not execution_id.strip()):
            return {"erro": "Selecione uma única execução de regras."}, 400
        with closing(connect_database(app.config["DATABASE"])) as connection:
            document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
            if document is None:
                abort(404)
            try:
                ruled = latest_rule_run(connection, document, execution_id=execution_id)
            except (ErroPreparacao, ErroSegmentacao, ErroAnotacao, ErroAnalise, ErroRegras) as error:
                return {"erro": str(error)}, 409
        if ruled is None:
            return {"erro": "Nenhuma execução encontrada. Aplique as regras linguísticas a uma análise deste documento."}, 404
        return Response(
            json.dumps(ruled, ensure_ascii=False, indent=2, allow_nan=False), mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="regras-{record_id}.json"'},
        )

    @app.post("/envios/<int:record_id>/contextos")
    def contextualize_submission(record_id):
        if request.is_json:
            payload = request.get_json(silent=True)
            allowed = {"regras_execucao_id", "execucao_id", "registrado_em", "raio_anterior", "raio_seguinte", "atravessar_paragrafos"}
            if not isinstance(payload, dict) or payload.keys() - allowed:
                return {"erro": "Envie um objeto JSON com a execução de regras e a política de contexto."}, 400
            options = dict(payload)
        else:
            allowed = {"regras_execucao_id", "raio_anterior", "raio_seguinte", "atravessar_paragrafos"}
            if (
                set(request.form) - allowed or "regras_execucao_id" not in request.form
                or any(len(request.form.getlist(field)) != 1 for field in request.form)
            ):
                return {"erro": "Selecione uma única execução de regras e uma política de contexto."}, 400
            options = {"regras_execucao_id": request.form["regras_execucao_id"]}
            for field in ("raio_anterior", "raio_seguinte"):
                if field in request.form:
                    value = request.form[field]
                    if not value.isascii() or not value.isdecimal():
                        return {"erro": f"{field} deve ser um inteiro não negativo."}, 400
                    try:
                        options[field] = int(value)
                    except ValueError:
                        return {"erro": f"{field} excedeu o tamanho permitido."}, 400
            if "atravessar_paragrafos" in request.form:
                if request.form["atravessar_paragrafos"] != "on":
                    return {"erro": "A passagem entre parágrafos deve ser uma caixa de seleção."}, 400
                options["atravessar_paragrafos"] = True
        for field in ("regras_execucao_id", "execucao_id", "registrado_em"):
            if field in options and (not isinstance(options[field], str) or not options[field].strip()):
                return {"erro": f"{field} deve ser uma string não vazia."}, 400
        rule_execution_id = options.pop("regras_execucao_id", None)
        try:
            with closing(connect_database(app.config["DATABASE"])) as connection:
                document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
                if document is None:
                    abort(404)
                ruled = latest_rule_run(connection, document, execution_id=rule_execution_id)
                if ruled is None:
                    return {"erro": "Execução de regras não encontrada. Conclua as regras antes de construir contexto."}, 404
                contextualized = save_context_run(connection, document, ruled, **options)
                connection.commit()
        except (ErroPreparacao, ErroSegmentacao, ErroAnotacao, ErroAnalise, ErroRegras, ErroContextoEntrada) as error:
            return {"erro": str(error)}, 409
        except ErroContextoLimite as error:
            return {"erro": str(error)}, 413
        except ErroContexto as error:
            return {"erro": str(error)}, 400
        except sqlite3.IntegrityError:
            return {"erro": "Esse identificador de contexto já existe. Gere uma nova execução."}, 409
        if request.is_json:
            return Response(json.dumps(contextualized, ensure_ascii=False, allow_nan=False), status=201, mimetype="application/json")
        return redirect(
            url_for("submission", record_id=record_id, contexto_execucao_id=contextualized["execucao_id"]), code=303,
        )

    @app.get("/envios/<int:record_id>/contexto.json")
    def context_json(record_id):
        if set(request.args) - {"contexto_execucao_id"}:
            return {"erro": "Use contexto_execucao_id para selecionar uma execução contextual."}, 400
        execution_id = request.args.get("contexto_execucao_id")
        if execution_id is not None and (len(request.args.getlist("contexto_execucao_id")) != 1 or not execution_id.strip()):
            return {"erro": "Selecione uma única execução de contexto."}, 400
        with closing(connect_database(app.config["DATABASE"])) as connection:
            document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
            if document is None:
                abort(404)
            try:
                contextualized = latest_context_run(connection, document, execution_id=execution_id)
            except (ErroPreparacao, ErroSegmentacao, ErroAnotacao, ErroAnalise, ErroRegras, ErroContexto) as error:
                return {"erro": str(error)}, 409
        if contextualized is None:
            return {"erro": "Nenhum contexto encontrado. Construa unidades a partir de uma execução de regras pronta."}, 404
        return Response(
            json.dumps(contextualized, ensure_ascii=False, indent=2, allow_nan=False), mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="contexto-{record_id}.json"'},
        )

    @app.get("/envios/<int:record_id>/unidade-contexto.json")
    def context_unit_json(record_id):
        allowed = {"contexto_execucao_id", "unidade_id", "periodo_id"}
        if set(request.args) - allowed or "contexto_execucao_id" not in request.args:
            return {"erro": "Informe a execução contextual e um único identificador de unidade ou período."}, 400
        selectors = set(request.args) & {"unidade_id", "periodo_id"}
        if len(selectors) != 1 or any(len(request.args.getlist(field)) != 1 or not request.args[field].strip() for field in request.args):
            return {"erro": "Informe a execução contextual e um único identificador de unidade ou período."}, 400
        execution_id = request.args["contexto_execucao_id"]
        selector = next(iter(selectors))
        with closing(connect_database(app.config["DATABASE"])) as connection:
            document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
            if document is None:
                abort(404)
            try:
                contextualized = latest_context_run(connection, document, execution_id=execution_id)
                if contextualized is None:
                    return {"erro": "Execução de contexto não encontrada."}, 404
                field = "id" if selector == "unidade_id" else "periodo_foco_id"
                if not any(unit[field] == request.args[selector] for unit in contextualized["unidades"]):
                    return {"erro": "Unidade ou período não encontrado nesta execução contextual."}, 404
                unit = consultar_unidade_contexto(contextualized, **{selector: request.args[selector]})
            except (ErroPreparacao, ErroSegmentacao, ErroAnotacao, ErroAnalise, ErroRegras, ErroContexto) as error:
                return {"erro": str(error)}, 409
        return Response(json.dumps(unit, ensure_ascii=False, allow_nan=False), mimetype="application/json")

    @app.post("/envios/<int:record_id>/vetorizacoes")
    def vectorize_submission(record_id):
        allowed = {"contexto_execucao_id", "execucao_id", "registrado_em", "configuracao"}
        if request.is_json:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or payload.keys() - allowed:
                return {"erro": "Envie a execução contextual e a configuração de vetorização em um objeto JSON."}, 400
            options = dict(payload)
        else:
            fields = {"contexto_execucao_id", "perfil", "max_tokens", "tamanho_lote", "agregar"}
            if set(request.form) - fields or "contexto_execucao_id" not in request.form or any(len(request.form.getlist(field)) != 1 for field in request.form):
                return {"erro": "Selecione uma única execução contextual e uma configuração de vetorização."}, 400
            configuration = {"agregar": "agregar" in request.form}
            if "perfil" in request.form:
                configuration["perfil"] = request.form["perfil"]
            for field in ("max_tokens", "tamanho_lote"):
                if field in request.form:
                    value = request.form[field]
                    if not value.isascii() or not value.isdecimal():
                        return {"erro": f"{field} deve ser um inteiro positivo."}, 400
                    try:
                        configuration[field] = int(value)
                    except ValueError:
                        return {"erro": f"{field} excedeu o tamanho permitido."}, 400
            if "agregar" in request.form and request.form["agregar"] != "on":
                return {"erro": "A agregação deve ser uma caixa de seleção."}, 400
            options = {"contexto_execucao_id": request.form["contexto_execucao_id"], "configuracao": configuration}
        for field in ("contexto_execucao_id", "execucao_id", "registrado_em"):
            if field in options and (not isinstance(options[field], str) or not options[field].strip()):
                return {"erro": f"{field} deve ser uma string não vazia."}, 400
        configuration = dict(app.config["EMBEDDING_CONFIGURATION"])
        supplied_configuration = options.pop("configuracao", {})
        if not isinstance(supplied_configuration, dict) or supplied_configuration.keys() - {"perfil", "max_tokens", "tamanho_lote", "agregar"}:
            return {"erro": "A configuração deve conter perfil, max_tokens, tamanho_lote ou agregar."}, 400
        configuration.update(supplied_configuration)
        if "perfil" in configuration and configuration["perfil"] not in ("recuperacao", "similaridade", "consulta"):
            return {"erro": "Perfil de embeddings desconhecido."}, 400
        for field, minimum in (("max_tokens", 8), ("tamanho_lote", 1)):
            if field in configuration and (type(configuration[field]) is not int or configuration[field] < minimum):
                return {"erro": f"{field} deve ser inteiro maior ou igual a {minimum}."}, 400
        if "agregar" in configuration and type(configuration["agregar"]) is not bool:
            return {"erro": "agregar deve ser um booleano."}, 400
        if "registrado_em" in options:
            try:
                instant = datetime.fromisoformat(options["registrado_em"].replace("Z", "+00:00"))
                if instant.utcoffset() is None:
                    raise ValueError("Sem fuso horário")
            except ValueError:
                return {"erro": "registrado_em deve conter uma data ISO com fuso horário."}, 400
        context_execution_id = options.pop("contexto_execucao_id", None)
        options.setdefault("execucao_id", str(uuid4()))
        options.setdefault("registrado_em", datetime.now(timezone.utc).isoformat())
        source_errors = (ErroPreparacao, ErroSegmentacao, ErroAnotacao, ErroAnalise, ErroRegras, ErroContexto, ErroPersistenciaVetores)
        try:
            with closing(connect_database(app.config["DATABASE"])) as connection:
                connection.execute("BEGIN")
                document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
                if document is None:
                    abort(404)
                contextualized = latest_context_run(connection, document, execution_id=context_execution_id)
                if contextualized is None:
                    return {"erro": "Execução contextual não encontrada. Construa unidades antes de vetorizar."}, 404
                if not contextualized["validacao"]["pronto_para_etapa_09"]:
                    return {"erro": "A execução contextual ainda não está pronta para a etapa 09."}, 409
                if connection.execute("SELECT 1 FROM embedding_runs WHERE execution_id = ?", (options["execucao_id"],)).fetchone():
                    return {"erro": "Esse identificador de vetorização já existe. Gere uma nova execução."}, 409
                cache = carregar_cache_vetores(
                    connection, max_artefatos=app.config["EMBEDDING_CACHE_MAX_ARTIFACTS"],
                    max_bytes=app.config["EMBEDDING_CACHE_MAX_BYTES"],
                )
                origin_snapshot = context_origin_snapshot(connection, record_id, contextualized)
        except source_errors as error:
            return {"erro": str(error)}, 409

        # A conexão de leitura já foi encerrada: carregar o modelo e inferir não
        # mantém conexão ou transação SQLite aberta.
        generator = None
        failure_status = None
        try:
            factory = app.config.get("EMBEDDING_GENERATOR_FACTORY")
            generator = factory(app.config["EMBEDDING_OPTIONS"]) if factory else criar_gerador_padrao(app.config["EMBEDDING_OPTIONS"] or None)
            vectorized = vetorizar_unidades_contexto(contextualized, gerador=generator, configuracao=configuration, cache=cache, **options)
            validar_vetorizacao(vectorized)
        except ErroVetorizacaoEntrada as error:
            return {"erro": str(error)}, 409
        except ErroVetorizacaoConfiguracao as error:
            return {"erro": str(error)}, 400
        except ErroVetorizacaoLimite as error:
            failure_status, failure_error = 413, error
        except (ErroVetorizacaoModelo, ErroVetorizacaoInferencia) as error:
            failure_status, failure_error = 503, error
        except ErroVetorizacao as error:
            failure_status, failure_error = 503, error
        if failure_status is not None:
            model = None
            if generator is not None:
                try:
                    model = generator.descrever()
                except Exception:
                    pass
            vectorized = {
                "schema_version": "1.0.0", "etapa": "09_vetorizacao", "estado": "falhou",
                "execucao_id": options["execucao_id"], "registrado_em": options["registrado_em"],
                "documento_id": record_id, "contexto_execucao_id": contextualized["execucao_id"],
                "contexto": contextualized, "contexto_sha256": sha256(json_canonico(contextualized).encode("utf-8")).hexdigest(),
                "modelo": model, "configuracao": configuration, "representacoes": [], "artefatos": [],
                "erro": {"tipo": type(failure_error).__name__, "mensagem": str(failure_error)},
                "validacao": {"execucao_concluida": False},
            }
        try:
            with closing(connect_database(app.config["DATABASE"])) as connection:
                # Bloqueio curto impede trocar a origem entre a conferência e o INSERT.
                connection.execute("BEGIN IMMEDIATE")
                if context_origin_snapshot(connection, record_id, contextualized) != origin_snapshot:
                    return {"erro": "A origem contextual mudou durante a vetorização. Nenhum resultado foi salvo."}, 409
                if failure_status is None:
                    salvar_execucao_vetorial(connection, vectorized)
                else:
                    salvar_falha_vetorial(connection, vectorized)
                connection.commit()
        except source_errors as error:
            return {"erro": str(error)}, 409
        except sqlite3.IntegrityError:
            return {"erro": "Esse identificador de vetorização já existe. Gere uma nova execução."}, 409
        if request.is_json:
            return Response(json.dumps(vectorized, ensure_ascii=False, allow_nan=False), status=failure_status or 201, mimetype="application/json")
        return redirect(url_for("submission", record_id=record_id, vetorizacao_execucao_id=vectorized["execucao_id"]), code=303)

    def load_requested_embedding(record_id, *, representation=False):
        fields = {"vetorizacao_execucao_id", "representacao_id"} if representation else {"vetorizacao_execucao_id"}
        if set(request.args) - fields or any(len(request.args.getlist(field)) != 1 or not request.args[field].strip() for field in request.args):
            return None, ({"erro": "Selecione uma única execução de vetorização e os identificadores permitidos."}, 400)
        if representation and set(request.args) != fields:
            return None, ({"erro": "Informe vetorizacao_execucao_id e representacao_id."}, 400)
        with closing(connect_database(app.config["DATABASE"])) as connection:
            document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
            if document is None:
                abort(404)
            try:
                record = latest_embedding_run(connection, document, execution_id=request.args.get("vetorizacao_execucao_id"))
            except (ErroPreparacao, ErroSegmentacao, ErroAnotacao, ErroAnalise, ErroRegras, ErroContexto, ErroVetorizacao, ErroPersistenciaVetores) as error:
                return None, ({"erro": str(error)}, 409)
        if record is None:
            return None, ({"erro": "Nenhuma execução de vetorização encontrada."}, 404)
        return record, None

    @app.get("/envios/<int:record_id>/vetorizacao.json")
    def embedding_json(record_id):
        vectorized, error = load_requested_embedding(record_id)
        if error:
            return error
        return Response(json.dumps(vectorized, ensure_ascii=False, indent=2, allow_nan=False), mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="vetorizacao-{record_id}.json"'})

    @app.get("/envios/<int:record_id>/vetorizacao.zip")
    def embedding_zip(record_id):
        vectorized, error = load_requested_embedding(record_id)
        if error:
            return error
        if vectorized.get("estado") == "falhou":
            return {"erro": "A execução falhou e não possui um pacote de vetores. Consulte o diagnóstico JSON."}, 409
        return Response(exportar_vetores_zip(vectorized), mimetype="application/zip",
            headers={"Content-Disposition": f'attachment; filename="vetorizacao-{record_id}.zip"'})

    @app.get("/envios/<int:record_id>/representacao-vetorial.json")
    def embedding_representation_json(record_id):
        vectorized, error = load_requested_embedding(record_id, representation=True)
        if error:
            return error
        if vectorized.get("estado") == "falhou":
            return {"erro": "A execução falhou e não possui representações vetoriais."}, 409
        identifier = request.args["representacao_id"]
        if not any(item["id"] == identifier for item in vectorized["representacoes"]):
            return {"erro": "Representação não encontrada nesta execução de vetorização."}, 404
        representation = consultar_representacao(vectorized, identifier)
        return Response(json.dumps(representation, ensure_ascii=False, allow_nan=False), mimetype="application/json")

    @app.get("/envios/<int:record_id>/segmentacoes/<segmentacao_id>/periodos/<periodo_id>/contexto.json")
    @app.get("/envios/<int:record_id>/contexto-periodo.json")
    def period_context_json(record_id, segmentacao_id=None, periodo_id=None):
        if segmentacao_id is None:
            values = [request.args.getlist(name) for name in ("segmentacao_id", "periodo_id")]
            if any(len(items) != 1 or not items[0].strip() for items in values):
                return {"erro": "Forneça segmentacao_id e periodo_id uma única vez."}, 400
            segmentacao_id, periodo_id = (items[0] for items in values)
        with closing(connect_database(app.config["DATABASE"])) as connection:
            document = connection.execute("SELECT * FROM submissions WHERE id = ?", (record_id,)).fetchone()
            if document is None:
                abort(404)
            try:
                segmented = latest_segmentation(connection, document, segmentation_id=segmentacao_id)
                if segmented is None:
                    abort(404)
                if not any(period["id"] == periodo_id for period in segmented["periodos"]):
                    return {"erro": "Período não encontrado nesta segmentação."}, 404
                context = contexto_periodo(segmented, periodo_id)
            except (ErroPreparacao, ErroSegmentacao) as error:
                return {"erro": str(error)}, 409
        return Response(json.dumps(context, ensure_ascii=False, allow_nan=False), mimetype="application/json")

    @app.errorhandler(413)
    def too_large(_error):
        return render_template(
            "index.html", content="", record=None,
            error="O envio excedeu o limite de 2 MB. Envie um texto menor.",
        ), 413

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5001)
