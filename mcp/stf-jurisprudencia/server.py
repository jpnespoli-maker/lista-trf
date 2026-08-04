"""
MCP Server: STF Jurisprudência

Acesso à jurisprudência do Supremo Tribunal Federal.
Bases disponíveis: acordaos, sumulas, decisoes-monocraticas, informativos.

API: POST https://jurisprudencia.stf.jus.br/api/search/search?base=<base>

Estratégia de acesso:
  1. Tentativa rápida via `requests` puro (~1-2s quando não bloqueado).
  2. Em caso de bloqueio AWS WAF (HTTP 202 com corpo vazio, 403, etc.),
     fallback via Playwright stealth headless (~5-10s) — abre a SPA do
     STF, deixa o JS resolver o desafio WAF, e dispara a chamada à API
     já no contexto autorizado via `page.evaluate(fetch(...))`.
  3. Se ambos falharem, retorna XML de erro semântico com orientação
     para usar bnp-api, base local ou cjf-jurisprudencia (STJ/TRFs).
"""

from mcp.server.fastmcp import FastMCP
import requests
import json as _json
import time
from typing import Any
from tenacity import retry, wait_exponential, stop_after_attempt
import sys
from pathlib import Path
from playwright.async_api import async_playwright

try:
    from playwright_stealth import Stealth
    _HAS_STEALTH = True
except ImportError:
    _HAS_STEALTH = False

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared import tls_sistema

tls_sistema.aplicar()
from shared.base_juridica import (
    BaseResultadoJuridico,
    formatar_resultados_xml,
    truncar_por_tokens,
    limpar_texto_html,
)

# Onda 2 — cache HTTP (TTL 7d para STF — acórdãos com data fechada).
try:
    from shared.cache_http import cached_http, registrar_dispositivo  # type: ignore
except ImportError:
    def cached_http(*_a, **_kw):  # type: ignore
        return None

    def registrar_dispositivo(*_a, **_kw) -> None:  # type: ignore
        pass

# Onda 1 — logger estruturado.
try:
    _DPU_SCRIPTS = Path.home() / ".claude" / "DPU" / "Scripts"
    if str(_DPU_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_DPU_SCRIPTS))
    from query_logger import log_query  # type: ignore
except ImportError:
    def log_query(**_kwargs: Any) -> None:  # type: ignore
        pass

mcp = FastMCP("stf-jurisprudencia")

_STF_TTL_S = 7 * 86400

STF_ORIGIN = "https://jurisprudencia.stf.jus.br"
STF_SEARCH_PATH = "/api/search/search"
STF_SEARCH_URL = STF_ORIGIN + STF_SEARCH_PATH
STF_SPA_URL = STF_ORIGIN + "/pages/search"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json",
    "Origin": STF_ORIGIN,
    "Referer": STF_SPA_URL,
}

SOURCE_FIELDS = [
    "processo_codigo_completo",
    "processo_numero",
    "processo_classe_processual_unificada_extenso",
    "processo_classe_processual_unificada_classe_sigla",
    "relator_processo_nome",
    "julgamento_data",
    "publicacao_data",
    "decisao_texto",
    "documental_indexacao_texto",
    "documental_observacao_texto",
    "inteiro_teor_url",
    "base",
]

BASES_VALIDAS = {
    "acordaos": "Acórdãos",
    "sumulas": "Súmulas",
    "decisoes-monocraticas": "Decisões Monocráticas",
    "informativos": "Informativos",
}


def _payload(query: str, inicio: int, tamanho: int) -> dict:
    return {
        "query": {
            "query_string": {
                "query": query,
                "default_operator": "AND",
                "fields": [
                    "titulo",
                    "documental_indexacao_texto",
                    "decisao_texto",
                    "documental_observacao_texto",
                ],
            }
        },
        "from": inicio,
        "size": tamanho,
        "_source": SOURCE_FIELDS,
    }


# ---------------------------------------------------------------------------
# Tentativa direta via requests
# ---------------------------------------------------------------------------

class _STFWAFBlock(RuntimeError):
    """Resposta sugere bloqueio AWS WAF / Cloudflare (202 vazio, 403, HTML challenge)."""


