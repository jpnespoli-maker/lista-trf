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
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Início de parágrafo: número de 1 a 3 dígitos, ponto, espaço, no começo da
# linha. Classe [0-9] em vez de \d de propósito — ver a regra de barra
# invertida do projeto.
_INICIO = re.compile(r"^[ \t]*([0-9]{1,3})\.[ \t]+(.*)$")

# Linhas que nunca fazem parte de parágrafo.
_RUIDO = re.compile(
    r"^[ \t]*(?:-[ \t]*[0-9]+[ \t]*-"          # - 3 -   (número de página)
    r"|[0-9]+[ \t]*$"                           # linha só com número
    r")[ \t]*$"
)

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
    mas sem o `.`) precisa de uma regra própria — ver `_NUMERO_SEM_PONTO`.
    """
    if not texto or not texto.strip():
        return []

    achados: list[tuple[int, list[str]]] = []
    esperado = 1
    corrente: list[str] | None = None
    linha_anterior_em_branco = True

    for linha in texto.splitlines():
        em_branco = not linha.strip()

        if _RUIDO.match(linha):
            linha_anterior_em_branco = em_branco
            continue

        m = _INICIO.match(linha)
        if m and int(m.group(1)) == esperado:
            achados.append((esperado, [m.group(2)]))
            corrente = achados[-1][1]
            esperado += 1
            linha_anterior_em_branco = em_branco
            continue

        if linha_anterior_em_branco and _NUMERO_SEM_PONTO.match(linha):
            # nota de rodapé: número sem ponto, logo após linha em branco.
            linha_anterior_em_branco = em_branco
            continue

        if corrente is not None:
            despido = linha.strip()
            if despido:
                corrente.append(despido)

        linha_anterior_em_branco = em_branco

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
