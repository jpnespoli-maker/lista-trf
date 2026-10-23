"""Cliente da API NOVA do CJF Unificada — rota de RESERVA dos clientes JSF.

Em 22/10/2026 o jurisprudencia.cjf.jus.br virou SPA (Angular, "busca semântica")
e o portal JSF passou a antiga-jurisprudencia.cjf.jus.br, que o próprio site novo
chama de "versão antiga". Os clientes ``cjf_client`` e ``cjf_regional_client``
seguem nele; quando ele também virar SPA, levantam ``PortalJsfIndisponivel`` e a
busca cai aqui, sem intervenção.

O QUE FOI MEDIDO NA API (22/10/2026), e que este módulo respeita:

- ``POST /api/jurisprudencia/pesquisar``, JSON, sem token. Aceita ``E``, ``OU``,
  ``ADJ`` e ``[EMEN]`` na query, só em MAIÚSCULAS. Resposta Elasticsearch:
  ``results.hits[]._source`` e agregações.
- Palavras soltas, sem operador, a API relaxa para OU em silêncio quando o E
  daria zero (23/10/2026: "bolsa familia unipessoal" no TRF4, 2.590 contra 0).
  ``pesquisar`` explicita o E, e o zero chega ao chamador, que relaxa com aviso.
- Filtro ``{"TRIBUNAL": [...]}``, sem distinção de caixa, alcança SÓ o 2º grau.
  Turma recursal e TNU se filtram por ``{"ORIGEM": ["JEF:TR:<n>", "JEF:TRU:<n>",
  "JEF:TNU"]}``, as fichas do próprio site; as duas chaves na mesma chamada se
  SOMAM (TRF3 16 + turma recursal da 3ª Região 42 = 58, 23/10/2026).
- As agregações ``TRIBUNAL`` (2º grau) e ``ORIGEM_JEF`` (``tr_by_regiao``,
  ``tru_by_regiao``, ``tnu``) vêm GLOBAIS (não mudam com o filtro) e com contagem
  exata, ao passo que ``results.total`` satura em 10.000 (``relation: gte``) —
  por isso os totais saem delas.
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
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple
from urllib.parse import quote

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
    """Tribunal pedido que a API nova não cobre. Nunca vira zero.

    A mensagem diz só o que a API não cobre; o PORQUÊ de a busca ter ido só a ela
    (portal antigo fora, filtro que só a API tem) é do chamador, em ``contexto``
    (ex.: "O portal antigo do CJF está fora, e ")."""

    def __init__(self, sem_cobertura: Sequence[str], cobertos: Sequence[str], contexto: str = ""):
        self.sem_cobertura = list(sem_cobertura)
        self.cobertos = list(cobertos)
        dica = " Para o STJ, usar o stj-jurisprudencia (SCON)." if "STJ" in self.sem_cobertura else ""
        texto = (
            f"a API nova do CJF não cobre {', '.join(self.sem_cobertura)} "
            f"(medido em 22/10/2026).{dica}"
        )
        super().__init__(contexto + texto if contexto else texto[0].upper() + texto[1:])


def _texto(valor: Any) -> str:
    return " ".join(str(valor or "").split())


def _data_br(iso: Any) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else ""


@dataclass(frozen=True)
class FiltrosApi:
    """Filtros e modo que só a API nova tem. ``legislacao`` já vem no formato da API."""

    modo: str = "comum"  # "comum" | "semantica"
    data_inicio: str | None = None  # AAAA-MM-DD
    data_fim: str | None = None
    tipo_data: str = "DATA_DECISAO"  # ou "DATA_PUBLICACAO_PESQ"
    relatores: Tuple[str, ...] = ()
    legislacao: str | None = None
    so_precedentes_relevantes: bool = False
    so_representativos: bool = False

    def ativo(self) -> bool:
        return self != FiltrosApi()


def documento(source: Dict[str, Any], doc_id: str = "") -> Dict[str, Any]:
    """``_source`` da API → dicionário dos clientes JSF (só os campos com valor)."""
    tribunal = (source.get("TRIBUNAL") or "").upper()
    link = _texto(source.get("LINK_INTEIRO_TEOR"))
    if not link:
        inteiro = _texto(source.get("INTEIRO_TEOR"))
        link = inteiro if inteiro.startswith("http") else ""
    doc = {
        "id": _texto(doc_id),
        "link_inteiro_teor": link,
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
    if str(source.get("PRECEDENTE_RELEVANTE")) == "1":
        doc["precedente_relevante"] = True
    if str(source.get("REPRESENTATIVO_CONTROVERSIA")) == "1":
        doc["representativo_controversia"] = True
    doc["tribunal"] = tribunal
    doc["via"] = VIA
    if tribunal == "STF":
        doc["aviso"] = AVISO_STF
    return doc


_RE_OPERADOR = re.compile(r"^(E|OU|NAO|NÃO|XOU|COM|MESMO|ADJ\d*|PROX\d*)$")


def _e_explicito(query: str) -> str:
    """Palavras soltas → unidas por ``E``. Busca com operador, aspas, parênteses
    ou campo (``[EMEN]``) vai intacta: ali a API não relaxa."""
    termos = query.split()
    if len(termos) < 2 or any(c in query for c in '"()[]'):
        return query.strip()
    if any(_RE_OPERADOR.match(t) for t in termos):
        return query
    return " E ".join(termos)


def _corpo(
    query: str,
    filtros: Dict[str, List[str]],
    n: int,
    *,
    ordenar_por_data: bool = False,
    processo: str | None = None,
    extras: FiltrosApi | None = None,
) -> Dict[str, Any]:
    """Corpo JSON de ``/pesquisar`` e ``/analytics`` (o mesmo nas duas rotas).

    No modo semântico a query vai CRUA: o E explícito é da busca por palavra, e na
    semântica muda o resultado. Medido em 23/10/2026 (/pesquisa-semantica, TRF1 a
    TRF6, 10 por página), a mesma query crua repetida dá resposta idêntica, ao
    passo que "pessoa que mora sozinha teve o bolsa familia cancelado" crua x
    reescrita com E: total 400 nas duas, só 8 dos 10 primeiros em comum e TRF1
    34 x 56; "idoso com deficiencia teve o beneficio assistencial negado por
    renda": 10 de 10 em comum, em outra ordem, e TRF3 83 x 10."""
    ordem = [{"field": "DATA_DECISAO", "direction": "desc"}] if ordenar_por_data else []
    semantica = extras is not None and extras.modo == "semantica"
    corpo = {
        "query": query.strip() if semantica else _e_explicito(query), "filters": dict(filtros), "sort": ordem, "page_size": max(1, min(n, _PAGINA_MAX)),
        "current_page": 1, "process_number": processo, "decision_date_start": None,
        "decision_date_end": None, "date_type": "DATA_DECISAO", "rapporteurs": [],
        "revisors": [], "relator_convocado": None, "relator_acordaos": [],
        "ref_legislativa": None,
    }
    if extras is not None:
        corpo["decision_date_start"] = extras.data_inicio
        corpo["decision_date_end"] = extras.data_fim
        corpo["date_type"] = extras.tipo_data
        corpo["rapporteurs"] = list(extras.relatores)
        corpo["ref_legislativa"] = extras.legislacao
        if extras.so_precedentes_relevantes:
            corpo["filters"]["PRECEDENTE_RELEVANTE"] = ["1"]
        if extras.so_representativos:
            corpo["filters"]["REPRESENTATIVO_CONTROVERSIA"] = ["1"]
    return corpo


