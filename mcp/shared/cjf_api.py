"""Cliente da API NOVA do CJF Unificada — rota de RESERVA dos clientes JSF.

Em 22/10/2026 o jurisprudencia.cjf.jus.br virou SPA (Angular, "busca semântica")
e o portal JSF passou a antiga-jurisprudencia.cjf.jus.br, que o próprio site novo
chama de "versão antiga". Os clientes ``cjf_client`` e ``cjf_regional_client``
seguem nele; quando ele também virar SPA, levantam ``PortalJsfIndisponivel`` e a
busca cai aqui, sem intervenção.

O QUE FOI MEDIDO NA API (22/10/2026), e que este módulo respeita:

- ``POST /api/jurisprudencia/pesquisar``, JSON, sem token. Aceita ``E``, ``OU``,
  ``ADJ`` e ``[EMEN]`` na query. Resposta Elasticsearch: ``results.hits[]._source``
  e agregações.
- Filtro ``{"TRIBUNAL": [...]}``, sem distinção de caixa. As agregações por
  ``TRIBUNAL`` vêm GLOBAIS (não mudam com o filtro) e com contagem exata, ao
  passo que ``results.total`` satura em 10.000 (``relation: gte``) — por isso os
  totais saem dos baldes.
- COBERTURA: STF e os seis TRFs. O STJ é RESÍDUO ("honorarios": 2 contra 33.225
  no portal antigo) e é recusado aqui, nunca devolvido como zero.
- O STF vem DEGRADADO: sem data, sem órgão julgador, ementa com o rótulo
  "EMENTA" e quebra de coluna no meio da palavra. Serve para localizar o
  julgado; citar exige conferir no portal do STF (``extra``-aviso no documento).
- Turma recursal vem com ``TRIBUNAL: "JEF1"``, mas o filtro ``TRIBUNAL: JEF1`` é
  lido como TRF1 e devolve o tribunal. O JEF1 se filtra pelos órgãos de turma
  recursal da região, que a agregação ``ORIGEM_JEF.tr_orgaos_by_regiao`` lista
  (a soma deles bate com o ``doc_count`` da região).

Cada documento sai no MESMO dicionário dos clientes JSF, com ``via: "api-nova"``.

TNU: aqui a API não é reserva, é a ROTA PRINCIPAL (``buscar_tnu``, usado pela tool
``buscar_jurisprudencia_tnu`` do ``cjf-jurisprudencia``) — nenhuma outra base do
projeto tem a TNU posterior a julho de 2017.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Tuple

import requests

API_URL = "https://jurisprudencia.cjf.jus.br/api/jurisprudencia/pesquisar"
TRIBUNAIS_COBERTOS = ("STF", "TRF1", "TRF2", "TRF3", "TRF4", "TRF5", "TRF6")
VIA = "api-nova"
AVISO_STF = (
    "API nova do CJF: julgado do STF sem data nem órgão, e ementa com quebra de "
    "coluna. Conferir no portal do STF antes de citar."
)

_TIMEOUT_S = 60
_PAGINA_MAX = 100

# Tipos do portal regional → TIPO_DOCUMENTO da API. Súmula e arguição não têm
# equivalente medido.
_TIPOS_REGIONAL = {"ACORDAO": "ACÓRDÃO", "DECISAOMONO": "DECISÃO MONOCRÁTICA"}


class PortalJsfIndisponivel(ValueError):
    """O host do portal JSF serviu página sem ViewState (o SPA novo)."""


class SemCoberturaNaApi(RuntimeError):
    """Tribunal pedido que a API nova não cobre. Nunca vira zero."""

    def __init__(self, sem_cobertura: Sequence[str], cobertos: Sequence[str]):
        self.sem_cobertura = list(sem_cobertura)
        self.cobertos = list(cobertos)
        dica = " Para o STJ, usar o stj-jurisprudencia (SCON)." if "STJ" in self.sem_cobertura else ""
        super().__init__(
            f"O portal antigo do CJF está fora e a API nova não cobre "
            f"{', '.join(self.sem_cobertura)} (medido em 22/10/2026).{dica}"
        )


def _texto(valor: Any) -> str:
    return " ".join(str(valor or "").split())


def _data_br(iso: Any) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else ""


def documento(source: Dict[str, Any]) -> Dict[str, Any]:
    """``_source`` da API → dicionário dos clientes JSF (só os campos com valor)."""
    tribunal = (source.get("TRIBUNAL") or "").upper()
    doc = {
        "numero": _texto(source.get("PROCESSO_CITACAO") or source.get("PROCESSO")),
        "classe": _texto(source.get("CLASSE")),
        "relator": _texto(source.get("RELATOR")),
        "orgao_julgador": _texto(source.get("ORGAO_JULGADOR")),
        "data_julgamento": _data_br(source.get("DATA_DECISAO")),
        "data_publicacao": _data_br(source.get("DATA_PUBLICACAO_PESQ")),
        "fonte_publicacao": _texto(source.get("FONTE_PUBLICACAO")),
        "decisao": _texto(source.get("DECISAO")),
        "ementa": re.sub(r"^EMENTA\s+", "", _texto(source.get("EMENTA"))),
        "tipo": _texto(source.get("TIPO_DOCUMENTO")),
    }
    doc = {k: v for k, v in doc.items() if v}
    doc["tribunal"] = tribunal
    doc["via"] = VIA
    if tribunal == "STF":
        doc["aviso"] = AVISO_STF
    return doc


def pesquisar(
    query: str,
    filtros: Dict[str, List[str]],
    n: int,
    *,
    ordenar_por_data: bool = False,
    processo: str | None = None,
) -> Dict[str, Any]:
    ordem = [{"field": "DATA_DECISAO", "direction": "desc"}] if ordenar_por_data else []
    corpo = {
        "query": query, "filters": filtros, "sort": ordem, "page_size": max(1, min(n, _PAGINA_MAX)),
        "current_page": 1, "process_number": processo, "decision_date_start": None,
        "decision_date_end": None, "date_type": "DATA_DECISAO", "rapporteurs": [],
        "revisors": [], "relator_convocado": None, "relator_acordaos": [],
        "ref_legislativa": None,
    }
    resp = requests.post(API_URL, json=corpo, timeout=_TIMEOUT_S)
    resp.raise_for_status()
    dados = resp.json()
    if "results" not in dados or "aggregations" not in dados:
        raise RuntimeError(
            f"API nova do CJF respondeu sem 'results'/'aggregations' — o contrato "
            f"mudou. Chaves: {sorted(dados)[:10]}"
        )
    return dados


def _docs_com_canario(dados: Dict[str, Any], total: int, n: int, onde: str) -> List[Dict[str, Any]]:
    docs = [documento(h.get("_source") or {}) for h in dados["results"].get("hits", [])]
    if total > 0 and not docs:
        raise RuntimeError(
            f"API nova do CJF reportou {total} documento(s) em {onde} e devolveu 0 — "
            "não tratar como 'nada encontrado'."
        )
    return docs[:n]


def buscar_unificada(
    termo: str, tribunais: Sequence[str], max_resultados: int
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Mesmo contrato de ``cjf_client.buscar_documentos``: ``(docs, totais)``."""
    pedidos = [t.strip().upper() for t in tribunais]
    sem = [t for t in pedidos if t not in TRIBUNAIS_COBERTOS]
    if sem:
        raise SemCoberturaNaApi(sem, [t for t in pedidos if t in TRIBUNAIS_COBERTOS])
    dados = pesquisar(termo, {"TRIBUNAL": pedidos}, max_resultados)
    totais = {t: 0 for t in pedidos}
    for balde in dados["aggregations"].get("TRIBUNAL", {}).get("buckets", []):
        chave = str(balde.get("key", "")).upper()
        if chave in totais:
            totais[chave] = int(balde.get("doc_count", 0))
    docs = _docs_com_canario(dados, sum(totais.values()), max_resultados, ", ".join(pedidos))
    return docs, totais


