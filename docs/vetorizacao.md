# Etapa 09: vetorizar conteúdo e unidades

A etapa 09 recebe uma saída validada e pronta da etapa 08 e gera três tipos
de representação: foco de cada unidade, janela de cada unidade e documento
integral de trabalho. Cada associação aponta para a origem exata, o campo
textual e sua configuração. A origem contextual completa é preservada uma
vez por execução, sem modificar análises linguísticas ou textos anteriores.

`vetorizacao.py` contém o planejamento, os contratos e a validação;
`embeddings_e5.py` adapta tokenização e inferência; `persistencia_vetores.py`
guarda e recupera resultados no SQLite. O núcleo recebe registros Python ou
JSON e não exige documentos previamente salvos nem acesso ao banco.

O adaptador inicial utiliza `intfloat/multilingual-e5-large`, com 1.024
dimensões e limite de 512 tokens. CPU é o padrão, com CUDA opcional. O modelo
e o tokenizador precisam ter revisões imutáveis. Trocar o mecanismo de
embeddings não altera as etapas 01 a 08.

## Instalação opcional e preparação local

As etapas 01 a 08 continuam utilizando apenas `requirements.txt`. A etapa 09
acrescenta dependências opcionais em `requirements-embeddings.txt`. Para as
versões fixadas, recomendamos Python 3.10 a 3.12; a faixa aceita pelas
dependências anteriores não garante disponibilidade dos mesmos wheels de
PyTorch para todas as versões de Python.

Com o aplicativo encerrado, dentro de `linguaSpike`, no macOS:

```bash
git pull --ff-only origin main
.venv/bin/python -m pip install -r requirements-embeddings.txt
.venv/bin/python -m embeddings_e5 preparar --modelo intfloat/multilingual-e5-large --revisao main --baixar
.venv/bin/python app.py
```

No Linux, para CPU:

```bash
git pull --ff-only origin main
.venv/bin/python -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install -r requirements-embeddings.txt
.venv/bin/python -m embeddings_e5 preparar --modelo intfloat/multilingual-e5-large --revisao main --baixar
.venv/bin/python app.py
```

No Windows:

```powershell
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements-embeddings.txt
.\.venv\Scripts\python.exe -m embeddings_e5 preparar --modelo intfloat/multilingual-e5-large --revisao main --baixar
.\.venv\Scripts\python.exe app.py
```

A preparação requer acesso ao Hugging Face. Resolve `main`, ou outra
referência fornecida, para o SHA oficial de 40 caracteres e fixa as revisões
dos pesos e do tokenizador em `instance/embeddings_modelo.json`. Com
`--baixar`, prepara os snapshots no cache local. Sem essa opção, resolve e
salva as revisões, mas os arquivos ainda precisam estar disponíveis antes da
inferência. O cache e `instance/` não são versionados no Git.

Depois da preparação, a inferência usa `local_files_only: true` por padrão;
não baixa pesos implicitamente durante uma requisição. Configuração ausente,
revisão inválida, cache incompleto ou bibliotecas ausentes produzem erro
explícito. O fluxo normal nunca substitui esse erro por embeddings simulados.

O comando aceita também:

| Opção | Uso |
| --- | --- |
| `--modelo` | Repositório dos pesos; padrão `intfloat/multilingual-e5-large`. |
| `--revisao` | Referência resolvida durante a preparação; padrão `main`. |
| `--tokenizador` | Repositório do tokenizador; padrão igual ao modelo. |
| `--tokenizador-revisao` | Referência própria do tokenizador, quando necessário. |
| `--dispositivo` | `cpu`, `cuda`, `cuda:N` ou `auto`. |
| `--precisao` | `float32` ou `float16`; CPU exige `float32`. |
| `--config` | Outro caminho para o manifesto local do modelo. |
| `--baixar` | Baixa os snapshots depois de resolver as revisões. |

## Configuração de CPU, GPU e revisões

O manifesto local tem `schema_version: "1.0.0"` e os campos `modelo_id`,
`revisao`, `tokenizador_id`, `tokenizador_revisao`, `dispositivo`, `precisao`,
`limite_tokens` e `local_files_only`. Seus valores de revisão são commits
oficiais, não a string `main`. O SHA dos pesos não é intercambiável com a
revisão de um tokenizador de outro repositório.

