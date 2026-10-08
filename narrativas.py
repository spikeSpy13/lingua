"""Estado e validação do gerador progressivo de narrativas.

Nenhuma função consulta um modelo ou grava arquivos. Operações sobre o estado
devolvem uma cópia independente, adequada à persistência como JSON. Textos não
são normalizados nem corrigidos automaticamente.
"""

from copy import deepcopy
from datetime import datetime, timezone
import re


class ErroNarrativa(ValueError):
    """Uma operação não atende às regras do gerador narrativo."""


MOVIMENTOS = (
    "Contextualização",
    "Acontecimento",
    "Recorrência",
    "Contradição",
    "Inconclusão",
)
PROVEDORES = ("openrouter", "openai")
SCHEMA_VERSION = "1.0.0"

# A contagem considera palavras compostas e formas com apóstrofo uma palavra.
# As letras acentuadas seguem as classes Unicode do Python; não há normalização.
_PALAVRA = re.compile(r"[^\W_]+(?:[-'’][^\W_]+)*", re.UNICODE)
_SEPARADOR_PARAGRAFO = re.compile(
    r"(?:\r\n|[\r\n])(?:[^\S\r\n]*(?:\r\n|[\r\n]))+|\u2029"
)
_ABREVIACAO = re.compile(
    r"(?<!\w)(?:"
    r"p\.[ \t]*ex\.|a\.C\.|d\.C\.|"
    r"(?:srs?|sras?|srta|drs?|dras?|prof|profa|profs|profas|"
    r"etc|ex|obs|av|pág|págs|pag|pags|pp|art|arts|cap|caps|"
    r"tel|cel|ed|eds|vol|vols|aprox|depto|depart|min|máx|max)\.|"
    r"(?-i:(?:[A-ZÀ-ÖØ-Ý]\.){2,})|"
    r"(?-i:[A-ZÀ-ÖØ-Ý]\.(?=[ \t]+[A-ZÀ-ÖØ-Ý][a-zà-öø-ÿ]))|"
    r"(?:n|núm|num)\.[ \t]*[º°]|\d+\.[ºª]"
    r")",
    re.IGNORECASE,
)


def _texto(valor, nome):
    if not isinstance(valor, str):
        raise ErroNarrativa(f"{nome} deve ser texto.")
    try:
        valor.encode("utf-8")
    except UnicodeEncodeError as erro:
        raise ErroNarrativa(f"{nome} contém caracteres Unicode inválidos.") from erro
    return valor


def _indice(indice):
    if type(indice) is not int or not 0 <= indice <= 5:
        raise ErroNarrativa("O campo deve estar entre 0 e 5.")
    return indice


def _campo(estado, indice):
    return estado["planejamento"] if indice == 0 else estado["movimentos"][indice - 1]


def _instante():
    return datetime.now(timezone.utc).isoformat()


def novo_estado():
    """Cria seis campos independentes, sem narrativa nem aprovação inicial."""
    return {
        "schema_version": SCHEMA_VERSION,
        "ideia_inicial": "",
        "planejamento": {
            "instrucoes": "",
            "texto_gerado": "",
            "texto_atual": "",
            "aprovado": False,
            "modelo_utilizado": None,
            "historico": [],
        },
        "intensidade": 3,
        "provedor": "openrouter",
        "modelo": "openai/gpt-4.1-mini",
        "movimentos": [
            {
                "id": indice,
                "label": label,
                "instrucoes": "",
                "texto_gerado": "",
                "texto_atual": "",
                "validacao": None,
                "aprovado": False,
                "revisao_coerencia": False,
                "modelo_utilizado": None,
                "historico": [],
            }
            for indice, label in enumerate(MOVIMENTOS, 1)
        ],
        "narrativa_final": "",
        "historico_alteracoes": [],
    }


