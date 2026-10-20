"""
MCP Server: Banco de Sentenças das Justiças Militares (PDPJ/CNJ)

Fonte: ``bancosentencas.pdpj.jus.br`` — sentenças públicas e não sigilosas da
1ª INSTÂNCIA das justiças militares, com o texto integral: as 19 Auditorias da
Justiça Militar da União e a Justiça Militar dos estados (Auditorias de SP, MG
e RS e as varas de auditoria militar dos TJs). É a única base do projeto com
decisão de Auditoria; o ``stm-jurisprudencia`` traz os acórdãos do STM.

PESO: sentença de 1º grau. Mostra como o Conselho de Justiça decide, mas é
persuasiva, não precedente: na peça, o que se cita como orientação é o STM ou
o STF.

Contrato medido em ``sentencas_parser.py`` — ``por_pagina`` só 10 ou 20 (outro
valor dá HTTP 403), busca OU por padrão e operadores só em maiúsculo (a
ferramenta converte e liga por AND), paginação por cursor.
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
    sanitizar_comentario_xml,
)
import sentencas_client
import sentencas_parser as P

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

mcp = FastMCP("sentencas-militares")

TTL_S = 7 * 86400
AVISO_PESO = ("Sentença de 1º grau: persuasiva, não precedente. Na peça, a orientação "
              "se cita pelo STM (stm-jurisprudencia) ou pelo STF.")


def _consultar(corpo: dict):
    """Busca com cache. Devolve (total, docs, cache_hit)."""
    import json as _json

    chave = {"mcp": "sentencas-militares", "corpo": corpo, "v": 1}
    cached = cached_http("sentencas-militares", chave)
    if cached is not None:
        payload = _json.loads(cached)
        return payload["total"], [P.SentencaMilitar(**d) for d in payload["docs"]], True
    total, docs = sentencas_client.buscar(corpo)
    registrar_dispositivo(
        "sentencas-militares", chave,
        _json.dumps({"total": total, "docs": [d.como_dict() for d in docs]}),
        ttl_s=TTL_S,
    )
    return total, docs, False


def _log(tool: str, busca: str, filtros: dict, n: int, t0: float,
         cache_hit: bool, erro: Optional[str]) -> None:
    log_query(mcp="sentencas-militares", tool=tool, query=busca, filtros=filtros,
              n_resultados=n, ms=int((time.perf_counter() - t0) * 1000),
              cache_hit=cache_hit, erro=erro)


@mcp.tool()
def buscar_sentencas_militares(
    busca: str,
    justica: str = "JMU",
    orgao: str = "",
    data_inicio: str = "",
    data_fim: str = "",
    ordem: str = "recente",
    por_pagina: int = 10,
    cursor: str = "",
    tamanho_trecho: int = 800,
) -> str:
    """
    Busca SENTENÇAS de 1ª instância das justiças militares (Auditorias), com o
    trecho do texto onde o termo aparece.

    USE PARA: saber como as Auditorias da JMU (Conselhos de Justiça) decidem —
    deserção, insubmissão, estado de necessidade, absolvição por
    inexigibilidade, dosimetria, ANPP e sursis em 1º grau. Por padrão só a JMU;
    `justica="estadual"` ou `"todas"` inclui a Justiça Militar dos estados.

    PESO: sentença é persuasiva, não precedente. O acórdão do STM sai pelo
    stm-jurisprudencia.

    SINTAXE (a do portal; a ferramenta corrige o que ele leria errado):
      termos sem operador são ligados por AND (o portal usaria OU, e "de" casa
      tudo) · OR · NOT ou -termo · "frase exata" · * curinga · ( )
      Operador em português ou minúsculo (e, ou, não) é convertido.
      Ex.: deserção "estado de necessidade"
           (deserção OR insubmissão) ameaça -furto

    Args:
        busca: Expressão de busca (mínimo de 3 caracteres).
        justica: "JMU" (default), "estadual" ou "todas".
        orgao: Trecho do nome do órgão, dentro da justiça escolhida — ex.:
               "1ª Auditoria da 1ª CJM", "1ª CJM" (as quatro Auditorias do Rio),
               "11ª CJM", "JME/SP". Acento e ª/a indiferentes.
        data_inicio, data_fim: Data de JUNTADA da sentença (dd/mm/aaaa).
        ordem: "recente" (default) ou "antigo".
        por_pagina: 10 (default) ou 20 — o portal recusa outro valor.
        cursor: Para a página seguinte, o valor de `cursor` do rodapé anterior.
        tamanho_trecho: Caracteres do trecho por sentença (200-4000). Default 800.

    Returns:
        XML com número do processo, órgão, classe, assunto principal, data de
        juntada, tamanho do texto e o trecho. O texto integral sai por
        texto_sentenca_militar(num_processo).
    """
    tamanho_trecho = max(200, min(int(tamanho_trecho), 4000))
    filtros = {"justica": justica, "orgao": orgao, "data_inicio": data_inicio,
               "data_fim": data_fim, "ordem": ordem, "por_pagina": por_pagina, "cursor": cursor}
    t0 = time.perf_counter()
    cache_hit, n, erro = False, 0, None
    try:
        query, avisos = P.normalizar_busca(busca)
        ids = P.resolver_orgaos(justica, orgao)
        corpo = P.montar_corpo(query, ids_orgaos=ids, data_inicio=data_inicio,
                               data_fim=data_fim, ordem=ordem, por_pagina=por_pagina,
                               cursor=cursor)
        total, docs, cache_hit = _consultar(corpo)
        n = len(docs)
        termos = P.termos_positivos(query)
        resultados = [
            BaseResultadoJuridico(
                conteudo=P.trecho(d.texto, termos, tamanho_trecho),
                fonte=d.tribunal,
                tipo=d.classe,
                orgao=d.orgao,
                numero=d.num_processo,
                data=d.data_juntada,
                extra={
                    "id_sentenca": d.id_sentenca,
                    "assunto_principal": d.assunto_principal,
                    "assuntos": "; ".join(d.assuntos),
                    "tamanho_texto": str(len(d.texto)),
                },
            )
            for d in docs
        ]
        escopo = "todas as justiças militares" if ids is None else f"{len(ids)} órgão(s)"
        linhas = [f'Busca: "{busca}" | enviada: "{query}" | total: {total} | '
                  f"exibindo {n} | escopo: {escopo}"]
        linhas += avisos
        if total == 0:
            linhas.append("ZERO RESULTADOS (o banco distingue zero de erro). Reformule com "
                          "menos termos, curinga (desert*) ou OR antes de concluir que não há.")
        if docs and n == int(por_pagina) and total > n:
            linhas.append(f"Próxima página: cursor={docs[-1].timestamp} (mesmos parâmetros).")
        linhas.append(AVISO_PESO)
        cab = "".join(f"<!-- {sanitizar_comentario_xml(l)} -->\n" for l in linhas)
        return cab + formatar_resultados_xml(resultados, "sentencas_militares")
    except ValueError as e:
        erro = str(e)
        return f"<erro>Parâmetro inválido: {sanitizar_comentario_xml(str(e))}</erro>"
    except Exception as e:
        erro = str(e)
        return f"<erro>Falha na busca do Banco de Sentenças: {sanitizar_comentario_xml(str(e))}</erro>"
    finally:
        _log("buscar_sentencas_militares", busca, filtros, n, t0, cache_hit, erro)


@mcp.tool()
def texto_sentenca_militar(
    num_processo: str,
    id_sentenca: str = "",
    inicio: int = 0,
    max_caracteres: int = 40000,
) -> str:
    """
    Texto INTEGRAL de uma sentença do Banco de Sentenças das Justiças Militares,
    pelo número do processo (o banco não tem consulta por id).

    USE para ler a fundamentação e o dispositivo, ou para conferir a
    continuação de um trecho antes de citá-lo.

    Args:
        num_processo: Número CNJ, como a busca devolve (ex.: 7000670-85.2026.7.01.0001).
        id_sentenca: Se o processo tiver mais de uma sentença no banco, o
                     <id_sentenca> da busca escolhe qual.
        inicio: Caractere a partir do qual ler. Default 0.
        max_caracteres: Tamanho do trecho (2000-120000). Default 40000.
    """
    max_caracteres = max(2000, min(int(max_caracteres), 120000))
    inicio = max(0, int(inicio))
    t0 = time.perf_counter()
    cache_hit, n, erro = False, 0, None
    try:
        total, docs, cache_hit = _consultar(P.corpo_por_processo(num_processo))
        npu = num_processo.strip()
        docs = [d for d in docs if d.num_processo == npu]
        if id_sentenca.strip():
            docs = [d for d in docs if d.id_sentenca == id_sentenca.strip()]
        if not docs:
            return (f"<erro>O banco não tem sentença do processo {npu}"
                    f"{' com esse id_sentenca' if id_sentenca.strip() else ''} (busca pelo número "
                    f"entre aspas: {total} resultado(s), nenhum deste processo). O banco só "
                    f"publica sentenças públicas e não sigilosas.</erro>")
        if len(docs) > 1:
            lista = "\n".join(f"- id_sentenca `{d.id_sentenca}` · {d.orgao} · juntada "
                              f"{d.data_juntada} · {len(d.texto)} caracteres" for d in docs)
            return (f"O processo {npu} tem {len(docs)} sentenças no banco. Escolha pelo "
                    f"id_sentenca:\n\n{lista}")
        d = docs[0]
        n = 1
        corte = d.texto[inicio:inicio + max_caracteres]
        fim = inicio + len(corte)
        linhas = [f"# Sentença — {d.num_processo}", "",
                  f"**Órgão:** {d.orgao} ({d.tribunal}) · **Classe:** {d.classe} · "
                  f"**Juntada:** {d.data_juntada}",
                  f"**Assuntos:** {'; '.join(d.assuntos) or d.assunto_principal or 'N/I'}",
                  f"**id_sentenca:** `{d.id_sentenca}`", "", f"> {AVISO_PESO}", "", corte, ""]
        if fim < len(d.texto):
            linhas.append(f"*[Trecho {inicio}–{fim} de {len(d.texto)} caracteres. Continuação: "
                          f"texto_sentenca_militar(num_processo, inicio={fim}).]*")
        else:
            linhas.append(f"*[Fim do texto: {len(d.texto)} caracteres.]*")
        linhas.append(f"\n*Base: {P.PAGINA_PUBLICA}*")
        return "\n".join(linhas)
    except ValueError as e:
        erro = str(e)
        return f"<erro>Parâmetro inválido: {sanitizar_comentario_xml(str(e))}</erro>"
    except Exception as e:
        erro = str(e)
        return f"<erro>Falha ao obter a sentença: {sanitizar_comentario_xml(str(e))}</erro>"
    finally:
        _log("texto_sentenca_militar", num_processo, {"id_sentenca": id_sentenca,
             "inicio": inicio}, n, t0, cache_hit, erro)


@mcp.tool()
def ajuda_sintaxe_sentencas_militares() -> str:
    """
    Sintaxe, filtros e a lista de órgãos do Banco de Sentenças das Justiças
    Militares. Consulte para escrever o filtro `orgao`.
    """
    tabela = P.carregar_orgaos()
    jmu = [o["descricao"] for o in tabela["orgaos"] if o["tribunal"] == P.JMU]
    estaduais: dict = {}
    for o in tabela["orgaos"]:
        if o["tribunal"] != P.JMU:
            estaduais.setdefault(tabela["tribunais"][str(o["tribunal"])], []).append(o["descricao"])
    lista_est = "\n".join(f"  {t}: {'; '.join(ds)}" for t, ds in sorted(estaduais.items()))
    return f"""
