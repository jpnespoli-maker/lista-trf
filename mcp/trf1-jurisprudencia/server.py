"""
MCP Server: Jurisprudência TRF1 (base REGIONAL do CJF — 1ª Região)

Cobre duas fontes: ``TRF1`` (o tribunal) e ``JEF1`` (as turmas recursais dos
Juizados Especiais Federais da 1ª Região).

QUANDO USAR ESTE, E NÃO O ``cjf-jurisprudencia``
------------------------------------------------
``JEF1`` é o motivo de existir: nenhum outro MCP do projeto alcança turma
recursal, de região alguma. É onde está a jurisprudência dos Juizados —
previdenciário, BPC/LOAS, Bolsa Família, saque fraudulento, passe livre.

A fonte ``TRF1``, ao contrário, é essencialmente o MESMO acervo que
``cjf-jurisprudencia`` devolve com ``tribunais="TRF1"`` — medido em 2026-08-11,
4.966 documentos nos dois para a mesma query (outra query deu 915 aqui contra
893 lá: os índices não estão sincronizados ao documento). Fica disponível por
conveniência — uma query só varrendo tribunal e turmas recursais junto —, mas
para buscar SÓ o tribunal prefira o ``cjf-jurisprudencia``.

SÓ A 1ª REGIÃO. Não há base equivalente para as demais: ``/trf2`` a ``/trf6``
no mesmo host respondem 404 (verificado em 2026-08-11). O CJF publica duas
bases — ``unificada`` e ``trf1``.

Origem: adaptado do pacote ``trf1-jurisprudencia-mcp`` (MIT), por sua vez
derivado do ``julia-pesquisa-mcp`` do kit-helio (MIT). O transporte JSF e as
salvaguardas vivem em ``shared/cjf_regional_client.py``.
"""

from mcp.server.fastmcp import FastMCP
import time
from typing import Any, List, Optional
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
from shared import cjf_regional_client as trf1
from shared.relaxamento import AVISO_RELAXADA, relaxar_cjf

# Onda 2 — cache HTTP de respostas (TTL 7d, mesma política do CJF).
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

mcp = FastMCP("trf1-jurisprudencia")


def _buscar(busca: str, fontes: str, tipos: str, max_resultados: int):
    """Busca com cache, saneamento e relaxamento. Devolve (docs, meta)."""
    import json as _json

    lista_fontes = trf1.normalizar_fontes(fontes)
    lista_tipos = trf1.normalizar_tipos(tipos)

    cache_key = {
        "mcp": "trf1-jurisprudencia",
        "busca": busca,
        "fontes": sorted(lista_fontes),
        "tipos": sorted(lista_tipos),
        "max_resultados": max_resultados,
        "v": 1,
    }
    cached = cached_http("trf1-jurisprudencia", cache_key)
    if cached is not None:
        payload = _json.loads(cached)
        return payload["docs"], payload["meta"], True

    docs, meta = trf1.buscar_documentos(busca, lista_fontes, lista_tipos, max_resultados)

    # Degradação de recall — mesma política do cjf-jurisprudencia: busca
    # conjuntiva que zera é reexecutada em OU, porque o precedente-âncora
    # costuma casar a maioria dos termos, não todos.
    if not docs:
        relaxada = relaxar_cjf(busca)
        if relaxada:
            docs_r, meta_r = trf1.buscar_documentos(
                relaxada, lista_fontes, lista_tipos, max_resultados
            )
            if docs_r:
                docs, meta = docs_r, meta_r
                meta["relaxada"] = relaxada

    registrar_dispositivo(
        "trf1-jurisprudencia",
        cache_key,
        _json.dumps({"docs": docs, "meta": meta}),
        ttl_s=7 * 86400,
    )
    return docs, meta, False


def _avisos(meta: dict) -> List[str]:
    """Comentários XML/Markdown sobre o que foi feito com a query."""
    avisos: List[str] = []
    if meta.get("sanitizada"):
        chars = " ".join(meta["sanitizada"])
        avisos.append(
            f"QUERY SANEADA: o portal regional recusa os caracteres [{chars}] e "
            f"devolveria erro com resultado vazio. Foram trocados por espaço. "
            f"Query efetivamente enviada: \"{meta.get('query_enviada', '')}\""
        )
    if meta.get("relaxada"):
        avisos.append(f'BUSCA RELAXADA: "{meta["relaxada"]}" | {AVISO_RELAXADA}')
    return avisos


