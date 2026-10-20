"""
MCP Server: Jurisprudência do Superior Tribunal Militar (STM)

Fonte: Consulta Judicial Unificada da JMU (``jurisprudencia.stm.jus.br``), por
HTTP simples — sem navegador, sem captcha, sem login. É a única base do projeto
com acórdãos do STM: o ``bnp-api`` traz só os precedentes qualificados dele
(súmulas, IRDR), e o ``stf-jurisprudencia`` traz o STF julgando matéria militar.

ALCANCE: acórdãos do STM (apelação, HC, RSE, embargos, correição parcial...).
NÃO cobre: decisões das Auditorias (1ª instância da JMU) — não estão nesta base;
SÚMULAS do STM — o DSpace (``dspace.stm.jus.br``) não completa o TLS pelo
cliente HTTP e o link "Procurando pelas Súmulas?" do portal dá 404 (medido em
20/10/2026); IRDR do STM — use o ``bnp-api``.

ZERO NÃO É AUSÊNCIA SEM CONTROLE: o portal devolve a mesma página para zero
resultados, query malformada e parâmetro inválido. A busca pré-valida a sintaxe
e, dando zero, roda uma consulta de controle que tem de casar; o resultado diz
qual dos dois aconteceu.

Contrato do portal medido e documentado em ``stm_parser.py``; transporte e
limites de uso (o ``robots.txt`` veda robôs: só consulta sob demanda) em
``stm_client.py``.
"""

from mcp.server.fastmcp import FastMCP
import time
from typing import Any, List, Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from shared import tls_sistema

tls_sistema.aplicar()
from shared.base_juridica import (
    BaseResultadoJuridico,
    formatar_resultados_xml,
    truncar_por_tokens,
    sanitizar_comentario_xml,
)
import stm_client
import stm_parser as P

try:
    from shared.cache_http import cached_http, registrar_dispositivo  # type: ignore
except ImportError:
    def cached_http(*_a, **_kw):  # type: ignore
        return None

    def registrar_dispositivo(*_a, **_kw) -> None:  # type: ignore
        pass

try:
    _DPU_SCRIPTS = Path.home() / ".claude" / "DPU" / "Scripts"
    if str(_DPU_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_DPU_SCRIPTS))
    from query_logger import log_query  # type: ignore
except ImportError:
    def log_query(**_kwargs: Any) -> None:  # type: ignore
        pass

mcp = FastMCP("stm-jurisprudencia")

TTL_BUSCA_S = 7 * 86400
TTL_INTEIRO_TEOR_S = 30 * 86400

AVISO_ZERO = (
    "ZERO RESULTADOS — e o portal estava respondendo (a consulta de controle "
    "casou). Atenção: o portal devolve a MESMA página para zero e para consulta "
    "que ele recusou; a sintaxe foi pré-validada (aspas e parênteses), mas um "
    "operador ou filtro que o portal não entende também cai aqui. Antes de "
    "afirmar que o STM não tem precedente, reformule: menos termos, radical com "
    "* (desert*), sem filtro de relator/classe (os nomes têm de ser exatos)."
)


def _buscar(busca: str, relator: str, classe: str, data_inicio: str,
            data_fim: str, ordem: str, inicio: int, quantidade: int):
    """Busca com cache e controle do zero. Devolve (total, docs, meta, cache_hit).

    Levanta ``ValueError`` (parâmetro que o portal recusaria em silêncio) e
    ``stm_client.PortalSTMIndisponivel``.
    """
    import json as _json

    params = P.montar_params(
        busca, relator=relator, classe=classe, data_inicio=data_inicio,
        data_fim=data_fim, ordem=ordem, inicio=inicio, quantidade=quantidade,
    )
    cache_key = {"mcp": "stm-jurisprudencia", "params": params, "v": 1}
    cached = cached_http("stm-jurisprudencia", cache_key)
    if cached is not None:
        payload = _json.loads(cached)
        docs = [P.AcordaoSTM(**d) for d in payload["docs"]]
        return payload["total"], docs, payload["meta"], True

    total, docs = stm_client.buscar(params)
    meta: dict = {"params": params}
    if params["q"] != busca.strip():
        meta["operador_corrigido"] = params["q"]
    if total is None:
        if docs:
            raise stm_client.PortalSTMIndisponivel(
                "a página trouxe acórdãos sem a contagem 'N documentos' — o "
                "layout do portal mudou; conferir o stm_parser"
            )
        if not stm_client.portal_responde():
            raise stm_client.PortalSTMIndisponivel(
                "nem a consulta de controle trouxe resultado — portal fora do ar "
                "ou layout mudado; a busca NÃO foi feita, e não há como dizer se "
                "há precedente"
            )
        meta["zero_com_controle"] = True
        return 0, [], meta, False

    registrar_dispositivo(
        "stm-jurisprudencia",
        cache_key,
        _json.dumps({"total": total, "docs": [d.como_dict() for d in docs], "meta": meta}),
        ttl_s=TTL_BUSCA_S,
    )
    return total, docs, meta, False


