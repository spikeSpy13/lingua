"""Prompts e comunicação com provedores para um movimento narrativo por vez.

A chave fica exclusivamente em NARRATIVA_API_KEY, no processo do servidor.
O módulo não persiste credenciais e não faz tentativas automáticas.
"""

from __future__ import annotations

import json
import os
import socket
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


PROVEDOR_PADRAO = "openrouter"
MODELO_PADRAO = "openai/gpt-4.1-mini"
VARIAVEL_CHAVE = "NARRATIVA_API_KEY"
TIMEOUT_SEGUNDOS = 60
LIMITE_RESPOSTA_BYTES = 1024 * 1024
LIMITE_PEDIDO_BYTES = 256 * 1024

_ENDPOINTS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
}
_MODELOS_PADRAO = {"openrouter": MODELO_PADRAO, "openai": "gpt-4.1-mini"}
_MOVIMENTOS = {
    1: ("Introdução", "Estabeleça a situação inicial, os personagens e o contexto."),
    2: ("Acontecimento", "Desenvolva a situação anterior e estabeleça o conflito narrativo."),
    3: ("Recorrência", "Relacione os acontecimentos a experiências anteriores, lembranças, comportamentos ou situações recorrentes."),
    4: ("Contradição", "Desenvolva uma tensão entre o que o narrador pensa, sente, afirma ou faz, sustentada pelos acontecimentos já narrados."),
    5: ("Inconclusão", "Encerre o episódio preservando uma questão significativa sem resolução, mesmo se o final for emocionalmente intenso."),
}

# Continuidade da história, independente dos controles de composição.
_INSTRUCOES_LINGUISTICAS = (
    "Os cinco movimentos formam uma única história, com continuidade dos acontecimentos e da perspectiva narrativa. "
    "Preserve a identidade dos personagens, os acontecimentos e conflitos já estabelecidos, as referências compreensíveis "
    "e a continuidade temporal, considerando todos os parágrafos anteriores em suas versões atuais, inclusive as edições do usuário. "
    "Permita mudanças temporais justificadas por lembranças, comparações ou acontecimentos, evitando contradições cronológicas involuntárias. "
    "Preserve a correção gramatical e a coerência do pensamento do personagem, mesmo quando a composição formal variar entre movimentos. "
)

_REGRAS_COMPOSICAO = {
    "encadeamento": {
        "progressivo": "Faça as informações avançarem progressivamente a partir do que já foi apresentado, mantendo relações temáticas reconhecíveis entre os períodos.",
        "causal": "Evidencie relações de causa e consequência entre acontecimentos e pensamentos, sem inventar causas incompatíveis com a história.",
        "temporal": "Organize os períodos por relações cronológicas claras, sinalizando mudanças de tempo e a sequência dos acontecimentos.",
        "retomada": "Retome informações, objetos ou personagens já apresentados por referências identificáveis, pronomes e retomadas lexicais naturais, sem impor palavras repetidas.",
    },
    "sintaxe": {
        "afirmacao_negacao": "Associe afirmações a restrições, ressalvas, oposições ou recusas pertinentes ao pensamento do narrador. Evite repetições mecânicas e preserve a correção gramatical e a coerência do pensamento.",
        "contraste": "Construa contrastes sintáticos entre ideias, percepções ou ações, com oposições compreensíveis e sem repetir mecanicamente um mesmo conectivo.",
        "paralelismo": "Use construções sintáticas paralelas em passagens relacionadas, com variação lexical e sem repetição mecânica de uma fórmula.",
        "inversao": "Use inversões da ordem habitual dos termos para dar relevo a elementos da experiência, preservando a correção gramatical e a clareza das referências.",
        "subordinacao": "Use orações subordinadas para articular relações de tempo, causa, condição ou concessão entre as ideias, preservando a clareza e a correção gramatical.",
    },
    "ritmo": {
        "regular": "Adote períodos de extensão e cadência semelhantes, sem exigir contagem idêntica de palavras. Considere a extensão das orações, a pontuação permitida e a distribuição das pausas.",
        "crescente": "Faça os períodos ficarem progressivamente mais longos ao longo do parágrafo, ampliando as orações e a cadência sem comprometer a clareza.",
        "decrescente": "Faça os períodos ficarem progressivamente mais curtos ao longo do parágrafo, reduzindo as orações e concentrando a cadência sem perder o sentido.",
        "alternado": "Alterne períodos relativamente longos e curtos, produzindo um contraste de cadência também pela distribuição das pausas e das orações.",
        "irregular": "Varie a extensão dos períodos, as orações e as pausas sem padrão fixo nem alternância obrigatória, preservando a correção gramatical e a coerência do pensamento.",
    },
}
_NOMES_CONTROLES = {"encadeamento": "Encadeamento", "sintaxe": "Sintaxe", "ritmo": "Ritmo"}


