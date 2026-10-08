# Projeto Língua — especificação consolidada da etapa 09

Esta especificação reúne a implementação autorizada da vetorização e as
decisões de arquitetura aprovadas após a avaliação da proposta. A base de
integração é a etapa 08 do commit `886d40f`. O funcionamento, os comandos e o
contrato implementado estão em [vetorizacao.md](vetorizacao.md).

## 1. Entrada e separação das etapas

A etapa 09 recebe um registro da etapa 08, com sua origem integral, e uma
configuração de geração. Reexecute o validador existente e confirme
`pronto_para_etapa_09: true` no relatório recalculado. Não confie apenas no
indicador recebido. Preserve a fonte sobre uma cópia, sem modificar textos,
identificadores, anotações, seleção contextual ou resultados linguísticos.

O núcleo deve funcionar com registros recebidos dinamicamente, sem banco de
dados nem documentos previamente armazenados. A interface e o SQLite são
adaptadores de execução e persistência. Separe contrato e planejamento,
tokenização e inferência, validação, armazenamento e apresentação.

A etapa 09 produz representações numéricas; não acrescenta classificação de
contradições, desejos, intenções ou outras interpretações semânticas.

## 2. Modelo e infraestrutura configuráveis

O adaptador inicial utiliza `intfloat/multilingual-e5-large`. Identifique os
pesos e o tokenizador por revisões imutáveis, incluindo a dimensão efetiva e
as versões do mecanismo de inferência. Não registre `main` como se fosse uma
revisão imutável. Um comando de preparação pode resolver essa referência e
guardar o commit específico antes do processamento.

Permita substituir o modelo sem modificar as etapas anteriores. Os
metadados do mecanismo devem distinguir inferência real de um gerador
simulado utilizado exclusivamente nos testes.

CPU é uma opção válida e GPU é opcional. Configure dispositivo, precisão e
tamanho do lote; registre dispositivo solicitado e efetivo. A configuração
do equipamento não deve determinar o contrato textual ou exigir uma decisão
sobre o ambiente de produção. Não troque silenciosamente precisão ou modelo
para contornar limites de memória. Bibliotecas e pesos da vetorização são
dependências opcionais das etapas 01 a 08.

## 3. Finalidade e compatibilidade verificável

Defina a finalidade explicitamente. Para recuperação, as representações
armazenadas usam `passage: `; consultas usam `query: `. Para comparação
simétrica, ambos os lados usam `query: `. O prefixo permanece separado do
texto canônico e participa da entrada efetivamente tokenizada.

Um perfil de compatibilidade deve registrar e comparar:

- identificação e revisão dos pesos;
- identificação e revisão do tokenizador;
- dimensão;
- prefixo e finalidade;
- pooling com máscara de atenção;
- normalização L2;
- limite total de tokens;
- política de divisão e de agregação;
- precisão numérica e formato de armazenamento;
- versões dos contratos e políticas que afetam a geração.

Não conclua compatibilidade pela dimensão. Outro projeto pode fornecer um
perfil independente para comparação; o Língua não precisa acessá-lo ou
depender dele. Sem esse perfil, registre somente a configuração produzida,
sem prometer equivalência com um sistema externo.

Compatibilidade de configuração e igualdade binária são garantias
distintas. Registre também bibliotecas, dispositivo e tamanho do lote para
avaliar diferenças numéricas entre ambientes.

## 4. Representações e textos canônicos

Produza associações independentes para:

| Tipo | Campo canônico |
| --- | --- |
| `foco` | Texto exato de `foco.texto` de cada unidade. |
| `janela` | Texto exato de `janela.texto` de cada unidade. |
| `documento` | Texto integral de trabalho da preparação preservada na origem. |

Preserve os hashes existentes do foco e da janela e confira os recortes
contra a fonte. O documento deve ser extraído da preparação exata, incluindo
separadores e espaços externos; não o reconstrua juntando períodos. Gere
uma associação de documento por execução, sem duplicá-la por unidade.

Cada associação conserva execução contextual, documento, preparação e,
quando aplicável, unidade, período foco e identidade lógica da janela.
Textos iguais podem compartilhar um cálculo, mas continuam tendo associações
distintas de origem.

Entradas sem conteúdo linguístico devem receber um estado explícito de
ausência de conteúdo, sem vetor artificial. Não invente foco ou janela para
um documento sem períodos.

## 5. Limites e cobertura de textos longos

Conte tokens antes da inferência com truncamento desativado, incluindo
prefixo e tokens especiais. Confira também a sequência enviada efetivamente
ao modelo. A política inicial usa um orçamento máximo de 512 tokens de
entrada; o orçamento configurado não pode ultrapassar a capacidade declarada
do mecanismo. A verificação se aplica ao foco, à janela, aos parágrafos e ao documento.

Quando houver excesso, divida em blocos contíguos sem sobreposição,
utilizando recortes do texto canônico. Não detokenize os IDs para reconstruir
o texto. Preserve Unicode, espaços, tabulações e quebras de linha. A
concatenação ordenada dos blocos precisa reconstruir exatamente o campo
integral, sem perda nem separadores inventados.

Cada bloco registra identificador, ordem, texto exato, intervalos Unicode
`[inicio, fim)` no campo, no trabalho e no original, hashes e entrada
tokenizada. Verifique o mapa da preparação para as coordenadas originais.
Conserve todos os vetores dos blocos, mesmo quando também houver um vetor
agregado.

