"""Teste público de HTTPS, sem chave de API, relato ou geração paga.

Execute com ``python -m agente_analista.diagnosticar_conexao`` no ambiente do
agente. O teste não altera certificados, arquivos, dados ou configuração.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
import platform
import re
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener

import api_narrativas


_ENDPOINTS = {
    "openrouter": ("openrouter.ai", "https://openrouter.ai/api/v1/models"),
    "openai": ("api.openai.com", "https://api.openai.com/v1/models"),
}
_MODELOS = {"openrouter": "openai/gpt-4.1-mini", "openai": "gpt-4.1-mini"}
_TIMEOUT_SEGUNDOS = 20
_CERTIFICADOS_MAC = Path("/Applications/Python 3.12/Install Certificates.command")
_VARIAVEIS_PROXY = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")


def _erro_conexao(erro):
    """Classifique sem devolver mensagens que possam conter URLs privadas."""
    causa = erro.reason if isinstance(erro, URLError) else erro
    if isinstance(causa, ssl.SSLCertVerificationError):
        return "certificado_tls", None
    if isinstance(causa, ssl.SSLError):
        return "negociacao_tls", None
    if isinstance(causa, (socket.timeout, TimeoutError)):
        return "tempo_esgotado", None
    if isinstance(causa, socket.gaierror):
        return "dns", None
    # urllib informa a rejeição CONNECT do proxy como OSError, sem atributo
    # de status. Só extraia o número desse formato conhecido; nunca a mensagem.
    if isinstance(causa, OSError):
        for argumento in causa.args:
            if isinstance(argumento, str):
                status = re.match(r"^Tunnel connection failed:\s*([1-5]\d{2})\b", argumento)
                if status:
                    return "proxy", int(status.group(1))
        if isinstance(causa, ConnectionRefusedError) or causa.errno == errno.ECONNREFUSED:
            return "conexao_recusada", None
    return "conexao", None


def _orientacao_tls():
    if platform.system() == "Darwin" and _CERTIFICADOS_MAC.is_file():
        print("Para instalar os certificados do Python 3.12, execute no Terminal:")
        print('open "/Applications/Python 3.12/Install Certificates.command"')
        print("Depois, execute este diagnóstico novamente.")
    else:
        print("Revise os certificados da instalação do Python e os certificados exigidos pela sua rede.")
    print("Mantenha a validação HTTPS ativada.")


def _mostrar_falha(tipo, status, host, proxy_configurado):
    print(f"Falha de conexão: {tipo}; destino: {host}.")
    if tipo == "configuracao_certificados":
        print("Não foi possível carregar os certificados HTTPS do Python.")
        print("Confira os caminhos de certificados configurados no ambiente.")
        _orientacao_tls()
    elif tipo == "certificado_tls":
        print("O Python não conseguiu validar o certificado HTTPS.")
        _orientacao_tls()
    elif tipo == "negociacao_tls":
        print("A negociação HTTPS falhou. Revise a conexão, VPN e proxy da rede.")
    elif tipo == "dns":
        print("A resolução DNS falhou. Confira a internet e as configurações de DNS, VPN ou proxy.")
    elif tipo == "tempo_esgotado":
        print("O tempo de conexão esgotou. Confira a internet, VPN ou proxy e tente novamente.")
    elif tipo == "proxy":
        print(f"O proxy recusou a conexão HTTPS (HTTP {status}). Revise suas configurações ou contate o administrador da rede.")
    elif tipo == "conexao_recusada":
        print("A conexão foi recusada. Confira a rede, VPN, firewall ou proxy.")
    else:
        print("Não foi possível estabelecer HTTPS. Confira a internet, VPN, firewall ou proxy.")
    if proxy_configurado:
        print("Há uma variável de proxy configurada; os valores foram omitidos.")


def _mostrar_http(host, status, provedor):
    print(f"Conexão HTTPS estabelecida com {host}; HTTP {status}.")
    if status == 401 and provedor == "openai":
        print("O HTTP 401 é esperado: este teste público não envia chave de API.")
    elif 300 <= status < 400:
        print("O servidor respondeu com um redirecionamento; o teste não o seguiu.")
    elif status >= 400:
        print("O servidor foi alcançado, mas recusou ou não concluiu a consulta pública.")
    print("Este teste não verifica a chave de API, o saldo ou a disponibilidade do modelo.")


def main():
    """Retorne zero ao alcançar HTTPS e um para falha de conexão/configuração."""
    provedor = os.environ.get("AGENTE_ANALISTA_PROVEDOR", "openrouter")
    if provedor not in _ENDPOINTS:
        print("Configuração inválida: AGENTE_ANALISTA_PROVEDOR deve ser openrouter ou openai.")
        return 1
    modelo = os.environ.get("AGENTE_ANALISTA_MODELO", _MODELOS[provedor])
    if not modelo or len(modelo) > 200 or not re.fullmatch(r"[A-Za-z0-9._:/-]+", modelo):
        print("Configuração inválida: AGENTE_ANALISTA_MODELO deve conter um identificador de modelo.")
        return 1
    host, endpoint = _ENDPOINTS[provedor]
    proxy_configurado = any(bool(os.environ.get(nome)) for nome in _VARIAVEIS_PROXY)
    print(f"Diagnóstico público de HTTPS: {provedor} ({host}).")
    print("Sem chave de API, sem envio de relato e sem geração de texto.")
    pedido = Request(endpoint, method="GET", headers={"Accept": "application/json"})
    try:
        cliente = build_opener(
            api_narrativas._SemRedirecionamento(),
            HTTPSHandler(context=api_narrativas._contexto_https()),
        )
        with cliente.open(pedido, timeout=_TIMEOUT_SEGUNDOS) as resposta:
            status = resposta.getcode()
    except api_narrativas.ErroAPINarrativa:
        _mostrar_falha("configuracao_certificados", None, host, proxy_configurado)
        return 1
    except HTTPError as erro:
        status = erro.code
        erro.close()
    except (URLError, OSError) as erro:
        tipo, status = _erro_conexao(erro)
        _mostrar_falha(tipo, status, host, proxy_configurado)
        return 1
    _mostrar_http(host, status, provedor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
