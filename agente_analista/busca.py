"""BM25 e E5 no acervo completo, com consultas e fontes rastreáveis."""

from collections import Counter, defaultdict
from copy import deepcopy
import math
import re
import unicodedata

import numpy as np

from vetorizacao import _dividir


class ErroBusca(ValueError):
    """Falha segura e acionável para a busca local."""


# A fórmula e os parâmetros reutilizam search.py do arquivo anterior. A nova
# tokenização dispensa Snowball e registra essa diferença no método exportado.
# Não removemos não/nem/sem: a avaliação deve ter acesso às negações do relato.
_VAZIAS = set("a ao aos as ate com da das de do dos e em entre essa esse esta este eu ela ele "
              "elas eles isso isto ja lhe lhes mais me meu meus minha minhas muito na nas no nos "
              "num numa o os ou para pela pelas pelo pelos por qual quando que quem se ser seu "
              "seus sobre sua suas tambem ter um uma umas uns".split())


def _termos(texto):
    texto = "".join(c for c in unicodedata.normalize("NFD", texto.casefold())
                    if unicodedata.category(c) != "Mn")
    return [t for t in re.findall(r"[^\W\d_]+", texto, re.UNICODE) if len(t) > 1 and t not in _VAZIAS]


class _BM25:
    K1, B = 1.2, 0.75

    def __init__(self, fragmentos):
        self.postings = defaultdict(list)
        self.tamanhos = np.zeros(len(fragmentos), dtype=np.float64)
        for i, f in enumerate(fragmentos):
            contagens = Counter(_termos(f["cabecalho"] + "\n" + f["texto"]))
            self.tamanhos[i] = sum(contagens.values())
            for termo, frequencia in contagens.items():
                self.postings[termo].append((i, frequencia))
        self.media = float(self.tamanhos.mean()) or 1.0

    def pontuar(self, texto):
        pontuacoes = np.zeros(len(self.tamanhos), dtype=np.float64)
        for termo in set(_termos(texto)):
            encontrados = self.postings.get(termo, [])
            if not encontrados:
                continue
            idf = math.log(1 + (len(self.tamanhos) - len(encontrados) + 0.5) / (len(encontrados) + 0.5))
            for indice, frequencia in encontrados:
                denominador = frequencia + self.K1 * (1 - self.B + self.B * self.tamanhos[indice] / self.media)
                pontuacoes[indice] += idf * frequencia * (self.K1 + 1) / denominador
        return pontuacoes


