"""
MCP Server: CJF Jurisprudência Unificada

Acesso à jurisprudência unificada do Conselho da Justiça Federal.
Inclui decisões de: STF, STJ, TRF1, TRF2, TRF3, TRF4, TRF5, TRF6.
"""

from mcp.server.fastmcp import FastMCP
import requests
from bs4 import BeautifulSoup
import re
import time
from typing import Optional, List, Dict, Any
from datetime import datetime
import html
from tenacity import retry, wait_exponential, stop_after_attempt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.base_juridica import (
    BaseResultadoJuridico,
    formatar_resultados_xml,
    truncar_por_tokens,
    limpar_texto_html,
    TRIBUNAIS,
)

# Onda 2 — cache HTTP de respostas (TTL 7d para CJF).
try:
    from shared.cache_http import cached_http, registrar_dispositivo  # type: ignore
except ImportError:
    def cached_http(*_a, **_kw):  # type: ignore
        return None

    def registrar_dispositivo(*_a, **_kw) -> None:  # type: ignore
        pass

# Onda 1 — logger estruturado de queries (C4 / B3).
try:
    _DPU_SCRIPTS = Path.home() / ".claude" / "DPU" / "Scripts"
    if str(_DPU_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_DPU_SCRIPTS))
    from query_logger import log_query  # type: ignore
except ImportError:
    def log_query(**_kwargs: Any) -> None:  # type: ignore
        pass

mcp = FastMCP("cjf-jurisprudencia")

# C3 (Onda 1) — ViewState com TTL de 25 min.
# JSF do CJF mantém ViewState por ~30 min (configuração JSF padrão). Renovar
# antes (margem de 5 min) reduz risco de erro "ViewState expired".
_VIEWSTATE_TTL_S = 25 * 60

