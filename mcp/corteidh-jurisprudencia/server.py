"""MCP Server: Corte IDH — jurisprudência da Corte Interamericana.

Lê o índice local `corteidh.db` e devolve PARÁGRAFO citável. Não vai à rede: o
acervo é adquirido pelo `corteidh_crawler.py`, que é script de manutenção. Essa
separação é deliberada — consulta durante a redação de peça não pode depender da
disponibilidade do site da Corte.

Regra de idioma: prefere português; não havendo, devolve o original marcando
`exige_traducao`, e a peça tem de trazer a tradução ao lado (ver
`ajuda_sintaxe_corteidh`).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared import tls_sistema  # noqa: E402

tls_sistema.aplicar()
from shared.base_juridica import (  # noqa: E402
    BaseResultadoJuridico,
    formatar_resultados_xml,
    sanitizar_comentario_xml,
    truncar_por_tokens,
)

sys.path.insert(0, str(Path(__file__).parent))
import citacao  # noqa: E402
import indice  # noqa: E402

try:
    _DPU_SCRIPTS = Path.home() / ".claude" / "DPU" / "Scripts"
    if str(_DPU_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_DPU_SCRIPTS))
    from query_logger import log_query  # type: ignore
except ImportError:
    def log_query(**_kwargs: Any) -> None:  # type: ignore
        pass

mcp = FastMCP("corteidh-jurisprudencia")

CAMINHO_BANCO = indice.CAMINHO_PADRAO

_ORGAO = "Corte Interamericana de Direitos Humanos"
_NOME_TIPO = {
    "CC": "Caso Contencioso", "OC": "Parecer Consultivo",
    "SS": "Supervisão de Cumprimento de Sentença",
    "MP": "Medida Provisória", "CO": "Resolução",
}

# Só estes dois têm o texto indexado (spec §4.2, decisão do Defensor de
# 2026-09-17): guardar texto dos 903 documentos de supervisão custaria 59% do
# acervo pelo bloco de menor retorno argumentativo. A supervisão entra por
# metadados, e é a `ficha_caso_corteidh` que a serve.
TIPOS_COM_TEXTO = {"CC", "OC"}


def _erro(tipo: str, mensagem: str) -> str:
    return (f'<erro tipo="{tipo}">{sanitizar_comentario_xml(mensagem)}</erro>')


def _cobertura_de_idioma(con, *, estado=None, tipo=None) -> dict:
    """Quantos documentos do recorte têm versão portuguesa, e quantos não.

    Lê-se do índice, não da rede. Serve ao `<aviso_idioma>`: sem este número,
    um resultado pequeno é indistinguível de acervo pequeno, e o Defensor não
    saberia que a barreira foi de LÍNGUA e não de precedente.
    """
    sql = ["SELECT COUNT(*) AS n,",
           "       SUM(CASE WHEN tem_por = 1 THEN 1 ELSE 0 END) AS com_por",
           "  FROM documento WHERE n_paragrafos > 0"]
    args: list = []
    if estado:
        sql.append(" AND estado = ?"); args.append(estado)
    if tipo:
        sql.append(" AND tipo = ?"); args.append(tipo.upper())
    r = con.execute("\n".join(sql), args).fetchone()
    total = int(r["n"] or 0)
    com_por = int(r["com_por"] or 0)
    return {"total": total, "com_por": com_por, "sem_por": total - com_por}


def _com_avisos(xml: str, *, consulta: str, expansoes: list, linhas: list,
                cobertura: dict) -> str:
    """Anexa `<expansao_consulta>` e `<aviso_idioma>` ao XML dos resultados.

    Duas declarações que a spec §6.1.1 exige, e as duas existem para que zero
    resultado nunca saia ambíguo:

    - `<expansao_consulta>` diz o que a busca DE FATO procurou. A expansão é
      assistência heurística, não garantia; busca que procurou outra coisa que
      não o pedido tem de dizê-lo, senão o Defensor lê os resultados como se
      fossem do termo que digitou.
    - `<aviso_idioma>` diz quantos documentos do recorte não têm versão
      portuguesa. Sem ele, "nada encontrado" se lê como ausência de
      precedente, quando pode ser barreira de língua — e essa confusão muda a
      tese da peça.

    Os blocos entram DENTRO de `<resultados>`, não depois dele.
    `formatar_resultados_xml` devolve raiz única, e anexá-los como irmãos
    produziria documento com duas raízes — malformado, ainda que legível. O
    contrato desta ferramenta diz XML, e XML tem uma raiz.
    """
    partes: list[str] = []

    if expansoes:
        linhas_exp = []
        for e in expansoes:
            alternativas = ", ".join(
                f"{lang}={sanitizar_comentario_xml(str(e[lang]))}"
                for lang in ("es", "en", "fr") if e.get(lang))
            linhas_exp.append(
                f'  <termo pt="{sanitizar_comentario_xml(e["termo"])}" '
                f'fonte="{sanitizar_comentario_xml(e["fonte"])}">'
                f'{alternativas}</termo>')
        partes.append(
            "<expansao_consulta>\n"
            + "\n".join(linhas_exp)
            + "\n  <nota>A busca procurou TAMBEM os termos acima em espanhol, "
              "ingles e frances, porque o acervo e majoritariamente espanhol. "
              "Expansao e assistencia, nao garantia: o glossario tem 161 "
              "entradas e nao cobre todo o vocabulario. Termo com "
              "fonte=curadoria nao foi confirmado no texto oficial da "
              "Convencao — o par e curadoria deste projeto.</nota>\n"
            "</expansao_consulta>")

    sem_por = cobertura.get("sem_por", 0)
    total = cobertura.get("total", 0)
    if total and sem_por:
        pct = 100.0 * sem_por / total
        urgencia = "critico" if not linhas else "informativo"
        partes.append(
            f'<aviso_idioma nivel="{urgencia}" '
            f'documentos_no_recorte="{total}" '
            f'sem_versao_portuguesa="{sem_por}">'
            f"{sem_por} de {total} documentos deste recorte ({pct:.0f}%) NAO "
            f"tem versao em portugues no acervo da Corte. Consulta em "
            f"portugues nao casa com texto em espanhol no indice; a expansao "
            f"por glossario reduz o problema e nao o elimina. "
            + ("RESULTADO VAZIO AQUI NAO SIGNIFICA AUSENCIA DE PRECEDENTE: "
               "tente o termo em espanhol, ou os filtros que nao dependem de "
               "lingua (tema, artigo_cadh). "
               if not linhas else "")
            + "Citando-se decisao sem versao portuguesa, a peca leva a "
              "traducao ao lado, marcada 'traducao livre'."
            "</aviso_idioma>")

    if not partes:
        return xml

    bloco = "\n".join("  " + p if not p.startswith(" ") else p
                      for p in "\n".join(partes).splitlines())
    fecho = "</resultados>"
    pos = xml.rfind(fecho)
    if pos == -1:
        # Raiz inesperada: devolve intacto em vez de montar XML quebrado.
        # Prefere-se perder o aviso a corromper a saída.
        return xml
    return xml[:pos] + bloco + "\n" + xml[pos:]


def _url_do_idioma(linha: dict) -> str:
    """URL do PDF no MESMO idioma do parágrafo devolvido.

    Nunca cair para outro idioma: link é instrumento de conferência na fonte
    (spec §6.1-bis), e apontar para o PDF em português ao lado de uma aspa em
    espanhol faz o Defensor abrir o documento e não achar o texto — a
    conferência falharia exatamente onde deveria funcionar. Não havendo URL
    para o idioma do trecho, devolve vazio, e a citação sai sem link: ausência
    declarada é melhor que link errado.
    """
    return linha.get(f"url_{linha['idioma']}") or ""


def _montar_resultado(linha: dict, max_tokens: int) -> BaseResultadoJuridico:
    url = _url_do_idioma(linha)
    cit = citacao.formatar_citacao(
        caso=linha["caso"], data=linha["data"] or "", serie=linha["serie"],
        numero=linha["numero"], paragrafo=linha["paragrafo"],
        etapa=linha.get("etapa"), tipo=linha["tipo"], url=url,
    )
    serie_num = (
        f"Série {linha['serie']} No. {linha['numero']}"
        if linha["serie"] and linha["numero"] is not None else (linha["caso"])
    )
    return BaseResultadoJuridico(
        conteudo=truncar_por_tokens(linha["texto"], max_tokens),
        fonte=url,
        tipo=_NOME_TIPO.get(linha["tipo"], linha["tipo"]),
        orgao=_ORGAO,
        numero=serie_num,
        data=linha["data"] or "",
        extra={
            "caso": linha["caso"],
            "estado": linha.get("estado") or "",
            "etapa": linha.get("etapa") or "",
            "paragrafo": linha["paragrafo"],
            "idioma": linha["idioma"],
            # STRING, não booleano: `formatar_resultados_xml` só emite campo de
            # `extra` quando ele é *truthy*, e o Python bool `False` é falsy —
            # o campo que dispara o dever de traduzir desapareceria do XML
            # justo quando vale False, ficando indistinguível de "o servidor
            # não disse". `str(False)` é "False", não-vazia, sempre emitida.
            "exige_traducao": str(linha["exige_traducao"]),
            # AVISO DE CONTAMINAÇÃO — o índice o tinha e a saída não o emitia.
            # Medido em 18/09/2026 pela tool MCP real: 1.639 dos 30.610
            # parágrafos (5,4%) estão marcados `suspeito=1`, e NENHUM desses
            # avisos chegava a quem redige. A busca devolveu o par. 398 da
            # OC-32 com nota de rodapé visivelmente colada ao texto, sem
            # nenhum sinal.
            #
            # O defeito é o pior do subsistema, porque produz citação
            # LITERALMENTE VERIFICÁVEL e SUBSTANTIVAMENTE FALSA: o trecho está
            # mesmo naquele parágrafo daquele PDF — passa no `validar_fontes`,
            # passa no `grep` —, mas é texto de RODAPÉ, e transcrevê-lo como
            # fundamentação atribui à Corte o que ela citou de terceiro.
            #
            # Emitido SEMPRE, como string, pela mesma razão do
            # `exige_traducao`: campo omitido quando falso é indistinguível de
            # "o servidor não apurou", e aqui a diferença é entre "conferi e
            # está limpo" e "não olhei".
            "suspeito": str(bool(linha.get("suspeito"))),
            "aviso_suspeito": (
                "Este parágrafo pode conter nota de rodapé ou cabeçalho "
                "colados ao texto pela extração do PDF. CONFERIR NA FONTE "
                "antes de transcrever verbatim: o trecho existe no PDF, mas "
                "pode não ser fundamentação da Corte."
                if linha.get("suspeito") else ""
            ),
            "citacao_sugerida": cit,
            "url_pdf_por": linha.get("url_por") or "",
        },
    )


def _buscar_sync(
    consulta: str, tema: str | None = None, estado: str | None = None,
    tipo: str | None = None, artigo_cadh: str | None = None,
    ano_de: int | None = None, ano_ate: int | None = None,
    idioma_preferido: str = "por", max_resultados: int = 10,
    max_tokens_paragrafo: int = 600,
) -> str:
    max_resultados = max(1, min(int(max_resultados), 50))
    max_tokens_paragrafo = max(50, min(int(max_tokens_paragrafo), 4000))
    t0 = time.perf_counter()

    # Não-cobertura se DECLARA; não se devolve como vazio. Pedindo um tipo cujo
    # texto não está indexado, uma busca honesta não pode responder
    # `total="0"` — isso se lê como "não há precedente sobre isso", quando a
    # verdade é "este bloco não foi indexado por texto", e o Defensor
    # concluiria ausência de precedente a partir de uma lacuna de engenharia.
    if tipo and tipo.upper() not in TIPOS_COM_TEXTO:
        nome = _NOME_TIPO.get(tipo.upper(), tipo)
        return _erro(
            "fora-do-indice-textual",
            f"{nome} ({tipo.upper()}) entra no índice por METADADOS, sem texto "
            f"— logo não há parágrafo para casar, e um resultado vazio aqui NÃO "
            f"significa ausência de precedente. Os tipos com texto indexado são "
            f"{', '.join(sorted(TIPOS_COM_TEXTO))}. Para supervisão de "
            f"cumprimento, use ficha_caso_corteidh(caso=...), que devolve o "
            f"estado do cumprimento a partir dos metadados.",
        )

    if not Path(CAMINHO_BANCO).exists():
        return _erro(
            "indice-ausente",
            f"O índice {CAMINHO_BANCO} não existe. Rode: python "
            f"mcp/corteidh-jurisprudencia/"
            f"corteidh_crawler.py --semear",
        )

    con = indice.abrir(CAMINHO_BANCO)
    try:
        # Expansão pelo glossário ANTES do FTS. O acervo é esmagadoramente
        # espanhol e consulta em português não casa com texto em espanhol —
        # sem isto, pesquisar "saúde" não acha "salud", e os dois leading
        # cases do eixo (Poblete Vilches, Cuscul Pivaral) ficariam invisíveis.
        expandida, expansoes = indice.expandir_consulta(con, consulta)
        linhas = indice.buscar(
            con, consulta=expandida, estado=estado, tipo=tipo,
            artigo_cadh=artigo_cadh, ano_de=ano_de, ano_ate=ano_ate,
            idioma_preferido=idioma_preferido, limite=max_resultados,
        )
        cobertura = _cobertura_de_idioma(con, estado=estado, tipo=tipo)
    finally:
        con.close()

    xml = formatar_resultados_xml(
        [_montar_resultado(l, max_tokens_paragrafo) for l in linhas])
    xml = _com_avisos(xml, consulta=consulta, expansoes=expansoes,
                      linhas=linhas, cobertura=cobertura)
    log_query(mcp="corteidh-jurisprudencia", tool="buscar_corteidh",
              query=consulta, n_resultados=len(linhas),
              ms=int((time.perf_counter() - t0) * 1000),
              filtros={"estado": estado, "tipo": tipo,
                       "artigo_cadh": artigo_cadh, "tema": tema})
    return xml


def _ficha_sync(caso: str) -> str:
    if not Path(CAMINHO_BANCO).exists():
        return _erro("indice-ausente",
                     f"O índice {CAMINHO_BANCO} não existe. Rode o "
                     f"corteidh_crawler.py --semear")
    con = indice.abrir(CAMINHO_BANCO)
    try:
        f = indice.ficha(con, caso=caso)
    finally:
        con.close()
    if f is None:
        return _erro("nao-encontrado",
                     f"Nenhum documento casa com {caso!r} no índice local. "
                     f"Pode não ter sido indexado ainda — ver o crawler.")

    r = BaseResultadoJuridico(
        conteudo=(f"{len(f['artigos_violados'])} artigo(s) da CADH declarados "
                  f"violados; {len(f['reparacoes'])} reparação(ões) ordenada(s); "
                  f"{f['n_paragrafos']} parágrafos indexados."),
        fonte=f.get("url_por") or f.get("url_esp") or "",
        tipo=_NOME_TIPO.get(f["tipo"], f["tipo"]),
        orgao=_ORGAO,
        numero=(f"Série {f['serie']} No. {f['numero']}"
                if f["serie"] and f["numero"] is not None else f["caso"]),
        data=f["data"] or "",
        extra={
            "caso": f["caso"], "estado": f.get("estado") or "",
            "etapa": f.get("etapa") or "",
            "artigos_violados": ", ".join(f["artigos_violados"]),
            "reparacoes": " | ".join(f["reparacoes"]),
            "idiomas": ", ".join(f["idiomas"]),
            "tem_portugues": "por" in f["idiomas"],
            # Link preferindo o português, que é o idioma em que se deve citar
            # havendo versão; a ficha não fixa um parágrafo, então aqui não há
            # idioma de trecho a casar.
            "citacao_sugerida": citacao.formatar_citacao(
                caso=f["caso"], data=f["data"] or "", serie=f["serie"],
                numero=f["numero"], etapa=f.get("etapa"), tipo=f["tipo"],
                url=f.get("url_por") or f.get("url_esp") or ""),
        },
    )
    return formatar_resultados_xml([r], tag_raiz="ficha")


@mcp.tool()
async def buscar_corteidh(
    consulta: str,
    tema: str | None = None,
    estado: str | None = None,
    tipo: str | None = None,
    artigo_cadh: str | None = None,
    ano_de: int | None = None,
    ano_ate: int | None = None,
    idioma_preferido: str = "por",
    max_resultados: int = 10,
    max_tokens_paragrafo: int = 600,
) -> str:
    """
    Busca jurisprudência da Corte Interamericana de Direitos Humanos por
    PARÁGRAFO citável. Use ajuda_sintaxe_corteidh() antes de consultas complexas.

    A Corte se cita por parágrafo ("Caso X Vs. Y, par. 89"); esta ferramenta
    devolve o parágrafo literal, mais a citação já na forma canônica.

    IDIOMA: prefere português. Não havendo versão portuguesa, devolve o original
    com `exige_traducao=True` — e a peça DEVE trazer a tradução ao lado, marcada
    "tradução livre". Vale para espanhol, inglês e francês.

    Args:
        consulta: termos de busca (FTS5; acentos são ignorados na indexação)
        tema: filtro temático (saúde, migração, povos indígenas, ...)
        estado: país demandado ("Brasil", "Chile", ...)
        tipo: CC (caso contencioso) | OC (parecer consultivo) — os ÚNICOS
              com texto indexado. Pedir SS (supervisão) ou outro tipo
              devolve AVISO EXPLÍCITO, nunca resultado vazio.
        artigo_cadh: artigo da Convenção Americana ("4", "5", "8", "25", "26")
        ano_de / ano_ate: faixa de anos da decisão
        idioma_preferido: por (padrão) | esp | ing | fra
        max_resultados: 1–50, padrão 10
        max_tokens_paragrafo: truncamento do parágrafo, 50–4000, padrão 600

    Returns:
        XML com caso, Série/número, data, parágrafo, idioma, exige_traducao e
        citacao_sugerida.

    ATENÇÃO: busca em português NÃO casa com texto em espanhol. Documento sem
    versão portuguesa se acha por `tema`, `artigo_cadh` ou pelo termo em
    espanhol — dois leading cases de saúde (Poblete Vilches e Cuscul Pivaral)
    só existem em espanhol.
    """
    return _buscar_sync(
        consulta=consulta, tema=tema, estado=estado, tipo=tipo,
        artigo_cadh=artigo_cadh, ano_de=ano_de, ano_ate=ano_ate,
        idioma_preferido=idioma_preferido, max_resultados=max_resultados,
        max_tokens_paragrafo=max_tokens_paragrafo,
    )


@mcp.tool()
async def ficha_caso_corteidh(caso: str) -> str:
    """
    Ficha de um caso ou parecer da Corte IDH, por nome ou número.

    Args:
        caso: "Ximenes Lopes", "C-149", "Série C 149", "A-18"

    Returns:
        XML com partes, data, etapa, artigos da CADH violados, reparações
        ordenadas, idiomas disponíveis e a citação canônica.
    """
    return _ficha_sync(caso=caso)


@mcp.tool()
def ajuda_sintaxe_corteidh() -> str:
    """
    Guia de sintaxe, vocabulário e a REGRA DE IDIOMA do corteidh-jurisprudencia.
    Consulte antes de formular consultas ou de citar em peça.
    """
    return """
