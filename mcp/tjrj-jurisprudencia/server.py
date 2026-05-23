"""
MCP Server: TJRJ Jurisprudência

Acesso à jurisprudência do Tribunal de Justiça do Estado do Rio de Janeiro.

Fontes:
  - eProc TJRJ (primária): https://eproc1g.tjrj.jus.br/eproc/
    Acesso programático completo. Cobre todas as decisões indexadas no eProc.
  - eJuris TJRJ (legado): https://www3.tjrj.jus.br/ejuris/
    Protegido por reCAPTCHA v3 — resolvido via Playwright (headless Chromium).
    Use buscar_jurisprudencia_ejuris() — mais lento (~15-20s), base mais ampla.
"""

from mcp.server.fastmcp import FastMCP
import requests
from bs4 import BeautifulSoup
import re
import time
from typing import Optional, List, Dict, Any, Tuple
from tenacity import retry, wait_exponential, stop_after_attempt
import sys
from pathlib import Path
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.base_juridica import (
    BaseResultadoJuridico,
    formatar_resultados_xml,
    truncar_por_tokens,
    limpar_texto_html,
)

# Onda 2 — cache HTTP (2d para TJRJ; eJuris é caro com Playwright, vale ouro).
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

mcp = FastMCP("tjrj-jurisprudencia")
_TJRJ_TTL_S = 2 * 86400

# ---------------------------------------------------------------------------
# Constantes — eProc TJRJ
# ---------------------------------------------------------------------------

EPROC_BASE = "https://eproc1g.tjrj.jus.br/eproc/"
EPROC_PESQUISAR = EPROC_BASE + "externo_controlador.php?acao=jurisprudencia@jurisprudencia/pesquisar"
EPROC_LISTAR = EPROC_BASE + "externo_controlador.php?acao=jurisprudencia@jurisprudencia/listar_resultados"
EPROC_AJAX = EPROC_BASE + "externo_controlador.php?acao=jurisprudencia@jurisprudencia/ajax_paginar_resultado"

EJURIS_URL = "https://www3.tjrj.jus.br/ejuris/ConsultarJurisprudencia.aspx"
EJURIS_RESULT_URL = "https://www3.tjrj.jus.br/ejuris/ProcessarConsJurisES.aspx"

EJURIS_TIPOS = {
    "acórdão": "AC",
    "acordao": "AC",
    "decisão": "D",
    "decisao": "D",
    "despacho": "DS",
    "sentença": "S",
    "sentenca": "S",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}


# ---------------------------------------------------------------------------
# eProc Session
# ---------------------------------------------------------------------------

