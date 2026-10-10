"""Validação sem normalizar o relato; posições são pontos de código Unicode."""

import re


_QUEBRA = r"(?:\r\n|\n|\r(?!\n))"
_SEPARADOR = re.compile(_QUEBRA + r"[\t ]*" + _QUEBRA + r"(?:[\t ]*" + _QUEBRA + r")*")


class ErroEntrada(ValueError):
    def __init__(self, contagem):
        self.contagem = contagem
        super().__init__(" ".join(contagem["erros"]))


def contar_relato(texto):
    """Conte palavras por whitespace e localize parágrafos no original intacto."""
    if not isinstance(texto, str):
        raise ValueError("Envie o relato como texto.")
    paragrafos = []
    inicio = 0
    intervalos = []
    for separador in _SEPARADOR.finditer(texto):
        intervalos.append((inicio, separador.start()))
        inicio = separador.end()
    intervalos.append((inicio, len(texto)))
    for inicio, fim in intervalos:
        while inicio < fim and texto[inicio].isspace():
            inicio += 1
        while fim > inicio and texto[fim - 1].isspace():
            fim -= 1
        if inicio < fim:
            paragrafos.append({"id": f"P{len(paragrafos) + 1}", "inicio": inicio,
                               "fim": fim, "texto": texto[inicio:fim]})
    palavras = len(texto.split())
    erros = []
    if not palavras:
        erros.append("Cole um relato para iniciar a busca.")
    else:
        if len(paragrafos) != 2:
            erros.append(f"O relato deve ter exatamente dois parágrafos; foram encontrados {len(paragrafos)}.")
        if palavras > 400:
            erros.append(f"O relato tem {palavras} palavras. O limite é 400.")
    return {"texto": texto, "palavras": palavras, "paragrafos": paragrafos,
            "erros": erros, "valido": not erros}


def validar_relato(texto):
    contagem = contar_relato(texto)
    if contagem["erros"]:
        raise ErroEntrada(contagem)
    return contagem
