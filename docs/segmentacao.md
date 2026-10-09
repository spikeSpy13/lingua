# Etapa 04: tokenização e segmentação

O módulo `segmentacao.py` recebe uma preparação válida da etapa 03 e acrescenta
tokens, períodos, parágrafos e separadores. Ele preserva uma cópia integral da
preparação recebida, incluindo textos, versões, hashes SHA-256 e o mapa até o
original. A entrada permanece intacta.

O registro usa `schema_version = "1.0.0"` e
`etapa = "04_tokenizacao_segmentacao"`. A prontidão para a etapa 05 indica que
a estrutura foi validada; a escolha de fronteiras linguísticas continua sujeita
a ambiguidades e à revisão humana.

Na aba **Segmentação**, os períodos aparecem recolhidos como **Período 1**,
**Período 2** e assim por diante. Abra um período para consultar seu texto e
sua localização. Abaixo de **Localização no texto de trabalho**, abra
**Revisar ou reescrever período**: o primeiro campo recebe um prompt opcional
e o segundo, somente leitura, exibe o resultado. Com o prompt vazio, o pedido
padrão revisa a gramática, a clareza e a fluidez, preservando o sentido.

A reescrita usa a mesma `NARRATIVA_API_KEY` do **Gerar narrativa**, pelo
OpenRouter, com o modelo padrão `openai/gpt-4.1-mini`. Não exige uma segunda
chave. Cada solicitação envia apenas o período selecionado e o prompt ao
provedor. O resultado fica disponível na caixa enquanto a página estiver
aberta, para consulta e cópia; não altera o texto original nem os registros
de segmentação e análise. O prompt admite até 8.000 caracteres. Falhas da API
são exibidas no próprio painel, preservando qualquer resultado anterior.

## Instalação e uso direto

