"""
MCP Server: CJF Jurisprudência Unificada

Acesso à jurisprudência unificada do Conselho da Justiça Federal.
Inclui decisões de: STF, STJ, TRF1, TRF2, TRF3, TRF4, TRF5, TRF6.
A busca consulta o portal antigo e a API nova do CJF ao mesmo tempo (ver
`shared/cjf_duas_rotas.py`). A TNU tem tool própria (`buscar_jurisprudencia_tnu`),
pela API nova do CJF.
"""

from mcp.server.fastmcp import FastMCP
import time
from datetime import datetime
from typing import Optional, List, Any, Dict
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
from xml.sax.saxutils import escape as xml_escape
from shared import cjf_api, cjf_client, cjf_duas_rotas
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

# Medido em 01/10/2026 (PAJ 2021/016-09136): "pandemia" traz 41 acórdãos do STF,
# milhares do TRF1/TRF3/TRF5 e só 2 do STJ, até 2019; "covid" dá zero para o STJ.
# O índice do STJ nesta base para em ~dez/2019 — os demais tribunais seguem atuais.
AVISO_COBERTURA_STJ = (
    "COBERTURA: esta base não indexa acórdão do STJ posterior a ~dez/2019 "
    "(medido em 01/10/2026). Jurisprudência recente do STJ: stj-jurisprudencia, "
    "que vai ao SCON. Os demais tribunais seguem atualizados."
)
AVISO_VIA_API = (
    "FONTE: API nova do CJF — julgado do STF (origem api-nova) vem sem data nem "
    "órgão; conferir no portal do STF antes de citar."
)
AVISO_FILTROS_API = (
    "FILTROS DA API NOVA: busca só na API; portal antigo não tem estes filtros"
)

_TRIBUNAIS_PADRAO = "STF,STJ,TRF1,TRF2,TRF3,TRF4,TRF5,TRF6"
_TIPOS_DATA = {"decisao": "DATA_DECISAO", "publicacao": "DATA_PUBLICACAO_PESQ"}
_MODOS = ("comum", "semantica")
_FORMATOS_DATA = ("%Y-%m-%d", "%d/%m/%Y")
_TTL_BUSCA_S = 7 * 86400
_TTL_DOCUMENTO_S = 30 * 86400
_TTL_ANALISE_S = 86400


def _data_api(valor: str, nome: str) -> Optional[str]:
    """AAAA-MM-DD ou DD/MM/AAAA → AAAA-MM-DD; vazio → None; outra forma → ValueError."""
    texto = (valor or "").strip()
    if not texto:
        return None
    for formato in _FORMATOS_DATA:
        try:
            return datetime.strptime(texto, formato).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"{nome} não entendida: {valor!r}. Use AAAA-MM-DD ou DD/MM/AAAA.")


def _montar_filtros(
    modo: str,
    data_inicio: str = "",
    data_fim: str = "",
    tipo_data: str = "decisao",
    relator: str = "",
    legislacao: str = "",
    so_precedentes_relevantes: bool = False,
    so_representativos: bool = False,
) -> "cjf_api.FiltrosApi":
    """Entrada da tool → ``FiltrosApi``. Entrada que não se entende levanta
    ``ValueError``: filtro descartado em silêncio faria a busca rodar sem ele."""
    modo = (modo or "comum").strip().lower()
    if modo not in _MODOS:
        raise ValueError(f"modo deve ser 'comum' ou 'semantica'; recebido {modo!r}")
    chave_data = (tipo_data or "decisao").strip().lower()
    if chave_data not in _TIPOS_DATA:
        raise ValueError(f"tipo_data deve ser 'decisao' ou 'publicacao'; recebido {tipo_data!r}")
    relatores: tuple = ()
    if relator.strip():
        relatores = tuple(cjf_api.sugerir_relator(relator.strip()))
        if not relatores:
            raise ValueError(f"relator não encontrado: {relator}")
    norma = cjf_api.normalizar_legislacao(legislacao) if legislacao.strip() else None
    return cjf_api.FiltrosApi(
        modo=modo,
        data_inicio=_data_api(data_inicio, "data_inicio"),
        data_fim=_data_api(data_fim, "data_fim"),
        tipo_data=_TIPOS_DATA[chave_data],
        relatores=relatores,
        legislacao=norma,
        so_precedentes_relevantes=bool(so_precedentes_relevantes),
        so_representativos=bool(so_representativos),
    )


