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
import re
import time
from typing import Any
from tenacity import retry, retry_if_not_exception_type, wait_exponential, stop_after_attempt
from xml.sax.saxutils import escape as _xml_escape
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
    "base",
    "titulo",
    "processo_codigo_completo",
    "processo_numero",
    "processo_classe_processual_unificada_extenso",
    "processo_classe_processual_unificada_classe_sigla",
    "orgao_julgador",
    "relator_processo_nome",
    "relator_acordao_nome",
    "julgamento_data",
    "publicacao_data",
    "ementa_texto",
    "decisao_texto",
    "sumula_texto",
    "conteudo_texto",
    "resumo_noticia",
    "documental_indexacao_texto",
    "documental_tese_tema_texto",
    "documental_tese_texto",
    "is_vinculante",
    "situacao_sumula",
    "inteiro_teor_url",
    "informativo_url",
]

BASES_VALIDAS = {
    "acordaos": "Acórdãos",
    "sumulas": "Súmulas",
    "decisoes-monocraticas": "Decisões Monocráticas",
    "informativos": "Informativos",
}

# Valores do campo `base` no índice para cada base da tool. A API IGNORA o
# `?base=` da URL: sem este filtro no corpo, as quatro bases devolviam o mesmo
# conjunto (medido em 2026-10-22). Os informativos têm duas séries no índice.
BASES_INDICE = {
    "acordaos": ["acordaos"],
    "sumulas": ["sumulas"],
    "decisoes-monocraticas": ["decisoes"],
    "informativos": ["informativos", "novo_informativo"],
}
ALIASES_BASE = {"decisoes": "decisoes-monocraticas"}

# O texto principal de cada base mora num campo próprio (ementa, enunciado,
# informativo, tese). Fora da lista, a base fica inalcançável: só com os quatro
# campos antigos, acórdãos e informativos davam zero.
CAMPOS_BUSCA = [
    # Peso no título: sem ele, "RE 567985" trazia antes os agravos que só o citam.
    "titulo^5",
    "ementa_texto",
    "decisao_texto",
    "sumula_texto",
    "conteudo_texto",
    "titulo_noticia",
    "resumo_noticia",
    "documental_tese_tema_texto",
    "documental_tese_texto",
    "documental_indexacao_texto",
    "documental_observacao_texto",
]

# Sobe quando o corpo da busca muda de sentido, para o cache em disco não
# servir resposta gravada pela versão anterior (a v1 não filtrava a base).
_CACHE_VERSAO = 2


def _escapar_barra(query: str) -> str:
    """`/` abre regex no query_string, e `Lei 8.742/93` volta HTTP 400."""
    return re.sub(r"(?<!\\)/", r"\\/", query)


def _payload(query: str, inicio: int, tamanho: int, base: str) -> dict:
    return {
        "query": {
            "bool": {
                "must": [{
                    "query_string": {
                        "query": _escapar_barra(query),
                        "default_operator": "AND",
                        "fields": CAMPOS_BUSCA,
                    }
                }],
                "filter": [{"terms": {"base": BASES_INDICE[base]}}],
            }
        },
        "from": inicio,
        "size": tamanho,
        "track_total_hits": True,
        "_source": SOURCE_FIELDS,
    }


# ---------------------------------------------------------------------------
# Tentativa direta via requests
# ---------------------------------------------------------------------------

class _STFWAFBlock(RuntimeError):
    """Resposta sugere bloqueio AWS WAF / Cloudflare (202 vazio, 403, HTML challenge)."""


class _STFQueryInvalida(ValueError):
    """HTTP 400 do Elasticsearch: a query não parseia. Repetir ou subir o
    navegador não muda nada, e tratá-la como bloqueio mandava o agente buscar
    fonte alternativa achando que o STF estava fora do ar."""


def _detalhe_400(texto: str) -> str:
    try:
        return str(_json.loads(texto).get("detail", texto))[:300]
    except (ValueError, AttributeError):
        return texto[:300]


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


@retry(wait=wait_exponential(multiplier=1, min=1, max=2), stop=stop_after_attempt(2), reraise=True,
       retry=retry_if_not_exception_type(_STFQueryInvalida))
