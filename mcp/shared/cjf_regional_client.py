"""Cliente do portal CJF REGIONAL da 1ª Região (JSF/PrimeFaces).

Base: https://jurisprudencia.cjf.jus.br/trf1/index.xhtml

POR QUE EXISTE, SE JÁ HÁ ``cjf_client``
--------------------------------------
A base UNIFICADA do CJF (``cjf_client``) cobre STF, STJ e os seis TRFs — mas
**apenas o tribunal**. Ela NÃO alcança as turmas recursais dos Juizados. Medido
em 2026-08-11 com a mesma query: a unificada devolve 4.966 documentos em TRF1 e
zero de turma recursal; a regional devolve os mesmos 4.966 na fonte ``TRF1`` e
mais 4.384 na fonte ``JEF1``, todos de turma recursal. O acervo do JEF1 é, hoje,
inalcançável por qualquer outro MCP do projeto.

Consequência prática: a fonte ``TRF1`` desta base é REDUNDANTE com
``cjf-jurisprudencia`` (mesmo acervo, e lá com cache, relaxamento e paginação
maduros). O ganho exclusivo está em ``JEF1``.

ALCANCE — SÓ A 1ª REGIÃO
------------------------
Não há base regional equivalente para as demais regiões: em 2026-08-11,
``/trf2`` a ``/trf6``, ``/jef``, ``/jef1``, ``/regional`` e ``/turmas`` no
mesmo host respondem 404. O CJF publica exatamente duas bases — ``unificada`` e
``trf1``. Estender este cliente a outra região exigiria outro portal, de outro
sistema; não é questão de trocar a URL.

ARMADILHAS DO PORTAL REGIONAL (todas tratadas aqui)
---------------------------------------------------
1. **Caracteres proibidos.** O portal REJEITA a query que contenha
   ``# ! + ' ; _ | -`` e devolve mensagem de erro JSF com resultado VAZIO. O
   hífen é o caso que mais dói: ``"auxílio-doença"`` é query inválida, e o
   servidor original devolvia "nenhum documento" — falso zero indistinguível de
   busca legítima sem resultado. ``sanitizar_query`` remove os proibidos ANTES
   do POST e reporta o que mudou.
2. **Erro JSF engolido.** ``detectar_erro_portal`` lê o bloco
   ``ui-messages-error-detail``; o chamador levanta em vez de devolver vazio.
3. **id dinâmico da fonte.** O ``name`` do grupo de checkboxes (ex.:
   ``formulario:j_idt62``) é autogerado pelo JSF e muda a cada deploy do
   portal — extraído do HTML a cada carga, ancorado em ``value="TRF1"``.
4. **Célula "Número" com ``<br/>``.** Traz o CNJ e a versão só-dígitos. Um
   padrão ``[^<]+`` deslizava para a célula seguinte e gravava a Classe no
   lugar do número.
5. **"Origem" não discrimina.** A coluna Origem vale "TRF - PRIMEIRA REGIÃO"
   para as DUAS fontes — não serve para saber se o julgado é do tribunal ou de
   turma recursal. A fonte é inferida do órgão julgador (``RECURSAL``).
6. **Totais por tipo não existem no HTML regional.** Os rótulos "Acórdãos",
   "Súmulas" etc. não aparecem; o total vem da linha de paginação.

Origem: adaptado do ``julia-pesquisa-mcp`` (kit-helio, MIT) via o pacote
``trf1-jurisprudencia-mcp``, e reescrito para as salvaguardas deste projeto
(ViewState com TTL, canário estrutural, paginação) na linha do ``cjf_client``.
"""

from __future__ import annotations

import html
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

TRF1_URL = "https://jurisprudencia.cjf.jus.br/trf1/index.xhtml"

