"""
MCP Server: Jurisprudência dos TRFs no eProc — TRF2, TRF4 e TRF6

Alcança o que nenhuma outra base do projeto alcançava: **Turma Recursal** e
**TRU** (Turma Regional de Uniformização). A base unificada do CJF cobre os seis
TRFs, mas só o tribunal; o acervo dos Juizados ficava de fora.

Para quem atua no Rio, o TRF2 é a jurisdição própria: medido em 2026-08-11,
10.601 acórdãos de 2ª/3ª/6ª Turma Recursal do Rio de Janeiro para
``benefício por incapacidade``.

COBERTURA (verificada em 2026-08-11)
------------------------------------
| Região | Onde                                  | Turma Recursal |
|--------|---------------------------------------|----------------|
| TRF2   | eproc.trf2.jus.br                     | sim (+ TRU2)   |
| TRF4   | jurisprudencia.trf4.jus.br/eproc2trf4 | sim (+ TRU4)   |
| TRF6   | eproc1g.trf6.jus.br                   | sim (+ TRU6)   |
| TRF1   | servidor ``trf1-jurisprudencia``      | sim (fonte JEF1) |
| TRF5   | servidor ``julia-trf5``               | sim (+ TRU5)   |
| TRF3   | sem portal acessível por HTTP          | não            |

Origens: ``turmas_recursais`` (padrão), ``tru``, ``tribunal``, ``varas``
(esta só no TRF4 e no TRF6). Para o TRIBUNAL, em regra prefira o
``cjf-jurisprudencia``, que cobre os seis de uma vez.

Transporte e parsers em ``shared/eproc_juris_client.py`` — a mesma tela de
jurisprudência do eProc que o ``tjrj-jurisprudencia`` já usa no TJRJ.
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
from shared import eproc_juris_client as eproc

# Onda 2 — cache HTTP (2d, como no tjrj: mesma tela, mesmo ritmo de atualização).
try:
    from shared.cache_http import cached_http, registrar_dispositivo  # type: ignore
except ImportError:
    def cached_http(*_a, **_kw):  # type: ignore
        return None

    def registrar_dispositivo(*_a, **_kw) -> None:  # type: ignore
        pass

# Onda 1 — logger estruturado de queries.
try:
    _DPU_SCRIPTS = Path.home() / ".claude" / "DPU" / "Scripts"
    if str(_DPU_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_DPU_SCRIPTS))
    from query_logger import log_query  # type: ignore
except ImportError:
    def log_query(**_kwargs: Any) -> None:  # type: ignore
        pass

mcp = FastMCP("trf-jurisprudencia")
_TTL_S = 2 * 86400


def _buscar(
    tribunal: str,
    busca: str,
    origem: str,
    campo: str,
    tipo_documento: Optional[str],
    data_inicio: Optional[str],
    data_fim: Optional[str],
    max_resultados: int,
    somente_caput: bool = False,
):
    import json as _json

    cache_key = {
        "mcp": "trf-jurisprudencia",
        "tribunal": eproc.normalizar_tribunal(tribunal),
        "busca": busca,
        "origem": eproc.normalizar_origem(origem),
        "campo": campo,
        "tipo_documento": tipo_documento or "",
        "data_inicio": data_inicio or "",
        "data_fim": data_fim or "",
        "max_resultados": max_resultados,
        "somente_caput": somente_caput,
        "v": 2,
    }
    cached = cached_http("trf-jurisprudencia", cache_key)
    if cached is not None:
        payload = _json.loads(cached)
        return payload["docs"], payload["meta"], True

    docs, meta = eproc.buscar_documentos(
        tribunal, busca, origem, campo, tipo_documento,
        data_inicio, data_fim, max_resultados, somente_caput,
    )
    registrar_dispositivo(
        "trf-jurisprudencia", cache_key,
        _json.dumps({"docs": docs, "meta": meta}), ttl_s=_TTL_S,
    )
    return docs, meta, False


@mcp.tool()
def buscar_jurisprudencia_trf(
    busca: str,
    tribunal: str = "TRF2",
    origem: str = "turmas_recursais",
    campo: str = "EM",
    tipo_documento: str = "",
    data_inicio: str = "",
    data_fim: str = "",
    max_resultados: int = 10,
    max_tokens_ementa: int = 600,
    somente_caput: bool = False,
) -> str:
    """
    Jurisprudência de TURMA RECURSAL e TRU no eProc do TRF2, TRF4 ou TRF6.

    USE PARA: acórdão de Juizado Especial Federal — previdenciário, BPC/LOAS,
    Bolsa Família, saque fraudulento, passe livre. É a única via do projeto para
    Turma Recursal fora da 1ª Região, e a ÚNICA para a TRU (Turma Regional de
    Uniformização), que uniformiza a matéria dentro da Região.

    JURISDIÇÃO: TRF2 = RJ/ES · TRF4 = RS/SC/PR · TRF6 = MG.
    Para a 1ª Região use `buscar_jurisprudencia_trf1` (fonte JEF1); para o TRF5,
    o `julia-trf5` (instâncias TR_AL…TR_SE e TRU). O TRF3 não tem base acessível.

    NÃO USE PARA: jurisprudência do TRIBUNAL em geral — `buscar_jurisprudencia_cjf`
    cobre os seis TRFs numa só consulta. Aqui `origem="tribunal"` serve para
    checar um ponto específico da Região.

    SINTAXE (eProc, operadores em MINÚSCULO — ao contrário do CJF):
      e · ou · não · prox · "frase exata" · sufixo* (curinga)
      Exemplos: `benefício por incapacidade`
                `"auxílio-doença" e cessação`
                `bpc ou loas e deficiência`
      Hífen é aceito normalmente.

    Args:
        busca: Termos com sintaxe eProc. NÃO passe pergunta em linguagem natural.
        tribunal: "TRF2" (padrão), "TRF4" ou "TRF6".
        origem: "turmas_recursais" (padrão), "tru", "tribunal" ou "varas"
                (varas só existe em TRF4 e TRF6).
        campo: "EM" ementa (padrão, rápido) ou "IT" inteiro teor (bem mais
               abrangente — ~20x mais documentos).
        tipo_documento: vazio = todos. Ex.: "Acórdão", "Decisão monocrática"
                        (o conjunto varia por tribunal; ajuda_sintaxe_trf lista).
        data_inicio / data_fim: DD/MM/AAAA.
        max_resultados: 1-150. O eProc devolve 50 por página e a busca pagina
                        até 5 páginas.
        max_tokens_ementa: truncamento da ementa (50-4000). Padrão 600.
        somente_caput: restringe ao CAPUT da ementa (checkbox do portal, que vem
                       desmarcado). Reduz ruído em termo genérico, mas ANULA o
                       campo="IT" e estreita também a busca por ementa.

    Returns:
        XML com número CNJ, tipo, órgão julgador, relator, data, UF e ementa.
        A ementa deve ser reproduzida FIELMENTE na peça — proibido resumir.
        Atenção ao peso: Turma Recursal é persuasivo (art. 926 CPC), não
        vinculante; a peça deve dizer o que está citando.
    """
    max_resultados = max(1, min(int(max_resultados), 150))
    max_tokens_ementa = max(50, min(int(max_tokens_ementa), 4000))

    t0 = time.perf_counter()
    cache_hit = False
    n_docs = 0
    erro_msg: Optional[str] = None
    try:
        documentos, meta, cache_hit = _buscar(
            tribunal, busca, origem, campo, tipo_documento or None,
            data_inicio or None, data_fim or None, max_resultados, somente_caput,
        )
        n_docs = len(documentos)

        resultados: List[BaseResultadoJuridico] = []
        for doc in documentos:
            resultados.append(
                BaseResultadoJuridico(
                    conteudo=truncar_por_tokens(
                        doc.get("ementa", "") or doc.get("decisao", ""),
                        max_tokens=max_tokens_ementa,
                    ),
                    fonte=f"{meta['tribunal']}/{meta['origem']}",
                    tipo=doc.get("tipo", ""),
                    orgao=doc.get("orgao julgador", ""),
                    numero=doc.get("numero", ""),
                    relator=doc.get("relator", ""),
                    data=doc.get("data do julgamento", ""),
                    extra={
                        "uf": doc.get("uf", ""),
                        "data_publicacao": doc.get("data da publicacao", ""),
                        "relator_tratamento": doc.get("relator_tratamento", ""),
                    },
                )
            )

        cabecalho = (
            f'<!-- Busca: "{sanitizar_comentario_xml(busca)}" | '
            f"{meta['tribunal']} · origem: {meta['origem']} | "
            f"total na base: {meta['total']} -->\n"
        )
        return cabecalho + formatar_resultados_xml(resultados, "jurisprudencia_trf")

    except Exception as e:
        erro_msg = str(e)
        return f"<erro>Falha na busca {tribunal}: {sanitizar_comentario_xml(str(e))}</erro>"
    finally:
        log_query(
            mcp="trf-jurisprudencia",
            tool="buscar_jurisprudencia_trf",
            query=busca,
            filtros={
                "tribunal": tribunal, "origem": origem, "campo": campo,
                "tipo_documento": tipo_documento, "max_resultados": max_resultados,
            },
            n_resultados=n_docs,
            ms=int((time.perf_counter() - t0) * 1000),
            cache_hit=cache_hit,
            erro=erro_msg,
        )


@mcp.tool()
def relatorio_jurisprudencia_trf(
    busca: str,
    tribunal: str = "TRF2",
    origem: str = "turmas_recursais",
    campo: str = "EM",
    max_resultados: int = 10,
) -> str:
    """
    Mesma busca de buscar_jurisprudencia_trf, em Markdown com ementas completas.

    USE para ler/apresentar os julgados ou copiar a ementa na peça. Para análise
    programática prefira buscar_jurisprudencia_trf (XML).

    A ementa deve ser reproduzida FIELMENTE — proibido modificar, resumir ou
    parafrasear. Julgado não localizado vira `[INSERIR JULGADO AQUI — tema: X]`,
    nunca texto inventado.

    Args:
        busca: Termos com sintaxe eProc (ver ajuda_sintaxe_trf).
        tribunal: "TRF2" (padrão), "TRF4" ou "TRF6".
        origem: "turmas_recursais" (padrão), "tru", "tribunal", "varas".
        campo: "EM" (padrão) ou "IT".
        max_resultados: 1-150. Padrão 10.

    Returns:
        Relatório Markdown com metadados e ementas.
    """
    max_resultados = max(1, min(int(max_resultados), 150))

    t0 = time.perf_counter()
    cache_hit = False
    n_docs = 0
    erro_msg: Optional[str] = None
    try:
        documentos, meta, cache_hit = _buscar(
            tribunal, busca, origem, campo, None, None, None, max_resultados
        )
        n_docs = len(documentos)

        linhas = [
            f"# Relatório de Jurisprudência — {meta['tribunal']} (eProc)",
            "",
            f"**Busca:** `{busca}`",
            f"**Origem:** {meta['origem']}",
            f"**Campo:** {'ementa' if campo.upper().startswith('E') else 'inteiro teor'}",
            f"**Total na base:** {meta['total']} documento(s) — exibindo {len(documentos)}",
            "",
            "---",
            "",
        ]

        if not documentos:
            linhas.append(
                "*Nenhum documento encontrado.* Antes de concluir que não há "
                "precedente, tente menos termos, ou `campo=\"IT\"` (inteiro teor), "
                "ou outra origem (`tru`, `tribunal`)."
            )
            return "\n".join(linhas)

        for i, doc in enumerate(documentos, 1):
            linhas.extend([f"## {i}. {doc.get('numero', 'N/I')}", ""])
            if doc.get("tipo"):
                linhas.append(f"**Tipo:** {doc['tipo']}")
            linhas.append(f"**Órgão Julgador:** {doc.get('orgao julgador', 'N/I')}")
            trat = doc.get("relator_tratamento") or "Relator(a)"
            linhas.append(f"**{trat}:** {doc.get('relator', 'N/I')}")
            linhas.append(f"**Data:** {doc.get('data do julgamento', 'N/I')}")
            if doc.get("data da publicacao"):
                linhas.append(f"**Publicação:** {doc['data da publicacao']}")
            if doc.get("uf"):
                linhas.append(f"**UF:** {doc['uf']}")
            linhas.append("")
            if doc.get("ementa"):
                linhas.extend(["### Ementa", "", f"> {doc['ementa']}", ""])
            elif doc.get("decisao"):
                linhas.extend(["### Decisão", "", f"> {doc['decisao']}", ""])
            linhas.extend(["---", ""])

        return "\n".join(linhas)

    except Exception as e:
        erro_msg = str(e)
        return f"**Erro na busca {tribunal}:** {e}"
    finally:
        log_query(
            mcp="trf-jurisprudencia",
            tool="relatorio_jurisprudencia_trf",
            query=busca,
            filtros={"tribunal": tribunal, "origem": origem, "campo": campo,
                     "max_resultados": max_resultados},
            n_resultados=n_docs,
            ms=int((time.perf_counter() - t0) * 1000),
            cache_hit=cache_hit,
            erro=erro_msg,
        )


@mcp.tool()
def ajuda_sintaxe_trf(tribunal: str = "") -> str:
    """
    Guia de sintaxe, tribunais, origens e tipos de documento do eProc.

    Args:
        tribunal: se informado ("TRF2"/"TRF4"/"TRF6"), consulta o formulário ao
                  vivo e lista as origens e os tipos REAIS daquele tribunal.
                  Vazio = só o guia estático.
    """
    guia = """
