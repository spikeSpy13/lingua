"""Adaptador E5 opcional: revisões fixas, IDs exatos e inferência sem truncamento.

Importar este módulo não importa PyTorch nem Transformers. Prepare uma revisão
oficial com ``python -m embeddings_e5 preparar``; a geração usa o cache local por
padrão. O contrato da etapa 09 permanece independente deste adaptador.
"""

from copy import deepcopy
import argparse
import importlib
import json
import os
from pathlib import Path
import re
import tempfile

from contratos_vetorizacao import ErroConfiguracao, ErroLimite, ErroModelo


MODELO_PADRAO = "intfloat/multilingual-e5-large"
CONFIG_PADRAO = Path(__file__).resolve().parent / "instance" / "embeddings_modelo.json"
_REVISAO = re.compile(r"[0-9a-fA-F]{40}\Z")
_REPOSITORIO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?\Z")
_DISPOSITIVO = re.compile(r"(?:cpu|auto|cuda(?::[0-9]+)?)\Z")
_OPCOES = {"modelo_id", "revisao", "tokenizador_id", "tokenizador_revisao",
           "dispositivo", "precisao", "local_files_only", "limite_tokens"}


def _revisao_fixa(valor, campo):
    if not isinstance(valor, str) or not _REVISAO.fullmatch(valor):
        raise ErroConfiguracao(f"{campo} exige o SHA de 40 caracteres da revisão; "
                               "prepare o modelo antes de gerar embeddings.")
    return valor.lower()


def _identificador(valor, campo):
    if not isinstance(valor, str) or not _REPOSITORIO.fullmatch(valor):
        raise ErroConfiguracao(f"{campo} deve identificar um repositório Hugging Face.")
    return valor


def _dependencias():
    try:
        return importlib.import_module("torch"), importlib.import_module("transformers")
    except ImportError as exc:
        raise ErroModelo("Dependências de embeddings indisponíveis. Instale "
                         "requirements-embeddings.txt com a distribuição PyTorch "
                         "adequada a CPU ou GPU; as etapas 01–08 não dependem dela.") from exc