def pesquisar(
    query: str,
    filtros: Dict[str, List[str]],
    n: int,
    *,
    ordenar_por_data: bool = False,
    processo: str | None = None,
    extras: FiltrosApi | None = None,
) -> Dict[str, Any]:
    corpo = _corpo(query, filtros, n, ordenar_por_data=ordenar_por_data, processo=processo, extras=extras)
    url = API_URL
    if extras is not None and extras.modo == "semantica":
        url = API_URL.rsplit("/", 1)[0] + "/pesquisa-semantica"
    resp = requests.post(url, json=corpo, timeout=_TIMEOUT_S)
    resp.raise_for_status()
    dados = resp.json()
    if "results" not in dados or "aggregations" not in dados:
        raise RuntimeError(
            f"API nova do CJF respondeu sem 'results'/'aggregations' — o contrato "
            f"mudou. Chaves: {sorted(dados)[:10]}"
        )
    return dados


def _docs_com_canario(dados: Dict[str, Any], total: int, n: int, onde: str) -> List[Dict[str, Any]]:
    docs = [documento(h.get("_source") or {}, h.get("_id", "")) for h in dados["results"].get("hits", [])]
    if total > 0 and not docs:
        raise RuntimeError(
            f"API nova do CJF reportou {total} documento(s) em {onde} e devolveu 0 — "
            "não tratar como 'nada encontrado'."
        )
    return docs[:n]


