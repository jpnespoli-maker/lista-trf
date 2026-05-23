"""
MCP Server: STJ Jurisprudência

PRIMÁRIO: SCON STJ via curl_cffi (https://scon.stj.jus.br/SCON/)
FALLBACK: CJF Unificada com filtro tribunais=STJ
          (https://jurisprudencia.cjf.jus.br/unificada/index.xhtml)

O SCON migrou para Cloudflare com challenge JS (Turnstile) que bloqueia
acesso programático. Enquanto isso, este servidor delega ao CJF Unificada,
que cobre acórdãos do STJ na mesma base. A API pública continua sendo
`buscar_jurisprudencia_stj(query, base, tamanho)`; a resposta indica
qual fonte respondeu.
"""

from mcp.server.fastmcp import FastMCP
from bs4 import BeautifulSoup
import requests
import re
import time
import html as htmlmod
from typing import List, Tuple, Any, Optional
from tenacity import retry, wait_exponential, stop_after_attempt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.base_juridica import (
    BaseResultadoJuridico,
    formatar_resultados_xml,
    truncar_por_tokens,
    limpar_texto_html,
)

# Onda 2 — cache HTTP (7d para STJ).
try:
    from shared.cache_http import cached_http, registrar_dispositivo  # type: ignore
except ImportError:
    def cached_http(*_a, **_kw):  # type: ignore
        return None

    def registrar_dispositivo(*_a, **_kw) -> None:  # type: ignore
        pass

# Onda 1 — logger.
try:
    _DPU_SCRIPTS = Path.home() / ".claude" / "DPU" / "Scripts"
    if str(_DPU_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_DPU_SCRIPTS))
    from query_logger import log_query  # type: ignore
except ImportError:
    def log_query(**_kwargs: Any) -> None:  # type: ignore
        pass

try:
    from curl_cffi import requests as curl_requests
    _HAS_CURL_CFFI = True
except ImportError:
    import requests as curl_requests  # noqa: F401
    _HAS_CURL_CFFI = False

mcp = FastMCP("stj-jurisprudencia")
_STJ_TTL_S = 7 * 86400
_VIEWSTATE_TTL_S = 25 * 60  # C3 — também aplicado ao fallback CJF aqui.

# --- SCON (primário) -------------------------------------------------------

SCON_BASE = "https://scon.stj.jus.br/SCON"
SCON_HOME = f"{SCON_BASE}/"
SCON_PESQUISAR = f"{SCON_BASE}/pesquisar.jsp"

SCON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": SCON_HOME,
}

BASES_VALIDAS = {
    "ACOR": "Acórdãos",
    "SUMU": "Súmulas",
    "INFJ": "Informativos",
}

# --- CJF Unificada (fallback) ----------------------------------------------

CJF_URL = "https://jurisprudencia.cjf.jus.br/unificada/index.xhtml"

CJF_HEADERS = {
    "User-Agent": SCON_HEADERS["User-Agent"],
    "Accept": "application/xml, text/xml, */*; q=0.01",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Faces-Request": "partial/ajax",
    "X-Requested-With": "XMLHttpRequest",
}


# ---------------------------------------------------------------------------
# Conversão de sintaxe BRS (STJ/SCON) → CJF (Unificada)
# ---------------------------------------------------------------------------

_BRS_OPERADORES = {"e", "ou", "nao", "não", "xou"}
_BRS_PROXIMIDADE = re.compile(r"^(adj|prox|com|mesmo)(\d*)$", re.IGNORECASE)


