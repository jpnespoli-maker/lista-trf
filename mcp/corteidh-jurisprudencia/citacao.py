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


def data_por_extenso(iso: str | None) -> str:
    """'2006-07-04' → '4 de julho de 2006'.

    Data AUSENTE (None, vazia, só espaços) devolve string vazia, e quem chama
    omite a cláusula de data. `str(None)` daria a palavra literal "None" dentro
    da citação — "Sentença de None." colado numa peça é pior que data faltando,
    porque parece dado e não parece falta.

    Data PRESENTE mas ilegível volta como veio: é informação que quem chamou
    colocou de propósito, e suprimi-la esconderia o problema.
    """
    if iso is None or not str(iso).strip():
        return ""
    try:
        d = date.fromisoformat(str(iso).strip())
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

    # O `caso` chega SEMPRE NU, espelhando a coluna `documento.caso` do índice
    # ("Ximenes Lopes Vs. Brasil", sem prefixo). É esta função que acrescenta
    # "Caso", e para TODO tipo que se refira a um caso contencioso — não só
    # `CC`. Parecer consultivo não é caso e não recebe o prefixo.
    # Prefixar só `CC` faria a resolução de supervisão sair "Corte IDH. Gomes
    # Lund e outros Vs. Brasil. Supervisão...", sem "Caso" — citação verdadeira
    # com aparência de truncada, que é o defeito que este módulo existe para
    # evitar.
    partes.append(f"{caso.strip()}." if tipo == "OC" else f"Caso {caso.strip()}.")

    por_extenso = data_por_extenso(data)

    def _com_data(rotulo: str) -> str:
        """'Sentença' + data → 'Sentença de 4 de julho de 2006.'; sem data,
        'Sentença.' — nunca 'Sentença de .' nem 'Sentença de None.'"""
        return f"{rotulo} de {por_extenso}." if por_extenso else f"{rotulo}."

    if tipo == "OC":
        if etapa:
            partes.append(f"{etapa.strip()}.")
        ano_curto = str(data)[2:4] if data and len(str(data)) >= 4 else "00"
        partes.append(_com_data(f"Parecer Consultivo OC-{numero}/{ano_curto}"))
    elif tipo in ("SS", "PS"):
        partes.append("Supervisão de Cumprimento de Sentença.")
        partes.append(_com_data("Resolução"))
    elif tipo == "CC":
        if etapa:
            partes.append(f"{etapa.strip()}.")
        partes.append(_com_data("Sentença"))
    else:
        # Tipo fora dos três que a Fase 1 e a Fase 2 indexam. O filtro oficial
        # tem 19 valores, e rotular qualquer um deles de "Sentença" seria
        # AFIRMAR o que não se sabe: uma Medida Provisória, por exemplo, se
        # cita como "Resolução sobre Medidas Provisórias", não como sentença.
        # Rótulo neutro mais o código do tipo, para que a lacuna se veja na
        # própria citação em vez de passar por dado correto.
        if etapa:
            partes.append(f"{etapa.strip()}.")
        partes.append(_com_data("Decisão"))
        partes.append(f"[tipo {tipo} — rótulo canônico não definido]")

    if serie and numero is not None:
        cauda = f"Série {serie} No. {numero}"
        cauda += f", par. {paragrafo}." if paragrafo is not None else "."
        partes.append(cauda)

    if url and url.strip():
        partes.append(f"Disponível em: {url.strip()}")

    return " ".join(partes)
