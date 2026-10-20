"""Transporte HTTP da Consulta Judicial Unificada da JMU (jurisprudência do STM).

Só a base ``jurisprudencia``. O mesmo portal busca PROCESSOS por nome e CPF de
parte e de advogado (``search_filter_option=feitos``), e as opções ``irdr`` e
``revista``, pedidas como busca livre, caem nessa listagem de processos com os
nomes das partes (medido em 20/10/2026): o cliente não as expõe.

O ``robots.txt`` do portal veda robôs (``Disallow: /``). Este cliente existe
para a consulta SOB DEMANDA de quem pesquisa — uma requisição por pedido, com
cache no servidor, sem paginação automática nem colheita em lote.
"""

from __future__ import annotations

import requests

import stm_parser as P

TIMEOUT_S = 60
_CABECALHOS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

# Consulta que TEM de casar: separa "o portal respondeu zero" de "o portal
# não está respondendo" (a página do zero é a mesma da recusa).
BUSCA_CONTROLE = "deserção"


class PortalSTMIndisponivel(RuntimeError):
    """O portal não devolveu o que o contrato medido prevê."""


def _get(url: str, params: dict) -> str:
    try:
        r = requests.get(url, params=params, headers=_CABECALHOS, timeout=TIMEOUT_S)
    except requests.RequestException as e:
        raise PortalSTMIndisponivel(f"falha de rede: {type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise PortalSTMIndisponivel(f"HTTP {r.status_code} em {url}")
    r.encoding = r.encoding or "utf-8"
    return r.text


def buscar(params: dict) -> tuple[int | None, list[P.AcordaoSTM]]:
    return P.parsear_resultados(_get(P.CONSULTA_URL, params))


def portal_responde() -> bool:
    """A consulta de controle traz contagem e painel?"""
    total, docs = buscar(P.montar_params(BUSCA_CONTROLE, quantidade=1))
    return bool(total) and bool(docs)


def modal(uuid: str, tipo: str) -> str:
    """Texto do modal (inteiro teor, referência legislativa, notas, indexação).

    Corpo vazio é o que o portal devolve para ``uuid`` desconhecido; o texto
    vazio volta como vazio, e quem chama decide.
    """
    if tipo not in P.TIPOS_MODAL:
        raise ValueError(f"tipo de modal desconhecido: {tipo!r}")
    html = _get(P.MODAL_URL, {
        "search_filter_option": "jurisprudencia",
        "uuid": P.validar_uuid(uuid),
        "doc_type": tipo,
    })
    return P.texto_modal(html)