def _exigir_cobertura(tribunais: Sequence[str]) -> List[str]:
    pedidos = [t.strip().upper() for t in tribunais]
    sem = [t for t in pedidos if t not in TRIBUNAIS_COBERTOS]
    if sem:
        raise SemCoberturaNaApi(sem, [t for t in pedidos if t in TRIBUNAIS_COBERTOS])
    return pedidos


def _filtros_tribunais(pedidos: Sequence[str]) -> Dict[str, List[str]]:
    """TRIBUNAL + ORIGEM: cada TRF leva as turmas recursais e a TRU da sua Região,
    como no portal antigo; STF e demais só o TRIBUNAL."""
    filtros: Dict[str, List[str]] = {"TRIBUNAL": list(pedidos)}
    regioes = [t[-1] for t in pedidos if t.startswith("TRF")]
    if regioes:
        filtros["ORIGEM"] = [f"JEF:{tipo}:{r}" for r in regioes for tipo in ("TR", "TRU")]
    return filtros


def buscar_unificada(
    termo: str, tribunais: Sequence[str], max_resultados: int, *, extras: FiltrosApi | None = None
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Mesmo contrato de ``cjf_client.buscar_documentos``: ``(docs, totais)``."""
    pedidos = _exigir_cobertura(tribunais)
    filtros = _filtros_tribunais(pedidos)
    if extras is None:
        dados = pesquisar(termo, filtros, max_resultados)
    else:
        dados = pesquisar(termo, filtros, max_resultados, extras=extras)
    aggs = dados["aggregations"]
    totais = {t: 0 for t in pedidos}
    for balde in aggs.get("TRIBUNAL", {}).get("buckets", []):
        chave = str(balde.get("key", "")).upper()
        if chave in totais:
            totais[chave] = int(balde.get("doc_count", 0))
    origem_jef = aggs.get("ORIGEM_JEF", {})
    for agregacao in ("tr_by_regiao", "tru_by_regiao"):
        for balde in origem_jef.get(agregacao, []):
            chave = f"TRF{balde.get('key')}"
            if chave in totais:
                totais[chave] += int(balde.get("doc_count", 0))
    docs = _docs_com_canario(dados, sum(totais.values()), max_resultados, ", ".join(pedidos))
    return docs, totais


def buscar_api(
    termo: str, tribunais: Sequence[str], max_resultados: int, extras: FiltrosApi
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """``buscar_unificada`` com os filtros e o modo da API nova (``FiltrosApi``)."""
    return buscar_unificada(termo, tribunais, max_resultados, extras=extras)


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


_BASE_API = API_URL.rsplit("/", 1)[0]
_CAMPOS_DOCUMENTO = ("ementa", "decisao", "inteiro_teor")


def _get_json(caminho: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    resp = requests.get(f"{_BASE_API}{caminho}", params=params, timeout=_TIMEOUT_S)
    if resp.status_code != 200:
        raise RuntimeError(f"API nova do CJF respondeu HTTP {resp.status_code} em {caminho}")
    return resp.json()


def obter_documento(doc_id: str, campo: str = "") -> Dict[str, Any]:
    """Documento pelo id. Sem ``campo``: o documento completo mais ``inteiro_teor``
    (texto do ``_source``, quando há). Com ``campo`` (ementa, decisao,
    inteiro_teor): só aquele texto, ``{"id", "campo", "texto"}``."""
    if campo and campo not in _CAMPOS_DOCUMENTO:
        raise ValueError(f"campo deve ser um de {', '.join(_CAMPOS_DOCUMENTO)}; recebido {campo!r}")
    # O id vai quotado: com "/" ou "?" ele mudaria o caminho pedido à API.
    caminho = f"/documento/{quote(doc_id, safe='')}"
    if campo:
        dados = _get_json(f"{caminho}/campo/{campo}")
        return {"id": doc_id, "campo": campo, "texto": dados.get("texto", "")}
    dados = _get_json(caminho)
    hits = (dados.get("results") or {}).get("hits") or []
    if not hits:
        raise RuntimeError(f"API nova do CJF não devolveu o documento {doc_id}")
    source = hits[0].get("_source") or {}
    doc = documento(source, hits[0].get("_id") or doc_id)
    texto = _texto(source.get("INTEIRO_TEOR"))
    if texto:
        doc["inteiro_teor"] = texto
    return doc


def similares(doc_id: str, n: int) -> List[Dict[str, Any]]:
    dados = _get_json(f"/similar/{quote(doc_id, safe='')}", {"size": n})
    return [documento(h.get("_source") or {}, h.get("_id", "")) for h in dados["results"].get("hits", [])]


def estatisticas(termo: str, tribunais: Sequence[str], extras: FiltrosApi) -> Dict[str, Any]:
    """Agregações da busca (``/analytics``): total, julgamentos por ano e os
    relatores, classes e tribunais que mais aparecem. Mesmos filtros de ``buscar_unificada``."""
    pedidos = _exigir_cobertura(tribunais)
    corpo = _corpo(termo, _filtros_tribunais(pedidos), 1, extras=extras)
    corpo["page_size"] = 0
    corpo["search_mode"] = extras.modo
    resp = requests.post(f"{_BASE_API}/analytics", json=corpo, timeout=_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def sugerir_relator(texto: str) -> List[str]:
    """Grafias de relator que a API conhece (``/sugestoes/relator?q=``).

    Medido em 23/10/2026: o filtro ``rapporteurs`` casa a grafia exata da base e
    várias grafias se SOMAM (OU), não se cruzam: ``["LUIZ FUX"]`` 6.358,
    ``["Ministro Luiz Fux"]`` 1, as duas juntas 6.359; ``["PAULO ROBERTO PARCA DE
    PINHO"]`` 297 + ``["LUIZ FUX"]`` 6.358 = 6.655 juntos. Por isso o relator se
    filtra pela grafia devolvida aqui, e vários relatores ampliam a busca."""
    return list(_get_json("/sugestoes/relator", {"q": texto}).get("sugestoes", []))


_RE_LEGISLACAO = re.compile(
    r"^\s*(DECRETO[\s-]*LEI|DEL|LEI[\s-]+COMPLEMENTAR|LCP|LC|MEDIDA[\s-]+PROVIS.RIA|MPV|MPR|MP|DECRETO|DEC|LEI)\b"
    r"[\s.-]*(?:N[º°O]\.?|N\.)?[\s-]*([0-9][0-9.]*)",
    re.IGNORECASE,
)
# Prefixo que a API guarda em REF_LEGISLATIVA (lido nos documentos).
_PREFIXO_LEGISLACAO = {"LEI": "LEI", "LCP": "LCP", "LC": "LCP", "DEL": "DEL", "DEC": "DEC", "DECRETO": "DEC",
                       "MPV": "MPR", "MPR": "MPR", "MP": "MPR"}


def normalizar_legislacao(texto: str) -> str:
    """"Lei 8.742/93", "lei nº 8.742", "LEI-8742" → ``LEI-8742``, o formato de ``ref_legislativa``.

    Aceita LEI, LEI COMPLEMENTAR/LC, DECRETO, DECRETO-LEI e MEDIDA PROVISÓRIA/MP,
    com "nº", "n." ou "no" antes do número; o que vem depois do número (ano,
    "art. N") é ignorado. Tipo ou número não reconhecido levanta ``ValueError``:
    ``ref_legislativa`` errado dá zero, e zero se lê como "sem jurisprudência".

    Medido em 23/10/2026 (total de ``ref_legislativa``, e o prefixo armazenado lido
    nos documentos). O número vai SEM zeros (a API os ignora):
    - Lei: ``LEI-8742`` 9.831 = ``LEI-008742`` 9.831; ``LEI-10836`` 35 =
      ``LEI-010836`` 35. ``LEG-FED LEI-008742`` 5.777 (mais restrito, descartado).
    - Lei Complementar: ``LCP-80`` 2.199 = ``LCP-000080`` 2.199; ``LC-80`` 12,
      ``LEI COMPLEMENTAR-80`` e ``LEI-COMPLEMENTAR-80`` 15 (casam texto solto, não o
      prefixo armazenado ``LCP``). Usa-se ``LCP-``.
    - Decreto: ``DEC-3048`` e ``DEC-003048`` 10.000+ (satura; os 100 primeiros
      hits trazem ``DEC-003048``), ``DEC-3049`` 82; ``DECRETO-3048`` 91 (texto
      solto, descartado). Usa-se ``DEC-``.
    - Decreto-Lei: ``DEL-4657`` 6.104 = ``DEL-004657`` 6.104 (99 dos 100 primeiros
      hits trazem a referência); ``DECRETO-LEI-4657`` 27 (descartado). Usa-se ``DEL-``.
    - Medida provisória: ``MPR-2164`` 3.178, ``MPR-2215`` 390, ``MPR-871`` 11;
      ``MP-2164`` 115 e ``MEDIDA PROVISORIA-2164`` 2 (descartados). Usa-se ``MPR-``;
      a reedição ("2.164-41") não entra.
    Controle: número inexistente (``-999999``) dá 0 em LEI, LCP, DEC, DEL e MPR.
    A API casa por token, então o total pode incluir documentos que só citam o
    número em outro dispositivo (``LCP-80``: 18 dos 100 primeiros com ``LCP-...80``)."""
    m = _RE_LEGISLACAO.match(texto or "")
    numero = m.group(2).replace(".", "").lstrip("0") if m else ""
    if not numero:
        raise ValueError(
            f"Legislação não entendida: {texto!r}. Formas aceitas: Lei, Lei Complementar (LC), "
            "Decreto, Decreto-Lei e Medida Provisória (MP) seguidos do número, "
            'ex.: "Lei 8.742/93", "LC 80", "Decreto 3.048", "Decreto-Lei 4.657", "MP 2.164".'
        )
    tipo = re.sub(r"[\s-]+", " ", m.group(1).upper())
    if tipo.startswith("DECRETO") and "LEI" in tipo:
        prefixo = "DEL"
    elif tipo.startswith("LEI") and "COMPLEMENTAR" in tipo:
        prefixo = "LCP"
    elif tipo.startswith("MEDIDA"):
        prefixo = "MPR"
    else:
        prefixo = _PREFIXO_LEGISLACAO[tipo]
    return f"{prefixo}-{numero}"