def _cabecalho(busca: str, total: int, n: int, inicio: int, meta: dict) -> List[str]:
    linhas = [
        f'Busca: "{busca}" | total na base: {total} | exibindo {n} a partir de {inicio}'
    ]
    filtros = {k: v for k, v in meta.get("params", {}).items() if k.startswith("fq_")}
    if filtros:
        linhas.append("Filtros: " + "; ".join(f"{k[3:]}={v}" for k, v in filtros.items()))
    if meta.get("operador_corrigido"):
        linhas.append(
            "OPERADOR CORRIGIDO: o portal só reconhece a negação com acento; "
            f'"nao" viraria termo da busca. Query enviada: "{meta["operador_corrigido"]}"'
        )
    if meta.get("zero_com_controle"):
        linhas.append(AVISO_ZERO)
    elif total and not n:
        linhas.append(f"Nenhum acórdão a partir da posição {inicio}: reduza `inicio`.")
    return linhas


def _log(tool: str, busca: str, filtros: dict, n: int, t0: float,
         cache_hit: bool, erro: Optional[str]) -> None:
    log_query(
        mcp="stm-jurisprudencia",
        tool=tool,
        query=busca,
        filtros=filtros,
        n_resultados=n,
        ms=int((time.perf_counter() - t0) * 1000),
        cache_hit=cache_hit,
        erro=erro,
    )


