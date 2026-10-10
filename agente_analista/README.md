# Agente analista: relato e Freud

Página local para examinar ligações entre um relato em português e o acervo
fornecido. Recebe exatamente dois parágrafos e até 400 palavras, pesquisa todos
os 8.087 fragmentos e termina em uma tabela de propostas interpretativas.

## Executar no Mac Intel

Na pasta do repositório:

```bash
cd ~/lingua-agente
git -c http.version=HTTP/1.1 pull --ff-only
bash agente_analista/iniciar_mac_intel.sh
```

O iniciador usa o E5 que você já preparou, instala a dependência da página
(Flask 3.1.3) quando necessário e solicita a chave do OpenRouter com entrada
oculta. Cole a chave e pressione Enter; ela permanece somente no processo do
servidor, sem ser escrita no repositório ou enviada ao navegador.

Abra **http://127.0.0.1:5002** no navegador. Deixe o terminal aberto enquanto usa
a página; pressione `Ctrl+C` para encerrar. A primeira busca carrega o E5 na
memória; as seguintes reutilizam o modelo e o índice.

Se ainda não preparou o modelo, instale Python 3.12 e execute antes:

```bash
bash agente_analista/preparar_e5_mac_intel.sh
```

O modelo fica em `instance/huggingface`, o ambiente Python em
`instance/venv-agente-e5-intel` e a configuração em
`instance/agente_analista_e5.json`. O acervo já está vetorizado: a busca gera
somente os vetores das consultas.

### Provedor das justificativas

Reutilizamos o transporte e a credencial `NARRATIVA_API_KEY` do projeto. O padrão
é OpenRouter com `openai/gpt-4.1-mini`; para usar a chave da OpenAI:

```bash
export AGENTE_ANALISTA_PROVEDOR=openai
export AGENTE_ANALISTA_MODELO=gpt-4.1-mini
bash agente_analista/iniciar_mac_intel.sh
```

Para outro modelo, ajuste `AGENTE_ANALISTA_MODELO` ao identificador do provedor.
Uma `NARRATIVA_API_KEY` já exportada é aproveitada pelo iniciador. Não é preciso
colar a chave em comandos ou arquivos. Arquivos `.env` não são carregados
automaticamente.

O E5 e o índice funcionam localmente. Para construir as justificativas, o
provedor recebe o relato e os blocos recuperados por HTTPS; essa chamada usa
a conta do provedor e pode consumir créditos. Sem chave, a página informa a
configuração necessária e não inventa análises. Os relatos e resultados ficam
somente em memória no servidor, com até oito buscas e disponibilidade por uma
hora. Resultados expirados são removidos nos próximos acessos; encerrar o
servidor libera todos eles. O usuário pode salvar o resultado por **Exportar JSON**.

### Executar diretamente no ambiente Python

Depois de instalar `agente_analista/requirements.txt` no ambiente com E5 e
configurar a chave no processo:

```bash
instance/venv-agente-e5-intel/bin/python -m agente_analista.app
```

O servidor escuta somente em `127.0.0.1:5002`, separado do Lingua na porta 5001.
Usa uma execução por vez e mostra o progresso na página. Configure o cache via
`HF_HOME`/`HF_HUB_CACHE` se usar outro caminho.

## Como a busca funciona

- `entrada.py` conta palavras por espaços em branco e localiza os parágrafos,
  aceitando separadores com linhas vazias contendo espaços/tabulações. O texto
  recebido permanece intacto. Todas as posições usam pontos de código Unicode,
  base zero, intervalo `[inicio, fim)`; emojis contam como um ponto de código.
- `corpus.py` carrega o manifesto e a matriz por mmap, valida IDs, dimensões,
  valores finitos, normalização L2 e hashes dos textos. Reconstrói blocos pelas
  posições, reúne sobreposições idênticas e rejeita lacunas/conflitos.
- `busca.py` prepara consultas de P1, P2 e do relato completo. Divide consultas
  longas em recortes rastreáveis de até 504 tokens, incluindo o prefixo, sem
  truncar o conteúdo. Reutiliza o adaptador `embeddings_e5.py` e o divisor de
  `vetorizacao.py`, com modelo, revisão e prefixos do manifesto.
- A busca lexical usa BM25 (`k1=1.2`, `b=0.75`), palavras em minúsculas sem
  acentos, remoção de palavras funcionais e preservação de negações. O código
  anterior no ZIP serviu de consulta para a fórmula; radicalização não é usada.
- A busca E5 calcula similaridade cosseno com todas as linhas da matriz. A fusão
  usa RRF: soma `1/(60 + rank_bloco)` por método e consulta, sobre os 50 melhores
  fragmentos. Cada bloco contribui uma vez por ranking. Os 12 melhores blocos
  distintos seguem com seu contexto inteiro para avaliação; nada é filtrado
  previamente por obra ou conceito. Os parâmetros saem no JSON.
- `ligacoes.py` usa um prompt próprio com resposta JSON para propor relações,
  considerando o relato completo, negações, contexto, limites e alternativas.
  Intervalos de citações são oferecidos com posições já calculadas. O servidor
  rejeita passagens, IDs ou intervalos sem correspondência literal e associa
  somente as referências e pontuações provenientes do índice.
  Se os contextos excederem o limite de envio, candidatos inteiros são retirados
  do fim do ranking até o pedido caber. Os textos continuam intactos; o JSON e
  a inspeção registram quais blocos foram efetivamente avaliados.

## Examinar os resultados

Cada linha apresenta passagem do relato, trecho de Freud, ligação proposta,
justificativa, referência e limites/situação. Os detalhes permitem inspecionar
o contexto, IDs, posições e pontuações. Relações descartadas e propostas que
falharam na conferência ficam em áreas separadas. Há controles para limpar,
copiar a tabela e exportar o JSON completo.

Uma busca pode terminar sem ligações utilizáveis. Isso significa que os
candidatos examinados não sustentaram uma relação suficiente, não que o acervo
inteiro careça de material pertinente. Conferências automáticas de integridade
não substituem a avaliação da pertinência pelo usuário.

As páginas são apresentadas como **“páginas registradas no índice; numeração
impressa não conferida”**. Edição, tradutor, editora e ano da edição permanecem
pendentes quando ausentes. A tabela encerra esta etapa; a escrita da resposta
final e a integração com os JSONs linguísticos ficam para etapas posteriores.

## Verificar

No ambiente preparado:

```bash
instance/venv-agente-e5-intel/bin/python -m unittest discover -s tests -p 'test_agente_analista_*.py' -v
AGENTE_ANALISTA_TESTE_E5_REAL=1 instance/venv-agente-e5-intel/bin/python -m unittest tests.test_agente_analista_busca -v
```

Os testes verificam limites de entrada, preservação Unicode e posições,
integridade e reconstrução do corpus, busca e agrupamento, citações e IDs
inexistentes, relações sem sustentação e o fluxo assíncrono. As respostas do
provedor são simuladas nos testes para evitar chamadas cobradas. O teste E5
opcional executa inferência real na revisão local do modelo.

O código antigo continua guardado, sem execução automática, em
`codigos/freud-agent-codigo-20261003-165655.zip`.
