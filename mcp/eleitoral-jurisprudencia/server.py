"""
MCP Server: Jurisprudência da Justiça Eleitoral — TSE e TREs (SJUR)

QUANDO USAR
-----------
Tese eleitoral: AIJE, AIME, registro de candidatura, inelegibilidade (LC 64/90),
prestação de contas, propaganda, condutas vedadas, crimes eleitorais. É a ÚNICA
base do projeto que alcança a Justiça Eleitoral — a CJF Unificada cobre STF,
STJ e TRFs, não o TSE nem os TREs.

COMO FUNCIONA
-------------
A busca roda num Edge real (janela visível, perfil próprio do `cdp_edge`),
porque a API exige o token do hCaptcha invisível do portal. ~11 s por busca,
com cache de 7 dias. O inteiro teor sai por HTTP direto, com cadência mínima.

Desenho: ~/.claude/DPU/docs/specs/2026-10-16-mcp-jurisprudencia-eleitoral-design.md
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

AQUI = Path(__file__).resolve().parent
for _p in (AQUI.parent, AQUI):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from shared import tls_sistema  # noqa: E402

tls_sistema.aplicar()
from shared.base_juridica import (  # noqa: E402
    BaseResultadoJuridico,
    formatar_resultados_xml,
    sanitizar_comentario_xml,
    truncar_por_tokens,
)
from shared.cdp_edge import CDPIndisponivel  # noqa: E402
import sjur_client  # noqa: E402
from sjur_parser import (  # noqa: E402
    ORDENACOES,
    TAMANHO_MAX,
    Alvo,
    Decisao,
    ErroSJUR,
    ParametroInvalido,
    avisos_busca,
    conferir_filtros,
    contagem_por_tribunal,
    extrair_texto_pdf,
    interpretar_busca,
    normalizar_tipos,
    normalizar_tribunal,
    sem_dados_de_partes,
)

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

NOME = "eleitoral-jurisprudencia"
TTL_BUSCA_S = 7 * 86400
TTL_INTEIRO_TEOR_S = 30 * 86400
MAX_BUSCA = 500
MSG_SEM_NAVEGADOR = (
    "navegador indisponível para o SJUR ({erro}). Não há base alternativa: a CJF "
    "não indexa a Justiça Eleitoral. Conferir se há sessão gráfica e repetir."
)

mcp = FastMCP(NOME)


@dataclass
class Consulta:
    busca: str
    alvo: Alvo
    tipos: list[str]
    ano: Optional[int]
    ordenacao: str
    tamanho: int
    pagina: int


def _preparar(busca, tribunal, tipo_decisao, ano_eleicao, ordenacao, max_resultados, pagina) -> Consulta:
    busca = (busca or "").strip()
    if not busca:
        raise ParametroInvalido("busca vazia")
    if len(busca) > MAX_BUSCA:
        raise ParametroInvalido(f"busca com mais de {MAX_BUSCA} caracteres")
    ano = None
    if str(ano_eleicao or "").strip():
        if not re.fullmatch(r"\d{4}", str(ano_eleicao).strip()):
            raise ParametroInvalido(f"ano_eleicao inválido: {ano_eleicao!r} (esperado AAAA)")
        ano = int(str(ano_eleicao).strip())
    if ordenacao not in ORDENACOES:
        raise ParametroInvalido(f"ordenacao inválida: {ordenacao!r}. Use {', '.join(ORDENACOES)}.")
    try:
        tamanho, pag = int(max_resultados), int(pagina)
    except (TypeError, ValueError) as e:
        raise ParametroInvalido(f"max_resultados e pagina devem ser inteiros: {e}") from e
    return Consulta(
        busca=busca,
        alvo=normalizar_tribunal(tribunal),
        tipos=normalizar_tipos(tipo_decisao),
        ano=ano,
        ordenacao=ordenacao,
        tamanho=max(1, min(tamanho, TAMANHO_MAX)),
        pagina=max(1, pag) - 1,
    )


def _chave_busca(c: Consulta) -> dict:
    """Chave do cache. A expressão vai no campo `expressao`, FORA dos campos
    textuais que o `shared.cache_http` normaliza (minúscula, sem aspas nem
    acento): no SJUR a caixa do operador (`OU` × `ou`) e as aspas mudam o
    resultado, e a normalização faria buscas diferentes colidirem."""
    return {
        "mcp": NOME, "expressao": c.busca, "portal": c.alvo.portal,
        "siglas": list(c.alvo.siglas), "tipos": c.tipos, "ano": c.ano,
        "ordenacao": c.ordenacao, "tamanho": c.tamanho, "pagina": c.pagina, "v": 2,
    }


def _buscar(c: Consulta) -> tuple[list[Decisao], int, dict[str, int], bool]:
    chave = _chave_busca(c)
    bruto = cached_http(NOME, chave)
    if bruto is not None:
        payload = json.loads(bruto)
    else:
        payload = sjur_client.pesquisar(
            c.busca, c.alvo, tipos=c.tipos, ano_eleicao=c.ano, tamanho=c.tamanho,
            pagina=c.pagina, ordenacao=c.ordenacao,
        )
    decisoes, total = interpretar_busca(payload)  # levanta ANTES de gravar no cache
    conferir_filtros(decisoes, alvo=c.alvo, tipos=c.tipos, ano_eleicao=c.ano)
    if bruto is None:
        registrar_dispositivo(
            NOME, chave, json.dumps(sem_dados_de_partes(payload), ensure_ascii=False),
            ttl_s=TTL_BUSCA_S,
        )
    return decisoes, total, contagem_por_tribunal(payload), bruto is not None


def _avisos(c: Consulta, decisoes: list[Decisao], por_tribunal: dict[str, int]) -> list[str]:
    avisos = list(avisos_busca(c.busca))
    if c.alvo.portal == "tres" and not c.alvo.siglas and por_tribunal:
        avisos.append("por tribunal: " + ", ".join(f"{s} = {n}" for s, n in por_tribunal.items()))
    sem_cnj = sum(1 for d in decisoes if d.sem_cnj)
    if sem_cnj:
        avisos.append(
            f"{sem_cnj} decisão(ões) sem número CNJ: o número exibido é o do processo "
            "no SJUR (prefixo do CNJ) com o nº do acórdão; recuperar o CNJ no inteiro "
            "teor antes de citar"
        )
    return avisos


def _erro(prefixo: str, e: Exception) -> str:
    if isinstance(e, ParametroInvalido):
        return f"<erro>Parâmetro inválido: {sanitizar_comentario_xml(str(e))}</erro>"
    if isinstance(e, CDPIndisponivel):
        return f"<erro>{sanitizar_comentario_xml(MSG_SEM_NAVEGADOR.format(erro=e))}</erro>"
    return f"<erro>{prefixo}: {sanitizar_comentario_xml(str(e))}</erro>"


@mcp.tool()
def buscar_jurisprudencia_eleitoral(
    busca: str,
    tribunal: str = "TSE",
    tipo_decisao: str = "todos",
    ano_eleicao: str = "",
    ordenacao: str = "re",
    max_resultados: int = 10,
    pagina: int = 1,
    max_tokens_ementa: int = 600,
) -> str:
    """
    Busca jurisprudência da Justiça Eleitoral (TSE e TREs) no SJUR.

    USE PARA: tese eleitoral — AIJE, AIME, registro de candidatura,
    inelegibilidade, prestação de contas, propaganda, condutas vedadas, crimes
    eleitorais. Nenhuma outra base do projeto alcança o TSE ou os TREs.

    SINTAXE: operadores E, OU, NÃO em MAIÚSCULO (em minúsculo viram termo);
    "frase exata"; * e ? como curingas. Ver ajuda_sintaxe_eleitoral().

    LENTO: abre o portal num Edge real (~11 s por busca; cache de 7 dias).

    Args:
        busca: Termos de pesquisa. NÃO passe pergunta em linguagem natural.
        tribunal: "TSE" (default), "TREs" (todos) ou "TRE-UF" (ex.: "TRE-RJ";
                  aceita lista "TRE-RJ,TRE-ES").
        tipo_decisao: "todos" (default) ou lista entre acordao, resolucao,
                      decisao_sem_resolucao.
        ano_eleicao: Ano da eleição (AAAA), opcional.
        ordenacao: "re" (relevância, default), "dj_desc", "dj_asc", "dp_desc", "dp_asc".
        max_resultados: 1-100. Default 10.
        pagina: Página de resultados, a partir de 1.
        max_tokens_ementa: Truncamento da ementa (50-4000). Default 600.

    Returns:
        XML com número CNJ, classe, tribunal, relator, data, publicação, UF,
        município, ano da eleição, codigo_decisao (para inteiro_teor_eleitoral)
        e ementa. A ementa se reproduz FIELMENTE na peça. Falha do portal volta
        como <erro>, nunca como lista vazia.
    """
    max_tokens_ementa = max(50, min(int(max_tokens_ementa), 4000))
    t0 = time.perf_counter()
    cache_hit, n_docs, erro_msg = False, 0, None
    try:
        c = _preparar(busca, tribunal, tipo_decisao, ano_eleicao, ordenacao, max_resultados, pagina)
        decisoes, total, por_tribunal, cache_hit = _buscar(c)
        n_docs = len(decisoes)
        resultados: List[BaseResultadoJuridico] = [
            BaseResultadoJuridico(
                conteudo=truncar_por_tokens(d.ementa or d.decisao, max_tokens=max_tokens_ementa),
                fonte=d.tribunal,
                tipo=d.classe,
                orgao=d.tribunal,
                numero=d.numero,
                relator=d.relator,
                data=d.data,
                extra={
                    "tipo_decisao": d.tipo_decisao,
                    "publicacao": d.publicacao,
                    "uf": d.uf,
                    "municipio": d.municipio,
                    "ano_eleicao": d.ano_eleicao,
                    "codigo_decisao": d.codigo_decisao,
                    "tem_inteiro_teor": "sim" if d.tem_inteiro_teor else "nao",
                    "sem_cnj": "sim" if d.sem_cnj else "",
                },
            )
            for d in decisoes
        ]
        cabecalho = (
            f'<!-- Busca: "{sanitizar_comentario_xml(c.busca)}" | tribunal: {c.alvo.rotulo} '
            f"| total na base: {total} | exibindo: {n_docs} -->\n"
        )
        for aviso in _avisos(c, decisoes, por_tribunal):
            cabecalho += f"<!-- {sanitizar_comentario_xml(aviso)} -->\n"
        if total == 0:
            cabecalho += (
                "<!-- Nenhuma decisão encontrada. Antes de concluir que não há precedente, "
                "revise a sintaxe: operador em minúsculo vira termo, e muitos termos "
                "sem OU zeram a busca. Tente menos termos. -->\n"
            )
        return cabecalho + formatar_resultados_xml(resultados, "jurisprudencia_eleitoral")
    except Exception as e:  # noqa: BLE001
        erro_msg = str(e)
        return _erro("Falha na busca eleitoral", e)
    finally:
        log_query(
            mcp=NOME, tool="buscar_jurisprudencia_eleitoral", query=busca,
            filtros={"tribunal": tribunal, "tipo_decisao": tipo_decisao, "ano_eleicao": ano_eleicao,
                     "ordenacao": ordenacao, "max_resultados": max_resultados, "pagina": pagina},
            n_resultados=n_docs, ms=int((time.perf_counter() - t0) * 1000),
            cache_hit=cache_hit, erro=erro_msg,
        )


@mcp.tool()
def relatorio_jurisprudencia_eleitoral(
    busca: str,
    tribunal: str = "TSE",
    tipo_decisao: str = "todos",
    ano_eleicao: str = "",
    ordenacao: str = "re",
    max_resultados: int = 10,
    pagina: int = 1,
) -> str:
    """
    Mesma busca de buscar_jurisprudencia_eleitoral, em Markdown com ementa íntegra.

    USE para ler os julgados ou copiar a ementa na peça. A ementa se reproduz
    FIELMENTE — é PROIBIDO resumir ou parafrasear. Acórdão de TRE é persuasivo
    fora da sua circunscrição; a peça diz qual tribunal está citando.
    """
    t0 = time.perf_counter()
    cache_hit, n_docs, erro_msg = False, 0, None
    try:
        c = _preparar(busca, tribunal, tipo_decisao, ano_eleicao, ordenacao, max_resultados, pagina)
        decisoes, total, por_tribunal, cache_hit = _buscar(c)
        n_docs = len(decisoes)
        linhas = [
            "# Relatório de Jurisprudência — Justiça Eleitoral (SJUR)",
            "",
            f"**Busca:** `{c.busca}`",
            f"**Tribunal:** {c.alvo.rotulo}",
            f"**Tipos:** {', '.join(c.tipos)}" + (f" | **Eleição:** {c.ano}" if c.ano else ""),
            f"**Total na base:** {total} decisão(ões) — exibindo {n_docs}",
        ]
        for aviso in _avisos(c, decisoes, por_tribunal):
            linhas.append(f"> **{aviso}**")
        linhas.extend(["", "---", ""])
        if not decisoes:
            linhas.append(
                "*Nenhuma decisão encontrada.* Antes de concluir que não há precedente, "
                "revise a sintaxe: operador em minúsculo vira termo, e busca longa com "
                "E entre todos os termos zera. Tente menos termos."
            )
            return "\n".join(linhas)
        for i, d in enumerate(decisoes, 1):
            linhas.extend([f"## {i}. {d.numero or 'N/I'}", ""])
            if d.sem_cnj:
                linhas.append("> **Decisão sem número CNJ** — o número acima é o do processo "
                              "no SJUR (prefixo do CNJ) com o nº do acórdão; recuperar o CNJ "
                              "no inteiro teor antes de citar.")
            linhas.append(f"**Tribunal:** {d.tribunal}")
            if d.classe:
                linhas.append(f"**Classe:** {d.classe}")
            if d.tipo_decisao:
                linhas.append(f"**Tipo:** {d.tipo_decisao}")
            linhas.append(f"**Relator(a):** {d.relator or 'N/I'}")
            linhas.append(f"**Data:** {d.data or 'N/I'}")
            if d.publicacao:
                linhas.append(f"**Publicação:** {d.publicacao}")
            local = ", ".join(filter(None, (d.municipio, d.uf)))
            if local:
                linhas.append(f"**Local:** {local}")
            if d.ano_eleicao:
                linhas.append(f"**Eleição:** {d.ano_eleicao}")
            linhas.append(f"**Código da decisão:** {d.codigo_decisao}"
                          + (" (inteiro teor disponível)" if d.tem_inteiro_teor else ""))
            linhas.append("")
            if d.ementa:
                linhas.extend(["### Ementa", "", f"> {d.ementa}", ""])
            if d.decisao:
                linhas.extend(["### Decisão", "", f"> {d.decisao}", ""])
            linhas.extend(["---", ""])
        linhas.append(f"*Base: {sjur_client.FRONT[c.alvo.portal]}*")
        return "\n".join(linhas)
    except Exception as e:  # noqa: BLE001
        erro_msg = str(e)
        return _erro("Falha na busca eleitoral", e).replace("<erro>", "**Erro:** ").replace("</erro>", "")
    finally:
        log_query(
            mcp=NOME, tool="relatorio_jurisprudencia_eleitoral", query=busca,
            filtros={"tribunal": tribunal, "tipo_decisao": tipo_decisao, "ano_eleicao": ano_eleicao,
                     "ordenacao": ordenacao, "max_resultados": max_resultados, "pagina": pagina},
            n_resultados=n_docs, ms=int((time.perf_counter() - t0) * 1000),
            cache_hit=cache_hit, erro=erro_msg,
        )


@mcp.tool()
def inteiro_teor_eleitoral(codigo_decisao: str, max_caracteres: int = 60000) -> str:
    """
    Texto do inteiro teor (PDF) de uma decisão do SJUR, pelo codigo_decisao que
    a busca devolve.

    USE para ler o voto — onde estão a razão de decidir e o fato que torna o
    caso análogo — e para conferir um trecho com a sua continuação antes de
    citá-lo. Cadência mínima entre downloads; o portal limita a taxa.

    Args:
        codigo_decisao: Campo <codigo_decisao> da busca (só dígitos).
        max_caracteres: Corte do texto devolvido (1000-400000). Default 60000.
    """
    max_caracteres = max(1000, min(int(max_caracteres), 400_000))
    t0 = time.perf_counter()
    cache_hit, erro_msg, n = False, None, 0
    try:
        chave = {"mcp": NOME, "inteiro_teor": str(codigo_decisao).strip(), "v": 1}
        texto = cached_http(NOME, chave)
        cache_hit = texto is not None
        if texto is None:
            texto = extrair_texto_pdf(sjur_client.baixar_inteiro_teor(str(codigo_decisao).strip()))
            registrar_dispositivo(NOME, chave, texto, ttl_s=TTL_INTEIRO_TEOR_S)
        n = 1
        truncado = len(texto) > max_caracteres
        return (
            f"# Inteiro teor — código {codigo_decisao}\n"
            f"Fonte: {sjur_client.URL_PDF.format(codigo=str(codigo_decisao).strip())}\n"
            f"({len(texto)} caracteres; truncado: {'sim' if truncado else 'não'})\n\n"
            + texto[:max_caracteres]
        )
    except Exception as e:  # noqa: BLE001
        erro_msg = str(e)
        return _erro("Falha no inteiro teor eleitoral", e)
    finally:
        log_query(
            mcp=NOME, tool="inteiro_teor_eleitoral", query=str(codigo_decisao),
            filtros={"max_caracteres": max_caracteres}, n_resultados=n,
            ms=int((time.perf_counter() - t0) * 1000), cache_hit=cache_hit, erro=erro_msg,
        )


@mcp.tool()
def ajuda_sintaxe_eleitoral() -> str:
    """Guia de sintaxe, tribunais e tipos de decisão do SJUR. Consulte antes de
    formular busca complexa."""
    return f"""
