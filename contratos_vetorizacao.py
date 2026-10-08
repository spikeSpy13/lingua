"""Tipos, integridade binária e perfis verificáveis da etapa 09 (stdlib)."""
from copy import deepcopy
import base64
import hashlib
import json
import math
import re
import struct


class ErroVetorizacao(ValueError):
    """Contrato vetorial inválido."""


class ErroEntrada(ErroVetorizacao):
    """Origem inválida ou não pronta."""


class ErroConfiguracao(ErroVetorizacao):
    """Configuração incompatível ou inválida."""


class ErroModelo(ErroVetorizacao):
    """Modelo, revisão ou dependência indisponível."""


class ErroInferencia(ErroVetorizacao):
    """O gerador falhou ou produziu dados inválidos."""


class ErroLimite(ErroVetorizacao):
    """Limite explícito excedido, sem truncamento."""


def exigir(condicao, mensagem):
    if not condicao:
        raise ErroVetorizacao(mensagem)


def canonico(valor):
    return json.dumps(valor, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def sha256(texto):
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def hash_json(valor):
    return sha256(canonico(valor))


def json_estrito(valor):
    def visitar(v, ancestrais):
        if v is None or type(v) in (str, bool, int):
            return
        if type(v) is float:
            exigir(math.isfinite(v), "Números JSON precisam ser finitos.")
            return
        exigir(type(v) in (dict, list), "O registro deve conter somente tipos JSON.")
        exigir(id(v) not in ancestrais, "JSON não pode conter ciclos.")
        ancestrais.add(id(v))
        if type(v) is dict:
            exigir(all(type(k) is str for k in v), "Chaves JSON devem ser textuais.")
            for filho in v.values():
                visitar(filho, ancestrais)
        else:
            for filho in v:
                visitar(filho, ancestrais)
        ancestrais.remove(id(v))
    try:
        visitar(valor, set())
        canonico(valor).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError) as erro:
        if isinstance(erro, ErroVetorizacao):
            raise
        raise ErroVetorizacao("Dados não serializáveis em JSON UTF-8 estrito.") from erro


def identificador(valor, nome):
    exigir(type(valor) is str and bool(valor.strip()), f"{nome} deve ser uma string não vazia.")
    return valor


def revisao_fixa(valor, nome="revisao"):
    if type(valor) is not str or re.fullmatch(r"[0-9a-f]{40}", valor) is None:
        raise ErroConfiguracao(f"{nome} deve ser um commit hexadecimal imutável de 40 caracteres.")
    return valor


def configuracao_vetorizacao(valor=None, *, limite_tokens=512):
    valor = {} if valor is None else valor
    if type(valor) is not dict:
        raise ErroConfiguracao("configuracao deve ser um objeto JSON.")
    try:
        json_estrito(valor)
    except ErroVetorizacao as erro:
        raise ErroConfiguracao("Configuração precisa conter tipos e chaves JSON válidos.") from erro
    permitidos = {"perfil", "max_tokens", "tamanho_lote", "agregar"}
    if set(valor) - permitidos:
        raise ErroConfiguracao("Campo de configuração não suportado: " + ", ".join(sorted(set(valor) - permitidos)))
    perfil = valor.get("perfil", "recuperacao")
    if type(perfil) is not str or perfil not in {"recuperacao", "similaridade", "consulta"}:
        raise ErroConfiguracao("perfil deve ser recuperacao, similaridade ou consulta.")
    max_tokens = valor.get("max_tokens", min(512, limite_tokens))
    lote = valor.get("tamanho_lote", 4)
    if type(max_tokens) is not int or not 8 <= max_tokens <= limite_tokens:
        raise ErroConfiguracao("max_tokens deve ser inteiro entre 8 e o limite real do modelo.")
    if type(lote) is not int or not 1 <= lote <= 1024:
        raise ErroConfiguracao("tamanho_lote deve ser inteiro entre 1 e 1024.")
    agregar = valor.get("agregar", True)
    if type(agregar) is not bool:
        raise ErroConfiguracao("agregar deve ser um booleano JSON.")
    return {"perfil": perfil, "prefixo": "passage: " if perfil == "recuperacao" else "query: ",
            "max_tokens": max_tokens, "tamanho_lote": lote, "agregar": agregar,
            "pooling": "media_com_mascara", "normalizacao": "l2",
            "politica_textos_longos": {"identificacao": "blocos_contiguos_sem_sobreposicao", "versao": "1.0.0"},
            "agregacao": "media_ponderada_tokens_conteudo_l2"}