SINTAXE CORTE IDH — FTS5 sobre parágrafos indexados

BUSCA:
  Termos simples:  buscar_corteidh("saúde mental instituição")
  Frase:           buscar_corteidh("especial vulnerabilidade")
  Por país:        buscar_corteidh("tortura", estado="Brasil")
  Por artigo CADH: buscar_corteidh("vida", artigo_cadh="4")
  Por tipo:        buscar_corteidh("migrante", tipo="OC")
  Faixa de anos:   buscar_corteidh("desaparecimento", ano_de=2010, ano_ate=2020)

TIPOS COM TEXTO INDEXADO (os únicos que a busca por assunto alcança):
  CC Caso Contencioso | OC Parecer Consultivo

COBERTURA — leia junto com a armadilha de idioma abaixo:
  A supervisão de cumprimento (SS, 903 documentos) entra por METADADOS,
  sem texto. Decisão de projeto: guardar o texto dela custaria 59% do
  acervo pelo bloco de menor retorno argumentativo — resolução de
  supervisão demonstra descumprimento estatal persistente, e isso se faz
  pela ficha e pela data, não por transcrição de parágrafo.
  Pedir tipo="SS" na busca devolve AVISO EXPLÍCITO, não lista vazia:
  vazio se leria como "não há precedente", e a verdade seria "não está
  indexado por texto". Para supervisão, use ficha_caso_corteidh(caso=...).

