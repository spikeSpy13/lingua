# Lingua

Projeto independente de análise linguística, em Python com interface HTML.
Recebe um texto, registra o original em SQLite, gera uma preparação validada
e segmenta sua cópia de trabalho em tokens, períodos e parágrafos. Um modelo
português local acrescenta lemas, classes gramaticais, traços morfológicos e
vocabulário do documento, relações sintáticas e entidades nomeadas. Regras
versionadas acrescentam ocorrências de orações, negação, tempo, modalidade
e conectores, com evidências e propostas de alcance. Cada período recebe
uma unidade de contexto, com o foco, os vizinhos selecionados e as referências
às anotações existentes.
Uma etapa opcional de vetorização gera representações do foco, da janela e
do documento, com modelo e revisões fixos, blocos rastreáveis para textos
longos e vetores associados à origem exata.
Os registros JSON contêm versões, hashes SHA-256,
metadados e posições que apontam até o original.

O botão **Gerar narrativa** abre um gerador progressivo de narrativas ficcionais
em primeira pessoa, com cinco abas, uma por movimento e parágrafo. Cada movimento
é gerado, editado, validado e aprovado separadamente. O seletor permite adicionar
modelos pelo identificador do OpenRouter. A geração usa a chave
`NARRATIVA_API_KEY`; edição manual, validação e montagem funcionam localmente.
Veja [fluxo, configuração da API e exportação](docs/narrativas.md).

## Obter uma cópia local

Requer Git e Python de 3.10 a 3.14, com `pip`. A dependência spaCy 3.8.16
aceita Python anterior a 3.15. No terminal:

```bash
git clone --branch main https://github.com/spikeSpy13/lingua.git linguaSpike
cd linguaSpike
```

O repositório público pode ser clonado sem login; para enviar alterações, autentique
sua conta no GitHub Desktop ou configure a autenticação Git no terminal.

## Executar

Execute os comandos na pasta `linguaSpike`. A instalação inicial das dependências
precisa de internet; depois, o aplicativo funciona localmente, sem serviços externos.

### macOS ou Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

### Windows (PowerShell ou Prompt de Comando)

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Abra <http://127.0.0.1:5001> no navegador do mesmo computador, cole o texto e
clique em **Registrar texto**. A confirmação mostra o identificador do registro
e a data em horário de Brasília. Atualizar a confirmação não repete o envio.
Mantenha o terminal aberto durante o uso e pressione `Ctrl+C` para encerrar.

A interface organiza o fluxo em oito abas: **Texto**, **Preparação**,
**Segmentação**, **Morfologia**, **Sintaxe e entidades**, **Regras**,
**Contexto** e **Vetorização**. Cada aba mostra somente as ações e os resultados
daquela parte do fluxo. Use **Anterior** e **Continuar** para navegar; esses
botões não executam o processamento. As próximas abas ficam disponíveis conforme
as etapas anteriores são concluídas e validadas. Ao consultar uma execução do
histórico, a navegação mantém as referências daquela execução.

## Preparar o registro para análise

Por padrão, o texto de trabalho é idêntico ao original. A opção técnica de
converter CRLF em LF começa desativada e modifica somente a cópia de trabalho.
Depois de registrar o texto, clique em **Baixar registro JSON**. Para outra
preparação do mesmo original, use **Gerar nova preparação**; as versões
anteriores permanecem disponíveis.

O módulo usa apenas a biblioteca padrão e pode ser utilizado sem Flask.
Veja [instruções, metadados e convenções de posições](docs/preparacao.md), e os
exemplos de [preservação literal](examples/preservacao_literal.json) e
[transformação opcional](examples/crlf_para_lf.json).

## Tokenizar e segmentar

Depois de registrar o texto, escolha a preparação em **Preparações salvas**,
quando houver versões anteriores, e clique em **Tokenizar e segmentar** no
painel dessa preparação. O resultado mostra as contagens e os
períodos; **Baixar segmentação JSON** exporta o registro completo. Cada geração
acrescenta um registro ao histórico, vinculado à preparação escolhida, sem
modificar o texto original ou resultados anteriores.

A etapa 04 usa `spacy.blank("pt")` e regras próprias versionadas para períodos,
com o tokenizador português. Ela prepara a estrutura para a etapa 05;
suas fronteiras podem precisar de revisão em textos ambíguos.
Veja [uso direto, regras e API HTTP](docs/segmentacao.md) e o
[exemplo de segmentação](examples/segmentacao.json).

## Anotar morfologia e vocabulário

