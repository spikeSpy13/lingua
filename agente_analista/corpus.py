"""Índice de Freud preservado, conferido uma vez e compartilhado entre buscas."""

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

import numpy as np


class ErroCorpus(ValueError):
    """Índice ausente ou inconsistente; pode ser exibido pela página local."""


def _sha(texto):
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _exigir(condicao, mensagem):
    if not condicao:
        raise ErroCorpus(mensagem)


class Corpus:
    """As linhas do mmap mantêm exatamente a ordem de ``fragmentos.jsonl``.

    Os hashes provam a integridade textual, não a pertinência de interpretações.
    Sobreposições idênticas são unidas. Lacunas ou sobreposições conflitantes
    bloqueiam o índice em vez de inventar contexto.
    """

    def __init__(self, pasta: Path):
        self.pasta = Path(pasta)
        try:
            self._carregar()
        except ErroCorpus:
            raise
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ErroCorpus("Não foi possível ler o corpus. Confira manifesto.json, "
                             "fragmentos.jsonl e os vetores em agente_analista/data/Vetor.") from exc

    def _carregar(self):
        self.manifesto = json.loads((self.pasta / "manifesto.json").read_text(encoding="utf-8"))
        m = self.manifesto
        _exigir(isinstance(m, dict), "manifesto.json deve conter um objeto JSON.")
        _exigir(isinstance(m.get("modelo"), str) and bool(m["modelo"]), "O manifesto precisa declarar o modelo E5.")
        _exigir(isinstance(m.get("revisao"), str) and re.fullmatch(r"[0-9a-f]{40}", m["revisao"]),
                "O manifesto precisa declarar uma revisão fixa de 40 caracteres.")
        for campo in ("dimensao", "tokens_maximo", "fragmentos", "blocos"):
            _exigir(type(m.get(campo)) is int and m[campo] > 0, f"O manifesto precisa declarar {campo} como inteiro positivo.")
        _exigir(type(m.get("margem_tokens")) is int and 0 <= m["margem_tokens"] < m["tokens_maximo"],
                "A margem de tokens do manifesto é inválida.")
        _exigir(m.get("normalizacao") == "L2" and m.get("tipo_vetor") == "float32",
                "Este índice exige vetores float32 com normalização L2.")
        _exigir(m.get("prefixo_passagens") == "passage: " and m.get("prefixo_consultas") == "query: ",
                "Os prefixos E5 do índice devem ser 'passage: ' e 'query: '.")
        for campo in ("referencias_arquivo", "vetores_arquivo"):
            nome = m.get(campo)
            _exigir(isinstance(nome, str) and nome not in ("", ".", "..") and Path(nome).name == nome,
                    f"O arquivo declarado em {campo} deve estar diretamente na pasta do corpus.")

        self.fragmentos = []
        self.por_id = {}
        self.indice_por_id = {}
        grupos = defaultdict(list)
        with (self.pasta / m["referencias_arquivo"]).open(encoding="utf-8") as arquivo:
            for numero, linha in enumerate(arquivo, 1):
                _exigir(bool(linha.strip()), f"Linha vazia no índice de fragmentos: {numero}.")
                f = json.loads(linha)
                self._validar_fragmento(f, numero)
                _exigir(f["id"] not in self.por_id, f"Identificador duplicado no índice, linha {numero}.")
                self.indice_por_id[f["id"]] = len(self.fragmentos)
                self.fragmentos.append(f)
                self.por_id[f["id"]] = f
                grupos[f["bloco_id"]].append(f)
        _exigir(len(self.fragmentos) == m["fragmentos"],
                "A quantidade de fragmentos diverge do manifesto. Restaure os arquivos do mesmo índice.")
        _exigir(len(grupos) == m["blocos"], "A quantidade de blocos diverge do manifesto.")
        self.blocos = {identificador: self._reconstruir(identificador, fs)
                       for identificador, fs in grupos.items()}

        self.vetores = np.load(self.pasta / m["vetores_arquivo"], mmap_mode="r", allow_pickle=False)
        _exigir(self.vetores.dtype == np.dtype("float32"), "A matriz do corpus deve usar float32.")
        _exigir(self.vetores.shape == (len(self.fragmentos), m["dimensao"]),
                "As linhas ou dimensões dos vetores divergem dos fragmentos e do manifesto.")
        # Conferência em blocos pequenos sem copiar a matriz inteira em RAM.
        for inicio in range(0, len(self.fragmentos), 512):
            lote = self.vetores[inicio:inicio + 512]
            _exigir(bool(np.isfinite(lote).all()), "O corpus contém vetores com NaN ou infinito. Restaure o índice.")
            normas = np.linalg.norm(lote, axis=1)
            _exigir(bool(np.allclose(normas, 1.0, atol=2e-5, rtol=0)),
                    "O corpus contém vetores sem normalização L2. Restaure o índice original.")

    def _validar_fragmento(self, f, numero):
        _exigir(isinstance(f, dict), f"Fragmento inválido na linha {numero}.")
        for campo in ("id", "bloco_id", "obra", "texto", "cabecalho", "texto_origem_sha256", "entrada_sha256"):
            _exigir(isinstance(f.get(campo), str) and bool(f[campo]),
                    f"Campo {campo} inválido no fragmento da linha {numero}.")
        _exigir(isinstance(f.get("secao"), str), f"Seção inválida no fragmento da linha {numero}.")
        _exigir(type(f.get("volume")) is int and f["volume"] > 0, f"Volume inválido na linha {numero}.")
        for campo in ("inicio", "fim", "fragmento_no_bloco", "tokens_entrada"):
            _exigir(type(f.get(campo)) is int and f[campo] >= 0,
                    f"Campo {campo} inválido na linha {numero}.")
        _exigir(f["fim"] > f["inicio"] and f["fim"] - f["inicio"] == len(f["texto"]),
                f"Texto e posições Unicode divergem na linha {numero}.")
        _exigir(0 < f["tokens_entrada"] <= self.manifesto["tokens_maximo"] - self.manifesto["margem_tokens"],
                f"Quantidade de tokens incompatível com o manifesto na linha {numero}.")
        _exigir(isinstance(f.get("paginas"), list) and all(type(p) is int and p > 0 for p in f["paginas"]),
                f"Páginas inválidas na linha {numero}.")
        _exigir(re.fullmatch(r"[0-9a-f]{64}", f["texto_origem_sha256"]) is not None,
                f"Hash de origem inválido na linha {numero}.")
        entrada = self.manifesto["prefixo_passagens"] + f["cabecalho"] + "\n" + f["texto"]
        _exigir(_sha(entrada) == f["entrada_sha256"],
                f"Hash da entrada E5 diverge na linha {numero}. Restaure o índice original.")

    def _reconstruir(self, identificador, fragmentos):
        ordenados = sorted(fragmentos, key=lambda f: (f["inicio"], f["fragmento_no_bloco"]))
        primeiro = ordenados[0]
        texto = ""
        campos_comuns = ("obra", "volume", "secao", "cabecalho", "texto_origem_sha256")
        for ordem, f in enumerate(ordenados):
            _exigir(all(f[c] == primeiro[c] for c in campos_comuns),
                    "Metadados inconsistentes entre fragmentos do mesmo bloco.")
            _exigir(f["fragmento_no_bloco"] == ordem, "A ordem dos fragmentos no bloco é inconsistente.")
            _exigir(f["inicio"] <= len(texto),
                    "Há lacuna entre fragmentos de um bloco. Restaure o índice original.")
            sobreposicao = min(f["fim"], len(texto)) - f["inicio"]
            _exigir(texto[f["inicio"]:f["inicio"] + sobreposicao] == f["texto"][:sobreposicao],
                    "Há sobreposição conflitante entre fragmentos de um bloco. Restaure o índice original.")
            texto += f["texto"][sobreposicao:]
        _exigir(_sha(texto) == primeiro["texto_origem_sha256"],
                "O hash do bloco reconstruído diverge da origem. Restaure o índice original.")
        titulo = primeiro["cabecalho"]
        if primeiro["secao"] and titulo.endswith(" — " + primeiro["secao"]):
            titulo = titulo[:-(len(primeiro["secao"]) + 3)]
        paginas = sorted({p for f in ordenados for p in f["paginas"]})
        pendencias = ["edição", "tradutor", "editora", "ano da edição", "correspondência entre páginas do PDF e impressas"]
        if not primeiro["secao"]:
            pendencias.append("seção")
        if not paginas:
            pendencias.append("páginas")
        return {
            "bloco_id": identificador, "texto": texto,
            "fragmentos_ids": [f["id"] for f in ordenados],
            "fragmentos": [{"id": f["id"], "inicio": f["inicio"], "fim": f["fim"]} for f in ordenados],
            "texto_origem_sha256": primeiro["texto_origem_sha256"],
            "referencia": {"obra": titulo, "obra_id": primeiro["obra"], "volume": primeiro["volume"],
                           "secao": primeiro["secao"], "paginas": paginas,
                           "nota_paginas": "páginas registradas no índice; numeração impressa não conferida",
                           "pendencias": pendencias},
        }