def _conferir_estado(estado):
    if not isinstance(estado, dict) or estado.get("schema_version") != SCHEMA_VERSION:
        raise ErroNarrativa("Estado narrativo inválido ou versão incompatível.")
    for nome in ("ideia_inicial", "provedor", "modelo", "narrativa_final"):
        _texto(estado.get(nome), nome)
    if estado["provedor"] not in PROVEDORES:
        raise ErroNarrativa("Provedor deve ser OpenRouter ou OpenAI.")
    if not estado["modelo"].strip():
        raise ErroNarrativa("Informe o modelo.")
    if type(estado.get("intensidade")) is not int or not 1 <= estado["intensidade"] <= 5:
        raise ErroNarrativa("A intensidade dramática deve estar entre 1 e 5.")
    if not isinstance(estado.get("historico_alteracoes"), list):
        raise ErroNarrativa("Histórico de alterações inválido.")
    if not isinstance(estado.get("planejamento"), dict):
        raise ErroNarrativa("Planejamento narrativo inválido.")
    movimentos = estado.get("movimentos")
    if not isinstance(movimentos, list) or len(movimentos) != 5:
        raise ErroNarrativa("O estado deve conter exatamente cinco movimentos.")
    for indice in range(6):
        campo = _campo(estado, indice)
        if not isinstance(campo, dict):
            raise ErroNarrativa(f"O campo {indice} é inválido.")
        for nome in ("instrucoes", "texto_gerado", "texto_atual"):
            _texto(campo.get(nome), f"Campo {indice}, {nome}")
        if type(campo.get("aprovado")) is not bool:
            raise ErroNarrativa(f"A aprovação do campo {indice} é inválida.")
        if campo.get("modelo_utilizado") is not None:
            _texto(campo["modelo_utilizado"], "Modelo utilizado")
        if not isinstance(campo.get("historico"), list):
            raise ErroNarrativa(f"O histórico do campo {indice} é inválido.")
        if indice:
            if type(campo.get("id")) is not int or campo["id"] != indice:
                raise ErroNarrativa("Identificação ou ordem dos movimentos inválida.")
            _texto(campo.get("label"), "Nome do movimento")
            if type(campo.get("revisao_coerencia")) is not bool:
                raise ErroNarrativa(f"A revisão do movimento {indice} é inválida.")
            if campo.get("validacao") is not None and not isinstance(campo["validacao"], dict):
                raise ErroNarrativa(f"A validação do movimento {indice} é inválida.")


def _registrar(estado, indice, operacao, **dados):
    evento = {"registrado_em": _instante(), "indice": indice, "operacao": operacao, **dados}
    estado["historico_alteracoes"].append(deepcopy(evento))
    if indice is not None:
        _campo(estado, indice)["historico"].append(deepcopy(evento))


def _invalidar_posteriores(estado, indice):
    estado["narrativa_final"] = ""
    for movimento in estado["movimentos"][indice:]:
        movimento["aprovado"] = False
        if movimento["texto_atual"].strip():
            movimento["revisao_coerencia"] = True


def _editar_texto(estado, indice, texto):
    campo = _campo(estado, indice)
    if campo["texto_atual"] == texto:
        return
    _registrar(
        estado, indice, "edicao_manual",
        texto_anterior=campo["texto_atual"], texto_atual=texto,
        texto_gerado=campo["texto_gerado"], modelo_utilizado=campo["modelo_utilizado"],
    )
    campo["texto_atual"] = texto
    campo["aprovado"] = False
    if indice:
        campo["validacao"] = validar_movimento(texto)
    _invalidar_posteriores(estado, indice)


