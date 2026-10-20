"""Transporte HTTP do Banco de Sentenças das Justiças Militares (PDPJ/CNJ).

Consulta SOB DEMANDA, uma requisição por pedido e com cache no servidor: o
banco não publica ``robots.txt`` (a rota devolve a própria página), mas é um
serviço público e não se colhe em lote. As sentenças trazem o nome das partes
(o banco publica só as "públicas e não sigilosas"); o cliente não oferece busca
por parte.
"""

from __future__ import annotations

import json

import requests

import sentencas_parser as P

TIMEOUT_S = 60
_CABECALHOS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Origin": P.PAGINA_PUBLICA.rstrip("/"),
    "Referer": P.PAGINA_PUBLICA,
}


class BancoSentencasIndisponivel(RuntimeError):
    """O banco não devolveu o que o contrato medido prevê."""


def buscar(corpo: dict) -> tuple[int, list[P.SentencaMilitar]]:
    try:
        r = requests.post(P.BUSCAR_URL, data=json.dumps(corpo).encode("utf-8"),
                          headers=_CABECALHOS, timeout=TIMEOUT_S)
    except requests.RequestException as e:
        raise BancoSentencasIndisponivel(f"falha de rede: {type(e).__name__}: {e}") from e
    if r.status_code == 403:
        raise BancoSentencasIndisponivel(
            "HTTP 403 — o gateway responde assim a corpo fora do contrato (era o caso de "
            "pageSize diferente de 10/20, medido em 20/10/2026); se o corpo está no "
            "contrato, a API mudou e o sentencas_parser precisa ser revisto"
        )
    if r.status_code != 200:
        raise BancoSentencasIndisponivel(f"HTTP {r.status_code} em {P.BUSCAR_URL}")
    try:
        dados = r.json()
    except ValueError as e:
        raise BancoSentencasIndisponivel("resposta não é JSON") from e
    try:
        return P.parsear_resposta(dados)
    except ValueError as e:
        raise BancoSentencasIndisponivel(str(e)) from e
