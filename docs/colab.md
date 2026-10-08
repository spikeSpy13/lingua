# Vetorização no Google Colab

O Lingua permite executar a etapa 09 no Google Colab usando a origem
validada que o aplicativo produziu na etapa 08. O computador local continua
executando o aplicativo; o Colab carrega o E5 e gera os vetores. Não é
necessário instalar ou baixar esse modelo no Mac para exportar o pacote.

## Passo a passo

1. No aplicativo local, abra o texto e conclua **Construir unidades de
   contexto**. Escolha a execução contextual que deseja utilizar.
2. No painel **Vetorização**, baixe **Baixar notebook Colab** e **Baixar
   pacote Colab ZIP**. Os dois downloads ficam disponíveis antes de gerar
   qualquer embedding local.
3. Abra [Google Colab](https://colab.research.google.com/) e escolha
   **Arquivo → Fazer upload de notebook**. Selecione o arquivo `.ipynb`.
4. Se quiser usar GPU, escolha **Ambiente de execução → Alterar o tipo de
   ambiente de execução → GPU**. A execução em CPU também é permitida.
5. Execute as células na ordem. Quando o notebook solicitar o pacote,
   selecione o ZIP baixado do aplicativo. O ZIP contém o texto original,
   as anotações e a seleção de contexto; esses dados serão enviados ao Colab.
6. Confira as opções no notebook, prepare o modelo e gere os vetores.
   O primeiro preparo precisa acessar o Hugging Face e baixar os pesos.
7. Baixe o JSON e o ZIP de resultados produzidos pelo notebook. Eles
   conservam a origem e podem ser conferidos sem executar o E5 novamente.
8. No aplicativo local, abra a execução de contexto usada para gerar o pacote.
   Na aba **Vetorização**, use **Importar resultados**, selecione o ZIP de
   resultados e envie. A execução passa a constar no histórico, com seus
   vetores e a indicação de importação.

O notebook contém opções de dispositivo, precisão, finalidade, tamanho do
lote, limite de tokens e agregação. A configuração inicial do ZIP utiliza
os padrões do aplicativo. Alterações no formulário de vetorização local
não mudam o download; ajuste as opções do notebook antes de executar.
GPU é opcional, e CUDA explicitamente solicitada exige uma GPU disponível.

O download do pacote não altera o texto nem as etapas anteriores. Para salvar
os resultados no SQLite local, importe o ZIP pela interface. Guarde também
os arquivos de resultados para consulta e transferência.

## Conteúdo e integridade

O pacote contém `contexto.json`, `configuracao.json`, `manifesto.json`, o
notebook, os módulos necessários e as dependências de embeddings. Não
contém o banco local, credenciais, ambiente virtual ou pesos do modelo.
A validação e a vetorização utilizam os módulos incluídos no pacote,
sem clonar uma versão mais recente do repositório durante a execução.

O notebook confere a estrutura do ZIP, os caminhos, os tamanhos e os
hashes antes de importar os módulos. A execução revalida a origem 08 e
preserva os textos exatos do foco, da janela e do documento integral.
Ela não repete as inferências linguísticas das etapas 05 e 06.

A preparação resolve uma revisão oficial imutável do E5 e do tokenizador.
O resultado identifica essas revisões, o dispositivo efetivo, a precisão,
a configuração, os hashes das entradas e os bytes vetoriais. O validador
da etapa 09 confere o resultado sem carregar o modelo; hashes demonstram
integridade, não qualidade semântica.

CPU e GPU podem produzir pequenas diferenças numéricas. Registros históricos
preservam os bytes gerados e os metadados do ambiente; repetir uma execução
não garante igualdade de todos os bytes em dispositivos distintos.

## Importar os resultados no aplicativo

O upload aceita o ZIP de resultados produzido pelo notebook e o ZIP de vetores
exportado pelo próprio aplicativo. O pacote de entrada do Colab, que contém
`contexto.json` e os módulos, deve ser enviado ao notebook.

Escolha a execução de contexto utilizada na exportação, mesmo que já existam
versões mais recentes. O importador confere o ZIP, os hashes, o manifesto,
os bytes dos vetores e o registro da etapa 09. Ele exige correspondência
integral com a origem da etapa 08 armazenada no banco local. Uma origem ausente
ou diferente impede a importação, sem alterar o histórico.

O aplicativo mantém os IDs, os horários de geração, o modelo, a configuração
e os vetores exatos do resultado. Registra separadamente o horário da importação
e o SHA-256 do ZIP. Uma execução idêntica já salva é reconhecida sem duplicação;
um ID existente com conteúdo diferente é recusado. A gravação dos resultados
e da proveniência é atômica. A importação não baixa o modelo nem executa inferência.

O ZIP pode ter até 64 MiB, cada arquivo até 96 MiB e o conteúdo descompactado
até 128 MiB. O limite de 2 MiB permanece para o envio de texto. Nenhum arquivo
do ZIP é executado ou extraído no sistema de arquivos.

Na API, envie `POST /envios/<id>/vetorizacoes/importar?contexto_execucao_id=...`
com `Content-Type: application/zip` e o ZIP no corpo. O retorno JSON identifica
documento, contexto e vetorização: 201 para uma importação nova e 200 para uma
execução idêntica já existente. A interface usa um formulário multipart com
`arquivo` e `contexto_execucao_id` e volta à execução importada após o sucesso.
Entrada inválida retorna 400, origem ausente 404, conflito 409 e excesso de
tamanho 413. Embeddings simulados são aceitos somente em modo de teste.

## Downloads HTTP

| Rota | Download |
| --- | --- |
| `GET /envios/<id>/colab.ipynb?contexto_execucao_id=...` | Notebook pronto para abrir no Colab. |
| `GET /envios/<id>/colab.zip?contexto_execucao_id=...` | Pacote com a execução contextual exata e seus módulos. |

O seletor é obrigatório e deve aparecer uma única vez. Campos desconhecidos
ou seletores inválidos retornam 400; documento ou contexto inexistente
retorna 404; origem inválida ou não pronta retorna 409. Essas rotas não
carregam o E5 nem exigem suas dependências opcionais no computador local.
Excesso de tamanho retorna 413: o ZIP admite até 64 MiB, cada arquivo até
96 MiB e o pacote completo até 128 MiB descompactado. Nenhuma parte da origem
é descartada para fazer o pacote caber.

## Limites da verificação

Os testes locais usam fontes controladas e embeddings explicitamente
simulados para conferir exportação, isolamento, integridade e integração.
Compilar e executar as partes controladas do notebook não demonstra que
o serviço do Google Colab foi utilizado nem que o E5 real foi executado.
Nesta nuvem, o acesso aos pesos E5 continua pendente por resposta 403 do
Hugging Face. O notebook deve ser executado no Colab para verificar esse
download e a inferência real no ambiente escolhido.

Na verificação de 8 de outubro de 2026, os 27 testes específicos do Colab
passaram. A suíte completa executou 539 casos: 538 passaram e o teste
optativo com E5 real foi pulado. As células de upload, configuração,
vetorização e download foram executadas em um subprocesso isolado, com
upload/download controlados e gerador explicitamente simulado.
