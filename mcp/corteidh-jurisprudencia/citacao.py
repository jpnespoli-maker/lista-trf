"""Forma canônica da citação da Corte IDH.

Módulo puro. A forma varia com o TIPO do documento: sentença de caso
contencioso diz "Sentença de <data>. Série C No. N"; parecer consultivo diz
"Parecer Consultivo OC-N/AA de <data>. Série A No. N"; resolução de supervisão
não tem série e diz "Resolução de <data>".
"""

from __future__ import annotations

from datetime import date

_MESES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)


def data_por_extenso(iso: str) -> str:
    """'2006-07-04' → '4 de julho de 2006'. Data ilegível volta como veio."""
    try:
        d = date.fromisoformat(iso)
    except (ValueError, TypeError):
        return str(iso)
    return f"{d.day} de {_MESES[d.month - 1]} de {d.year}"


def formatar_citacao(
    *,
    caso: str,
    data: str,
    serie: str | None,
    numero: int | None,
    paragrafo: int | None = None,
    etapa: str | None = None,
    tipo: str = "CC",
    url: str | None = None,
) -> str:
    """Monta a citação canônica, com o endereço de conferência no fim.

    O `url` é o link do IDIOMA DO TRECHO citado, e quem chama é responsável por
    escolhê-lo: apontar para o PDF em português ao lado de uma aspa em espanhol
    destruiria o objetivo do link, que é o Defensor achar aquele texto na fonte.
    A função é pura e não adivinha endereço — sem `url`, sai sem link.
    """
    partes = ["Corte IDH."]

    if tipo == "CC":
        partes.append(f"Caso {caso.strip()}.")
    else:
        partes.append(f"{caso.strip()}.")

    if tipo == "OC":
        if etapa:
            partes.append(f"{etapa.strip()}.")
        ano_curto = data[2:4] if len(data) >= 4 else "00"
        partes.append(
            f"Parecer Consultivo OC-{numero}/{ano_curto} de "
            f"{data_por_extenso(data)}."
        )
    elif tipo in ("SS", "PS"):
        partes.append("Supervisão de Cumprimento de Sentença.")
        partes.append(f"Resolução de {data_por_extenso(data)}.")
    else:
        if etapa:
            partes.append(f"{etapa.strip()}.")
        partes.append(f"Sentença de {data_por_extenso(data)}.")

    if serie and numero is not None:
        cauda = f"Série {serie} No. {numero}"
        cauda += f", par. {paragrafo}." if paragrafo is not None else "."
        partes.append(cauda)

    if url and url.strip():
        partes.append(f"Disponível em: {url.strip()}")

    return " ".join(partes)