def _brs_para_cjf(query: str) -> str:
    """
    Converte query em sintaxe BRS (operadores minúsculos) para sintaxe CJF
    (operadores maiúsculos). Apenas operadores isolados são transformados;
    termos de pesquisa (mesmo coincidentes com palavras como "e") dentro de
    aspas permanecem intactos.
    """
    out = []
    in_quote = False
    buf = []

    def flush():
        if not buf:
            return
        token = "".join(buf)
        buf.clear()
        low = token.lower()
        m = _BRS_PROXIMIDADE.match(token)
        if low in _BRS_OPERADORES:
            out.append({"e": "E", "ou": "OU", "nao": "NAO", "não": "NAO", "xou": "XOU"}[low])
        elif m:
            out.append(m.group(1).upper() + (m.group(2) or ""))
        else:
            out.append(token)

    for ch in query:
        if ch == '"':
            flush()
            out.append(ch)
            in_quote = not in_quote
        elif in_quote:
            out.append(ch)
        elif ch.isspace():
            flush()
            out.append(ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


# ---------------------------------------------------------------------------
# SCON — tentativa primária (curl_cffi)
# ---------------------------------------------------------------------------

def _criar_session_scon():
    if _HAS_CURL_CFFI:
        s = curl_requests.Session(impersonate="chrome131")
    else:
        s = curl_requests.Session()
    s.headers.update(SCON_HEADERS)
    return s


@retry(wait=wait_exponential(multiplier=1, min=2, max=6), stop=stop_after_attempt(2), reraise=True)
def _pesquisar_scon(session, query: str, base: str) -> str:
    params = {
        "acao": "pesquisar",
        "novaConsulta": "true",
        "i": "1",
        "b": base,
        "livre": query,
        "tipo_visualizacao": "RESUMO",
        "p": "true",
        "O": "JT",
    }
    resp = session.get(SCON_PESQUISAR, params=params, timeout=20)
    resp.raise_for_status()
    text = resp.text
    # Sentinelas de Cloudflare/erro silencioso
    if "Verificação automática" in text or "cf-mitigated" in text.lower():
        raise RuntimeError("Cloudflare challenge ativo no SCON")
    return text


def _parse_scon_html(html_str: str, base: str) -> Tuple[List[BaseResultadoJuridico], int]:
    soup = BeautifulSoup(html_str, "html.parser")

    num_docs_el = soup.find(class_="numDocs")
    total = 0
    if num_docs_el:
        m = re.search(r"(\d[\d.]*)", num_docs_el.get_text())
        if m:
            total = int(m.group(1).replace(".", ""))

    resultados = []
    for item in soup.select(".itemlistadocumentos"):
        meta_div = item.select_one(".col-sm-3")
        meta_items = []
        if meta_div:
            for el in meta_div.find_all(["span", "div", "p", "label", "strong"]):
                txt = el.get_text(" ", strip=True)
                if txt and len(txt) > 1:
                    meta_items.append(txt)
            seen = set()
            meta_items = [t for t in meta_items if not (t in seen or seen.add(t))]

        numero = meta_items[0] if meta_items else ""
        tipo = meta_items[1] if len(meta_items) > 1 else BASES_VALIDAS.get(base, base)

        relator = next((m for m in meta_items if re.match(r"Ministr[oa]", m, re.I)), "")
        data = next((m for m in meta_items if re.match(r"DJe\s+\d{2}/\d{2}/\d{4}", m)), "")
        if not data:
            for m in meta_items:
                hit = re.search(r"\d{2}/\d{2}/\d{4}", m)
                if hit:
                    data = hit.group()
                    break

        ementa_div = item.select_one(".clsEmentaCompleta")
        ementa = limpar_texto_html(str(ementa_div)) if ementa_div else ""
        ementa = truncar_por_tokens(ementa, max_tokens=400)

        extra = {"base": base}
        repet_div = item.select_one(".indicaRepetitivo")
        if repet_div:
            t = repet_div.get_text(" ", strip=True)
            if t:
                extra["vinculante"] = t[:100]

        resultados.append(
            BaseResultadoJuridico(
                conteudo=ementa,
                fonte="STJ",
                tipo=tipo,
                orgao="Superior Tribunal de Justiça",
                numero=numero,
                relator=relator,
                data=data,
                extra=extra,
            )
        )

    return resultados, total


# ---------------------------------------------------------------------------
# CJF Unificada — fallback (filtra tribunais=STJ)
# ---------------------------------------------------------------------------

class _CJFSession:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": CJF_HEADERS["User-Agent"],
            "Accept-Language": CJF_HEADERS["Accept-Language"],
        })
        self.viewstate = None
        self._fetched_at: float = 0.0

    @property
    def viewstate_is_fresh(self) -> bool:
        return bool(self.viewstate) and (time.monotonic() - self._fetched_at) < _VIEWSTATE_TTL_S

    @retry(wait=wait_exponential(multiplier=1, min=2, max=6), stop=stop_after_attempt(3))
    def _obter_viewstate(self) -> str:
        resp = self.session.get(CJF_URL, timeout=30)
        resp.raise_for_status()
        m = re.search(r'name="javax\.faces\.ViewState"[^>]*value="([^"]+)"', resp.text)
        if m:
            self.viewstate = m.group(1)
            self._fetched_at = time.monotonic()
            return self.viewstate
        m = re.search(r'ViewState:([^"]+)"', resp.text)
        if m:
            self.viewstate = m.group(1)
            self._fetched_at = time.monotonic()
            return self.viewstate
        raise ValueError("ViewState não encontrado")

    @retry(wait=wait_exponential(multiplier=1, min=2, max=8), stop=stop_after_attempt(3))
    def buscar(self, termo: str) -> str:
        if not self.viewstate_is_fresh:
            self._obter_viewstate()
        form = [
            ("javax.faces.partial.ajax", "true"),
            ("javax.faces.source", "formulario:actPesquisar"),
            ("javax.faces.partial.execute", "@all"),
            ("javax.faces.partial.render", "formulario:resultado"),
            ("formulario:actPesquisar", "formulario:actPesquisar"),
            ("formulario", "formulario"),
            ("formulario:textoLivre", termo),
            ("formulario:j_idt51", "STJ"),
            ("javax.faces.ViewState", self.viewstate),
        ]
        resp = self.session.post(CJF_URL, data=form, headers=CJF_HEADERS, timeout=60)
        resp.raise_for_status()
        return resp.text