@mcp.tool()
def buscar_jurisprudencia_trf1(
    busca: str,
    fontes: str = "TRF1,JEF1",
    tipos: str = "todos",
    max_resultados: int = 10,
    max_tokens_ementa: int = 600,
) -> str:
    """
    Busca na base REGIONAL da 1ª Região — TRF1 (tribunal) + JEF1 (turmas recursais).

    USE PARA: jurisprudência de TURMA RECURSAL (fonte JEF1), que nenhuma outra
    base do projeto alcança — previdenciário, BPC/LOAS, Bolsa Família, saque
    fraudulento, passe livre nos Juizados.

    NÃO USE PARA: buscar só o tribunal. A fonte TRF1 é o mesmo acervo que
    buscar_jurisprudencia_cjf(tribunais="TRF1") já devolve.

    ALCANCE: só a 1ª Região. Não existe base regional para TRF2-TRF6.

    SINTAXE: a mesma do CJF (E, OU, NAO, ADJ[n], PROX[n], COM, MESMO — caixa
    indiferente; campos [EMEN], [REL], [ORGA], [DTDP]; wildcards $ e ?).
    Use ajuda_sintaxe_trf1() para o guia completo.

    ATENÇÃO — HÍFEN: este portal RECUSA `# ! + ' ; _ | -`. Termos como
    "auxílio-doença" e "salário-maternidade" são inválidos aqui. A ferramenta
    troca esses caracteres por espaço automaticamente e avisa no rodapé; o
    resultado é equivalente ("auxílio doença" casa o mesmo acervo).

    Args:
        busca: Query com sintaxe CJF. NÃO passe pergunta em linguagem natural.
        fontes: "TRF1", "JEF1" ou "TRF1,JEF1" (default). Para turma recursal
                apenas, use "JEF1".
        tipos: "todos" (default) ou lista entre ACORDAO, SUMULA, ARGUICAO,
               DECISAOMONO.
        max_resultados: Máximo de documentos (1-150; o portal devolve 30 por
                        página e a busca pagina até 5). Default: 10.
        max_tokens_ementa: Truncamento da ementa em tokens (50-4000). Default: 600.

    Returns:
        XML com os documentos: número CNJ, classe, órgão julgador, relator,
        data, fonte (TRF1 ou JEF1) e ementa. A ementa deve ser reproduzida
        FIELMENTE na peça — é proibido resumir ou parafrasear.
    """
    max_resultados = max(1, min(int(max_resultados), 150))
    max_tokens_ementa = max(50, min(int(max_tokens_ementa), 4000))

    t0 = time.perf_counter()
    cache_hit = False
    n_docs = 0
    erro_msg: Optional[str] = None
    try:
        documentos, meta, cache_hit = _buscar(busca, fontes, tipos, max_resultados)
        n_docs = len(documentos)

        resultados: List[BaseResultadoJuridico] = []
        for doc in documentos:
            resultados.append(
                BaseResultadoJuridico(
                    conteudo=truncar_por_tokens(
                        doc.get("ementa", ""), max_tokens=max_tokens_ementa
                    ),
                    fonte=doc.get("fonte", ""),
                    tipo=doc.get("classe", "") or doc.get("tipo", ""),
                    orgao=doc.get("orgao_julgador", ""),
                    numero=doc.get("numero", ""),
                    relator=doc.get("relator", ""),
                    data=doc.get("data_julgamento", ""),
                )
            )

        por_fonte = ", ".join(
            f"{f}:{n}" for f, n in (meta.get("por_fonte") or {}).items()
        )
        cabecalho = (
            f'<!-- Busca: "{sanitizar_comentario_xml(busca)}" | '
            f"total na base: {meta.get('total', 0)}"
            f"{f' ({por_fonte})' if por_fonte else ''} -->\n"
        )
        for aviso in _avisos(meta):
            cabecalho += f"<!-- {sanitizar_comentario_xml(aviso)} -->\n"

        return cabecalho + formatar_resultados_xml(resultados, "jurisprudencia_trf1")

    except Exception as e:
        erro_msg = str(e)
        return f"<erro>Falha na busca TRF1/JEF1: {sanitizar_comentario_xml(str(e))}</erro>"
    finally:
        log_query(
            mcp="trf1-jurisprudencia",
            tool="buscar_jurisprudencia_trf1",
            query=busca,
            filtros={"fontes": fontes, "tipos": tipos, "max_resultados": max_resultados},
            n_resultados=n_docs,
            ms=int((time.perf_counter() - t0) * 1000),
            cache_hit=cache_hit,
            erro=erro_msg,
        )


