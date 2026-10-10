"""Integridade do índice, recuperação real e consultas sem truncamento."""

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from agente_analista.busca import Buscador, ErroBusca, _BM25
from agente_analista.corpus import Corpus, ErroCorpus


RAIZ = Path(__file__).resolve().parents[1]
REVISAO = "3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3"


def _sha(texto):
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _escrever(pasta, fragmentos):
    (pasta / "fragmentos.jsonl").write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in fragmentos), encoding="utf-8")


def _fixture(pasta):
    manifesto = {"modelo": "intfloat/multilingual-e5-large", "revisao": REVISAO,
                 "prefixo_passagens": "passage: ", "prefixo_consultas": "query: ",
                 "tokens_maximo": 64, "margem_tokens": 8, "dimensao": 3,
                 "normalizacao": "L2", "tipo_vetor": "float32", "blocos": 2, "fragmentos": 3,
                 "referencias_arquivo": "fragmentos.jsonl", "vetores_arquivo": "vetores.npy"}
    fragmentos = []
    for bloco, textos in (("sonhos#1", ["O sonho retorna. ", "O desejo insiste."]), ("viagem#1", ["Viagem a Paris."])):
        origem = "".join(textos)
        inicio = 0
        for i, texto in enumerate(textos):
            f = {"id": f"{bloco}@{i}", "bloco_id": bloco, "obra": bloco.split("#")[0], "volume": 1,
                 "secao": "", "fragmento_no_bloco": i, "inicio": inicio, "fim": inicio + len(texto),
                 "paginas": [12 + i], "texto": texto, "cabecalho": "Sonhos" if bloco.startswith("sonhos") else "Viagem",
                 "texto_origem_sha256": _sha(origem), "tokens_entrada": 30}
            f["entrada_sha256"] = _sha("passage: " + f["cabecalho"] + "\n" + texto)
            inicio = f["fim"]
            fragmentos.append(f)
    (pasta / "manifesto.json").write_text(json.dumps(manifesto), encoding="utf-8")
    _escrever(pasta, fragmentos)
    np.save(pasta / "vetores.npy", np.array([[1, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32))
    return fragmentos


def _relato(p1="Sonho e desejo.", p2="Volto a pensar no sonho."):
    separador = "\n \t\n"
    texto = p1 + separador + p2
    inicio2 = len(p1) + len(separador)
    return {"texto": texto, "palavras": len(texto.split()), "paragrafos": [
        {"id": "P1", "inicio": 0, "fim": len(p1), "texto": p1},
        {"id": "P2", "inicio": inicio2, "fim": len(texto), "texto": p2}]}


class GeradorControlado:
    def __init__(self):
        self.entradas = []

    def descrever(self):
        return {"identificacao": "intfloat/multilingual-e5-large", "revisao": REVISAO,
                "tokenizador": {"identificacao": "intfloat/multilingual-e5-large", "revisao": REVISAO},
                "dimensao": 3, "limite_tokens": 64}

    def tokenizar(self, texto, prefixo):
        efetivo = prefixo + texto
        return {"input_ids": [1] + [ord(c) for c in efetivo] + [2],
                "offset_mapping": [[0, 0]] + [[i, i + 1] for i in range(len(efetivo))] + [[0, 0]],
                "special_tokens_mask": [1] + [0] * len(efetivo) + [1]}

    def gerar(self, lote):
        assert len(lote) == 1, "Inferência deve ser serial para o Mac Intel."
        self.entradas.extend(deepcopy(lote))
        return [[1.0, 0.0, 0.0]]


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.temporario = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporario.cleanup)
        self.pasta = Path(self.temporario.name)
        self.fragmentos = _fixture(self.pasta)

    def test_reconstroi_texto_e_referencia_sem_inventar_bibliografia(self):
        corpus = Corpus(self.pasta)
        self.assertIsInstance(corpus.vetores, np.memmap)
        bloco = corpus.blocos["sonhos#1"]
        self.assertEqual(bloco["texto"], "O sonho retorna. O desejo insiste.")
        self.assertEqual(bloco["fragmentos_ids"], ["sonhos#1@0", "sonhos#1@1"])
        self.assertEqual(bloco["referencia"]["paginas"], [12, 13])
        self.assertIn("numeração impressa não conferida", bloco["referencia"]["nota_paginas"])
        self.assertIn("edição", bloco["referencia"]["pendencias"])

    def test_rejeita_identificador_duplicado(self):
        self.fragmentos[1]["id"] = self.fragmentos[0]["id"]
        _escrever(self.pasta, self.fragmentos)
        with self.assertRaisesRegex(ErroCorpus, "duplicado"):
            Corpus(self.pasta)

    def test_rejeita_lacuna_e_sobreposicao(self):
        for deslocamento in (-1, 1):
            with self.subTest(deslocamento=deslocamento):
                fs = deepcopy(self.fragmentos)
                fs[1]["inicio"] += deslocamento
                fs[1]["fim"] += deslocamento
                _escrever(self.pasta, fs)
                with self.assertRaisesRegex(ErroCorpus, "lacuna|sobreposição conflitante"):
                    Corpus(self.pasta)

    def test_une_sobreposicao_consistente_como_uma_fonte(self):
        origem = "O sonho retorna. O desejo insiste."
        f = self.fragmentos[1]
        f["inicio"] = 8
        f["texto"] = origem[f["inicio"]:]
        f["fim"] = len(origem)
        f["entrada_sha256"] = _sha("passage: " + f["cabecalho"] + "\n" + f["texto"])
        _escrever(self.pasta, self.fragmentos)
        corpus = Corpus(self.pasta)
        self.assertEqual(corpus.blocos["sonhos#1"]["texto"], origem)
        resultado = Buscador(corpus, GeradorControlado()).buscar(_relato())
        self.assertEqual(sum(c["bloco_id"] == "sonhos#1" for c in resultado["candidatos"]), 1)

    def test_rejeita_texto_alterado_mesmo_com_hash_da_entrada_recalculado(self):
        f = self.fragmentos[0]
        f["texto"] = f["texto"].replace("sonho", "medos")
        f["entrada_sha256"] = _sha("passage: " + f["cabecalho"] + "\n" + f["texto"])
        _escrever(self.pasta, self.fragmentos)
        with self.assertRaisesRegex(ErroCorpus, "bloco reconstruído"):
            Corpus(self.pasta)

    def test_rejeita_hash_da_entrada_incorreto(self):
        self.fragmentos[0]["entrada_sha256"] = "0" * 64
        _escrever(self.pasta, self.fragmentos)
        with self.assertRaisesRegex(ErroCorpus, "entrada E5"):
            Corpus(self.pasta)

    def test_rejeita_matriz_incompativel_nan_ou_nao_normalizada(self):
        for matriz in (np.ones((3, 4), dtype=np.float32), np.eye(3, dtype=np.float64),
                       np.array([[np.nan, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32),
                       np.zeros((3, 3), dtype=np.float32)):
            with self.subTest(shape=matriz.shape, dtype=matriz.dtype):
                np.save(self.pasta / "vetores.npy", matriz)
                with self.assertRaises(ErroCorpus):
                    Corpus(self.pasta)

    def test_nao_le_arquivos_fora_da_pasta_do_corpus(self):
        caminho = self.pasta / "manifesto.json"
        manifesto = json.loads(caminho.read_text())
        manifesto["referencias_arquivo"] = "../fragmentos.jsonl"
        caminho.write_text(json.dumps(manifesto))
        with self.assertRaisesRegex(ErroCorpus, "diretamente na pasta"):
            Corpus(self.pasta)


class BuscaTests(unittest.TestCase):
    def setUp(self):
        self.temporario = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporario.cleanup)
        pasta = Path(self.temporario.name)
        _fixture(pasta)
        self.corpus = Corpus(pasta)
        self.gerador = GeradorControlado()
        self.buscador = Buscador(self.corpus, self.gerador)

    def test_agrupa_fontes_e_cada_bloco_contribui_uma_vez_por_ranking(self):
        etapas = []
        resultado = self.buscador.buscar(_relato(), etapas.append)
        self.assertEqual({c["origem"] for c in resultado["consultas"]}, {"P1", "P2", "relato"})
        blocos = resultado["candidatos"]
        self.assertEqual(len({c["bloco_id"] for c in blocos}), len(blocos))
        primeiro = blocos[0]
        self.assertEqual(primeiro["bloco_id"], "sonhos#1")
        self.assertEqual(primeiro["fragmentos_ids"], ["sonhos#1@0", "sonhos#1@1"])
        contribuicoes = defaultdict(list)
        for p in primeiro["pontuacoes"]:
            contribuicoes[(p["consulta_id"], p["metodo"])].append(p["contribuicao_rrf"])
        self.assertTrue(all(sum(c > 0 for c in contrib) == 1 for contrib in contribuicoes.values()))
        self.assertAlmostEqual(primeiro["rrf"], sum(sum(c) for c in contribuicoes.values()))
        self.assertTrue(etapas)

    def test_divide_consultas_preservando_todo_texto_unicode_e_offsets(self):
        relato = _relato("  Não apago o sonho 🧠.\t" * 7, "A lembrança retorna.\r\nMesmo assim há dúvida.  ")
        original = deepcopy(relato)
        resultado = self.buscador.buscar(relato)
        self.assertEqual(relato, original)
        for origem in ("P1", "P2", "relato"):
            consultas = [c for c in resultado["consultas"] if c["origem"] == origem]
            esperado = relato["texto"] if origem == "relato" else next(p["texto"] for p in relato["paragrafos"] if p["id"] == origem)
            self.assertEqual("".join(c["texto"] for c in consultas), esperado)
            self.assertTrue(all(c["tokens"] <= 56 for c in consultas))
            for c in consultas:
                self.assertEqual(relato["texto"][c["inicio"]:c["fim"]], c["texto"])
            for anterior, atual in zip(consultas, consultas[1:]):
                self.assertEqual(anterior["fim"], atual["inicio"])
        self.assertTrue(all(e["texto"].startswith("query: ") for e in self.gerador.entradas))

    def test_nao_aceita_gerador_de_outra_revisao(self):
        descricao = self.gerador.descrever()
        descricao["revisao"] = "0" * 40
        with patch.object(self.gerador, "descrever", return_value=descricao), self.assertRaisesRegex(ErroBusca, "divergem"):
            self.buscador.buscar(_relato())

    def test_nao_aceita_vetor_zero_ou_dimensao_errada(self):
        for vetor in ([[0, 0, 0]], [[1, 0]], [[float("nan"), 0, 0]]):
            with self.subTest(vetor=vetor), patch.object(self.gerador, "gerar", return_value=vetor):
                with self.assertRaisesRegex(ErroBusca, "incompatível"):
                    self.buscador.buscar(_relato())


class CorpusRealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = Corpus(RAIZ / "agente_analista/data/Vetor")

    def test_recuperacao_lexical_do_indice_fornecido(self):
        lexical = _BM25(self.corpus.fragmentos)
        pontuacoes = lexical.pontuar("sonho lembrança desejo")
        indice = int(pontuacoes.argmax())
        fonte = self.corpus.fragmentos[indice]
        self.assertGreater(float(pontuacoes[indice]), 0)
        self.assertEqual(fonte["obra"], "interpretacao-sonhos")
        bloco = self.corpus.blocos[fonte["bloco_id"]]
        self.assertEqual(bloco["texto"][fonte["inicio"]:fonte["fim"]], fonte["texto"])
        self.assertEqual(len(self.corpus.fragmentos), 8087)
        self.assertEqual(len(self.corpus.blocos), 6249)

    @unittest.skipUnless(os.environ.get("AGENTE_ANALISTA_TESTE_E5_REAL") == "1", "Inferência real E5 opcional")
    def test_busca_hibrida_com_e5_local_real(self):
        from embeddings_e5 import criar_gerador_padrao
        ambiente = {"HF_HOME": str(RAIZ / "instance/huggingface"),
                    "HF_HUB_CACHE": str(RAIZ / "instance/huggingface/hub"),
                    "HF_HUB_OFFLINE": "1", "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"}
        with patch.dict(os.environ, ambiente):
            gerador = criar_gerador_padrao(caminho_config=RAIZ / "instance/agente_analista_e5.json")
            resultado = Buscador(self.corpus, gerador).buscar(_relato())
        self.assertTrue(resultado["candidatos"])
        self.assertEqual(len(resultado["consultas"]), 3)
        for candidato in resultado["candidatos"]:
            self.assertIn(candidato["bloco_id"], self.corpus.blocos)
            self.assertTrue(any(p["metodo"] == "E5" for p in candidato["pontuacoes"]))


if __name__ == "__main__":
    unittest.main()