def _extrair_cjf(html_content: str) -> List[dict]:
    content = htmlmod.unescape(html_content)
    cdata = re.findall(r"<!\[CDATA\[(.*?)\]\]>", content, re.DOTALL)
    if cdata:
        content = "".join(cdata)

    indices = sorted(set(re.findall(r"tabelaDocumentos:(\d+):", content)), key=int)
    docs = []
    campos = [
        ("numero", "Número"),
        ("classe", "Classe"),
        ("relator", r"Relator\(a\)"),
        ("orgao_julgador", "Órgão julgador"),
        ("data_julgamento", "Data"),
        ("data_publicacao", "Data da publicação"),
    ]
    for idx in indices:
        d = {"indice": int(idx)}
        for k, label in campos:
            pat = rf'tabelaDocumentos:{idx}:.*?label_pontilhada[^>]*>{label}</span>.*?<td[^>]*>([^<]+)</td>'
            m = re.search(pat, content, re.DOTALL)
            if m:
                d[k] = m.group(1).strip()
        docs.append(d)

    ementas = []
    for m in re.finditer(r'painel_ementa-([^"]+)"[^>]*>(.*?)</div>', content, re.DOTALL):
        e = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        e = re.sub(r"\s+", " ", e)
        if len(e) > 50:
            ementas.append(e)
    for i, d in enumerate(docs):
        if i < len(ementas):
            d["ementa"] = ementas[i]
    return [d for d in docs if d.get("numero") or d.get("ementa")]


_CJF_SHARED: Optional["_CJFSession"] = None


def _get_cjf_session_stj() -> "_CJFSession":
    """C3 — singleton de _CJFSession (com TTL embutido no ViewState)."""
    global _CJF_SHARED
    if _CJF_SHARED is None:
        _CJF_SHARED = _CJFSession()
        return _CJF_SHARED
    if _CJF_SHARED._fetched_at and (time.monotonic() - _CJF_SHARED._fetched_at) > 3600:
        try:
            _CJF_SHARED.session.close()
        except Exception:
            pass
        _CJF_SHARED = _CJFSession()
    return _CJF_SHARED