def _filtros_para_chave(f: "cjf_api.FiltrosApi") -> Dict[str, Any]:
    return {
        "modo": f.modo, "data_inicio": f.data_inicio, "data_fim": f.data_fim,
        "tipo_data": f.tipo_data, "relatores": list(f.relatores),
        "legislacao": f.legislacao, "so_precedentes_relevantes": f.so_precedentes_relevantes,
        "so_representativos": f.so_representativos,
    }


def _resultados(documentos: List[Dict[str, Any]], max_tokens_ementa: int) -> List[BaseResultadoJuridico]:
    resultados: List[BaseResultadoJuridico] = []
    for doc in documentos:
        extra: Dict[str, Any] = {}
        for campo in ("origem", "tribunal", "id", "link_inteiro_teor"):
            if doc.get(campo):
                extra[campo] = doc[campo]
        if doc.get("precedente_relevante"):
            extra["precedente_relevante"] = "sim"
        if doc.get("representativo_controversia"):
            extra["representativo_controversia"] = "sim"
        resultados.append(BaseResultadoJuridico(
            conteudo=truncar_por_tokens(doc.get("ementa", ""), max_tokens=max_tokens_ementa),
            fonte="",
            tipo=doc.get("classe", ""),
            orgao=doc.get("orgao_julgador", ""),
            numero=doc.get("numero", ""),
            relator=doc.get("relator", ""),
            data=doc.get("data_julgamento", ""),
            extra=extra,
        ))
    return resultados


def _rotas_caidas(res: "cjf_duas_rotas.ResultadoDuasRotas") -> Dict[str, str]:
    return {
        nome: rota.erro or "sem detalhe"
        for nome, rota in (("portal-antigo", res.antiga), ("api-nova", res.api))
        if not rota.ok
    }


def _totais_str(totais: Dict[str, int]) -> str:
    return ", ".join(f"{k}:{v}" for k, v in totais.items())