CJF_URL = "https://jurisprudencia.cjf.jus.br/unificada/index.xhtml"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/xml, text/xml, */*; q=0.01",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Faces-Request": "partial/ajax",
    "X-Requested-With": "XMLHttpRequest"
}


class CJFSession:
    """Gerencia sessão com o portal CJF.

    Onda 1 — C3: ViewState passou a ter TTL de 25 min via _get_shared_session().
    A instância em si reusa requests.Session() (TCP keep-alive) e só refaz o
    GET inicial quando o ViewState expira.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": HEADERS["User-Agent"],
            "Accept-Language": HEADERS["Accept-Language"]
        })
        self.viewstate = None
        self._fetched_at: float = 0.0

    @property
    def viewstate_is_fresh(self) -> bool:
        return bool(self.viewstate) and (time.monotonic() - self._fetched_at) < _VIEWSTATE_TTL_S

    @retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
    def obter_viewstate(self) -> str:
        """Obtém ViewState da página inicial."""
        resp = self.session.get(CJF_URL, timeout=30)
        resp.raise_for_status()

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

    @retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
    def buscar(self, termo: str, tribunais: List[str]) -> str:
        """Faz busca e retorna HTML dos resultados."""
        if not self.viewstate_is_fresh:
            self.obter_viewstate()

        form_data = []
        form_data.append(("javax.faces.partial.ajax", "true"))
        form_data.append(("javax.faces.source", "formulario:actPesquisar"))
        form_data.append(("javax.faces.partial.execute", "@all"))
        form_data.append(("javax.faces.partial.render", "formulario:resultado"))
        form_data.append(("formulario:actPesquisar", "formulario:actPesquisar"))
        form_data.append(("formulario", "formulario"))
        form_data.append(("formulario:textoLivre", termo))

        for trib in tribunais:
            form_data.append(("formulario:j_idt51", trib))

        form_data.append(("javax.faces.ViewState", self.viewstate))

        resp = self.session.post(
            CJF_URL,
            data=form_data,
            headers=HEADERS,
            timeout=60
        )
        resp.raise_for_status()
        return resp.text


def extrair_totais(html_content: str) -> Dict[str, int]:
    """Extrai totais de documentos por tribunal."""
    totais = {}
    pattern = r'<td[^>]*>(\w+)</td>\s*<td[^>]*>.*?(\d+)\s*Documento'
    matches = re.findall(pattern, html_content, re.DOTALL)

    for tribunal, count in matches:
        if tribunal in TRIBUNAIS:
            totais[tribunal] = int(count)

    return totais


def extrair_documentos(html_content: str) -> List[Dict[str, Any]]:
    """Extrai documentos detalhados da resposta."""
    documentos = []
    content = html.unescape(html_content)

    cdata_matches = re.findall(r'<!\[CDATA\[(.*?)\]\]>', content, re.DOTALL)
    if cdata_matches:
        content = ''.join(cdata_matches)

    doc_indices = set(re.findall(r'tabelaDocumentos:(\d+):', content))

    for idx in sorted(doc_indices, key=int):
        doc = {"indice": int(idx)}

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


# C3 — singleton de CJFSession com TTL embutido na própria instância.
_CJF_SHARED: Optional["CJFSession"] = None


def _get_cjf_session() -> "CJFSession":
    """Retorna a CJFSession compartilhada; cria nova se inexistente.

    O ViewState dentro da sessão tem TTL próprio (25 min) — não precisa recriar
    a instância só por ele ter expirado, basta deixar buscar() chamar
    obter_viewstate() de novo. Mas se a sessão acumula muito tempo sem uso
    (>1 h), recriamos para evitar cookies stale.
    """
    global _CJF_SHARED
    if _CJF_SHARED is None:
        _CJF_SHARED = CJFSession()
        return _CJF_SHARED
    # Recria se o ViewState está MUITO velho (sinal de sessão idle).
    if _CJF_SHARED._fetched_at and (time.monotonic() - _CJF_SHARED._fetched_at) > 3600:
        try:
            _CJF_SHARED.session.close()
        except Exception:
            pass
        _CJF_SHARED = CJFSession()
    return _CJF_SHARED


@mcp.tool()
def buscar_jurisprudencia_cjf(
    busca: str,
    tribunais: str = "STF,STJ,TRF1,TRF2,TRF3,TRF4,TRF5,TRF6",
    max_resultados: int = 10
) -> str:
    """
    Busca jurisprudência unificada no portal CJF — inclui STF, STJ e todos os TRFs.
    Use ajuda_sintaxe_cjf() para guia completo de operadores e exemplos.

    SINTAXE RÁPIDA (operadores em MAIÚSCULO):
    E, OU, NAO, ADJ[n], PROX[n], COM, MESMO; campos: termo[EMEN], termo[REL], etc.
    Wildcards: $ (sufixo), ? (1 char).

    TRIBUNAIS: STF, STJ, TRF1, TRF2, TRF3, TRF4, TRF5, TRF6

    Args:
        busca: Query com sintaxe CJF (operadores MAIÚSCULOS, campos [EMEN], etc).
               NÃO passe perguntas diretas. Use a estratégia de busca.
        tribunais: Tribunais separados por vírgula. Default: todos
        max_resultados: Máximo de resultados (1-100). Default: 10

    Returns:
        XML estruturado com documentos: ementa, relator, órgão julgador, data
    """
    lista_tribunais = [t.strip().upper() for t in tribunais.split(",")]

    t0 = time.perf_counter()
    cache_hit = False
    n_docs = 0
    erro_msg: Optional[str] = None
    try:
        # A1 (Onda 2) — cache HTTP: chave inclui query + tribunais.
        cache_key = {"mcp": "cjf-jurisprudencia", "busca": busca, "tribunais": sorted(lista_tribunais)}
        cached = cached_http("cjf-jurisprudencia", cache_key)
        if cached is not None:
            cache_hit = True
            html_resultado = cached
        else:
            session = _get_cjf_session()
            try:
                html_resultado = session.buscar(busca, lista_tribunais)
            except Exception:
                # ViewState expirado no servidor → força refresh e tenta de novo.
                session.viewstate = None
                session._fetched_at = 0.0
                html_resultado = session.buscar(busca, lista_tribunais)
            registrar_dispositivo("cjf-jurisprudencia", cache_key, html_resultado, ttl_s=7 * 86400)

        totais = extrair_totais(html_resultado)
        docs_extraidos = extrair_documentos(html_resultado)
        documentos = docs_extraidos[:max_resultados]
        n_docs = len(documentos)

        # Canário estrutural: se o portal reporta documentos mas o parser
        # extraiu zero, o HTML/JSF do CJF provavelmente mudou (ex.: id
        # autogerado j_idt51, reordenação de <td>). Falha LOUD em vez de
        # devolver vazio silencioso, que seria indistinguível de "nada
        # encontrado" e mascararia um parser quebrado.
        total_portal = sum(totais.values())
        if total_portal > 0 and not docs_extraidos:
            raise RuntimeError(
                f"CJF reportou {total_portal} documento(s) mas o parser extraiu 0 "
                "— provável mudança no HTML do portal. Verificar regex de "
                "extrair_documentos/j_idt51 antes de confiar em 'nada encontrado'."
            )

        resultados: List[BaseResultadoJuridico] = []

        for doc in documentos:
            ementa = doc.get("ementa", "")
            ementa = truncar_por_tokens(ementa, max_tokens=600)

            resultado = BaseResultadoJuridico(
                conteudo=ementa,
                fonte="",
                tipo=doc.get("classe", ""),
                orgao=doc.get("tribunal", ""),
                numero=doc.get("numero", ""),
                relator=doc.get("relator", ""),
                data=doc.get("data_julgamento", ""),
            )
            resultados.append(resultado)

        xml_resultado = formatar_resultados_xml(resultados, "jurisprudencia_cjf")

        totais_str = ", ".join([f"{k}:{v}" for k, v in totais.items()])
        meta = f'<!-- Busca: "{busca}" | Totais: {totais_str} -->\n'

        return meta + xml_resultado

    except Exception as e:
        erro_msg = str(e)
        return f'<erro>Falha na busca CJF: {str(e)}</erro>'
    finally:
        log_query(
            mcp="cjf-jurisprudencia",
            tool="buscar_jurisprudencia_cjf",
            query=busca,
            filtros={"tribunais": tribunais, "max_resultados": max_resultados},
            n_resultados=n_docs,
            ms=int((time.perf_counter() - t0) * 1000),
            cache_hit=cache_hit,
            erro=erro_msg,
        )


@mcp.tool()
def ajuda_sintaxe_cjf() -> str:
    """
    Retorna guia completo de sintaxe, operadores e exemplos para buscar_jurisprudencia_cjf.
    Consulte antes de formular queries complexas. Esta é a base mais poderosa.
    """
    return """
SINTAXE CJF — Operadores em PORTUGUÊS e MAIÚSCULO

OPERADORES BOOLEANOS:
  E     — ambos obrigatórios          pensão E morte
  OU    — qualquer um                 aposentadoria OU benefício
  NAO   — exclui segundo              servidor NAO militar
  XOU   — um ou outro, não ambos      pensão XOU aposentadoria

OPERADORES DE PROXIMIDADE:
  ADJ[n]  — adjacentes NA ordem, até n palavras    Repartição ADJ Pública
  PROX[n] — próximos QUALQUER ordem, até n         aposentadoria PROX3 invalidez
  COM     — na mesma SENTENÇA                      pensão COM dependente
  MESMO   — no mesmo PARÁGRAFO                     benefício MESMO previdenciário

NEGAÇÃO COMPOSTA: NAO ADJ[n], NAO PROX[n], NAO COM, NAO MESMO

BUSCA POR CAMPO:
  termo[EMEN]  — ementa          aposentadoria[EMEN]
  termo[DECI]  — decisão         procedente[DECI]
  termo[REL]   — relator         Silva[REL]
  termo[TRIB]  — tribunal        STJ[TRIB]
  termo[ORGA]  — órgão julgador  "primeira turma"[ORGA]
  termo[REFL]  — legislação      Lei-8112[REFL]
  termo[INDE]  — indexação       previdenciário[INDE]
  termo[ITEO]  — inteiro teor    "dano moral"[ITEO]
  20240315[DTDP] — data decisão
  202401$[DTPP]  — data publicação

WILDCARDS:
  $      — qualquer sufixo          aposentad$ → aposentadoria, etc
  $[n]   — máximo n caracteres      A$3Z
  ?      — exatamente 1 caractere   MA?? → MAIO, MAPA

ESTRATÉGIA:
  1. Identifique o instituto jurídico central
  2. Use [EMEN] para ementas, [INDE] para indexação
  3. Liste sinônimos com OU
  4. Adicione qualificadores com E
  5. Use PROX/ADJ quando a relação entre termos importa

EXEMPLOS:
  (pensão E morte)[EMEN] E (homoafetivo OU "mesmo sexo")[EMEN]
  "auxílio-doença"[EMEN] E (cessação OU indeferimento) E perícia
  Fux[REL] E previdenciário[INDE]
  (BPC OU LOAS)[EMEN] E 2024$[DTDP]
  "aposentadoria especial"[EMEN] E EPI PROX3 neutralização

REGRAS:
  - NÃO usar preposições, conjunções, artigos como termos de busca
  - NÃO usar pontuação (exceto aspas para frase exata)
  - Case insensitive: maiúsculas = minúsculas nos termos
  - Acentos ignorados: aposentadoria = aposentadória

PRIORIDADE DE TRIBUNAIS:
  STF  — questões constitucionais, repercussão geral
  STJ  — uniformização de lei federal, repetitivos
  TRF4 — referência em direito previdenciário
  TRF1 — grande volume, Brasília
  TRF3 — São Paulo, grande volume
"""


if __name__ == "__main__":
    mcp.run()
