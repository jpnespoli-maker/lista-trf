"""Cliente compartilhado do portal CJF Unificada (JSF/PrimeFaces).

Usado por ``cjf-jurisprudencia`` (busca multi-tribunal) e ``stj-jurisprudencia``
(fallback com filtro tribunais=STJ). Antes vivia duplicado quase linha a linha
nos dois servidores — extração 2026-06-10.

API de alto nível::

    from shared.cjf_client import buscar_documentos

    docs, totais = buscar_documentos("pensão E morte", ["STJ"], max_resultados=20)

Características:

- ViewState com TTL de 25 min (C3 / Onda 1) + singleton com recriação após
  1 h de idle (cookies stale).
- Descoberta dinâmica do ``name`` do checkbox de tribunal (o id ``j_idt51``
  é autogerado pelo JSF e pode mudar a cada deploy do portal); fallback para
  o valor histórico se a regex não casar.
- Canário estrutural: se o portal reporta total > 0 e o parser extrai 0
  documentos, levanta ``RuntimeError`` (falha LOUD) em vez de devolver vazio
  silencioso — indistinguível de "nada encontrado".
- Zero reconferido: ``total == 0`` é reexecutado UMA vez, com ViewState novo,
  antes de ser aceito. O shard de um tribunal pode ficar às escuras e o portal
  responde 200 com "Total 0 Documento(s)", sem erro — o canário acima cobre só
  o caso inverso, e o retry do ``@retry`` só dispara em exceção.
- Paginação best-effort via AJAX do datatable PrimeFaces
  (``formulario:tabelaDocumentos_first``), limitada a ``_MAX_PAGINAS``.
"""

from __future__ import annotations

import html
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from shared.base_juridica import TRIBUNAIS

CJF_URL = "https://jurisprudencia.cjf.jus.br/unificada/index.xhtml"