@mcp.tool()
def buscar_jurisprudencia_cjf(
    busca: str,
    tribunais: str = _TRIBUNAIS_PADRAO,
    max_resultados: int = 10,
    max_tokens_ementa: int = 600,
    modo: str = "comum",
    data_inicio: str = "",
    data_fim: str = "",
    tipo_data: str = "decisao",
    relator: str = "",
    legislacao: str = "",
    so_precedentes_relevantes: bool = False,
    so_representativos: bool = False,
) -> str:
    """
    Busca jurisprudência unificada do CJF — STF, STJ e todos os TRFs. Consulta o
    portal antigo e a API nova AO MESMO TEMPO e junta as duas listas; cada
    resultado traz <origem> (portal-antigo, api-nova ou ambas). Use
    ajuda_sintaxe_cjf() para o guia completo.

    SINTAXE RÁPIDA: operadores em MAIÚSCULO — E, OU, NAO, ADJ[n], PROX[n], COM,
    MESMO; campos: termo[EMEN], termo[REL], etc. Wildcards: $ (sufixo), ? (1 char).
    "e" e "ou" em minúsculo são palavras comuns, não operadores. Palavras soltas
    ("bolsa familia unipessoal") são unidas por E.

    TRIBUNAIS: STF, STJ, TRF1, TRF2, TRF3, TRF4, TRF5, TRF6

    O QUE CADA ROTA TRAZ (medido em 23/10/2026): a API nova devolve o número CNJ
    (84 de 90 documentos) mas NÃO cobre o STJ; o portal antigo cobre o STJ mas
    quase nunca traz o CNJ (0 de 65: o <numero> vem como o nome da classe). Julgado
    do STF vindo só da API não tem data nem órgão (há aviso). Se uma rota cair, o
    cabeçalho diz `ROTA FORA` e a outra segue respondendo.
    - Para CITAR acórdão do TRF1 sem CNJ: buscar_jurisprudencia_trf1().
    - Demais Regiões: julia-trf5 (TRF5), trf-jurisprudencia (TRF2/TRF4/TRF6).

    ATENÇÃO — O STJ NESTA BASE PARA EM ~DEZ/2019 (medido em 01/10/2026). Zero ou
    só julgados antigos do STJ não provam que ele não decidiu a matéria: para o
    recente, stj-jurisprudencia com forcar_scon=True. Os demais tribunais seguem
    atualizados.

    FILTROS NOVOS (modo, data_inicio, data_fim, tipo_data, relator, legislacao,
    so_precedentes_relevantes, so_representativos): só a API nova os tem. Usar
    QUALQUER um faz a busca ir SÓ à API e EXCLUI o STJ (aviso SEM COBERTURA).
    Entrada que não se entende (data, legislação, relator, modo) volta como <erro>,
    nunca como busca sem o filtro.

    TOOLS IRMÃS: documento_cjf (ementa, decisão ou inteiro teor de um <id>),
    similares_cjf (julgados parecidos com um <id>), estatisticas_cjf (total,
    julgamentos por ano, relatores e classes mais frequentes).

    Args:
        busca: Query com sintaxe CJF (operadores MAIÚSCULOS, campos [EMEN], etc).
               NÃO passe perguntas diretas. Use a estratégia de busca.
        tribunais: Tribunais separados por vírgula. Default: todos
        max_resultados: Máximo de resultados (1-100). Default: 10
        max_tokens_ementa: Truncamento da ementa em tokens (50-4000). Default: 600.
        modo: "comum" (default) ou "semantica" (busca por sentido, só API).
        data_inicio, data_fim: AAAA-MM-DD ou DD/MM/AAAA (só API).
        tipo_data: "decisao" (default) ou "publicacao" — a que data se aplicam.
        relator: nome ou parte do nome; resolvido pelas grafias que a API conhece.
        legislacao: "Lei 8.742/93", "LC 80", "Decreto 3.048", "Decreto-Lei 4.657",
                    "MP 2.164" (só API).
        so_precedentes_relevantes: só julgados marcados como precedente relevante.
        so_representativos: só representativos de controvérsia.

    Returns:
        XML com <origem>, <tribunal>, <id> (use em documento_cjf/similares_cjf),
        ementa, relator, órgão julgador, data e, quando há, <link_inteiro_teor>.
    """
    lista_tribunais = [t.strip().upper() for t in tribunais.split(",")]
    max_resultados = max(1, min(int(max_resultados), 100))
    max_tokens_ementa = max(50, min(int(max_tokens_ementa), 4000))

    t0 = time.perf_counter()
    cache_hit = False
    n_docs = 0
    erro_msg: Optional[str] = None
    try:
        import json as _json
        try:
            filtros = _montar_filtros(
                modo, data_inicio, data_fim, tipo_data, relator, legislacao,
                so_precedentes_relevantes, so_representativos,
            )
        except ValueError as e:
            erro_msg = str(e)
            return f"<erro>{xml_escape(str(e))}</erro>"
        so_api = filtros.ativo()

        # v3: cache de docs já fundidos das duas rotas; a chave leva o modo e todos
        # os filtros (mudam o payload).
        cache_key = {
            "mcp": "cjf-jurisprudencia", "busca": busca,
            "tribunais": sorted(lista_tribunais),
            "max_resultados": max_resultados,
            "filtros": _filtros_para_chave(filtros), "v": 3,
        }
        busca_relaxada: Optional[str] = None
        aviso_cobertura: Optional[str] = None
        totais_antiga: Dict[str, int] = {}
        totais_api: Dict[str, int] = {}
        rotas_fora: Dict[str, str] = {}
        avisos_rota: Dict[str, str] = {}
        cached = cached_http("cjf-jurisprudencia", cache_key)
        if cached is not None:
            cache_hit = True
            payload = _json.loads(cached)
            documentos = payload["docs"]
            totais_antiga = payload.get("totais_antiga") or {}
            totais_api = payload.get("totais_api") or {}
            busca_relaxada = payload.get("relaxada")
            aviso_cobertura = payload.get("aviso_cobertura")
        elif so_api:
            # Tribunal sem cobertura na API (STJ) levanta em vez de virar zero;
            # busca-se nos cobertos e declara-se o resto.
            lista_efetiva = lista_tribunais
            try:
                documentos, totais_api = cjf_api.buscar_api(
                    busca, lista_efetiva, max_resultados, filtros
                )
            except cjf_api.SemCoberturaNaApi as exc:
                if not exc.cobertos:
                    raise
                aviso_cobertura = str(exc)
                documentos, totais_api = cjf_api.buscar_api(
                    busca, exc.cobertos, max_resultados, filtros
                )
            documentos = [{**d, "origem": "api-nova"} for d in documentos]
            if documentos:
                registrar_dispositivo(
                    "cjf-jurisprudencia", cache_key,
                    _json.dumps({
                        "docs": documentos, "totais_api": totais_api,
                        "aviso_cobertura": aviso_cobertura,
                    }),
                    ttl_s=_TTL_BUSCA_S,
                )
        else:
            res = cjf_duas_rotas.buscar_duas_rotas(busca, lista_tribunais, max_resultados)
            cobertos = [t for t in lista_tribunais if t in cjf_api.TRIBUNAIS_COBERTOS]
            if not res.antiga.ok and (not res.api.ok or not cobertos):
                causa_api = res.api.erro if not res.api.ok else (
                    f"não cobre {', '.join(lista_tribunais)}; use stj-jurisprudencia para o STJ"
                )
                raise RuntimeError(
                    f"portal antigo: {res.antiga.erro}; API nova: {causa_api}"
                )
            # Degradação de recall (auditoria 2026-07-30): a busca conjuntiva
            # zerava em 60,4% das 1.496 chamadas do período. A lista UNIFICADA
            # zerou (não só uma rota), reexecuta em OU — o precedente-âncora costuma
            # casar a maioria dos termos, não todos.
            falhas_estrita = _rotas_caidas(res)
            falhas_relaxada: Dict[str, str] = {}
            if not res.docs:
                relaxada = relaxar_cjf(busca)
                if relaxada:
                    res_relaxada = cjf_duas_rotas.buscar_duas_rotas(
                        relaxada, lista_tribunais, max_resultados, relaxada=True
                    )
                    falhas_relaxada = _rotas_caidas(res_relaxada)
                    if res_relaxada.docs:
                        res, busca_relaxada = res_relaxada, relaxada
            documentos = res.docs
            totais_antiga, totais_api = res.antiga.totais, res.api.totais
            rotas_fora = _rotas_caidas(res)
            # A rota caída da busca que NÃO foi a exibida também se declara: sem isso
            # o zero (ou o resultado) passaria por limpo.
            if busca_relaxada:
                avisos_rota = {f"{n} na busca estrita": e for n, e in falhas_estrita.items()}
            else:
                avisos_rota = {f"{n} na busca relaxada": e for n, e in falhas_relaxada.items()}
            # Zero NÃO se cacheia (31/08/2026): um shard de tribunal às escuras
            # devolve 200 com "Total 0", e gravar isso deixava o falso zero GRUDADO.
            # Rota caída também não: o resultado parcial não pode valer por 7 dias.
            # Tribunal que a API não cobre (STJ) não impede: é limite conhecido.
            if documentos and not rotas_fora and not falhas_estrita and not falhas_relaxada:
                registrar_dispositivo(
                    "cjf-jurisprudencia", cache_key,
                    _json.dumps({
                        "docs": documentos, "totais_antiga": totais_antiga,
                        "totais_api": totais_api, "relaxada": busca_relaxada,
                    }),
                    ttl_s=_TTL_BUSCA_S,
                )
            if "STJ" in lista_tribunais and "portal-antigo" in rotas_fora:
                aviso_cobertura = (
                    "STJ: a API nova não cobre o STJ e o portal antigo está fora "
                    f"({rotas_fora['portal-antigo']}); não há STJ nesta resposta"
                )
        n_docs = len(documentos)

        xml_resultado = formatar_resultados_xml(
            _resultados(documentos, max_tokens_ementa), "jurisprudencia_cjf"
        )

        if so_api:
            totais_txt = f"Totais api-nova: {_totais_str(totais_api)}"
        else:
            totais_txt = (
                f"Totais portal-antigo: {_totais_str(totais_antiga)} "
                f"| Totais api-nova: {_totais_str(totais_api)}"
            )
        meta = f'<!-- Busca: "{sanitizar_comentario_xml(busca)}" | {totais_txt} -->\n'
        if so_api:
            meta += f"<!-- {AVISO_FILTROS_API} -->\n"
        for nome, erro in {**rotas_fora, **avisos_rota}.items():
            meta +=f"<!-- ROTA FORA: {nome} ({sanitizar_comentario_xml(erro)}) -->\n"
        if "STJ" in lista_tribunais and not so_api and not aviso_cobertura:
            meta += f"<!-- {AVISO_COBERTURA_STJ} -->\n"
        if aviso_cobertura:
            meta += f"<!-- SEM COBERTURA: {sanitizar_comentario_xml(aviso_cobertura)} -->\n"
        if any(
            d.get("origem") in ("api-nova", "ambas") and d.get("tribunal") == "STF"
            and not d.get("data_julgamento")
            for d in documentos
        ):
            meta += f"<!-- {AVISO_VIA_API} -->\n"
        if busca_relaxada:
            meta += (
                f'<!-- BUSCA RELAXADA: "{sanitizar_comentario_xml(busca_relaxada)}" '
                f"| {AVISO_RELAXADA} -->\n"
            )

        return meta + xml_resultado

    except Exception as e:
        erro_msg = str(e)
        return f"<erro>Falha na busca CJF: {xml_escape(str(e))}</erro>"
    finally:
        log_query(
            mcp="cjf-jurisprudencia",
            tool="buscar_jurisprudencia_cjf",
            query=busca,
            filtros={"tribunais": tribunais, "max_resultados": max_resultados, "modo": modo},
            n_resultados=n_docs,
            ms=int((time.perf_counter() - t0) * 1000),
            cache_hit=cache_hit,
            erro=erro_msg,
        )