def _buscar_via_cjf(query_brs: str, tamanho: int) -> Tuple[List[BaseResultadoJuridico], int]:
    query_cjf = _brs_para_cjf(query_brs)
    sess = _get_cjf_session_stj()
    try:
        html_resp = sess.buscar(query_cjf)
    except Exception:
        # ViewState pode ter expirado no servidor — força refresh + retry único.
        sess.viewstate = None
        sess._fetched_at = 0.0
        html_resp = sess.buscar(query_cjf)

    # total específico do STJ na resposta CJF
    total_stj = 0
    m = re.search(r"STJ\s*</td>\s*<td[^>]*>.*?(\d+)\s*Documento", htmlmod.unescape(html_resp), re.DOTALL)
    if m:
        total_stj = int(m.group(1))

    docs = _extrair_cjf(html_resp)[:tamanho]
    resultados: List[BaseResultadoJuridico] = []
    for d in docs:
        ementa = truncar_por_tokens(d.get("ementa", ""), max_tokens=400)
        resultados.append(
            BaseResultadoJuridico(
                conteudo=ementa,
                fonte="STJ (via CJF Unificada)",
                tipo=d.get("classe", ""),
                orgao=d.get("orgao_julgador", "Superior Tribunal de Justiça"),
                numero=d.get("numero", ""),
                relator=d.get("relator", ""),
                data=d.get("data_julgamento", d.get("data_publicacao", "")),
                extra={"fallback": "cjf-unificada", "query_convertida": query_cjf},
            )
        )
    return resultados, total_stj


# ---------------------------------------------------------------------------
# Tool pública
# ---------------------------------------------------------------------------

@mcp.tool()
def buscar_jurisprudencia_stj(
    query: str,
    base: str = "ACOR",
    tamanho: int = 10,
) -> str:
    """
    Busca jurisprudência no Superior Tribunal de Justiça (STJ).
    Use ajuda_sintaxe_stj() para guia completo de operadores e exemplos.

    SINTAXE RÁPIDA (BRS — operadores em MINÚSCULO):
    termos simples (AND implícito), "e"/"ou"/"nao", adjacência com "adj5", truncamento com "$".

    BASES: ACOR (acórdãos, padrão) | SUMU (súmulas) | INFJ (informativos)

    Args:
        query: Termos de busca em sintaxe BRS (operadores minúsculos)
        base: Base de dados (ACOR | SUMU | INFJ)
        tamanho: Número de resultados por página (1–40, padrão 10)

    Returns:
        XML estruturado com os resultados encontrados.
        A resposta indica qual fonte respondeu (SCON ou CJF Unificada).

    Notas:
        Quando o SCON estiver sob Cloudflare Challenge ou indisponível,
        a busca é roteada automaticamente para a base CJF Unificada
        com filtro tribunais=STJ. Bases SUMU e INFJ só estão disponíveis
        no SCON; em fallback, retornam aviso de indisponibilidade.
    """
    tamanho = max(1, min(tamanho, 40))
    base = base.upper().strip()
    if base not in BASES_VALIDAS:
        base = "ACOR"

    t0 = time.perf_counter()
    cache_hit = False
    n_results = 0
    erro_final: Optional[str] = None
    rota = "scon"
    try:
        # A1 — cache HTTP. Chave inclui base.
        cache_key = {"mcp": "stj-jurisprudencia", "query": query, "base": base, "tamanho": tamanho}
        cached = cached_http("stj-jurisprudencia", cache_key)
        if cached is not None:
            cache_hit = True
            import json as _json
            payload = _json.loads(cached)
            n_results = payload.get("n", 0)
            return payload["xml"]

        erros = []

        # 1) Tenta SCON
        try:
            sess = _criar_session_scon()
            html_str = _pesquisar_scon(sess, query, base)
            resultados, total = _parse_scon_html(html_str, base)
            n_results = len(resultados)
            xml = formatar_resultados_xml(resultados, tag_raiz="resultados")
            meta = (
                f'<!-- STJ/SCON | Base: {BASES_VALIDAS[base]} '
                f'| Total encontrado: {total} | Exibindo: {len(resultados)} -->\n'
            )
            saida = meta + xml
            try:
                import json as _json
                registrar_dispositivo(
                    "stj-jurisprudencia", cache_key,
                    _json.dumps({"xml": saida, "n": n_results}), ttl_s=_STJ_TTL_S,
                )
            except Exception:
                pass
            return saida
        except Exception as e:
            erros.append(f"SCON: {type(e).__name__}: {str(e)[:160]}")

        # 2) Fallback CJF (apenas para ACOR; CJF não cobre súmulas/informativos)
        if base != "ACOR":
            erro_final = " | ".join(erros)
            return (
                f'<erro>Base {base} disponível apenas no SCON, que está indisponível. '
                f'Detalhes: {erro_final}</erro>'
            )

        rota = "cjf-fallback"
        try:
            resultados, total = _buscar_via_cjf(query, tamanho)
            n_results = len(resultados)
            xml = formatar_resultados_xml(resultados, tag_raiz="resultados")
            meta = (
                f'<!-- STJ via CJF Unificada (fallback) | Total STJ: {total} '
                f'| Exibindo: {len(resultados)} | SCON inacessível: {erros[0]} -->\n'
            )
            saida = meta + xml
            try:
                import json as _json
                registrar_dispositivo(
                    "stj-jurisprudencia", cache_key,
                    _json.dumps({"xml": saida, "n": n_results}), ttl_s=_STJ_TTL_S,
                )
            except Exception:
                pass
            return saida
        except Exception as e:
            erros.append(f"CJF: {type(e).__name__}: {str(e)[:160]}")
            erro_final = " | ".join(erros)
            return f'<erro>Falha em SCON e CJF. {erro_final}</erro>'
    finally:
        log_query(
            mcp="stj-jurisprudencia",
            tool="buscar_jurisprudencia_stj",
            query=query,
            filtros={"base": base, "tamanho": tamanho, "rota": rota},
            n_resultados=n_results,
            ms=int((time.perf_counter() - t0) * 1000),
            cache_hit=cache_hit,
            erro=erro_final,
        )