def _buscar_via_requests(query: str, base: str, tamanho: int) -> dict:
    session = requests.Session()
    session.headers.update(HEADERS)
    resp = session.post(
        f"{STF_SEARCH_URL}?base={base}",
        json=_payload(query, 0, tamanho, base),
        timeout=10,
    )
    if resp.status_code == 400:
        raise _STFQueryInvalida(_detalhe_400(resp.text))
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
    body = _payload(query, 0, tamanho, base)
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

    if result["status"] == 400:
        raise _STFQueryInvalida(_detalhe_400(result["text"]))
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

# Texto principal, na ordem: ementa (acórdão), enunciado (súmula), decisão
# monocrática, informativo novo e antigo. A indexação é só o último recurso —
# é a lista de descritores do STF, não a ementa, e saía como se fosse.
_CAMPOS_CONTEUDO = (
    "ementa_texto",
    "sumula_texto",
    "decisao_texto",
    "resumo_noticia",
    "conteudo_texto",
    "documental_indexacao_texto",
)


def _txt(src: dict, campo: str) -> str:
    """Campo como texto: na série novo_informativo o tema e o número do
    processo vêm como int, e o tratamento de texto quebrava com TypeError."""
    valor = src.get(campo)
    return "" if valor is None else str(valor)


def _parse_resultado(hit: dict, base: str, max_tokens_ementa: int = 400) -> BaseResultadoJuridico:
    src = hit.get("_source", {})
    base_doc = _txt(src, "base") or base
    numero = (_txt(src, "processo_codigo_completo") or _txt(src, "titulo")
              or _txt(src, "processo_numero"))
    if base_doc == "sumulas":
        tipo = "Súmula Vinculante" if src.get("is_vinculante") else "Súmula"
    elif base_doc in ("informativos", "novo_informativo"):
        tipo = "Informativo"
    else:
        tipo = _txt(src, "processo_classe_processual_unificada_extenso") or _txt(
            src, "processo_classe_processual_unificada_classe_sigla"
        )
    relator = _txt(src, "relator_processo_nome")
    data = _txt(src, "publicacao_data") or _txt(src, "julgamento_data")

    conteudo = next((_txt(src, c) for c in _CAMPOS_CONTEUDO if _txt(src, c)), "")
    conteudo = limpar_texto_html(conteudo)
    conteudo = truncar_por_tokens(conteudo, max_tokens=max_tokens_ementa)

    redator = _txt(src, "relator_acordao_nome")
    tema = src.get("documental_tese_tema_texto")
    extra = {
        "redator_acordao": redator if redator != relator else "",
        "data_julgamento": _txt(src, "julgamento_data"),
        "data_publicacao": _txt(src, "publicacao_data"),
        "tema": f"Tema {tema}" if isinstance(tema, int) else limpar_texto_html(_txt(src, "documental_tese_tema_texto")),
        "tese": limpar_texto_html(_txt(src, "documental_tese_texto")),
        "situacao": _txt(src, "situacao_sumula"),
        "url": _txt(src, "inteiro_teor_url") or _txt(src, "informativo_url"),
        "base": base_doc,
    }

    return BaseResultadoJuridico(
        conteudo=conteudo,
        fonte="STF",
        tipo=tipo or base.upper(),
        orgao=src.get("orgao_julgador") or "Supremo Tribunal Federal",
        numero=numero,
        relator=relator,
        data=data,
        extra=extra,
    )


# ---------------------------------------------------------------------------
# Tool pública
# ---------------------------------------------------------------------------

