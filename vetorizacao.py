"""Etapa 09: geração injetável, origem integral e validação sem inferência.

O JSON portátil contém bytes float32 em base64; a persistência extrai BLOBs.
Hashes atestam integridade e associações, não a qualidade semântica do modelo.
"""
from copy import deepcopy
from datetime import datetime, timezone
import math
from uuid import uuid4

from contratos_vetorizacao import (
    ErroVetorizacao, ErroEntrada, ErroConfiguracao, ErroModelo, ErroInferencia, ErroLimite,
    canonico, sha256, hash_json, json_estrito, exigir, identificador,
    configuracao_vetorizacao, descricao_modelo, perfil_compatibilidade,
    codificar_vetor, decodificar_vetor,
)
from preparacao import mapear_intervalos
from unidades_contexto import ErroContexto, validar_unidades_contexto

SCHEMA_VERSION = "1.1.0"
SCHEMA_VERSIONS = ("1.0.0", SCHEMA_VERSION)
MODULO_VERSION = "1.1.0"
ETAPA = "09_vetorizacao"
MAX_REPRESENTACOES = 25_000
MAX_BLOCOS = 50_000
MAX_TOKENS_TOTAL = 2_000_000
MAX_BYTES_VETORES = 256 * 1024 * 1024
MAX_CARACTERES_ENTRADAS = 32 * 1024 * 1024


def _data(valor, *, gerar=False):
    if gerar and valor is None:
        valor = datetime.now(timezone.utc).isoformat()
    if gerar and isinstance(valor, datetime):
        valor = valor.isoformat()
    exigir(type(valor) is str, "registrado_em deve ser ISO 8601 com fuso.")
    try:
        instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        exigir(instante.utcoffset() is not None, "registrado_em precisa de fuso horário.")
    except (ValueError, OverflowError) as erro:
        if isinstance(erro, ErroVetorizacao):
            raise
        raise ErroVetorizacao("registrado_em inválido.") from erro
    return valor


