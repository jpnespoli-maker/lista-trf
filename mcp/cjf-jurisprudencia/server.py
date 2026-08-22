"""
MCP Server: CJF Jurisprudência Unificada

Acesso à jurisprudência unificada do Conselho da Justiça Federal.
Inclui decisões de: STF, STJ, TRF1, TRF2, TRF3, TRF4, TRF5, TRF6.
"""

from mcp.server.fastmcp import FastMCP
import time
from typing import Optional, List, Any
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

# Sessão, parsers, paginação e canário estrutural vivem em shared/cjf_client.py
# (compartilhado com o fallback do stj-jurisprudencia desde 2026-06-10).


@mcp.tool()
def buscar_jurisprudencia_cjf(
    busca: str,
    tribunais: str = "STF,STJ,TRF1,TRF2,TRF3,TRF4,TRF5,TRF6",
    max_resultados: int = 10,
    max_tokens_ementa: int = 600,
) -> str:
    """
    Busca jurisprudência unificada no portal CJF — inclui STF, STJ e todos os TRFs.
    Use ajuda_sintaxe_cjf() para guia completo de operadores e exemplos.

    SINTAXE RÁPIDA (operadores em MAIÚSCULO):
    E, OU, NAO, ADJ[n], PROX[n], COM, MESMO; campos: termo[EMEN], termo[REL], etc.
    Wildcards: $ (sufixo), ? (1 char).

    TRIBUNAIS: STF, STJ, TRF1, TRF2, TRF3, TRF4, TRF5, TRF6

    ATENÇÃO — O NÚMERO DO PROCESSO COSTUMA VIR VAZIO NESTA BASE. O campo <numero>
    reflete o que o portal devolve, e para acórdão de TRF ele frequentemente não
    vem. Como precedente de 2º grau sem CNJ não se cita (regra de recuperação de
    CNJ do projeto), a ementa volta inaproveitável e gera pendência.
    - Acórdão do TRF1 que se pretenda CITAR: use buscar_jurisprudencia_trf1(),
      que devolve o número em campo próprio.
    - Demais Regiões: julia-trf5 (TRF5), trf-jurisprudencia (TRF2/TRF4/TRF6).
    Para STF/STJ não há problema — o número do recurso basta e vem na ementa.

    Args:
        busca: Query com sintaxe CJF (operadores MAIÚSCULOS, campos [EMEN], etc).
               NÃO passe perguntas diretas. Use a estratégia de busca.
        tribunais: Tribunais separados por vírgula. Default: todos
        max_resultados: Máximo de resultados (1-100; acima de 10 pagina o portal,
                        até 5 páginas). Default: 10
        max_tokens_ementa: Truncamento da ementa em tokens (50-4000). Default: 600.
                           Aumente para obter ementa mais longa/íntegra.

    Returns:
        XML estruturado com documentos: ementa, relator, órgão julgador, data
    """
    lista_tribunais = [t.strip().upper() for t in tribunais.split(",")]
    max_resultados = max(1, min(int(max_resultados), 100))
    max_tokens_ementa = max(50, min(int(max_tokens_ementa), 4000))

    t0 = time.perf_counter()
    cache_hit = False
    n_docs = 0
    erro_msg: Optional[str] = None
    try:
        # A1 (Onda 2) — cache HTTP: chave inclui query + tribunais + nº de
        # resultados (paginação muda o payload). v2: cacheia JSON de docs já
        # parseados (antes era o HTML cru de uma página só).
        cache_key = {
            "mcp": "cjf-jurisprudencia", "busca": busca,
            "tribunais": sorted(lista_tribunais),
            "max_resultados": max_resultados, "v": 2,
        }
        import json as _json
        busca_relaxada: Optional[str] = None
        cached = cached_http("cjf-jurisprudencia", cache_key)
        if cached is not None:
            cache_hit = True
            payload = _json.loads(cached)
            documentos = payload["docs"]
            totais = payload["totais"]
            busca_relaxada = payload.get("relaxada")
        else:
            documentos, totais = cjf_client.buscar_documentos(
                busca, lista_tribunais, max_resultados
            )
            # Degradação de recall (auditoria 2026-07-30): a busca conjuntiva
            # zerava em 60,4% das 1.496 chamadas do período. Zerou, reexecuta
            # em OU — o precedente-âncora costuma casar a maioria dos termos,
            # não todos. Mesma ideia que a base local adotou em 2026-07-09.
            if not documentos:
                relaxada = relaxar_cjf(busca)
                if relaxada:
                    documentos, totais = cjf_client.buscar_documentos(
                        relaxada, lista_tribunais, max_resultados
                    )
                    busca_relaxada = relaxada if documentos else None
            registrar_dispositivo(
                "cjf-jurisprudencia", cache_key,
                _json.dumps({
                    "docs": documentos, "totais": totais,
                    "relaxada": busca_relaxada,
                }),
                ttl_s=7 * 86400,
            )
        n_docs = len(documentos)

        resultados: List[BaseResultadoJuridico] = []

        for doc in documentos:
            ementa = doc.get("ementa", "")
            ementa = truncar_por_tokens(ementa, max_tokens=max_tokens_ementa)

            resultado = BaseResultadoJuridico(
                conteudo=ementa,
                fonte="",
                tipo=doc.get("classe", ""),
                orgao=doc.get("orgao_julgador", ""),
                numero=doc.get("numero", ""),
                relator=doc.get("relator", ""),
                data=doc.get("data_julgamento", ""),
            )
            resultados.append(resultado)

        xml_resultado = formatar_resultados_xml(resultados, "jurisprudencia_cjf")

        totais_str = ", ".join([f"{k}:{v}" for k, v in totais.items()])
        meta = f'<!-- Busca: "{sanitizar_comentario_xml(busca)}" | Totais: {totais_str} -->\n'
        if busca_relaxada:
            meta += (
                f'<!-- BUSCA RELAXADA: "{sanitizar_comentario_xml(busca_relaxada)}" '
                f'| {AVISO_RELAXADA} -->\n'
            )

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
