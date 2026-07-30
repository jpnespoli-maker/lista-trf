"""
MCP Server: STJ Jurisprudência

ACÓRDÃOS (ACOR)   → CJF Unificada com filtro tribunais=STJ
                    (https://jurisprudencia.cjf.jus.br/unificada/index.xhtml)
SÚMULAS (SUMU) e
INFORMATIVOS (INFJ) → SCON STJ via curl_cffi (https://scon.stj.jus.br/SCON/)

O SCON migrou para Cloudflare com challenge JS (Turnstile) que bloqueia acesso
programático. Até 2026-07-30 este servidor ainda o tentava primeiro em toda
chamada e só então caía no CJF — a telemetria do período mostrou 359 de 366
chamadas terminando no fallback, ao custo de ~44 s cada (268 min). Desde então
a precedência é invertida para ACOR: o CJF responde direto (~1,3 s) e o SCON
vira fallback. Em SUMU/INFJ, que o CJF não cobre, o SCON segue primeiro, com
pedágio curto (1 tentativa, 8 s); bloqueado, a via é a escada do projeto
(Playwright → desafio resolvido pelo Defensor).

A API pública continua sendo `buscar_jurisprudencia_stj(query, base, tamanho)`,
agora com `forcar_scon` para pedir o SCON explicitamente em ACOR. O comentário
inicial da resposta declara qual fonte respondeu de fato.
"""

from mcp.server.fastmcp import FastMCP
from bs4 import BeautifulSoup
import re
import time
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
    sanitizar_comentario_xml,
)
from shared import cjf_client

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

# Pedágio do SCON (auditoria 2026-07-30). O SCON está atrás de Cloudflare desde
# a migração e, na telemetria de 23/05 a 30/07 (DPU/logs/jurisprudencia_queries.jsonl),
# 359 de 366 chamadas terminaram no fallback CJF — depois de gastar ~44 s por
# chamada (2 tentativas × timeout 20 s + backoff), 268 min no período. Como o
# CJF cobre acórdãos do STJ e responde em ~1,3 s, ACOR passa a ir direto ao CJF
# (ver `buscar_jurisprudencia_stj`) e o SCON só é tentado onde é insubstituível
# (SUMU/INFJ) ou por pedido explícito — aí com pedágio curto.
_SCON_TENTATIVAS = 1
_SCON_TIMEOUT_S = 8

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
# Sessão, parsers, paginação e canário estrutural vivem em shared/cjf_client.py
# (compartilhado com o cjf-jurisprudencia desde 2026-06-10).


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


@retry(wait=wait_exponential(multiplier=1, min=2, max=6),
       stop=stop_after_attempt(_SCON_TENTATIVAS), reraise=True)
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
    resp = session.get(SCON_PESQUISAR, params=params, timeout=_SCON_TIMEOUT_S)
    resp.raise_for_status()
    text = resp.text
    # Sentinelas de Cloudflare/erro silencioso
    if "Verificação automática" in text or "cf-mitigated" in text.lower():
        raise RuntimeError("Cloudflare challenge ativo no SCON")
    return text


def _parse_scon_html(
    html_str: str, base: str, max_tokens_ementa: int = 400
) -> Tuple[List[BaseResultadoJuridico], int]:
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
        ementa = truncar_por_tokens(ementa, max_tokens=max_tokens_ementa)

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

def _buscar_via_cjf(
    query_brs: str, tamanho: int, max_tokens_ementa: int = 400
) -> Tuple[List[BaseResultadoJuridico], int]:
    query_cjf = _brs_para_cjf(query_brs)
    # Cliente compartilhado: sessão singleton, refresh de ViewState, paginação
    # e canário estrutural (total > 0 e parser extraiu 0 → RuntimeError LOUD).
    docs, totais = cjf_client.buscar_documentos(query_cjf, ["STJ"], tamanho)
    total_stj = totais.get("STJ", 0)
    resultados: List[BaseResultadoJuridico] = []
    for d in docs:
        ementa = truncar_por_tokens(d.get("ementa", ""), max_tokens=max_tokens_ementa)
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
# Rotas — cada uma devolve (saida_xml, n_resultados) ou levanta exceção
# ---------------------------------------------------------------------------

def _rota_scon(
    query: str, base: str, tamanho: int, max_tokens_ementa: int
) -> Tuple[str, int]:
    sess = _criar_session_scon()
    html_str = _pesquisar_scon(sess, query, base)
    parseados, total = _parse_scon_html(html_str, base, max_tokens_ementa)
    # Canário estrutural: a página indica total > 0 mas o parser
    # (seletores .itemlistadocumentos) extraiu 0 itens → layout do SCON
    # provavelmente mudou. Falha LOUD em vez de devolver vazio silencioso.
    if total > 0 and not parseados:
        raise RuntimeError(
            f"SCON reportou {total} documento(s) mas o parser extraiu 0 "
            "— provável mudança no HTML do portal. Verificar "
            "_parse_scon_html (.itemlistadocumentos/.numDocs)."
        )
    resultados = parseados[:tamanho]
    meta = (
        f'<!-- STJ/SCON | Base: {BASES_VALIDAS[base]} '
        f'| Total encontrado: {total} | Exibindo: {len(resultados)} -->\n'
    )
    return meta + formatar_resultados_xml(resultados, tag_raiz="resultados"), len(resultados)