class EProcSession:
    """Gerencia sessão de 3 etapas com o portal eProc TJRJ."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.session.verify = False  # eProc tem cert auto-assinado em alguns endpoints

    @retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
    def obter_sessao(self) -> None:
        """Etapa 1: GET na página de pesquisa para obter PHPSESSID."""
        resp = self.session.get(EPROC_PESQUISAR, timeout=20)
        resp.raise_for_status()

    @retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
    def submeter_busca(
        self,
        termo: str,
        campo: str = "EM",
        tipo_documento: Optional[str] = None,
        data_inicio: Optional[str] = None,
        data_fim: Optional[str] = None,
        relator: Optional[str] = None,
        orgao: Optional[str] = None,
    ) -> str:
        """
        POST do formulário de busca. Retorna o HTML completo da resposta —
        que já contém os resultados filtrados. (O endpoint AJAX
        ``ajax_paginar_resultado`` ignora o filtro e devolve a base inteira;
        por isso é evitado aqui — ver ``obter_resultados``.)
        """
        tem_filtros_avancados = any([tipo_documento, data_inicio, data_fim, relator, orgao])

        # Mapeia rótulos amigáveis para os values reais do form do eProc.
        # O portal aceita apenas "E" (ementa) e "I" (inteiro teor) para rdoCampo.
        rdo_value = {"EM": "E", "E": "E", "IT": "I", "I": "I"}.get(campo.upper(), "E")

        post_data: Dict[str, Any] = {
            "txtPesquisa": termo,
            "hdnExibirPesquisaAvancada": "1" if tem_filtros_avancados else "0",
            "rdoCampo": rdo_value,
            "chkCaput": "on",  # checkbox HTML default (era "S", inválido)
        }

        if tipo_documento:
            post_data["selTipoDocumento[]"] = tipo_documento
        if data_inicio:
            post_data["dtDecisaoInicio"] = data_inicio
            post_data["hdnDecisaoInicio"] = data_inicio
        if data_fim:
            post_data["dtDecisaoFim"] = data_fim
            post_data["hdnDecisaoFim"] = data_fim
        if relator:
            post_data["selRelator[]"] = relator
        if orgao:
            post_data["selOrgao[]"] = orgao

        resp = self.session.post(
            EPROC_LISTAR,
            data=post_data,
            headers={**HEADERS, "Referer": EPROC_PESQUISAR},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.text

    @retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
    def obter_resultados(self) -> str:
        """
        Fallback legado: GET AJAX para paginação adicional. Retornado em
        condições normais ignora o filtro da busca e devolve a base inteira;
        manter apenas para compatibilidade. Use o retorno de ``submeter_busca``.
        """
        resp = self.session.get(
            EPROC_AJAX,
            headers={
                **HEADERS,
                "Referer": EPROC_LISTAR,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=40,
        )
        resp.raise_for_status()
        return resp.text


# ---------------------------------------------------------------------------
# Parser de resultados eProc
# ---------------------------------------------------------------------------

def extrair_total(html: str) -> int:
    """Extrai total de documentos encontrados."""
    match = re.search(r"(\d[\d.]*)\s*documentos?\s*encontrados?", html, re.IGNORECASE)
    if match:
        return int(match.group(1).replace(".", ""))
    return 0


def extrair_documentos_eproc(html: str, max_resultados: int = 10) -> List[BaseResultadoJuridico]:
    """
    Extrai documentos do HTML retornado pelo ajax_paginar_resultado.

    Estrutura dos itens:
      <div id="resultado{id}" class="card mb-3 resultadoItem">
        <div class="resValueTipoJurisprudencia">Decisão monocrática</div>
        <a class="numero-processo">3000922-77.2026.8.19.0000/TJRJ</a>
        <span>AG - Agravo de Instrumento</span>
        <div class="resLabel">CAMPO</div>  <div class="resValue">VALOR</div>
        <a class="copiarCitacao" data-citacao="...(TJRJ, AG num, 17ª Câmara..., Relator..., D.E. data)">
      </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    result_divs = soup.find_all("div", id=re.compile(r"^resultado\d"))
    resultados = []

    for div in result_divs[:max_resultados]:
        doc: Dict[str, str] = {}

        # 1. Tipo de julgamento (Acórdão / Decisão monocrática)
        tipo_div = div.find("div", class_="resValueTipoJurisprudencia")
        if tipo_div:
            doc["TIPO_JULGAMENTO"] = tipo_div.get_text().strip()

        # 2. Número CNJ formatado e classe processual
        num_proc_link = div.find("a", class_="numero-processo")
        if num_proc_link:
            doc["NUMERO_FORMATADO"] = num_proc_link.get_text().strip()

        # Classe processual fica em span irmão do link número-processo
        proc_label = div.find("div", class_="resLabel", string=re.compile(r"PROCESSO", re.I))
        if proc_label:
            proc_value = proc_label.find_next_sibling("div", class_="resValue")
            if proc_value:
                spans = proc_value.find_all("span")
                for span in spans:
                    text = span.get_text().strip()
                    if text and " -" in text:
                        # Ex: "AG -   Agravo de Instrumento  "
                        doc["CLASSE"] = re.sub(r"\s+", " ", text).strip()
                        break

        # 3. Órgão julgador — extraído do data-citacao
        citacao_link = div.find("a", class_="copiarCitacao")
        if citacao_link:
            citacao = citacao_link.get("data-citacao", "")
            # Padrão: (TJRJ, AG num,   17ª Câmara de Direito Privado  , Relator ...)
            organ_match = re.search(
                r"TJRJ,\s*[^,]+,\s*([^,]+(?:Câmara|Turma|Seção|Grupo|Conselho|Órgão)[^,]*),",
                citacao, re.IGNORECASE
            )
            if organ_match:
                doc["ÓRGÃO JULGADOR"] = re.sub(r"\s+", " ", organ_match.group(1)).strip()

        # 4. Pares resLabel/resValue
        labels = div.find_all("div", class_="resLabel")
        for label in labels:
            value_div = label.find_next_sibling("div", class_="resValue")
            if value_div:
                campo = label.get_text().strip().upper()
                valor = limpar_texto_html(str(value_div))
                doc[campo] = valor

        # 5. Número do processo via link de consulta processual (fallback)
        proc_link = div.find("a", class_="consultaProcessual")
        if proc_link:
            data_link = proc_link.get("data-link", "")
            num_match = re.search(r"num_processo=(\d+)", data_link)
            if num_match:
                doc.setdefault("NUMERO_RAW", num_match.group(1))

        # Montar resultado padronizado
        ementa = doc.get("EMENTA", doc.get("DECISÃO", ""))
        ementa = truncar_por_tokens(ementa, max_tokens=700)

        # Número preferencial: formatado (com pontos e traços) > raw
        numero = doc.get("NUMERO_FORMATADO", doc.get("NUMERO_RAW", ""))

        resultado = BaseResultadoJuridico(
            conteudo=ementa,
            fonte="TJRJ/eProc",
            tipo=doc.get("TIPO_JULGAMENTO", doc.get("CLASSE", "")),
            orgao=doc.get("ÓRGÃO JULGADOR", ""),
            numero=numero,
            relator=doc.get("RELATOR", "").strip(),
            data=doc.get("DATA DO JULGAMENTO", ""),
            extra={
                "classe": doc.get("CLASSE", ""),
                "data_publicacao": doc.get("DATA DA PUBLICAÇÃO", ""),
                "uf": doc.get("UF", "RJ"),
            },
        )
        resultados.append(resultado)

    return resultados


