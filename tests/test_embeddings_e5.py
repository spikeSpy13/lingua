"""Adaptador opcional, tensores controlados e inferência E5 real separada."""

from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import embeddings_e5 as e5
from contratos_vetorizacao import ErroConfiguracao, ErroLimite, ErroModelo


SHA = "0123456789abcdef0123456789abcdef01234567"
SHA_TOKENIZADOR = "89abcdef0123456789abcdef0123456789abcdef"


class ConfiguracaoE5Tests(unittest.TestCase):
    def test_importacao_e_construcao_nao_importam_torch_ou_transformers(self):
        comando = [sys.executable, "-c", "import sys; import embeddings_e5; "
                   f"embeddings_e5.GeradorE5(revisao='{SHA}'); "
                   "assert 'torch' not in sys.modules; assert 'transformers' not in sys.modules"]
        resultado = subprocess.run(comando, capture_output=True, text=True,
                                   cwd=Path(e5.__file__).parent)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)

    def test_revisao_imutavel_obrigatoria(self):
        for invalida in (None, "main", "v1.0", "", "a" * 39, "g" * 40):
            with self.subTest(invalida=invalida), self.assertRaises(ErroConfiguracao):
                e5.GeradorE5(revisao=invalida)
        self.assertEqual(e5.GeradorE5(revisao=SHA.upper()).revisao, SHA)

    def test_cpu_padrao_sem_gpu_ou_dependencias(self):
        gerador = e5.GeradorE5(revisao=SHA)
        self.assertEqual(gerador.dispositivo_solicitado, "cpu")
        self.assertEqual(gerador.precisao, "float32")
        self.assertTrue(gerador.local_files_only)

    def test_opcoes_invalidas(self):
        for opcoes in ({"dispositivo": "mps"}, {"precisao": "bf16"},
                       {"precisao": "float16"}, {"limite_tokens": True},
                       {"limite_tokens": 2}, {"local_files_only": 1},
                       {"modelo_id": " "}, {"modelo_id": "/tmp/modelo"},
                       {"modelo_id": "../modelo"}, {"tokenizador_id": "outro/modelo"}):
            with self.subTest(opcoes=opcoes), self.assertRaises(ErroConfiguracao):
                e5.GeradorE5(revisao=SHA, **opcoes)

    def test_ausencia_dependencias_erro_claro(self):
        with patch.object(e5.importlib, "import_module", side_effect=ImportError("ausente")):
            with self.assertRaisesRegex(ErroModelo, "requirements-embeddings"):
                e5._dependencias()

    def test_factory_sem_config_nao_inventa_revisao(self):
        with tempfile.TemporaryDirectory() as pasta, patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ErroConfiguracao):
                e5.criar_gerador_padrao(caminho_config=Path(pasta) / "nao-existe.json")

    def test_factory_config_env_opcoes_em_ordem(self):
        with tempfile.TemporaryDirectory() as pasta, patch.dict(os.environ, {}, clear=True):
            caminho = Path(pasta) / "modelo.json"
            caminho.write_text(json.dumps({"schema_version": "1.0.0", "revisao": SHA,
                                          "modelo_id": "repositorio/modelo", "dispositivo": "cpu"}))
            os.environ["LINGUA_EMBEDDINGS_DISPOSITIVO"] = "auto"
            gerador = e5.criar_gerador_padrao({"dispositivo": "cuda:2"}, caminho_config=caminho)
            self.assertEqual(gerador.dispositivo_solicitado, "cuda:2")
            self.assertEqual(gerador.revisao, SHA)
            self.assertEqual(gerador.modelo_id, "repositorio/modelo")

    def test_repositorio_nao_pode_ser_substituido_por_diretorio_local(self):
        gerador = e5.GeradorE5(modelo_id="instance", revisao=SHA)
        with patch.object(e5.Path, "exists", return_value=True):
            with self.assertRaisesRegex(ErroConfiguracao, "Diretórios locais"):
                gerador.descrever()

    def test_factory_rejeita_arquivo_e_opcoes_invalidos(self):
        with tempfile.TemporaryDirectory() as pasta, patch.dict(os.environ, {}, clear=True):
            caminho = Path(pasta) / "modelo.json"
            for valor in ("{", "[]", '{"schema_version":"2.0.0"}'):
                caminho.write_text(valor)
                with self.assertRaises(ErroConfiguracao):
                    e5.criar_gerador_padrao(caminho_config=caminho)
            caminho.unlink()
            with self.assertRaises(ErroConfiguracao):
                e5.criar_gerador_padrao({"revisao": SHA, "surpresa": True}, caminho_config=caminho)

    def test_setup_resolve_oficial_e_salva_sha_sem_pesos(self):
        hub = SimpleNamespace(HfApi=Mock(return_value=Mock(model_info=Mock(return_value=SimpleNamespace(sha=SHA)))),
                              snapshot_download=Mock())
        with tempfile.TemporaryDirectory() as pasta, patch.object(e5.importlib, "import_module", return_value=hub):
            caminho = Path(pasta) / "instance" / "modelo.json"
            registro = e5.preparar_configuracao(caminho_config=caminho)
            hub.HfApi.return_value.model_info.assert_called_once_with(e5.MODELO_PADRAO, revision="main")
            hub.snapshot_download.assert_not_called()
            self.assertEqual(json.loads(caminho.read_text()), registro)
            self.assertEqual(registro["revisao"], SHA)
            self.assertEqual(registro["tokenizador_revisao"], SHA)
            self.assertTrue(registro["local_files_only"])

    def test_setup_revisoes_distintas_download_fixado(self):
        api = Mock(model_info=Mock(side_effect=[SimpleNamespace(sha=SHA), SimpleNamespace(sha=SHA_TOKENIZADOR)]))
        hub = SimpleNamespace(HfApi=Mock(return_value=api), snapshot_download=Mock())
        with tempfile.TemporaryDirectory() as pasta, patch.object(e5.importlib, "import_module", return_value=hub):
            registro = e5.preparar_configuracao(tokenizador_id="outro/tokenizador", tokenizador_revisao="v2",
                                              baixar=True, caminho_config=Path(pasta) / "modelo.json")
            self.assertEqual(registro["tokenizador_revisao"], SHA_TOKENIZADOR)
            self.assertEqual({chamada.kwargs["revision"] for chamada in hub.snapshot_download.call_args_list},
                             {SHA, SHA_TOKENIZADOR})

    def test_setup_403_preserva_config_anterior(self):
        hub = SimpleNamespace(HfApi=Mock(side_effect=RuntimeError("HTTP 403")))
        with tempfile.TemporaryDirectory() as pasta, patch.object(e5.importlib, "import_module", return_value=hub):
            caminho = Path(pasta) / "modelo.json"
            caminho.write_text("configuração anterior")
            with self.assertRaisesRegex(ErroModelo, "Nenhuma revisão provisória"):
                e5.preparar_configuracao(caminho_config=caminho)
            self.assertEqual(caminho.read_text(), "configuração anterior")

    def test_setup_rejeita_sha_oficial_invalido(self):
        hub = SimpleNamespace(HfApi=Mock(return_value=Mock(model_info=Mock(return_value=SimpleNamespace(sha="main")))))
        with patch.object(e5.importlib, "import_module", return_value=hub):
            with self.assertRaises(ErroConfiguracao):
                e5.preparar_configuracao()

    def test_cli_preparar(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "modelo.json"
            with patch.object(e5, "preparar_configuracao", return_value={"modelo_id": e5.MODELO_PADRAO, "revisao": SHA}) as preparar:
                saida = io.StringIO()
                with redirect_stdout(saida):
                    self.assertEqual(e5.main(["preparar", "--config", str(caminho), "--baixar"]), 0)
                self.assertEqual(json.loads(saida.getvalue())["revisao"], SHA)
                self.assertTrue(preparar.call_args.kwargs["baixar"])


class TokenizadorControlado:
    is_fast = True
    model_max_length = 512
    pad_token_id = 0
    init_kwargs = {"_commit_hash": SHA}

    def __init__(self):
        self.chamadas = []

    def __call__(self, texto, **opcoes):
        self.chamadas.append((texto, opcoes))
        return {"input_ids": [101] + [ord(c) % 97 + 1 for c in texto] + [102],
                "offset_mapping": [(0, 0)] + [(i, i + 1) for i in range(len(texto))] + [(0, 0)],
                "special_tokens_mask": [1] + [0] * len(texto) + [1]}


@unittest.skipUnless(importlib.util.find_spec("torch"), "PyTorch opcional indisponível")
class AdapterTensorControladoTests(unittest.TestCase):
    """Usa operações PyTorch reais com estados controlados, sem pesos E5."""

    def setUp(self):
        import torch
        self.torch = torch
        self.tokenizador = TokenizadorControlado()
        self.chamadas_modelo = []
        chamadas = self.chamadas_modelo

        class Modelo:
            config = SimpleNamespace(hidden_size=3, max_position_embeddings=514, _commit_hash=SHA)

            def to(self, dispositivo):
                self.dispositivo = dispositivo
                return self

            def eval(self):
                return self

            def __call__(self, *, input_ids, attention_mask):
                chamadas.append((input_ids.clone(), attention_mask.clone()))
                base = input_ids.float()
                # Padding possui valores grandes: o resultado deve excluí-los.
                segundo = torch.where(input_ids == 0, 10000.0, 1.0)
                return SimpleNamespace(last_hidden_state=torch.stack((base, segundo, segundo * 2), dim=-1))

        self.modelo = Modelo()
        self.transformers = SimpleNamespace(__version__="controlado-1.0",
            AutoTokenizer=SimpleNamespace(from_pretrained=Mock(return_value=self.tokenizador)),
            AutoModel=SimpleNamespace(from_pretrained=Mock(return_value=self.modelo)))
        self.patch = patch.object(e5, "_dependencias", return_value=(torch, self.transformers))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.gerador = e5.GeradorE5(revisao=SHA)

    def entrada(self, texto, prefixo="passage: "):
        return {"texto": prefixo + texto, **self.gerador.tokenizar(texto, prefixo)}

    def test_revisoes_e_dispositivo_reais_descricao_independente(self):
        descricao = self.gerador.descrever()
        self.assertEqual(descricao["natureza"], "inferencia_real")
        self.assertEqual(descricao["ambiente"]["dispositivo"], "cpu")
        self.assertEqual(descricao["ambiente"]["precisao_calculo"], "float32")
        self.assertEqual(descricao["dimensao"], 3)
        descricao["ambiente"]["dispositivo"] = "adulterado"
        self.assertEqual(self.gerador.descrever()["ambiente"]["dispositivo"], "cpu")
        self.assertEqual(self.transformers.AutoModel.from_pretrained.call_args.kwargs["revision"], SHA)
        self.assertFalse(self.transformers.AutoModel.from_pretrained.call_args.kwargs["trust_remote_code"])

    def test_carrega_uma_vez(self):
        self.gerador.descrever()
        self.gerador.tokenizar("texto", "query: ")
        self.gerador.gerar([self.entrada("texto")])
        self.transformers.AutoTokenizer.from_pretrained.assert_called_once()
        self.transformers.AutoModel.from_pretrained.assert_called_once()

    def test_tokenizacao_unicode_crlf_prefixo_sem_truncamento(self):
        texto = "ação\r\n👩🏽‍💻 e\u0301"
        resultado = self.gerador.tokenizar(texto, "passage: ")
        string, opcoes = self.tokenizador.chamadas[-1]
        self.assertEqual(string, "passage: " + texto)
        self.assertIs(opcoes["truncation"], False)
        self.assertIs(opcoes["padding"], False)
        self.assertEqual(len(resultado["input_ids"]), len(string) + 2)
        self.assertEqual(resultado["offset_mapping"][-2], [len(string) - 1, len(string)])

    def test_ids_exatos_padding_excluido_especiais_incluidos_pooling_l2(self):
        primeiro, segundo = self.entrada("x"), self.entrada("texto longo")
        # Simula um planejamento que já preservou IDs diferentes dos que uma nova
        # tokenização produziria. A inferência não pode retokenizar estes IDs.
        primeiro["input_ids"][1] = 777
        total_chamadas = len(self.tokenizador.chamadas)
        vetores = self.gerador.gerar([primeiro, segundo])
        self.assertEqual(len(self.tokenizador.chamadas), total_chamadas)
        ids, mask = self.chamadas_modelo[0]
        self.assertEqual(ids[0][:len(primeiro["input_ids"])].tolist(), primeiro["input_ids"])
        self.assertEqual(mask[0].sum().item(), len(primeiro["input_ids"]))
        media_id = sum(primeiro["input_ids"]) / len(primeiro["input_ids"])
        norma = math.sqrt(media_id ** 2 + 1 + 4)
        for obtido, esperado in zip(vetores[0], (media_id / norma, 1 / norma, 2 / norma)):
            self.assertAlmostEqual(obtido, esperado, places=6)
        for vetor in vetores:
            self.assertAlmostEqual(math.sqrt(sum(x * x for x in vetor)), 1, places=6)

    def test_entrada_excessiva_recusada_antes_inferencia(self):
        entrada = self.entrada("a" * 513, "")
        self.assertEqual(len(entrada["input_ids"]), 515)
        with self.assertRaises(ErroLimite):
            self.gerador.gerar([entrada])
        self.assertEqual(self.chamadas_modelo, [])

    def test_dados_malformados_recusados(self):
        entrada = self.entrada("ok")
        casos = []
        for campo, valor in (("input_ids", [True]), ("offset_mapping", []),
                             ("special_tokens_mask", [False] * len(entrada["input_ids"]))):
            adulterada = deepcopy(entrada)
            adulterada[campo] = valor
            casos.append(adulterada)
        adulterada = deepcopy(entrada)
        adulterada["offset_mapping"][1] = [0, len(entrada["texto"]) + 1]
        casos.append(adulterada)
        for caso in casos:
            with self.assertRaises(ErroConfiguracao):
                self.gerador.gerar([caso])
        self.assertEqual(self.chamadas_modelo, [])

    def test_cuda_indisponivel_nao_faz_fallback_silencioso(self):
        with patch.object(self.torch.cuda, "is_available", return_value=False):
            with self.assertRaisesRegex(ErroModelo, "CUDA"):
                e5.GeradorE5(revisao=SHA, dispositivo="cuda").descrever()

    def test_auto_cpu_float16_rejeitado(self):
        with patch.object(self.torch.cuda, "is_available", return_value=False):
            with self.assertRaises(ErroConfiguracao):
                e5.GeradorE5(revisao=SHA, dispositivo="auto", precisao="float16").descrever()

    def test_revisao_modelo_e_tokenizador_carregados_verificadas(self):
        self.modelo.config = SimpleNamespace(hidden_size=3, max_position_embeddings=514, _commit_hash=SHA_TOKENIZADOR)
        with self.assertRaisesRegex(ErroModelo, "Revisão carregada"):
            self.gerador.descrever()
        self.modelo.config._commit_hash = SHA
        self.tokenizador.init_kwargs = {"_commit_hash": SHA_TOKENIZADOR}
        with self.assertRaisesRegex(ErroModelo, "tokenizador"):
            self.gerador.descrever()

    def test_lote_vazio_sem_carregar(self):
        self.assertEqual(self.gerador.gerar([]), [])
        self.transformers.AutoModel.from_pretrained.assert_not_called()


@unittest.skipUnless(os.environ.get("LINGUA_TESTE_E5_REAL") == "1", "Inferência E5 real opt-in")
class InferenciaE5RealTests(unittest.TestCase):
    """Exige dependências, configuração oficial fixa e pesos no cache local."""

    def test_modelo_real_dois_textos_l2_determinismo_e_limite(self):
        gerador = e5.criar_gerador_padrao()
        descricao = gerador.descrever()
        self.assertEqual(descricao["natureza"], "inferencia_real")
        textos = ["Hoje vou estudar português.", "Amanhã viajarei para São Paulo.\r\n"]
        entradas = [{"texto": "passage: " + texto, **gerador.tokenizar(texto, "passage: ")} for texto in textos]
        vetores = gerador.gerar(entradas)
        repetidos = gerador.gerar(entradas)
        self.assertEqual(len(vetores), 2)
        for vetor, repetido in zip(vetores, repetidos):
            self.assertEqual(len(vetor), descricao["dimensao"])
            self.assertTrue(all(math.isfinite(x) for x in vetor))
            self.assertAlmostEqual(math.sqrt(sum(x * x for x in vetor)), 1, places=5)
            self.assertLess(max(abs(a - b) for a, b in zip(vetor, repetido)), 1e-5)
        longo = "português " * (descricao["limite_tokens"] + 1)
        entrada = {"texto": "passage: " + longo, **gerador.tokenizar(longo, "passage: ")}
        self.assertGreater(len(entrada["input_ids"]), descricao["limite_tokens"])
        with self.assertRaises(ErroLimite):
            gerador.gerar([entrada])


if __name__ == "__main__":
    unittest.main()