AVISO_TNU = (
    "FONTE: API do CJF, filtrada pela Turma Nacional de Uniformização (julgados até "
    "2026). Antes de citar, conferir o acórdão no eProc da TNU "
    "(eproctnu-jur.cjf.jus.br), que traz o campo DECISÃO por extenso."
)


@mcp.tool()
def buscar_jurisprudencia_tnu(
    busca: str,
    max_resultados: int = 10,
    max_tokens_ementa: int = 600,
    ordenar_por_data: bool = False,
) -> str:
    """
    Busca jurisprudência da TURMA NACIONAL DE UNIFORMIZAÇÃO (TNU) dos JEFs.

    USE PARA: PUIL, temas representativos da TNU ("Tema 379"), tese de Juizado
    uniformizada nacionalmente. É a única base do projeto com a TNU posterior a
    julho de 2017 (vai até 2026); `buscar_jurisprudencia_cjf` não alcança a TNU.

    Args:
        busca: termos (operadores E, OU, ADJ, [EMEN], como no CJF) ou um número
               CNJ sozinho ("0044230-77.2023.4.05.8300"), que traz os julgados
               daquele processo.
        max_resultados: 1 a 100. Padrão 10.
        max_tokens_ementa: truncamento da ementa (50 a 4000). Padrão 600.
        ordenar_por_data: True traz os mais recentes primeiro; o padrão é por
                          relevância.

    Returns:
        XML com número CNJ, classe, relator, data e ementa; o campo DECISÃO vem
        em <decisao>, truncado. Antes de citar, conferir no eProc da TNU.
    """
    max_resultados = max(1, min(int(max_resultados), 100))
    max_tokens_ementa = max(50, min(int(max_tokens_ementa), 4000))
    t0 = time.perf_counter()
    cache_hit = False
    n_docs = 0
    erro_msg: Optional[str] = None
    try:
        import json as _json
        cache_key = {
            "mcp": "cjf-jurisprudencia", "tool": "tnu", "busca": busca,
            "max_resultados": max_resultados, "ordenar_por_data": ordenar_por_data, "v": 1,
        }
        cached = cached_http("cjf-jurisprudencia", cache_key)
        if cached is not None:
            cache_hit = True
            payload = _json.loads(cached)
            documentos, total, exato = payload["docs"], payload["total"], payload["exato"]
        else:
            documentos, total, exato = cjf_api.buscar_tnu(
                busca, max_resultados, ordenar_por_data=ordenar_por_data
            )
            # Zero não se cacheia, como na busca do CJF (31/08/2026).
            if documentos:
                registrar_dispositivo(
                    "cjf-jurisprudencia", cache_key,
                    _json.dumps({"docs": documentos, "total": total, "exato": exato}),
                    ttl_s=7 * 86400,
                )
        n_docs = len(documentos)

        resultados = []
        for doc in documentos:
            extra = {}
            if doc.get("decisao"):
                extra["decisao"] = truncar_por_tokens(doc["decisao"], max_tokens=max_tokens_ementa)
            if doc.get("tipo"):
                extra["tipo_documento"] = doc["tipo"]
            resultados.append(BaseResultadoJuridico(
                conteudo=truncar_por_tokens(doc.get("ementa", ""), max_tokens=max_tokens_ementa),
                fonte="TNU",
                tipo=doc.get("classe", ""),
                orgao=doc.get("orgao_julgador", ""),
                numero=doc.get("numero", ""),
                relator=doc.get("relator", ""),
                data=doc.get("data_julgamento", ""),
                extra=extra,
            ))

        contagem = f"{total}" if exato else f"{total} ou mais"
        meta = (
            f'<!-- TNU | Busca: "{sanitizar_comentario_xml(busca)}" | Total: {contagem} '
            f'| Exibindo: {len(resultados)} -->\n<!-- {AVISO_TNU} -->\n'
        )
        return meta + formatar_resultados_xml(resultados, "jurisprudencia_tnu")

    except Exception as e:
        erro_msg = str(e)
        return f'<erro>Falha na busca da TNU: {str(e)}</erro>'
    finally:
        log_query(
            mcp="cjf-jurisprudencia",
            tool="buscar_jurisprudencia_tnu",
            query=busca,
            filtros={"max_resultados": max_resultados, "ordenar_por_data": ordenar_por_data},
            n_resultados=n_docs,
            ms=int((time.perf_counter() - t0) * 1000),
            cache_hit=cache_hit,
            erro=erro_msg,
        )