# ---------------------------------------------------------------------------
# eJuris — Parser de resultados
# ---------------------------------------------------------------------------

def extrair_total_ejuris(html: str) -> int:
    """Extrai total de resultados da página eJuris."""
    # Padrão: "Relação de 1 até 10 de 29001"
    match = re.search(r"Rela[çc][aã]o de \d+ at[eé] \d+ de (\d[\d.]*)", html, re.IGNORECASE)
    if match:
        return int(match.group(1).replace(".", ""))
    # Padrão alternativo: "Foram encontrados N..."
    match2 = re.search(r"Foram encontrados?\s+(\d[\d.]*)", html, re.IGNORECASE)
    if match2:
        return int(match2.group(1).replace(".", ""))
    # Fallback: "Total: N"
    match3 = re.search(r"Total[:\s]+(\d+)", html, re.IGNORECASE)
    if match3:
        return int(match3.group(1))
    return 0


def extrair_documentos_ejuris(html: str, max_resultados: int = 10) -> List[BaseResultadoJuridico]:
    """
    Extrai documentos do HTML retornado pelo eJuris.

    Estrutura por resultado:
      <tr><td>Des(a). NOME - Julgamento: DD/MM/YYYY - NOME DA CÂMARA</td></tr>
      <tr><td><div class="divEmenta" id="divementa_N">
        <textarea>Ementa: TEXTO...</textarea>
      </div></td></tr>
    """
    soup = BeautifulSoup(html, "html.parser")
    ementa_divs = soup.find_all("div", class_="divEmenta")
    resultados = []

    for ementa_div in ementa_divs[:max_resultados]:
        # Ementa: texto dentro do <textarea>
        textarea = ementa_div.find("textarea")
        ementa_text = ""
        if textarea:
            ementa_text = textarea.get_text().strip()
            if ementa_text.lower().startswith("ementa:"):
                ementa_text = ementa_text[7:].strip()

        # Metadados: linha imediatamente anterior na tabela
        relator = ""
        data_julg = ""
        orgao = ""

        parent_td = ementa_div.parent
        parent_tr = parent_td.parent if parent_td else None
        if parent_tr:
            prev_tr = parent_tr.find_previous_sibling("tr")
            if prev_tr:
                meta_text = prev_tr.get_text(" - ").strip()
                # Limpa espaços múltiplos
                meta_text = re.sub(r"\s{2,}", " ", meta_text)
                # Divide nos separadores " - "
                parts = [p.strip() for p in re.split(r"\s+-\s+", meta_text) if p.strip()]

                # Parte 0: relator (pode ter prefixo "Des(a).", "Dr(a).", etc.)
                if parts:
                    relator = re.sub(r"^Des(?:\(a\))?\.?\s*|^Dr(?:\(a\))?\.?\s*", "", parts[0], flags=re.IGNORECASE).strip()

                # Parte 1: "Julgamento: DD/MM/YYYY"
                for part in parts[1:]:
                    data_match = re.search(r"Julgamento[:\s]+(\d{2}/\d{2}/\d{4})", part, re.IGNORECASE)
                    if data_match:
                        data_julg = data_match.group(1)
                        break

                # Parte 2+: órgão julgador (tudo após a data)
                if len(parts) >= 3:
                    # O órgão é o último segmento com indicação de câmara/turma/seção
                    for part in reversed(parts[2:]):
                        if re.search(r"CAMARA|CÂMARA|TURMA|SEÇÃO|SECAO|ORGAO|ÓRGÃO", part, re.IGNORECASE):
                            orgao = part.title()
                            break
                    if not orgao and len(parts) >= 3:
                        orgao = parts[-1].title()

        ementa_text = truncar_por_tokens(ementa_text, max_tokens=700)

        resultado = BaseResultadoJuridico(
            conteudo=ementa_text,
            fonte="TJRJ/eJuris",
            tipo="",
            orgao=orgao,
            numero="",
            relator=relator,
            data=data_julg,
            extra={"base": "eJuris"},
        )
        resultados.append(resultado)

    return resultados