class GeradorE5:
    """Modelo carregado uma vez, com dispositivo e precisão efetivos registrados."""

    def __init__(self, *, modelo_id=MODELO_PADRAO, revisao=None,
                 tokenizador_id=None, tokenizador_revisao=None,
                 dispositivo="cpu", precisao="float32", local_files_only=True,
                 limite_tokens=512):
        self.modelo_id = _identificador(modelo_id, "modelo_id")
        self.revisao = _revisao_fixa(revisao, "revisao")
        self.tokenizador_id = _identificador(tokenizador_id or modelo_id, "tokenizador_id")
        if tokenizador_revisao is None:
            if self.tokenizador_id != self.modelo_id:
                raise ErroConfiguracao("Tokenizador de outro repositório exige revisão própria.")
            tokenizador_revisao = self.revisao
        self.tokenizador_revisao = _revisao_fixa(tokenizador_revisao, "tokenizador_revisao")
        if not isinstance(dispositivo, str) or not _DISPOSITIVO.fullmatch(dispositivo):
            raise ErroConfiguracao("dispositivo deve ser cpu, cuda, cuda:N ou auto.")
        if precisao not in ("float32", "float16"):
            raise ErroConfiguracao("precisao deve ser float32 ou float16.")
        if dispositivo == "cpu" and precisao != "float32":
            raise ErroConfiguracao("Inferência CPU exige float32 neste adaptador.")
        if type(local_files_only) is not bool:
            raise ErroConfiguracao("local_files_only deve ser booleano.")
        if type(limite_tokens) is not int or limite_tokens < 3:
            raise ErroConfiguracao("limite_tokens deve ser inteiro maior ou igual a 3.")
        self.dispositivo_solicitado = dispositivo
        self.precisao = precisao
        self.local_files_only = local_files_only
        self.limite_tokens = limite_tokens
        self._torch = self._transformers = self._tokenizador = self._modelo = None
        self._descricao = None

    def _carregar(self):
        if self._modelo is not None:
            return
        if Path(self.modelo_id).exists() or Path(self.tokenizador_id).exists():
            raise ErroConfiguracao("Diretórios locais não podem substituir repositórios "
                                   "com revisão oficial fixa. Use o cache Hugging Face.")
        torch, transformers = _dependencias()
        dispositivo = self.dispositivo_solicitado
        if dispositivo == "auto":
            dispositivo = "cuda:0" if torch.cuda.is_available() else "cpu"
        elif dispositivo == "cuda":
            dispositivo = "cuda:0"
        if dispositivo.startswith("cuda"):
            if not torch.cuda.is_available():
                raise ErroModelo("CUDA foi solicitada, mas está indisponível. Configure cpu.")
            indice = int(dispositivo.split(":")[1])
            if indice >= torch.cuda.device_count():
                raise ErroModelo("O dispositivo CUDA solicitado não existe.")
        elif self.precisao != "float32":
            raise ErroConfiguracao("Dispositivo efetivo CPU exige float32.")
        dtype = torch.float32 if self.precisao == "float32" else torch.float16
        try:
            tokenizador = transformers.AutoTokenizer.from_pretrained(
                self.tokenizador_id, revision=self.tokenizador_revisao,
                local_files_only=self.local_files_only, use_fast=True,
                trust_remote_code=False,
            )
            if not tokenizador.is_fast:
                raise ErroModelo("Rastreabilidade exige tokenizador rápido com offsets.")
            sha_tokenizador = getattr(tokenizador, "init_kwargs", {}).get("_commit_hash")
            if sha_tokenizador is not None and sha_tokenizador.lower() != self.tokenizador_revisao:
                raise ErroModelo("Revisão carregada do tokenizador difere da revisão fixa.")
            modelo = transformers.AutoModel.from_pretrained(
                self.modelo_id, revision=self.revisao,
                local_files_only=self.local_files_only, torch_dtype=dtype,
                trust_remote_code=False,
            )
            modelo.to(dispositivo)
            modelo.eval()
            dimensao = modelo.config.hidden_size
            if type(dimensao) is not int or dimensao < 1:
                raise ErroModelo("Modelo não declarou uma dimensão vetorial válida.")
            sha_carregado = getattr(modelo.config, "_commit_hash", None)
            if sha_carregado is not None and sha_carregado.lower() != self.revisao:
                raise ErroModelo("Revisão carregada difere da revisão fixa solicitada.")
            limite_tokenizador = getattr(tokenizador, "model_max_length", None)
            if type(limite_tokenizador) is int and self.limite_tokens > limite_tokenizador:
                raise ErroConfiguracao("limite_tokens excede o limite do tokenizador.")
            limite_modelo = getattr(modelo.config, "max_position_embeddings", None)
            if type(limite_modelo) is int and self.limite_tokens > limite_modelo:
                raise ErroConfiguracao("limite_tokens excede o limite de posições do modelo.")
            if self.modelo_id.startswith("intfloat/multilingual-e5-") and self.limite_tokens > 512:
                raise ErroConfiguracao("Os modelos multilingual-e5 suportam até 512 tokens.")
            if tokenizador.pad_token_id is None:
                raise ErroModelo("Tokenizador precisa declarar pad_token_id para os lotes.")
        except (ErroModelo, ErroConfiguracao):
            raise
        except Exception as exc:
            raise ErroModelo("Não foi possível carregar o modelo na revisão fixa. "
                             "Confira as dependências e prepare/baixe seu cache com "
                             "python -m embeddings_e5 preparar --baixar. "
                             f"Detalhe: {exc}") from exc
        self._torch, self._transformers = torch, transformers
        self._tokenizador, self._modelo = tokenizador, modelo
        ambiente = {
            "backend": "pytorch", "versao_backend": str(torch.__version__),
            "versao_transformers": str(transformers.__version__),
            "dispositivo": dispositivo, "dispositivo_solicitado": self.dispositivo_solicitado,
            "precisao_calculo": self.precisao, "precisao_pooling": "float32",
            "precisao_saida": "float32", "cuda": torch.version.cuda,
        }
        self._descricao = {
            "identificacao": self.modelo_id, "revisao": self.revisao,
            "tokenizador": {"identificacao": self.tokenizador_id,
                            "revisao": self.tokenizador_revisao},
            "dimensao": dimensao, "limite_tokens": self.limite_tokens,
            "natureza": "inferencia_real", "ambiente": ambiente,
        }

    def descrever(self):
        self._carregar()
        return deepcopy(self._descricao)

    def tokenizar(self, texto_canonico, prefixo):
        if not isinstance(texto_canonico, str) or not isinstance(prefixo, str):
            raise ErroConfiguracao("Texto canônico e prefixo devem ser strings.")
        self._carregar()
        try:
            resultado = self._tokenizador(
                prefixo + texto_canonico, add_special_tokens=True,
                padding=False, truncation=False, return_offsets_mapping=True,
                return_special_tokens_mask=True, return_attention_mask=False,
                return_token_type_ids=False,
            )
            return {
                "input_ids": list(resultado["input_ids"]),
                "offset_mapping": [list(par) for par in resultado["offset_mapping"]],
                "special_tokens_mask": list(resultado["special_tokens_mask"]),
            }
        except Exception as exc:
            raise ErroModelo(f"Falha na tokenização sem truncamento: {exc}") from exc

    def gerar(self, lote):
        if not isinstance(lote, list):
            raise ErroConfiguracao("Lote de inferência deve ser uma lista.")
        if not lote:
            return []
        self._carregar()
        ids_por_entrada = []
        for entrada in lote:
            if not isinstance(entrada, dict) or not isinstance(entrada.get("texto"), str):
                raise ErroConfiguracao("Entrada de inferência precisa declarar seu texto.")
            ids = entrada.get("input_ids")
            offsets, especiais = entrada.get("offset_mapping"), entrada.get("special_tokens_mask")
            if (not isinstance(ids, list) or not ids
                    or any(type(i) is not int or i < 0 for i in ids)):
                raise ErroConfiguracao("input_ids precisa conter inteiros não negativos.")
            if len(ids) > self.limite_tokens:
                raise ErroLimite("Entrada excede o limite; divida em blocos antes da inferência.")
            if (not isinstance(offsets, list) or len(offsets) != len(ids)
                    or not isinstance(especiais, list) or len(especiais) != len(ids)
                    or any(type(i) is not int or i not in (0, 1) for i in especiais)):
                raise ErroConfiguracao("Offsets e máscara especial devem corresponder aos IDs.")
            for par in offsets:
                if (not isinstance(par, (list, tuple)) or len(par) != 2
                        or any(type(i) is not int for i in par)
                        or not 0 <= par[0] <= par[1] <= len(entrada["texto"])):
                    raise ErroConfiguracao("Offset fora da entrada textual efetiva.")
            ids_por_entrada.append(ids)
        torch = self._torch
        tamanho = max(map(len, ids_por_entrada))
        preenchidos = [ids + [self._tokenizador.pad_token_id] * (tamanho - len(ids))
                       for ids in ids_por_entrada]
        mascaras = [[1] * len(ids) + [0] * (tamanho - len(ids)) for ids in ids_por_entrada]
        try:
            tensor_ids = torch.tensor(preenchidos, dtype=torch.long,
                                      device=self._descricao["ambiente"]["dispositivo"])
            tensor_mask = torch.tensor(mascaras, dtype=torch.long, device=tensor_ids.device)
            with torch.inference_mode():
                saida = self._modelo(input_ids=tensor_ids, attention_mask=tensor_mask)
                estados = saida.last_hidden_state.float()
                if estados.shape != (len(lote), tamanho, self._descricao["dimensao"]):
                    raise ErroModelo("Saída do modelo diverge da dimensão e do lote declarados.")
                # O pooling oficial E5 inclui especiais e exclui apenas padding.
                estados = estados.masked_fill(~tensor_mask.bool().unsqueeze(-1), 0.0)
                vetores = estados.sum(dim=1) / tensor_mask.sum(dim=1).unsqueeze(-1)
                normas = torch.linalg.vector_norm(vetores, ord=2, dim=1, keepdim=True)
                if not torch.isfinite(vetores).all() or not torch.isfinite(normas).all() or (normas <= 0).any():
                    raise ErroModelo("Modelo produziu vetor não finito ou de norma zero.")
                vetores = (vetores / normas).to(dtype=torch.float32, device="cpu")
            return vetores.tolist()
        except (ErroModelo, ErroLimite):
            raise
        except Exception as exc:
            if isinstance(exc, torch.OutOfMemoryError):
                raise ErroLimite("Memória insuficiente durante inferência. Reduza o lote "
                                 "ou escolha outro dispositivo/modelo; nada foi truncado.") from exc
            raise ErroModelo(f"Falha durante inferência E5: {exc}") from exc