@mcp.tool()
def relatorio_jurisprudencia_trf1(
    busca: str,
    fontes: str = "TRF1,JEF1",
    tipos: str = "todos",
    max_resultados: int = 10,
) -> str:
    """
    Mesma busca de buscar_jurisprudencia_trf1, em Markdown com ementas completas.

    USE para ler/apresentar os julgados ou para copiar a ementa na peça.
    Para análise programática, prefira buscar_jurisprudencia_trf1 (XML).

    A ementa deve ser reproduzida FIELMENTE — é PROIBIDO modificar, resumir ou
    parafrasear. Julgado não localizado vira `[INSERIR JULGADO AQUI — tema: X]`,
    nunca um texto inventado. Atenção ao campo Fonte: JEF1 é turma recursal,
    precedente de persuasão limitada — a peça deve dizer o que está citando.

    Args:
        busca: Query com sintaxe CJF (ver ajuda_sintaxe_trf1).
        fontes: "TRF1", "JEF1" ou "TRF1,JEF1" (default).
        tipos: "todos" (default) ou ACORDAO/SUMULA/ARGUICAO/DECISAOMONO.
        max_resultados: Máximo de documentos (1-150). Default: 10.

    Returns:
        Relatório Markdown com metadados e ementas.
    """
    max_resultados = max(1, min(int(max_resultados), 150))

    t0 = time.perf_counter()
    cache_hit = False
    n_docs = 0
    erro_msg: Optional[str] = None
    try:
        lista_fontes = trf1.normalizar_fontes(fontes)
        lista_tipos = trf1.normalizar_tipos(tipos)
        documentos, meta, cache_hit = _buscar(busca, fontes, tipos, max_resultados)
        n_docs = len(documentos)

        linhas = [
            "# Relatório de Jurisprudência — TRF1 / JEF1 (base regional CJF)",
            "",
            f"**Busca:** `{busca}`",
            f"**Fontes:** {', '.join(trf1.FONTES[f] for f in lista_fontes)}",
            f"**Tipos:** {', '.join(trf1.TIPOS_DOCUMENTO[t] for t in lista_tipos)}",
            f"**Total na base:** {meta.get('total', 0)} documento(s) — "
            f"exibindo {len(documentos)}",
        ]
        if meta.get("por_fonte"):
            linhas.append(
                "**Por fonte:** "
                + ", ".join(f"{f} = {n}" for f, n in meta["por_fonte"].items())
            )
        for aviso in _avisos(meta):
            linhas.append(f"> **{aviso}**")
        linhas.extend(["", "---", ""])

        if not documentos:
            linhas.append(
                "*Nenhum documento encontrado.* Antes de concluir que não há "
                "precedente, revise a sintaxe: busca sem operadores é disjuntiva "
                "e traz ruído; busca conjuntiva longa zera. Tente "
                "`termo[EMEN] E outro[EMEN]` com menos termos."
            )
            return "\n".join(linhas)

        for i, doc in enumerate(documentos, 1):
            linhas.extend([f"## {i}. {doc.get('numero', 'N/I')}", ""])
            linhas.append(f"**Fonte:** {doc.get('fonte', 'N/I')}")
            if doc.get("classe"):
                linhas.append(f"**Classe:** {doc['classe']}")
            linhas.append(f"**Relator(a):** {doc.get('relator', 'N/I')}")
            if doc.get("orgao_julgador"):
                linhas.append(f"**Órgão Julgador:** {doc['orgao_julgador']}")
            linhas.append(f"**Data:** {doc.get('data_julgamento', 'N/I')}")
            if doc.get("data_publicacao"):
                linhas.append(f"**Publicação:** {doc['data_publicacao']}")
            linhas.append("")

            if doc.get("ementa"):
                linhas.extend(["### Ementa", "", f"> {doc['ementa']}", ""])
            if doc.get("decisao"):
                linhas.extend(["### Decisão", "", f"> {doc['decisao']}", ""])

            linhas.extend(["---", ""])

        linhas.append(f"*Base: {trf1.TRF1_URL}*")
        return "\n".join(linhas)

    except Exception as e:
        erro_msg = str(e)
        return f"**Erro na busca TRF1/JEF1:** {e}"
    finally:
        log_query(
            mcp="trf1-jurisprudencia",
            tool="relatorio_jurisprudencia_trf1",
            query=busca,
            filtros={"fontes": fontes, "tipos": tipos, "max_resultados": max_resultados},
            n_resultados=n_docs,
            ms=int((time.perf_counter() - t0) * 1000),
            cache_hit=cache_hit,
            erro=erro_msg,
        )