_CAMPOS_TEXTO_DOCUMENTO = ("ementa", "decisao", "inteiro_teor", "texto")


@mcp.tool()
def documento_cjf(id: str, campo: str = "", max_tokens: int = 0) -> str:
    """
    Documento da API nova do CJF pelo <id> que a busca devolve.

    Args:
        id: o <id> de um resultado de buscar_jurisprudencia_cjf ou similares_cjf
            (só resultados com origem api-nova ou ambas têm id).
        campo: vazio para o documento completo; "ementa", "decisao" ou
               "inteiro_teor" para só aquele texto.
        max_tokens: se maior que 0, trunca cada texto longo a esse tamanho.
                    O inteiro teor pode ser muito extenso.

    Returns:
        XML <documento_cjf> com os campos, ou <erro>.
    """
    t0 = time.perf_counter()
    cache_hit = False
    erro_msg: Optional[str] = None
    try:
        import json as _json
        cache_key = {
            "mcp": "cjf-jurisprudencia", "tool": "documento", "id": id, "campo": campo, "v": 3,
        }
        cached = cached_http("cjf-jurisprudencia", cache_key)
        if cached is not None:
            cache_hit = True
            doc = _json.loads(cached)
        else:
            doc = cjf_api.obter_documento(id, campo)
            # Resposta vazia não se cacheia: ficaria 30 dias como "sem texto".
            vazio = (not doc.get("texto")) if campo else not (
                doc.get("ementa") or doc.get("inteiro_teor")
            )
            if not vazio:
                registrar_dispositivo(
                    "cjf-jurisprudencia", cache_key, _json.dumps(doc), ttl_s=_TTL_DOCUMENTO_S
                )
        vazio = (not doc.get("texto")) if campo else not (
            doc.get("ementa") or doc.get("inteiro_teor")
        )
        linhas = []
        if vazio:
            alvo = f"o campo {campo}" if campo else "ementa e inteiro teor"
            linhas.append(f"<!-- VAZIO: a API nova devolveu {alvo} vazio para este id -->")
        linhas.append("<documento_cjf>")
        for chave, valor in doc.items():
            if valor in (None, "", False):
                continue
            texto = str(valor)
            if max_tokens > 0 and chave in _CAMPOS_TEXTO_DOCUMENTO:
                texto = truncar_por_tokens(texto, max_tokens=max_tokens)
            linhas.append(f"  <{chave}>{xml_escape(texto)}</{chave}>")
        linhas.append("</documento_cjf>")
        return "\n".join(linhas)
    except Exception as e:
        erro_msg = str(e)
        return f"<erro>Falha ao obter o documento CJF: {xml_escape(str(e))}</erro>"
    finally:
        log_query(
            mcp="cjf-jurisprudencia", tool="documento_cjf", query=id,
            filtros={"campo": campo, "max_tokens": max_tokens}, n_resultados=0,
            ms=int((time.perf_counter() - t0) * 1000), cache_hit=cache_hit, erro=erro_msg,
        )