@mcp.tool()
def buscar_jurisprudencia_stm(
    busca: str,
    relator: str = "",
    classe: str = "",
    data_inicio: str = "",
    data_fim: str = "",
    ordem: str = "recente",
    max_resultados: int = 10,
    inicio: int = 0,
    max_tokens_ementa: int = 600,
) -> str:
    """
    Busca acórdãos do SUPERIOR TRIBUNAL MILITAR (Justiça Militar da União).

    USE PARA: crime militar e processo penal militar — deserção, insubmissão,
    abandono de posto, estado de necessidade/inexigibilidade, ANPP e sursis na
    JMU (Súmula 18/STM), Lei 9.099 na JMU, art. 90-A, termo de deserção,
    interrogatório ao final (art. 400 CPP no CPPM), dosimetria no CPM. É a única
    base do projeto com a jurisprudência do STM.

    NÃO TRAZ: decisões das Auditorias (1ª instância); súmulas do STM; IRDR (use
    bnp-api).

    SINTAXE (a do portal; operadores em PORTUGUÊS, caixa indiferente):
      e (padrão entre palavras) · ou · não (COM acento; "nao" é corrigido e
      avisado) · "frase exata" · "a b"~5 (distância) · ( ) · * (curinga:
      desert*) · ? (um caractere)
      Ex.: deserção e "estado de necessidade"
           deserção e (agiota ou usura) não furto
      Ver ajuda_sintaxe_stm().

    Args:
        busca: Expressão de busca (sintaxe acima). Não passe pergunta em
               linguagem natural.
        relator: Nome EXATO do ministro relator (ex.: "CARLOS VUYK DE AQUINO").
                 Nome parcial zera.
        classe: Classe EXATA (ex.: "APELAÇÃO CRIMINAL", "HABEAS CORPUS CRIMINAL";
                acervo antigo: "APELAÇÃO", "HABEAS CORPUS").
        data_inicio, data_fim: Data de JULGAMENTO, dd/mm/aaaa ou aaaa-mm-dd.
        ordem: "recente" (default: julgamento mais recente primeiro) ou "antigo".
        max_resultados: 1-100. Default 10.
        inicio: Posição do primeiro resultado (para a página seguinte, some
                max_resultados). Default 0.
        max_tokens_ementa: Truncamento da ementa (50-4000). Default 600. A busca
                já traz a ementa INTEGRAL do portal; para lê-la sem corte, use
                relatorio_jurisprudencia_stm.

    Returns:
        XML com número, classe, relator, revisor, data de julgamento e de
        publicação, assuntos, ementa e o <id> que inteiro_teor_stm recebe. A
        ementa deve ser reproduzida FIELMENTE na peça — é proibido resumir.
    """
    max_resultados = max(1, min(int(max_resultados), 100))
    max_tokens_ementa = max(50, min(int(max_tokens_ementa), 4000))
    inicio = max(0, int(inicio))
    filtros = {"relator": relator, "classe": classe, "data_inicio": data_inicio,
               "data_fim": data_fim, "ordem": ordem, "max_resultados": max_resultados,
               "inicio": inicio}

    t0 = time.perf_counter()
    cache_hit = False
    n_docs = 0
    erro_msg: Optional[str] = None
    try:
        total, docs, meta, cache_hit = _buscar(
            busca, relator, classe, data_inicio, data_fim, ordem, inicio, max_resultados)
        n_docs = len(docs)
        resultados = [
            BaseResultadoJuridico(
                conteudo=truncar_por_tokens(d.ementa, max_tokens=max_tokens_ementa),
                fonte="STM",
                tipo=d.classe,
                numero=d.numero,
                relator=d.relator,
                data=d.data_julgamento,
                extra={
                    "id": d.id,
                    "relator_acordao": d.relator_acordao,
                    "revisor": d.revisor,
                    "data_publicacao": d.data_publicacao,
                    "assuntos": d.assuntos,
                },
            )
            for d in docs
        ]
        cab = "".join(
            f"<!-- {sanitizar_comentario_xml(linha)} -->\n"
            for linha in _cabecalho(busca, total, n_docs, inicio, meta)
        )
        return cab + formatar_resultados_xml(resultados, "jurisprudencia_stm")
    except ValueError as e:
        erro_msg = str(e)
        return f"<erro>Parâmetro inválido: {sanitizar_comentario_xml(str(e))}</erro>"
    except Exception as e:
        erro_msg = str(e)
        return f"<erro>Falha na busca STM: {sanitizar_comentario_xml(str(e))}</erro>"
    finally:
        _log("buscar_jurisprudencia_stm", busca, filtros, n_docs, t0, cache_hit, erro_msg)


