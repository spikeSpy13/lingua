"""Servidor local separado do Lingua, com uma busca de cada vez e progresso."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from .entrada import ErroEntrada, validar_relato


RAIZ = Path(__file__).resolve().parents[1]
CONFIG_E5 = RAIZ / "instance/agente_analista_e5.json"


class ServicoAnalista:
    """Reutilize corpus, BM25 e modelo entre buscas; nunca persista o relato."""

    def __init__(self, pasta=None, caminho_config=None):
        self.pasta = Path(pasta or RAIZ / "agente_analista/data/Vetor")
        self.caminho_config = Path(caminho_config or CONFIG_E5)
        self.corpus = None
        self.buscador = None
        self._lock = Lock()
        os.environ.setdefault("HF_HOME", str(RAIZ / "instance/huggingface"))
        os.environ.setdefault("HF_HUB_CACHE", str(Path(os.environ["HF_HOME"]) / "hub"))
        os.environ.setdefault("OMP_NUM_THREADS", "2")
        os.environ.setdefault("MKL_NUM_THREADS", "2")

    def _corpus(self):
        from .corpus import Corpus
        with self._lock:
            if self.corpus is None:
                self.corpus = Corpus(self.pasta)
            return self.corpus

    def status(self):
        problemas = []
        try:
            corpus = self._corpus()
            contagens = {"fragmentos": len(corpus.fragmentos), "blocos": len(corpus.blocos)}
        except (ValueError, ImportError) as erro:
            corpus = None
            contagens = {}
            problemas.append(str(erro) if isinstance(erro, ValueError) else
                             "Instale as dependências executando: bash agente_analista/preparar_e5_mac_intel.sh")
        try:
            configuracao = json.loads(self.caminho_config.read_text(encoding="utf-8"))
            if not isinstance(configuracao, dict):
                raise ValueError("A configuração E5 deve ser um objeto JSON.")
            esperado = corpus.manifesto if corpus else {}
            for campo, manifesto in (("modelo_id", "modelo"), ("revisao", "revisao"),
                                     ("tokenizador_id", "modelo"), ("tokenizador_revisao", "revisao")):
                if corpus and configuracao.get(campo) != esperado[manifesto]:
                    raise ValueError("A configuração E5 difere do manifesto do corpus.")
            if configuracao.get("local_files_only") is not True:
                raise ValueError("Configure o E5 para usar somente o cache local já verificado.")
            if (configuracao.get("dispositivo") != "cpu" or configuracao.get("precisao") != "float32"
                    or configuracao.get("limite_tokens") != 512):
                raise ValueError("Prepare a configuração CPU float32 com limite de 512 tokens.")
            modelo = configuracao["modelo_id"]
            revisao = configuracao["revisao"]
            if not isinstance(modelo, str) or not isinstance(revisao, str):
                raise ValueError("Modelo e revisão devem ser textos.")
            snapshot = Path(os.environ["HF_HUB_CACHE"]) / ("models--" + modelo.replace("/", "--")) / "snapshots" / revisao
            if any(not (snapshot / nome).is_file() for nome in (
                    "config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json")):
                raise ValueError("O download do modelo E5 está ausente ou incompleto.")
        except (OSError, ValueError, KeyError, TypeError):
            problemas.append("Prepare o modelo executando: bash agente_analista/preparar_e5_mac_intel.sh")
        return {"pronto": not problemas, "problemas": problemas, "corpus": contagens}

    def executar(self, relato, progresso, *, provedor, modelo, chave_api):
        from .busca import Buscador
        from .ligacoes import ErroLigacoes, avaliar_ligacoes
        from .modelos import ErroModelo, escolher_formato_resposta
        from embeddings_e5 import criar_gerador_padrao
        progresso("Conferindo o modelo das justificativas")
        try:
            modo_resposta = escolher_formato_resposta(provedor, modelo)
        except ErroModelo as erro:
            raise ErroLigacoes(str(erro)) from None
        progresso("Conferindo o corpus e carregando o modelo E5")
        corpus = self._corpus()
        if self.buscador is None:
            self.buscador = Buscador(corpus, criar_gerador_padrao(caminho_config=self.caminho_config))
        recuperacao = self.buscador.buscar(relato, progresso=progresso)
        progresso("Avaliando os candidatos e conferindo as citações")
        ligacoes = avaliar_ligacoes(relato, recuperacao, provedor=provedor, modelo=modelo,
                                   chave_api=chave_api, modo_resposta=modo_resposta)
        return {"relato": relato, **recuperacao, **ligacoes,
                "justificativas": {"provedor": provedor, "modelo": modelo, "formato_resposta": modo_resposta},
                "posicoes": "Pontos de código Unicode, início inclusivo e fim exclusivo [inicio, fim).",
                "aviso": "Pontuações ordenam candidatos; a pertinência interpretativa exige revisão."}


def criar_app(config=None, *, servico=None):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=256 * 1024)
    if config:
        app.config.update(config)
    servico = servico or ServicoAnalista()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agente-analista")
    lock = Lock()
    tarefas = {}
    estado = {"ativa": None}
    app.extensions["agente_analista"] = {"servico": servico, "executor": executor, "tarefas": tarefas}

    @app.before_request
    def somente_local():
        hostname = urlsplit("http://" + request.host).hostname
        if hostname not in ("localhost", "127.0.0.1", "::1"):
            return jsonify(erro="Acesse a página pelo endereço local 127.0.0.1 ou localhost."), 403
        if request.method == "POST":
            origem = request.headers.get("Origin")
            if origem and urlsplit(origem).netloc != request.host:
                return jsonify(erro="Abra a página local para iniciar a busca."), 403

    @app.after_request
    def sem_cache(resposta):
        resposta.headers["Cache-Control"] = "no-store"
        resposta.headers["X-Content-Type-Options"] = "nosniff"
        resposta.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return resposta

    @app.get("/")
    def pagina():
        return render_template("index.html")

    @app.get("/api/status")
    def status():
        with lock:
            limpar_expiradas()
        return jsonify(servico.status())

    def limpar_expiradas():
        agora = monotonic()
        for id in list(tarefas):
            if id != estado["ativa"] and agora - tarefas[id]["criada"] > 3600:
                del tarefas[id]

    def executar(tarefa_id, relato, configuracao_api):
        def progresso(etapa):
            with lock:
                tarefas[tarefa_id]["etapa"] = etapa
        try:
            resultado = servico.executar(relato, progresso, **configuracao_api)
            with lock:
                tarefas[tarefa_id].update(estado="concluido", etapa="Busca concluída", resultado=resultado)
        except Exception as erro:
            from .busca import ErroBusca
            from .corpus import ErroCorpus
            from .ligacoes import ErroLigacoes
            from contratos_vetorizacao import ErroVetorizacao
            conhecidos = (ErroEntrada, ErroBusca, ErroCorpus, ErroLigacoes, ErroVetorizacao)
            mensagem = str(erro) if isinstance(erro, conhecidos) else "Não foi possível concluir a busca. Confira o ambiente e tente novamente."
            if configuracao_api["chave_api"] in mensagem:
                mensagem = "Não foi possível concluir a avaliação. Confira a chave, o provedor e o modelo na página."
            app.logger.error("Busca não concluída (%s)", type(erro).__name__)
            with lock:
                tarefas[tarefa_id].update(estado="erro", etapa="Busca interrompida", erro=mensagem)
        finally:
            configuracao_api.clear()
            with lock:
                estado["ativa"] = None

    @app.post("/api/buscas")
    def iniciar():
        if not request.is_json:
            return jsonify(erro="Envie o relato em JSON."), 415
        dados = request.get_json(silent=True)
        if not isinstance(dados, dict) or not isinstance(dados.get("texto"), str):
            return jsonify(erro="Envie o relato como texto."), 400
        try:
            relato = validar_relato(dados["texto"])
        except ErroEntrada as erro:
            return jsonify(erro=str(erro), contagem=erro.contagem), 400
        from api_narrativas import ErroAPINarrativa, _obter_chave_api
        from .ligacoes import ErroLigacoes, _configuracao
        if not isinstance(dados.get("chave_api"), str) or not dados["chave_api"].strip():
            return jsonify(erro="Informe a chave de API na página para gerar as justificativas."), 400
        if not isinstance(dados.get("modelo"), str) or not dados["modelo"].strip():
            return jsonify(erro="Adicione e selecione um modelo para as justificativas."), 400
        if dados.get("provedor") not in ("openrouter", "openai"):
            return jsonify(erro="Selecione OpenRouter ou OpenAI como provedor das justificativas."), 400
        try:
            provedor, modelo = _configuracao(dados["provedor"], dados["modelo"])
            chave = _obter_chave_api(dados["chave_api"])
        except (ErroAPINarrativa, ErroLigacoes):
            return jsonify(erro="Confira a chave de API e o identificador do modelo informado na página."), 400
        configuracao = servico.status()
        if not configuracao["pronto"]:
            return jsonify(erro=" ".join(configuracao["problemas"]), problemas=configuracao["problemas"]), 503
        with lock:
            if estado["ativa"]:
                return jsonify(erro="Uma busca já está em andamento. Aguarde sua conclusão."), 409
            # Retenção limitada, somente em memória; relatos não são gravados em disco.
            limpar_expiradas()
            agora = monotonic()
            for antiga in list(tarefas):
                if len(tarefas) >= 8:
                    del tarefas[antiga]
            tarefa_id = str(uuid4())
            tarefas[tarefa_id] = {"id": tarefa_id, "estado": "executando", "etapa": "Preparando a busca", "criada": agora}
            estado["ativa"] = tarefa_id
        executor.submit(executar, tarefa_id, relato, {"provedor": provedor, "modelo": modelo, "chave_api": chave})
        return jsonify(id=tarefa_id), 202

    @app.get("/api/buscas/<tarefa_id>")
    def consultar(tarefa_id):
        with lock:
            limpar_expiradas()
            tarefa = tarefas.get(tarefa_id)
            if tarefa is None:
                return jsonify(erro="Busca não encontrada ou expirada. Execute uma nova busca."), 404
            return jsonify({k: v for k, v in tarefa.items() if k != "criada"})

    @app.errorhandler(HTTPException)
    def erro_http(erro):
        mensagem = "O relato excedeu o tamanho permitido para envio." if erro.code == 413 else "Pedido inválido ou endereço não encontrado."
        return jsonify(erro=mensagem), erro.code

    return app


if __name__ == "__main__":
    criar_app().run(host="127.0.0.1", port=5002, debug=False, use_reloader=False)