@mcp.tool()
def similares_cjf(id: str, max_resultados: int = 10, max_tokens_ementa: int = 600) -> str:
    """
    Julgados parecidos com um documento, pela API nova do CJF.

    Args:
        id: o <id> de um resultado de buscar_jurisprudencia_cjf.
        max_resultados: 1 a 100. Padrão 10.
        max_tokens_ementa: truncamento da ementa (50 a 4000). Padrão 600.

    Returns:
        Mesmo XML da busca (raiz jurisprudencia_cjf_similares), ou <erro>.
    """
    max_resultados = max(1, min(int(max_resultados), 100))
    max_tokens_ementa = max(50, min(int(max_tokens_ementa), 4000))
    t0 = time.perf_counter()
    cache_hit = False
    n_docs = 0
    erro_msg: Optional[str] = None
    try:
        import json as _json
        cache_key = {
            "mcp": "cjf-jurisprudencia", "tool": "similares", "id": id,
            "max_resultados": max_resultados, "v": 3,
        }
        cached = cached_http("cjf-jurisprudencia", cache_key)
        if cached is not None:
            cache_hit = True
            documentos = _json.loads(cached)
        else:
            documentos = cjf_api.similares(id, max_resultados)
            if documentos:
                registrar_dispositivo(
                    "cjf-jurisprudencia", cache_key, _json.dumps(documentos),
                    ttl_s=_TTL_ANALISE_S,
                )
        n_docs = len(documentos)
        meta = f'<!-- Similares de: {sanitizar_comentario_xml(id)} -->\n'
        return meta + formatar_resultados_xml(
            _resultados(documentos, max_tokens_ementa), "jurisprudencia_cjf_similares"
        )
    except Exception as e:
        erro_msg = str(e)
        return f"<erro>Falha ao buscar similares CJF: {xml_escape(str(e))}</erro>"
    finally:
        log_query(
            mcp="cjf-jurisprudencia", tool="similares_cjf", query=id,
            filtros={"max_resultados": max_resultados}, n_resultados=n_docs,
            ms=int((time.perf_counter() - t0) * 1000), cache_hit=cache_hit, erro=erro_msg,
        )