def _rota_cjf(
    query: str, tamanho: int, max_tokens_ementa: int, *, apos_falha_scon: str = ""
) -> Tuple[str, int]:
    resultados, total = _buscar_via_cjf(query, tamanho, max_tokens_ementa)
    origem = (
        f"fallback após falha do SCON: {apos_falha_scon}"
        if apos_falha_scon
        else "rota primária para acórdãos"
    )
    meta = (
        f'<!-- STJ via CJF Unificada ({sanitizar_comentario_xml(origem)}) '
        f'| Total STJ: {total} | Exibindo: {len(resultados)} -->\n'
    )
    return meta + formatar_resultados_xml(resultados, tag_raiz="resultados"), len(resultados)


# ---------------------------------------------------------------------------
# Tool pública
# ---------------------------------------------------------------------------

@mcp.tool()
def buscar_jurisprudencia_stj(
    query: str,
    base: str = "ACOR",
    tamanho: int = 10,
    max_tokens_ementa: int = 400,
    forcar_scon: bool = False,
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
        max_tokens_ementa: Truncamento da ementa em tokens (50-4000). Default: 400.
                           Aumente para obter ementa mais longa/íntegra.
        forcar_scon: Tenta o SCON primeiro mesmo em ACOR. Default False — o SCON
                     está sob Cloudflare e falha em ~98% das chamadas, então
                     acórdãos vão direto ao CJF Unificada. Use quando quiser
                     especificamente a ficha do SCON e aceitar a espera.

    Returns:
        XML estruturado com os resultados encontrados.
        O comentário inicial declara qual fonte respondeu de fato.

    Notas:
        ACOR (acórdãos) é servido pelo CJF Unificada com filtro tribunais=STJ,
        que responde em ~1,3 s; o SCON entra como fallback se o CJF falhar.
        SUMU e INFJ existem apenas no SCON — ali ele é tentado primeiro, com
        pedágio curto (1 tentativa, 8 s). Bloqueado o SCON, a via para súmula
        e informativo é a escada documentada no projeto (Playwright e, se o
        desafio do Cloudflare aparecer, resolução manual pelo Defensor).
    """
    tamanho = max(1, min(tamanho, 40))
    max_tokens_ementa = max(50, min(int(max_tokens_ementa), 4000))
    base = base.upper().strip()
    if base not in BASES_VALIDAS:
        base = "ACOR"

    t0 = time.perf_counter()
    cache_hit = False
    n_results = 0
    erro_final: Optional[str] = None
    # `scon_primeiro`: o SCON é insubstituível em SUMU/INFJ (o CJF não cobre
    # súmula nem informativo) e opcional em ACOR. Fora desses casos ele entra
    # como fallback, não como pedágio de entrada.
    scon_primeiro = base != "ACOR" or forcar_scon
    rota = "scon" if scon_primeiro else "cjf"
    try:
        # A1 — cache HTTP. Chave inclui base (e o truncamento, pois o XML
        # cacheado já está truncado) e a preferência de rota, que muda a fonte.
        cache_key = {
            "mcp": "stj-jurisprudencia", "query": query, "base": base,
            "tamanho": tamanho, "max_tokens_ementa": max_tokens_ementa,
            "forcar_scon": forcar_scon,
        }
        cached = cached_http("stj-jurisprudencia", cache_key)
        if cached is not None:
            cache_hit = True
            import json as _json
            payload = _json.loads(cached)
            n_results = payload.get("n", 0)
            return payload["xml"]

        def _guardar(saida: str, n: int) -> str:
            try:
                import json as _json
                registrar_dispositivo(
                    "stj-jurisprudencia", cache_key,
                    _json.dumps({"xml": saida, "n": n}), ttl_s=_STJ_TTL_S,
                )
            except Exception:
                pass
            return saida

        erros = []

        # 1) Rota primária.
        try:
            if scon_primeiro:
                saida, n_results = _rota_scon(query, base, tamanho, max_tokens_ementa)
            else:
                saida, n_results = _rota_cjf(query, tamanho, max_tokens_ementa)
            return _guardar(saida, n_results)
        except Exception as e:
            rotulo = "SCON" if scon_primeiro else "CJF"
            erros.append(f"{rotulo}: {type(e).__name__}: {str(e)[:160]}")

        # 2) Fallback pela outra rota. O CJF não cobre súmula/informativo, então
        #    quando a primária era SCON por causa da base não há para onde ir.
        if scon_primeiro and base != "ACOR":
            erro_final = " | ".join(erros)
            return (
                f'<erro>Base {base} disponível apenas no SCON, que está indisponível. '
                f'Detalhes: {erro_final}</erro>'
            )

        try:
            if scon_primeiro:
                rota = "cjf-fallback"
                saida, n_results = _rota_cjf(
                    query, tamanho, max_tokens_ementa, apos_falha_scon=erros[0]
                )
            else:
                rota = "scon-fallback"
                saida, n_results = _rota_scon(query, base, tamanho, max_tokens_ementa)
            return _guardar(saida, n_results)
        except Exception as e:
            rotulo = "CJF" if scon_primeiro else "SCON"
            erros.append(f"{rotulo}: {type(e).__name__}: {str(e)[:160]}")
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
  ACOR vai direto ao CJF Unificada com filtro STJ (~1,3 s) — operadores BRS
  são convertidos para a sintaxe CJF (e→E, ou→OU, nao→NAO etc.). O SCON, que
  está sob Cloudflare e falhava em ~98% das chamadas, entra só como fallback
  se o CJF falhar; para tentá-lo primeiro use forcar_scon=True.
  SUMU e INFJ só existem no SCON e vão a ele primeiro, com 1 tentativa e 8 s.
  A resposta indica em comentário qual fonte respondeu de fato.
"""


if __name__ == "__main__":
    mcp.run()