def salvar_campos(estado, campos):
    """Salva somente os campos enviados, sem aceitar aprovação ou texto gerado.

    ``movimentos`` é uma lista parcial de objetos com ``id`` e os campos
    ``instrucoes`` e/ou ``texto_atual``. Elementos e chaves ausentes permanecem
    exatamente como estavam. Mudanças nas instruções, intensidade e modelo não
    modificam textos nem aprovações já obtidas.
    """
    _conferir_estado(estado)
    if not isinstance(campos, dict):
        raise ErroNarrativa("Os campos devem ser um objeto JSON.")
    permitidos = {"ideia_inicial", "intensidade", "provedor", "modelo", "planejamento", "movimentos"}
    if set(campos) - permitidos:
        raise ErroNarrativa("O envio contém campos que não podem ser editados.")
    novo = deepcopy(estado)
    for nome in ("ideia_inicial", "provedor", "modelo"):
        if nome not in campos:
            continue
        valor = _texto(campos[nome], nome)
        if nome == "provedor" and valor not in PROVEDORES:
            raise ErroNarrativa("Provedor deve ser OpenRouter ou OpenAI.")
        if nome == "modelo" and not valor.strip():
            raise ErroNarrativa("Informe o modelo.")
        if novo[nome] == valor:
            continue
        _registrar(novo, None, "alteracao_configuracao", campo=nome, antes=novo[nome], depois=valor)
        novo[nome] = valor
        if nome == "ideia_inicial":
            novo["planejamento"]["aprovado"] = False
            _invalidar_posteriores(novo, 0)
    if "intensidade" in campos:
        valor = campos["intensidade"]
        if type(valor) is not int or not 1 <= valor <= 5:
            raise ErroNarrativa("A intensidade dramática deve estar entre 1 e 5.")
        if novo["intensidade"] != valor:
            _registrar(novo, None, "alteracao_configuracao", campo="intensidade", antes=novo["intensidade"], depois=valor)
            novo["intensidade"] = valor
    patches = []
    if "planejamento" in campos:
        patches.append((0, campos["planejamento"]))
    if "movimentos" in campos:
        if not isinstance(campos["movimentos"], list):
            raise ErroNarrativa("Movimentos devem ser uma lista de campos editados.")
        vistos = set()
        for patch in campos["movimentos"]:
            if not isinstance(patch, dict):
                raise ErroNarrativa("Cada movimento editado deve ser um objeto.")
            indice = _indice(patch.get("id"))
            if indice == 0 or indice in vistos:
                raise ErroNarrativa("Cada movimento deve ter um id único entre 1 e 5.")
            vistos.add(indice)
            patches.append((indice, {nome: valor for nome, valor in patch.items() if nome != "id"}))
    # Ordem narrativa garante que editar vários campos juntos preserva os sinais
    # de revisão criados por mudanças em um campo anterior.
    for indice, patch in sorted(patches, key=lambda item: item[0]):
        if not isinstance(patch, dict) or set(patch) - {"instrucoes", "texto_atual"}:
            raise ErroNarrativa("Edite somente instruções e versão atual de cada campo.")
        campo = _campo(novo, indice)
        if "instrucoes" in patch:
            valor = _texto(patch["instrucoes"], "Instruções")
            if valor != campo["instrucoes"]:
                _registrar(novo, indice, "alteracao_instrucoes", antes=campo["instrucoes"], depois=valor)
                campo["instrucoes"] = valor
        if "texto_atual" in patch:
            _editar_texto(novo, indice, _texto(patch["texto_atual"], "Texto atual"))
    return novo


def _ocorrencias_pontuacao(texto):
    ocorrencias = []
    for tipo, padrao in (
        ("dois_pontos", r":"),
        ("ponto_e_virgula", r";"),
        ("reticencias", r"\.{3,}|…"),
        ("travessao", r"[—―]"),
    ):
        for item in re.finditer(padrao, texto):
            ocorrencias.append({"tipo": tipo, "trecho": item.group(), "inicio": item.start(), "fim": item.end()})
    abreviacoes = list(_ABREVIACAO.finditer(texto))
    for item in abreviacoes:
        ocorrencias.append({"tipo": "abreviacao_com_ponto", "trecho": item.group(), "inicio": item.start(), "fim": item.end()})
    return sorted(ocorrencias, key=lambda item: (item["inicio"], item["fim"])), abreviacoes


