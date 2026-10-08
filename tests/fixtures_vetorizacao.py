"""Embeddings simulados e origens manuais para testar o contrato da etapa 09.

Nada neste arquivo executa ou representa inferência do multilingual-e5.
O tokenizador conta um token por ponto de código, além do prefixo e dos dois
tokens especiais. Isso dá limites exatos e gabaritos independentes do E5.
"""

from copy import deepcopy
import hashlib
import json
import math

from fixtures_contexto import INSTANTE, construir_regras
from fixtures_regras import CASOS
from unidades_contexto import construir_unidades_contexto


REVISAO = "a" * 40


class GeradorSimulado:
    """Adaptador determinístico destinado exclusivamente aos testes."""

    def __init__(self, dimensao=4, limite_tokens=512, revisao=REVISAO,
                 precisao="float32", dispositivo="cpu"):
        self.dimensao = dimensao
        self.limite_tokens = limite_tokens
        self.revisao = revisao
        self.precisao = precisao
        self.dispositivo = dispositivo
        self.chamadas_tokenizacao = []
        self.chamadas_geracao = []
        self.entradas_geradas = []

    def descrever(self):
        return {
            "identificacao": "fixture_embeddings",
            "revisao": self.revisao,
            "tokenizador": {"identificacao": "fixture_unicode_por_caractere",
                            "revisao": self.revisao},
            "dimensao": self.dimensao,
            "limite_tokens": self.limite_tokens,
            "natureza": "simulado_teste",
            "ambiente": {"backend": "fixture", "versao_backend": "1.0.0",
                         "versao_transformers": None,
                         "dispositivo": self.dispositivo,
                         "precisao_calculo": self.precisao},
        }

    def tokenizar(self, texto, prefixo):
        self.chamadas_tokenizacao.append((texto, prefixo))
        inicio = len(prefixo)
        prefix_id = 100 + int.from_bytes(hashlib.sha256(prefixo.encode("utf-8")).digest()[:2], "big")
        return {
            "input_ids": [1, prefix_id] + [100_000 + ord(c) for c in texto] + [2],
            "offset_mapping": [[0, 0], [0, inicio]] +
                              [[inicio + i, inicio + i + 1] for i in range(len(texto))] + [[0, 0]],
            "special_tokens_mask": [1, 0] + [0] * len(texto) + [1],
        }

    def gerar(self, lote):
        self.chamadas_geracao.append(deepcopy(lote))
        self.entradas_geradas.extend(deepcopy(lote))
        vetores = []
        for entrada in lote:
            serializado = json.dumps(entrada["input_ids"], ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False)
            digest = hashlib.sha256(serializado.encode("utf-8")).digest()
            valores = [float(1 + int.from_bytes(digest[2 * i:2 * i + 2], "big"))
                       for i in range(self.dimensao)]
            norma = math.sqrt(sum(v * v for v in valores))
            vetores.append([v / norma for v in valores])
        return vetores


def construir_contexto(*, normalizar=False, pequeno=False, texto=None,
                       linhas=None, execucao_id="contexto-fixture-09",
                       registrado_em=INSTANTE, **opcoes):
    """Constrói uma fonte válida sem executar um modelo linguístico."""
    argumentos = {"normalizar": normalizar}
    if pequeno:
        argumentos.update(texto=CASOS["nominal"]["texto"], linhas=CASOS["nominal"]["tokens"])
    if texto is not None:
        argumentos["texto"] = texto
    if linhas is not None:
        argumentos["linhas"] = linhas
    regras = construir_regras(**argumentos)
    return construir_unidades_contexto(regras, execucao_id=execucao_id,
                                       registrado_em=registrado_em, **opcoes)