TRF1_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/xml, text/xml, */*; q=0.01",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Faces-Request": "partial/ajax",
    "X-Requested-With": "XMLHttpRequest",
}

FONTES = {
    "TRF1": "Tribunal Regional Federal da 1ª Região",
    "JEF1": "Turmas Recursais dos Juizados Especiais Federais da 1ª Região",
}

TIPOS_DOCUMENTO = {
    "ACORDAO": "Acórdãos",
    "SUMULA": "Súmulas",
    "ARGUICAO": "Arguições de inconstitucionalidade",
    "DECISAOMONO": "Decisões monocráticas",
}

# JSF do CJF mantém ViewState por ~30 min; renovar com margem de 5 min.
_VIEWSTATE_TTL_S = 25 * 60

# Valor observado em 2026-08-11 — usado se a descoberta dinâmica falhar.
_CHECKBOX_FONTE_FALLBACK = "formulario:j_idt62"

# O datatable regional devolve 30 por página (a unificada, 10).
_DOCS_POR_PAGINA = 30
_MAX_PAGINAS = 5

# Caracteres que o portal regional recusa. A própria mensagem de erro os lista:
# "Os seguintes textos são inválidos para pesquisa: #  !  +  '  ;  _  |  -".
_PROIBIDOS = "#!+';_|-"
_RE_PROIBIDOS = re.compile(f"[{re.escape(_PROIBIDOS)}]")


def sanitizar_query(busca: str) -> Tuple[str, List[str]]:
    """Remove os caracteres que o portal regional recusa.

    Devolve ``(query_saneada, caracteres_removidos)``. Cada proibido vira um
    espaço — ``"auxílio-doença"`` vira ``"auxílio doença"``, que o portal aceita
    e que casa o mesmo acervo (17.362 documentos, medido em 2026-08-11).

    O chamador deve reportar ``caracteres_removidos`` ao usuário: a query que
    chega ao portal não é a que ele escreveu.
    """
    removidos = sorted(set(_RE_PROIBIDOS.findall(busca)))
    if not removidos:
        return busca, []
    saneada = _RE_PROIBIDOS.sub(" ", busca)
    saneada = re.sub(r"\s+", " ", saneada).strip()
    return saneada, removidos


def detectar_erro_portal(html_content: str) -> Optional[str]:
    """Extrai a mensagem de erro do JSF, se houver.

    Sem isto, um erro do portal chega ao chamador como zero documentos — o
    falso zero que faz uma tese parecer não ter precedente.
    """
    m = re.search(r'ui-messages-error-detail">([^<]*)<', html_content)
    if not m:
        return None
    detalhe = html.unescape(m.group(1)).replace("\\n", " ").strip()
    return re.sub(r"\s+", " ", detalhe) or "erro não detalhado pelo portal"


class TRF1Session:
    """Sessão com o portal regional (requests.Session + ViewState com TTL)."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": TRF1_HEADERS["User-Agent"],
            "Accept-Language": TRF1_HEADERS["Accept-Language"],
        })
        self.viewstate: Optional[str] = None
        self._fetched_at: float = 0.0
        self.checkbox_fonte: str = _CHECKBOX_FONTE_FALLBACK

    @property
    def viewstate_is_fresh(self) -> bool:
        return bool(self.viewstate) and (time.monotonic() - self._fetched_at) < _VIEWSTATE_TTL_S

    @retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
    def obter_viewstate(self) -> str:
        resp = self.session.get(TRF1_URL, timeout=30)
        resp.raise_for_status()

        self._descobrir_checkbox_fonte(resp.text)

        for pat in (
            r'name="javax\.faces\.ViewState"[^>]*value="([^"]+)"',
            r'ViewState:([^"]+)"',
        ):
            m = re.search(pat, resp.text)
            if m:
                self.viewstate = m.group(1)
                self._fetched_at = time.monotonic()
                return self.viewstate

        raise ValueError("ViewState não encontrado na página regional do TRF1")

    def _descobrir_checkbox_fonte(self, html_inicial: str) -> None:
        """Descobre o ``name`` do grupo de checkboxes TRF1/JEF1.

        O id ``j_idt62`` é autogerado pelo JSF. Ancorar em ``value="TRF1"``,
        que é estável (o código da fonte não muda).
        """
        for pat in (
            r'name="(formulario:[\w:.-]+)"[^>]*\bvalue="(?:TRF1|JEF1)"',
            r'\bvalue="(?:TRF1|JEF1)"[^>]*name="(formulario:[\w:.-]+)"',
        ):
            m = re.search(pat, html_inicial)
            if m:
                self.checkbox_fonte = m.group(1)
                return

    @retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
    def buscar(self, termo: str, fontes: List[str], tipos: List[str]) -> str:
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
        for tipo in tipos:
            form_data.append(("formulario:selectTiposDocumento", tipo))
        for fonte in fontes:
            form_data.append((self.checkbox_fonte, fonte))
        form_data.append(("javax.faces.ViewState", self.viewstate))

        resp = self.session.post(TRF1_URL, data=form_data, headers=TRF1_HEADERS, timeout=60)
        resp.raise_for_status()
        return resp.text

    def buscar_pagina(self, first: int, rows: int = _DOCS_POR_PAGINA) -> str:
        """Pagina o datatable via AJAX PrimeFaces. Reusa o ViewState corrente."""
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
        resp = self.session.post(TRF1_URL, data=form_data, headers=TRF1_HEADERS, timeout=60)
        resp.raise_for_status()
        return resp.text


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def extrair_paginacao(html_content: str) -> Dict[str, Any]:
    """Lê a linha "Exibindo 1 - 30 de 4.384, Página: 1/147".

    É a única fonte de total do HTML regional — os rótulos por tipo de
    documento da base unificada não existem aqui.
    """
    content = html.unescape(html_content)
    m = re.search(
        r"Exibindo\s*(\d+)\s*-\s*(\d+)\s*de\s*([\d.]+),\s*Página:\s*(\d+)/(\d+)",
        content,
    )
    if not m:
        return {}
    return {
        "de": int(m.group(1)),
        "ate": int(m.group(2)),
        "total": int(m.group(3).replace(".", "")),
        "pagina": int(m.group(4)),
        "paginas": int(m.group(5)),
    }


def _inferir_fonte(doc: Dict[str, Any]) -> str:
    """TRF1 ou JEF1, a partir do órgão julgador.

    A coluna "Origem" do portal vale "TRF - PRIMEIRA REGIÃO" nas duas fontes e
    não serve para discriminar. Importa distinguir: turma recursal é precedente
    de persuasão limitada, e a peça precisa dizer o que está citando.
    """
    orgao = (doc.get("orgao_julgador") or "").upper()
    return "JEF1" if "RECURSAL" in orgao else "TRF1"


def extrair_documentos(html_content: str) -> List[Dict[str, Any]]:
    """Extrai os documentos do bloco de resultado do portal regional."""
    documentos: List[Dict[str, Any]] = []
    content = html.unescape(html_content)
    cdata_matches = re.findall(r"<!\[CDATA\[(.*?)\]\]>", content, re.DOTALL)
    if cdata_matches:
        content = "".join(cdata_matches)

    doc_indices = set(re.findall(r"tabelaDocumentos:(\d+):", content))

    campos = [
        ("tipo", "Tipo"),
        ("numero", "Número"),
        ("classe", "Classe"),
        ("relator", r"Relator\(a\)"),
        ("origem", "Origem"),
        ("orgao_julgador", "Órgão julgador"),
        ("data_julgamento", "Data"),
        ("data_publicacao", "Data da publicação"),
        ("fonte_publicacao", "Fonte da publicação"),
    ]

    for idx in sorted(doc_indices, key=int):
        doc: Dict[str, Any] = {"indice": int(idx)}

        for campo, label in campos:
            # A célula pode conter marcação interna — "Número" vem como
            # "<td>1029564-39.2023.4.01.3200<br/> 10295643920234013200</td>".
            # Um padrão [^<]+ falharia e deslizaria para a célula SEGUINTE,
            # gravando a Classe no lugar do número.
            pattern = (
                rf"tabelaDocumentos:{idx}:.*?label_pontilhada[^>]*>{label}</span>"
                rf".*?<td[^>]*>(.*?)</td>"
            )
            match = re.search(pattern, content, re.DOTALL)
            if not match:
                continue
            valor = re.sub(r"<br\s*/?>", "\n", match.group(1))
            valor = re.sub(r"<[^>]+>", " ", valor)
            linhas_valor = [l.strip() for l in valor.split("\n") if l.strip()]
            if not linhas_valor:
                continue
            if campo == "numero":
                cnj = next(
                    (l for l in linhas_valor if re.match(r"^\d{7}-\d{2}\.\d{4}\.", l)),
                    None,
                )
                doc[campo] = cnj or linhas_valor[0]
            else:
                doc[campo] = re.sub(r"\s+", " ", " ".join(linhas_valor)).strip()

        decisao_match = re.search(
            rf"tabelaDocumentos:{idx}:.*?label_pontilhada[^>]*>Decisão</span>"
            rf".*?<td[^>]*>\s*([^<]+(?:<[^>]+>[^<]*</[^>]+>)*[^<]*?)\s*</td>",
            content,
            re.DOTALL,
        )
        if decisao_match:
            decisao = re.sub(r"<[^>]+>", "", decisao_match.group(1)).strip()
            doc["decisao"] = re.sub(r"\s+", " ", decisao)

        documentos.append(doc)

    ementa_pattern = re.compile(r'painel_ementa-([^"]+)"[^>]*>(.*?)</div>', re.DOTALL)
    ementas = []
    for match in ementa_pattern.finditer(content):
        ementa = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        ementa = re.sub(r"\s+", " ", ementa)
        if len(ementa) > 50:
            ementas.append(ementa)

    for i, doc in enumerate(documentos):
        if i < len(ementas):
            doc["ementa"] = ementas[i]

    documentos = [d for d in documentos if d.get("numero") or d.get("ementa")]
    for d in documentos:
        d["fonte"] = _inferir_fonte(d)
    return documentos


