"""Interface Flask e persistência dos rascunhos de geração progressiva."""

from contextlib import closing
from datetime import datetime, timezone
import json
import secrets
import sqlite3
from uuid import uuid4

from flask import Blueprint, Response, abort, current_app, redirect, render_template, request, url_for

from api_narrativas import ErroAPINarrativa, gerar_texto, status_configuracao
from narrativas import (
    ErroNarrativa, novo_estado, salvar_campos, aplicar_geracao, aprovar,
    validar_movimento, montar_narrativa, preparar_geracao,
)


bp = Blueprint("narrativas", __name__)


@bp.after_request
def nao_armazenar_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def _conexao():
    connection = sqlite3.connect(current_app.config["DATABASE"], timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def criar_tabela(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS narrative_drafts (
        id TEXT PRIMARY KEY,
        revision INTEGER NOT NULL,
        csrf_token TEXT NOT NULL,
        state_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        busy_since REAL
    )""")


def _agora():
    return datetime.now(timezone.utc)


def _json(state):
    return json.dumps(state, ensure_ascii=False, allow_nan=False)


def _ler(identifier):
    with closing(_conexao()) as connection:
        row = connection.execute("SELECT * FROM narrative_drafts WHERE id = ?", (identifier,)).fetchone()
    if row is None:
        abort(404)
    return dict(row)


def _render(row=None, *, error=None, notice=None, status=200):
    with closing(_conexao()) as connection:
        drafts = connection.execute(
            "SELECT id, updated_at, state_json FROM narrative_drafts ORDER BY updated_at DESC LIMIT 30"
        ).fetchall()
    history = [{"id": d["id"], "updated_at": d["updated_at"],
                "idea": json.loads(d["state_json"])["ideia_inicial"][:100] or "Narrativa sem título"}
               for d in drafts]
    state = json.loads(row["state_json"]) if row else None
    provider = state["provedor"] if state else "openrouter"
    return render_template("narrativas.html", draft=row, narrative=state,
                           drafts=history, api_status=status_configuracao(provider),
                           error=error, notice=notice), status


def _salvar(row, state, *, busy=False):
    """Reserva o rascunho sem manter uma transação aberta durante a API."""
    with closing(_conexao()) as connection:
        cursor = connection.execute(
            """UPDATE narrative_drafts SET revision = revision + 1, state_json = ?,
                updated_at = ?, busy_since = ? WHERE id = ? AND revision = ? AND busy_since IS NULL""",
            (_json(state), _agora().isoformat(), _agora().timestamp() if busy else None,
             row["id"], row["revision"]),
        )
        if cursor.rowcount != 1:
            raise ErroNarrativa("Este rascunho foi atualizado em outra aba. Recarregue antes de continuar.")
        connection.commit()
    return _ler(row["id"])


def _texto_formulario(form, name, current):
    value = form[name]
    # Textareas HTML normalizam quebras de linha ao enviar o formulário.
    # Um envio sem edição deve conservar a versão exata já aprovada.
    if value.replace("\r\n", "\n").replace("\r", "\n") == current.replace("\r\n", "\n").replace("\r", "\n"):
        return current
    return value


def _campos(form, state):
    return {
        "ideia_inicial": _texto_formulario(form, "ideia_inicial", state["ideia_inicial"]),
        "intensidade": form["intensidade"],
        "provedor": form["provedor"], "modelo": form["modelo"],
        "planejamento": {
            "instrucoes": _texto_formulario(form, "instrucoes_0", state["planejamento"]["instrucoes"]),
            "texto_atual": _texto_formulario(form, "texto_0", state["planejamento"]["texto_atual"]),
        },
        "movimentos": [{"id": i,
                        "instrucoes": _texto_formulario(form, f"instrucoes_{i}", state["movimentos"][i-1]["instrucoes"]),
                        "texto_atual": _texto_formulario(form, f"texto_{i}", state["movimentos"][i-1]["texto_atual"])}
                       for i in range(1, 6)],
    }


@bp.get("/narrativas")
def inicio():
    return _render()


@bp.post("/narrativas")
def criar():
    state = novo_estado()
    try:
        state = salvar_campos(state, {"ideia_inicial": request.form.get("ideia_inicial", "")})
    except ErroNarrativa as error:
        return _render(error=str(error), status=400)
    identifier = str(uuid4())
    with closing(_conexao()) as connection:
        connection.execute(
            "INSERT INTO narrative_drafts VALUES (?, 0, ?, ?, ?, NULL)",
            (identifier, secrets.token_urlsafe(32), _json(state), _agora().isoformat()),
        )
        connection.commit()
    return redirect(url_for("narrativas.editar", identifier=identifier), code=303)


@bp.get("/narrativas/<identifier>")
def editar(identifier):
    return _render(_ler(identifier))


@bp.post("/narrativas/<identifier>")
def acao(identifier):
    row = _ler(identifier)
    token = request.form.get("csrf_token", "")
    if not token.isascii() or not secrets.compare_digest(token, row["csrf_token"]):
        return _render(row, error="Formulário inválido. Recarregue a página.", status=400)
    if request.form.get("revision") != str(row["revision"]):
        return _render(row, error="Este rascunho mudou em outra aba. Suas alterações não foram aplicadas. Recarregue antes de continuar.", status=409)
    if row["busy_since"] is not None:
        # Uma chamada interrompida não pode bloquear o rascunho indefinidamente.
        if _agora().timestamp() - row["busy_since"] > 180:
            with closing(_conexao()) as connection:
                connection.execute(
                    "UPDATE narrative_drafts SET busy_since = NULL, revision = revision + 1 WHERE id = ? AND revision = ?",
                    (identifier, row["revision"]),
                )
                connection.commit()
            return _render(_ler(identifier), error="A geração anterior foi interrompida. O rascunho foi liberado; revise-o antes de solicitar novamente.", status=409)
        return _render(row, error="Uma geração já está em andamento. Aguarde e recarregue a página.", status=409)
    state = json.loads(row["state_json"])
    reserved = None
    try:
        fields = _campos(request.form, state)
        fields["intensidade"] = int(fields["intensidade"])
        state = salvar_campos(state, fields)
        # Persistir edições também quando uma geração ou aprovação for recusada.
        row = _salvar(row, state)
        action = request.form.get("acao", "salvar")
        if action == "salvar":
            return _render(row, notice="Alterações salvas.")
        if action == "montar":
            state = montar_narrativa(state)
        else:
            verb, raw_index = action.split(":", 1)
            index = int(raw_index)
            if not 0 <= index <= 5:
                raise ErroNarrativa("Selecione um campo válido.")
            target = state["planejamento"] if index == 0 else state["movimentos"][index - 1]
            if verb == "aprovar":
                state = aprovar(state, index)
            elif verb == "validar" and index:
                target["validacao"] = validar_movimento(target["texto_atual"])
            elif verb in ("gerar", "corrigir"):
                if target["texto_atual"].strip() and request.form.get(f"substituir_{index}") != "on":
                    raise ErroNarrativa("Para substituir o texto deste campo, marque a confirmação de regeneração. Suas edições foram salvas.")
                context = preparar_geracao(state, index, corrigir=verb == "corrigir")
                if not status_configuracao(state["provedor"])["chave_configurada"] and not current_app.config.get("NARRATIVE_GENERATOR"):
                    raise ErroAPINarrativa("Configure NARRATIVA_API_KEY nas configurações do ambiente para gerar pelo OpenRouter.")
                reserved = _salvar(row, state, busy=True)
                generator = current_app.config.get("NARRATIVE_GENERATOR") or gerar_texto
                text = generator(provedor=state["provedor"], modelo=state["modelo"], **context)
                state = aplicar_geracao(state, index, text, state["modelo"])
                with closing(_conexao()) as connection:
                    cursor = connection.execute(
                        """UPDATE narrative_drafts SET state_json = ?, revision = revision + 1,
                            updated_at = ?, busy_since = NULL WHERE id = ? AND revision = ? AND busy_since IS NOT NULL""",
                        (_json(state), _agora().isoformat(), identifier, reserved["revision"]),
                    )
                    if cursor.rowcount != 1:
                        raise ErroNarrativa("A geração terminou após uma atualização do rascunho e não substituiu seus textos.")
                    connection.commit()
                return _render(_ler(identifier), notice="Campo gerado. Revise o texto e a validação antes de aprovar.")
            else:
                raise ErroNarrativa("Ação inválida.")
        row = _salvar(row, state)
        return _render(row, notice="Narrativa montada." if action == "montar" else "Campo atualizado.")
    except (ErroNarrativa, ErroAPINarrativa, ValueError, KeyError) as error:
        if reserved is not None:
            with closing(_conexao()) as connection:
                connection.execute(
                    "UPDATE narrative_drafts SET busy_since = NULL WHERE id = ? AND revision = ?",
                    (identifier, reserved["revision"]),
                )
                connection.commit()
        message = str(error) if isinstance(error, (ErroNarrativa, ErroAPINarrativa)) else "Formulário inválido. Confira os campos enviados."
        return _render(_ler(identifier), error=message, status=502 if isinstance(error, ErroAPINarrativa) else 400)


@bp.get("/narrativas/<identifier>/exportar.<format>")
def exportar(identifier, format):
    state = json.loads(_ler(identifier)["state_json"])
    if format == "json":
        content = json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        mime = "application/json"
    elif format == "txt":
        try:
            assembled = montar_narrativa(state)
            if not state["narrativa_final"] or assembled["narrativa_final"] != state["narrativa_final"]:
                raise ErroNarrativa("Monte a narrativa aprovada antes de exportar o TXT.")
        except ErroNarrativa as error:
            return _render(_ler(identifier), error=str(error), status=400)
        content, mime = state["narrativa_final"], "text/plain"
    else:
        abort(404)
    return Response(content, mimetype=mime, headers={
        "Content-Disposition": f'attachment; filename="narrativa.{format}"',
        "Cache-Control": "no-store",
    })