JURISPRUDÊNCIA DOS TRFs NO eProc — TRF2, TRF4, TRF6

O QUE ESTE SERVIDOR TEM DE ÚNICO
  Turma Recursal e TRU. A base unificada do CJF cobre os seis TRFs, mas só o
  tribunal — nenhum acórdão de Juizado. Aqui está o acervo dos Juizados.

JURISDIÇÃO
  TRF2 = RJ/ES · TRF4 = RS/SC/PR · TRF6 = MG
  TRF1 → servidor trf1-jurisprudencia (fonte JEF1)
  TRF5 → servidor julia-trf5 (instâncias TR_AL…TR_SE e TRU; NÃO tem 1º grau)
  TRF3 → sem portal acessível por HTTP (WAF); só o 2º grau, pela CJF Unificada

ORIGENS (parâmetro `origem`)
  turmas_recursais  Turmas Recursais dos JEFs (PADRÃO — o motivo deste servidor)
  tru               Turma Regional de Uniformização (uniformiza dentro da Região)
  tribunal          O próprio TRF — em regra prefira o cjf-jurisprudencia
  varas             Varas Federais (só TRF4 e TRF6)

CAMPO (parâmetro `campo`)
  EM   ementa (padrão — rápido e preciso)
  IT   inteiro teor (mais abrangente, mais lento)

