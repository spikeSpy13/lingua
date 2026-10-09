# Gerador de narrativas confessionais

Na página inicial, use **Gerar narrativa**. A área funciona dentro do Flask
do Língua e não depende de registrar um texto ou executar a análise linguística.
O comportamento do projeto de geração progressiva foi integrado ao aplicativo
existente, com persistência em SQLite.

Clique em **Começar história**. Há exatamente cinco abas: Introdução,
Acontecimento, Recorrência, Contradição e Inconclusão. Cada aba contém um
parágrafo; juntos, os cinco formam uma única história. As instruções da
Introdução orientam o início, sem uma etapa de ideia inicial ou planejamento.
Cada pedido seguinte inclui **todos** os parágrafos anteriores em suas versões
atuais e as instruções do movimento. A intensidade dramática vai de 1 a 5,
com padrão 3. As instruções linguísticas de encadeamento continuam presentes
em cada geração.

Cada aba tem instruções próprias e texto atual editável. Trocar de aba mantém
as edições ainda não salvas. **Salvar todas as
edições** não consulta a API. Gerar, corrigir, validar e aprovar também salvam
as edições das outras abas antes da ação. Para regenerar um parágrafo preenchido,
marque a confirmação de substituição naquela aba e use **Gerar novamente**.
O texto original gerado
e o histórico de alterações permanecem no JSON.

Editar um parágrafo invalida sua aprovação e sinaliza
revisão nos movimentos seguintes, preservando os textos. Reveja a coerência
e aprove-os novamente em ordem. Uma validação estrutural não atesta primeira
pessoa, continuidade literária ou ausência de interpretações psicológicas;
essas propriedades são instruções do modelo e exigem revisão humana.

## Validação e montagem

Cada parágrafo precisa conter exatamente cinco períodos, de 20 a 36 palavras
por período e no máximo 180 palavras. O validador local mostra as contagens
e os motivos de recusa. Dois-pontos, ponto e vírgula, reticências, travessões
e abreviações com ponto são recusados. A contagem é determinística, independente
do modelo; não substitui uma revisão linguística completa.

A aprovação é explícita e é bloqueada quando há erros estruturais. A montagem
exige os cinco movimentos aprovados e sem revisão pendente. **Montar história**
concatena seus textos exatos, separados por uma linha em branco, sem consultar
a API. O TXT contém somente esses parágrafos; o JSON também contém
instruções, versões, validações, modelo e aprovações. Rascunhos antigos preservam
a ideia inicial e o planejamento em `legado` no JSON, sem usá-los na geração.
O JSON pode ser baixado
durante a elaboração. Copiar usa a área de transferência ou seleciona o texto
para cópia manual quando o navegador não permite acesso automático.

## Configurar a API

O provedor padrão é **OpenRouter**, com modelo inicial
`openai/gpt-4.1-mini`. O seletor **Modelo para a próxima geração** contém os
modelos disponíveis no aplicativo. Para incluir outro, digite seu identificador
em **Adicionar outro modelo** e clique em **Adicionar modelo**. Ele será
selecionado e ficará salvo no catálogo local para outras histórias, sem consultar
a API. Entre as opções iniciais estão `openai/gpt-4.1`, para GPT-4.1, e
`openrouter/free`, que escolhe automaticamente um modelo gratuito disponível.
Para escolher um modelo gratuito específico, copie seu identificador completo
do [catálogo do OpenRouter](https://openrouter.ai/models), incluindo `:free`
quando fizer parte do identificador.

Configure a chave do OpenRouter uma única vez. Você pode trocar o modelo antes
de gerar qualquer movimento, usando a mesma chave. O modelo escolhido é salvo no
rascunho e cada movimento mantém o modelo usado na sua geração; trocar de
modelo não reescreve os parágrafos anteriores.

Há também a opção OpenAI, cujo modelo
precisa usar o identificador do próprio provedor, por exemplo `gpt-4.1-mini`.
A disponibilidade e o preço dos modelos são definidos pelo provedor.

A credencial é lida de `NARRATIVA_API_KEY` no processo do servidor. Não coloque
a chave nas instruções, em textos, no Git ou em mensagens do chat. No ambiente
de nuvem, forneça o valor secretamente nas configurações do ambiente, com destino
HTTPS `openrouter.ai`, e aplique a configuração. Para OpenAI, configure uma chave
da OpenAI e autorize `api.openai.com`.

Localmente, use o ambiente virtual e injete a variável antes de iniciar:

```bash
cd lingua
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
read -rsp 'Chave OpenRouter: ' NARRATIVA_API_KEY
export NARRATIVA_API_KEY
.venv/bin/python app.py
```

O arquivo `.env.example` documenta o nome da variável. `.env` está ignorado
pelo Git, mas não é carregado automaticamente pelo aplicativo. Se usar esse
arquivo, carregue-o no processo pelo seu gerenciador de ambiente.

Pedidos usam HTTPS, timeout de 60 segundos e nenhuma repetição automática.
Erros de autenticação, créditos, limite de solicitações ou resposta incompleta
preservam as edições locais e não aprovam texto. Cada clique gera somente um
campo; não há geração em lote. O conteúdo enviado pode ser processado pelo
OpenRouter e pelo provedor do modelo escolhido.

## Persistência e verificação

Os rascunhos ficam em `narrative_drafts` e os modelos adicionados em
`narrative_models`, no mesmo banco definido por
`ANALISE_DB`, separado das tabelas de análise. Recarregar a página ou reiniciar
o servidor preserva os dados salvos. Edições ainda não salvas recebem um aviso
ao sair da página. O aplicativo usa o modelo de acesso local do Língua; não
adiciona autenticação de usuários nem deve ser exposto publicamente como serviço.

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Os testes do gerador simulam a API e verificam prompts, validação, encadeamento,
edição, persistência, aprovação e exportação. Eles não gastam créditos e não
certificam a qualidade de uma resposta real. Para testar o provedor real,
configure a chave e gere primeiro apenas a Introdução pela interface.
