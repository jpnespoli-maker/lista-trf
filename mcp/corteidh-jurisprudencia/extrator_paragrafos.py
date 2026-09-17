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
A regra por isso só descarta esse tipo de linha quando ela tem até 3 dígitos
(número de página plausível) E vem logo após uma linha em branco — mesmo
gate usado para a nota de rodapé sem ponto. Limite aceito e declarado: um
valor de até 3 dígitos isolado em célula de tabela, precedido de linha em
branco, ainda seria perdido por esta regra (indistinguível de número de
página nesse desenho). É improvável — indenizações da Corte são tipicamente
em milhares — e fica registrado aqui, não escondido.
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
# página solto. Só é ruído quando vem logo após linha em branco (ver
# docstring do módulo sobre o limite que isto aceita).
_NUMERO_ISOLADO = re.compile(r"^[ \t]*[0-9]{1,3}[ \t]*$")

# Candidato a nota de rodapé: número de 1 a 3 dígitos seguido de espaço e
# texto, SEM o ponto que marcaria início de parágrafo. Só é tratado como
# ruído quando a linha anterior estava em branco (ver docstring do módulo).
_NUMERO_SEM_PONTO = re.compile(r"^[ \t]*[0-9]{1,3}[ \t]+\S.*$")


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
    própria, condicionada a vir após linha em branco — ver `_NUMERO_SEM_PONTO`
    e `_NUMERO_ISOLADO`.
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

        if linha_anterior_em_branco and _NUMERO_ISOLADO.match(linha):
            # número de página plausível: só dígitos, até 3, após branco.
            linha_anterior_em_branco = em_branco
            i += 1
            continue

        if linha_anterior_em_branco and _NUMERO_SEM_PONTO.match(linha):
            # nota de rodapé: consome o BLOCO inteiro (todas as linhas até a
            # próxima linha em branco ou o fim do texto), não só esta linha —
            # a nota real da Corte costuma quebrar em várias linhas.
            i += 1
            while i < total and linhas[i].strip():
                i += 1
            continue

        if corrente is not None:
            despido = linha.strip()
            if despido:
                corrente.append(despido)

        linha_anterior_em_branco = em_branco
        i += 1

    return [
        Paragrafo(numero=n, texto=re.sub(r"\s+", " ", " ".join(partes)).strip())
        for n, partes in achados
    ]


def relatorio_lacunas(paragrafos: list[Paragrafo]) -> list[int]:
    """Números faltantes entre o menor e o maior parágrafo achado.

    Serve de instrumento de qualidade do extrator: lacuna significa que a
    segmentação perdeu parágrafo, e parágrafo perdido é citação que não se acha.
    """
    if not paragrafos:
        return []
    numeros = {p.numero for p in paragrafos}
    return [n for n in range(min(numeros), max(numeros) + 1) if n not in numeros]