def _periodos(texto, abreviacoes):
    pontos_protegidos = {
        posicao
        for item in abreviacoes
        for posicao in range(item.start(), item.end())
        if texto[posicao] == "."
    }
    # Pontos decimais não encerram um período, por exemplo "2.5".
    for item in re.finditer(r"(?<=\d)\.(?=\d)", texto):
        pontos_protegidos.add(item.start())
    resultado = []

    def adicionar(inicio, fim, terminado):
        while inicio < fim and texto[inicio].isspace():
            inicio += 1
        while fim > inicio and texto[fim - 1].isspace():
            fim -= 1
        trecho = texto[inicio:fim]
        if trecho and _PALAVRA.search(trecho):
            resultado.append({
                "texto": trecho, "inicio": inicio, "fim": fim,
                "palavras": len(_PALAVRA.findall(trecho)), "terminado": terminado,
            })

    inicio = cursor = 0
    while cursor < len(texto):
        if texto[cursor] in ".!?" and cursor not in pontos_protegidos:
            fim = cursor + 1
            while fim < len(texto) and texto[fim] in ".!?":
                fim += 1
            while fim < len(texto) and texto[fim] in '\"\'”’»':
                fim += 1
            adicionar(inicio, fim, True)
            inicio = cursor = fim
        else:
            cursor += 1
    adicionar(inicio, len(texto), False)
    return resultado


def validar_movimento(texto):
    """Valida a estrutura com contagens e intervalos transparentes.

    Linhas em branco (ou U+2029) separam parágrafos. Sequências de ``.?!``
    encerram períodos, exceto pontos decimais e abreviações reconhecidas.
    Um trecho final sem terminador é contado e apontado como erro. A análise
    não atesta a voz narrativa, a continuidade ou interpretações semânticas.
    """
    _texto(texto, "Movimento")
    pontuacao, abreviacoes = _ocorrencias_pontuacao(texto)
    periodos = _periodos(texto, abreviacoes)
    paragrafos = [parte for parte in _SEPARADOR_PARAGRAFO.split(texto) if parte.strip()]
    contagens = [periodo["palavras"] for periodo in periodos]
    total = len(_PALAVRA.findall(texto))
    erros = []
    if len(paragrafos) != 1:
        erros.append(f"Esperado exatamente 1 parágrafo; encontrado(s) {len(paragrafos)}.")
    if len(periodos) != 5:
        erros.append(f"Esperados exatamente 5 períodos; encontrado(s) {len(periodos)}.")
    for indice, periodo in enumerate(periodos, 1):
        if not 20 <= periodo["palavras"] <= 36:
            erros.append(f"Período {indice}: {periodo['palavras']} palavras; permitido de 20 a 36.")
        if not periodo["terminado"]:
            erros.append(f"Período {indice}: falta pontuação de encerramento (. ! ou ?).")
    if total > 180:
        erros.append(f"Parágrafo com {total} palavras; máximo permitido de 180.")
    nomes = {
        "dois_pontos": "dois-pontos", "ponto_e_virgula": "ponto e vírgula",
        "reticencias": "reticências", "travessao": "travessão",
        "abreviacao_com_ponto": "abreviação com ponto",
    }
    for item in pontuacao:
        erros.append(f"Pontuação proibida: {nomes[item['tipo']]} ({item['trecho']!r}, posição {item['inicio']}).")
    return {
        "valido": not erros,
        "quantidade_paragrafos": len(paragrafos),
        "quantidade_periodos": len(periodos),
        "palavras_por_periodo": contagens,
        "total_palavras": total,
        "periodos": periodos,
        "pontuacao_proibida": pontuacao,
        "erros": erros,
        "criterio_contagem": "Palavras Unicode, compostos e apóstrofos unidos; .!? encerram períodos, exceto abreviações conhecidas e decimais. Linhas em branco separam parágrafos.",
    }


def _exigir_anteriores(estado, indice):
    planejamento = estado["planejamento"]
    if not planejamento["aprovado"] or not planejamento["texto_atual"].strip():
        raise ErroNarrativa("Aprove a ideia geral antes de trabalhar nos movimentos.")
    for movimento in estado["movimentos"][:indice - 1]:
        if not movimento["aprovado"] or movimento["revisao_coerencia"]:
            raise ErroNarrativa(f"Revise e aprove o movimento {movimento['id']} antes de continuar.")
        if not validar_movimento(movimento["texto_atual"])["valido"]:
            raise ErroNarrativa(f"O movimento {movimento['id']} contém erros estruturais.")