def criar_gerador_padrao(configuracao=None, *, caminho_config=None):
    """Configuração local + ambiente + opções explícitas; nenhum SHA inventado."""
    caminho = Path(caminho_config or os.environ.get("LINGUA_EMBEDDINGS_CONFIG", CONFIG_PADRAO))
    opcoes = {}
    if caminho.exists():
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ErroConfiguracao(f"Configuração de embeddings inválida em {caminho}.") from exc
        if not isinstance(dados, dict) or dados.get("schema_version") != "1.0.0":
            raise ErroConfiguracao("Configuração local de embeddings exige schema_version 1.0.0.")
        opcoes = {k: v for k, v in dados.items() if k in _OPCOES}
    for nome, chave in {
        "MODELO": "modelo_id", "REVISAO": "revisao", "TOKENIZADOR": "tokenizador_id",
        "TOKENIZADOR_REVISAO": "tokenizador_revisao", "DISPOSITIVO": "dispositivo",
        "PRECISAO": "precisao",
    }.items():
        if f"LINGUA_EMBEDDINGS_{nome}" in os.environ:
            opcoes[chave] = os.environ[f"LINGUA_EMBEDDINGS_{nome}"]
    if configuracao is not None:
        if not isinstance(configuracao, dict) or set(configuracao) - _OPCOES:
            raise ErroConfiguracao("Opções do gerador desconhecidas ou configuração inválida.")
        opcoes.update(configuracao)
    return GeradorE5(**opcoes)