`criar_gerador_padrao()` lê esse arquivo. As variáveis de ambiente
`LINGUA_EMBEDDINGS_CONFIG`, `LINGUA_EMBEDDINGS_MODELO`,
`LINGUA_EMBEDDINGS_REVISAO`, `LINGUA_EMBEDDINGS_TOKENIZADOR`,
`LINGUA_EMBEDDINGS_TOKENIZADOR_REVISAO`, `LINGUA_EMBEDDINGS_DISPOSITIVO` e
`LINGUA_EMBEDDINGS_PRECISAO` permitem escolher outro caminho ou sobrescrever
campos. Opções fornecidas explicitamente à fábrica têm prioridade final.

Para CUDA, instale a distribuição PyTorch 2.6.0 adequada ao seu driver e à
plataforma, seguindo o [seletor oficial](https://pytorch.org/get-started/previous-versions/).
Depois, instale `requirements-embeddings.txt` no mesmo ambiente. A seleção
`cuda` exige CUDA disponível e não muda silenciosamente para CPU. `auto`
seleciona CUDA quando disponível e CPU caso contrário; o dispositivo efetivo
é registrado. `float16` exige dispositivo CUDA neste adaptador.

Pooling e saída são calculados em `float32`, inclusive quando os pesos
utilizam `float16`. A memória disponível pode limitar lote e modelo. Reduza
o lote ou escolha outro dispositivo ao receber erro de memória; o mecanismo
não corta o texto ou troca a precisão automaticamente.

## Uso direto e independência do banco

```python
import json
from embeddings_e5 import criar_gerador_padrao
from vetorizacao import vetorizar_unidades_contexto, validar_vetorizacao

with open("examples/unidades_contexto_literal.json", encoding="utf-8") as arquivo:
    contexto = json.load(arquivo)

gerador = criar_gerador_padrao()
registro = vetorizar_unidades_contexto(
    contexto,
    gerador=gerador,
    configuracao={"perfil": "recuperacao", "tamanho_lote": 4},
    execucao_id="minha-execucao-etapa09",
    registrado_em="2026-10-08T09:00:00-03:00",
)
validar_vetorizacao(registro)

with open("vetorizacao.json", "w", encoding="utf-8") as arquivo:
    json.dump(registro, arquivo, ensure_ascii=False, allow_nan=False)
```

Esse uso carrega o modelo local preparado, mas não abre SQLite nem depende de
um envio registrado no aplicativo. A função aceita qualquer registro da
etapa 08 cuja fonte e prontidão sejam efetivamente revalidadas. O relatório
recebido sozinho não autoriza a execução.

O mecanismo é carregado uma vez e reutilizado entre lotes. Os testes fornecem
um mecanismo independente e simulado; a natureza registrada distingue
simulação de `inferencia_real`.

As opções da execução são estritas:

| Campo de `configuracao` | Padrão e valores |
| --- | --- |
| `perfil` | `recuperacao`; aceita também `similaridade` e `consulta`. |
| `max_tokens` | Até 512, limitado pela capacidade do mecanismo; inteiro de no mínimo 8. |
| `tamanho_lote` | 4; inteiro de 1 a 1.024. |
| `agregar` | `true`; booleano JSON. |

Booleanos não substituem números inteiros, campos desconhecidos são recusados
e não há coerção de strings para números. A configuração normalizada conserva
prefixo, pooling, normalização e política de divisão. Dispositivo, revisão e
precisão são opções do mecanismo, não campos dessa configuração de execução.

O gerador implementa `descrever()`, `tokenizar(texto_canonico, prefixo)` e
`gerar(lote)`. A descrição identifica modelo, tokenizador, revisões, dimensão,
limite, natureza e ambiente. A tokenização retorna IDs, offsets Unicode e
máscara de tokens especiais sem truncamento; a geração recebe as entradas já
planejadas e retorna uma lista de vetores normalizados. A validação do resultado
não chama esses métodos.

- `vetorizar_unidades_contexto(contexto, *, gerador, configuracao=None,
  cache=None, execucao_id=None, registrado_em=None)` revalida a origem,
  planeja os blocos, consulta cálculos reutilizáveis e gera o registro.
- `validar_vetorizacao(registro)` verifica o resultado sem carregar pesos;
  seu relatório informa `pronto_para_uso`.
- `consultar_representacao(registro, representacao_id)` retorna uma cópia da
  representação dentro da execução informada, após validação.
- `comparar_compatibilidade(registro, perfil)` compara a configuração
  verificável com o perfil externo e informa `compativel`.

O cache opcional é um dicionário de artefatos por identidade, independente
da associação de origem. ID e data da execução são gerados quando omitidos;
para reproduzir a identidade histórica, forneça ambos. A data precisa ser
ISO 8601 com fuso horário. Uma nova execução conserva associações próprias
mesmo quando reutiliza os cálculos.

O planejamento limita cada execução a 25.000 representações, 50.000 blocos,
2.000.000 de tokens somados entre os blocos, 256 MiB de bytes vetoriais e
33.554.432 caracteres nas entradas canônicas somadas. Os limites não medem
o tamanho do JSON completo. Exceder um limite interrompe a execução antes
da inferência, conservando um diagnóstico no app. Não há seleção parcial
de unidades nem redução silenciosa do texto.

`ErroVetorizacao` deriva de `ValueError`. Origem inválida, configuração
incorreta, modelo indisponível, falha de inferência e excesso de recursos
possuem tipos específicos de erro. O fluxo normal não promove falhas ou
registros incompletos à prontidão.

## Finalidades e compatibilidade com outros sistemas

O perfil `recuperacao` usa `passage: ` para foco, janela e documento; o perfil
`consulta` usa `query: `. O perfil `similaridade` também usa `query: ` e deve
ser usado dos dois lados de uma comparação simétrica. Os prefixos incluem
o espaço final e nunca modificam o texto canônico da origem.

O E5 utiliza média dos estados dos tokens com máscara de atenção, incluindo
tokens especiais e excluindo padding, seguida de normalização L2. Registre
modelo e tokenizador com suas revisões, dimensão, prefixo, pooling,
normalização, limite, divisão de entradas longas, agregação e precisão.

`comparar_compatibilidade()` compara perfis verificáveis. A mesma dimensão
não torna dois espaços compatíveis. Outro sistema pode fornecer seu perfil
sem qualquer dependência entre projetos; sem esse perfil não há como
afirmar equivalência. A comparação descreve compatibilidade da configuração,
não igualdade de bytes ou qualidade semântica.

Dispositivo, bibliotecas e lote são preservados nos metadados. CPU e GPU
podem alterar os últimos dígitos dos resultados. A recuperação de um vetor
salvo preserva seus bytes; a regeneração em ambientes diferentes exige uma
comparação por tolerância apropriada.

## Textos exatos, divisão e agregação

O foco usa `unidade.foco.texto`; a janela usa `unidade.janela.texto`. O
documento usa o texto integral de trabalho da preparação preservada no
registro contextual, incluindo espaços externos e separadores. O documento
não é reconstruído a partir de períodos nem de janelas.

Conte tokens com truncamento desativado, incluindo prefixo e especiais,
antes de enviar cada entrada ao mecanismo. O orçamento se aplica aos três
tipos de representação. Para entradas acima do limite, a política divide em
recortes contíguos de caracteres Unicode, sem sobreposição e sem
detokenização. Os blocos ordenados reconstituem integralmente o texto.
Os intervalos no trabalho são convertidos ao original pelo mapa validado
da preparação. Unicode, espaços, tabulações, CRLF e LF permanecem exatos.

Cada bloco preserva seu vetor e o vínculo ao cálculo. Com agregação
habilitada, o vetor integral é a média ponderada pela quantidade de tokens
de conteúdo de cada bloco, normalizada em L2. Prefixo, padding e especiais
não entram nos pesos. Sem agregação, a representação conserva os blocos e
não declara um vetor integral.

A agregação é uma aproximação que perde ordem e relações entre blocos.
Cobertura integral e hashes não eliminam a perda de informação inerente a
um embedding. Um campo sem conteúdo recebe estado explícito de ausência de
conteúdo, sem vetor artificial.

## Identidades, integridade e armazenamento

O ID histórico da execução é separado das identidades lógicas de geração e
das associações. Foco e janela com entrada efetiva igual podem reutilizar um
cálculo, conservando origens distintas. A identidade lógica da janela da
etapa 08 não serve sozinha para reutilizar vetores: preparações literal e
normalizada podem compartilhar a seleção e ter textos diferentes.

Os hashes verificam texto canônico UTF-8, entrada com prefixo, sequência de
IDs dos tokens e bytes vetoriais. Vetores são serializados como `float32`
little-endian. O JSON portável conserva os bytes em base64, dimensão,
quantidade de bytes e SHA-256; o SQLite conserva os mesmos bytes em BLOBs.
O validador não carrega o modelo nem requer o adaptador E5.

A validação revalida a origem e verifica associações, recortes, cobertura,
configuração, hashes, dimensões, valores finitos, norma, pesos e agregação.
Ela verifica a coerência do registro armazenado; sem regeneração não
comprova que valores arbitrários foram de fato produzidos pelo modelo
declarado. Integridade e qualidade dos embeddings são verificações distintas.

## Contrato JSON 1.0.0

O registro concluído tem `etapa: "09_vetorizacao"` e os seguintes campos.
Campos desconhecidos no registro, representações, blocos ou armazenamento
são recusados por `validar_vetorizacao()`.

| Grupo | Campos e finalidade |
| --- | --- |
| Identificação | `schema_version`, `etapa`, `execucao_id`, `contexto_execucao_id`, `documento_id`, `registrado_em`. |
| Origem | `contexto` contém a execução 08 completa; `contexto_sha256` assina esse JSON; `coordenadas` preserva a convenção da origem. |
| Geração | `modelo`, `configuracao`, `compatibilidade`, `geracao`, `processamento`. As duas assinaturas contêm `descricao` e `sha256`; a assinatura de geração inclui ambiente efetivo e lote para reutilização conservadora. |
| Resultados | `representacoes`, `artefatos`, `validacao`. A ordem é foco e janela de cada unidade, seguidos pelo documento integral. |

Cada representação conserva `id`, `ordem`, `tipo`, `unidade_id`,
`periodo_foco_id`, `janela_logica_id`, `campo`, `texto`, `sha256_texto`,
`trabalho`, `original`, `construcao`, `blocos`, `vetor`, `selecao_logica` e
`representacao_logica_id`. A construção é `direta`, `agregada`, `blocos` ou
`sem_conteudo`. Intervalos usam `inicio` inclusivo e `fim` exclusivo em
caracteres Unicode. Identidades históricas incluem a execução; identidades
lógicas assinam a seleção, o texto, o perfil e o plano de blocos.

Um bloco conserva `id`, `ordem`, `relativo`, `trabalho`, `original`, `texto`,
`sha256_texto`, `entrada_modelo`, `peso_tokens` e `artefato_id`. A entrada
contém texto efetivo com prefixo, IDs, offsets, máscara de especiais,
hashes e contagens. Um artefato contém `id`, `entrada_modelo`,
`armazenamento` e `origem_calculo` (`gerado` ou `reutilizado`). O armazenamento
contém exatamente `formato`, `dimensao`, `bytes`, `sha256_bytes` e `base64`.

O exemplo [vetorizacao_simulada.json](../examples/vetorizacao_simulada.json)
é um registro completo com gerador de teste e dimensões reduzidas. Ele pode
ser validado sem instalar o E5; não demonstra qualidade de embeddings reais.
Sua natureza é `simulado_teste`, `validacao.inferencia_real` é `false` e
`validacao.pronto_para_uso` é `false`. Uma execução estruturalmente válida
precisa de inferência real e vetores presentes para receber prontidão de uso.

## Interface, persistência e API HTTP

O painel também permite **Baixar notebook Colab** e **Baixar pacote Colab
ZIP** para gerar os vetores no Google Colab, em CPU ou GPU. A exportação
revalida a execução contextual selecionada, inclui sua origem exata e não
carrega o modelo no computador local. O notebook baixa resultados JSON e
ZIP com os contratos da etapa 09. Use **Importar resultados** na mesma execução
contextual para salvar o ZIP de resultados no histórico local, com validação
da origem e dos vetores e sem repetir a inferência. Veja
[como executar no Colab e importar os resultados](colab.md).

No aplicativo, escolha uma execução contextual pronta e use **Vetorizar
conteúdo e unidades**. O painel apresenta modelo, configuração,
representações, blocos e histórico. Resultados antigos recuperam a cadeia
exata, mesmo depois de novas preparações ou janelas.

`embedding_runs` guarda execuções concluídas e falhas. `embedding_artifacts`
guarda cálculos reutilizáveis em BLOBs; `embedding_run_artifacts` associa esses
cálculos às execuções; `embedding_vectors` guarda os vetores das
representações. As tabelas são criadas de forma aditiva. Copiar o banco com
o app encerrado preserva os vetores e suas origens.

Os bytes arquivados são imutáveis. A identidade física combina a identidade
do cálculo com o hash dos bytes, permitindo conservar variações numéricas
de uma regeneração sem sobrescrever o histórico. `origem_calculo` pertence
ao registro de cada execução, não ao conteúdo físico compartilhado.

A inferência ocorre fora da transação SQLite. Depois da validação, uma
transação curta persiste o resultado integral. Falhas não são apresentadas
como resultados concluídos com cobertura parcial. O processamento inicial
é síncrono; tamanho do lote e limites são explícitos.

| Método e rota | Uso |
| --- | --- |
| `POST /envios/<id>/vetorizacoes` | Gera vetores para `contexto_execucao_id`; aceita `configuracao`, `execucao_id` e `registrado_em`. |
| `GET /envios/<id>/vetorizacao.json` | Recupera o JSON portável; `vetorizacao_execucao_id` seleciona o histórico. |
| `GET /envios/<id>/vetorizacao.zip` | Exporta manifesto e binários da execução selecionada. |
| `GET /envios/<id>/representacao-vetorial.json` | Consulta uma representação com `vetorizacao_execucao_id` e `representacao_id`. |
| `GET /envios/<id>?vetorizacao_execucao_id=...` | Abre a execução e sua origem exata na interface. |

Sem seletor, os downloads usam a execução mais recente. Consultas e
downloads validam o registro e os BLOBs sem realizar inferência. O ZIP tem
`manifesto.json`, com formato `lingua_etapa09_zip` e versão `1.0.0`, além
de arquivos em `artefatos/` e `representacoes/`. O manifesto preserva o
registro sem duplicar base64 e lista caminhos, dimensão, formato, quantidade
de bytes e hashes para conferir os binários.

Para requisições JSON, uma execução concluída retorna HTTP 201. Falha de
modelo ou inferência retorna 503 e salva um diagnóstico; excesso de recursos
retorna 413 com diagnóstico. Configuração inválida retorna 400; origem
inconsistente, ID já utilizado ou corrupção retorna 409. Os formulários
redirecionam com HTTP 303 ao resultado ou diagnóstico salvo.

O JSON de uma execução com falha pode ser baixado com HTTP 200 para inspeção,
mas ela não possui representações ou ZIP de vetores; essas consultas retornam
409. O histórico distingue `concluida` de `falhou`.

O cache de reutilização tem limites opcionais de 2.000 artefatos e 32 MiB
na configuração padrão do aplicativo. São controlados por
`EMBEDDING_CACHE_MAX_ARTIFACTS` e `EMBEDDING_CACHE_MAX_BYTES` em `create_app`.
Ultrapassar esses limites restringe somente o carregamento do cache;
cálculos ausentes são realizados novamente, sem reduzir texto ou cobertura.

## Testes de contratos e inferência real

A suíte comum usa dados controlados e um gerador determinístico simulado
para testar contratos, fronteiras de tokens, cobertura, Unicode/CRLF,
agregação, adulterações, reutilização, histórico, exportação e recuperação
após reinício. Não requer PyTorch, Transformers, pesos E5 ou GPU.

```bash
.venv/bin/python -m unittest discover -s tests -v
```

No Windows, use `.\.venv\Scripts\python.exe` no mesmo comando.
Os testes de inferência real são separados e optativos. Eles exigem
dependências opcionais, manifesto com revisões fixas e cache local preparado;
não baixam pesos implicitamente. Ative-os definindo
`LINGUA_TESTE_E5_REAL=1` antes de executar os testes. Sem essa configuração,
esses testes são marcados como não executados.

No macOS ou Linux, para executar somente as verificações do adaptador com
o teste E5 real habilitado:

```bash
LINGUA_TESTE_E5_REAL=1 .venv/bin/python -m unittest discover -s tests -p test_embeddings_e5.py -v
```

No PowerShell:

```powershell
$env:LINGUA_TESTE_E5_REAL = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_embeddings_e5.py -v
Remove-Item Env:LINGUA_TESTE_E5_REAL
```

Esse arquivo também contém verificações do pooling com tensores PyTorch e
estados controlados, sem pesos E5. Elas conferem a operação numérica, mas
continuam distintas da inferência com o modelo real.

Durante esta implementação, o acesso ao Hugging Face neste ambiente retornou
HTTP 403. Assim, não foi possível resolver uma revisão oficial nem baixar e
executar o E5 aqui. Os testes com mecanismos simulados e as verificações de
persistência não substituem essa inferência real. A preparação e o teste real
podem ser realizados localmente pelos comandos acima quando o acesso ao
repositório do modelo estiver disponível.

Na verificação de 8 de outubro de 2026, a suíte completa executou 512 casos:
511 passaram e o caso optativo com pesos E5 reais foi pulado. A instalação
reutilizável e `pip check` passaram. O fluxo HTTP foi exercitado com as
etapas linguísticas reais anteriores e embeddings 09 explicitamente
simulados: geração, reutilização, consultas, JSON/ZIP e hashes, histórico
após reinício sem backend e diagnóstico de falha do backend real sem cache.
Esses resultados validam integração e contratos; a inferência E5 real
permanece pendente.

As decisões completas de arquitetura estão na
[especificação consolidada](especificacao_etapa09.md).