Escolha uma segmentação salva e clique em **Anotar morfologia e vocabulário**.
O resultado mostra os tokens com lema, classe gramatical e morfologia, além
do vocabulário agrupado por lema e classe. Cada anotação entra no histórico;
use **Baixar anotações JSON** para exportar o registro completo.

A etapa 05 usa spaCy 3.8.16 com o modelo treinado português `pt_core_news_sm`
3.8.0. A instalação por `requirements.txt` inclui o modelo, com download de
aproximadamente 13 MB na primeira vez. Depois de instalado, o processamento
funciona localmente em CPU e o texto permanece no computador.

Esta etapa admite até **50.000 pontos de código** e **10.000 tokens internos**
do modelo, incluindo espaços. Entradas maiores geram erro explícito, sem
truncamento. Os limites são menores que os das etapas anteriores para conter
o consumo de memória do modelo. Fronteiras e previsões linguísticas podem
precisar de revisão humana.

Veja [uso direto, campos, limites e API HTTP](docs/anotacao.md), o
[exemplo completo](examples/anotacao.json) e a
[avaliação sobre referência manual](docs/avaliacao_morfologia.md).
O [relatório calculado](docs/avaliacao_morfologia_resultado.md) descreve o
desempenho do modelo nesse conjunto de referência.

## Analisar sintaxe e entidades

Escolha uma anotação morfológica salva e clique em **Analisar sintaxe e
entidades**. O resultado apresenta a relação sintática e a cabeça de cada
token, além das ocorrências de entidades nomeadas. Uma lista vazia de entidades
é um resultado válido. Use **Baixar análise JSON** para exportar o registro
completo; cada execução acrescenta uma análise ao histórico, vinculada à
anotação exata utilizada.

A etapa 06 usa o mesmo modelo português instalado pela etapa 05, com os
componentes `tok2vec`, `parser` e `ner`. As anotações morfológicas anteriores
permanecem intactas. Os limites continuam em **50.000 pontos de código** e
**10.000 tokens internos**. Uma divergência de tokens, períodos, árvore ou
entidades gera um erro explícito; o aplicativo preserva a previsão do modelo
sem corrigir relações ou recortar entidades para ajustar o resultado.

Veja [uso direto, convenções e API HTTP](docs/sintaxe_entidades.md) e os
exemplos de análise com [preservação literal](examples/sintaxe_entidades_literal.json)
e [normalização opcional de CRLF](examples/sintaxe_entidades_normalizada.json).
Uma análise validada fornece o material para a etapa 07.

## Aplicar regras linguísticas

Escolha uma análise sintática salva e clique em **Aplicar regras linguísticas**.
O resultado reúne cinco famílias: orações e núcleos verbais, negação, tempo
linguístico, modalidade e conectores. Cada ocorrência mostra a regra, seus
marcadores, núcleos, evidências, fragmentos de alcance, vínculos e ambiguidades.
O catálogo e o estado de cada regra ficam disponíveis no mesmo painel.

A etapa 07 usa somente a biblioteca padrão sobre o JSON validado da etapa 06.
Não executa novamente o modelo nem modifica as previsões anteriores. Ela
conserva as hipóteses linguísticas e suas limitações; a validação estrutural
não certifica que uma previsão do modelo ou uma interpretação esteja correta.

Cada execução entra em **Execuções de regras salvas**, vinculada à análise
exata usada, e pode ser exportada em **Baixar regras JSON**. Um diagnóstico
incompleto também fica no histórico: a interface diferencia regras sem
ocorrências, desabilitadas, impedidas por anotações ausentes e com falha.
Somente uma execução completa e validada recebe `pronto_para_etapa_08: true`.

Veja [catálogo, decisões, limitações e API HTTP](docs/regras_linguisticas.md)
e os exemplos com [preservação literal](examples/regras_linguisticas_literal.json)
e [normalização opcional de CRLF](examples/regras_linguisticas_normalizada.json).

## Construir unidades de contexto

Escolha uma execução de regras pronta e clique em **Construir unidades de
contexto**. A configuração inicial seleciona até um período anterior e um
seguinte, dentro do mesmo parágrafo. Você pode alterar os raios e permitir
explicitamente a passagem entre parágrafos. O painel destaca o foco, mostra
os vizinhos e mantém acesso ao parágrafo, ao documento e às anotações de origem.

A etapa 08 usa somente a biblioteca padrão, preserva a saída completa da
etapa 07 e acrescenta uma unidade por período. Ela organiza as evidências,
ambiguidades e necessidades de contexto existentes. Não cria interpretações
semânticas novas nem declara ambiguidades resolvidas por incluir mais texto.

