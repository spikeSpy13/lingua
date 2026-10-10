#!/usr/bin/env bash
# E5 em ambiente separado para Mac Intel; também permite validar em Linux CPU.
set -euo pipefail
E5_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$E5_SCRIPT_DIR/.."
if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" != "x86_64" ]]; then
  echo "Este instalador é destinado a Mac Intel." >&2
  exit 1
fi
if ! command -v python3.12 >/dev/null 2>&1; then
  echo "Instale Python 3.12 antes de executar este instalador." >&2
  exit 1
fi
python3.12 -m venv instance/venv-agente-e5-intel
E5_INTEL_PYTHON="$PWD/instance/venv-agente-e5-intel/bin/python"
if [[ "$(uname -s)" == "Linux" ]]; then
  "$E5_INTEL_PYTHON" -m pip install --no-cache-dir torch==2.2.2 --index-url https://download.pytorch.org/whl/cpu
fi
"$E5_INTEL_PYTHON" -m pip install --no-cache-dir torch==2.2.2 numpy==1.26.4 transformers==4.51.3 huggingface-hub==0.30.2 tokenizers==0.21.2 safetensors==0.5.3
"$E5_INTEL_PYTHON" -m pip check
export HF_HOME="$PWD/instance/huggingface"
export HF_HUB_CACHE="$HF_HOME/hub"
export OMP_NUM_THREADS=2
export MKL_NUM_THREADS=2
"$E5_INTEL_PYTHON" - <<'PY'
"""Prepara o E5 do corpus sem baixar distribuições alternativas dos pesos."""

from pathlib import Path
import hashlib
import json
import os
import re
import sys
from urllib.parse import urlparse

RAIZ = Path.cwd()
os.environ.setdefault("HF_HOME", str(RAIZ / "instance" / "huggingface"))
os.environ.setdefault("HF_HUB_CACHE", str(Path(os.environ["HF_HOME"]) / "hub"))
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
sys.path.insert(0, str(RAIZ))