class Buscador:
    K_RRF, TOP_METODO, TOP_BLOCOS = 60, 50, 12

    def __init__(self, corpus, gerador):
        self.corpus, self.gerador = corpus, gerador
        self.lexical = _BM25(corpus.fragmentos)
        self._modelo_conferido = False

    def _conferir_modelo(self):
        if self._modelo_conferido:
            return
        try:
            descricao = self.gerador.descrever()
        except Exception as exc:
            raise ErroBusca("Não foi possível carregar o E5. Execute bash agente_analista/preparar_e5_mac_intel.sh "
                            "e confira a configuração instance/agente_analista_e5.json.") from exc
        m = self.corpus.manifesto
        if (descricao.get("identificacao") != m["modelo"] or descricao.get("revisao") != m["revisao"]
                or descricao.get("dimensao") != m["dimensao"]
                or descricao.get("limite_tokens", 0) < m["tokens_maximo"]):
            raise ErroBusca("Modelo, revisão, dimensão ou limite do E5 divergem do manifesto. "
                            "Prepare a revisão usada pelo corpus antes de buscar.")
        tokenizador = descricao.get("tokenizador", {})
        if tokenizador.get("identificacao") != m["modelo"] or tokenizador.get("revisao") != m["revisao"]:
            raise ErroBusca("O tokenizador E5 diverge do modelo ou revisão do corpus.")
        self._modelo_conferido = True

    def _consultas(self, relato):
        texto = relato["texto"]
        origens = [dict(p, origem=p["id"]) for p in relato["paragrafos"]]
        origens.append({"origem": "relato", "inicio": 0, "fim": len(texto), "texto": texto})
        m = self.corpus.manifesto
        configuracao = {"prefixo": m["prefixo_consultas"], "max_tokens": m["tokens_maximo"] - m["margem_tokens"]}
        consultas = []
        for origem in origens:
            if texto[origem["inicio"]:origem["fim"]] != origem["texto"]:
                raise ErroBusca("As posições dos parágrafos divergem do relato original.")
            try:
                recortes = list(_dividir(origem["texto"], self.gerador, configuracao))
            except Exception as exc:
                raise ErroBusca("Não foi possível dividir as consultas no limite E5 sem truncamento. "
                                "Confira o modelo e o tokenizador configurados.") from exc
            for numero, (inicio, fim, entrada) in enumerate(recortes, 1):
                identificador = origem["origem"] if len(recortes) == 1 else f"{origem['origem']}.{numero}"
                consulta = {"id": identificador, "origem": origem["origem"],
                            "inicio": origem["inicio"] + inicio, "fim": origem["inicio"] + fim,
                            "texto": origem["texto"][inicio:fim], "tokens": entrada["tokens_total"]}
                consultas.append((consulta, entrada))
        return consultas

    def buscar(self, relato, progresso=lambda etapa: None):
        progresso("Carregando o modelo E5")
        self._conferir_modelo()
        progresso("Preparando consultas de P1, P2 e do relato completo")
        consultas = self._consultas(relato)
        candidatos = {}
        for numero, (consulta, entrada) in enumerate(consultas, 1):
            progresso(f"Buscando no acervo: consulta {numero} de {len(consultas)}")
            lexical = self.lexical.pontuar(consulta["texto"])
            try:
                saida = self.gerador.gerar([entrada])
                vetor = np.asarray(saida, dtype=np.float32)
            except Exception as exc:
                raise ErroBusca("O E5 não conseguiu vetorizar a consulta. Confira a instalação e a memória disponível; "
                                "nenhum texto foi truncado.") from exc
            if (vetor.shape != (1, self.corpus.manifesto["dimensao"]) or not np.isfinite(vetor).all()
                    or not np.isclose(np.linalg.norm(vetor[0]), 1, atol=2e-5, rtol=0)):
                raise ErroBusca("O E5 retornou uma dimensão ou normalização incompatível com o corpus.")
            semantica = self.corpus.vetores @ vetor[0]
            for metodo, pontuacoes in (("BM25", lexical), ("E5", semantica)):
                # Lexical zero não constitui um resultado. E5 sempre examina
                # todas as linhas; a interpretação poderá rejeitar todos.
                indices = np.flatnonzero(pontuacoes > 0) if metodo == "BM25" else np.arange(len(pontuacoes))
                ordenados = sorted(indices, key=lambda i: (-float(pontuacoes[i]), int(i)))[:self.TOP_METODO]
                blocos_ranking = {}
                for rank, indice in enumerate(ordenados, 1):
                    f = self.corpus.fragmentos[int(indice)]
                    bloco_id = f["bloco_id"]
                    novo = bloco_id not in blocos_ranking
                    if novo:
                        blocos_ranking[bloco_id] = len(blocos_ranking) + 1
                    rank_bloco = blocos_ranking[bloco_id]
                    if bloco_id not in candidatos:
                        candidatos[bloco_id] = deepcopy(self.corpus.blocos[bloco_id])
                        candidatos[bloco_id].update(pontuacoes=[], rrf=0.0, fragmentos_recuperados_ids=[])
                    candidato = candidatos[bloco_id]
                    contribuicao = 1.0 / (self.K_RRF + rank_bloco) if novo else 0.0
                    candidato["rrf"] += contribuicao
                    candidato["pontuacoes"].append({"consulta_id": consulta["id"], "metodo": metodo,
                        "fragmento_id": f["id"], "rank": rank, "rank_bloco": rank_bloco,
                        "score": float(pontuacoes[indice]), "contribuicao_rrf": contribuicao})
                    if f["id"] not in candidato["fragmentos_recuperados_ids"]:
                        candidato["fragmentos_recuperados_ids"].append(f["id"])
        progresso("Reunindo fragmentos e ampliando o contexto das fontes")
        ordenados = sorted(candidatos.values(), key=lambda c: (-c["rrf"], c["bloco_id"]))[:self.TOP_BLOCOS]
        return {"consultas": [c for c, _ in consultas], "candidatos": ordenados,
                "metodo_fusao": {"nome": "Reciprocal Rank Fusion (RRF)", "k": self.K_RRF,
                    "top_por_metodo_consulta": self.TOP_METODO, "max_blocos": self.TOP_BLOCOS,
                    "formula": "soma de 1/(60+rank_bloco), com uma contribuição por bloco, método e consulta",
                    "agrupamento": "blocos distintos por ordem de primeira aparição no top 50 de fragmentos",
                    "bm25": {"k1": self.lexical.K1, "b": self.lexical.B,
                        "tokenizacao": "palavras sem acentos, minúsculas, sem palavras funcionais; sem radicalização"},
                    "semantica": "produto escalar de vetores E5 normalizados L2 (similaridade cosseno)",
                    "aviso": "Pontuações ordenam candidatos; não representam probabilidades de interpretação correta."}}