# ---------------------------------------------------------------------------
# eJuris — Playwright async (contorna reCAPTCHA v3)
# ---------------------------------------------------------------------------

async def _buscar_ejuris_async(
    busca: str,
    max_resultados: int = 10,
    ano_inicio: str = "",
    ano_fim: str = "",
    tipo_documento: str = "",
) -> Tuple[List[BaseResultadoJuridico], int]:
    """Executa busca no eJuris via headless Chromium (Playwright)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            page = await browser.new_page()
            await page.goto(EJURIS_URL, timeout=30000, wait_until="domcontentloaded")

            # Preenche campo de busca
            await page.fill("#ContentPlaceHolder1_txtTextoPesq", busca)

            # Filtros de ano (select options com valores "2000", "2001", ..., "2026")
            if ano_inicio:
                try:
                    await page.select_option("#ContentPlaceHolder1_cmbAnoInicio", ano_inicio)
                except Exception:
                    pass
            if ano_fim:
                try:
                    await page.select_option("#ContentPlaceHolder1_cmbAnoFim", ano_fim)
                except Exception:
                    pass

            # Tipo de documento (se fornecido)
            if tipo_documento:
                codigo = EJURIS_TIPOS.get(tipo_documento.lower(), "")
                if codigo:
                    try:
                        await page.select_option("#ContentPlaceHolder1_cmbTipoDecisao", codigo)
                    except Exception:
                        pass

            # Submete o formulário e aguarda redirect para ProcessarConsJurisES.aspx
            await page.click("#ContentPlaceHolder1_btnPesquisar")
            await page.wait_for_url(re.compile(r"ProcessarConsJurisES"), timeout=25000)

            # Conteúdo é renderizado dinamicamente após o redirect — aguarda primeiro resultado
            # state="attached" porque os divEmenta podem ter display:none (textarea oculto)
            try:
                await page.wait_for_selector("div.divEmenta", state="attached", timeout=15000)
            except Exception:
                pass  # Pode não haver resultados

            html = await page.content()

            # Total é renderizado por JS — extrair via evaluate (não está no HTML cru)
            total = 0
            try:
                body_text = await page.evaluate("() => document.body.innerText.substring(0, 1000)")
                m = re.search(r"Rela[çc][aã]o de \d+ at[eé] \d+ de (\d[\d.]*)", body_text, re.IGNORECASE)
                if m:
                    total = int(m.group(1).replace(".", ""))
            except Exception:
                pass

            resultados = extrair_documentos_ejuris(html, max_resultados=max_resultados)

            # Paginação: PageSeq é base-0 (página 1 = PageSeq=0, página 2 = PageSeq=1)
            # A URL base da sessão (com Version e outros params) é preservada navegando
            # para próximas páginas clicando no link de próxima página ou alterando PageSeq
            page_seq = 1
            while len(resultados) < max_resultados and len(resultados) < total:
                current_url = page.url
                next_url = re.sub(r"PageSeq=\d+", f"PageSeq={page_seq}", current_url)
                if next_url == current_url:
                    break
                try:
                    await page.goto(next_url, timeout=20000, wait_until="domcontentloaded")
                    try:
                        await page.wait_for_selector("div.divEmenta", state="attached", timeout=10000)
                    except Exception:
                        break
                    html_extra = await page.content()
                    novos = extrair_documentos_ejuris(
                        html_extra,
                        max_resultados=max_resultados - len(resultados),
                    )
                    if not novos:
                        break
                    resultados.extend(novos)
                    page_seq += 1
                except Exception:
                    break

            return resultados, total

        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def buscar_jurisprudencia_tjrj(
    busca: str,
    campo: str = "EM",
    max_resultados: int = 10,
    data_inicio: str = "",
    data_fim: str = "",
    tipo_documento: str = "",
) -> str:
    """
    Busca jurisprudência do TJRJ via eProc (Tribunal de Justiça do Estado do Rio de Janeiro).
    Cobre acórdãos, decisões monocráticas e demais decisões indexadas no eProc TJRJ.
    Use ajuda_sintaxe_tjrj() para guia de operadores e parâmetros.

    SINTAXE RÁPIDA (operadores em minúsculo):
    e, ou, não, prox; aspas para frase exata; * para prefixo.

    Args:
        busca: Termo de busca. Suporta operadores: e, ou, não, prox, "frase exata", "prefixo*".
               Ex: "plano de saude" e fornecimento
               Ex: "obrigacao de fazer" e medicamento e tutela
        campo: Campo de busca — "EM" (ementa, padrão) ou "IT" (inteiro teor).
        max_resultados: Máximo de resultados (1-100, padrão: 10).
        data_inicio: Data início do julgamento — DD/MM/AAAA. Filtro best-effort (requer JS no portal).
        data_fim: Data fim do julgamento — DD/MM/AAAA. Filtro best-effort (requer JS no portal).
        tipo_documento: Tipo de documento. Ex: "Acórdão", "Decisão monocrática".
                        Deixar em branco para todos os tipos.

    Returns:
        XML estruturado com: ementa, relator, órgão julgador, data do julgamento,
        data da publicação, classe, número do processo.
    """
    t0 = time.perf_counter()
    cache_hit = False
    n_results = 0
    erro_final: Optional[str] = None
    try:
        cache_key = {
            "mcp": "tjrj-jurisprudencia", "fonte": "eproc",
            "busca": busca, "campo": campo, "max_resultados": max_resultados,
            "data_inicio": data_inicio, "data_fim": data_fim, "tipo_documento": tipo_documento,
        }
        cached = cached_http("tjrj-jurisprudencia", cache_key)
        if cached is not None:
            cache_hit = True
            import json as _json
            payload = _json.loads(cached)
            n_results = payload.get("n", 0)
            return payload["xml"]

        sess = EProcSession()
        sess.obter_sessao()
        html = sess.submeter_busca(
            termo=busca,
            campo=campo,
            tipo_documento=tipo_documento or None,
            data_inicio=data_inicio or None,
            data_fim=data_fim or None,
        )

        total = extrair_total(html)
        documentos = extrair_documentos_eproc(html, max_resultados=max_resultados)
        n_results = len(documentos)

        xml = formatar_resultados_xml(documentos, "jurisprudencia_tjrj")
        meta = f'<!-- TJRJ/eProc | Busca: "{busca}" | Campo: {campo} | Total encontrado: {total} | Exibindo: {len(documentos)} -->\n'
        saida = meta + xml
        try:
            import json as _json
            registrar_dispositivo(
                "tjrj-jurisprudencia", cache_key,
                _json.dumps({"xml": saida, "n": n_results}), ttl_s=_TJRJ_TTL_S,
            )
        except Exception:
            pass
        return saida

    except Exception as e:
        erro_final = str(e)
        return f'<erro>Falha na busca TJRJ/eProc: {str(e)}</erro>'
    finally:
        log_query(
            mcp="tjrj-jurisprudencia",
            tool="buscar_jurisprudencia_tjrj",
            query=busca,
            filtros={"campo": campo, "tipo_documento": tipo_documento,
                     "data_inicio": data_inicio, "data_fim": data_fim},
            n_resultados=n_results,
            ms=int((time.perf_counter() - t0) * 1000),
            cache_hit=cache_hit,
            erro=erro_final,
        )


@mcp.tool()
async def buscar_jurisprudencia_ejuris(
    busca: str,
    max_resultados: int = 10,
    ano_inicio: str = "",
    ano_fim: str = "",
    tipo_documento: str = "",
) -> str:
    """
    Busca jurisprudência do TJRJ via eJuris (portal legado, base histórica ampla).
    Usa navegação headless (Playwright/Chromium) para contornar reCAPTCHA v3.

    Mais lento que buscar_jurisprudencia_tjrj (~15-25s), mas com cobertura distinta —
    acórdãos e decisões que podem não estar indexados no eProc.

    QUANDO USAR: complementar ao eProc, especialmente para:
    - decisões mais antigas (pré-eProc)
    - jurisprudência cível do TJRJ em geral

    Args:
        busca: Termo de busca. Suporta E, OU, NÃO e aspas para frase exata.
               Ex: "plano de saude" E fornecimento E negativa
               Ex: "dano moral" E "cobrança indevida"
        max_resultados: Máximo de resultados (1-50, padrão: 10). Paginação automática.
        ano_inicio: Ano inicial (AAAA). Ex: "2022". Vazio = sem limite inferior.
        ano_fim: Ano final (AAAA). Ex: "2025". Vazio = sem limite superior.
        tipo_documento: Tipo do documento. Aceita: "Acórdão", "Decisão", "Despacho",
                        "Sentença". Vazio = todos os tipos.

    Returns:
        XML estruturado com: ementa, relator, órgão julgador, data do julgamento.
        Número do processo não disponível nesta base.
    """
    t0 = time.perf_counter()
    cache_hit = False
    n_results = 0
    erro_final: Optional[str] = None
    try:
        cache_key = {
            "mcp": "tjrj-jurisprudencia", "fonte": "ejuris",
            "busca": busca, "max_resultados": max_resultados,
            "ano_inicio": ano_inicio, "ano_fim": ano_fim, "tipo_documento": tipo_documento,
        }
        cached = cached_http("tjrj-jurisprudencia", cache_key)
        if cached is not None:
            cache_hit = True
            import json as _json
            payload = _json.loads(cached)
            n_results = payload.get("n", 0)
            return payload["xml"]

        resultados, total = await _buscar_ejuris_async(
            busca, max_resultados, ano_inicio, ano_fim, tipo_documento
        )
        n_results = len(resultados)
        xml = formatar_resultados_xml(resultados, "jurisprudencia_ejuris")
        meta = (
            f'<!-- TJRJ/eJuris | Busca: "{busca}"'
            f' | Total encontrado: {total}'
            f' | Exibindo: {len(resultados)} -->\n'
        )
        saida = meta + xml
        try:
            import json as _json
            registrar_dispositivo(
                "tjrj-jurisprudencia", cache_key,
                _json.dumps({"xml": saida, "n": n_results}), ttl_s=_TJRJ_TTL_S,
            )
        except Exception:
            pass
        return saida
    except Exception as e:
        erro_final = str(e)
        return f'<erro>Falha na busca TJRJ/eJuris: {str(e)}</erro>'
    finally:
        log_query(
            mcp="tjrj-jurisprudencia",
            tool="buscar_jurisprudencia_ejuris",
            query=busca,
            filtros={"ano_inicio": ano_inicio, "ano_fim": ano_fim, "tipo_documento": tipo_documento},
            n_resultados=n_results,
            ms=int((time.perf_counter() - t0) * 1000),
            cache_hit=cache_hit,
            erro=erro_final,
        )


@mcp.tool()
def ajuda_sintaxe_tjrj() -> str:
    """
    Retorna guia completo de sintaxe, operadores e exemplos para buscar_jurisprudencia_tjrj.
    Também documenta as limitações e fontes disponíveis.
    """
    return """