@mcp.tool()
def ajuda_sintaxe_stj() -> str:
    """
    Retorna guia completo de sintaxe, operadores e exemplos para buscar_jurisprudencia_stj.
    Consulte antes de formular queries complexas.
    """
    return """
SINTAXE STJ/SCON — BRS (Boolean Retrieval System)
IMPORTANTE: operadores em MINÚSCULO.

OPERADORES:
  AND implícito:  "BPC assistência social"       → todos os termos
  AND explícito:  "BPC e assistência"
  OR:             "BPC ou assistência"
  NOT:            "BPC nao administrativo"
  Adjacência:     "assistência adj5 social"       → dentro de 5 palavras
  Proximidade:    "assistência prox5 social"      → qualquer ordem
  Frase exata:    "benefício de prestação continuada"
  Truncamento:    "assist$"                       → qualquer terminação

BASES DISPONÍVEIS:
  ACOR — Acórdãos (padrão)
  SUMU — Súmulas
  INFJ — Informativos e outros produtos

EXEMPLOS:
  buscar_jurisprudencia_stj("BPC e assistência e deficiência")
  buscar_jurisprudencia_stj("FGTS e expurgos", base="ACOR", tamanho=5)
  buscar_jurisprudencia_stj("súmula", base="SUMU")
  buscar_jurisprudencia_stj("prisão adj2 civil e alimentos")
  buscar_jurisprudencia_stj("execução fiscal e prescrição$")

DICAS:
  - Para precedentes vinculantes: "tema repetitivo NNN"
  - Para recursos específicos: "REsp e FGTS"
  - Resultados ordenados por data de julgamento (mais recentes primeiro)

ROTEAMENTO INTERNO:
  Tenta SCON primeiro. Se inacessível (Cloudflare/timeout), faz fallback
  automático para CJF Unificada com filtro STJ — operadores BRS são
  convertidos para a sintaxe CJF (e→E, ou→OU, nao→NAO etc.).
  A resposta indica em comentário qual fonte respondeu.
  Bases SUMU e INFJ só existem no SCON.
"""


if __name__ == "__main__":
    mcp.run()