def _tabela(titulo: str, itens: List[Dict[str, Any]], rotulo: str, limite: int = 10) -> str:
    linhas = [titulo]
    for item in itens[:limite]:
        complemento = f" ({item['subtitle']})" if item.get("subtitle") else ""
        linhas.append(f"  {item.get(rotulo, '')}{complemento}: {item.get('count', 0)}")
    return "\n".join(linhas)


def _texto_estatisticas(busca: str, dados: Dict[str, Any]) -> str:
    por_ano = sorted(dados.get("julgamentos_por_ano") or [], key=lambda x: x.get("year", 0))
    partes = [
        f'Estatísticas CJF (API nova) — busca: "{busca}"',
        f"Total de documentos: {dados.get('total_documents', 0)}",
        "Julgamentos por ano:\n" + "\n".join(
            f"  {a.get('year')}: {a.get('count', 0)}" for a in por_ano
        ),
        _tabela("Relatores mais frequentes:", dados.get("top_relatores") or [], "key"),
        _tabela("Classes mais frequentes:", dados.get("top_classes") or [], "key"),
        _tabela("Tribunais:", dados.get("top_tribunais") or [], "key"),
    ]
    return "\n".join(partes)


@mcp.tool()
def estatisticas_cjf(
    busca: str,
    tribunais: str = "STF,TRF1,TRF2,TRF3,TRF4,TRF5,TRF6",
    modo: str = "comum",
    data_inicio: str = "",
    data_fim: str = "",
    relator: str = "",
) -> str:
    """
    Estatísticas de uma busca na API nova do CJF: total de documentos, julgamentos
    por ano, os 10 relatores e as 10 classes mais frequentes e os tribunais.
    Serve para ver se a tese é pacífica, crescente ou rara antes de pesquisar.
    Não cobre o STJ.

    Args:
        busca: mesma sintaxe de buscar_jurisprudencia_cjf.
        tribunais: padrão STF e TRFs (sem STJ).
        modo, data_inicio, data_fim, relator: como em buscar_jurisprudencia_cjf.

    Returns:
        Texto legível, ou <erro>.
    """
    t0 = time.perf_counter()
    cache_hit = False
    erro_msg: Optional[str] = None
    try:
        import json as _json
        lista = [t.strip().upper() for t in tribunais.split(",")]
        try:
            filtros = _montar_filtros(modo, data_inicio, data_fim, "decisao", relator)
        except ValueError as e:
            erro_msg = str(e)
            return f"<erro>{xml_escape(str(e))}</erro>"
        cache_key = {
            "mcp": "cjf-jurisprudencia", "tool": "estatisticas", "busca": busca,
            "tribunais": sorted(lista), "filtros": _filtros_para_chave(filtros), "v": 3,
        }
        cached = cached_http("cjf-jurisprudencia", cache_key)
        if cached is not None:
            cache_hit = True
            dados = _json.loads(cached)
        else:
            dados = cjf_api.estatisticas(busca, lista, filtros)
            # Total zero (ou ausente) não se cacheia: o falso zero ficaria grudado.
            if dados.get("total_documents"):
                registrar_dispositivo(
                    "cjf-jurisprudencia", cache_key, _json.dumps(dados), ttl_s=_TTL_ANALISE_S
                )
        return _texto_estatisticas(busca, dados)
    except Exception as e:
        erro_msg = str(e)
        return f"<erro>Falha nas estatísticas CJF: {xml_escape(str(e))}</erro>"
    finally:
        log_query(
            mcp="cjf-jurisprudencia", tool="estatisticas_cjf", query=busca,
            filtros={"tribunais": tribunais, "modo": modo}, n_resultados=0,
            ms=int((time.perf_counter() - t0) * 1000), cache_hit=cache_hit, erro=erro_msg,
        )