A dependência é spaCy 3.8.16. Seus metadados oficiais no
[PyPI](https://pypi.org/project/spacy/3.8.16/) declaram Python `>=3.9,<3.15`;
o Lingua mantém o mínimo de Python 3.10 e admite as versões até 3.14.
Depois de atualizar sua cópia com Git, instale as dependências na raiz `lingua`.
No macOS ou Linux:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

No Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

O tokenizador usa `spacy.blank("pt")`. A segmentação de períodos segue regras
próprias versionadas; não há download de modelo adicional. O módulo pode ser
utilizado diretamente, sem iniciar o Flask:

```python
import json
from preparacao import preparar_texto
from segmentacao import (
    segmentar_preparacao, validar_segmentacao, contexto_periodo,
)

preparacao = preparar_texto(
    "Dr. João chegou... ainda cedo.\n\nDra. Ana saiu? Eu fiquei!",
    documento_id="documento-exemplo",
)
registro = segmentar_preparacao(preparacao)
relatorio = validar_segmentacao(registro)
assert relatorio["pronto_para_etapa_05"] is True

primeiro_id = registro["periodos"][0]["id"]
contexto = contexto_periodo(registro, primeiro_id)
assert contexto["anterior"] is None

serializado = json.dumps(registro, ensure_ascii=False, allow_nan=False)
restaurado = json.loads(serializado)
validar_segmentacao(restaurado)
```

As funções públicas são:

- `segmentar_preparacao(preparacao, *, segmentacao_id=None, registrado_em=None)`:
  valida a preparação e devolve o registro completo de segmentação. Os argumentos
  opcionais permitem fornecer a identidade e a data; sem eles, gera uma nova
  identidade UUID4 e data atual em UTC. A data fornecida aceita `datetime` com
  fuso ou string ISO 8601 com fuso. IDs de unidades são derivados do ID da
  segmentação, do tipo da unidade e da ordem.
- `validar_segmentacao(registro)`: confere a preparação preservada, posições,
  vínculos, ordem, textos e regras registradas. Devolve um relatório com
  `estado`, `pronto_para_etapa_05` e `verificacoes`, sem modificar a entrada.
  A leitura de registros existentes dispensa spaCy e não refaz a tokenização;
  a conferência estrutural usa as unidades e metadados registrados.
- `contexto_periodo(registro, periodo_id)`: devolve `anterior`, `atual` e
  `seguinte`, cada um como cópia do período completo. A ordem é global no documento,
  inclusive quando o vizinho pertence a outro parágrafo. Nas extremidades,
  o vizinho ausente é `None` em Python e `null` em JSON.

Entradas ou registros inconsistentes levantam `ErroSegmentacao`, uma subclasse
de `ValueError`. O limite da segmentação é **2.097.152 pontos de código Unicode**
no texto de trabalho. Exceder esse limite produz um erro explícito; o texto não
é truncado. Esse limite é distinto do limite HTTP de 2 MB por requisição usado
no formulário de envio.

## Estrutura e rastreabilidade

O registro reúne `documento_id`, `preparacao_id`, `segmentacao_id`,
`registrado_em`, `preparacao`, `coordenadas`, `processamento`, `paragrafos`,
`periodos`, `tokens`, `separadores` e `validacao`, além da etapa e do schema.
O vínculo é com a preparação exata que foi segmentada, mesmo que outra
preparação do documento seja criada posteriormente.

Cada unidade tem `id`, `ordem` a partir de zero, `texto` e intervalos
`trabalho` e `original`, ambos com `inicio` e `fim`. A ordem é global dentro
de cada lista. Os vínculos adicionais são:

- Parágrafo: `periodos`, com os identificadores dos seus períodos.
- Período: `paragrafo_id`, `tokens` e `regra_encerramento`.
- Token: `periodo_id` e `paragrafo_id`.

`regra_encerramento` informa `pontuacao_terminal` ou `fim_paragrafo`.
Parágrafos e períodos começam no primeiro token e terminam no último token;
espaços nas bordas ficam preservados como separadores. Os tokens e separadores,
ordenados por posição no trabalho, permitem reconstruir esse texto por inteiro.
Espaços, tabulações e quebras entre unidades permanecem disponíveis no registro.
As camadas se sobrepõem: um período contém seus tokens e um parágrafo contém
seus períodos. Para reconstruir o trabalho, concatene apenas tokens e separadores
na ordem das posições. Cada coleção tem unidades sem sobreposição interna.
Texto composto só de espaços tem tokens, períodos e parágrafos vazios e um
separador cobrindo todo o conteúdo; texto vazio tem todas essas listas vazias.

`processamento` registra a ferramenta spaCy, sua versão efetiva, o idioma `pt`,
`modelo = null`, a identificação `spacy.blank(pt)` do tokenizador, seus ajustes
e as configurações aplicadas. As regras próprias usam a identificação
`segmentacao_portugues_por_regras` e a versão `1.0.0`. Consumidores devem
considerar essas versões ao comparar resultados.

Os SHA-256 preservados correspondem ao texto UTF-8 exato de cada bloco da
preparação, conforme a etapa 03. A validação efetiva deve preceder o consumo;
um campo de prontidão recebido isoladamente não comprova integridade.

## Coordenadas Unicode

As posições usam pontos de código Unicode, base zero e fim exclusivo:
`[inicio, fim)`. O `texto` da unidade é o recorte desse intervalo no trabalho;
o intervalo original é obtido pelo mapa da preparação. Uma conversão técnica
CRLF para LF pode fazer os comprimentos dos dois recortes diferirem, preservando
as posições correspondentes no original.

Um emoji pode ocupar um ponto de código e duas unidades UTF-16. Caracteres
combinantes têm posições próprias; grafemas visuais podem ocupar várias
posições. Para converter um limite `p` ao formato usado por outro aplicativo,
use o texto correspondente, original ou trabalho:

```python
bytes_utf8 = len(texto[:p].encode("utf-8"))
unidades_utf16 = len(texto[:p].encode("utf-16-le")) // 2
```

Índices de bytes e índices JavaScript/UTF-16 precisam ser convertidos antes de
usar os mapas. A [documentação de preparação](preparacao.md) detalha o tratamento
de intervalos que atravessam uma transformação técnica.

## Regras de parágrafos e períodos

Linhas em branco delimitam parágrafos quando usam LF ou CRLF e contêm somente
espaços ASCII ou tabulações. Uma quebra simples entre linhas continua no mesmo
parágrafo. CR isolado e separadores Unicode de linha ou parágrafo são preservados
no texto e não funcionam como delimitadores de parágrafo desta regra.

Dentro de cada parágrafo, as regras de períodos consideram pontuação terminal,
abreviações e o encerramento do próprio parágrafo. Pontuação terminal reconhecida
usa `.`, `!` ou `?`, inclusive combinações. Reticências `...` e `…` não encerram
um período por si mesmas. Títulos `Dr.`, `Dra.`, `Sr.` e `Sra.` são sempre
protegidos contra cortes no ponto da abreviação; o fim do parágrafo ainda encerra
o trecho. `etc.` permite o corte diante da próxima palavra com inicial maiúscula
ou no fim do parágrafo e mantém a continuação diante de inicial minúscula.
Aspas e parênteses de abertura são considerados ao buscar essa inicial. Um
ponto separado pelo tokenizador entre dígitos adjacentes tem proteção adicional.

Após pontuação terminal, a regra inclui pontuação consecutiva e fechamentos
reconhecidos, como aspas curvas ou parênteses. Aspas retas `"` e `'` são tratadas
como fechamento quando adjacentes à pontuação; com um espaço antes delas,
podem iniciar o trecho seguinte. A regra usa esse critério local e não acompanha
semanticamente citações ou a hierarquia de aspas e parênteses.

Essas regras tornam a saída reproduzível e auditável, mas não resolvem todas
as ambiguidades do português. Um título no fim de uma frase ou uma maiúscula
após `etc.` pode exigir revisão. Outras abreviações dependem dos tokens produzidos
pelo spaCy. A etapa 05 deve poder consultar o texto, os vizinhos e a regra aplicada
ao avaliar uma fronteira.

## Interface, histórico e API HTTP

Na página do documento, abra **Preparações salvas** para escolher uma preparação
anterior, quando houver mais de uma, e use **Tokenizar e segmentar** no painel
correspondente. A página mostra contagens e períodos; use
**Baixar segmentação JSON** para exportar o resultado. Cada geração acrescenta
um registro imutável ao histórico SQLite `segmentations`. Originais e preparações
anteriores permanecem intactos, e o banco continua local e fora do Git.

- `POST /envios/<id>/segmentacoes`: com JSON, aceita `preparacao_id`,
  `segmentacao_id` e `registrado_em` opcionais e retorna HTTP 201 com o registro
  completo. Quando `preparacao_id` é omitido, resolve a preparação mais recente
  do documento naquele momento e registra a identidade exata utilizada.
- No mesmo POST, o formulário envia explicitamente `preparacao_id` em um campo
  oculto. Após a gravação, recebe redirecionamento HTTP 303 para o documento,
  com `preparacao_id` e `segmentacao_id` na seleção da página.
- `GET /envios/<id>/segmentacao.json?segmentacao_id=<id>`: baixa o JSON como
  anexo. Sem o parâmetro, entrega a segmentação mais recente do documento.
- `GET /envios/<id>/contexto-periodo.json?segmentacao_id=<id>&periodo_id=<id>`:
  entrega `anterior`, `atual` e `seguinte` em JSON. É a rota recomendada para
  consumidores: os dois parâmetros são obrigatórios, não vazios e devem ocorrer
  uma única vez; violações retornam HTTP 400. Codifique os valores como parâmetros
  de URL, inclusive quando os IDs contêm `/`.
- `GET /envios/<id>/segmentacoes/<segmentacao_id>/periodos/<periodo_id>/contexto.json`:
  mantém o acesso por caminho para IDs sem barras.
- `POST /envios/<id>/periodos/reescrever`: recebe JSON com `segmentacao_id`,
  `periodo_id` e `prompt` opcional. Resolve o texto na segmentação salva desse
  documento e retorna `{"texto": "..."}` com HTTP 200, sem gravar alterações.
  Prompt vazio ou composto apenas por espaços usa a revisão padrão. Pedidos
  inválidos retornam HTTP 400; origem ausente, 404; origem inválida, 409;
  falhas de geração, 502. A resposta não é armazenada em cache.

Para montar a rota recomendada em Python:

```python
from urllib.parse import urlencode
query = urlencode({
    "segmentacao_id": registro["segmentacao_id"],
    "periodo_id": primeiro_id,
})
url_contexto = f"/envios/1/contexto-periodo.json?{query}"
# Substitua 1 pelo identificador real do documento na API HTTP.
```

O resultado preserva a preparação utilizada; ele não muda para acompanhar a
preparação mais recente. O JSON exportado e as respostas de contexto passam
pela validação do registro armazenado.

## Exemplos e verificação

O [exemplo completo](../examples/segmentacao.json) contém uma preparação e sua
segmentação. Para executar a suíte a partir da raiz `lingua`, use
`.venv/bin/python -m unittest discover -s tests -v` no macOS/Linux ou
`.\.venv\Scripts\python.exe -m unittest discover -s tests -v` no Windows.

A verificação usa exemplos curtos anotados manualmente, incluindo preservação,
mapas, regras de períodos e contexto. O relato de três parágrafos e dezesseis
períodos ainda não foi disponibilizado; essa contagem não foi verificada.