def descricao_modelo(valor):
    json_estrito(valor)
    exigir(type(valor) is dict, "Descrição do gerador ausente.")
    for campo in ("identificacao", "revisao", "tokenizador", "dimensao", "limite_tokens", "natureza", "ambiente"):
        exigir(campo in valor, f"Descrição do gerador sem {campo}.")
    identificador(valor["identificacao"], "modelo.identificacao")
    revisao_fixa(valor["revisao"])
    exigir(type(valor["tokenizador"]) is dict and set(valor["tokenizador"]) == {"identificacao", "revisao"},
            "Descrição do tokenizador inválida.")
    identificador(valor["tokenizador"]["identificacao"], "tokenizador.identificacao")
    revisao_fixa(valor["tokenizador"]["revisao"], "tokenizador.revisao")
    exigir(type(valor["dimensao"]) is int and 1 <= valor["dimensao"] <= 8192, "Dimensão do modelo inválida.")
    exigir(type(valor["limite_tokens"]) is int and 8 <= valor["limite_tokens"] <= 32768, "Limite de tokens inválido.")
    exigir(valor["natureza"] in ("inferencia_real", "simulado_teste"), "Natureza do gerador inválida.")
    ambiente = valor["ambiente"]
    exigir(type(ambiente) is dict, "Metadados de ambiente inválidos.")
    for campo in ("backend", "versao_backend", "dispositivo", "precisao_calculo"):
        identificador(ambiente.get(campo), "ambiente." + campo)
    exigir(ambiente["precisao_calculo"] in ("float32", "float16", "bfloat16"), "Precisão de cálculo não suportada.")
    return deepcopy(valor)


def perfil_compatibilidade(modelo, configuracao):
    descricao = {"versao": "1.0.0", "modelo": {k: deepcopy(modelo[k]) for k in (
        "identificacao", "revisao", "tokenizador", "dimensao", "natureza")},
        "geracao": {k: deepcopy(configuracao[k]) for k in (
            "perfil", "prefixo", "pooling", "normalizacao", "max_tokens", "agregar", "politica_textos_longos", "agregacao")},
        "precisao_calculo": modelo["ambiente"]["precisao_calculo"],
        "precisao_pooling": modelo["ambiente"].get("precisao_pooling", "float32"), "formato_vetor": "float32_le"}
    return {"descricao": descricao, "sha256": hash_json(descricao)}


def codificar_vetor(valores, dimensao):
    exigir(type(valores) is list and len(valores) == dimensao, "Dimensão do vetor diverge do modelo.")
    try:
        exigir(all(type(v) in (int, float) and math.isfinite(v) for v in valores), "Vetor contém valores não finitos ou tipos inválidos.")
        dados = struct.pack("<" + "f" * dimensao, *valores)
        arredondados = struct.unpack("<" + "f" * dimensao, dados)
    except (OverflowError, struct.error) as erro:
        raise ErroVetorizacao("Vetor não representável em float32.") from erro
    exigir(all(math.isfinite(v) for v in arredondados), "Vetor não finito após conversão float32.")
    exigir(abs(math.sqrt(math.fsum(v * v for v in arredondados)) - 1.0) <= 1e-5, "O vetor precisa ter norma L2 unitária.")
    return {"formato": "float32_le", "dimensao": dimensao, "bytes": len(dados),
            "sha256_bytes": hashlib.sha256(dados).hexdigest(), "base64": base64.b64encode(dados).decode("ascii")}


def decodificar_vetor(armazenamento, dimensao):
    exigir(type(armazenamento) is dict and set(armazenamento) == {"formato", "dimensao", "bytes", "sha256_bytes", "base64"},
            "Armazenamento vetorial inválido.")
    exigir(armazenamento["formato"] == "float32_le" and type(armazenamento["dimensao"]) is int
            and armazenamento["dimensao"] == dimensao and type(armazenamento["bytes"]) is int
            and armazenamento["bytes"] == dimensao * 4, "Formato, dimensão ou tamanho binário inconsistente.")
    exigir(type(armazenamento["base64"]) is str, "Payload vetorial deve ser base64 textual.")
    try:
        dados = base64.b64decode(armazenamento["base64"], validate=True)
    except (ValueError, UnicodeError) as erro:
        raise ErroVetorizacao("Payload base64 inválido.") from erro
    exigir(len(dados) == dimensao * 4, "Tamanho binário do vetor incorreto.")
    valores = list(struct.unpack("<" + "f" * dimensao, dados))
    exigir(canonico(codificar_vetor(valores, dimensao)) == canonico(armazenamento), "Hash ou bytes do vetor inconsistentes.")
    return valores
