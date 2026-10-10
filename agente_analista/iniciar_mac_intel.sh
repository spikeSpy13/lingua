#!/usr/bin/env bash
# Execute na pasta do repositório; a credencial permanece só neste processo.
set -euo pipefail
AGENTE_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$AGENTE_SCRIPT_DIR/.."
AGENTE_PYTHON="$PWD/instance/venv-agente-e5-intel/bin/python"
if [[ ! -x "$AGENTE_PYTHON" || ! -f instance/agente_analista_e5.json ]]; then
  echo "Prepare o E5 primeiro: bash agente_analista/preparar_e5_mac_intel.sh" >&2
  exit 1
fi
if ! "$AGENTE_PYTHON" -c 'from importlib.metadata import version; assert version("Flask") == "3.1.3"; assert version("certifi") == "2026.7.22"' >/dev/null 2>&1; then
  "$AGENTE_PYTHON" -m pip install -r agente_analista/requirements.txt
fi
export HF_HOME="${HF_HOME:-$PWD/instance/huggingface}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-$HF_HOME/hub}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-2}"
export AGENTE_ANALISTA_PROVEDOR="${AGENTE_ANALISTA_PROVEDOR:-openrouter}"
if ! "$AGENTE_PYTHON" -m agente_analista.diagnosticar_conexao; then
  echo "A página será aberta; resolva o problema de conexão indicado antes de buscar ligações." >&2
fi
if [[ -z "${NARRATIVA_API_KEY:-}" ]]; then
  if [[ -t 0 ]]; then
    read -r -s -p "Chave de API do $AGENTE_ANALISTA_PROVEDOR (entrada oculta; Enter abre a página sem configurar a API): " NARRATIVA_API_KEY
    printf '\n'
    export NARRATIVA_API_KEY
  fi
fi
echo "Abra http://127.0.0.1:5002 no navegador. Para encerrar, pressione Ctrl+C."
exec "$AGENTE_PYTHON" -m agente_analista.app