def main():
    from huggingface_hub import HfApi, snapshot_download
    from embeddings_e5 import preparar_configuracao

    manifesto = json.loads((RAIZ / "agente_analista/data/Vetor/manifesto.json").read_text())
    modelo, revisao = manifesto["modelo"], manifesto["revisao"]
    info = HfApi().model_info(modelo, revision=revisao, files_metadata=True)
    if info.sha != revisao:
        raise ValueError("A revisão oficial difere do manifesto.")
    arquivos = ["config.json", "model.safetensors", "sentencepiece.bpe.model",
                "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"]
    por_nome = {arquivo.rfilename: arquivo for arquivo in info.siblings}
    if any(nome not in por_nome for nome in arquivos):
        raise ValueError("O snapshot não contém os arquivos necessários.")
    peso = por_nome["model.safetensors"]
    if peso.lfs is None or not peso.lfs.sha256:
        raise ValueError("O snapshot não informa o SHA-256 dos pesos.")
    snapshot = Path(snapshot_download(modelo, revision=revisao,
                                     allow_patterns=arquivos, max_workers=2))
    caminho_pesos = snapshot / "model.safetensors"
    with caminho_pesos.open("rb") as arquivo:
        sha = hashlib.file_digest(arquivo, "sha256").hexdigest()
    if sha != peso.lfs.sha256 or caminho_pesos.stat().st_size != peso.size:
        raise ValueError("Os pesos não passaram na conferência oficial de integridade.")
    preparar_configuracao(modelo_id=modelo, revisao=revisao,
                         dispositivo="cpu", precisao="float32", baixar=False,
                         caminho_config=RAIZ / "instance/agente_analista_e5.json")
    print(json.dumps({"status": "preparado", "modelo": modelo,
                      "revisao": revisao, "sha256_pesos_verificado": sha,
                      "arquivos": len(arquivos)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as erro:
        hosts = sorted({urlparse(url).hostname for url in
                        re.findall(r"https?://[^\s'\"<>]+", str(erro))
                        if urlparse(url).hostname})
        print(f"Preparação E5 não concluída: {type(erro).__name__}; "
              f"destinos envolvidos: {', '.join(hosts) or 'não identificados'}.",
              file=sys.stderr)
        raise SystemExit(1) from None

PY
"$E5_INTEL_PYTHON" - <<'PY'
"""Confere consultas E5 e compara duas passagens com o índice fornecido."""

from pathlib import Path
import json
import os
import sys

RAIZ = Path.cwd()
os.environ.setdefault("HF_HOME", str(RAIZ / "instance" / "huggingface"))
os.environ.setdefault("HF_HUB_CACHE", str(Path(os.environ["HF_HOME"]) / "hub"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")
sys.path.insert(0, str(RAIZ))


def main():
    import numpy as np
    from embeddings_e5 import criar_gerador_padrao

    pasta = RAIZ / "agente_analista/data/Vetor"
    manifesto = json.loads((pasta / "manifesto.json").read_text())
    with (pasta / manifesto["referencias_arquivo"]).open(encoding="utf-8") as arquivo:
        fragmentos = [json.loads(linha) for linha in arquivo]
    matriz = np.load(pasta / manifesto["vetores_arquivo"], mmap_mode="r", allow_pickle=False)
    if matriz.shape != (len(fragmentos), manifesto["dimensao"]):
        raise ValueError("A matriz não corresponde aos fragmentos.")
    gerador = criar_gerador_padrao(caminho_config=RAIZ / "instance/agente_analista_e5.json")
    descricao = gerador.descrever()
    if (descricao["identificacao"] != manifesto["modelo"]
            or descricao["revisao"] != manifesto["revisao"]
            or descricao["dimensao"] != manifesto["dimensao"]):
        raise ValueError("O gerador não corresponde ao manifesto do corpus.")

    def gerar(texto, prefixo):
        tokens = gerador.tokenizar(texto, prefixo)
        if len(tokens["input_ids"]) > manifesto["tokens_maximo"]:
            raise ValueError("Entrada de validação acima do limite de tokens.")
        vetor = np.asarray(gerador.gerar([{"texto": prefixo + texto, **tokens}])[0],
                           dtype=np.float32)
        if (vetor.shape != (manifesto["dimensao"],) or not np.isfinite(vetor).all()
                or abs(float(np.linalg.norm(vetor)) - 1) > 1e-5):
            raise ValueError("O vetor não é finito, normalizado ou da dimensão esperada.")
        return vetor

    similaridades = []
    for indice in [0, len(fragmentos) - 1]:
        fragmento = fragmentos[indice]
        vetor = gerar(fragmento["cabecalho"] + "\n" + fragmento["texto"],
                      manifesto["prefixo_passagens"])
        similaridade = float(np.dot(vetor, matriz[indice]))
        if similaridade < 0.999:
            raise ValueError("Passagem regenerada incompatível com o vetor registrado.")
        similaridades.append(similaridade)
    consulta = gerar("Uma lembrança retorna nos sonhos e provoca sentimentos ambíguos.",
                     manifesto["prefixo_consultas"])
    repetida = gerar("Uma lembrança retorna nos sonhos e provoca sentimentos ambíguos.",
                     manifesto["prefixo_consultas"])
    if not np.allclose(consulta, repetida, atol=1e-5, rtol=0):
        raise ValueError("A consulta não passou na conferência de determinismo.")
    scores = matriz @ consulta
    if scores.shape != (len(fragmentos),) or not np.isfinite(scores).all():
        raise ValueError("A consulta não pontuou o corpus completo corretamente.")
    print(json.dumps({"status": "validado", "dimensao": len(consulta),
                      "fragmentos_pontuados": len(scores),
                      "similaridades_passagens_amostrais": similaridades,
                      "consulta_repetida_consistente": True,
                      "ambiente": descricao["ambiente"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

PY
echo "E5 preparado e validado em instance/venv-agente-e5-intel; configuração em instance/agente_analista_e5.json."