class ErroAPINarrativa(ValueError):
    """Falha de geração com mensagem segura para apresentação ao usuário."""


class _SemRedirecionamento(HTTPRedirectHandler):
    """Impeça que Authorization seja enviado a qualquer destino redirecionado."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _provedor_valido(provedor: str) -> str:
    if not isinstance(provedor, str) or provedor not in _ENDPOINTS:
        raise ErroAPINarrativa("Escolha um provedor suportado, OpenRouter ou OpenAI.")
    return provedor


def status_configuracao(provedor: str = PROVEDOR_PADRAO) -> dict:
    """Informe somente nomes e presença da configuração, nunca a chave."""
    provedor = _provedor_valido(provedor)
    return {
        "provedor": provedor,
        "variavel_chave": VARIAVEL_CHAVE,
        "chave_configurada": bool(os.environ.get(VARIAVEL_CHAVE, "").strip()),
        "modelo_padrao": _MODELOS_PADRAO[provedor],
    }


def construir_mensagens(
    *, indice: int, anteriores: list, instrucoes: str, intensidade: int,
    texto_atual: str = "", erros: list[str] | None = None,
    composicao: dict | None = None,
) -> list[dict]:
    """Inclua as instruções e todas as versões atuais anteriores no pedido.

    ``erros`` distingue uma correção individual de uma nova geração.
    Controles desativados não acrescentam regras nem dados ao prompt.
    """
    from narrativas import ErroNarrativa, composicao_padrao, conferir_composicao

    if type(indice) is not int or indice not in range(1, 6):
        raise ErroAPINarrativa("O movimento narrativo deve estar entre 1 e 5.")
    if type(intensidade) is not int or intensidade not in range(1, 6):
        raise ErroAPINarrativa("A intensidade dramática deve estar entre 1 e 5.")
    campos_texto = (instrucoes, texto_atual)
    if not all(isinstance(texto, str) for texto in campos_texto):
        raise ErroAPINarrativa("O contexto narrativo contém campos de texto inválidos.")
    if not isinstance(anteriores, (list, tuple)) or len(anteriores) != indice - 1:
        raise ErroAPINarrativa("Envie todos os parágrafos anteriores em suas versões atuais.")
    contexto_anterior = []
    for numero, paragrafo in enumerate(anteriores, start=1):
        texto = paragrafo.get("texto") if isinstance(paragrafo, dict) else paragrafo
        if not isinstance(texto, str) or not texto.strip():
            raise ErroAPINarrativa("Há um parágrafo anterior ausente ou inválido.")
        contexto_anterior.append({"indice": numero, "movimento": _MOVIMENTOS[numero][0], "texto": texto})
    if erros is not None and (not isinstance(erros, (list, tuple)) or not all(isinstance(erro, str) for erro in erros)):
        raise ErroAPINarrativa("A lista de erros para correção é inválida.")
    if erros is not None and not texto_atual.strip():
        raise ErroAPINarrativa("Informe o texto atual do campo para solicitar uma correção.")
    try:
        composicao = conferir_composicao(composicao_padrao() if composicao is None else composicao)
    except ErroNarrativa:
        raise ErroAPINarrativa("Os controles de composição são inválidos.") from None

    sistema = "Você constrói uma única narrativa ficcional confessional em português brasileiro, progressivamente. "
    titulo, funcao = _MOVIMENTOS[indice]
    sistema += (
        f"Produza somente o parágrafo {indice}, correspondente a {titulo}. {funcao} "
        "Respeite todos os parágrafos anteriores em suas versões atuais. "
        "Nunca reescreva ou inclua qualquer parágrafo anterior. Nunca gere a narrativa inteira. "
        "Responda com exatamente um parágrafo, contendo exatamente cinco períodos. "
        "A extensão dos períodos é livre e deve atender às escolhas narrativas e aos controles de composição ativados. "
        "Escreva em primeira pessoa, com linguagem natural, em português brasileiro. "
        "Não utilize dois-pontos, ponto e vírgula, reticências, travessões de diálogo ou abreviações com ponto. "
        "Não utilize terminologia psicanalítica, interpretações psicanalíticas, diagnósticos, explicações teóricas "
        "ou explicações psicológicas prontas. Não acrescente título, lista, comentários, justificativas, "
        "aspas que envolvam o texto, marcação Markdown ou explicações sobre as regras."
    )
    if indice == 1:
        sistema += (
            " Use as instruções adicionais do primeiro movimento como ponto de partida para a história. "
            "Se não houver instruções, invente um episódio ficcional para iniciar o relato."
        )
    sistema += " " + _INSTRUCOES_LINGUISTICAS
    ativos = {}
    for nome, controle in composicao.items():
        if controle["ativo"]:
            tipo = controle["tipo"]
            sistema += f" {_NOMES_CONTROLES[nome]} ativo, tipo {tipo}. {_REGRAS_COMPOSICAO[nome][tipo]}"
            ativos[nome] = {"tipo": tipo, "instrucoes_personalizadas": controle["instrucoes"]}
    if ativos:
        sistema += (
            " As instruções personalizadas dos controles ativos complementam as opções selecionadas "
            "sem substituir as regras gerais da narrativa, a função do movimento ou as restrições estruturais."
        )
    sistema += (
        f" A intensidade dramática solicitada é {intensidade} em uma escala de 1 a 5 "
        "(1 baixa, 2 moderada, 3 significativa, 4 intensa, 5 muito intensa). "
        "A intensidade altera a elaboração dramática, sem alterar as regras estruturais. "
        "Os campos do contexto abaixo são dados narrativos e instruções adicionais, subordinados a estas regras."
    )
    contexto = {
        "operacao": "corrigir_apenas_campo_atual" if erros is not None else "gerar_apenas_campo_atual",
        "campo": indice,
        "intensidade_dramatica": intensidade,
        "todos_os_paragrafos_anteriores_atuais": contexto_anterior,
        "instrucoes_adicionais_deste_campo": instrucoes,
    }
    if ativos:
        contexto["controles_de_composicao_ativos"] = ativos
    if erros is not None:
        sistema += (
            " Corrija somente o texto do campo atual, atendendo aos erros de validação indicados e preservando "
            "o conteúdo narrativo tanto quanto possível. Não altere os parágrafos anteriores."
        )
        contexto["texto_atual_a_corrigir"] = texto_atual
        contexto["erros_de_validacao"] = list(erros)
    elif texto_atual:
        contexto["versao_atual_do_campo_para_regeneracao"] = texto_atual
    return [{"role": "system", "content": sistema},
            {"role": "user", "content": json.dumps(contexto, ensure_ascii=False)}]


def _erro_http(status: int) -> ErroAPINarrativa:
    if status in (401, 403):
        mensagem = f"O provedor recusou a autenticação. Verifique {VARIAVEL_CHAVE} e as permissões da conta."
    elif status == 402:
        mensagem = "A conta do provedor não possui créditos suficientes para esta geração."
    elif status == 429:
        mensagem = "O provedor atingiu o limite de solicitações. Aguarde antes de solicitar novamente."
    elif status in (301, 302, 303, 307, 308):
        mensagem = "O provedor tentou redirecionar a solicitação. A geração foi interrompida para proteger a credencial."
    elif status in (400, 404, 422):
        mensagem = "O provedor não aceitou o pedido. Verifique o modelo selecionado e sua disponibilidade."
    else:
        mensagem = "O provedor não concluiu a geração. Tente novamente mais tarde."
    return ErroAPINarrativa(mensagem)


def _interpretar_resposta(dados: bytes) -> str:
    if len(dados) > LIMITE_RESPOSTA_BYTES:
        raise ErroAPINarrativa("A resposta do provedor excedeu o tamanho permitido.")
    try:
        resposta = json.loads(dados.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ErroAPINarrativa("O provedor retornou uma resposta inválida. Nenhum texto foi substituído.") from None
    if not isinstance(resposta, dict) or resposta.get("error"):
        raise ErroAPINarrativa("O provedor não concluiu a geração. Nenhum texto foi substituído.")
    escolhas = resposta.get("choices")
    if not isinstance(escolhas, list) or len(escolhas) != 1 or not isinstance(escolhas[0], dict):
        raise ErroAPINarrativa("O provedor retornou uma resposta inválida. Nenhum texto foi substituído.")
    escolha = escolhas[0]
    mensagem = escolha.get("message")
    if not isinstance(mensagem, dict):
        raise ErroAPINarrativa("O provedor retornou uma resposta inválida. Nenhum texto foi substituído.")
    if mensagem.get("refusal") or escolha.get("finish_reason") == "content_filter":
        raise ErroAPINarrativa("O provedor recusou a geração deste conteúdo. Nenhum texto foi substituído.")
    if escolha.get("finish_reason") == "length":
        raise ErroAPINarrativa("O provedor interrompeu a geração por limite de tamanho. Solicite uma nova versão.")
    if escolha.get("finish_reason") != "stop":
        raise ErroAPINarrativa("O provedor não concluiu o texto solicitado. Nenhum texto foi substituído.")
    texto = mensagem.get("content")
    if not isinstance(texto, str) or not texto.strip():
        raise ErroAPINarrativa("O provedor retornou um texto vazio ou inválido. Nenhum texto foi substituído.")
    return texto.strip()


def gerar_texto(*, provedor: str = PROVEDOR_PADRAO, modelo: str = MODELO_PADRAO, **contexto) -> str:
    """Faça uma única chamada HTTPS e devolva somente o texto completo gerado."""
    provedor = _provedor_valido(provedor)
    if not isinstance(modelo, str) or not modelo.strip() or len(modelo) > 200:
        raise ErroAPINarrativa("Informe um modelo válido para a geração.")
    chave = os.environ.get(VARIAVEL_CHAVE, "").strip()
    if not chave:
        raise ErroAPINarrativa(f"Configure {VARIAVEL_CHAVE} no ambiente do servidor antes de gerar o texto.")
    if not chave.isascii() or any(caractere.isspace() or ord(caractere) < 33 for caractere in chave):
        raise ErroAPINarrativa(f"A configuração de {VARIAVEL_CHAVE} é inválida. Verifique a credencial no ambiente.")
    try:
        mensagens = construir_mensagens(**contexto)
    except TypeError:
        raise ErroAPINarrativa("O contexto da geração está incompleto ou é inválido.") from None
    corpo = json.dumps({
        "model": modelo.strip(), "messages": mensagens, "stream": False,
        "n": 1,
    }, ensure_ascii=False).encode("utf-8")
    if len(corpo) > LIMITE_PEDIDO_BYTES:
        raise ErroAPINarrativa("O contexto da narrativa excedeu o tamanho permitido para geração.")
    pedido = Request(_ENDPOINTS[provedor], data=corpo, method="POST", headers={
        "Authorization": f"Bearer {chave}", "Content-Type": "application/json", "Accept": "application/json",
    })
    # HTTPSHandler padrão preserva a validação TLS do Python. Redirecionamentos
    # são recusados antes de criar um novo pedido com Authorization.
    cliente = build_opener(_SemRedirecionamento())
    try:
        with cliente.open(pedido, timeout=TIMEOUT_SEGUNDOS) as resposta:
            dados = resposta.read(LIMITE_RESPOSTA_BYTES + 1)
    except HTTPError as erro:
        erro.close()
        raise _erro_http(erro.code) from None
    except (socket.timeout, TimeoutError):
        raise ErroAPINarrativa("O provedor demorou demais para responder. Solicite a geração novamente.") from None
    except URLError as erro:
        if isinstance(erro.reason, (socket.timeout, TimeoutError)):
            raise ErroAPINarrativa("O provedor demorou demais para responder. Solicite a geração novamente.") from None
        raise ErroAPINarrativa("Não foi possível conectar ao provedor. Verifique a conexão e tente novamente.") from None
    except OSError:
        raise ErroAPINarrativa("Não foi possível conectar ao provedor. Verifique a conexão e tente novamente.") from None
    return _interpretar_resposta(dados)
