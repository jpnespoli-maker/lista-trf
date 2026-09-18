"""Aquisição e indexação do acervo da Corte IDH — script de MANUTENÇÃO.

Vai à rede. O `server.py` NÃO. Essa separação é o que garante que uma consulta
durante a redação de uma peça não dependa da disponibilidade do site.

Uso:
  python corteidh_crawler.py --semear          # 11 casos BR + OCs em português
  python corteidh_crawler.py --censo CC        # descobre o que existe
  python corteidh_crawler.py --semear --banco <caminho> --pasta-texto <dir>
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import baixador
import extrator_paragrafos as ep
import indice

PASTA_TEXTO_PADRAO = (
    Path.home() / ".claude" / "DPU" / "conhecimento" / "corteidh" / "texto"
)

_BASE_CASOS = "https://www.corteidh.or.cr/docs/casos/articulos/seriec_{n}"
_BASE_OPINIOES = "https://www.corteidh.or.cr/docs/opiniones/seriea_{n}"

# Assinatura do `suspeito` (Step 7d): "Cf."/"Cfr." dentro do texto do
# parágrafo é o sinal de nota de rodapé vazada. MEDIDA antes de ligar — sobre
# os 14 documentos da semente de 2026-09-18: 136/3975 parágrafos casam
# (3,42%, fração pequena) e a amostra de 5 lidos manualmente veio 5/5
# confirmados como vazamento de nota (precisão alta) — ver task-6-report.md
# §7d para o texto de cada um. As duas condições do Step 7d se cumprem, por
# isso a coluna é populada; não é suposição.
_SUSPEITO = re.compile(r"Cf\.|Cfr\.")

SEMENTE_CNJ = [
    dict(serie="C", numero=149, tipo="CC", estado="Brasil", data="2006-07-04",
         caso="Ximenes Lopes Vs. Brasil"),
    dict(serie="C", numero=161, tipo="CC", estado="Brasil", data="2006-11-28",
         caso="Nogueira de Carvalho e outro Vs. Brasil"),
    dict(serie="C", numero=200, tipo="CC", estado="Brasil", data="2009-07-06",
         caso="Escher e outros Vs. Brasil"),
    dict(serie="C", numero=203, tipo="CC", estado="Brasil", data="2009-09-23",
         caso="Garibaldi Vs. Brasil"),
    dict(serie="C", numero=219, tipo="CC", estado="Brasil", data="2010-11-24",
         caso="Gomes Lund e outros (Guerrilha do Araguaia) Vs. Brasil"),
    dict(serie="C", numero=318, tipo="CC", estado="Brasil", data="2016-10-20",
         caso="Trabalhadores da Fazenda Brasil Verde Vs. Brasil"),
    dict(serie="C", numero=333, tipo="CC", estado="Brasil", data="2017-02-16",
         caso="Favela Nova Brasília Vs. Brasil"),
    dict(serie="C", numero=346, tipo="CC", estado="Brasil", data="2018-02-05",
         caso="Povo Indígena Xucuru e seus membros Vs. Brasil"),
    dict(serie="C", numero=353, tipo="CC", estado="Brasil", data="2018-03-15",
         caso="Herzog e outros Vs. Brasil"),
    dict(serie="C", numero=407, tipo="CC", estado="Brasil", data="2020-07-15",
         caso="Empregados da Fábrica de Fogos de Santo Antônio de Jesus "
              "e seus familiares Vs. Brasil"),
    dict(serie="C", numero=435, tipo="CC", estado="Brasil", data="2021-09-07",
         caso="Barbosa de Souza e outros Vs. Brasil"),
]

SEMENTE_OC = [
    dict(serie="A", numero=18, tipo="OC", estado=None, data="2003-09-17",
         caso="Condição Jurídica e Direitos dos Migrantes Indocumentados"),
    dict(serie="A", numero=21, tipo="OC", estado=None, data="2014-08-19",
         caso="Direitos e Garantias de Crianças no Contexto da Migração"),
    # SÉRIE C, não A: `_base_url` decide a URL pela SÉRIE, e `serie="A"` com
    # `tipo="CC"` montaria `seriea_NNN`, que não existe. O registro falharia no
    # download e sairia como `[FALHA]` — indistinguível de fonte indisponível.
    #
    # NÚMERO RESOLVIDO no Step 7a (2026-09-18): baixados os dois candidatos
    # (`seriec_214_por.pdf` e `seriec_218_por.pdf`) e lido o início do texto
    # extraído de cada um. `seriec_214` é "CASO DA COMUNIDADE INDÍGENA XÁKMOK
    # KÁSEK VS. PARAGUAI", sentença de 24/08/2010 — outro caso, descartado sem
    # ambiguidade. `seriec_218` abre com "CASO VÉLEZ LOOR VS. PANAMÁ",
    # "SENTENÇA DE 23 DE NOVEMBRO DE 2010" — nome e data batem exatamente com
    # este registro (`data="2010-11-23"`). A citação "Série C Nº 218", no
    # formato literal que se esperava na capa, não aparece no corpo do PDF (a
    # sentença não se autocita); a confirmação é por nome + data, não pela
    # string "Serie C No. N".
    dict(serie="C", numero=218, tipo="CC", estado="Panamá", data="2010-11-23",
         caso="Vélez Loor Vs. Panamá"),
]


def _base_url(registro: dict) -> str:
    molde = _BASE_OPINIOES if registro["serie"] == "A" else _BASE_CASOS
    return molde.format(n=registro["numero"])


# Marcador do cabeçalho removido (round 6 do extrator, ver
# `extrator_paragrafos.Paragrafo.titulos_removidos`) dentro do `.txt`
# gravado. Duas exigências: (a) sobreviver ao PDF descartado — sem isto, o
# cabeçalho existe só no objeto em memória e desaparece do artefato durável,
# que é a aposta inteira do projeto (§4.2: o texto é reconstruído OFFLINE a
# partir do `.txt`, nunca de volta ao PDF); (b) ser INEQUIVOCAMENTE distinto
# do texto do parágrafo, para que ninguém o transcreva como se fosse fonte —
# nenhum texto de parágrafo real começa por "[", e a palavra "removido" não
# tem como aparecer por acidente na abertura de uma linha de corpo.
_MARCADOR_TITULO_REMOVIDO = "[titulo removido] "


def _bloco_txt(p: "ep.Paragrafo") -> str:
    """Um parágrafo como bloco gravável no `.txt`.

    Os cabeçalhos removidos (se houver) vão numa linha própria IMEDIATAMENTE
    ANTES do bloco `N. texto` a que pertenciam — é o mesmo parágrafo que os
    engoliu (ver "Round 6" na docstring de `extrator_paragrafos`), então a
    marca fica colada a ele, não a outro. Documento sem título removido
    (`titulos_removidos == ()`) não grava linha de marca nenhuma: um
    marcador vazio seria informação inventada, não uma ausência declarada.
    """
    marcas = "".join(f"{_MARCADOR_TITULO_REMOVIDO}{t}\n" for t in p.titulos_removidos)
    return f"{marcas}{p.numero}. {p.texto}"


def indexar_documento(con, registro: dict, *, pasta_texto: Path) -> dict:
    """Baixa, extrai, segmenta e indexa UM documento.

    Devolve relatório. Falhando o download, NADA entra no índice — documento
    pela metade é pior que documento ausente, porque a busca o acha e o
    parágrafo não existe.
    """
    # Número não confirmado NÃO se tenta adivinhar. Sem esta guarda,
    # `_base_url` montaria `seriec_None` e o registro sairia como `[FALHA]` —
    # indistinguível de fonte indisponível, que é o diagnóstico errado. A
    # semente usa `numero=None` como declaração de ignorância (ver o Vélez
    # Loor), e o relatório tem de dizer "não semeado por número não
    # confirmado", não "falhou".
    if registro.get("numero") is None:
        return {"caso": registro["caso"], "erro": None, "idioma": None,
                "n_paragrafos": 0, "lacunas": [],
                "pulado": "número de série não confirmado — ver Step 7a"}

    base = _base_url(registro)
    try:
        idioma, corpo, url = baixador.baixar_melhor_idioma(base)
    except baixador.PdfInvalido as e:
        return {"caso": registro["caso"], "erro": str(e), "idioma": None,
                "n_paragrafos": 0, "lacunas": []}

    texto = ep.extrair_texto_pdf(corpo)
    paragrafos = ep.segmentar(texto)
    lacunas = ep.relatorio_lacunas(paragrafos)

    # O PDF é TRANSITÓRIO: extrai-se e descarta-se, sem nunca tocar o disco.
    # O artefato durável é o TEXTO (spec §4.2, decisão do Defensor de
    # 2026-09-17). Três razões, e a primeira não é espaço: com o texto
    # guardado, o banco volta a ser construído em minutos e OFFLINE, contra
    # horas de rede num site que estrangula; o PDF não é a via de conferência
    # (o §6.1-bis manda a citação carregar o endereço oficial); e texto é
    # grepável fora do MCP.
    #
    # O `sha256` do PDF descartado vai para o índice, e é a salvaguarda: um
    # download futuro pode ser PROVADO idêntico ao que foi indexado. Isso
    # importa porque os defeitos que exigem geometria de página — palavra
    # deslocada por ordem de leitura, medida em 2 parágrafos de C-435 — não
    # são atacáveis a partir do texto, e a reaquisição precisa ser
    # recuperação verificável, não aposta.
    pasta_texto.mkdir(parents=True, exist_ok=True)
    nome = url.rsplit("/", 1)[-1].replace(".pdf", ".txt")
    # Formato: um bloco por parágrafo, número explícito, linha em branco entre
    # blocos. `.txt` e não `.md` de propósito — Markdown convida a reflow, e
    # reflow destrói a fronteira de parágrafo, que é a unidade de citação
    # deste projeto inteiro.
    (pasta_texto / nome).write_text(
        "\n\n".join(_bloco_txt(p) for p in paragrafos),
        encoding="utf-8",
    )

    doc_id = indice.inserir_documento(
        con, serie=registro["serie"], numero=registro["numero"],
        tipo=registro["tipo"], caso=registro["caso"],
        estado=registro.get("estado"), data=registro["data"],
        etapa=registro.get("etapa"),
        url_por=url if idioma == "por" else None,
        url_esp=url if idioma == "esp" else None,
        url_ing=url if idioma == "ing" else None,
        url_fra=url if idioma == "fra" else None,

        sha256_por=baixador.sha256(corpo) if idioma == "por" else None,
        sha256_esp=baixador.sha256(corpo) if idioma == "esp" else None,
    )
    # Dois sinais independentes de contaminação (Round 6): a assinatura
    # "Cf."/"Cfr." (nota de rodapé vazada) e o TAMANHO anômalo em relação à
    # mediana do próprio documento (cabeçalho, voto ou anexo engolidos por
    # falta de fechamento — ver a docstring de `extrator_paragrafos`). O
    # segundo é mais forte para o pior defeito medido nesta tarefa: um
    # parágrafo pode ter engolido cem nomes de vítimas sem conter "Cf."
    # nenhum.
    suspeitos = {p.numero for p in paragrafos if _SUSPEITO.search(p.texto)}
    suspeitos |= ep.detectar_paragrafos_grandes_demais(paragrafos)
    indice.inserir_paragrafos(con, doc_id, idioma, paragrafos, suspeitos=suspeitos)

    return {"caso": registro["caso"], "erro": None, "idioma": idioma,
            "n_paragrafos": len(paragrafos), "lacunas": lacunas,
            "documento_id": doc_id, "texto": str(pasta_texto / nome)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--semear", action="store_true",
                   help="indexa o recorte em português (11 casos BR + OCs)")
    p.add_argument("--censo", metavar="TIPO",
                   help="só descobre o que existe para o TIPO (CC, OC, SS...)")
    p.add_argument("--banco", default=None)
    p.add_argument("--pasta-texto", default=str(PASTA_TEXTO_PADRAO))
    args = p.parse_args(argv)

    if not args.semear and not args.censo:
        p.error("informe --semear ou --censo")

    if args.censo:
        import buscador_oficial as bo
        achados = bo.buscar(tipo=args.censo, pagina_linhas=100)
        print(f"{args.censo}: {len(achados)} documentos")
        for a in achados[:20]:
            print("  ", a["titulo"][:110])
        return 0

    con = indice.abrir(args.banco)
    indice.criar_schema(con)
    pasta = Path(args.pasta_texto)

    ok = falhas = pulados = 0
    for registro in SEMENTE_CNJ + SEMENTE_OC:
        rel = indexar_documento(con, registro, pasta_texto=pasta)
        # PULADO é categoria própria, e não um terço de falha. Registro sem
        # número confirmado não foi tentado: chamá-lo de falha faria o log
        # dizer que a fonte não respondeu, que é diagnóstico errado.
        if rel.get("pulado"):
            pulados += 1
            print(f"[pulado] {rel['caso']}: {rel['pulado']}")
            continue
        if rel["erro"]:
            falhas += 1
            print(f"[FALHA] {rel['caso']}: {rel['erro']}")
            continue
        ok += 1
        aviso = f"  LACUNAS: {rel['lacunas'][:10]}" if rel["lacunas"] else ""
        print(f"[ok] {rel['caso']} — {rel['idioma']} — "
              f"{rel['n_paragrafos']} paragrafos{aviso}")

    total = ok + falhas + pulados
    print(f"\nindexados {ok}/{total}; falhas {falhas}; pulados {pulados}")
    # rc != 0 SÓ por falha. Pulado é decisão declarada, não erro — e fazer o rc
    # subir por ele treinaria quem chama a ignorar o rc.
    con.close()
    return 0 if falhas == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