def _eh_resposta_bloqueada(resp: requests.Response) -> bool:
    """
    Detecta bloqueio anti-bot do portal STF (atualmente AWS WAF, antes Cloudflare):
    HTTP 202 + Content-Type text/html + corpo vazio, ou 403 puro,
    ou corpo com sinais de challenge JS.
    """
    ct = (resp.headers.get("Content-Type") or "").lower()
    if "json" in ct and resp.status_code == 200:
        return False
    if resp.status_code in (202, 403):
        return True
    body = resp.text or ""
    if not body.strip():
        return True
    low = body[:2000].lower()
    sinais = (
        "cf-mitigated",
        "verifying you are human",
        "challenge-platform",
        "aws-waf-token",
        "captcha",
    )
    return any(s in low for s in sinais)


@retry(wait=wait_exponential(multiplier=1, min=1, max=2), stop=stop_after_attempt(2), reraise=True)
def _buscar_via_requests(query: str, base: str, tamanho: int) -> dict:
    session = requests.Session()
    session.headers.update(HEADERS)
    resp = session.post(
        f"{STF_SEARCH_URL}?base={base}",
        json=_payload(query, 0, tamanho),
        timeout=10,
    )
    if _eh_resposta_bloqueada(resp):
        raise _STFWAFBlock(
            f"WAF ativo (HTTP {resp.status_code}, CT={resp.headers.get('Content-Type','?')}, "
            f"len={len(resp.text)})"
        )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Fallback Playwright stealth
# ---------------------------------------------------------------------------

async def _buscar_via_playwright(query: str, base: str, tamanho: int) -> dict:
    """
    Abre a SPA do STF em Chromium stealth headless, deixa o JS do AWS WAF
    aplicar o aws-waf-token cookie e dispara a chamada à API via fetch()
    no contexto autorizado da própria origem. Retorna o JSON parseado.

    Custo típico: 5-10s (boot do browser + waf challenge + fetch).
    """
    body = _payload(query, 0, tamanho)
    body_json = _json.dumps(body)

    pw_cm = async_playwright()
    if _HAS_STEALTH:
        pw_cm = Stealth().use_async(pw_cm)

    async with pw_cm as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        try:
            ctx = await browser.new_context(
                user_agent=UA,
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
            )
            page = await ctx.new_page()
            # networkidle deixa o WAF concluir o desafio e setar o aws-waf-token
            await page.goto(STF_SPA_URL, wait_until="networkidle", timeout=60000)

            # Confirma que o aws-waf-token foi setado (até 15s a mais, se preciso)
            import asyncio as _aio
            for _ in range(15):
                cookies = await ctx.cookies()
                if any(c["name"] == "aws-waf-token" for c in cookies):
                    break
                await _aio.sleep(1)

            # Chama a API dentro da origem autorizada
            result = await page.evaluate(
                """
                async ({path, body, base}) => {
                    const r = await fetch(path + '?base=' + base, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: body,
                        credentials: 'include'
                    });
                    const ct = r.headers.get('content-type') || '';
                    const text = await r.text();
                    return {status: r.status, ct: ct, text: text};
                }
                """,
                {"path": STF_SEARCH_PATH, "body": body_json, "base": base},
            )
        finally:
            await browser.close()

    if result["status"] != 200 or "json" not in (result["ct"] or "").lower():
        raise RuntimeError(
            f"Playwright também falhou: HTTP {result['status']} CT={result['ct']} "
            f"preview={result['text'][:200]}"
        )
    try:
        return _json.loads(result["text"])
    except _json.JSONDecodeError as e:
        raise RuntimeError(f"Resposta Playwright não é JSON válido: {e}; preview={result['text'][:200]}")


# ---------------------------------------------------------------------------
# Parser de resultado individual
# ---------------------------------------------------------------------------