Cada execução possui seu próprio ID e fica no histórico. Os textos exatos do
foco e da janela recebem hashes SHA-256, e a seleção recebe uma identidade
lógica estável, independente do ID e da data da execução. Somente um registro
validado recebe `pronto_para_etapa_09: true` e pode seguir para a vetorização.

Veja [uso direto, identidade, limites e API HTTP](docs/unidades_contexto.md),
a [especificação consolidada](docs/especificacao_etapa08.md) e os exemplos com
[preservação literal](examples/unidades_contexto_literal.json) e
[normalização opcional de CRLF](examples/unidades_contexto_normalizada.json).

## Vetorizar foco, janela, parágrafos e documento

A etapa 09 é opcional e utiliza inicialmente `intfloat/multilingual-e5-large`.
CPU é o padrão; CUDA pode ser configurada, sem exigir GPU. As etapas 01 a 08
continuam funcionando com as dependências originais e sem baixar esse modelo.
Para instalar as dependências de embeddings, recomendamos Python 3.10 a 3.12
por compatibilidade com as versões fixadas de PyTorch e Transformers.

No macOS, dentro de `linguaSpike`:

```bash
.venv/bin/python -m pip install -r requirements-embeddings.txt
.venv/bin/python -m embeddings_e5 preparar --modelo intfloat/multilingual-e5-large --revisao main --baixar
.venv/bin/python app.py
```

No Linux, para CPU:

```bash
.venv/bin/python -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install -r requirements-embeddings.txt
.venv/bin/python -m embeddings_e5 preparar --modelo intfloat/multilingual-e5-large --revisao main --baixar
.venv/bin/python app.py
```

No Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements-embeddings.txt
.\.venv\Scripts\python.exe -m embeddings_e5 preparar --modelo intfloat/multilingual-e5-large --revisao main --baixar
.\.venv\Scripts\python.exe app.py
```

O comando de preparação consulta o Hugging Face, resolve `main` para uma revisão
imutável dos pesos e do tokenizador, baixa os arquivos e salva a configuração
local em `instance/embeddings_modelo.json`. A geração utiliza esse cache local;
sem configuração ou pesos disponíveis, apresenta um erro explícito. Pesos e
configurações locais não são enviados pelo Git.

Escolha uma execução contextual pronta e use **Vetorizar conteúdo e
unidades**. Cada resultado conserva o texto exato e seus hashes, identifica
modelo, revisões e perfil, e mantém os vetores individuais dos blocos quando
há divisão de entradas longas. A inferência ocorre fora da transação SQLite;
resultados e falhas ficam no histórico. A consulta e a exportação não
executam novamente o modelo.

Veja [instalação, configuração CPU/GPU, contrato e API](docs/vetorizacao.md)
e a [especificação consolidada da etapa 09](docs/especificacao_etapa09.md).

Para executar a etapa 09 no Google Colab, use **Baixar notebook Colab** e
**Baixar pacote Colab ZIP** no painel de vetorização. Abra o notebook no Colab,
envie o ZIP quando solicitado e execute as células na ordem. O notebook
permite CPU ou GPU e baixa os resultados com sua origem preservada. Esse
fluxo não exige instalar o E5 no computador local. Depois, na mesma execução
de contexto, use **Importar resultados**, selecione o ZIP de resultados e
confirme o envio. O aplicativo valida o pacote e salva os vetores no histórico,
sem repetir a inferência. Reenviar a mesma execução não duplica os registros.
Veja o
[passo a passo do Colab](docs/colab.md).

## Dados locais

O banco SQLite é criado automaticamente em `instance/textos.sqlite3` e não é
versionado nem enviado ao GitHub. Cada computador começa com seu próprio banco
vazio. Para fazer uma cópia de segurança ou transferir os textos, copie esse
arquivo com o aplicativo encerrado.

A tabela `submissions` guarda `id`, `content` e `created_at` (data em UTC no formato
ISO 8601). O conteúdo preserva espaços, acentos e quebras de linha. Textos vazios
são recusados e o envio de texto tem limite de 2 MB. A importação de resultados
permite ZIPs de até 64 MB. Para escolher outro caminho
para o banco, defina a variável `ANALISE_DB` antes de iniciar o aplicativo.
As preparações ficam na tabela `preparations`, no mesmo banco, sem sobrescrever
os textos recebidos. A nova tabela é criada automaticamente ao atualizar o app.
As segmentações ficam na tabela `segmentations`, também local e criada
automaticamente. Cada uma preserva a preparação exata que foi utilizada.
As anotações ficam na tabela `annotations`, acrescentando registros ligados
à segmentação exata utilizada. Atualizar ou gerar outra análise preserva o
original e todos os registros anteriores.
As análises sintáticas e de entidades ficam na tabela `analyses`, vinculadas
à anotação morfológica exata. A tabela é criada automaticamente ao iniciar
o aplicativo atualizado, preservando o banco existente.
As execuções da etapa 07 ficam na tabela `rule_runs`, vinculadas à análise
sintática exata. O registro inclui a origem completa, o catálogo e a
configuração utilizados, preservando também os diagnósticos incompletos.
A tabela é criada automaticamente sem apagar registros anteriores.
As execuções da etapa 08 ficam na tabela `context_runs`, vinculadas à execução
exata de regras. Cada linha conserva sua política e origem; gerar novas
análises, regras ou janelas não altera o histórico contextual.
As execuções da etapa 09 ficam em `embedding_runs`, vinculadas à execução
contextual exata. As tabelas auxiliares guardam vetores binários, cálculos
reutilizáveis e associações de origem; são criadas de forma aditiva. Um backup
do SQLite inclui os vetores, sem depender de arquivos NumPy externos.

## Sincronizar o código

Trabalhamos na branch `main`. Antes de editar, execute `git status` e preserve
alterações locais: faça um commit do trabalho concluído ou guarde temporariamente
o trabalho em andamento antes de mudar de branch ou atualizar.

Com a cópia sem alterações pendentes:

```bash
git switch main
git pull --ff-only origin main
```

Depois de editar, revise com `git diff` e selecione as alterações para o commit
com `git add -p`. Para arquivos novos, use `git add` seguido do caminho de cada
arquivo que deseja incluir. Confira a seleção com `git diff --cached` e execute:

```bash
git commit -m "Descreva sua alteração"
git pull --rebase origin main
git push origin main
```

Se o rebase apontar conflitos, resolva-os antes de continuar com
`git rebase --continue`; use `git rebase --abort` para voltar ao estado anterior
ao rebase. Evite `push --force` para preservar o histórico compartilhado.

O Git sincroniza o código; o banco e o ambiente virtual permanecem locais.
Depois de atualizar para a etapa 05, instale as dependências e o modelo português.
As etapas 05 e 06 compartilham esse modelo; a etapa 06 não exige um novo download
quando as dependências já estão instaladas. A etapa 07 não acrescenta dependências.
A etapa 08 também não acrescenta dependências nem exige outro modelo.
A etapa 09 acrescenta dependências opcionais em `requirements-embeddings.txt`
e requer a preparação local do modelo descrita acima. Não reinstale ou baixe
os pesos a cada atualização se o perfil e suas revisões continuarem iguais.
Na raiz `linguaSpike`, no macOS ou Linux:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

No Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Repita a instalação quando `requirements.txt` mudar.

## Verificar

Na raiz `linguaSpike`, execute os testes com o Python do ambiente virtual.
No macOS ou Linux:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

No Windows:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Os testes usam bancos temporários e verificam persistência após reiniciar,
preservação literal e Unicode, mapas CRLF, metadados/hashes, ida e volta pelo
JSON, histórico de preparações e segmentações, contexto dos períodos, erros
explícitos e exibição segura de HTML. A etapa 05 também verifica alinhamento
com os tokens existentes, vocabulário, metadados do modelo e histórico de
anotações. Os exemplos curtos anotados manualmente
servem como referência de avaliação da etapa 05. A etapa 06 verifica árvores
sintáticas, entidades, separadores, intervalos literais e normalizados,
histórico e falhas explícitas, além da execução com o modelo real. A etapa 07
verifica regras com anotações controladas independentes do modelo, exceções,
ambiguidade, fragmentos descontínuos, vínculos, estados de execução, adulterações
do JSON, histórico e API HTTP. Os testes de integração com o modelo real
conferem alinhamento e preservação, sem usá-lo como gabarito linguístico.
A etapa 08 verifica seleções contextuais com referências independentes,
fronteiras de parágrafos, raios e tipos estritos, recortes e hashes exatos,
identidade lógica estável, anotações por proprietário, pendências herdadas,
limites de recursos, adulterações, consultas e histórico com bancos temporários.
A etapa 09 utiliza um gerador simulado e determinístico para verificar os
contratos, limites, hashes, blocos, agregação, reutilização, armazenamento e
exportação. Esses resultados são identificados como simulação. Os testes de
inferência E5 real são separados e opcionais; consulte os comandos e condições
em [vetorizacao.md](docs/vetorizacao.md).
O relato
de três parágrafos e dezesseis períodos ainda
não foi fornecido e suas contagens não foram verificadas.