A agregação, quando habilitada, é uma média ponderada pelo número de tokens
de conteúdo de cada bloco, seguida de normalização L2. Declare os pesos e a
fórmula. Não conte prefixo, padding ou tokens especiais como conteúdo.
Sem agregação, a representação continua acessível por seus blocos e não
declara um vetor integral inexistente.

A agregação perde ordem e relações entre blocos. Cobertura e rastreabilidade
integrais do texto não tornam um embedding uma representação sem perda. O
vetor do documento não deve ser uma média das janelas, que repetem conteúdo
contextual sobreposto.

Limites de quantidade de blocos, tokens, representações ou bytes devem ser
explícitos e verificados antes do processamento excessivo. Ao excedê-los,
produza erro; não reduza cobertura nem trunque silenciosamente.

## 6. Identidades, hashes e reutilização

Distinga o ID histórico da execução, o ID de cada associação e a identidade
lógica da representação. A identidade lógica deve incluir a origem textual
e a configuração relevante da geração, mas não a data ou o ID novo da
execução. Para resultados agregados, inclua o plano ordenado de blocos e a
política de pesos.

`janela_logica_id` da etapa 08 não identifica, sozinha, um vetor: preparações
literal e normalizada podem ter a mesma seleção lógica e textos de trabalho
diferentes. Reutilize cálculos somente quando entrada efetiva, sequência de
tokens, revisões e configurações relevantes coincidirem. Preserve uma nova
associação para cada origem e uma nova execução histórica quando solicitada.

Registre SHA-256 do texto canônico UTF-8, da entrada textual com prefixo, da
sequência de IDs dos tokens em sua codificação declarada e dos bytes do vetor.
Serialize descrições usadas nas identidades com JSON canônico versionado,
chaves ordenadas e UTF-8, sem normalizar os textos. Hashes são garantias de
integridade; não certificam qualidade semântica nem autenticidade da autoria.

## 7. Contrato e armazenamento

O registro exportável conserva uma única cópia da origem contextual,
configuração, perfil, metadados de processamento, representações, blocos,
vetores e relatório de validação. Cada vetor identifica dimensão, precisão,
formato numérico, localização, quantidade de bytes e hash.

Use SQLite inicialmente, com alterações aditivas e vínculo à execução exata
de `context_runs`. Armazene vetores em BLOBs `float32`, com ordem dos bytes
declarada. O contrato portável pode transportar os mesmos bytes em base64;
a exportação deve permitir recuperar manifesto, origem e binários sem
depender de arquivos externos ao pacote.

Gere embeddings fora de uma transação SQLite longa. Persista uma execução
concluída e seus vetores em uma transação curta, depois de validar. Registre
falhas de execução de forma explícita, sem apresentar cobertura parcial como
sucesso. Preserve os históricos e as análises de origem.

Integre iniciar processamento, consultar representações, selecionar histórico,
mostrar modelo e perfil, exportar dados e recuperar falhas. Consultar e
baixar registros não deve carregar ou executar o modelo.

## 8. Validação independente da inferência

O validador deve revalidar a etapa 08 uma vez e verificar:

- prontidão efetiva e preservação integral da origem;
- associação de cada foco, janela e parágrafo e cobertura do documento;
- textos, recortes, hashes e intervalos originais;
- configuração, perfil e identidades lógicas;
- entradas e sequência de tokens declaradas;
- cobertura integral e ordem dos blocos, sem sobreposição;
- dimensões, precisão e codificação dos bytes;
- valores finitos, ausência de NaN/Inf e normalização aplicável;
- pesos e coerência numérica dos vetores agregados;
- localização, quantidade de bytes e hashes do armazenamento;
- estados de ausência de conteúdo, falha e sucesso.

Abrir um registro não exige inferência nem download dos pesos. A validação
estrutural de um registro verifica sua coerência e integridade declarada;
sem regeneração não demonstra que um modelo foi realmente responsável por
cada valor. A procedência do mecanismo e a avaliação real são verificações
separadas.

## 9. Testes e entrega

Use `unittest`, fixtures de origem controladas e um gerador simulado
determinístico, identificado como tal. As expectativas de recortes, hashes,
seleções e agregação devem ter referências independentes do produtor.

Cubra entrada inválida ou não pronta, fonte preservada, Unicode/CRLF,
limites próximos ao orçamento, excesso nos quatro tipos, ausência de
truncamento, blocos e pesos, configuração incompatível, dimensões e bytes
adulterados, reutilização correta, falhas intermediárias, históricos,
exportação e recuperação após reinício com SQLite temporário.

Mantenha testes do modelo real separados e opcionais, executados somente
quando dependências e pesos estiverem disponíveis. Embeddings simulados não
são evidência de inferência real nem de qualidade em português. Não baixe
pesos implicitamente ao executar a suíte comum. Informe resultados reais e
verificações pendentes, sem converter indisponibilidade do modelo em uma
alegação de validação da inferência.

Entregue módulos, integração, contratos JSON, testes e comandos locais.
Qualquer revisão desconhecida, dependência ausente ou download impedido deve
produzir diagnóstico preciso. Não substitua o E5 por resultados simulados no
fluxo normal do aplicativo.

O critério é: **nenhum vetor sem origem verificável, nenhuma perda silenciosa
de texto e nenhuma modificação das análises linguísticas anteriores.**