def _parse_resultado(hit: dict, base: str, max_tokens_ementa: int = 400) -> BaseResultadoJuridico:
    src = hit.get("_source", {})
    numero = src.get("processo_codigo_completo") or src.get("processo_numero", "")
    tipo = src.get("processo_classe_processual_unificada_extenso") or src.get(
        "processo_classe_processual_unificada_classe_sigla", ""
    )
    relator = src.get("relator_processo_nome", "")
    data = src.get("publicacao_data") or src.get("julgamento_data", "")

    ementa = src.get("documental_indexacao_texto") or src.get("decisao_texto", "")
    ementa = limpar_texto_html(ementa)
    ementa = truncar_por_tokens(ementa, max_tokens=max_tokens_ementa)

    url = src.get("inteiro_teor_url", "")

    return BaseResultadoJuridico(
        conteudo=ementa,
        fonte="STF",
        tipo=tipo or base.upper(),
        orgao="Supremo Tribunal Federal",
        numero=numero,
        relator=relator,
        data=data,
        extra={"url": url, "base": base} if url else {"base": base},
    )


# ---------------------------------------------------------------------------
# Tool pública
# ---------------------------------------------------------------------------

@mcp.tool()
async def buscar_jurisprudencia_stf(
    query: str,
    base: str = "acordaos",
    tamanho: int = 10,
    max_tokens_ementa: int = 400,
) -> str:
    """
    Busca jurisprudência no Supremo Tribunal Federal (STF).
    Use ajuda_sintaxe_stf() para guia completo de operadores e exemplos.

    SINTAXE RÁPIDA: termos simples (AND implícito), AND/OR/NOT explícito,
    "frase exata", wildcards com *, boost com ^2.

    BASES: acordaos (padrão) | sumulas | decisoes-monocraticas | informativos

    Args:
        query: Termos de busca em sintaxe Elasticsearch query_string
        base: Base de dados (acordaos | sumulas | decisoes-monocraticas | informativos)
        tamanho: Número de resultados (1–50, padrão 10)
        max_tokens_ementa: Truncamento da ementa em tokens (50-4000). Default: 400.
                           Aumente para obter ementa mais longa/íntegra.

    Returns:
        XML estruturado com os resultados encontrados.
        Comentário inicial indica qual rota respondeu (api-direta ou playwright-fallback).

    Notas de roteamento interno:
        1. Tenta a API JSON via `requests` (1-2s). Funciona se o portal não estiver
           sob WAF/Cloudflare challenge ativo.
        2. Em caso de bloqueio, faz fallback via Playwright stealth headless (~5-10s):
           abre a SPA do STF, deixa o JS do WAF aplicar o aws-waf-token e dispara
           a chamada de API no contexto da origem autorizada.
        3. Se ambos falharem, retorna `<erro tipo="..."/>` com orientação para
           bnp-api, base local ou cjf-jurisprudencia.
    """
    tamanho = max(1, min(tamanho, 50))
    max_tokens_ementa = max(50, min(int(max_tokens_ementa), 4000))
    base = base.lower().strip()
    if base not in BASES_VALIDAS:
        base = "acordaos"

    t0 = time.perf_counter()
    cache_hit = False
    n_results = 0
    erro_final = None
    try:
        erros = []
        rota = None
        data = None

        # A1 — cache HTTP. STF é caro (Playwright fallback ~10s); cache vale ouro.
        cache_key = {"mcp": "stf-jurisprudencia", "query": query, "base": base, "tamanho": tamanho}
        cached = cached_http("stf-jurisprudencia", cache_key)
        if cached is not None:
            data = _json.loads(cached)
            rota = "cache-hit"
            cache_hit = True

        # 1. Tentativa rápida via requests
        if data is None:
            try:
                data = _buscar_via_requests(query, base, tamanho)
                rota = "api-direta"
            except _STFWAFBlock as e:
                erros.append(f"requests: WAF block — {e}")
            except Exception as e:
                erros.append(f"requests: {type(e).__name__}: {str(e)[:160]}")

        # 2. Fallback Playwright stealth
        if data is None:
            try:
                data = await _buscar_via_playwright(query, base, tamanho)
                rota = "playwright-fallback"
            except Exception as e:
                erros.append(f"playwright: {type(e).__name__}: {str(e)[:200]}")
                erro_final = " | ".join(erros)
                return (
                    f'<erro tipo="acesso-bloqueado" base="{BASES_VALIDAS[base]}">'
                    f'Falha ao acessar o portal STF por ambas as rotas. '
                    f'Detalhes: {" | ".join(erros)}. '
                    f'Alternativas: (1) bnp-api para Temas/RG/SV do STF; '
                    f'(2) base local em DPU/indice-temas-rg.md, DPU/indice-sumulas.md; '
                    f'(3) cjf-jurisprudencia para STJ/TRFs (CJF não cobre STF).'
                    f'</erro>'
                )

        # Grava cache só quando NÃO veio do cache (evita re-escrever idêntico).
        if not cache_hit and data is not None:
            try:
                registrar_dispositivo(
                    "stf-jurisprudencia", cache_key,
                    _json.dumps(data), ttl_s=_STF_TTL_S,
                )
            except Exception:
                pass

        hits = data.get("result", {}).get("hits", {})
        total = hits.get("total", {}).get("value", 0)
        docs = hits.get("hits", [])

        if not docs:
            return (
                f'<!-- STF | rota: {rota} | base: {BASES_VALIDAS[base]} -->\n'
                f'<resultados total="0" base="{base}">'
                f'<mensagem>Nenhum resultado encontrado para: {query}</mensagem>'
                f'</resultados>'
            )

        resultados = [_parse_resultado(h, base, max_tokens_ementa) for h in docs]
        n_results = len(resultados)

        xml = formatar_resultados_xml(resultados, tag_raiz="resultados")
        xml = xml.replace(
            f'<resultados total="{len(resultados)}">',
            f'<resultados total="{len(resultados)}" total_encontrados="{total}" '
            f'base="{BASES_VALIDAS[base]}" tribunal="STF" rota="{rota}">',
        )
        meta = f'<!-- STF | rota: {rota} | base: {BASES_VALIDAS[base]} | total_encontrados: {total} -->\n'
        return meta + xml
    finally:
        log_query(
            mcp="stf-jurisprudencia",
            tool="buscar_jurisprudencia_stf",
            query=query,
            filtros={"base": base, "tamanho": tamanho},
            n_resultados=n_results,
            ms=int((time.perf_counter() - t0) * 1000),
            cache_hit=cache_hit,
            erro=erro_final,
        )


