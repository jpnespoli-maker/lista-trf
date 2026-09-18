"""Texto de sentença da Corte IDH → parágrafos numerados.

A Corte se cita por parágrafo ("Caso Ximenes Lopes Vs. Brasil, par. 89"), e
nenhum buscador dela devolve parágrafo — só o PDF tem. Este módulo é puro: não
toca rede nem banco, para que a calibração seja barata de testar.

A validação é por MONOTONICIDADE: um candidato só é parágrafo se o seu número
for o do anterior mais um. Isso descarta item de lista, nota de rodapé e número
de página, que são três das armadilhas reais do layout da Corte.

Uma quarta armadilha não cai na monotonicidade: a nota de rodapé real da Corte
tem a MESMA forma sintática do início de parágrafo — número seguido de
espaço e texto —, só que sem o ponto. Quando essa linha aparece logo após uma
linha em branco (o padrão de diagramação com que a Corte separa a nota de
rodapé do corpo do parágrafo anterior), ela é descartada como ruído; caso
contrário — uma linha de continuação que apenas começa por número (um ano, uma
quantidade), colada ao texto anterior sem linha em branco — é preservada como
parte do parágrafo corrente.

A nota de rodapé real quase sempre quebra em MAIS de uma linha (toda citação
a precedente com "par. X" tende a estourar a largura da página). Rounds 1 e
3 tentaram consumir o BLOCO inteiro (todas as linhas até a próxima linha em
branco, marcador de página ou início de parágrafo exato), para não deixar a
segunda linha da nota colada ao parágrafo anterior. **Isso foi RETIRADO no
round 5** — ver a oitava armadilha, mais abaixo, e a lista de consumo
LIMITADO que substitui o "modo rodapé" de bloco.

Uma quinta armadilha: "linha só com número" (usada para descartar número de
página tipo "3" solto, sem os traços de "- 3 -") não pode ser irrestrita —
sentenças de reparação reproduzem tabela de indenização, e um valor em
reais/dólares isolado em célula própria (ex.: "10000") tem essa mesma forma.
A regra descarta esse tipo de linha quando ela tem até 3 dígitos e nada
mais — em QUALQUER posição do texto, sem exigir linha em branco antes.
Número de página e marcador de nota de rodapé em sobrescrito são ambos
curtos (até 3 dígitos) e ambos podem cair em qualquer posição: o marcador em
sobrescrito, em particular, aparece no MEIO do parágrafo (colado ao fim da
frase anterior, sem linha em branco), porque é assim que o PyMuPDF às vezes
o extrai quando o isola em linha própria. Limite aceito e declarado: um
valor de até 3 dígitos SEM separador de milhar, isolado em linha própria,
ainda seria perdido por esta regra (indistinguível de número de página ou
marcador de nota nesse desenho). É improvável em documento real — a Corte
grafa indenização com separador de milhar ("10.000,00"), o que já a tira do
padrão de dígitos puros; só o valor sintético do cenário de teste ("10000")
não tem separador, e mesmo esse sobrevive, por ter 5 dígitos — e fica
registrado aqui, não escondido.

Uma condição de posição (só tratar como ruído quando a linha vinha após
linha em branco) foi TESTADA E RETIRADA desta regra: ela deixava passar o
marcador de nota em sobrescrito no meio do parágrafo — que não vem após
linha em branco —, causando regressão medida em 46 de 230 parágrafos de um
documento real (o dígito solto do marcador ficava colado ao texto). Não a
reintroduza achando-a mais conservadora; ela é o contrário disso aqui. Essa
condição de posição CONTINUA valendo para `_NUMERO_SEM_PONTO` (a nota sem
ponto, com texto depois do número) — ali o gate é o que distingue nota de
rodapé de uma continuação legítima que começa por ano ou quantidade.

Uma sexta armadilha, mais grave: o marcador de nota (isolado OU seguido de
texto) é só o NÚMERO — o CORPO da nota (a citação em si, que pode ocupar
várias linhas: "Cf. Autor. Obra. Data. Disponível em: <URL>.") não casava
nenhuma das regras acima, porque começa com uma palavra, não com dígito.
Esse corpo vazava por inteiro para dentro do parágrafo anterior sempre que
a nota seguinte de um mesmo bloco de rodapé de página vinha colada, sem
linha em branco, à nota anterior — e a URL de outro documento contamina
justamente o instrumento que serviria para conferir a citação na fonte.

A hipótese óbvia — tratar TODA linha só-de-dígitos como entrada num "modo
rodapé" que consome tudo até a próxima linha em branco, marcador de página
ou início de parágrafo — foi TESTADA E REJEITADA: medido num documento real
(Caso Ximenes Lopes, mais antigo, cuja paginação usa número solto SEM
traços, igual ao marcador de nota), essa entrada dispara também sobre
NÚMERO DE PÁGINA de fim de página, e o texto que vem depois nem sempre é
nota — às vezes é a CONTINUAÇÃO LEGÍTIMA do corpo, quebrada pela paginação
no meio de uma lista de itens `a) b) c) d) e)` do próprio parágrafo. Como
os itens de lista não casam `_INICIO` (não têm ponto), o "modo rodapé"
ampliado engoliria silenciosamente 23 linhas de argumento do Estado — dado
de corpo perdido, o que é PIOR do que o vazamento que se queria consertar.

A regra adotada em round 3 era mais estreita que a hipótese original: só
entrava em modo-nota quando o texto logo após o marcador começava por
"Cf." ou "Cfr." — a abreviação latina ("confer"/"conferir") que introduz
quase toda citação de fonte da Corte. É um sinal mais forte que "há um
dígito solto": nenhum item de lista, argumento de Estado ou número de
página real observado nos dois documentos de calibração começa dessa forma
logo em seguida. **Essa parte da decisão (o GATE de entrada) continua
valendo no round 5** — o que mudou foi o quanto se consome DEPOIS de
entrar, não o critério para entrar. O preço aceito continua o mesmo: NÃO
capturar nota cujo corpo comece de outro jeito (ex.: "O artigo 110 da
Constituição...", ou "Ver Declaração de...", observadas nos documentos de
calibração).

Auditoria de composição (round 4): medi diretamente — instrumentando o
próprio laço de consumo, não por diff de texto (diff de string por
similaridade se confunde com citação judicial repetitiva; um "trecho
suspeito" que pareceu corpo perdido, ao ler o parágrafo inteiro, era na
verdade o texto SOBREVIVENTE mal alinhado pelo `difflib`, não algo
removido) — que 100% dos blocos que o modo-nota consumiu, nos dois
documentos de calibração, começam por "Cf."/"Cfr." logo após o marcador.
Nenhum corpo de parágrafo foi identificado entre o removido.

Sétima armadilha, que o gate de "Cf."/"Cfr." não cobre: nota que NÃO
começa por essas abreviações (ex.: "Ver Declaração de...", no par. 44 de
um documento de calibração) continua vazando por inteiro — inclusive o
seu próprio "Disponível em: <URL>" de fechamento —, e ESSE marcador
("Disponível em:") é precisamente o que um verificador de fase posterior
usa como gate duro para exigir que toda citação carregue endereço
conferível. Parágrafo contaminado por nota vazada que, por acaso, contém
"Disponível em: <URL>" satisfaria esse gate sem que o parágrafo tenha,
ele próprio, trazido citação nenhuma — gate que se satisfaz com lixo da
fonte é pior que gate nenhum. Medida também uma forma mais rara: URL NUA
(sem "Disponível em:" antes) fundida no meio de uma frase, substituindo
aparentemente o número de referência em sobrescrito de uma nota (efeito
de extração de anotação de hyperlink do PyMuPDF quando o marcador também
é link clicável).

`_remover_url_absoluta` cobre as duas formas: remove "Disponível em:
<URL>" (com o prefixo) e URL nua, sempre que houver "http://"/"https://"
de fato — nunca a frase "disponível em" sozinha, para não arriscar o caso
(não observado nos documentos de calibração, mas hipoteticamente possível)
de um parágrafo dizer "disponível em" em prosa comum sem URL nenhuma.
Aplicada ao TEXTO JÁ CONCATENADO do parágrafo (não é regra de linha — o
artefato está fundido no meio de uma frase, sem fronteira de linha ao
redor), depois da junção e da normalização de espaços.

Limite declarado, e é maior que o da regra de "Cf."/"Cfr.": em pelo menos
dois parágrafos observados (101 e 106 de um documento de calibração), o
vazamento de nota não é apenas ANEXADO ao final do parágrafo — ele é
INSERIDO NO MEIO de uma frase, deslocando palavras da própria frase para
depois do trecho vazado (ex.: "...contra um de seus [bloco de nota
inteiro] membros." — a palavra "membros." pertence logo após "seus", mas
o layout de extração a empurrou para o fim). Remover a URL/"Disponível
em:" desses casos tira o gatilho do gate, mas NÃO restaura a ordem
correta da frase — isso é um problema de ORDEM DE LEITURA do PyMuPDF
(texto extraído fora de ordem), não de RUÍDO removível por regex, e este
módulo não tenta resolvê-lo. Fica documentado para a indexação (Tarefa 6)
decidir o que fazer com esses parágrafos — possivelmente sinalizá-los
para conferência humana em vez de indexá-los como se estivessem íntegros.

Oitava armadilha, e é a mais grave de todas — RECUO do round 5: a
re-revisão reproduziu uma quebra que os rounds 1 e 3 introduziram juntos.
Uma nota de UMA linha, já terminada ("1 Cf. Laudo médico às fls. 12.") é
sintaticamente IDÊNTICA, para o consumo em bloco, a uma nota que continua
na linha seguinte — nada no texto, sem informação de posição/largura de
página (que este módulo não tem; `extrair_texto_pdf` usa `get_text("text")`,
que descarta coordenadas), permite diferenciar as duas com segurança.
Reproduzido:

    1. O Estado alegou o seguinte:

    a) os primeiros indicios seguem o laudo1.

    1 Cf. Laudo medico as fls. 12.
    b) o processo penal observou as garantias fundamentais.

    2. A Corte pondera as alegacoes.

Com o consumo em bloco (rounds 1/3), a linha `b)` — corpo legítimo do
parágrafo 1, um item de lista — era engolida junto, porque o laço só saía
em linha em branco, marcador de página ou início de parágrafo exato, e
nenhum dos três aparece antes dela. **O parágrafo ficava mais curto, sem
lacuna e sem qualquer sinal de que algo sumiu — perda de corpo SILENCIOSA,
a categoria de defeito mais grave deste projeto.**

O diagnóstico importante aqui não é da regra, é da VERIFICAÇÃO: a auditoria
do round 4 (100% dos blocos começando por "Cf."/"Cfr." nos 2 PDFs de
calibração) era verdadeira e ainda assim não provava segurança geral —
ela testava contra os documentos que se tinha, não contra as CONDIÇÕES DE
SAÍDA do laço. Nos dois PDFs de calibração, toda nota reconhecida por
"Cf."/"Cfr." é seguida de linha em branco ou de outra nota — nunca de um
item de lista ou de prosa colada sem separador. Mas o Ximenes Lopes já
tinha mostrado, no próprio round 3 (com outro gatilho — número de página
sem traços), que lista `a) b) c) d) e)` pode ficar colada a um marcador
sem linha em branco antes. Não há garantia estrutural de que uma nota
"Cf." jamais terá essa mesma vizinhança em outro documento — só não
aconteceu de acontecer nestes dois.

**Critério de desempate adotado, explícito:** entre nota mal cortada
(contaminação VISÍVEL — alguém lê e vê o "Cf." solto) e corpo engolido
(perda INVISÍVEL — o parágrafo fica mais curto e ninguém percebe), este
módulo escolhe a contaminação visível. É reversível por releitura humana;
a perda invisível não é.

**O consumo deixou de ser um LAÇO com condições de saída e virou uma
JANELA FIXA, sem nenhuma condição de saída para enumerar** — por isso a
lista abaixo é de ENTRADA e de ALCANCE, não de "entrada e saída":

1. Linha casa `_NUMERO_SEM_PONTO` (dígito(s) + espaço + texto, sem ponto)
   E (linha anterior em branco OU o texto após o número começa por
   "Cf."/"Cfr."): consome **só essa linha** — nunca a seguinte, qualquer
   que seja o seu conteúdo.
2. Linha casa `_NUMERO_ISOLADO` (só dígitos, 1 a 3) E a linha SEGUINTE
   começa por "Cf."/"Cfr.": consome **essa linha e a seguinte, exatamente
   2 linhas** — a segunda é a mesma que já foi inspecionada para decidir
   entrar, então consumi-la não é "adivinhar mais", é confirmar o que já
   se sabia. Nenhuma linha além dessas duas é tocada, seja lá o que
   vier a seguir (branco, outra nota, item de lista, corpo colado,
   marcador de página, início de parágrafo — todos são o MESMO caso
   agora: simplesmente não fazem parte da janela).
3. Linha casa `_NUMERO_ISOLADO` mas a linha seguinte NÃO começa por
   "Cf."/"Cfr.": consome só essa linha (número de página plausível, ou
   nota cujo corpo não foi possível confirmar) — inalterado desde o
   round 2.

**Preço medido do recuo** (não hipotético — ver task-1-report.md para o
comando e a saída completa): o delta de caracteres em relação ao texto
original (antes de qualquer conserto desta série) subiu de -51.205 para
-16.013 em C-149, e de -66.757 para -26.755 em C-435 — ou seja, ~35.192 e
~40.002 caracteres de nota que os rounds 1/3 removiam voltam a vazar,
porque notas de 3+ linhas agora só têm a primeira removida. `http` em
texto de parágrafo continua em 0 nos dois documentos, porque
`_remover_url_absoluta` roda por cima do resultado final e limpa URL que
vaze por transbordo, independente do mecanismo que a deixou passar.

**Round 6 — a acreção medida sobre os 14 documentos reais da semente**
(2026-09-18): 12 de 14 tinham parágrafo com razão tamanho/mediana de até
514×. A hipótese de entrada era "o segmentador perde o fio ao topar seção
sem parágrafo numerado e cola tudo até reencontrar o número esperado" —
medida DIRETAMENTE (instrumentando a busca por todo número que `_INICIO`
reconheceria dentro do trecho inflado, não por diff de texto), essa
hipótese só se confirma em 2 dos 7 casos "do MEIO" auditados: A-18
(OC-18/03) par. 47 e C-161 (Nogueira de Carvalho) par. 67 realmente
terminam colando um CABEÇALHO DE SEÇÃO — "III COMPETÊNCIA", "VIII ARTIGOS
8.1 E 25.1 DA CONVENÇÃO AMERICANA (...)" — sem que nenhum parágrafo real
tenha sido perdido no meio (o próprio parágrafo, no documento oficial, é
mesmo um resumo de manifestações de dezenas de participantes sem
renumeração interna; só o título da seção seguinte, sintaticamente
indistinguível de continuação de frase, vazava para dentro dele). Nos
outros 3 casos do meio (C-353 Herzog par. 240, A-21 OC-21/14 par. 207,
C-435 Barbosa de Souza par. 106) o tamanho vem de outra causa já
DECLARADA e ACEITA por este módulo — nota que não começa por "Cf."/"Cfr."
(sétima armadilha, acima) e citação de artigo com numeração própria — e
não da perda de monotonicidade; tentar "consertar" isso aqui reabriria um
gate que já tem preço medido e aceito, sem relação com este round.

Nos documentos em que o parágrafo inflado é o ÚLTIMO (9 dos 14 — inclusive
C-407 e C-318 da tabela, e outros 5 fora dela), o mecanismo É o mesmo
CABEÇALHO GRUDADO, só que repetido várias vezes: depois do último parágrafo
numerado da Sentença (o "dispositivo", que legitimamente contém sua PRÓPRIA
lista numerada 1..N de pontos resolutivos — isso NÃO é defeito, é assim que
a Corte redige o dispositivo), vêm o encerramento, as assinaturas e então,
sem que `esperado` jamais volte a ser encontrado (a Sentença acabou — não
há "próximo parágrafo"), um ou mais blocos "VOTO CONCORDANTE / JUIZ X /
CASO Y / SENTENÇA DE Z" e, quando há, um "ANEXO N. TÍTULO" com lista de
vítimas — cada bloco reinicia sua PRÓPRIA numeração interna (1, 2, 3...),
que nunca bate com o `esperado` do documento principal e por isso nunca
fecha o parágrafo. Mesmo mecanismo de acreção do caso do meio (texto que
não é parágrafo grudando por falta de fechamento), tratado pela MESMA regra
abaixo — mas com um limite diferente e DECLARADO: os CABEÇALHOS de cada
voto/anexo são removidos (a regra roda por igual, em qualquer ponto da
acreção, não só no fechamento), mas o CORPO de cada voto (o parecer do
Juiz, página após página) e a lista de nomes do anexo são texto real, sem
número de parágrafo correspondente no documento oficial — não há "parágrafo
319" para devolver esse corpo, e inventar um fabricaria uma citação que a
fonte não sustenta. Isso não sai do parágrafo que o engoliu; sai marcado
`suspeito` por TAMANHO (ver `detectar_paragrafos_grandes_demais`), que é o
sinal que sobra quando dividir seria mentir.

**A regra adotada, e por que não é "todo texto em CAIXA ALTA":** uma linha
sozinha em maiúsculas NÃO basta — o artigo "II" de "os artigos II da
Declaração Americana" (medido em A-18, dentro do próprio par. 47) também
sai isolado em sua própria linha pela extração do PyMuPDF, e removê-lo
apagaria corpo real (violaria a restrição que não se negocia). O sinal
adotado é a SEQUÊNCIA: 2 ou mais linhas SEGUIDAS, cada uma sem nenhuma
letra minúscula (`_eh_linha_titulo`), formam cabeçalho; 1 linha isolada é
sempre tratada como falso alarme e devolvida ao corpo, palavra por palavra,
na ordem em que apareceu — nunca descartada. A decisão só é tomada quando a
sequência TERMINA (por linha em branco, por linha comum, ou pelo próximo
parágrafo numerado sendo reconhecido) — nunca no meio, o que mantém a
mesma filosofia de "janela fechada por condição observável, nunca por
busca aberta" do round 5. Medido nos 2 casos reais: A-18 par. 47 fecha com
exatamente 2 linhas ("III", "COMPETÊNCIA"); C-161 par. 67 fecha com 5
("VIII", "ARTIGOS 8.1 E 25.1 DA CONVENÇÃO AMERICANA", e mais 3 linhas de
parêntese). Preço aceito, medido em C-161: a sub-legenda "Alegações da
Comissão" que vem LOGO DEPOIS do cabeçalho de 5 linhas (título e
maiúsculo-misto, não maiúsculo puro) não é capturada por esta regra e
continua colada ao final do par. 67 — pequena, e um cabeçalho não pode
"adivinhar" onde termina uma legenda de caixa mista sem arriscar prosa
real.

O destino do cabeçalho removido (restrição de não fazê-lo desaparecer):
um novo campo `Paragrafo.titulos_removidos` — tupla de string, uma por
bloco removido, na ordem em que ocorreram dentro do parágrafo. Não um
"parágrafo 0" nem descarte: fica no próprio objeto, disponível para quem
quiser inspecionar ou logar, sem inventar numeração que a fonte não tem e
sem tocar no formato "N. texto" do `.txt` gravado (mudar esse contrato é
decisão de outra tarefa, não deste round).
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

# Início de parágrafo: número de 1 a 3 dígitos, ponto, espaço, no começo da
# linha. Classe [0-9] em vez de \d de propósito — ver a regra de barra
# invertida do projeto.
_INICIO = re.compile(r"^[ \t]*([0-9]{1,3})\.[ \t]+(.*)$")

# Marcador de página com traços — sempre ruído, em qualquer posição.
_PAGINA_COM_TRACOS = re.compile(r"^[ \t]*-[ \t]*[0-9]+[ \t]*-[ \t]*$")

# Linha que é SÓ dígitos (1 a 3), sem mais nada — candidato a número de
# página solto OU marcador de nota de rodapé em sobrescrito. Ruído em
# QUALQUER posição (sem gate de linha em branco — ver docstring do módulo
# sobre por que a posição foi testada e retirada, e sobre o limite que isto
# aceita).
_NUMERO_ISOLADO = re.compile(r"^[ \t]*[0-9]{1,3}[ \t]*$")

# Candidato a nota de rodapé: número de 1 a 3 dígitos seguido de espaço e
# texto, SEM o ponto que marcaria início de parágrafo. Só é tratado como
# ruído quando a linha anterior estava em branco OU quando o texto que
# segue começa por "Cf."/"Cfr." (ver docstring do módulo).
_NUMERO_SEM_PONTO = re.compile(r"^[ \t]*[0-9]{1,3}[ \t]+(\S.*)$")

# Abreviação latina que introduz quase toda citação de fonte da Corte —
# o sinal que autoriza entrar em modo-nota mesmo sem linha em branco antes.
_CITACAO = re.compile(r"^(?:Cf\.|Cfr\.)")

# URL absoluta, com ou sem o prefixo "Disponível em:", fundida no meio de
# texto já concatenado — sétima armadilha (ver docstring do módulo). Só
# dispara havendo "http(s)://" de fato, nunca por causa da frase sozinha.
_URL_ABSOLUTA = re.compile(r"(?:Disponível em:\s*)?https?://\S+")


def _eh_linha_titulo(linha: str) -> bool:
    """Uma linha é CANDIDATA a cabeçalho de seção/voto/anexo quando não tem
    nenhuma letra minúscula e tem ao menos uma letra (a segunda exigência
    exclui linha só de dígitos/pontuação, já tratada por outra regra).

    NUNCA decide sozinha — precisa de outra linha adjacente que também
    qualifique (ver `_fechar_titulo_pendente`, dentro de `segmentar`) para
    não confundir um numeral romano solto no MEIO de uma frase (medido em
    documento real: "os artigos II da Declaração Americana", onde "II"
    sai isolado em sua própria linha pela extração do PyMuPDF) com um
    cabeçalho de verdade — ver "Round 6" na docstring do módulo.
    """
    return any(c.isalpha() for c in linha) and linha == linha.upper()


@dataclass(frozen=True)
class Paragrafo:
    numero: int
    texto: str
    # Cabeçalhos de seção/voto/anexo removidos de DENTRO deste parágrafo
    # (round 6) — nunca descartados, ver a docstring do módulo. Tupla, não
    # lista: `Paragrafo` é `frozen`, e um campo mutável quebraria isso.
    titulos_removidos: tuple[str, ...] = ()


def extrair_texto_pdf(pdf_bytes: bytes) -> str:
    """Extrai o texto de um PDF em memória. Usa PyMuPDF, padrão do projeto."""
    import fitz

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return "\n".join(pagina.get_text("text") for pagina in doc)


def segmentar(texto: str) -> list[Paragrafo]:
    """Devolve os parágrafos numerados, em ordem, sem lacuna interna.

    Um candidato entra apenas se o seu número for `esperado`; qualquer outro é
    tratado como continuação do parágrafo corrente. É essa regra que descarta
    `a)`, nota de rodapé com ponto e número de página sem precisar de lista de
    exceções. A nota de rodapé SEM ponto (mesma forma do início de parágrafo,
    mas sem o `.`) e o número de página solto (sem traços) precisam de regra
    própria — ver `_NUMERO_SEM_PONTO` e `_NUMERO_ISOLADO`. O consumo do CORPO
    da nota é por JANELA FIXA (1 ou 2 linhas, nunca mais) desde o round 5 —
    ver `_CITACAO` e a "oitava armadilha" na docstring do módulo para a
    lista completa de entrada/alcance e por que o consumo em bloco (rounds
    1/3) foi retirado.
    """
    if not texto or not texto.strip():
        return []

    linhas = texto.splitlines()
    total = len(linhas)
    achados: list[tuple[int, list[str]]] = []
    esperado = 1
    corrente: list[str] | None = None
    linha_anterior_em_branco = True

    # Round 6: cabeçalho de seção/voto/anexo grudado no parágrafo corrente
    # (ver docstring do módulo). `titulo_pendente` guarda linhas candidatas
    # (cada uma sem letra minúscula) até decidir o destino delas;
    # `cabecalho_atual` acumula os blocos JÁ DECIDIDOS como cabeçalho
    # (2+ linhas seguidas) enquanto o parágrafo corrente segue aberto —
    # pode haver mais de um bloco (um por voto anexado, por exemplo).
    # `cabecalhos_por_numero` é o que sobra para anexar ao `Paragrafo` no
    # fechamento de cada número.
    titulo_pendente: list[str] = []
    cabecalho_atual: list[str] = []
    cabecalhos_por_numero: dict[int, tuple[str, ...]] = {}

    def _fechar_titulo_pendente(confirmado: bool) -> None:
        """Decide o destino do que está em `titulo_pendente`.

        Vira cabeçalho de verdade só quando as DUAS condições se cumprem:
        2+ linhas seguidas em CAIXA ALTA, E o encerramento veio de algo que
        NÃO é prosa comum — linha em branco, nota de rodapé reconhecida, ou
        o próximo parágrafo numerado (`confirmado=True` nesses casos). Sem
        a segunda condição, "318. Portanto, A CORTE DECIDE, por
        unanimidade: 1. ..." — medido em C-407 E em C-318, a MESMA fórmula
        de abertura do dispositivo em dois documentos — perderia "A CORTE
        DECIDE," do corpo: são 3 linhas em CAIXA ALTA ("A", "CORTE",
        "DECIDE,"), mas encerradas por "por unanimidade: " (prosa comum,
        minúscula, colada sem linha em branco) — ênfase tipográfica NO
        MEIO de uma frase, não cabeçalho de seção. Nenhum cabeçalho real
        observado nos 14 documentos é seguido de prosa colada sem
        separador; todos fecham em linha em branco, nota ou próximo
        parágrafo. Por isso `confirmado=False` (a linha comum do laço
        principal) NUNCA vira cabeçalho, não importa quantas linhas — e
        volta ao corpo, palavra por palavra, na ordem em que apareceu.
        Nunca se perde texto aqui: ou vira cabeçalho declarado, ou volta
        ao corpo.
        """
        if not titulo_pendente:
            return
        if confirmado and len(titulo_pendente) >= 2:
            cabecalho_atual.append(" ".join(titulo_pendente))
        elif corrente is not None:
            corrente.extend(titulo_pendente)
        titulo_pendente.clear()

    def _fechar_paragrafo_corrente(numero_fechado: int | None) -> None:
        """Comita `cabecalho_atual` (se houver) para o número que está
        FECHANDO — chamado tanto ao abrir o próximo parágrafo quanto ao
        fim do texto, para o último parágrafo não perder os cabeçalhos que
        engoliu (o caso do dispositivo + votos anexados, que nunca reabre
        `esperado`)."""
        nonlocal cabecalho_atual
        if numero_fechado is not None and cabecalho_atual:
            cabecalhos_por_numero[numero_fechado] = tuple(cabecalho_atual)
        cabecalho_atual = []

    i = 0
    while i < total:
        linha = linhas[i]
        em_branco = not linha.strip()

        if _PAGINA_COM_TRACOS.match(linha):
            linha_anterior_em_branco = em_branco
            i += 1
            continue

        m = _INICIO.match(linha)
        if m and int(m.group(1)) == esperado:
            _fechar_titulo_pendente(confirmado=True)
            _fechar_paragrafo_corrente(achados[-1][0] if achados else None)
            achados.append((esperado, [m.group(2)]))
            corrente = achados[-1][1]
            esperado += 1
            linha_anterior_em_branco = em_branco
            i += 1
            continue

        m_sem_ponto = _NUMERO_SEM_PONTO.match(linha)
        m_isolado = _NUMERO_ISOLADO.match(linha)

        if m_sem_ponto:
            resto = m_sem_ponto.group(1)
            if linha_anterior_em_branco or _CITACAO.match(resto):
                # nota reconhecida, marcador e corpo na MESMA linha: janela
                # fixa de 1 linha — nunca a seguinte, qualquer que seja o
                # seu conteúdo (ver "oitava armadilha" na docstring do
                # módulo; consumir mais do que isto perdeu corpo real de
                # parágrafo num caso medido).
                _fechar_titulo_pendente(confirmado=True)
                linha_anterior_em_branco = em_branco
                i += 1
                continue

        if m_isolado:
            proxima = linhas[i + 1].strip() if i + 1 < total else ""
            if _CITACAO.match(proxima):
                # nota reconhecida, marcador isolado com corpo na linha
                # seguinte: janela fixa de EXATAMENTE 2 linhas (o marcador
                # e a linha que já inspecionamos para decidir entrar — não
                # é uma terceira linha "adivinhada"). Nunca mais que isso.
                _fechar_titulo_pendente(confirmado=True)
                i += 2
                linha_anterior_em_branco = False
                continue
            # marcador solto sem "Cf."/"Cfr." reconhecível em seguida:
            # descarta só esta linha (número de página plausível, ou nota
            # cujo corpo não foi possível confirmar) — nunca o que vem
            # depois, que pode ser corpo legítimo (ver docstring do módulo).
            _fechar_titulo_pendente(confirmado=True)
            linha_anterior_em_branco = em_branco
            i += 1
            continue

        if em_branco:
            # linha em branco fecha um cabeçalho em curso (é assim que os
            # dois casos reais medidos — A-18 par. 47, C-161 par. 67 —
            # separam o bloco do que vem depois); não é corpo, então nunca
            # é ela mesma candidata a título.
            _fechar_titulo_pendente(confirmado=True)
            linha_anterior_em_branco = em_branco
            i += 1
            continue

        despido = linha.strip()
        if _eh_linha_titulo(despido):
            # candidata a cabeçalho — decisão adiada até a sequência
            # terminar (ver `_fechar_titulo_pendente`).
            titulo_pendente.append(despido)
            linha_anterior_em_branco = em_branco
            i += 1
            continue

        # linha comum (prosa): NUNCA confirma cabeçalho, por maior que seja
        # o que estava pendente — "A CORTE DECIDE," (3 linhas em CAIXA
        # ALTA) seguido sem separador de "por unanimidade: " é ênfase
        # tipográfica no meio de uma frase, não título de seção (ver
        # `_fechar_titulo_pendente`). Volta ao corpo antes desta linha.
        _fechar_titulo_pendente(confirmado=False)
        if corrente is not None:
            corrente.append(despido)

        linha_anterior_em_branco = em_branco
        i += 1

    _fechar_titulo_pendente(confirmado=True)
    _fechar_paragrafo_corrente(achados[-1][0] if achados else None)

    return [
        Paragrafo(
            numero=n,
            texto=_remover_url_absoluta(
                re.sub(r"\s+", " ", " ".join(partes)).strip()
            ),
            titulos_removidos=cabecalhos_por_numero.get(n, ()),
        )
        for n, partes in achados
    ]


def _remover_url_absoluta(texto: str) -> str:
    """Remove URL absoluta (com ou sem "Disponível em:") fundida no meio de
    texto já concatenado — limpeza de STRING, não de linha, porque o
    artefato não tem fronteira de linha ao redor (ver docstring do módulo,
    sétima armadilha). Colapsa o espaço duplo que a remoção deixa, para a
    frase ao redor continuar legível.
    """
    sem_url = _URL_ABSOLUTA.sub("", texto)
    return re.sub(r"[ \t]{2,}", " ", sem_url).strip()


def relatorio_lacunas(paragrafos: list[Paragrafo]) -> list[int]:
    """Números faltantes entre o menor e o maior parágrafo achado.

    Serve de instrumento de qualidade do extrator: lacuna significa que a
    segmentação perdeu parágrafo, e parágrafo perdido é citação que não se acha.
    """
    if not paragrafos:
        return []
    numeros = {p.numero for p in paragrafos}
    return [n for n in range(min(numeros), max(numeros) + 1) if n not in numeros]


def detectar_paragrafos_grandes_demais(
    paragrafos: list[Paragrafo], *, razao_minima: float = 10.0
) -> set[int]:
    """Números de parágrafo cujo tamanho estoura a mediana do documento.

    Sinal de `suspeito` independente da assinatura "Cf."/"Cfr." que o
    crawler já usa (medida em produção: 3,42% dos parágrafos, precisão
    5/5 numa amostra lida à mão) — e mais forte para o pior defeito deste
    projeto: um parágrafo que engoliu cabeçalho, voto anexado ou lista de
    vítimas por falta de fechamento (ver "Round 6" na docstring do módulo)
    não necessariamente contém "Cf."/"Cfr.", mas SEMPRE destoa em tamanho
    do resto do mesmo documento — o corpo de um voto individual ou uma
    lista de 100 nomes não têm como caber no tamanho normal de um
    parágrafo de sentença.

    A COMPARAÇÃO é sempre contra a MEDIANA DO PRÓPRIO documento, nunca um
    limiar absoluto de caracteres — a Corte tem parágrafo curto de decisão
    interlocutória e parágrafo longo de resumo de manifestações (medido:
    A-18 par. 47 tem 189 mil caracteres e é, ele próprio, um parágrafo
    real e íntegro da fonte oficial), e um limiar absoluto confundiria os
    dois. `razao_minima=10.0` replica o corte usado para identificar os
    documentos desta tarefa; documento com menos de 3 parágrafos não tem
    mediana informativa e não é avaliado (devolve conjunto vazio).

    Marcar como `suspeito` não é dizer que o parágrafo está ERRADO — pode
    ser genuinamente grande (ver A-18 acima). É dizer que ele não deve ser
    transcrito verbatim numa peça sem conferência na fonte, que é
    precisamente a decisão que caberia ao Defensor, não a este módulo.
    """
    if len(paragrafos) < 3:
        return set()
    mediana = statistics.median(len(p.texto) for p in paragrafos)
    if mediana <= 0:
        return set()
    return {p.numero for p in paragrafos if len(p.texto) >= razao_minima * mediana}