CJF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/xml, text/xml, */*; q=0.01",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Faces-Request": "partial/ajax",
    "X-Requested-With": "XMLHttpRequest",
}

# JSF do CJF mantém ViewState por ~30 min; renovar com margem de 5 min.
_VIEWSTATE_TTL_S = 25 * 60

# Valor histórico do checkbox de tribunal — usado se a descoberta dinâmica falhar.
_CHECKBOX_TRIBUNAL_FALLBACK = "formulario:j_idt51"

# Guarda da paginação: no máximo 5 páginas por busca (~50 documentos).
_MAX_PAGINAS = 5
_DOCS_POR_PAGINA = 10

# Pausa antes de reconferir um total=0 (ver reconferir_zero em buscar_documentos).
_PAUSA_RECONFERIR_ZERO_S = 1.5


class CJFSession:
    """Gerencia sessão com o portal CJF (requests.Session + ViewState com TTL)."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": CJF_HEADERS["User-Agent"],
            "Accept-Language": CJF_HEADERS["Accept-Language"],
        })
        self.viewstate: Optional[str] = None
        self._fetched_at: float = 0.0
        self.checkbox_tribunal: str = _CHECKBOX_TRIBUNAL_FALLBACK

    @property
    def viewstate_is_fresh(self) -> bool:
        return bool(self.viewstate) and (time.monotonic() - self._fetched_at) < _VIEWSTATE_TTL_S

    @retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
    def obter_viewstate(self) -> str:
        """Obtém ViewState da página inicial e descobre o name do checkbox."""
        resp = self.session.get(CJF_URL, timeout=30)
        resp.raise_for_status()

        self._descobrir_checkbox_tribunal(resp.text)

        match = re.search(r'name="javax\.faces\.ViewState"[^>]*value="([^"]+)"', resp.text)
        if match:
            self.viewstate = match.group(1)
            self._fetched_at = time.monotonic()
            return self.viewstate

        match = re.search(r'ViewState:([^"]+)"', resp.text)
        if match:
            self.viewstate = match.group(1)
            self._fetched_at = time.monotonic()
            return self.viewstate

        raise ValueError("ViewState não encontrado na página")

    def _descobrir_checkbox_tribunal(self, html_inicial: str) -> None:
        """Descobre o ``name`` real do checkbox de tribunal no HTML do GET inicial.

        O id ``j_idt51`` é autogerado pelo JSF — muda quando o portal é
        recompilado. Procura um input checkbox cujo value seja sigla de
        tribunal; mantém o fallback histórico se nada casar.
        """
        for pat in (
            r'name="(formulario:[\w:.-]+)"[^>]*\bvalue="(?:STF|STJ|TRF[1-6])"',
            r'\bvalue="(?:STF|STJ|TRF[1-6])"[^>]*name="(formulario:[\w:.-]+)"',
        ):
            m = re.search(pat, html_inicial)
            if m:
                self.checkbox_tribunal = m.group(1)
                return

    @retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
    def buscar(self, termo: str, tribunais: List[str]) -> str:
        """Submete a busca e retorna o HTML (partial JSF) da primeira página."""
        if not self.viewstate_is_fresh:
            self.obter_viewstate()

        form_data = [
            ("javax.faces.partial.ajax", "true"),
            ("javax.faces.source", "formulario:actPesquisar"),
            ("javax.faces.partial.execute", "@all"),
            ("javax.faces.partial.render", "formulario:resultado"),
            ("formulario:actPesquisar", "formulario:actPesquisar"),
            ("formulario", "formulario"),
            ("formulario:textoLivre", termo),
        ]
        for trib in tribunais:
            form_data.append((self.checkbox_tribunal, trib))
        form_data.append(("javax.faces.ViewState", self.viewstate))

        resp = self.session.post(CJF_URL, data=form_data, headers=CJF_HEADERS, timeout=60)
        resp.raise_for_status()
        return resp.text

    def buscar_pagina(self, first: int, rows: int = _DOCS_POR_PAGINA) -> str:
        """Pagina o datatable de resultados via AJAX PrimeFaces (best-effort).

        ``first`` é o offset absoluto (página 2 → first=10). Reusa o ViewState
        da busca corrente — deve ser chamado logo após ``buscar()``.
        """
        form_data = [
            ("javax.faces.partial.ajax", "true"),
            ("javax.faces.source", "formulario:tabelaDocumentos"),
            ("javax.faces.partial.execute", "formulario:tabelaDocumentos"),
            ("javax.faces.partial.render", "formulario:tabelaDocumentos"),
            ("formulario:tabelaDocumentos", "formulario:tabelaDocumentos"),
            ("formulario:tabelaDocumentos_pagination", "true"),
            ("formulario:tabelaDocumentos_first", str(first)),
            ("formulario:tabelaDocumentos_rows", str(rows)),
            ("formulario:tabelaDocumentos_encodeFeature", "true"),
            ("formulario", "formulario"),
            ("javax.faces.ViewState", self.viewstate),
        ]
        resp = self.session.post(CJF_URL, data=form_data, headers=CJF_HEADERS, timeout=60)
        resp.raise_for_status()
        return resp.text


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def extrair_totais(html_content: str) -> Dict[str, int]:
    """Extrai totais de documentos por tribunal."""
    totais: Dict[str, int] = {}
    pattern = r'<td[^>]*>(\w+)</td>\s*<td[^>]*>.*?(\d+)\s*Documento'
    matches = re.findall(pattern, html.unescape(html_content), re.DOTALL)

    for tribunal, count in matches:
        if tribunal in TRIBUNAIS:
            totais[tribunal] = int(count)

    return totais


def extrair_documentos(html_content: str) -> List[Dict[str, Any]]:
    """Extrai documentos detalhados da resposta (partial JSF)."""
    documentos = []
    content = html.unescape(html_content)

    cdata_matches = re.findall(r'<!\[CDATA\[(.*?)\]\]>', content, re.DOTALL)
    if cdata_matches:
        content = ''.join(cdata_matches)

    doc_indices = set(re.findall(r'tabelaDocumentos:(\d+):', content))

    for idx in sorted(doc_indices, key=int):
        doc: Dict[str, Any] = {"indice": int(idx)}

        campos = [
            ("numero", "Número"),
            ("classe", "Classe"),
            ("relator", r"Relator\(a\)"),
            ("orgao_julgador", "Órgão julgador"),
            ("data_julgamento", "Data"),
            ("data_publicacao", "Data da publicação"),
            ("fonte_publicacao", "Fonte da publicação"),
        ]

        for campo, label in campos:
            pattern = rf'tabelaDocumentos:{idx}:.*?label_pontilhada[^>]*>{label}</span>.*?<td[^>]*>([^<]+)</td>'
            match = re.search(pattern, content, re.DOTALL)
            if match:
                doc[campo] = match.group(1).strip()

        decisao_match = re.search(
            rf'tabelaDocumentos:{idx}:.*?label_pontilhada[^>]*>Decisão</span>.*?<td[^>]*>\s*([^<]+(?:<[^>]+>[^<]*</[^>]+>)*[^<]*?)\s*</td>',
            content, re.DOTALL
        )
        if decisao_match:
            decisao = decisao_match.group(1)
            decisao = re.sub(r'<[^>]+>', '', decisao).strip()
            decisao = re.sub(r'\s+', ' ', decisao)
            doc["decisao"] = decisao

        documentos.append(doc)

    ementa_pattern = re.compile(r'painel_ementa-([^"]+)"[^>]*>(.*?)</div>', re.DOTALL)
    ementas = []
    for match in ementa_pattern.finditer(content):
        ementa_raw = match.group(2)
        ementa = re.sub(r'<[^>]+>', '', ementa_raw).strip()
        ementa = re.sub(r'\s+', ' ', ementa)
        if len(ementa) > 50:
            ementas.append(ementa)

    for i, doc in enumerate(documentos):
        if i < len(ementas):
            doc["ementa"] = ementas[i]

    return [d for d in documentos if d.get("numero") or d.get("ementa")]


# ---------------------------------------------------------------------------
# Singleton com TTL embutido (C3)
# ---------------------------------------------------------------------------

_CJF_SHARED: Optional[CJFSession] = None


def get_cjf_session() -> CJFSession:
    """Retorna a CJFSession compartilhada; cria nova se inexistente.

    O ViewState dentro da sessão tem TTL próprio (25 min) — não precisa
    recriar a instância só por ele ter expirado. Mas se a sessão acumula
    muito tempo sem uso (>1 h), recriamos para evitar cookies stale.
    """
    global _CJF_SHARED
    if _CJF_SHARED is None:
        _CJF_SHARED = CJFSession()
        return _CJF_SHARED
    if _CJF_SHARED._fetched_at and (time.monotonic() - _CJF_SHARED._fetched_at) > 3600:
        try:
            _CJF_SHARED.session.close()
        except Exception:
            pass
        _CJF_SHARED = CJFSession()
    return _CJF_SHARED


# ---------------------------------------------------------------------------
# API de alto nível — busca + canário + paginação
# ---------------------------------------------------------------------------

def buscar_documentos(
    termo: str,
    tribunais: List[str],
    max_resultados: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Busca no CJF e retorna (documentos, totais_por_tribunal).

    - Em erro na primeira tentativa (ViewState expirado no servidor), força
      refresh do ViewState e tenta uma segunda vez.
    - Canário estrutural: total reportado > 0 e parser extraiu 0 → RuntimeError.
    - Pagina até reunir ``max_resultados`` documentos (máx. _MAX_PAGINAS).
    """
    sess = get_cjf_session()
    try:
        html_resultado = sess.buscar(termo, tribunais)
    except Exception:
        sess.viewstate = None
        sess._fetched_at = 0.0
        html_resultado = sess.buscar(termo, tribunais)

    totais = extrair_totais(html_resultado)
    docs = extrair_documentos(html_resultado)

    # Zero NÃO se aceita de primeira (medido em 31/08/2026). O shard de um
    # tribunal pode ficar às escuras no portal: a resposta vem HTTP 200, sem erro
    # algum, dizendo "Total TRF2 0 Documento(s) encontrado(s)" — zero
    # indistinguível de ausência de jurisprudência. Naquela sessão o TRF2
    # respondeu 704 para `medicamento E União` e, dez minutos depois, passou a
    # devolver 0 para QUALQUER termo, inclusive `medicamento`, enquanto TRF1 e STJ
    # respondiam normais. O retry de cima não alcança este caso porque só dispara
    # em EXCEÇÃO, e 200-com-zero não levanta nada.
    #
    # Custa uma requisição a mais só quando o zero aparece, e converte o falso
    # zero em resultado ou em zero CONFIRMADO por repetição.
    if not docs and sum(totais.values()) == 0:
        time.sleep(_PAUSA_RECONFERIR_ZERO_S)
        sess.viewstate = None
        sess._fetched_at = 0.0
        try:
            html_2 = sess.buscar(termo, tribunais)
        except Exception:
            html_2 = None
        if html_2 is not None:
            totais_2 = extrair_totais(html_2)
            docs_2 = extrair_documentos(html_2)
            if docs_2 or sum(totais_2.values()) > 0:
                html_resultado, totais, docs = html_2, totais_2, docs_2

    # Canário estrutural: se o portal reporta documentos mas o parser extraiu
    # zero, o HTML/JSF do CJF provavelmente mudou (ex.: id autogerado do
    # checkbox, reordenação de <td>). Falha LOUD em vez de devolver vazio
    # silencioso, que mascararia um parser quebrado.
    total_portal = sum(totais.values())
    if total_portal > 0 and not docs:
        raise RuntimeError(
            f"CJF reportou {total_portal} documento(s) mas o parser extraiu 0 "
            "— provável mudança no HTML do portal. Verificar regex de "
            "shared/cjf_client.extrair_documentos antes de confiar em "
            "'nada encontrado'."
        )

    # Paginação best-effort: o datatable devolve 10 por página; busca páginas
    # adicionais só quando o chamador pediu mais do que a primeira página tem.
    pagina = 1
    vistos = {(d.get("numero"), d.get("ementa")) for d in docs}
    while len(docs) < max_resultados and len(docs) < total_portal and pagina < _MAX_PAGINAS:
        try:
            html_extra = sess.buscar_pagina(first=pagina * _DOCS_POR_PAGINA)
        except Exception:
            break
        novos = [
            d for d in extrair_documentos(html_extra)
            if (d.get("numero"), d.get("ementa")) not in vistos
        ]
        if not novos:
            break
        for d in novos:
            vistos.add((d.get("numero"), d.get("ementa")))
        docs.extend(novos)
        pagina += 1

    return docs[:max_resultados], totais