# ---------------------------------------------------------------------------
# Singleton com TTL (mesma política do cjf_client)
# ---------------------------------------------------------------------------

_TRF1_SHARED: Optional[TRF1Session] = None


def get_trf1_session() -> TRF1Session:
    global _TRF1_SHARED
    if _TRF1_SHARED is None:
        _TRF1_SHARED = TRF1Session()
        return _TRF1_SHARED
    if _TRF1_SHARED._fetched_at and (time.monotonic() - _TRF1_SHARED._fetched_at) > 3600:
        try:
            _TRF1_SHARED.session.close()
        except Exception:
            pass
        _TRF1_SHARED = TRF1Session()
    return _TRF1_SHARED


# ---------------------------------------------------------------------------
# API de alto nível
# ---------------------------------------------------------------------------

def normalizar_fontes(fontes: str) -> List[str]:
    lista = [f.strip().upper() for f in str(fontes).split(",") if f.strip()]
    invalidas = [f for f in lista if f not in FONTES]
    if invalidas:
        raise ValueError(
            f"Fonte(s) inválida(s): {', '.join(invalidas)}. Use TRF1 e/ou JEF1."
        )
    return lista or ["TRF1", "JEF1"]


def normalizar_tipos(tipos: str) -> List[str]:
    if not tipos or str(tipos).strip().lower() in ("todos", "todas", "all", "*"):
        return list(TIPOS_DOCUMENTO)
    lista = [t.strip().upper() for t in str(tipos).split(",") if t.strip()]
    invalidos = [t for t in lista if t not in TIPOS_DOCUMENTO]
    if invalidos:
        raise ValueError(
            f"Tipo(s) inválido(s): {', '.join(invalidos)}. "
            f"Use: {', '.join(TIPOS_DOCUMENTO)} — ou 'todos'."
        )
    return lista