OPERADORES (MINÚSCULO — ao contrário do CJF, que usa maiúsculo)
  e      ambos obrigatórios      "auxílio-doença" e cessação
  ou     qualquer um             bpc ou loas
  não    exclui                  incapacidade não temporária
  prox   termos próximos         invalidez prox perícia
  "..."  frase exata             "salário-maternidade"
  *      curinga de sufixo       aposentad*

  Hífen é aceito normalmente (o portal regional do TRF1 é que o recusa).

FILTROS DE DATA
  data_inicio / data_fim no formato DD/MM/AAAA

PESO DO PRECEDENTE
  Turma Recursal é persuasivo (art. 926 CPC), não vinculante; TRU uniformiza a
  Região. Diga na peça o que está citando — não apresente TR como se fosse TRF.

EXEMPLOS
  buscar_jurisprudencia_trf("bpc ou loas e deficiência", tribunal="TRF2")
  buscar_jurisprudencia_trf("\\"auxílio-doença\\" e restabelecimento", origem="tru")
  buscar_jurisprudencia_trf("bolsa família e cancelamento", tribunal="TRF4")
"""
    if not tribunal:
        return guia

    try:
        trib = eproc.normalizar_tribunal(tribunal)
        sess = eproc.get_sessao(trib)
        sess.abrir()
        return (
            guia
            + f"\nAO VIVO — {trib} ({eproc.TRIBUNAIS[trib]})\n"
            + f"  origens disponíveis: {', '.join(sorted(sess.origens))}\n"
            + f"  tipos de documento:  {', '.join(sorted(sess.tipos))}\n"
        )
    except Exception as e:
        return guia + f"\n(não foi possível consultar {tribunal} ao vivo: {e})\n"


if __name__ == "__main__":
    mcp.run()
