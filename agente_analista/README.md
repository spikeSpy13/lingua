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

O iniciador usa o E5 que você já preparou, instala as dependências da página
(Flask 3.1.3 e Certifi 2026.7.22) quando necessário, testa a conexão HTTPS
com uma consulta pública e abre o servidor sem pedir uma chave no terminal.

Abra **http://127.0.0.1:5002** no navegador. Deixe o terminal aberto enquanto usa
a página; pressione `Ctrl+C` para encerrar. A primeira busca carrega o E5 na
memória; as seguintes reutilizam o modelo e o índice.

Na seção **Justificativas**, escolha o provedor, cole a chave de API no campo
oculto e use **+** para incluir o identificador de um modelo. O seletor começa
sem modelo selecionado, sem modelos predefinidos. Ao incluir um modelo, ele
é selecionado para a próxima busca. Informe o relato e clique em **Buscar ligações**.

Se ainda não preparou o modelo, instale Python 3.12 e execute antes:

```bash
bash agente_analista/preparar_e5_mac_intel.sh
```

O modelo fica em `instance/huggingface`, o ambiente Python em
`instance/venv-agente-e5-intel` e a configuração em
`instance/agente_analista_e5.json`. O acervo já está vetorizado: a busca gera
somente os vetores das consultas.

### Chave e modelo das justificativas

A configuração é feita na página. OpenRouter aparece como provedor inicial;
OpenAI também pode ser escolhido. Cada modelo adicionado pertence ao provedor
selecionado, e a lista mostra somente os modelos desse provedor. Exemplos de
identificadores: `openai/gpt-4.1-mini` no OpenRouter e `gpt-4.1-mini` na OpenAI.
Antes de buscar, o servidor consulta o catálogo público do OpenRouter para
conferir o identificador e as capacidades do modelo selecionado. Se houver
suporte a respostas estruturadas, usa **JSON Schema**; se houver somente modo
JSON, usa um objeto JSON; nos demais modelos, solicita JSON pelas instruções.
Todas as respostas passam pelas mesmas conferências locais de estrutura,
citações e fontes. Modelos com o sufixo `:free` continuam com esse identificador;
não há troca automática de modelo nem repetição da chamada de avaliação.

A consulta ao catálogo não envia chave ou relato e não gera texto. As capacidades
ficam em memória por cinco minutos. Se o catálogo estiver indisponível ou não
informar as capacidades, a busca para com uma orientação específica. Para
OpenAI, a avaliação continua usando JSON Schema. Quando um formato é exigido
no OpenRouter, a requisição seleciona uma rota que respeite esse formato.

Somente a lista de identificadores dos modelos é guardada no navegador. A
chave fica no campo oculto enquanto a página está aberta e é enviada ao servidor
local para cada busca. Não é gravada em arquivos, no armazenamento do navegador
ou nos resultados exportados; ao recarregar a página, o campo começa vazio.
Não há necessidade de configurar variáveis no terminal. A configuração enviada
na página é usada somente naquela busca, sem alterar as credenciais do projeto
Lingua ou as variáveis de ambiente do servidor.

O E5 e o índice funcionam localmente. Para construir as justificativas, o
provedor recebe o relato e os blocos recuperados por HTTPS; essa chamada usa
a conta do provedor e pode consumir créditos. A busca é liberada após informar
a chave, selecionar o modelo e preencher um relato válido. Os relatos e resultados ficam
somente em memória no servidor, com até oito buscas e disponibilidade por uma
hora. Resultados expirados são removidos nos próximos acessos; encerrar o
servidor libera todos eles. O usuário pode salvar o resultado por **Exportar JSON**.

### Executar diretamente no ambiente Python

Depois de instalar `agente_analista/requirements.txt` no ambiente com E5:

```bash
instance/venv-agente-e5-intel/bin/python -m agente_analista.app
```

O servidor escuta somente em `127.0.0.1:5002`, separado do Lingua na porta 5001.
Usa uma execução por vez e mostra o progresso na página. Configure o cache via
`HF_HOME`/`HF_HUB_CACHE` se usar outro caminho.

### Diagnosticar a conexão com o provedor

O iniciador executa um diagnóstico público do provedor padrão ao abrir o servidor.
Esse teste usa OpenRouter por padrão; a escolha do provedor das buscas é feita
na página. O teste
apenas consulta o endereço do provedor por GET, sem autenticação, envio do
relato ou geração de texto; não consome créditos. Para executá-lo separadamente:

```bash
instance/venv-agente-e5-intel/bin/python -m agente_analista.diagnosticar_conexao
```

As mensagens distinguem certificados HTTPS, DNS, tempo de conexão e bloqueios
de proxy. Receber HTTP 401 no teste da OpenAI é esperado, pois a consulta não
envia chave; confirma conexão HTTPS, sem verificar autenticação ou saldo.

O transporte da API soma os certificados do Certifi aos certificados nativos
do Python, preservando a verificação de certificado e de hostname. Isso atende
ao Python instalado pelo site oficial no Mac, mesmo quando os certificados
globais ainda não foram preparados. Caminhos explícitos `SSL_CERT_FILE`,
`SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE` e `CURL_CA_BUNDLE` são respeitados.

Se o diagnóstico ainda indicar falha de certificado e o instalador oficial
estiver disponível, execute:

```bash
open "/Applications/Python 3.12/Install Certificates.command"
```

Depois, repita o diagnóstico. Se houver proxy ou VPN na rede, siga a indicação
do diagnóstico. Chave inválida e saldo insuficiente têm mensagens próprias
quando a chamada de avaliação consegue alcançar o provedor.

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
- `modelos.py` consulta o catálogo do OpenRouter e ajusta o formato aceito pelo
  modelo escolhido antes da busca, sem mudar seu identificador.
- `ligacoes.py` usa um prompt próprio e o formato aceito pelo modelo para propor relações,
  considerando o relato completo, negações, contexto, limites e alternativas.
  Intervalos de citações são oferecidos com posições já calculadas. O servidor
  rejeita passagens, IDs ou intervalos sem correspondência literal e associa
  somente as referências e pontuações provenientes do índice.
  Se os contextos excederem o limite de envio, candidatos inteiros são retirados
  do fim do ranking até o pedido caber. Os textos continuam intactos; o JSON e
  a inspeção registram quais blocos foram efetivamente avaliados.
  O limite inclui o esquema JSON e os parâmetros de roteamento no pedido real.
  Uma resposta inteiramente envolvida em um único bloco Markdown `json` também
  pode ser lida, sem alterar o JSON ou suas citações. Respostas incompletas,
  texto externo ao JSON, campos repetidos e valores inválidos são rejeitados.
  A conferência das fontes continua obrigatória em todos os formatos; não há
  novas tentativas automáticas ou troca automática de modelo.
  Os pedidos OpenRouter não incluem o parâmetro redundante `n`; o retorno
  continua limitado à conferência de uma única resposta completa.

Erros de HTTP 400, 404 e 422 incluem o código e distinguem causas conhecidas,
como modelo ausente, rota incompatível e contexto acima da janela do modelo.
O corpo de erro do provedor é usado somente para essa classificação; não é
mostrado na página ou escrito nos resultados.

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
inexistentes, relações sem sustentação, o fluxo assíncrono e a separação das
credenciais de cada busca. As respostas do
provedor são simuladas nos testes para evitar chamadas cobradas. O teste E5
opcional executa inferência real na revisão local do modelo.

O código antigo continua guardado, sem execução automática, em
`codigos/freud-agent-codigo-20261003-165655.zip`.