TJRJ JURISPRUDÊNCIA — Guia de Uso

═══════════════════════════════════════════════════════════
FERRAMENTA 1: buscar_jurisprudencia_tjrj  (eProc TJRJ)
═══════════════════════════════════════════════════════════
Rápida (~3-5s). Sem CAPTCHA. Base: acórdãos e decisões do eProc.
Retorna: ementa, relator, órgão, data, número CNJ, classe.

FERRAMENTA 2: buscar_jurisprudencia_ejuris  (eJuris TJRJ)
═══════════════════════════════════════════════════════════
Lenta (~15-25s). Usa Playwright para contornar reCAPTCHA v3.
Base: acórdãos e decisões legadas do eJuris (cobertura histórica mais ampla).
Retorna: ementa, relator, órgão, data. (sem número CNJ)

ESTRATÉGIA RECOMENDADA:
1. Iniciar com buscar_jurisprudencia_tjrj (mais rápida)
2. Complementar com buscar_jurisprudencia_ejuris se cobertura insuficiente

═══════════════════════════════
OPERADORES DE BUSCA (eProc)
═══════════════════════════════
Os operadores são em minúsculo (diferente do CJF que usa maiúsculas):

  e        — ambos obrigatórios        plano de saude e fornecimento
  ou       — qualquer um               acórdão ou decisão
  não      — exclui segundo            medicamento não federal
  prox     — palavras próximas         saude prox tratamento