def preparar_configuracao(*, modelo_id=MODELO_PADRAO, revisao="main",
                         tokenizador_id=None, tokenizador_revisao=None,
                         dispositivo="cpu", precisao="float32", baixar=False,
                         caminho_config=CONFIG_PADRAO):
    """Resolve SHA oficial somente no setup e, opcionalmente, baixa os snapshots."""
    try:
        hub = importlib.import_module("huggingface_hub")
    except ImportError as exc:
        raise ErroModelo("Instale requirements-embeddings.txt para preparar o modelo.") from exc
    tokenizador_id = tokenizador_id or modelo_id
    tokenizador_revisao = tokenizador_revisao or revisao
    try:
        api = hub.HfApi()
        sha_modelo = _revisao_fixa(api.model_info(modelo_id, revision=revisao).sha, "revisao")
        if tokenizador_id == modelo_id and tokenizador_revisao == revisao:
            sha_tokenizador = sha_modelo
        else:
            sha_tokenizador = _revisao_fixa(
                api.model_info(tokenizador_id, revision=tokenizador_revisao).sha,
                "tokenizador_revisao",
            )
        opcoes = {"modelo_id": modelo_id, "revisao": sha_modelo,
                  "tokenizador_id": tokenizador_id, "tokenizador_revisao": sha_tokenizador,
                  "dispositivo": dispositivo, "precisao": precisao,
                  "limite_tokens": 512, "local_files_only": True}
        # Validação pura, sem carregar dependências de inferência ou pesos.
        GeradorE5(**opcoes)
        if baixar:
            modelos = {(modelo_id, sha_modelo), (tokenizador_id, sha_tokenizador)}
            for identificacao, sha in sorted(modelos):
                hub.snapshot_download(repo_id=identificacao, revision=sha,
                    allow_patterns=["*.json", "*.safetensors", "*.safetensors.index.json",
                                    "*.bin", "*.model", "*.txt"])
    except (ErroModelo, ErroConfiguracao):
        raise
    except Exception as exc:
        raise ErroModelo("Não foi possível consultar/baixar a revisão oficial no Hugging Face. "
                         "Nenhuma revisão provisória será usada. "
                         f"Detalhe: {exc}") from exc
    caminho = Path(caminho_config)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    dados = {"schema_version": "1.0.0", **opcoes}
    temporario = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=caminho.parent,
                                         delete=False) as arquivo:
            temporario = Path(arquivo.name)
            json.dump(dados, arquivo, ensure_ascii=False, indent=2, allow_nan=False)
            arquivo.write("\n")
        os.replace(temporario, caminho)
    finally:
        if temporario is not None and temporario.exists():
            temporario.unlink()
    return dados


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)
    preparar = sub.add_parser("preparar", help="Resolve e grava revisões oficiais imutáveis.")
    preparar.add_argument("--modelo", default=MODELO_PADRAO)
    preparar.add_argument("--revisao", default="main", help="Referência resolvida no setup; grava somente SHA.")
    preparar.add_argument("--tokenizador")
    preparar.add_argument("--tokenizador-revisao")
    preparar.add_argument("--dispositivo", default="cpu")
    preparar.add_argument("--precisao", choices=("float32", "float16"), default="float32")
    preparar.add_argument("--config", type=Path, default=CONFIG_PADRAO)
    preparar.add_argument("--baixar", action="store_true")
    args = parser.parse_args(argv)
    try:
        dados = preparar_configuracao(modelo_id=args.modelo, revisao=args.revisao,
            tokenizador_id=args.tokenizador, tokenizador_revisao=args.tokenizador_revisao,
            dispositivo=args.dispositivo, precisao=args.precisao, baixar=args.baixar,
            caminho_config=args.config)
    except (ErroModelo, ErroConfiguracao, OSError) as exc:
        parser.exit(2, f"Preparação não concluída: {exc}\n")
    print(json.dumps({"configuracao": str(args.config), "modelo": dados["modelo_id"],
                      "revisao": dados["revisao"], "pesos_baixados": args.baixar},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