BANCO DE SENTENÇAS DAS JUSTIÇAS MILITARES — {P.PAGINA_PUBLICA}

ALCANCE: sentenças públicas e não sigilosas da 1ª instância. Peso: persuasivo.

SINTAXE (a do portal, com as correções que a ferramenta faz):
  termos sem operador → ligados por AND (o portal usaria OU: "estado de
                        necessidade" sem aspas casaria o acervo inteiro)
  OR                  → alternativa              (deserção OR insubmissão) ameaça
  NOT ou -termo       → exclui                   deserção -furto
  "..."               → expressão exata          "estado de necessidade"
  *                   → curinga                  desert*
  ( )                 → agrupa
  e / ou / não, and / or / not em minúsculo → convertidos para AND / OR / NOT
  (o portal só os reconhece em MAIÚSCULO; em minúsculo seriam termo da busca)

FILTROS:
  justica  — "JMU" (default), "estadual", "todas"
  orgao    — trecho do nome: "1ª Auditoria da 1ª CJM", "1ª CJM", "JME/SP"...
  data_inicio / data_fim — data de juntada (dd/mm/aaaa)
  ordem    — "recente" (default) ou "antigo"
  por_pagina — 10 ou 20 (o portal recusa outro valor com HTTP 403)
  cursor   — página seguinte: o valor que a busca anterior devolveu

AUDITORIAS DA JMU ({len(jmu)}):
  {chr(10).join('  ' + j for j in jmu).strip()}

JUSTIÇA MILITAR DOS ESTADOS:
{lista_est}

EXEMPLOS:
  deserção "estado de necessidade"
  deserção ameaça
  (deserção OR insubmissão) "acordo de não persecução"
  "art. 187" absolvição
"""


if __name__ == "__main__":
    mcp.run()