def _buscar_uma_fonte(
    termo: str,
    fonte: str,
    tipos: List[str],
    max_resultados: int,
) -> Tuple[List[Dict[str, Any]], int]:
    """Busca em UMA fonte e devolve ``(documentos, total_reportado)``."""
    sess = get_trf1_session()
    fontes = [fonte]
    try:
        html_resultado = sess.buscar(termo, fontes, tipos)
    except Exception:
        sess.viewstate = None
        sess._fetched_at = 0.0
        html_resultado = sess.buscar(termo, fontes, tipos)

    erro = detectar_erro_portal(html_resultado)
    if erro:
        raise RuntimeError(
            f"Portal regional do TRF1 recusou a busca: {erro} — "
            f"query enviada: {termo!r}. NÃO tratar como 'nada encontrado'."
        )

    paginacao = extrair_paginacao(html_resultado)
    docs = extrair_documentos(html_resultado)
    total_portal = int(paginacao.get("total", 0))

    # Canário estrutural: total > 0 e parser vazio significa HTML mudado —
    # falha LOUD, porque vazio silencioso é indistinguível de "não há
    # precedente" e é assim que uma tese boa é abandonada por engano.
    if total_portal > 0 and not docs:
        raise RuntimeError(
            f"Portal regional reportou {total_portal} documento(s) mas o parser "
            "extraiu 0 — provável mudança no HTML. Verificar as regex de "
            "shared/cjf_regional_client.extrair_documentos antes de confiar em "
            "'nada encontrado'."
        )

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

    return docs[:max_resultados], total_portal