# TNU (medido em 22/10/2026): filtra-se pelo órgão, com esta grafia EXATA
# ("Turma Nacional de Uniformização" e "TNU" dão zero). O acervo vai até 2026 —
# "beneficio assistencial" deu 2.702 julgados, o mais recente de 14/08/2026 —,
# ao passo que a base da TNU no portal antigo termina em julho de 2017.
TNU_ORGAO = "TURMA NACIONAL DE UNIFORMIZAÇÃO"
# Número CNJ no TEXTO da busca dá zero; ele só casa pelo campo `process_number`.
_RE_CNJ = re.compile(r"^\s*(\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})(?:/TNU)?\s*$", re.I)


def buscar_tnu(
    termo: str, max_resultados: int, *, ordenar_por_data: bool = False
) -> Tuple[List[Dict[str, Any]], int, bool]:
    """Julgados da TNU: ``(docs, total, total_exato)``.

    Busca que é só um número CNJ vai pelo ``process_number`` e traz os
    julgados daquele processo. ``total_exato`` é falso quando a API satura a
    contagem (``relation: gte``, a partir de 10.000).
    """
    m = _RE_CNJ.match(termo or "")
    if m:
        dados = pesquisar("", {"ORGAO_JULGADOR": [TNU_ORGAO]}, max_resultados,
                          ordenar_por_data=ordenar_por_data, processo=m.group(1))
    else:
        dados = pesquisar(termo, {"ORGAO_JULGADOR": [TNU_ORGAO]}, max_resultados,
                          ordenar_por_data=ordenar_por_data)
    contagem = dados["results"].get("total") or {}
    total = int(contagem.get("value", 0))
    docs = _docs_com_canario(dados, total, max_resultados, "TNU")
    return docs, total, contagem.get("relation", "eq") == "eq"


