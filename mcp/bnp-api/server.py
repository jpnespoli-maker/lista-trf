"""
MCP Server: BNP API - Banco Nacional de Precedentes (PAGEA/CNJ)

Acesso ao Banco Nacional de Precedentes do CNJ.
Precedentes vinculantes: RG, RR, SV, Súmulas, IRDRs, IACs.
"""

from mcp.server.fastmcp import FastMCP
import requests
import time
from typing import Optional, List, Any
from tenacity import retry, wait_exponential, stop_after_attempt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.base_juridica import (
    BaseResultadoJuridico,
    formatar_resultados_xml,
    truncar_por_tokens,
    sanitizar_comentario_xml,
    TIPOS_PRECEDENTES,
)
from shared.relaxamento import AVISO_RELAXADA, relaxar_bnp

# Onda 2 — cache HTTP (TTL 30d para BNP — vinculantes mudam pouco).
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

mcp = FastMCP("bnp-api")

# TTL: vinculantes mudam pouco — 30 dias é seguro.
_BNP_TTL_S = 30 * 86400

BNP_API_URL = "https://pangeabnp.pdpj.jus.br/api/v1/precedentes"


class BNPApi:
    """Cliente da API do BNP com retry automático."""

    @retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
    def buscar(self, filtro: dict) -> dict:
        """Executa busca com retry automático."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        response = requests.post(
            BNP_API_URL,
            json={"filtro": filtro},
            headers=headers,
            timeout=30
        )
        response.raise_for_status()
        return response.json()


_api = BNPApi()


@mcp.tool()
def buscar_precedentes(
    busca: str,
    orgaos: str = "STF,STJ",
    tipos: str = "RG,RR,SV,SUM",
    max_resultados: int = 10,
    data_desde: str = "",
    data_ate: str = "",
    pagina: int = 1,
    incluir_cancelados: bool = False,
    max_tokens_ementa: int = 700,
) -> str:
    """
    Busca precedentes vinculantes no Banco Nacional de Precedentes (BNP/PAGEA/CNJ).
    Use ajuda_sintaxe_bnp() para guia completo de operadores e exemplos.

    SINTAXE RÁPIDA: +termo (obrigatório), -termo (excluído), "frase exata".
    NÃO usar operadores E/OU/NAO — não funcionam nesta base.

    TIPOS: RG (Repercussão Geral) | RR (Repetitivo) | SV (Súmula Vinculante) |
           SUM (Súmula) | IRDR | IAC
    ÓRGÃOS: STF, STJ, TST, TSE, STM, TRFs, TJs

    Args:
        busca: Query com sintaxe BNP (+termo, -termo, "frase").
               NÃO passe perguntas diretas.
        orgaos: Órgãos separados por vírgula. Default: "STF,STJ"
        tipos: Tipos de precedente. Default: "RG,RR,SV,SUM"
        max_resultados: Máximo de resultados (1-50). Default: 10
        data_desde: Filtra por última atualização a partir desta data,
                    formato DD/MM/AAAA (ISO é ignorado pelo portal). Vazio = sem filtro.
        data_ate: Filtra por última atualização até esta data (DD/MM/AAAA).
        pagina: Página de resultados (1-based). Default: 1.
        incluir_cancelados: Inclui precedentes cancelados/superados. Default: False.
        max_tokens_ementa: Truncamento do conteúdo em tokens (50-4000). Default: 700.
                           Aumente para obter tese/questão mais longa.

    Returns:
        XML estruturado com precedentes: número, tese, questão jurídica, situação
    """
    lista_orgaos = [o.strip().upper() for o in orgaos.split(",")]
    lista_tipos = [t.strip().upper() for t in tipos.split(",")]
    max_tokens_ementa = max(50, min(int(max_tokens_ementa), 4000))

    filtro = {
        "buscaGeral": busca,
        "todasPalavras": "",
        "quaisquerPalavras": "",
        "semPalavras": "",
        "trechoExato": "",
        "atualizacaoDesde": data_desde,
        "atualizacaoAte": data_ate,
        "cancelados": bool(incluir_cancelados),
        "ordenacao": "Text",
        "nr": "",
        "pagina": max(1, int(pagina)),
        "tamanhoPagina": min(max_resultados, 50),
        "orgaos": lista_orgaos,
        "tipos": lista_tipos
    }

    t0 = time.perf_counter()
    cache_hit = False
    n_results = 0
    erro_msg: Optional[str] = None
    try:
        # A1 — cache HTTP (chave = filtro inteiro).
        cache_key = {"mcp": "bnp-api", "filtro": filtro}
        busca_relaxada: Optional[str] = None
        cached = cached_http("bnp-api", cache_key)
        if cached is not None:
            import json as _json
            data = _json.loads(cached)
            cache_hit = True
            busca_relaxada = data.get("_relaxada")
        else:
            data = _api.buscar(filtro)
            # Degradação de recall (auditoria 2026-07-30): 63,8% das 309
            # chamadas do período zeraram. No PAGEA o prefixo `+` marca termo
            # OBRIGATÓRIO — removê-lo converte exigência em preferência, sem
            # tocar nas exclusões `-termo`.
            if not data.get("resultados"):
                relaxada = relaxar_bnp(busca)
                if relaxada:
                    filtro_relaxado = dict(filtro, buscaGeral=relaxada)
                    data_r = _api.buscar(filtro_relaxado)
                    if data_r.get("resultados"):
                        data = data_r
                        busca_relaxada = relaxada
                        data["_relaxada"] = relaxada
            import json as _json
            registrar_dispositivo("bnp-api", cache_key, _json.dumps(data), ttl_s=_BNP_TTL_S)

        resultados: List[BaseResultadoJuridico] = []

        for r in data.get("resultados", []):
            conteudo_partes = []

            questao = r.get("questao", "")
            if questao:
                conteudo_partes.append(f"QUESTÃO JURÍDICA: {questao}")

            tese = r.get("tese", "")
            if tese:
                conteudo_partes.append(f"TESE: {tese}")

            paradigmas = r.get("processosParadigma", [])
            if paradigmas:
                procs = [p.get("numero", "") for p in paradigmas if p.get("numero")]
                if procs:
                    conteudo_partes.append(f"PROCESSOS PARADIGMA: {', '.join(procs)}")

            conteudo = "\n\n".join(conteudo_partes)
            conteudo = truncar_por_tokens(conteudo, max_tokens=max_tokens_ementa)

            fonte = ""
            if paradigmas and paradigmas[0].get("link"):
                fonte = paradigmas[0]["link"]

            resultado = BaseResultadoJuridico(
                conteudo=conteudo,
                fonte=fonte,
                tipo=TIPOS_PRECEDENTES.get(r.get("tipo"), r.get("tipo", "")),
                orgao=r.get("orgao", ""),
                numero=f"{r.get('tipo', '')} {r.get('nr', '')}",
                situacao=r.get("situacao", ""),
                data=r.get("ultimaAtualizacao", ""),
            )
            resultados.append(resultado)

        xml_resultado = formatar_resultados_xml(resultados, "precedentes_bnp")
        n_results = len(resultados)

        meta = f'<!-- Busca: "{sanitizar_comentario_xml(busca)}" | Total: {data.get("total", len(resultados))} | Órgãos: {orgaos} | Cache: {"HIT" if cache_hit else "MISS"} -->\n'
        if busca_relaxada:
            meta += (
                f'<!-- BUSCA RELAXADA: "{sanitizar_comentario_xml(busca_relaxada)}" '
                f'| {AVISO_RELAXADA} -->\n'
            )

        return meta + xml_resultado

    except requests.exceptions.RequestException as e:
        erro_msg = f"http: {e}"
        return f'<erro>Falha na comunicação com BNP: {str(e)}</erro>'
    except Exception as e:
        erro_msg = str(e)
        return f'<erro>Erro inesperado: {str(e)}</erro>'
    finally:
        log_query(
            mcp="bnp-api",
            tool="buscar_precedentes",
            query=busca,
            filtros={"orgaos": orgaos, "tipos": tipos, "max_resultados": max_resultados},
            n_resultados=n_results,
            ms=int((time.perf_counter() - t0) * 1000),
            cache_hit=cache_hit,
            erro=erro_msg,
        )


@mcp.tool()
def ajuda_sintaxe_bnp() -> str:
    """
    Retorna guia completo de sintaxe, operadores e exemplos para buscar_precedentes.
    Consulte antes de formular queries. Sintaxe diferente dos outros MCPs.
    """
    return """