@mcp.tool()
def relatorio_jurisprudencia_stm(
    busca: str,
    relator: str = "",
    classe: str = "",
    data_inicio: str = "",
    data_fim: str = "",
    ordem: str = "recente",
    max_resultados: int = 10,
    inicio: int = 0,
) -> str:
    """
    Mesma busca de buscar_jurisprudencia_stm, em Markdown com as ementas
    INTEGRAIS (sem truncamento).

    USE para ler ou apresentar os julgados, ou para copiar a ementa na peça. A
    ementa deve ser reproduzida FIELMENTE — é PROIBIDO modificar, resumir ou
    parafrasear. Julgado não localizado vira `[INSERIR JULGADO AQUI — tema: X]`,
    nunca texto inventado. Para o voto e o dispositivo, inteiro_teor_stm(id).

    Args: os mesmos de buscar_jurisprudencia_stm (sem max_tokens_ementa).
    """
    max_resultados = max(1, min(int(max_resultados), 100))
    inicio = max(0, int(inicio))
    filtros = {"relator": relator, "classe": classe, "data_inicio": data_inicio,
               "data_fim": data_fim, "ordem": ordem, "max_resultados": max_resultados,
               "inicio": inicio}

    t0 = time.perf_counter()
    cache_hit = False
    n_docs = 0
    erro_msg: Optional[str] = None
    try:
        total, docs, meta, cache_hit = _buscar(
            busca, relator, classe, data_inicio, data_fim, ordem, inicio, max_resultados)
        n_docs = len(docs)
        linhas = ["# Relatório de Jurisprudência — Superior Tribunal Militar", ""]
        linhas += [f"> {linha}" if i else f"**{linha}**"
                   for i, linha in enumerate(_cabecalho(busca, total, n_docs, inicio, meta))]
        linhas += ["", "---", ""]
        for i, d in enumerate(docs, inicio + 1):
            linhas += [f"## {i}. {d.classe} nº {d.numero}", ""]
            linhas.append(f"**Relator(a):** {d.relator or 'N/I'}")
            if d.relator_acordao:
                linhas.append(f"**Relator(a) para o acórdão:** {d.relator_acordao}")
            if d.revisor:
                linhas.append(f"**Revisor(a):** {d.revisor}")
            linhas.append(f"**Julgamento:** {d.data_julgamento or 'N/I'} · "
                          f"**Publicação:** {d.data_publicacao or 'N/I'}")
            if d.assuntos:
                linhas.append(f"**Assuntos:** {d.assuntos}")
            linhas.append(f"**id (para inteiro_teor_stm):** `{d.id}`")
            linhas += ["", "### Ementa", "", f"> {d.ementa}", "", "---", ""]
        linhas.append(f"*Base: {P.BASE_URL}*")
        return "\n".join(linhas)
    except ValueError as e:
        erro_msg = str(e)
        return f"**Parâmetro inválido:** {e}"
    except Exception as e:
        erro_msg = str(e)
        return f"**Erro na busca STM:** {e}"
    finally:
        _log("relatorio_jurisprudencia_stm", busca, filtros, n_docs, t0, cache_hit, erro_msg)


def _modal_com_cache(uuid: str, tipo: str) -> tuple[str, bool]:
    chave = {"mcp": "stm-jurisprudencia", "modal": tipo, "uuid": uuid, "v": 1}
    cached = cached_http("stm-jurisprudencia", chave)
    if cached is not None:
        return cached, True
    texto = stm_client.modal(uuid, tipo)
    if texto:
        registrar_dispositivo("stm-jurisprudencia", chave, texto, ttl_s=TTL_INTEIRO_TEOR_S)
    return texto, False


@mcp.tool()
def inteiro_teor_stm(
    id: str,
    incluir_metadados: bool = True,
    inicio: int = 0,
    max_caracteres: int = 40000,
) -> str:
    """
    Inteiro teor de um acórdão do STM (extrato de ata, relatório, voto,
    dispositivo), pelo <id> que buscar_jurisprudencia_stm devolve.

    USE para conferir o que o acórdão decidiu além da ementa, transcrever
    trecho do voto, ou ler a continuação de uma aspa antes de citá-la.

    O texto vem do PDF do acórdão, com as quebras de linha da página: aspa
    longa se confere por trecho curto (uma oração), não pela frase inteira.

    Args:
        id: O <id> do resultado da busca (32 caracteres hexadecimais).
        incluir_metadados: Acrescenta referência legislativa, notas (precedentes
                citados) e indexação. Default True.
        inicio: Caractere a partir do qual ler (acórdão longo). Default 0.
        max_caracteres: Tamanho do trecho (2000-120000). Default 40000. O rodapé
                diz o total e o `inicio` da continuação.
    """
    max_caracteres = max(2000, min(int(max_caracteres), 120000))
    inicio = max(0, int(inicio))
    t0 = time.perf_counter()
    cache_hit = False
    erro_msg: Optional[str] = None
    n = 0
    try:
        uuid = P.validar_uuid(id)
        texto, cache_hit = _modal_com_cache(uuid, "inteiro_teor")
        if not texto:
            return (f"<erro>O portal não devolveu inteiro teor para o id {uuid}: id "
                    f"inexistente (o portal responde vazio) ou acórdão sem inteiro "
                    f"teor publicado. Confira o id na busca.</erro>")
        n = 1
        trecho = texto[inicio:inicio + max_caracteres]
        fim = inicio + len(trecho)
        linhas = [f"# Inteiro teor — STM (id `{uuid}`)", "", trecho, ""]
        if fim < len(texto):
            linhas += [f"*[Trecho {inicio}–{fim} de {len(texto)} caracteres. "
                       f"Continuação: inteiro_teor_stm(id, inicio={fim}).]*", ""]
        else:
            linhas += [f"*[Fim do texto: {len(texto)} caracteres.]*", ""]
        if incluir_metadados:
            for tipo in ("referencia_legislativa", "notas", "thesaurus"):
                extra, _ = _modal_com_cache(uuid, tipo)
                if extra:
                    linhas += [f"## {P.TIPOS_MODAL[tipo]}", "", extra, ""]
        linhas.append(f"*Base: {P.BASE_URL}*")
        return "\n".join(linhas)
    except ValueError as e:
        erro_msg = str(e)
        return f"<erro>Parâmetro inválido: {sanitizar_comentario_xml(str(e))}</erro>"
    except Exception as e:
        erro_msg = str(e)
        return f"<erro>Falha ao obter o inteiro teor: {sanitizar_comentario_xml(str(e))}</erro>"
    finally:
        _log("inteiro_teor_stm", id, {"inicio": inicio, "max_caracteres": max_caracteres},
             n, t0, cache_hit, erro_msg)