@mcp.tool()
def ajuda_sintaxe_stf() -> str:
    """
    Retorna guia completo de sintaxe, operadores e exemplos para buscar_jurisprudencia_stf.
    Consulte antes de formular queries complexas.
    """
    return """
SINTAXE STF — Elasticsearch query_string

OPERADORES:
  Termos simples: "BPC assistência social"     → AND implícito
  AND explícito:  "BPC AND assistência"
  OR:             "BPC OR assistência"
  NOT:            "BPC NOT administrativo"
  Frase exata:    '"benefício de prestação continuada"'
  Wildcards:      "assist*"
  Boost:          "BPC^2 assistência"

BASES DISPONÍVEIS:
  acordaos            — Acórdãos do Plenário e Turmas (padrão)
  sumulas             — Súmulas vinculantes e não vinculantes
  decisoes-monocraticas — Decisões individuais dos Ministros
  informativos        — Informativos de jurisprudência

EXEMPLOS:
  buscar_jurisprudencia_stf("BPC assistência social deficiência")
  buscar_jurisprudencia_stf("FGTS expurgos inflacionários", base="acordaos", tamanho=5)
  buscar_jurisprudencia_stf("súmula vinculante", base="sumulas")
  buscar_jurisprudencia_stf('"repercussão geral" previdenciário', tamanho=20)

DICAS:
  - Para temas vinculantes: "Tema 1234"
  - Para repercussão geral: inclua "repercussão geral" ou "RG" na query
  - Resultados ordenados por relevância (score Elasticsearch)

ROTEAMENTO INTERNO:
  Tenta a API JSON direta primeiro (~1-2s). Se o portal estiver sob desafio
  anti-bot (AWS WAF / Cloudflare), cai automaticamente em fallback Playwright
  stealth (~5-10s) que abre a SPA, deixa o JS resolver o desafio e dispara o
  fetch dentro do contexto autorizado. O comentário inicial do XML indica
  qual rota respondeu.
"""


if __name__ == "__main__":
    mcp.run()