@mcp.tool()
def ajuda_sintaxe_trf1() -> str:
    """
    Guia de sintaxe, fontes e tipos de documento da base regional TRF1/JEF1.
    Consulte antes de formular queries complexas.
    """
    fontes = "\n".join(f"  {c:<6} — {n}" for c, n in trf1.FONTES.items())
    tipos = "\n".join(f"  {c:<12} — {n}" for c, n in trf1.TIPOS_DOCUMENTO.items())
    return f"""
BASE REGIONAL DA 1ª REGIÃO — {trf1.TRF1_URL}

FONTES (parâmetro `fontes`; default "TRF1,JEF1"):
{fontes}

  JEF1 é o motivo de existir este MCP — turma recursal não aparece em
  nenhuma outra base do projeto. A fonte TRF1 duplica o que
  buscar_jurisprudencia_cjf(tribunais="TRF1") já devolve.
  Não há base regional para TRF2-TRF6: o CJF publica só `unificada` e `trf1`.

TIPOS DE DOCUMENTO (parâmetro `tipos`; default "todos"):
{tipos}

CARACTERES PROIBIDOS PELO PORTAL:  #  !  +  '  ;  _  |  -
  A ferramenta os troca por espaço e avisa no rodapé. Consequência prática:
  "auxílio-doença" vira "auxílio doença" (mesmo acervo), e nunca se recebe
  um zero falso por causa de hífen.

OPERADORES BOOLEANOS (em português; MAIÚSCULO ou minúsculo, tanto faz):
  E     — ambos obrigatórios          pensão E morte
  OU    — qualquer um                 aposentadoria OU benefício
  NAO   — exclui o segundo            servidor NAO militar

OPERADORES DE PROXIMIDADE:
  ADJ[n]  — adjacentes NA ordem       falta ADJ grave
  PROX[n] — próximos, qualquer ordem  remição PROX3 trabalho
  COM     — mesma SENTENÇA
  MESMO   — mesmo PARÁGRAFO

BUSCA POR CAMPO:
  termo[EMEN]  — ementa (use SEMPRE que puder — reduz ruído)
  termo[DECI]  — decisão
  termo[REL]   — relator
  termo[ORGA]  — órgão julgador    "primeira turma recursal"[ORGA]
  termo[INDE]  — indexação
  2025$[DTDP]  — data de publicação

WILDCARDS:  $ (qualquer sufixo: aposentad$)  ·  ? (exatamente 1 caractere)

ATENÇÃO: busca por texto livre SEM operadores é tratada de forma disjuntiva
pelo portal e traz muito ruído. Prefira `termo[EMEN] E outro[EMEN]`.

EXEMPLOS:
  (BPC OU LOAS)[EMEN] E deficiência[EMEN]
  "auxílio doença"[EMEN] E (cessação OU restabelecimento)[EMEN]
  "salário maternidade"[EMEN] E rural[EMEN]
  "bolsa família"[EMEN] E (cancelamento OU restabelecimento)[EMEN]
  "primeira turma recursal"[ORGA] E prescrição[EMEN]
"""


if __name__ == "__main__":
    mcp.run()