def _filtro_tipos(tipos: Sequence[str]) -> Dict[str, List[str]]:
    pedidos = set(tipos)
    if pedidos >= {"ACORDAO", "SUMULA", "ARGUICAO", "DECISAOMONO"}:
        return {}
    sem_equivalente = sorted(pedidos - set(_TIPOS_REGIONAL))
    if sem_equivalente:
        raise RuntimeError(
            f"O portal antigo do CJF está fora e a API nova não tem tipo equivalente a "
            f"{', '.join(sem_equivalente)}."
        )
    return {"TIPO_DOCUMENTO": [_TIPOS_REGIONAL[t] for t in sorted(pedidos)]}


def buscar_regional(
    termo: str, fonte: str, tipos: Sequence[str], max_resultados: int
) -> Tuple[List[Dict[str, Any]], int]:
    """Mesmo contrato de ``cjf_regional_client._buscar_uma_fonte``: ``(docs, total)``.

    O ``total`` sai da agregação, que é GLOBAL: com ``tipos`` restrito, os
    documentos vêm filtrados e o total conta todos os tipos.
    """
    filtros = _filtro_tipos(tipos)
    if fonte == "TRF1":
        dados = pesquisar(termo, {**filtros, "TRIBUNAL": ["TRF1"]}, max_resultados)
        total = next(
            (int(b.get("doc_count", 0)) for b in dados["aggregations"].get("TRIBUNAL", {}).get("buckets", [])
             if str(b.get("key", "")).upper() == "TRF1"),
            0,
        )
    elif fonte == "JEF1":
        base = pesquisar(termo, filtros, 1)
        regiao = (base["aggregations"].get("ORIGEM_JEF", {})
                  .get("tr_orgaos_by_regiao", {}).get("1", {}))
        orgaos = [o["key"] for o in regiao.get("orgaos", []) if o.get("key")]
        total = int(regiao.get("doc_count", 0))
        if not orgaos:
            return [], total
        dados = pesquisar(termo, {**filtros, "ORGAO_JULGADOR": orgaos}, max_resultados)
    else:
        raise ValueError(f"Fonte regional desconhecida: {fonte}")
    docs = _docs_com_canario(dados, total, max_resultados, fonte)
    for d in docs:
        d["fonte"] = fonte
    return docs, total