SINTAXE BNP — Banco Nacional de Precedentes (PAGEA/CNJ)
IMPORTANTE: NÃO use E, OU, NAO — não funcionam nesta base.

OPERADORES:
  +termo     — palavra OBRIGATÓRIA (equivale a AND)
  -termo     — palavra EXCLUÍDA (equivale a NOT)
  "frase"    — expressão EXATA entre aspas

TIPOS DE PRECEDENTE:
  RG   — Repercussão Geral (STF)
  RR   — Recurso Repetitivo (STJ)
  SV   — Súmula Vinculante (STF)
  SUM  — Súmula (STF/STJ)
  IRDR — Incidente de Resolução de Demandas Repetitivas
  IAC  — Incidente de Assunção de Competência
  PUIL — Pedido de Uniformização de Interpretação de Lei

ESTRATÉGIA:
  1. Verifique se existe tema vinculante conhecido → busque: "tema 1066"
  2. Identifique o instituto jurídico central (não a pergunta inteira)
  3. Use termos TÉCNICOS, não linguagem coloquial
  4. Adicione + para termos obrigatórios
  5. Use - para excluir contextos indesejados
  6. Máx 4-5 termos significativos

EXEMPLOS (pergunta → query):
  "Pensão por morte para companheiro homoafetivo"
    → "pensão por morte" +homoafetivo

  "Aposentadoria especial com uso de EPI"
    → +"aposentadoria" +"especial" +EPI

  "Servidor pode acumular aposentadorias?"
    → +acumulação +aposentadoria +servidor -militar

  "Qual o tema do STF sobre teto previdenciário?"
    → "tema 1066"

TERMOS TÉCNICOS (use em vez de linguagem coloquial):
  aposentar por doença     → aposentadoria por invalidez
  auxílio do INSS          → benefício previdenciário
  pensão da viúva          → pensão por morte
  dinheiro para deficiente → BPC, LOAS, benefício assistencial
  tempo de roça            → atividade rural, segurado especial
  cortar benefício         → cessação, cancelamento
"""


if __name__ == "__main__":
    mcp.run()