def buscar_documentos(
    busca: str,
    fontes: List[str],
    tipos: List[str],
    max_resultados: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Busca na base regional e devolve ``(documentos, meta)``.

    ``meta`` traz ``total`` (soma do reportado pelo portal), ``por_fonte``
    (total de cada fonte) e ``sanitizada`` (caracteres proibidos removidos da
    query, se houve).

    UMA CONSULTA POR FONTE, e não uma consulta com as duas marcadas. Motivo
    medido em 2026-08-11: quando as duas vão no mesmo POST, o portal ordena o
    JEF1 inteiro antes do TRF1 — pedir 60 documentos devolvia 60 de turma
    recursal e zero do tribunal, embora o total somasse os dois acervos. O
    default ``TRF1,JEF1`` estaria prometendo o que não entregava. Consultando
    separado e intercalando, cada fonte aparece na proporção pedida.

    Salvaguardas, na ordem em que atuam:
      1. saneamento da query (caracteres que o portal recusa);
      2. erro JSF vira ``RuntimeError`` — nunca lista vazia silenciosa;
      3. canário estrutural: portal reporta total > 0 e o parser extrai 0;
      4. paginação até reunir a cota da fonte (teto ``_MAX_PAGINAS``).
    """
    termo, removidos = sanitizar_query(busca)
    if not termo:
        raise ValueError(
            "Query vazia após remover os caracteres que o portal regional "
            f"recusa ({' '.join(_PROIBIDOS)})."
        )

    # Cota por fonte, arredondada para cima — com duas fontes e 10 pedidos,
    # 5 de cada; se uma render menos, a outra preenche na intercalação.
    cota = -(-max_resultados // len(fontes))

    por_fonte: Dict[str, int] = {}
    colhidos: List[List[Dict[str, Any]]] = []
    for fonte in fontes:
        docs_f, total_f = _buscar_uma_fonte(termo, fonte, tipos, cota)
        por_fonte[fonte] = total_f
        colhidos.append(docs_f)

    # Intercala para que o corte em max_resultados não elimine uma fonte
    # inteira: TRF1[0], JEF1[0], TRF1[1], JEF1[1], ...
    documentos: List[Dict[str, Any]] = []
    vistos = set()
    for i in range(max(len(c) for c in colhidos) if colhidos else 0):
        for c in colhidos:
            if i < len(c):
                chave = (c[i].get("numero"), c[i].get("ementa"))
                if chave not in vistos:
                    vistos.add(chave)
                    documentos.append(c[i])

    meta: Dict[str, Any] = {
        "total": sum(por_fonte.values()),
        "por_fonte": por_fonte,
        "sanitizada": removidos,
        "query_enviada": termo,
    }
    return documentos[:max_resultados], meta