JURISPRUDÊNCIA DA JUSTIÇA ELEITORAL (SJUR) — TSE: {sjur_client.FRONT['tse']}
                                            TREs: {sjur_client.FRONT['tres']}

TRIBUNAL (parâmetro `tribunal`):
  TSE        — Tribunal Superior Eleitoral (default)
  TREs       — os 27 regionais juntos
  TRE-RJ     — um regional; lista: "TRE-RJ,TRE-ES"
  Acórdão de TRE é persuasivo fora da sua circunscrição; a peça diz qual cita.

OPERADORES — sempre em MAIÚSCULO (em minúsculo viram termo da busca):
  E      ambos          abuso E poder
  OU     qualquer um    AIJE OU AIME
  NÃO    exclui         contas NÃO partido   (NAO também vale)
  Sem operador, todos os termos são obrigatórios.

OUTROS:
  "frase exata"   *  (sufixo: desincompatibiliz*)   ?  (1 caractere: di?igente)
  termo~  (grafia semelhante)   termo^3  (relevância)   ( )  agrupa

TIPO (parâmetro `tipo_decisao`): todos | acordao | resolucao | decisao_sem_resolucao
ORDENAÇÃO: re (relevância) | dj_desc | dj_asc | dp_desc | dp_asc

EXEMPLOS:
  "abuso de poder econômico" E AIJE
  "prestação de contas" E desaprovação E (partido OU candidato)
  inelegibilidade E "alínea g" E "rejeição de contas"
  "fraude à cota de gênero"    tribunal="TREs"   ano_eleicao="2024"
"""


if __name__ == "__main__":
    mcp.run()
