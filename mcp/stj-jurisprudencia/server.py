"""
MCP Server: STJ Jurisprudência

ACÓRDÃOS (ACOR)   → CJF Unificada com filtro tribunais=STJ
                    (https://jurisprudencia.cjf.jus.br/unificada/index.xhtml)
SÚMULAS (SUMU)    → SCON por HTTP no host processo.stj.jus.br (sem Cloudflare),
                    depois scon.stj.jus.br por HTTP, depois CDP
                    (https://processo.stj.jus.br/SCON/pesquisar.jsp?b=SUMU)
INFORMATIVOS (INFJ) → portal do Informativo de Jurisprudência por HTTP
                    (https://processo.stj.jus.br/jurisprudencia/externo/informativo/)

O SCON migrou para Cloudflare com challenge JS (Turnstile) que bloqueia acesso
programático. Até 2026-07-30 este servidor ainda o tentava primeiro em toda
chamada e só então caía no CJF — a telemetria do período mostrou 359 de 366
chamadas terminando no fallback, ao custo de ~44 s cada (268 min). Desde então
a precedência é invertida para ACOR: o CJF responde direto (~1,3 s) e o SCON
vira fallback. Em SUMU, que o CJF não cobre, o SCON segue primeiro, com
pedágio curto (1 tentativa, 8 s por host).

HOST SEM CLOUDFLARE (01/10/2026): o Cloudflare barra scon.stj.jus.br (403),
mas o MESMO aplicativo SCON responde em processo.stj.jus.br, por HTTP simples
e com a pesquisa por querystring funcionando (SUMU "impenhorabilidade" = 9
súmulas em ~1 s). A via HTTP do SCON tenta esse host primeiro, em SUMU e em
ACOR (forcar_scon ou fallback), e o comentário inicial declara qual host
respondeu. A lista de súmulas tem layout próprio (`.gridSumula`) — ver
`_parse_sumulas_html`.

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
from shared import tls_sistema

tls_sistema.aplicar()
from shared.base_juridica import (
    BaseResultadoJuridico,
    formatar_resultados_xml,
    truncar_por_tokens,
    sanitizar_comentario_xml,
)
from shared import cjf_client
from shared.relaxamento import AVISO_RELAXADA, relaxar_cjf

# Rota de navegador real por CDP — o que efetivamente atravessa o Cloudflare do
# SCON. Opcional: sem playwright no venv, a rota some e o servidor segue com
# HTTP + CJF, como antes.
try:
    from shared import cdp_edge  # type: ignore
except Exception:  # noqa: BLE001
    cdp_edge = None  # type: ignore

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

# O MESMO aplicativo SCON responde em processo.stj.jus.br SEM Cloudflare
# (medido em 01/10/2026: scon.stj.jus.br → 403; processo.stj.jus.br → 200, com
# a pesquisa por querystring funcionando — SUMU "impenhorabilidade" = 9 súmulas,
# ACOR idem). Por isso a via HTTP tenta esse host PRIMEIRO e só depois o
# scon.stj.jus.br; a rota CDP continua no scon.stj.jus.br.
SCON_PROCESSO_BASE = "https://processo.stj.jus.br/SCON"
_SCON_HOSTS_HTTP = (SCON_PROCESSO_BASE, SCON_BASE)

# A busca sem resultado traz este aviso; página SEM contagem e SEM o aviso não
# é resultado (tela inicial, erro silencioso) e não pode virar "0 encontrados".
_SCON_AVISO_ZERO = "Nenhum documento encontrado"

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

# --- Informativo de Jurisprudência (base INFJ) -------------------------------
# Medido em 01/10/2026: no SCON, `pesquisar.jsp?b=INFJ` ainda CONTA as notas
# (164 para "defensoria publica") mas renderiza a aba de Acórdãos com cada item
# reduzido a "Documento inválido: <n>" — não há o que extrair. A base vive no
# portal próprio do Informativo, que responde por HTTP simples, sem Cloudflare
# (~3 s), com o mesmo total de notas.
INFORMATIVO_HOST = "https://processo.stj.jus.br"
INFORMATIVO_PESQUISAR = f"{INFORMATIVO_HOST}/jurisprudencia/externo/informativo/"
_INFORMATIVO_TIMEOUT_S = 20

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
def _pesquisar_scon(
    session, query: str, base: str, tamanho: int = 10,
    scon_base: str = SCON_BASE,
) -> str:
    params = {
        "acao": "pesquisar",
        "novaConsulta": "true",
        "i": "1",
        "b": base,
        "livre": query,
        "tipo_visualizacao": "RESUMO",
        "p": "true",
        "O": "JT",
        # Itens por página: sem `l` o portal devolve 10, e a tool aceita até 40.
        "l": str(tamanho),
    }
    # curl_cffi/libcurl não lê REQUESTS_CA_BUNDLE — verify explícito.
    resp = session.get(f"{scon_base}/pesquisar.jsp", params=params,
                       timeout=_SCON_TIMEOUT_S,
                       headers={"Referer": f"{scon_base}/"},
                       verify=tls_sistema.caminho_bundle())
    resp.raise_for_status()
    # O SCON serve ISO-8859-1; decodificar pelo cabeçalho, não por palpite.
    text = resp.content.decode(resp.encoding or "iso-8859-1", "replace")
    # Sentinelas de Cloudflare/erro silencioso
    if "Verificação automática" in text or "cf-mitigated" in text.lower():
        raise RuntimeError("Cloudflare challenge ativo no SCON")
    return text


def _ler_total_scon(soup) -> int:
    num_docs_el = soup.find(class_="numDocs")
    if num_docs_el:
        m = re.search(r"(\d[\d.]*)", num_docs_el.get_text())
        if m:
            return int(m.group(1).replace(".", ""))
    return 0


# Nota inline depois da fonte: cancelamento/revogação, revisão/alteração/
# modificação (com a "REDAÇÃO ANTERIOR") ou questão de ordem — os rótulos que
# aparecem nas 676 súmulas (01/10/2026). O verbete vigente é o que vem antes.
_RE_SUM_NOTA = re.compile(
    r"\b(S[ÚU]MULA\s+(CANCELADA|REVOGADA|REVISADA|ALTERADA)"
    r"|QUEST[ÃA]O\s+DE\s+ORDEM|MODIFICA[ÇC][ÃA]O\s+DE\s+TEXTO)\s*:", re.I
)
# Súmula que deixou de valer: o rótulo vai à `situacao` e ao `tipo`.
_SUM_FORA_DE_VIGOR = {"CANCELADA", "REVOGADA"}
# Data com ou sem zero à esquerda: "11/9/2024" convive com "03/03/2010".
_RE_SUM_PUBLICACAO = re.compile(r"\b(DJe|DJEN|DJ)\s+(?:de\s+)?(\d{1,2}/\d{1,2}/\d{4})")
_RE_SUM_JULGADO = re.compile(r"julgad[oa] em\s+(\d{1,2}/\d{1,2}/\d{4})")
# Há súmulas que não marcam o órgão com `.clsOrgaoJulgador`, ou no formato
# antigo "(SÚMULA 152, PRIMEIRA SEÇÃO, DJ ...)" ou no novo sem a marcação
# "(SEGUNDA SEÇÃO, julgado em ...)" — Súmula 222.
_RE_SUM_ORGAO_ANTIGO = re.compile(
    r"\((?:S[ÚU]MULA\s+\d+\s*,\s*)?([A-ZÀ-ÚÇ][A-ZÀ-ÚÇ ]+?)\s*,\s*(?:julgad|DJ)"
)


def _texto_corrido(el) -> str:
    # Separador vazio: os nós de texto já trazem o espaçamento, e o realce da
    # busca (`span.highlightBrs`) não pode partir palavra nem soltar pontuação.
    return " ".join(el.get_text("").split())


def _parse_sumulas_html(
    html_str: str, max_tokens_ementa: int = 400
) -> Tuple[List[BaseResultadoJuridico], int]:
    """Parser da lista de SÚMULAS do SCON (base SUMU).

    A base SUMU tem layout PRÓPRIO — cada súmula é um `.gridSumula`, sem o
    `.itemlistadocumentos` dos acórdãos; por isso o parser dos acórdãos via
    a contagem ("9 súmulas") e extraía zero. Campos: `.numeroSumula`,
    `.ramoSumula`, `.clsVerbete`, `.clsOrgaoJulgador`, `.clsData`.

    O MARKUP VARIA COM O REALCE DA BUSCA (medido em 01/10/2026 sobre as 676
    súmulas): quando o termo pesquisado cai no verbete, o portal o serve como
    texto solto, SEM `.clsVerbete`/`.clsOrgaoJulgador`, com a fonte entre
    parênteses — "(TERCEIRA SEÇÃO, julgado em ..., DJe de ...)" ou, nas antigas,
    "(SÚMULA 152, PRIMEIRA SEÇÃO, DJ ...)". Aí o verbete é o texto do bloco sem
    o ramo, sem a nota e sem o parêntese final.

    CANCELAMENTO tem duas marcas: `.clsINDE` ("CANCELADA", ao lado do número)
    com a nota em `.clsCOM`, ou — quando a busca casa o próprio índice — a nota
    inline após "SÚMULA CANCELADA:". As duas vão ao `tipo` e a
    `extra["situacao"]`, porque citar súmula cancelada é erro grave.
    """
    soup = BeautifulSoup(html_str, "html.parser")
    total = _ler_total_scon(soup)

    resultados: List[BaseResultadoJuridico] = []
    for item in soup.select(".gridSumula"):
        num_el = item.select_one(".numeroSumula")
        num = num_el.get_text(strip=True) if num_el else ""
        inde_el = item.select_one(".clsINDE")
        inde = _texto_corrido(inde_el).upper() if inde_el else ""

        bloco = item.select_one(".blocoVerbete")
        ramo, verbete, orgao, texto, nota = "", "", "", "", ""
        if bloco is not None:
            ramo_el = bloco.select_one(".ramoSumula")
            if ramo_el is not None:
                ramo = _texto_corrido(ramo_el)
                ramo_el.extract()
            com_el = bloco.select_one(".clsCOM")
            if com_el is not None:
                nota = _texto_corrido(com_el)
                com_el.extract()
            verbete_el = bloco.select_one(".clsVerbete")
            orgao_el = bloco.select_one(".clsOrgaoJulgador")
            orgao = _texto_corrido(orgao_el) if orgao_el else ""
            texto = _texto_corrido(bloco)
            if verbete_el is not None:
                verbete = _texto_corrido(verbete_el)

        vigente = texto
        m_nota = _RE_SUM_NOTA.search(texto)
        if m_nota:
            vigente = texto[:m_nota.start()].strip()
            estado = (m_nota.group(2) or "").upper()
            if estado in _SUM_FORA_DE_VIGOR:
                inde = inde or estado
                nota = nota or texto[m_nota.end():].strip()
            else:
                nota = nota or texto[m_nota.start():].strip()
        if not verbete:
            verbete = re.sub(r"\s*\([^()]*\)\.?\s*$", "", vigente).strip()
        if not orgao:
            m_org = _RE_SUM_ORGAO_ANTIGO.search(vigente)
            orgao = m_org.group(1) if m_org else ""

        m_pub = _RE_SUM_PUBLICACAO.search(vigente)
        m_julg = _RE_SUM_JULGADO.search(vigente)

        extra = {"base": "SUMU"}
        if ramo:
            extra["ramo"] = ramo
        if m_julg:
            extra["julgamento"] = m_julg.group(1)
        if inde:
            extra["situacao"] = f"{inde} — {nota}" if nota else inde
        elif nota:
            extra["observacao"] = nota

        resultados.append(
            BaseResultadoJuridico(
                conteudo=truncar_por_tokens(verbete, max_tokens=max_tokens_ementa),
                fonte="STJ",
                tipo=f"Súmula ({inde})" if inde else "Súmula",
                orgao=orgao or "Superior Tribunal de Justiça",
                numero=f"Súmula {num}" if num else "",
                data=f"{m_pub.group(1)} {m_pub.group(2)}" if m_pub else "",
                extra=extra,
            )
        )

    return resultados, total


def _parser_scon(base: str):
    """SUMU tem layout próprio; ACOR (e o resto) sai pelos rótulos do RESUMO."""
    if base == "SUMU":
        return lambda html_str, _base, mt: _parse_sumulas_html(html_str, mt)
    return _parse_scon_resumo


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

def _host_scon(scon_base: str) -> str:
    from urllib.parse import urlparse
    return urlparse(scon_base).netloc


def _rota_scon(
    query: str, base: str, tamanho: int, max_tokens_ementa: int,
    *, scon_base: str = SCON_BASE,
) -> Tuple[str, int]:
    host = _host_scon(scon_base)
    sess = _criar_session_scon()
    html_str = _pesquisar_scon(sess, query, base, tamanho, scon_base=scon_base)
    parseados, total = _parser_scon(base)(html_str, base, max_tokens_ementa)
    # Canário estrutural: a página indica total > 0 mas o parser extraiu 0
    # itens → layout do SCON provavelmente mudou. Falha LOUD em vez de
    # devolver vazio silencioso.
    if total > 0 and not parseados:
        raise RuntimeError(
            f"SCON ({host}) reportou {total} documento(s) mas o parser extraiu 0 "
            "— provável mudança no HTML do portal. Verificar "
            "_parse_sumulas_html (.gridSumula) / _parse_scon_resumo "
            "(.itemlistadocumentos)."
        )
    # Canário do item OCO: itens extraídos, todos sem número — resultado oco
    # passa por resultado e vira citação sem fonte.
    if parseados and not any(r.numero for r in parseados):
        raise RuntimeError(
            f"SCON ({host}) extraiu {len(parseados)} item(ns), todos SEM número "
            "— seletores/rótulos do portal mudaram."
        )
    # Canário do FALSO ZERO: sem contagem, sem itens e sem o aviso de busca
    # vazia, a página não é uma lista de resultados (tela inicial, erro).
    if not parseados and total == 0 and _SCON_AVISO_ZERO not in html_str:
        raise RuntimeError(
            f"SCON ({host}) devolveu página sem contagem, sem itens e sem o "
            f"aviso '{_SCON_AVISO_ZERO}' — não é lista de resultados."
        )
    resultados = parseados[:tamanho]
    meta = (
        f'<!-- STJ/SCON ({host}) | Base: {BASES_VALIDAS[base]} '
        f'| Total encontrado: {total} | Exibindo: {len(resultados)} -->\n'
    )
    return meta + formatar_resultados_xml(resultados, tag_raiz="resultados"), len(resultados)


def _rota_scon_http(
    query: str, base: str, tamanho: int, max_tokens_ementa: int
) -> Tuple[str, int, str]:
    """SCON por HTTP, host a host (`_SCON_HOSTS_HTTP`): processo.stj.jus.br,
    sem Cloudflare, e depois scon.stj.jus.br. Devolve (saida, n, host) do
    primeiro que responder; falhando todos, levanta com a falha de cada um."""
    falhas = []
    for scon_base in _SCON_HOSTS_HTTP:
        host = _host_scon(scon_base)
        try:
            saida, n = _rota_scon(
                query, base, tamanho, max_tokens_ementa, scon_base=scon_base
            )
            return saida, n, host
        except Exception as e:
            falhas.append(f"{host}: {type(e).__name__}: {str(e)[:140]}")
    raise RuntimeError(" | ".join(falhas))


_RE_TIPO = re.compile(r"^\((.+)\)$")
_RE_RELATOR = re.compile(r"^Ministr[oa]\b", re.I)
_RE_PUBLICACAO = re.compile(r"^(DJe|DJEN|DJ|RSTJ|RT)\b.*?(\d{2}/\d{2}/\d{4})")
_RE_JULGAMENTO = re.compile(r"^Decis[ãa]o:\s*(\d{2}/\d{2}/\d{4})")


def _parse_scon_resumo(
    html_str: str, base: str, max_tokens_ementa: int = 400
) -> Tuple[List[BaseResultadoJuridico], int]:
    """Parser da lista do SCON na visualização RESUMO (a que a rota CDP usa).

    Distinto de `_parse_scon_html`, que foi escrito para o HTML servido por HTTP
    e usa `.col-sm-3`/`.clsEmentaCompleta` — classes que o portal renderizado não
    tem mais. Aqui a leitura é pelos RÓTULOS do item (Processo / (TIPO) /
    Ministro / DJe / Decisão: / Ementa), que são estáveis e legíveis por humano,
    em vez de classe CSS, que muda a cada reforma do portal.

    LIMITE DECLARADO: nesta visualização o SCON TRUNCA a ementa (termina em "..."
    seguido de "(+)"). Serve para CONFIRMAR e LOCALIZAR o julgado — relator,
    órgão, datas, número —, que é o uso do gate de citações. NÃO serve para
    transcrever ementa em peça: a regra de ementa integral do projeto exige abrir
    o espelho do documento. O campo `extra["ementa_truncada"]` sinaliza isso.
    """
    soup = BeautifulSoup(html_str, "html.parser")

    total = 0
    num_docs_el = soup.find(class_="numDocs")
    if num_docs_el:
        m = re.search(r"(\d[\d.]*)", num_docs_el.get_text())
        if m:
            total = int(m.group(1).replace(".", ""))

    resultados: List[BaseResultadoJuridico] = []
    for item in soup.select(".itemlistadocumentos"):
        linhas = [ln.strip() for ln in item.get_text("\n", strip=True).split("\n") if ln.strip()]

        numero_partes: List[str] = []
        tipo = BASES_VALIDAS.get(base, base)
        relator = ""
        data = ""
        julgamento = ""
        ementa_linhas: List[str] = []
        estado = "inicio"

        for ln in linhas:
            if ln.lower() == "processo":
                estado = "numero"
                continue
            if ln.lower() == "ementa":
                estado = "ementa"
                continue
            if estado == "ementa":
                if ln == "(+)":
                    break
                ementa_linhas.append(ln)
                continue
            m_tipo = _RE_TIPO.match(ln)
            if m_tipo and estado == "numero":
                tipo = m_tipo.group(1).strip()
                estado = "meta"
                continue
            if estado == "numero":
                numero_partes.append(ln)
                continue
            if _RE_RELATOR.match(ln) and not relator:
                relator = re.sub(r"\s*\(\d+\)\s*$", "", ln).strip()
                continue
            m_pub = _RE_PUBLICACAO.match(ln)
            if m_pub and not data:
                data = ln
                continue
            m_julg = _RE_JULGAMENTO.match(ln)
            if m_julg and not julgamento:
                julgamento = m_julg.group(1)
                continue

        ementa = " ".join(ementa_linhas).strip()
        truncada = ementa.endswith("...")
        ementa = truncar_por_tokens(ementa, max_tokens=max_tokens_ementa)

        extra = {"base": base}
        if julgamento:
            extra["julgamento"] = julgamento
        if truncada:
            extra["ementa_truncada"] = (
                "SIM — visualização RESUMO do SCON. Para citar em peça, abrir o "
                "espelho do documento (regra de ementa integral)."
            )

        resultados.append(
            BaseResultadoJuridico(
                conteudo=ementa,
                fonte="STJ",
                tipo=tipo,
                orgao="Superior Tribunal de Justiça",
                numero=" ".join(numero_partes).strip(),
                relator=relator,
                data=data,
                extra=extra,
            )
        )

    return resultados, total


_RE_INF_TOTAL = re.compile(r"Notas encontradas:\s*(\d[\d.]*)")
# "Informativo de Jurisprudência n. 897 - 18 de agosto de 2026. <tema>" ou
# "Informativo de Jurisprudência - Edição Extraordinária n. 33 - Direito Penal
# - 28 de julho de 2026. <tema>" — a numeração das extraordinárias é PRÓPRIA,
# então o rótulo da edição inteiro vai no tipo, nunca só o número.
_RE_INF_EDICAO = re.compile(
    r"^(Informativo de Jurisprud\S+.*?n\.\s*\d+).*?(\d{1,2} de \w+ de \d{4})"
)
_RE_INF_RELATOR = re.compile(r"Rel\.\s*(Ministr[oa]\s+[^,]+)")
_RE_INF_JULGADO = re.compile(r"julgado em\s+(\d{1,2}/\d{1,2}/\d{4})")
_RE_INF_PUBLICACAO = re.compile(r"\b(DJEN|DJe|DJ)\s+(\d{1,2}/\d{1,2}/\d{4})")


def _rotulos_informativo(item) -> dict:
    """Pares rótulo→texto de uma nota. O valor vem na MESMA `.divLinha` do
    rótulo (Processo, Ramo, Tema) ou na linha SEGUINTE (Destaque, Inteiro Teor)."""
    pares: dict = {}
    pendente = None
    for linha in item.select(".clsInformativoTextoBloco .divLinha"):
        rotulo_el = linha.select_one(".clsInformativoLabel")
        valor_el = linha.select_one(".clsInformativoTexto, .clsInformativoTextoFormatado")
        if rotulo_el is not None:
            rotulo = rotulo_el.get_text(" ", strip=True)
            if valor_el is None:
                pendente = rotulo
                continue
        else:
            rotulo, pendente = pendente, None
        if rotulo and valor_el is not None:
            # O realce da busca (`span.highlightBrs`) parte o texto; o " " do
            # get_text deixaria "Defensoria Pública ." — cola a pontuação.
            texto = re.sub(r"\s+([.,;:)])", r"\1", valor_el.get_text(" ", strip=True))
            pares.setdefault(rotulo, texto)
    return pares


def _parse_informativo_html(
    html_str: str, max_tokens_ementa: int = 400
) -> Tuple[List[BaseResultadoJuridico], int]:
    """Parser da lista do portal do Informativo de Jurisprudência do STJ.

    Cada nota é um `.clsInformativoBlocoItem`. A identificação (URL, edição) sai
    dos campos ocultos `#urlNotaN`/`#temaNotaN`, presentes em TODA nota — o
    título visível da edição só aparece na primeira nota de cada edição. O
    conteúdo é o DESTAQUE (a tese da nota); sem ele, o TEMA.
    """
    soup = BeautifulSoup(html_str, "html.parser")

    total = 0
    m_total = _RE_INF_TOTAL.search(soup.get_text(" ", strip=True))
    if m_total:
        total = int(m_total.group(1).replace(".", ""))

    resultados: List[BaseResultadoJuridico] = []
    orgao_corrente, tipo_corrente = "", ""
    for item in soup.select(".clsInformativoBlocoItem"):
        pares = _rotulos_informativo(item)
        processo = pares.get("Processo", "")
        numero = re.split(r",\s*Rel\.", processo, maxsplit=1)[0].strip(" ,")

        m_rel = _RE_INF_RELATOR.search(processo)
        m_julg = _RE_INF_JULGADO.search(processo)
        m_pub = _RE_INF_PUBLICACAO.search(processo)

        tema_oculto = item.select_one("[id^=temaNota]")
        m_ed = _RE_INF_EDICAO.search(tema_oculto.get_text(" ", strip=True)) if tema_oculto else None
        tipo = " ".join(m_ed.group(1).split()) if m_ed else BASES_VALIDAS["INFJ"]

        # O cabeçalho do órgão só vem na PRIMEIRA nota de cada órgão dentro da
        # edição; as seguintes herdam-no até a edição mudar.
        if tipo != tipo_corrente:
            orgao_corrente, tipo_corrente = "", tipo
        orgao_el = item.select_one(".clsInformativoOrgaojulgador")
        if orgao_el:
            orgao_corrente = orgao_el.get_text(" ", strip=True)
        orgao = orgao_corrente or "Superior Tribunal de Justiça"

        tema = pares.get("Tema", "")
        conteudo = truncar_por_tokens(pares.get("Destaque") or tema, max_tokens=max_tokens_ementa)

        extra = {"base": "INFJ"}
        if m_ed:
            extra["data_edicao"] = m_ed.group(2)
        if tema:
            extra["tema"] = tema
        if pares.get("Ramo do Direito"):
            extra["ramo"] = pares["Ramo do Direito"]
        if m_julg:
            extra["julgamento"] = m_julg.group(1)
        url_el = item.select_one("[id^=urlNota]")
        if url_el:
            extra["url"] = INFORMATIVO_HOST + url_el.get_text(strip=True)

        resultados.append(
            BaseResultadoJuridico(
                conteudo=conteudo,
                fonte="STJ",
                tipo=tipo,
                orgao=orgao,
                numero=numero,
                relator=m_rel.group(1).strip() if m_rel else "",
                data=f"{m_pub.group(1)} {m_pub.group(2)}" if m_pub else "",
                extra=extra,
            )
        )

    return resultados, total


def _pesquisar_informativo(query: str, tamanho: int) -> str:
    params = {
        "acao": "pesquisar", "livre": query, "b": "INFJ",
        "p": "true", "l": str(tamanho), "i": "1",
    }
    sess = _criar_session_scon()
    resp = sess.get(INFORMATIVO_PESQUISAR, params=params,
                    timeout=_INFORMATIVO_TIMEOUT_S,
                    verify=tls_sistema.caminho_bundle())
    resp.raise_for_status()
    # O portal serve ISO-8859-1; decodificar pelo cabeçalho, não por palpite.
    return resp.content.decode(resp.encoding or "iso-8859-1", "replace")


def _rota_informativo(
    query: str, tamanho: int, max_tokens_ementa: int
) -> Tuple[str, int]:
    html_str = _pesquisar_informativo(query, tamanho)
    parseados, total = _parse_informativo_html(html_str, max_tokens_ementa)
    if total > 0 and not parseados:
        raise RuntimeError(
            f"Informativo reportou {total} nota(s) mas o parser extraiu 0 "
            "— provável mudança no HTML do portal. Verificar "
            "_parse_informativo_html (.clsInformativoBlocoItem)."
        )
    if parseados and not any(r.numero for r in parseados):
        raise RuntimeError(
            f"Informativo extraiu {len(parseados)} nota(s), todas SEM processo "
            "— rótulos do portal mudaram. Ver _rotulos_informativo."
        )
    resultados = parseados[:tamanho]
    meta = (
        f'<!-- STJ/Informativo de Jurisprudência (processo.stj.jus.br) '
        f'| Base: {BASES_VALIDAS["INFJ"]} '
        f'| Total encontrado: {total} | Exibindo: {len(resultados)} -->\n'
    )
    return meta + formatar_resultados_xml(resultados, tag_raiz="resultados"), len(resultados)


def _rota_scon_cdp(
    query: str, base: str, tamanho: int, max_tokens_ementa: int
) -> Tuple[str, int]:
    """SCON por navegador REAL, alcançado por CDP.

    Existe porque o SCON responde 403 a cliente HTTP (Cloudflare Turnstile) e o
    desafio NÃO se vence com o Defensor clicando — medido em 24/08/2026: ele
    clicou e o desafio voltou. A detecção é do modo de lançamento do navegador,
    não do clique. Ver `shared/cdp_edge.py` para o racional completo e para a
    armadilha do `--user-data-dir` (obrigatório desde Edge/Chrome 136).
    """
    if cdp_edge is None:
        raise RuntimeError("shared.cdp_edge indisponível (playwright não instalado)")

    from urllib.parse import urlencode

    params = {
        "acao": "pesquisar", "novaConsulta": "true", "i": "1", "b": base,
        "livre": query, "tipo_visualizacao": "RESUMO", "p": "true", "O": "JT",
    }
    url_alvo = f"{SCON_PESQUISAR}?{urlencode(params)}"

    # `tipo_visualizacao=RESUMO` é obrigatório: sem ele a lista volta VAZIA
    # (medido em 24/08/2026 — a visualização cheia depende de estado de sessão
    # que a querystring sozinha não estabelece). O preço é a ementa truncada,
    # declarado em `extra["ementa_truncada"]` de cada resultado.
    _titulo, html_str = cdp_edge.obter_html(
        url_alvo, "scon.stj.jus.br", url_base=SCON_HOME,
    )
    parseados, total = _parser_scon(base)(html_str, base, max_tokens_ementa)

    if total > 0 and not parseados:
        raise RuntimeError(
            f"SCON/CDP reportou {total} documento(s) mas o parser extraiu 0 "
            "— provável mudança no HTML do portal."
        )
    # Canário do item OCO. O canário de cima só vê lista vazia; em 24/08/2026 o
    # modo de falha real foi outro — 2 itens extraídos, todos SEM número, sem
    # relator e sem ementa, porque o parser mirava classes que o portal não tem
    # mais. Resultado oco passa por resultado e vira citação sem fonte.
    if parseados and not any(r.numero for r in parseados):
        raise RuntimeError(
            f"SCON/CDP extraiu {len(parseados)} item(ns), todos SEM número "
            "— seletores/rótulos do portal mudaram. Ver _parse_scon_resumo."
        )
    resultados = parseados[:tamanho]
    meta = (
        f'<!-- STJ/SCON via navegador (CDP) | Base: {BASES_VALIDAS[base]} '
        f'| Total encontrado: {total} | Exibindo: {len(resultados)} -->\n'
    )
    return meta + formatar_resultados_xml(resultados, tag_raiz="resultados"), len(resultados)


def _rota_cjf(
    query: str, tamanho: int, max_tokens_ementa: int, *, apos_falha_scon: str = ""
) -> Tuple[str, int]:
    resultados, total = _buscar_via_cjf(query, tamanho, max_tokens_ementa)
    # Degradação de recall — 83% dos 248 zeros do STJ no período eram elegíveis.
    # O relaxamento roda aqui (e não no cjf_client) porque esta rota chama o
    # cliente compartilhado direto, sem passar pela tool do servidor CJF.
    relaxada = None
    if not resultados:
        candidata = relaxar_cjf(_brs_para_cjf(query))
        if candidata:
            resultados, total = _buscar_via_cjf(candidata, tamanho, max_tokens_ementa)
            relaxada = candidata if resultados else None
    origem = (
        f"fallback após falha do SCON: {apos_falha_scon}"
        if apos_falha_scon
        else "rota primária para acórdãos"
    )
    meta = (
        f'<!-- STJ via CJF Unificada ({sanitizar_comentario_xml(origem)}) '
        f'| Total STJ: {total} | Exibindo: {len(resultados)} -->\n'
    )
    if relaxada:
        meta += (
            f'<!-- BUSCA RELAXADA: "{sanitizar_comentario_xml(relaxada)}" '
            f'| {AVISO_RELAXADA} -->\n'
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
        forcar_scon: Tenta o SCON primeiro mesmo em ACOR. Default False —
                     acórdãos vão direto ao CJF Unificada. Use quando quiser
                     especificamente a ficha do SCON (sai pelo host
                     processo.stj.jus.br; ementa TRUNCADA na visualização
                     RESUMO, sinalizada em `ementa_truncada`).

    Returns:
        XML estruturado com os resultados encontrados.
        O comentário inicial declara qual fonte respondeu de fato.

    Notas:
        ACOR (acórdãos) é servido pelo CJF Unificada com filtro tribunais=STJ,
        que responde em ~1,3 s; o SCON entra como fallback se o CJF falhar.
        SUMU existe apenas no SCON — ali ele é tentado primeiro, por HTTP, no
        host processo.stj.jus.br (sem Cloudflare) e depois no scon.stj.jus.br.
        Cada súmula traz verbete, órgão, data, ramo e, se não vigora mais,
        tipo "Súmula (CANCELADA)"/"(REVOGADA)" com a nota em `situacao`;
        alteração de redação vai em `observacao`. INFJ vai ao portal do
        Informativo de Jurisprudência por HTTP (no SCON a base só devolve
        "Documento inválido").

        BLOQUEADO O SCON POR HTTP NOS DOIS HOSTS, A ROTA É AUTOMÁTICA: `scon-cdp` abre um Edge
        REAL (lançado fora do Playwright, com porta de depuração) e conecta-se a
        ele por CDP, atravessando o Cloudflare sem intervenção humana. Uma janela
        do navegador aparece na máquina e é reaproveitada entre chamadas.

        NÃO peça ao Defensor para "clicar no desafio": foi medido em 24/08/2026
        que o clique NÃO resolve — o Turnstile relança o desafio porque detecta o
        modo de lançamento do navegador, não a ausência de clique. A orientação
        anterior desta docstring, que mandava pedir resolução manual, estava
        errada. Racional e limites em `shared/cdp_edge.py`.

        Para desligar a rota (ambiente sem sessão gráfica, CI): DPU_CDP_DESABILITADO=1.
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
            # Resultado vazio NÃO se cacheia (31/08/2026): a rota do CJF pode
            # devolver 0 por shard do tribunal às escuras — 200, sem erro, "Total
            # 0 Documento(s)" — e gravar isso servia o falso zero pelo TTL inteiro,
            # muito depois de o portal ter voltado.
            if n <= 0:
                return saida
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

        # 0) INFJ tem portal próprio e não passa pelo SCON: lá a base devolve
        #    só "Documento inválido" (01/10/2026). Falhando o portal, não há
        #    rota alternativa que tenha as notas.
        if base == "INFJ":
            rota = "informativo"
            try:
                saida, n_results = _rota_informativo(query, tamanho, max_tokens_ementa)
                return _guardar(saida, n_results)
            except Exception as e:
                erro_final = f"Informativo: {type(e).__name__}: {str(e)[:160]}"
                return (
                    f'<erro>Base INFJ (portal do Informativo de Jurisprudência) '
                    f'indisponível. Detalhes: {erro_final}</erro>'
                )

        # 1) Rota primária. O SCON por HTTP tenta processo.stj.jus.br (sem
        #    Cloudflare) antes de scon.stj.jus.br — ver `_rota_scon_http`.
        try:
            if scon_primeiro:
                saida, n_results, host = _rota_scon_http(
                    query, base, tamanho, max_tokens_ementa
                )
                rota = "scon-processo" if host == _host_scon(SCON_PROCESSO_BASE) else "scon"
            else:
                saida, n_results = _rota_cjf(query, tamanho, max_tokens_ementa)
            return _guardar(saida, n_results)
        except Exception as e:
            rotulo = "SCON" if scon_primeiro else "CJF"
            erros.append(f"{rotulo}: {type(e).__name__}: {str(e)[:320]}")

        # 2) SCON por NAVEGADOR REAL (CDP). Entra sempre que a via HTTP do SCON
        #    falhou nos dois hosts — e é a última saída em SUMU, que o CJF não
        #    cobre.
        if scon_primeiro and cdp_edge is not None:
            try:
                rota = "scon-cdp"
                saida, n_results = _rota_scon_cdp(
                    query, base, tamanho, max_tokens_ementa
                )
                return _guardar(saida, n_results)
            except Exception as e:
                erros.append(f"SCON/CDP: {type(e).__name__}: {str(e)[:160]}")

        # 3) Fallback pela outra rota. O CJF não cobre súmula/informativo, então
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
                saida, n_results, _host = _rota_scon_http(
                    query, base, tamanho, max_tokens_ementa
                )
            return _guardar(saida, n_results)
        except Exception as e:
            rotulo = "CJF" if scon_primeiro else "SCON"
            erros.append(f"{rotulo}: {type(e).__name__}: {str(e)[:320]}")

        # 4) Último degrau em ACOR: o SCON pelo navegador real. Chega-se aqui
        #    quando CJF não tinha o julgado E o SCON recusou por HTTP — que é
        #    exatamente o caso do AgInt no AREsp 1.859.057/SP (24/08/2026).
        if not scon_primeiro and cdp_edge is not None:
            try:
                rota = "scon-cdp-fallback"
                saida, n_results = _rota_scon_cdp(
                    query, base, tamanho, max_tokens_ementa
                )
                return _guardar(saida, n_results)
            except Exception as e:
                erros.append(f"SCON/CDP: {type(e).__name__}: {str(e)[:160]}")

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
  são convertidos para a sintaxe CJF (e→E, ou→OU, nao→NAO etc.). O SCON entra
  só como fallback se o CJF falhar; para tentá-lo primeiro use forcar_scon=True.
  SUMU só existe no SCON e vai a ele primeiro, por HTTP no host
  processo.stj.jus.br (sem Cloudflare), depois scon.stj.jus.br, depois CDP.
  Súmula cancelada/revogada vem marcada no tipo e em <situacao>.
  INFJ vai ao portal do Informativo de Jurisprudência (processo.stj.jus.br).
  A resposta indica em comentário qual fonte respondeu de fato.
"""


if __name__ == "__main__":
    mcp.run()