FRASE EXATA:
  "..."    — pesquisa a expressão      "plano de saude"
  "...*"   — prefixo com wildcard      "embarg*"

CAMPO DE BUSCA (parâmetro campo):
  "EM"  — Ementa (padrão, mais rápido, recomendado)
  "IT"  — Inteiro teor (mais abrangente, mais lento)

═══════════════════════════════
TIPOS DE DOCUMENTO
═══════════════════════════════
Passar no parâmetro tipo_documento:
  "Acórdão"              — decisões colegiadas
  "Decisão monocrática"  — decisões individuais
  "" (vazio)             — todos os tipos (padrão)

═══════════════════════════════
FILTROS DE DATA
═══════════════════════════════
Formato: DD/MM/AAAA
  data_inicio="01/01/2023"
  data_fim="31/12/2025"

═══════════════════════════════
ESTRATÉGIA DE BUSCA
═══════════════════════════════
1. Comece com termos da ementa (campo="EM")
2. Use aspas para termos compostos: "plano de saude"
3. Combine com "e": "plano de saude" e fornecimento
4. Filtre por data para decisões recentes
5. Se poucos resultados, troque para campo="IT"

═══════════════════════════════
EXEMPLOS PRONTOS — DPE Cível
═══════════════════════════════
Saúde pública / SUS:
  "obrigacao de fazer" e medicamento e municipio
  "tutela de urgencia" e "direito a saude" e crianca
  "plano de saude" e "negativa de cobertura" e dano moral

Família:
  "guarda unilateral" e "melhor interesse" e crianca
  alimentos e "ex-companheiro" e arbitramento
  divorcio e "partilha de bens"

Consumidor:
  "superendividamento" e repactuacao
  "dano moral" e "cobrança indevida" e "águas do rio"
  CDC e "continuidade do servico" e "corte de agua"

Plano de saúde:
  "plano de saude" e TEA e "tratamento multidisciplinar"
  "home care" e "plano de saude" e negativa
  "cirurgia" e "plano de saude" e "demora" e "dano moral"

═══════════════════════════════
METADADOS RETORNADOS
═══════════════════════════════
  <numero>     — número CNJ do processo
  <tipo>       — classe processual (AC, AI, MS, etc.)
  <orgao>      — câmara/turma julgadora
  <relator>    — nome do(a) relator(a)
  <data>       — data do julgamento
  <conteudo>   — texto da ementa (truncado a ~700 tokens)
  <extra>
    <data_publicacao>  — data da publicação
    <uf>               — UF (sempre RJ)
  </extra>
"""


if __name__ == "__main__":
    mcp.run()