def _erro_sintaxe(base: str, detalhe: str) -> str:
    return (
        f'<erro tipo="sintaxe" base="{BASES_VALIDAS[base]}">'
        f'O STF recusou a query (HTTP 400): ela não parseia no query_string do '
        f'Elasticsearch. Não é bloqueio do portal: corrija a query e repita. '
        f'Causas comuns: aspa ou parêntese sem fecho, dois-pontos fora de campo, '
        f'caractere reservado ( + - = &amp;&amp; || &gt; &lt; ! ( ) {{ }} [ ] ^ " ~ * ? : \\ ) '
        f'sem barra invertida. Detalhe: {_xml_escape(detalhe)}'
        f'</erro>'
    )


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
    "frase exata", wildcards com *, boost com ^2. Número de processo
    ("RE 567985") traz o próprio processo primeiro.

    BASES: acordaos (padrão) | sumulas | decisoes-monocraticas (ou decisoes) | informativos
    Cada base devolve só documentos dela; base inexistente é erro.

    Args:
        query: Termos de busca em sintaxe Elasticsearch query_string
        base: Base de dados (acordaos | sumulas | decisoes-monocraticas | informativos)
        tamanho: Número de resultados (1–50, padrão 10)
        max_tokens_ementa: Truncamento do conteúdo em tokens (50-4000). Default: 400.
                           Aumente para obter ementa mais longa/íntegra.

    Returns:
        XML estruturado. <conteudo> é a EMENTA no acórdão, o ENUNCIADO na súmula,
        o texto da decisão na monocrática e o resumo no informativo. Campos de
        citação: <orgao> (Pleno/Turma), <relator>, <redator_acordao> (quando
        outro), <data_julgamento>, <data_publicacao>, <tema>/<tese> (RG),
        <situacao> (súmula), <url> (inteiro teor) e <base> do documento.
        Comentário inicial indica qual rota respondeu (api-direta ou playwright-fallback).
        Query que não parseia volta <erro tipo="sintaxe"> — não é bloqueio do portal.

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
    base = ALIASES_BASE.get(base, base)
    if base not in BASES_VALIDAS:
        return (
            f'<erro tipo="base-invalida">Base "{_xml_escape(base)}" não existe. '
            f'Bases válidas: {", ".join(BASES_VALIDAS)}.</erro>'
        )

    t0 = time.perf_counter()
    cache_hit = False
    n_results = 0
    erro_final = None
    try:
        erros = []
        rota = None
        data = None

        # A1 — cache HTTP. STF é caro (Playwright fallback ~10s); cache vale ouro.
        cache_key = {"mcp": "stf-jurisprudencia", "query": query, "base": base, "tamanho": tamanho,
                     "v": _CACHE_VERSAO}
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
            except _STFQueryInvalida as e:
                erro_final = f"sintaxe: {e}"
                return _erro_sintaxe(base, str(e))
            except _STFWAFBlock as e:
                erros.append(f"requests: WAF block — {e}")
            except Exception as e:
                erros.append(f"requests: {type(e).__name__}: {str(e)[:160]}")

        # 2. Fallback Playwright stealth
        if data is None:
            try:
                data = await _buscar_via_playwright(query, base, tamanho)
                rota = "playwright-fallback"
            except _STFQueryInvalida as e:
                erro_final = f"sintaxe: {e}"
                return _erro_sintaxe(base, str(e))
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
                f'<mensagem>Nenhum resultado encontrado para: {_xml_escape(query)}</mensagem>'
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
  Barra:          "Lei 8.742/93" funciona — a ferramenta escapa a "/", que no
                  query_string abriria expressão regular (HTTP 400)

CARACTERES RESERVADOS: + - = && || > < ! ( ) { } [ ] ^ " ~ * ? : \\
  Aspa ou parêntese sem fecho e dois-pontos solto ("tema: 1234") fazem a query
  não parsear: volta <erro tipo="sintaxe">, que NÃO é bloqueio do portal —
  corrija a query e repita, não troque de fonte.

BASES DISPONÍVEIS (cada uma devolve só documentos dela):
  acordaos              — Acórdãos do Plenário e Turmas (padrão); busca na ementa,
                          na tese de RG, na decisão e na indexação
  sumulas               — Súmulas vinculantes e não vinculantes; busca no enunciado
  decisoes-monocraticas — Decisões individuais dos Ministros (alias: decisoes)
  informativos          — Informativos de jurisprudência (série antiga e nova)

EXEMPLOS:
  buscar_jurisprudencia_stf("RE 567985")                       # o processo vem primeiro
  buscar_jurisprudencia_stf("BPC assistência social deficiência")
  buscar_jurisprudencia_stf("ato jurídico perfeito", base="sumulas")
  buscar_jurisprudencia_stf("HC 184828", base="decisoes-monocraticas")
  buscar_jurisprudencia_stf('"repercussão geral" previdenciário', tamanho=20)

DICAS:
  - Para tema de repercussão geral: '"Tema 1234"' (entre aspas) — casa a tese
    e os acórdãos que a aplicam; <tema> e <tese> vêm no resultado do leading case
  - Súmula vinculante pelo enunciado, em base="sumulas"; <tipo> diz se é vinculante
    e <situacao> se está aprovada, revogada ou cancelada
  - Para citar o acórdão: <orgao>, <relator>, <redator_acordao> (quando o
    relator ficou vencido), <data_julgamento> e <data_publicacao>
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