def _fonte(contexto):
    try:
        relatorio = validar_unidades_contexto(contexto)
    except (ErroContexto, TypeError, KeyError, RecursionError) as erro:
        raise ErroEntrada(f"Origem da etapa 08 inválida: {erro}") from erro
    if relatorio.get("pronto_para_etapa_09") is not True:
        raise ErroEntrada("A execução contextual não está pronta para a etapa 09.")
    return contexto["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]


def _limites():
    return {"max_representacoes": MAX_REPRESENTACOES, "max_blocos": MAX_BLOCOS,
            "max_tokens_total": MAX_TOKENS_TOTAL, "max_bytes_vetores": MAX_BYTES_VETORES,
            "max_caracteres_entradas": MAX_CARACTERES_ENTRADAS}


def _alvos(contexto, preparacao, execucao_id, *, incluir_paragrafos=True):
    resultado = []
    for unidade in contexto["unidades"]:
        for tipo in ("foco", "janela"):
            bloco = unidade[tipo]
            resultado.append({"tipo": tipo, "unidade_id": unidade["id"],
                              "periodo_foco_id": unidade["periodo_foco_id"],
                              "janela_logica_id": unidade["janela_logica_id"] if tipo == "janela" else None,
                              "campo": tipo + ".texto", "texto": bloco["texto"],
                              "sha256_texto": bloco["sha256_texto"],
                              "trabalho": deepcopy(bloco["trabalho"]), "original": deepcopy(bloco["original"])})
    if incluir_paragrafos:
        segmentacao = contexto["regras"]["analise"]["anotacao"]["segmentacao"]
        for paragrafo in segmentacao["paragrafos"]:
            intervalo = paragrafo["trabalho"]
            texto = preparacao["trabalho"]["texto"][intervalo["inicio"]:intervalo["fim"]]
            resultado.append({"tipo": "paragrafo", "paragrafo_id": paragrafo["id"],
                              "unidade_id": None, "periodo_foco_id": None,
                              "janela_logica_id": None, "campo": "segmentacao.paragrafos.texto",
                              "texto": texto, "sha256_texto": sha256(texto),
                              "trabalho": deepcopy(intervalo), "original": deepcopy(paragrafo["original"])})
    resultado.append({"tipo": "documento", "unidade_id": None, "periodo_foco_id": None,
                      "janela_logica_id": None, "campo": "preparacao.trabalho.texto",
                      "texto": preparacao["trabalho"]["texto"], "sha256_texto": preparacao["trabalho"]["sha256"],
                      "trabalho": {"inicio": 0, "fim": len(preparacao["trabalho"]["texto"])},
                      "original": {"inicio": 0, "fim": len(preparacao["original"]["texto"])}})
    if len(resultado) > MAX_REPRESENTACOES:
        raise ErroLimite("Quantidade de representações excede o limite; nenhuma origem foi omitida.")
    if sum(len(a["texto"]) for a in resultado) > MAX_CARACTERES_ENTRADAS:
        raise ErroLimite("Textos excedem o limite contextual da etapa 09; nenhum texto foi truncado.")
    for i, alvo in enumerate(resultado):
        alvo.update(id=f"{execucao_id}:representacao:{i}", ordem=i)
    return resultado


def _entrada(texto, prefixo, tokenizacao):
    exigir(type(tokenizacao) is dict and set(tokenizacao) == {"input_ids", "offset_mapping", "special_tokens_mask"},
            "Tokenização deve fornecer IDs, offsets e máscara de tokens especiais.")
    ids, offsets, especiais = (tokenizacao[k] for k in ("input_ids", "offset_mapping", "special_tokens_mask"))
    exigir(type(ids) is list and bool(ids) and all(type(v) is int and 0 <= v < 2 ** 63 for v in ids), "IDs de tokens inválidos.")
    exigir(type(offsets) is list and type(especiais) is list and len(ids) == len(offsets) == len(especiais),
            "Comprimentos da tokenização inconsistentes.")
    efetivo = prefixo + texto
    conteudo = 0
    anterior = 0
    for par, especial in zip(offsets, especiais):
        exigir(type(par) is list and len(par) == 2 and all(type(v) is int for v in par), "Offsets devem ser pares Unicode inteiros.")
        inicio, fim = par
        exigir(0 <= inicio <= fim <= len(efetivo), "Offset fora da entrada efetiva.")
        exigir(type(especial) is int and especial in (0, 1), "Máscara de especiais inválida.")
        if especial:
            exigir(par == [0, 0], "Tokens especiais precisam de offset vazio.")
        elif fim > inicio:
            exigir(inicio >= anterior, "Offsets de tokens não seguem a ordem textual.")
            # Tokenizadores podem compartilhar offsets de um caractere em
            # subpeças; não exigimos que os intervalos sejam disjuntos.
            anterior = inicio
            if fim > len(prefixo) and efetivo[max(inicio, len(prefixo)):fim].strip():
                conteudo += 1
    exigir(not texto.strip() or conteudo > 0,
            "Tokenização omitiu todo o conteúdo canônico; ausência de vetor não pode ocultar texto.")
    return {"texto": efetivo, "input_ids": ids[:], "offset_mapping": deepcopy(offsets),
            "special_tokens_mask": especiais[:], "sha256_texto": sha256(efetivo),
            "sha256_token_ids": hash_json(ids), "tokens_total": len(ids), "tokens_conteudo": conteudo}


def _tokenizar(gerador, texto, prefixo):
    try:
        return _entrada(texto, prefixo, gerador.tokenizar(texto, prefixo))
    except (ErroModelo, ErroInferencia, ErroLimite):
        raise
    except ErroVetorizacao as erro:
        raise ErroInferencia(f"Tokenização inválida: {erro}") from erro
    except Exception as erro:
        raise ErroInferencia("O gerador falhou ao tokenizar a entrada sem truncamento.") from erro


def _dividir(texto, gerador, configuracao):
    if not texto.strip():
        return
    prefixo, max_tokens = configuracao["prefixo"], configuracao["max_tokens"]
    entrada = _tokenizar(gerador, texto, prefixo)
    if entrada["tokens_total"] <= max_tokens:
        yield (0, len(texto), entrada)
        return
    quantidade, inicio = 0, 0
    while inicio < len(texto):
        # Cada candidato é um recorte exato e é recontado. A busca não
        # presume equivalência entre caracteres e tokens, nem detokeniza.
        # A janela inicial limita o trabalho por bloco. Se couber, ela cresce
        # até encontrar um limite, evitando retokenizar o restante inteiro
        # do documento em todas as iterações.
        baixo, alto, melhor = inicio + 1, min(len(texto), inicio + max_tokens * 4), None
        while True:
            candidato = _tokenizar(gerador, texto[inicio:alto], prefixo)
            if candidato["tokens_total"] > max_tokens:
                alto -= 1
                break
            melhor = (alto, candidato)
            if alto == len(texto):
                baixo = alto + 1
                break
            baixo = alto + 1
            alto = min(len(texto), inicio + 2 * (alto - inicio))
        while baixo <= alto:
            fim = (baixo + alto) // 2
            candidato = _tokenizar(gerador, texto[inicio:fim], prefixo)
            if candidato["tokens_total"] <= max_tokens:
                melhor = (fim, candidato)
                baixo = fim + 1
            else:
                alto = fim - 1
        if melhor is None:
            raise ErroLimite("Um caractere com prefixo e especiais excede o orçamento; nenhum conteúdo foi descartado.")
        fim, candidato = melhor
        quantidade += 1
        if quantidade > MAX_BLOCOS:
            raise ErroLimite("Quantidade de blocos excede o limite antes da inferência.")
        yield (inicio, fim, candidato)
        inicio = fim


def _artefato_id(entrada, geracao):
    return hash_json({"versao": "1.0.0", "geracao_sha256": geracao["sha256"],
                      "entrada_sha256": entrada["sha256_texto"], "token_ids_sha256": entrada["sha256_token_ids"]})


def _agregar(blocos, artefatos, dimensao):
    ativos = [b for b in blocos if b["peso_tokens"] > 0]
    if not ativos:
        return None
    linhas = [(b["peso_tokens"], decodificar_vetor(artefatos[b["artefato_id"]]["armazenamento"], dimensao)) for b in ativos]
    total = sum(peso for peso, _ in linhas)
    valores = [math.fsum(peso * vetor[i] for peso, vetor in linhas) / total for i in range(dimensao)]
    norma = math.sqrt(math.fsum(v * v for v in valores))
    exigir(norma > 0 and math.isfinite(norma), "Agregação sem norma válida; vetor zero não foi fabricado.")
    return codificar_vetor([v / norma for v in valores], dimensao)


def _concluir_representacao(alvo, blocos, artefatos, contexto, preparacao, compatibilidade, configuracao, dimensao):
    ativos = [b for b in blocos if b["peso_tokens"] > 0]
    if not ativos:
        construcao, vetor = "sem_conteudo", None
    elif len(blocos) == 1:
        construcao, vetor = "direta", deepcopy(artefatos[ativos[0]["artefato_id"]]["armazenamento"])
    elif configuracao["agregar"]:
        construcao, vetor = "agregada", _agregar(blocos, artefatos, dimensao)
    else:
        construcao, vetor = "blocos", None
    selecao = {"versao": "1.0.0", "documento_id": contexto["documento_id"],
               "sha256_documento_original": preparacao["original"]["sha256"],
               "tipo": alvo["tipo"], "campo": alvo["campo"], "original": deepcopy(alvo["original"]),
               "sha256_texto": alvo["sha256_texto"], "janela_logica_id": alvo["janela_logica_id"],
               "compatibilidade_sha256": compatibilidade["sha256"],
               "plano": [{"relativo": b["relativo"], "sha256_texto": b["sha256_texto"],
                         "sha256_entrada": b["entrada_modelo"]["sha256_texto"],
                         "sha256_token_ids": b["entrada_modelo"]["sha256_token_ids"], "peso_tokens": b["peso_tokens"]} for b in blocos]}
    return {**deepcopy(alvo), "construcao": construcao, "blocos": blocos, "vetor": vetor,
            "selecao_logica": selecao, "representacao_logica_id": hash_json(selecao)}


def _relatorio(modelo, representacoes, artefatos):
    reais = modelo["natureza"] == "inferencia_real"
    return {"estado": "valido", "pronto_para_uso": reais and any(r["vetor"] is not None or any(b["artefato_id"] for b in r["blocos"]) for r in representacoes),
            "inferencia_real": reais, "cobertura": {
                "representacoes_total": len(representacoes), "blocos_total": sum(len(r["blocos"]) for r in representacoes),
                "artefatos_total": len(artefatos), "gerados": sum(a["origem_calculo"] == "gerado" for a in artefatos),
                "reutilizados": sum(a["origem_calculo"] == "reutilizado" for a in artefatos),
                "sem_conteudo": sum(r["construcao"] == "sem_conteudo" for r in representacoes)},
            "verificacoes": ["origem08_validada", "campos_hashes_e_associacoes", "prefixos_ids_e_offsets", "blocos_cobertura_e_pesos", "bytes_dimensao_finitude_norma", "perfis_e_identidades", "agregacao_recalculada_sem_modelo"],
            "limites": ["Integridade estrutural não comprova qualidade semântica nem autentica a inferência.",
                        "A média agregada perde ordem e relações globais entre blocos.",
                        "Query e passage são papéis complementares na recuperação; equivalência exige perfis iguais."]}


def vetorizar_unidades_contexto(contexto, *, gerador, configuracao=None, cache=None, execucao_id=None, registrado_em=None):
    """Gere sobre uma cópia da fonte validada, sem acesso a banco ou arquivos."""
    preparacao = _fonte(contexto)
    contexto = deepcopy(contexto)
    preparacao = contexto["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]
    modelo = descricao_modelo(gerador.descrever())
    config = configuracao_vetorizacao(configuracao, limite_tokens=modelo["limite_tokens"])
    identificacao = str(uuid4()) if execucao_id is None else identificador(execucao_id, "execucao_id")
    exigir(identificacao != contexto["execucao_id"], "A etapa 09 precisa de ID próprio.")
    data = _data(registrado_em, gerar=True)
    compatibilidade = perfil_compatibilidade(modelo, config)
    descricao_geracao = {"compatibilidade": compatibilidade, "ambiente": modelo["ambiente"],
                        "tamanho_lote": config["tamanho_lote"], "modulo_version": MODULO_VERSION}
    geracao = {"descricao": descricao_geracao, "sha256": hash_json(descricao_geracao)}
    alvos = _alvos(contexto, preparacao, identificacao)
    cache = {} if cache is None else cache
    exigir(type(cache) is dict, "cache deve mapear IDs para artefatos verificáveis.")
    planos, pendentes, entradas_unicas = [], [], {}
    tokens_total, blocos_total, bytes_total = 0, 0, 0
    for alvo in alvos:
        blocos = []
        for i, (inicio, fim, entrada) in enumerate(_dividir(alvo["texto"], gerador, config)):
            blocos_total += 1
            tokens_total += entrada["tokens_total"]
            if blocos_total > MAX_BLOCOS or tokens_total > MAX_TOKENS_TOTAL:
                raise ErroLimite("Blocos ou tokens excedem o limite antes de qualquer inferência.")
            artefato_id = _artefato_id(entrada, geracao) if entrada["tokens_conteudo"] else None
            if artefato_id is not None and artefato_id not in entradas_unicas:
                entradas_unicas[artefato_id] = entrada
                bytes_total += modelo["dimensao"] * 4
            bloco = {"id": f"{alvo['id']}:bloco:{i}", "ordem": i, "relativo": {"inicio": inicio, "fim": fim},
                     "trabalho": {"inicio": alvo["trabalho"]["inicio"] + inicio, "fim": alvo["trabalho"]["inicio"] + fim},
                     "original": None, "texto": alvo["texto"][inicio:fim], "sha256_texto": sha256(alvo["texto"][inicio:fim]),
                     "entrada_modelo": entrada, "peso_tokens": entrada["tokens_conteudo"], "artefato_id": artefato_id}
            pendentes.append(bloco)
            blocos.append(bloco)
        if any(b["peso_tokens"] > 0 for b in blocos) and (len(blocos) == 1 or config["agregar"]):
            bytes_total += modelo["dimensao"] * 4
        planos.append(blocos)
    if bytes_total > MAX_BYTES_VETORES:
        raise ErroLimite("Bytes vetoriais excedem o limite antes da inferência.")
    originais = mapear_intervalos(preparacao, [(b["trabalho"]["inicio"], b["trabalho"]["fim"]) for b in pendentes])
    for bloco, original in zip(pendentes, originais):
        bloco["original"] = original
    artefatos, novos = {}, []
    for chave, entrada in entradas_unicas.items():
        if chave in cache:
            candidato = deepcopy(cache[chave])
            json_estrito(candidato)
            exigir(type(candidato) is dict and set(candidato) == {"id", "entrada_modelo", "armazenamento", "origem_calculo"}, "Artefato de cache inválido.")
            exigir(candidato["id"] == chave and canonico(candidato["entrada_modelo"]) == canonico(entrada), "Cache não corresponde à entrada e configuração solicitadas.")
            decodificar_vetor(candidato["armazenamento"], modelo["dimensao"])
            candidato["origem_calculo"] = "reutilizado"
            artefatos[chave] = candidato
        else:
            novos.append((chave, entrada))
    for inicio in range(0, len(novos), config["tamanho_lote"]):
        lote = novos[inicio:inicio + config["tamanho_lote"]]
        try:
            valores = gerador.gerar([deepcopy(e) for _, e in lote])
            exigir(type(valores) is list and len(valores) == len(lote), "O gerador não entregou um vetor por entrada.")
            for (chave, entrada), vetor in zip(lote, valores):
                artefatos[chave] = {"id": chave, "entrada_modelo": entrada,
                                    "armazenamento": codificar_vetor(vetor, modelo["dimensao"]), "origem_calculo": "gerado"}
        except (ErroModelo, ErroInferencia, ErroLimite):
            raise
        except Exception as erro:
            raise ErroInferencia("Falha de inferência ou vetor inválido; nenhum resultado parcial foi entregue.") from erro
    ordenados = [artefatos[k] for k in entradas_unicas]
    try:
        representacoes = [_concluir_representacao(a, b, artefatos, contexto, preparacao, compatibilidade, config, modelo["dimensao"])
                          for a, b in zip(alvos, planos)]
    except ErroVetorizacao as erro:
        raise ErroInferencia(f"Falha ao construir ou agregar vetores: {erro}") from erro
    registro = {"schema_version": SCHEMA_VERSION, "etapa": ETAPA, "execucao_id": identificacao,
                "contexto_execucao_id": contexto["execucao_id"], "documento_id": contexto["documento_id"], "registrado_em": data,
                "contexto": contexto, "contexto_sha256": hash_json(contexto), "coordenadas": deepcopy(contexto["coordenadas"]),
                "modelo": modelo, "configuracao": config, "compatibilidade": compatibilidade, "geracao": geracao,
                "processamento": {"modulo": {"nome": "vetorizacao", "versao": MODULO_VERSION}, "limites_recursos": _limites()},
                "representacoes": representacoes, "artefatos": ordenados,
                "validacao": _relatorio(modelo, representacoes, ordenados)}
    json_estrito(registro)
    # Os campos são derivados de uma fonte validada; a API pública confere o
    # JSON arquivado independentemente de gerador ou disponibilidade do modelo.
    return registro


def validar_vetorizacao(registro):
    """Confira o registro inteiro e os vetores sem chamar um gerador."""
    exigir(type(registro) is dict, "Registro vetorial deve ser objeto JSON.")
    json_estrito(registro)
    campos = {"schema_version", "etapa", "execucao_id", "contexto_execucao_id", "documento_id", "registrado_em", "contexto", "contexto_sha256", "coordenadas", "modelo", "configuracao", "compatibilidade", "geracao", "processamento", "representacoes", "artefatos", "validacao"}
    exigir(set(registro) == campos and registro["schema_version"] in SCHEMA_VERSIONS and registro["etapa"] == ETAPA, "Contrato ou versão vetorial não suportado.")
    modulo_version = "1.0.0" if registro["schema_version"] == "1.0.0" else MODULO_VERSION
    contexto = registro["contexto"]
    preparacao = _fonte(contexto)
    identificacao = identificador(registro["execucao_id"], "execucao_id")
    _data(registro["registrado_em"])
    exigir(identificacao != contexto["execucao_id"], "A etapa 09 precisa de ID próprio.")
    exigir(registro["contexto_execucao_id"] == contexto["execucao_id"] and registro["documento_id"] == contexto["documento_id"]
            and registro["contexto_sha256"] == hash_json(contexto) and canonico(registro["coordenadas"]) == canonico(contexto["coordenadas"]), "Origem contextual ou seu hash divergente.")
    modelo = descricao_modelo(registro["modelo"])
    informado = registro["configuracao"]
    exigir(type(informado) is dict and all(k in informado for k in ("perfil", "max_tokens", "tamanho_lote", "agregar")), "Configuração ausente.")
    config = configuracao_vetorizacao({k: informado[k] for k in ("perfil", "max_tokens", "tamanho_lote", "agregar")}, limite_tokens=modelo["limite_tokens"])
    exigir(canonico(config) == canonico(informado), "Configuração ou política adulterada.")
    compatibilidade = perfil_compatibilidade(modelo, config)
    descricao_geracao = {"compatibilidade": compatibilidade, "ambiente": modelo["ambiente"], "tamanho_lote": config["tamanho_lote"], "modulo_version": modulo_version}
    geracao = {"descricao": descricao_geracao, "sha256": hash_json(descricao_geracao)}
    exigir(canonico(registro["compatibilidade"]) == canonico(compatibilidade) and canonico(registro["geracao"]) == canonico(geracao), "Perfis ou assinaturas de geração inconsistentes.")
    exigir(canonico(registro["processamento"]) == canonico({"modulo": {"nome": "vetorizacao", "versao": modulo_version}, "limites_recursos": _limites()}), "Versão ou limites de processamento inconsistentes.")
    alvos = _alvos(contexto, preparacao, identificacao,
                    incluir_paragrafos=registro["schema_version"] != "1.0.0")
    reps = registro["representacoes"]
    lista_artefatos = registro["artefatos"]
    exigir(type(reps) is list and len(reps) == len(alvos) and type(lista_artefatos) is list, "Cobertura de representações incorreta.")
    exigir(len(lista_artefatos) <= MAX_BLOCOS and len(lista_artefatos) * modelo["dimensao"] * 4 <= MAX_BYTES_VETORES,
            "Artefatos excedem limites de recursos.")
    artefatos = {}
    for a in lista_artefatos:
        exigir(type(a) is dict and set(a) == {"id", "entrada_modelo", "armazenamento", "origem_calculo"}, "Formato de artefato inválido.")
        identificador(a["id"], "artefato.id")
        exigir(a["id"] not in artefatos and a["origem_calculo"] in ("gerado", "reutilizado"), "Artefato duplicado ou origem de cálculo inválida.")
        decodificar_vetor(a["armazenamento"], modelo["dimensao"])
        artefatos[a["id"]] = a
    esperadas, pendentes, vistos, conjunto_vistos = [], [], [], set()
    tokens_total, blocos_total, bytes_total = 0, 0, len(artefatos) * modelo["dimensao"] * 4
    for alvo, rep in zip(alvos, reps):
        exigir(type(rep) is dict and type(rep.get("blocos")) is list, "Representação sem blocos válidos.")
        blocos, proximo = rep["blocos"], 0
        exigir(blocos_total + len(blocos) <= MAX_BLOCOS, "Registro excede o limite de blocos.")
        exigir(bool(blocos) or not alvo["texto"].strip(), "Texto com conteúdo sem cobertura de blocos.")
        for i, b in enumerate(blocos):
            exigir(type(b) is dict and set(b) == {"id", "ordem", "relativo", "trabalho", "original", "texto", "sha256_texto", "entrada_modelo", "peso_tokens", "artefato_id"}, "Contrato de bloco inválido.")
            relativo = b["relativo"]
            exigir(type(relativo) is dict and set(relativo) == {"inicio", "fim"} and all(type(v) is int for v in relativo.values()), "Intervalo relativo inválido.")
            inicio, fim = relativo["inicio"], relativo["fim"]
            exigir(inicio == proximo and inicio < fim <= len(alvo["texto"]), "Blocos possuem lacuna, sobreposição ou limite inválido.")
            texto = alvo["texto"][inicio:fim]
            e = b["entrada_modelo"]
            exigir(type(e) is dict and all(k in e for k in ("input_ids", "offset_mapping", "special_tokens_mask")), "Entrada do bloco ausente.")
            entrada = _entrada(texto, config["prefixo"], {k: e[k] for k in ("input_ids", "offset_mapping", "special_tokens_mask")})
            exigir(canonico(e) == canonico(entrada) and entrada["tokens_total"] <= config["max_tokens"], "Entrada, IDs, hash ou limite de tokens inconsistente.")
            artefato_id = _artefato_id(entrada, geracao) if entrada["tokens_conteudo"] else None
            esperado = {"id": f"{alvo['id']}:bloco:{i}", "ordem": i, "relativo": relativo,
                        "trabalho": {"inicio": alvo["trabalho"]["inicio"] + inicio, "fim": alvo["trabalho"]["inicio"] + fim},
                        "original": b["original"], "texto": texto, "sha256_texto": sha256(texto),
                        "entrada_modelo": entrada, "peso_tokens": entrada["tokens_conteudo"], "artefato_id": artefato_id}
            exigir(canonico(b) == canonico(esperado), "Bloco, origem, peso ou identidade adulterados.")
            if artefato_id is not None:
                exigir(artefato_id in artefatos and canonico(artefatos[artefato_id]["entrada_modelo"]) == canonico(entrada), "Associação de artefato incorreta.")
                if artefato_id not in conjunto_vistos:
                    conjunto_vistos.add(artefato_id)
                    vistos.append(artefato_id)
            proximo = fim
            blocos_total += 1
            tokens_total += entrada["tokens_total"]
            exigir(tokens_total <= MAX_TOKENS_TOTAL, "Registro excede o limite de tokens.")
            pendentes.append(b)
        exigir(not blocos or proximo == len(alvo["texto"]), "Blocos não cobrem o texto integral.")
        esperadas.append(_concluir_representacao(alvo, blocos, artefatos, contexto, preparacao, compatibilidade, config, modelo["dimensao"]))
        if any(b["peso_tokens"] > 0 for b in blocos) and (len(blocos) == 1 or config["agregar"]):
            bytes_total += modelo["dimensao"] * 4
    exigir(vistos == [a["id"] for a in lista_artefatos], "Artefatos sem origem, omitidos, duplicados ou reordenados.")
    exigir(blocos_total <= MAX_BLOCOS and tokens_total <= MAX_TOKENS_TOTAL and bytes_total <= MAX_BYTES_VETORES, "Registro excede limites de recursos.")
    originais = mapear_intervalos(preparacao, [(b["trabalho"]["inicio"], b["trabalho"]["fim"]) for b in pendentes])
    exigir(all(canonico(b["original"]) == canonico(o) for b, o in zip(pendentes, originais)), "Mapeamento original dos blocos inconsistente.")
    exigir(canonico(reps) == canonico(esperadas), "Representações, vetores, agregação ou identidade lógica inconsistentes.")
    relatorio = _relatorio(modelo, esperadas, lista_artefatos)
    exigir(canonico(registro["validacao"]) == canonico(relatorio), "Relatório de validação inconsistente.")
    return deepcopy(relatorio)


def consultar_representacao(registro, representacao_id):
    identificador(representacao_id, "representacao_id")
    validar_vetorizacao(registro)
    for rep in registro["representacoes"]:
        if rep["id"] == representacao_id:
            return deepcopy(rep)
    raise ErroVetorizacao("Representação inexistente nesta execução.")


def comparar_compatibilidade(registro, perfil):
    """Equivalência estrita de geração; query/passage são papéis complementares."""
    validar_vetorizacao(registro)
    json_estrito(perfil)
    exigir(type(perfil) is dict and set(perfil) == {"descricao", "sha256"}
            and perfil["sha256"] == hash_json(perfil["descricao"]), "Perfil externo precisa de descrição e hash verificáveis.")
    def diferencas(a, b, caminho=""):
        if type(a) is dict and type(b) is dict:
            return [p for k in sorted(set(a) | set(b)) for p in diferencas(a.get(k), b.get(k), caminho + ("." if caminho else "") + k)]
        return [] if canonico(a) == canonico(b) else [caminho]
    divergencias = diferencas(registro["compatibilidade"]["descricao"], perfil["descricao"])
    return {"compativel": not divergencias, "diferencas": divergencias,
            "criterio": "equivalencia_de_configuracao; query/passage complementares na recuperacao"}