def preparar_geracao(estado, indice, corrigir=False):
    """Retorna somente o contexto do alvo, com todas as versões anteriores atuais."""
    _conferir_estado(estado)
    indice = _indice(indice)
    if type(corrigir) is not bool:
        raise ErroNarrativa("A opção de correção deve ser verdadeira ou falsa.")
    if indice:
        _exigir_anteriores(estado, indice)
    elif not estado["ideia_inicial"].strip():
        raise ErroNarrativa("Informe a ideia inicial para gerar o planejamento.")
    campo = _campo(estado, indice)
    if corrigir and (indice == 0 or not campo["texto_atual"].strip()):
        raise ErroNarrativa("A correção requer um movimento narrativo já preenchido.")
    return {
        "indice": indice,
        "ideia_inicial": estado["ideia_inicial"],
        "planejamento": estado["planejamento"]["texto_atual"],
        "anteriores": [
            {"id": item["id"], "label": item["label"], "texto": item["texto_atual"]}
            for item in estado["movimentos"][:max(0, indice - 1)]
        ],
        "instrucoes": campo["instrucoes"],
        "intensidade": estado["intensidade"],
        "texto_atual": campo["texto_atual"],
        "erros": validar_movimento(campo["texto_atual"])["erros"] if corrigir else None,
    }


def aplicar_geracao(estado, indice, texto, modelo):
    """Substitui somente o alvo, preservando versões anteriores no histórico."""
    preparar_geracao(estado, indice)
    _texto(texto, "Texto gerado")
    _texto(modelo, "Modelo utilizado")
    if not texto.strip() or not modelo.strip():
        raise ErroNarrativa("A resposta e o modelo utilizado não podem ser vazios.")
    novo = deepcopy(estado)
    campo = _campo(novo, indice)
    _registrar(
        novo, indice, "geracao" if not campo["texto_gerado"] else "regeneracao",
        texto_anterior=campo["texto_atual"], texto_gerado_anterior=campo["texto_gerado"],
        texto_atual=texto, texto_gerado=texto, modelo_utilizado=modelo,
        modelo_anterior=campo["modelo_utilizado"],
    )
    campo["texto_gerado"] = texto
    campo["texto_atual"] = texto
    campo["modelo_utilizado"] = modelo
    campo["aprovado"] = False
    if indice:
        campo["validacao"] = validar_movimento(texto)
        # A nova geração usa o contexto atual; sua aprovação continua explícita.
        campo["revisao_coerencia"] = False
    _invalidar_posteriores(novo, indice)
    return novo


def validar(estado, indice):
    """Atualiza somente a validação do movimento solicitado, sem aprová-lo."""
    _conferir_estado(estado)
    indice = _indice(indice)
    if indice == 0:
        raise ErroNarrativa("O planejamento não está sujeito às regras dos parágrafos.")
    novo = deepcopy(estado)
    campo = _campo(novo, indice)
    campo["validacao"] = validar_movimento(campo["texto_atual"])
    if not campo["validacao"]["valido"]:
        campo["aprovado"] = False
        novo["narrativa_final"] = ""
    return novo


def aprovar(estado, indice):
    """Aprova explicitamente a versão atual, inclusive sua coerência revisada."""
    _conferir_estado(estado)
    indice = _indice(indice)
    campo = _campo(estado, indice)
    if not campo["texto_atual"].strip():
        raise ErroNarrativa("Preencha o campo antes de aprová-lo.")
    validacao = None
    if indice:
        _exigir_anteriores(estado, indice)
        validacao = validar_movimento(campo["texto_atual"])
        if not validacao["valido"]:
            raise ErroNarrativa("Não é possível aprovar este movimento. " + " ".join(validacao["erros"]))
    novo = deepcopy(estado)
    campo = _campo(novo, indice)
    campo["aprovado"] = True
    if indice:
        campo["validacao"] = validacao
        campo["revisao_coerencia"] = False
    _registrar(novo, indice, "aprovacao", texto_atual=campo["texto_atual"])
    return novo


def montar_narrativa(estado):
    """Concatena exatamente as cinco versões aprovadas, sem consultar um modelo."""
    _conferir_estado(estado)
    _exigir_anteriores(estado, 6)
    novo = deepcopy(estado)
    novo["narrativa_final"] = "\n\n".join(item["texto_atual"] for item in novo["movimentos"])
    _registrar(novo, None, "montagem_narrativa")
    return novo