ARMADILHA DE IDIOMA — leia antes de concluir que "não há precedente":
  A busca em PORTUGUÊS não casa com texto em ESPANHOL. "saúde" não encontra
  "salud". Documento sem versão portuguesa se acha por `tema`, por
  `artigo_cadh`, ou pelo termo em espanhol. Os dois leading cases de saúde da
  Corte — Poblete Vilches (Chile, C-349) e Cuscul Pivaral (Guatemala, C-359) —
  só existem em espanhol.

REGRA DE CITAÇÃO EM PEÇA:
  1. Havendo versão em PORTUGUÊS, cite em português. O campo `url_pdf_por` diz
     se existe.
  2. Não havendo, cite no idioma original E traga a tradução em seguida,
     marcada "tradução livre". Vale para espanhol, inglês e francês.
  3. SEMPRE leve o ENDEREÇO da decisão na internet. O `citacao_sugerida` já vem
     com "Disponível em: <url>" — é o que permite ao Defensor conferir na fonte,
     e conferência na fonte é o que impede alucinação. O link aponta para o PDF
     oficial da Corte; o número do parágrafo se acha buscando "89." no próprio
     leitor de PDF.
  4. O link é o do IDIOMA DO TRECHO citado. Citou em espanhol, o endereço é o do
     PDF em espanhol. Mandar o Defensor ao PDF em português ao lado de uma aspa
     em espanhol faz a conferência falhar exatamente onde deveria funcionar.
  5. Use o `citacao_sugerida` verbatim — a forma da Corte tem ordem própria
     (caso, etapa, data, Série, número, par., endereço), e errá-la faz citação
     verdadeira parecer inventada.

  Exemplo de citação com tradução e endereço:
    "La salud es un derecho humano fundamental e indispensable…" (Corte IDH.
    Caso Poblete Vilches Vs. Chile. Sentença de 8 de março de 2018. Série C
    No. 349, par. 118 — tradução livre: "A saúde é um direito humano
    fundamental e indispensável…". Disponível em:
    https://www.corteidh.or.cr/docs/casos/articulos/seriec_349_esp.pdf)

COBERTURA ATUAL (Fase 1): os 11 casos brasileiros e os pareceres consultivos
com versão portuguesa. Consulta que não acha pode ser acervo ainda não
indexado, e não ausência de precedente — o índice se amplia rodando o
corteidh_crawler.py.

O QUE ESTE MCP NÃO FAZ: não vai à rede (o acervo é local), não traduz, e não
diz como STF/STJ receberam o precedente.
"""


if __name__ == "__main__":
    mcp.run()
