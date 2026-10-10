"""Escolha o formato pelo catálogo público, sem credencial nem geração de texto.

As capacidades publicadas servem somente para escolher o formato do pedido.
A estrutura recebida e a literalidade das citações continuam sendo conferidas
localmente, inclusive quando o modelo oferece apenas respostas em texto.
"""

from __future__ import annotations

import json
import re
from http.client import HTTPException
from threading import Lock
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener

from api_narrativas import ErroAPINarrativa, _contexto_https, _SemRedirecionamento


URL_CATALOGO = "https://openrouter.ai/api/v1/models"
TIMEOUT_SEGUNDOS = 20
LIMITE_CATALOGO_BYTES = 4 * 1024 * 1024
VALIDADE_CACHE_SEGUNDOS = 300
_cache = {}
_lock_cache = Lock()


class ErroModelo(ValueError):
    """Problema com mensagem própria e segura para a página local."""


def _objeto_sem_repeticoes(pares):
    objeto = {}
    for nome, valor in pares:
        if nome in objeto:
            raise ValueError("Campo repetido")
        objeto[nome] = valor
    return objeto


def _constante_invalida(_valor):
    raise ValueError("Constante inválida")


def _formato_do_catalogo(dados, modelo):
    if not isinstance(dados, bytes) or len(dados) > LIMITE_CATALOGO_BYTES:
        raise ErroModelo("O catálogo público de modelos excedeu o tamanho permitido ou retornou dados inválidos.")
    try:
        catalogo = json.loads(dados.decode("utf-8"), object_pairs_hook=_objeto_sem_repeticoes,
                              parse_constant=_constante_invalida)
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
        raise ErroModelo("O catálogo público de modelos retornou JSON inválido. Tente consultar novamente mais tarde.") from None
    if not isinstance(catalogo, dict) or not isinstance(catalogo.get("data"), list):
        raise ErroModelo("O catálogo público de modelos retornou dados inválidos. Nenhuma capacidade foi presumida.")
    encontrados = []
    for item in catalogo["data"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise ErroModelo("O catálogo público de modelos retornou dados inválidos. Nenhuma capacidade foi presumida.")
        if item["id"] == modelo:
            encontrados.append(item)
    if not encontrados:
        raise ErroModelo(
            "O modelo informado não consta no catálogo público do OpenRouter. "
            "Confira o identificador exato, incluindo o nome antes da barra e o sufixo, quando houver."
        )
    if len(encontrados) != 1:
        raise ErroModelo("O catálogo público retornou informações conflitantes para o modelo. Tente novamente mais tarde.")
    parametros = encontrados[0].get("supported_parameters")
    if (not isinstance(parametros, list)
            or any(not isinstance(parametro, str) or not parametro for parametro in parametros)):
        raise ErroModelo(
            "O catálogo público não informou capacidades válidas para o modelo escolhido. "
            "Nenhum formato de resposta foi presumido."
        )
    if "structured_outputs" in parametros:
        return "json_schema"
    if "response_format" in parametros:
        return "json_object"
    return "texto"


def _consultar_formato(modelo):
    pedido = Request(URL_CATALOGO, method="GET", headers={"Accept": "application/json"})
    try:
        cliente = build_opener(_SemRedirecionamento(), HTTPSHandler(context=_contexto_https()))
        with cliente.open(pedido, timeout=TIMEOUT_SEGUNDOS) as resposta:
            dados = resposta.read(LIMITE_CATALOGO_BYTES + 1)
    except HTTPError as erro:
        status = erro.code
        erro.close()
        if status in (301, 302, 303, 307, 308):
            raise ErroModelo("O catálogo público tentou redirecionar a consulta. O redirecionamento foi recusado.") from None
        raise ErroModelo(
            f"Não foi possível consultar o catálogo público do OpenRouter (HTTP {status}). "
            "Verifique a conexão ou tente novamente mais tarde."
        ) from None
    except ErroAPINarrativa:
        raise ErroModelo(
            "Não foi possível preparar os certificados HTTPS para consultar o catálogo público. "
            "Execute o diagnóstico de conexão."
        ) from None
    except (URLError, OSError, HTTPException):
        raise ErroModelo(
            "Não foi possível conectar ao catálogo público do OpenRouter. "
            "Verifique a conexão e execute o diagnóstico de conexão."
        ) from None
    return _formato_do_catalogo(dados, modelo)


def escolher_formato_resposta(provedor, modelo) -> str:
    """Devolva json_schema, json_object ou texto, sem consultar chaves de API.

    A OpenAI preserva o pedido em JSON Schema. O OpenRouter é consultado em um
    GET público e fixo; o cache guarda apenas identificadores, formatos e prazo
    por cinco minutos. Falhas e capacidades ausentes nunca são armazenadas.
    """
    if provedor not in ("openrouter", "openai"):
        raise ErroModelo("Escolha OpenRouter ou OpenAI como provedor do modelo.")
    if (not isinstance(modelo, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}", modelo) is None):
        raise ErroModelo("Informe o identificador exato do modelo, sem espaços e com até 200 caracteres.")
    if provedor == "openai":
        return "json_schema"
    agora = monotonic()
    with _lock_cache:
        for id_expirado in [id for id, (validade, _formato) in _cache.items() if validade <= agora]:
            del _cache[id_expirado]
        armazenado = _cache.get(modelo)
        if armazenado is not None:
            return armazenado[1]
    formato = _consultar_formato(modelo)
    with _lock_cache:
        _cache[modelo] = (monotonic() + VALIDADE_CACHE_SEGUNDOS, formato)
    return formato
