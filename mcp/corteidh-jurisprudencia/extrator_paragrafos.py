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
a precedente com "par. X" tende a estourar a largura da página). Por isso o
descarte não é de uma única linha: ao reconhecer o início do bloco, o
segmentador consome todas as linhas seguintes até a próxima linha em branco
(ou o fim do texto) — é o bloco inteiro que é ruído, não só a sua primeira
linha. Sem isso, a segunda linha da nota (que não casa nenhum padrão de
início nem de ruído) seria colada ao parágrafo ANTERIOR, contaminando-o com
texto de outro caso.

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

A regra adotada é mais estreita: só entra em modo-nota (consumo de bloco)
quando o texto logo após o marcador começa por "Cf." ou "Cfr." — a
abreviação latina ("confer"/"conferir") que introduz quase toda citação de
fonte da Corte. É um sinal mais forte que "há um dígito solto": nenhum
item de lista, argumento de Estado ou número de página real observado nos
dois documentos de calibração começa dessa forma logo em seguida. O preço
é NÃO capturar nota cujo corpo comece de outro jeito (ex.: "O artigo 110 da
Constituição... ", ou "Ver Declaração de...", observadas nos documentos de
calibração) — aceito, porque a alternativa (regra ampla) mediu perda de
corpo real, e o objetivo aqui é não perder parágrafo, não zerar toda nota.

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
"""

from __future__ import annotations

import re
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


@dataclass(frozen=True)
class Paragrafo:
    numero: int
    texto: str


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
    própria — ver `_NUMERO_SEM_PONTO` e `_NUMERO_ISOLADO`. O CORPO da nota
    (a citação que segue o marcador, que pode ocupar várias linhas e conter
    URL) só é consumido quando o texto após o marcador começa por
    "Cf."/"Cfr." — ver `_CITACAO` e a docstring do módulo sobre por que uma
    regra mais ampla foi testada e rejeitada.
    """
    if not texto or not texto.strip():
        return []

    linhas = texto.splitlines()
    total = len(linhas)
    achados: list[tuple[int, list[str]]] = []
    esperado = 1
    corrente: list[str] | None = None
    linha_anterior_em_branco = True

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
            achados.append((esperado, [m.group(2)]))
            corrente = achados[-1][1]
            esperado += 1
            linha_anterior_em_branco = em_branco
            i += 1
            continue

        m_sem_ponto = _NUMERO_SEM_PONTO.match(linha)
        m_isolado = _NUMERO_ISOLADO.match(linha)

        entra_modo_nota = False
        if m_sem_ponto:
            resto = m_sem_ponto.group(1)
            if linha_anterior_em_branco or _CITACAO.match(resto):
                entra_modo_nota = True
        elif m_isolado:
            proxima = linhas[i + 1].strip() if i + 1 < total else ""
            if _CITACAO.match(proxima):
                entra_modo_nota = True

        if entra_modo_nota:
            # corpo da nota reconhecido ("Cf."/"Cfr." logo em seguida):
            # consome o bloco inteiro — todas as linhas até a próxima linha
            # em branco, marcador de página ou início de parágrafo exato —
            # porque pode conter mais de uma nota consecutiva do mesmo
            # bloco de rodapé, sem linha em branco entre elas.
            i += 1
            while i < total:
                prox = linhas[i]
                if not prox.strip():
                    break
                if _PAGINA_COM_TRACOS.match(prox):
                    break
                m_prox = _INICIO.match(prox)
                if m_prox and int(m_prox.group(1)) == esperado:
                    break
                i += 1
            continue

        if m_isolado:
            # marcador solto sem "Cf."/"Cfr." reconhecível em seguida:
            # descarta só esta linha (número de página plausível, ou nota
            # cujo corpo não foi possível confirmar) — nunca o que vem
            # depois, que pode ser corpo legítimo (ver docstring do módulo).
            linha_anterior_em_branco = em_branco
            i += 1
            continue

        if corrente is not None:
            despido = linha.strip()
            if despido:
                corrente.append(despido)

        linha_anterior_em_branco = em_branco
        i += 1

    return [
        Paragrafo(
            numero=n,
            texto=_remover_url_absoluta(
                re.sub(r"\s+", " ", " ".join(partes)).strip()
            ),
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