@mcp.tool()
def ajuda_sintaxe_stm() -> str:
    """
    Guia de sintaxe, filtros e alcance da base de jurisprudência do STM.
    Consulte antes de formular queries complexas.
    """
    return f"""
JURISPRUDÊNCIA DO STM — {P.BASE_URL}

ALCANCE: acórdãos do Superior Tribunal Militar. NÃO traz decisões das
Auditorias (1ª instância), súmulas do STM nem IRDR (este, no bnp-api).

OPERADORES (em português, caixa indiferente; os mesmos da ajuda do portal):
  e        — todas as palavras (é o padrão: "deserção ameaça" = deserção e ameaça)
  ou       — qualquer uma                 deserção e (agiota ou usura)
  não      — exclui; SÓ COM ACENTO        deserção não furto
             ("nao" sem acento o portal lê como termo e a busca encolhe calada:
             4.664 contra 7 documentos; a tool o corrige e avisa no cabeçalho)
  "..."    — expressão exata              "estado de necessidade"
  "..."~n  — palavras a até n de distância "termo deserção"~5
  ( )      — agrupa                       (insubmissão ou deserção) e "sursis"
  *        — curinga, início/meio/fim     desert*   de*cao
  ?        — um caractere                 em?argos

FILTROS (parâmetros da tool):
  relator      — nome EXATO, como o portal grafa: "CARLOS VUYK DE AQUINO"
  classe       — EXATA: "APELAÇÃO CRIMINAL", "HABEAS CORPUS CRIMINAL",
                 "RECURSO EM SENTIDO ESTRITO", "EMBARGOS INFRINGENTES E DE NULIDADE",
                 "EMBARGOS DE DECLARAÇÃO CRIMINAL"; no acervo antigo, "APELAÇÃO",
                 "HABEAS CORPUS"
  data_inicio / data_fim — data de JULGAMENTO (dd/mm/aaaa)
  ordem        — "recente" (default) ou "antigo"

ZERO RESULTADOS: o portal devolve a mesma página para zero e para consulta
recusada. A tool pré-valida aspas e parênteses e roda uma consulta de controle;
mesmo assim, antes de afirmar "não há precedente no STM", reformule (menos
termos, curinga, sem filtro de nome).

EXEMPLOS:
  deserção e "estado de necessidade"
  deserção e ("acordo de não persecução" ou ANPP)
  "termo de deserção" e (nulidade ou irregularidade)
  deserção e "interrogatório" e "art. 400"
  insubmissão e "sursis"
"""


if __name__ == "__main__":
    mcp.run()