@mcp.tool()
def ajuda_sintaxe_cjf() -> str:
    """
    Retorna guia completo de sintaxe, operadores, filtros e exemplos para
    buscar_jurisprudencia_cjf e suas tools irmãs. Consulte antes de formular
    queries complexas.
    """
    return """
SINTAXE CJF — Operadores em PORTUGUÊS e MAIÚSCULO

AS DUAS ROTAS (a busca consulta as duas ao mesmo tempo):
  portal-antigo — cobre o STJ, mas quase nunca traz o número CNJ
  api-nova      — traz o CNJ, mas NÃO cobre o STJ; STF vem sem data nem órgão
  ambas         — o mesmo julgado veio das duas; os campos se completam
  Cada resultado tem <origem>. Cabeçalho `ROTA FORA: <rota> (<erro>)` = uma rota
  caiu e a outra respondeu sozinha; `Totais portal-antigo` e `Totais api-nova`
  mostram o que cada uma contou.

OPERADORES BOOLEANOS (SEMPRE em MAIÚSCULO — "e" e "ou" minúsculos são palavras):
  E     — ambos obrigatórios          pensão E morte
  OU    — qualquer um                 aposentadoria OU benefício
  NAO   — exclui segundo              servidor NAO militar
  XOU   — um ou outro, não ambos      pensão XOU aposentadoria
  Palavras soltas ("bolsa familia unipessoal") são unidas por E.

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

FILTROS NOVOS (parâmetros de buscar_jurisprudencia_cjf; só a API nova os tem):
  modo="semantica"            busca por sentido, não por palavra
  data_inicio, data_fim       AAAA-MM-DD ou DD/MM/AAAA
  tipo_data                   "decisao" (padrão) ou "publicacao"
  relator                     nome ou parte; resolvido pela grafia da API
  legislacao                  "Lei 8.742/93", "LC 80", "Decreto 3.048",
                              "Decreto-Lei 4.657", "MP 2.164"
  so_precedentes_relevantes   só os marcados como precedente relevante
  so_representativos          só os representativos de controvérsia
  Usar QUALQUER um leva a busca SÓ à API nova e EXCLUI o STJ (comentário
  `FILTROS DA API NOVA` e `SEM COBERTURA`). Data, legislação, relator ou modo que
  não se entende voltam como <erro>; a busca nunca roda sem o filtro.

TOOLS IRMÃS:
  documento_cjf(id, campo, max_tokens)  — ementa, decisão ou inteiro teor de um <id>
  similares_cjf(id)                     — julgados parecidos com um <id>
  estatisticas_cjf(busca, ...)          — total, por ano, relatores e classes
  buscar_jurisprudencia_tnu(busca)      — TNU (a busca do CJF não a alcança)
  O <id> só existe em resultado com origem api-nova ou ambas.

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
  - Case insensitive nos termos; operadores só em MAIÚSCULO
  - Acentos ignorados: aposentadoria = aposentadória

ATENÇÃO — NÚMERO DO PROCESSO: o portal antigo quase nunca traz o CNJ (o <numero>
vem como o nome da classe); a API nova traz. Resultado só do portal antigo sem CNJ
não se cita sem recuperar o número (TRF1: buscar_jurisprudencia_trf1).
ATENÇÃO — STJ: esta base para em ~dez/2019 (medido em 01/10/2026); o recente está
em stj-jurisprudencia (forcar_scon=True).

PRIORIDADE DE TRIBUNAIS:
  STF  — questões constitucionais, repercussão geral
  STJ  — uniformização de lei federal, repetitivos
  TRF4 — referência em direito previdenciário
  TRF1 — grande volume, Brasília
  TRF3 — São Paulo, grande volume
"""


if __name__ == "__main__":
    mcp.run()
