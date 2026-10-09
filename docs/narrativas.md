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
com padrão 3. A continuidade dos acontecimentos, dos personagens e da voz
narrativa orienta cada geração; a composição formal pode variar entre movimentos.

Cada aba tem uma **Descrição do movimento** e texto atual editável. Trocar de
aba mantém as edições ainda não salvas. **Salvar todas as edições** não consulta
a API. Gerar, corrigir, validar, aprovar, herdar e visualizar o prompt também salvam
as edições das outras abas antes da ação. Para regenerar um parágrafo preenchido,
marque a confirmação de substituição naquela aba e use **Gerar novamente**.
O texto original gerado e o histórico de alterações permanecem no JSON.

## Composição por movimento

Em **Personalizar composição**, cada aba possui três controles independentes.
Todos começam desativados, deixando a escolha formal ao modelo. Marque um
controle para incluir sua opção no prompt. O botão **+ Instruções personalizadas**
abre um campo que complementa a opção escolhida. Desmarcar o controle conserva
suas escolhas, mas não envia suas regras nem as instruções personalizadas.

- **Encadeamento:** progressão temática, relações de causa e consequência,
  sequência temporal ou retomada temática entre períodos e movimentos.
- **Sintaxe:** afirmação e negação, contraste, paralelismo, inversão ou
  subordinação. Afirmação e negação articula afirmações com ressalvas,
  restrições, oposições ou recusas, sem repetições mecânicas.
- **Ritmo:** regular, crescente, decrescente, alternado ou irregular. O ritmo
  considera extensão das orações, cadência, pontuação e pausas; regularidade
  não exige contagens idênticas de palavras.

Do segundo movimento em diante, **Herdar configurações anteriores** copia os
três controles da aba imediatamente anterior, incluindo ativação, tipo e
instruções personalizadas. É uma cópia naquele momento: mudanças posteriores
ficam restritas à aba editada. Depois de herdar, personalize qualquer controle.
Copiar configurações ou editá-las não reescreve textos já gerados.

**Visualizar prompt** mostra as mensagens completas usadas na geração ou
regeneração, com o modelo selecionado, a descrição e todos os parágrafos
anteriores atuais. **Visualizar prompt de correção** mostra a variante que inclui
o texto atual e seus erros estruturais. Ambos usam o mesmo construtor de mensagens
da chamada real e funcionam sem chave da API. As prévias exigem os mesmos
movimentos anteriores aprovados e não consultam o provedor. Se alterar algo
depois da prévia, visualize novamente para conferir as novas mensagens.

Editar um parágrafo invalida sua aprovação e sinaliza
revisão nos movimentos seguintes, preservando os textos. Reveja a coerência
e aprove-os novamente em ordem. Uma validação estrutural não atesta primeira
pessoa, continuidade literária ou ausência de interpretações psicológicas;
essas propriedades são instruções do modelo e exigem revisão humana.

## Validação e montagem

Cada parágrafo precisa conter exatamente cinco períodos. Não há limites
obrigatórios de palavras por período ou parágrafo: as contagens servem apenas
para consulta. O validador local mostra os motivos de recusa estrutural.
Dois-pontos, ponto e vírgula, reticências, travessões
e abreviações com ponto são recusados. A contagem é determinística, independente
do modelo; não substitui uma revisão linguística completa.

A aprovação é explícita e é bloqueada quando há erros estruturais. A montagem
exige os cinco movimentos aprovados e sem revisão pendente. **Montar história**
concatena seus textos exatos, separados por uma linha em branco, sem consultar
a API. O TXT contém somente esses parágrafos; o JSON também contém
instruções, controles de composição, versões, validações, modelo e aprovações.
Rascunhos antigos recebem os controles desativados sem perder textos ou
aprovações, e suas validações são atualizadas para as regras sem limites de palavras.
Rascunhos com planejamento preservam
a ideia inicial e o planejamento em `legado` no JSON, sem usá-los na geração.
O JSON pode ser baixado durante a elaboração. Copiar usa a área de transferência ou seleciona o texto
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

Os testes do gerador simulam a API e verificam prompts, controles independentes,
herança, prévia, validação sem limites de palavras, encadeamento,
edição, persistência, aprovação e exportação. Eles não gastam créditos e não
certificam a qualidade de uma resposta real. Para testar o provedor real,
configure a chave e gere primeiro apenas a Introdução pela interface.
