"""Aquisição e manutenção do acervo da Corte IDH — script de MANUTENÇÃO.

A AQUISIÇÃO vai à rede; o `server.py` NUNCA vai. Essa separação é o que garante
que uma consulta durante a redação de uma peça não dependa da disponibilidade
do site. Metade dos comandos aqui também não vai à rede, e isso está dito em
cada um — porque quem recupera um acervo perdido precisa saber, antes de
digitar, se depende de terceiro.

Aquisição (REDE):
  --semear            recorte em português: 11 casos BR + pareceres
  --fase2             acervo completo pelo catálogo oficial (horas)
  --censo TIPO        só descobre o que existe
  --temas             mapa temático pelos Cadernos

Manutenção (OFFLINE):
  --reindexar-do-texto   refaz o índice a partir do texto guardado (~20s)
  --exportar-metadados   grava o `_metadados.jsonl` ao lado do texto
  --migrar-fts           converte índice antigo em FTS5 external-content
  --glossario            semeia o glossário pt->es/en/fr
  --backfill-estado      preenche `estado` a partir do nome do caso

Comuns a todos: `--banco <caminho>` e `--pasta-texto <dir>`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import baixador
import cadernos
import extrator_paragrafos as ep
import indice
import titulo_catalogo

# Irmã do banco, e derivada dele de propósito: apontando `CORTEIDH_DB` para
# outro lugar, o texto extraído acompanha em vez de ficar órfão na árvore de
# origem. Hoje resolve para o mesmo caminho de sempre.
PASTA_TEXTO_PADRAO = indice.CAMINHO_PADRAO.parent / "texto"

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


NOME_METADADOS = "_metadados.jsonl"

# Tudo o que o `.txt` NÃO carrega. `paragrafo` fica de fora de propósito: o
# texto já está nos arquivos, e duplicá-lo aqui desfaria a economia que
# justifica o desenho e criaria duas fontes da mesma verdade. A ordem importa
# — `documento` primeiro, porque as demais têm chave estrangeira para ela.
_TABELAS_DE_METADADOS = (
    "documento", "artigo_cadh", "tema", "tema_paragrafo", "reparacao",
    "recepcao", "glossario",
)


def exportar_metadados(con, destino: Path) -> dict:
    """Grava ao lado do texto o que o texto não sabe. Devolve a contagem.

    JSONL, e não SQL nem binário, pelas mesmas razões que fizeram o acervo ser
    `.txt`: é grepável, diffável e o git versiona linha a linha. Junto dos
    `.txt`, é o retrato completo — em formato de texto — de um banco que o
    `.gitignore` descarta.

    A ordem de saída é determinística (tabela, depois `rowid`) para que duas
    exportações do mesmo banco gerem arquivos idênticos: exportação que
    embaralha linhas produz diff gigante a cada corrida e o artefato deixa de
    ser versionável na prática.
    """
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    contagem: dict[str, int] = {}
    with destino.open("w", encoding="utf-8", newline="\n") as saida:
        for tabela in _TABELAS_DE_METADADOS:
            colunas = [r[1] for r in con.execute(f'PRAGMA table_info("{tabela}")')]
            if not colunas:
                continue
            n = 0
            for linha in con.execute(f'SELECT * FROM "{tabela}" ORDER BY rowid'):
                registro = {"_tabela": tabela}
                registro.update({c: linha[c] for c in colunas})
                saida.write(json.dumps(registro, ensure_ascii=False) + "\n")
                n += 1
            contagem[tabela] = n
    return contagem


def importar_metadados(con, origem: Path) -> dict:
    """Repõe as tabelas de metadados. Idempotente. Devolve a contagem.

    `INSERT OR REPLACE` e o **id preservado**: o `.txt` se amarra ao documento
    pela URL, mas `paragrafo.documento_id` é o id numérico. Deixar o SQLite
    reatribuir ids reataria os parágrafos ao documento errado, e a peça citaria
    outro caso — plausível, numerado e falso.
    """
    contagem: dict[str, int] = {}
    with Path(origem).open(encoding="utf-8") as entrada:
        for linha in entrada:
            linha = linha.strip()
            if not linha:
                continue
            registro = json.loads(linha)
            tabela = registro.pop("_tabela")
            campos = list(registro)
            con.execute(
                'INSERT OR REPLACE INTO "%s" (%s) VALUES (%s)' % (
                    tabela, ", ".join('"%s"' % c for c in campos),
                    ", ".join("?" * len(campos))),
                [registro[c] for c in campos],
            )
            contagem[tabela] = contagem.get(tabela, 0) + 1
    con.commit()
    return contagem


def nome_txt_da_url(url: str) -> str:
    """`.../seriec_149_por.pdf` -> `seriec_149_por.txt`.

    É o par que amarra o texto ao documento: o `.txt` não guarda o id, então a
    ligação de volta se faz casando este nome com a coluna `url_<idioma>` dos
    metadados. Uma função só, usada pela escrita e pela leitura, para que as
    duas não possam divergir.
    """
    return url.rsplit("/", 1)[-1].replace(".pdf", ".txt")


def gravar_paragrafos_txt(pasta_texto: Path, url: str, paragrafos) -> Path:
    """Grava o artefato durável de UM documento. Inverso de `ler_paragrafos_do_txt`.

    `.txt` e não `.md` de propósito — Markdown convida a reflow, e reflow
    destrói a fronteira de parágrafo, que é a unidade de citação deste projeto
    inteiro.
    """
    pasta_texto = Path(pasta_texto)
    pasta_texto.mkdir(parents=True, exist_ok=True)
    destino = pasta_texto / nome_txt_da_url(url)
    destino.write_text(
        "\n\n".join(_bloco_txt(p) for p in paragrafos), encoding="utf-8")
    return destino


def reindexar_do_texto(con, pasta_texto: Path) -> dict:
    """Refaz o índice a partir do texto guardado. NÃO vai à rede.

    É a rota que a spec §4.2 anunciava desde 17/09/2026 e que não existia em
    código até 19/09/2026: o crawler escrevia os `.txt` e nunca os relia, de
    modo que a única via para um banco povoado era o crawl de horas — o
    oposto do que a decisão de guardar o texto queria comprar.

    Duas pernas, porque o texto sozinho não basta: o `_metadados.jsonl` repõe
    caso, Estado, data, etapa e URL, e os `.txt` repõem os parágrafos. Faltando
    o primeiro, a função RECUSA em vez de produzir um acervo sem citação — um
    banco só com parágrafos responde busca com caso vazio, e isso se lê como
    dado corrompido tarde demais.

    Devolve o relatório, e nele `sem_documento` é o que mais importa: arquivo
    de texto sem documento declarado seria omissão silenciosa, então ele é
    NOMEADO em vez de pulado.

    **O que NÃO se preserva, e é inócuo:** a ordem entre resultados de score
    bm25 EMPATADO. O desempate cai no rowid interno, que difere entre um banco
    montado na ordem do crawl e outro na ordem da varredura de pasta — medido
    em 19/09/2026 sobre o acervo real, na consulta "reparação integral", onde
    dois parágrafos empatam em -13.143994 e trocam de posição. O conjunto
    devolvido é o mesmo; ordem entre empatados nunca foi contrato do SQLite.
    Conferido no resto: 108.893 parágrafos idênticos campo a campo, 1.481
    documentos idênticos, mesmas contagens em todas as tabelas.
    """
    pasta_texto = Path(pasta_texto)
    metadados = pasta_texto / NOME_METADADOS
    if not metadados.exists():
        raise FileNotFoundError(
            f"{NOME_METADADOS} ausente em {pasta_texto}. Sem ele não há caso, "
            f"Estado nem URL, e o índice sairia sem citação. Gere-o com "
            f"`--exportar-metadados` enquanto o banco ainda existir."
        )
    importar_metadados(con, metadados)

    # Mapa nome-do-arquivo -> (documento_id, idioma), montado de uma vez.
    por_arquivo: dict[str, tuple[int, str]] = {}
    for linha in con.execute(
            "SELECT id, url_por, url_esp, url_ing, url_fra FROM documento"):
        for idi in ("por", "esp", "ing", "fra"):
            url = linha["url_" + idi]
            if url:
                por_arquivo[nome_txt_da_url(url)] = (linha["id"], idi)

    docs: set[int] = set()
    n_par = 0
    orfaos: list[str] = []
    arquivos = sorted(p for p in pasta_texto.glob("*.txt"))
    for arq in arquivos:
        alvo = por_arquivo.get(arq.name)
        if alvo is None:
            orfaos.append(arq.name)
            continue
        doc_id, idioma = alvo
        paragrafos = ler_paragrafos_do_txt(arq.read_text(encoding="utf-8"))
        if not paragrafos:
            continue
        suspeitos = {p.numero for p in paragrafos if _SUSPEITO.search(p.texto)}
        suspeitos |= ep.detectar_paragrafos_grandes_demais(paragrafos)
        indice.inserir_paragrafos(con, doc_id, idioma, paragrafos,
                                  suspeitos=suspeitos)
        docs.add(doc_id)
        n_par += len(paragrafos)

    return {
        "arquivos": len(arquivos) - len(orfaos),
        "documentos": len(docs),
        "paragrafos": n_par,
        "sem_documento": orfaos,
    }


_INICIO_DE_BLOCO = re.compile(r"^(\d+)\.[ ]")


def ler_paragrafos_do_txt(bruto: str) -> list["ep.Paragrafo"]:
    """Inverso de `_bloco_txt`: relê o artefato durável de volta em objetos.

    **Separar por linha em branco é medição, não convenção.** Aferido em
    19/09/2026 contra o acervo inteiro antes de existir este parser: os 627
    arquivos têm 108.893 blocos numerados para 108.893 parágrafos no banco,
    **zero** blocos órfãos e **zero** parágrafos cujo texto contenha linha em
    branco. Não fosse assim, o separador seria ambíguo e a releitura perderia
    trecho em silêncio — que é a família de defeito que este subsistema mais
    teme, porque um parágrafo a menos não se anuncia.

    Dentro do bloco, só a PRIMEIRA linha pode abrir a numeração. Linha
    posterior iniciada por "N. " é corpo, não parágrafo novo — a Corte cita
    artigo assim o tempo todo ("Conforme o artigo 8.\\n2. O Estado alegou"), e
    um parser que abrisse bloco ali partiria o parágrafo em dois, com o
    segundo pedaço ganhando número alheio.
    """
    saida: list[ep.Paragrafo] = []
    for bloco in bruto.split("\n\n"):
        if not bloco.strip():
            continue
        linhas = bloco.split("\n")
        titulos: list[str] = []
        while linhas and linhas[0].startswith(_MARCADOR_TITULO_REMOVIDO):
            titulos.append(linhas.pop(0)[len(_MARCADOR_TITULO_REMOVIDO):])
        if not linhas:
            continue
        casou = _INICIO_DE_BLOCO.match(linhas[0])
        if not casou:
            continue
        corpo = "\n".join(linhas)[casou.end():]
        saida.append(ep.Paragrafo(
            numero=int(casou.group(1)), texto=corpo,
            titulos_removidos=tuple(titulos)))
    return saida


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
    # Formato: um bloco por parágrafo, número explícito, linha em branco entre
    # blocos. Uma função só para escrever e outra para ler (`gravar_paragrafos_txt`
    # / `ler_paragrafos_do_txt`), de modo que o formato não possa divergir entre
    # quem grava e quem reconstrói.
    nome = gravar_paragrafos_txt(pasta_texto, url, paragrafos).name

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


def _exportar_ao_fim_do_crawl(con, pasta_texto: Path) -> None:
    """Deixa os metadados frescos ao lado do texto, sempre que o acervo muda.

    Exportar é passo do CRAWL, e não tarefa que alguém precise lembrar: o
    JSONL só serve se estiver atualizado no momento em que o banco se perde, e
    pedi-lo depois da perda seria tarde. Falha aqui não derruba o crawl, que já
    gravou o que importa — mas é DITA, porque exportação que falha em silêncio
    é a insegurança que este arquivo existe para eliminar.
    """
    try:
        contagem = exportar_metadados(con, Path(pasta_texto) / NOME_METADADOS)
    except Exception as erro:  # noqa: BLE001
        print(f"AVISO: metadados NÃO exportados ({erro}). O texto sozinho não "
              f"reconstrói o índice — rode `--exportar-metadados`.")
        return
    print(f"metadados: {sum(contagem.values())} linhas em {NOME_METADADOS}")


def _reindexar_do_texto_cli(args) -> int:
    """Recuperação pela linha de comando. Falta previsível vira `rc`, não
    traceback: quem digita isto está recuperando acervo perdido, e stack trace
    é a pior forma de dizer o que fazer em seguida."""
    pasta = Path(args.pasta_texto)
    if not pasta.is_dir():
        print(f"pasta de texto inexistente: {pasta}")
        return 1

    con = indice.abrir(args.banco)
    indice.criar_schema(con)
    t0 = time.time()
    try:
        rel = reindexar_do_texto(con, pasta)
    except FileNotFoundError as erro:
        print(str(erro))
        return 1
    finally:
        con.close()

    print(f"arquivos reindexados : {rel['arquivos']}")
    print(f"documentos           : {rel['documentos']}")
    print(f"parágrafos           : {rel['paragrafos']}")
    print(f"tempo                : {time.time() - t0:.0f}s")
    if rel["sem_documento"]:
        # Declarado, nunca omitido: texto sem documento sai do acervo, e sair
        # em silêncio é o defeito que este projeto mais combate.
        print(f"SEM DOCUMENTO nos metadados ({len(rel['sem_documento'])}) — "
              f"estes NÃO entraram no índice:")
        for nome in rel["sem_documento"][:20]:
            print(f"  {nome}")
    return 0


def _migrar_fts(args) -> int:
    """Converte o índice para external-content e compacta. Não vai à rede.

    Abre com `permitir_legado=True` porque a guarda de `indice.abrir` recusa
    justamente o banco que este comando existe para consertar.

    O `VACUUM` vem DEPOIS e fora de transação, que é exigência do SQLite, e é
    ele que devolve o espaço ao sistema de arquivos: a conversão sozinha apenas
    move as páginas da cópia para a lista livre, e o arquivo continua do mesmo
    tamanho. Ele pede espaço livre igual ao do banco enquanto roda.
    """
    caminho = Path(args.banco) if args.banco else indice.CAMINHO_PADRAO
    if not Path(caminho).exists():
        print(f"banco inexistente: {caminho}")
        return 1

    antes = Path(caminho).stat().st_size
    con = indice.abrir(caminho, permitir_legado=True)
    n_par = con.execute("SELECT COUNT(*) FROM paragrafo").fetchone()[0]
    t0 = time.time()
    converteu = indice.migrar_fts_externo(con)
    if not converteu:
        con.close()
        print(f"já estava em external-content: {caminho}")
        return 0
    con.execute("VACUUM")
    con.close()

    depois = Path(caminho).stat().st_size
    mb = 1024 * 1024
    print(f"parágrafos reindexados : {n_par}")
    print(f"antes                  : {antes / mb:8.1f} MB")
    print(f"depois                 : {depois / mb:8.1f} MB")
    print(f"redução                : {(antes - depois) / mb:8.1f} MB "
          f"({100 * (antes - depois) / antes:.0f}%)")
    print(f"tempo                  : {time.time() - t0:.0f}s")
    return 0


def main(argv=None) -> int:
    # `RawDescriptionHelpFormatter` porque o docstring separa comandos de REDE
    # dos OFFLINE em lista, e o formatter padrão re-quebra tudo num parágrafo
    # corrido — justamente a distinção que quem recupera um acervo precisa ler
    # de relance vira um muro de texto.
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--semear", action="store_true",
                   help="indexa o recorte em português (11 casos BR + OCs)")
    p.add_argument("--censo", metavar="TIPO",
                   help="só descobre o que existe para o TIPO (CC, OC, SS...)")
    p.add_argument("--banco", default=None)
    p.add_argument("--pasta-texto", default=str(PASTA_TEXTO_PADRAO))
    p.add_argument("--glossario", action="store_true",
                   help="só semeia o glossário pt->es/en/fr (não vai à rede)")
    p.add_argument("--backfill-estado", action="store_true",
                   help="preenche `estado` do NOME nos ja indexados (sem rede)")
    p.add_argument("--temas", action="store_true",
                   help="semeia o mapa tematico pelos Cadernos (vai a rede)")
    p.add_argument("--cadernos", default=None,
                   help="numeros de caderno a semear, separados por virgula")
    p.add_argument("--fase2", action="store_true",
                   help="acervo COMPLETO pelo catálogo oficial (horas de rede)")
    p.add_argument("--tipos", default="CC,OC,SS",
                   help="tipos a colher na fase 2 (padrão CC,OC,SS)")
    p.add_argument("--limite", type=int, default=None,
                   help="para a fase 2 depois de N documentos POR TIPO")
    p.add_argument("--pausa", type=float, default=2.0,
                   help="pausa entre idiomas da cascata, em segundos")
    p.add_argument("--migrar-fts", action="store_true",
                   help="converte o indice antigo em FTS5 external-content e "
                        "compacta o banco; nao vai a rede")
    p.add_argument("--reindexar-do-texto", action="store_true",
                   help="refaz o indice a partir do texto guardado; NAO vai a "
                        "rede. E a rota de recuperacao quando o banco se perde")
    p.add_argument("--exportar-metadados", action="store_true",
                   help="grava o _metadados.jsonl ao lado do texto (o que o "
                        "texto nao carrega: caso, Estado, data, etapa, URL)")
    args = p.parse_args(argv)

    if not (args.semear or args.censo or args.glossario or args.fase2
            or args.backfill_estado or args.temas or args.migrar_fts
            or args.reindexar_do_texto or args.exportar_metadados):
        p.error("informe --semear, --censo, --glossario, --fase2, "
                "--backfill-estado, --temas, --migrar-fts, "
                "--reindexar-do-texto ou --exportar-metadados")

    if args.migrar_fts:
        return _migrar_fts(args)

    if args.exportar_metadados:
        con = indice.abrir(args.banco)
        indice.criar_schema(con)
        contagem = exportar_metadados(con, Path(args.pasta_texto) / NOME_METADADOS)
        con.close()
        print(f"metadados em {Path(args.pasta_texto) / NOME_METADADOS}")
        for tabela, n in contagem.items():
            print(f"  {tabela:<16} {n}")
        return 0

    if args.reindexar_do_texto:
        return _reindexar_do_texto_cli(args)

    if args.temas:
        con = indice.abrir(args.banco)
        indice.criar_schema(con)
        so = None
        if args.cadernos:
            so = {int(x) for x in args.cadernos.split(",") if x.strip()}
        r = semear_temas(con, so_numeros=so)
        print()
        for k in ("cadernos", "documentos", "paragrafos", "fora_do_indice",
                  "pulados", "falhas"):
            print(f"  {k:<16} {r[k]}")
        for linha in indice.temas_disponiveis(con):
            print(f"    {linha['tema'][:34]:<34} {linha['n_documentos']:>3} docs")
        con.close()
        return 1 if r["falhas"] else 0

    if args.backfill_estado:
        con = indice.abrir(args.banco)
        indice.criar_schema(con)
        r = backfill_estado(con)
        por_estado = con.execute(
            "SELECT estado, COUNT(*) n FROM documento WHERE estado IS NOT NULL"
            " GROUP BY estado ORDER BY n DESC LIMIT 8").fetchall()
        con.close()
        print(f"candidatos (estado nulo) : {r['candidatos']}")
        print(f"preenchidos              : {r['preenchidos']}")
        print(f"sem Estado no nome       : {r['sem_estado_no_nome']}")
        print("maiores:", {x["estado"]: x["n"] for x in por_estado})
        return 0

    if args.fase2:
        return _fase2(args)

    if args.glossario:
        # Separado do `--semear` de propósito: semear o glossário não toca a
        # rede e leva milissegundos, então quem só precisa dele (banco já
        # povoado, glossário atualizado) não paga o crawl inteiro.
        con = indice.abrir(args.banco)
        indice.criar_schema(con)
        n = indice.semear_glossario(con)
        por_fonte = dict(con.execute(
            "SELECT CASE WHEN fonte = 'curadoria' THEN 'curadoria'"
            "            ELSE 'CADH' END AS f, COUNT(*)"
            " FROM glossario GROUP BY f").fetchall())
        con.close()
        print(f"glossário semeado: {n} termos "
              f"(CADH medido: {por_fonte.get('CADH', 0)}; "
              f"curadoria: {por_fonte.get('curadoria', 0)})")
        return 0

    if args.censo:
        import buscador_oficial as bo
        achados = bo.buscar(tipo=args.censo, pagina_linhas=100)
        print(f"{args.censo}: {len(achados)} documentos")
        for a in achados[:20]:
            print("  ", a["titulo"][:110])
        return 0

    con = indice.abrir(args.banco)
    indice.criar_schema(con)
    # O glossário entra junto do acervo: índice povoado sem ele responde em
    # silêncio pior — consulta em português não casa com texto em espanhol, e
    # 597 dos 598 casos contenciosos só têm espanhol linkado.
    n_glos = indice.semear_glossario(con)
    print(f"glossário: {n_glos} termos")
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
    _exportar_ao_fim_do_crawl(con, pasta)
    # rc != 0 SÓ por falha. Pulado é decisão declarada, não erro — e fazer o rc
    # subir por ele treinaria quem chama a ignorar o rc.
    con.close()
    return 0 if falhas == 0 else 1


# ===========================================================================
# FASE 2 — acervo completo, dirigido pelo CATÁLOGO OFICIAL
#
# A Fase 1 indexou 14 documentos a partir de uma semente escrita à mão, e o
# `_base_url` acima monta a URL por molde fixo a partir de `serie`/`numero`.
# Isso NÃO escala, e a razão é medida: as URLs do catálogo têm SETE formas
# distintas só nos casos contenciosos — `http` e `https`, com e sem `www`, e
# 44 delas com `Seriec_` de S MAIÚSCULO, que o servidor distingue. Nos
# pareceres há ainda sufixo numérico (`_esp1.pdf`, 4 casos) e a forma curta
# `_es.pdf` (2 casos). Molde fixo produziria 404 em dezenas de documentos, e
# 404 aqui se lê como "documento inexistente" — diagnóstico errado.
#
# Portanto a Fase 2 parte da URL QUE O CATÁLOGO DEU e apenas SUBSTITUI o
# sufixo de idioma, preservando caixa, host e sufixo numérico.
#
# Volume medido em 18/09/2026, numa requisição por tipo (o formulário já traz
# `page_rows=3000` e não tem campo de página): CC 598, OC 33, SS 903 = 1.534,
# conferindo com o censo independente por `recordcount`.
# ===========================================================================

_SUFIXO_IDIOMA = re.compile(r"_(por|esp|es|ing|eng|fra|fre)([0-9]*)\.pdf$", re.I)

# Normaliza para HTTPS, e a razão é de SEGURANÇA, não de desempenho: 521 das
# 598 URLs do catálogo são `http://`, isto é, texto claro.
#
# NÃO se afirma ganho de velocidade. A latência do servidor foi medida como
# ERRÁTICA — 0,16 s e 55 s na mesma URL, em corridas diferentes — e a causa
# NÃO FOI APURADA; a suspeita é estrangulamento. Uma hipótese anterior
# atribuiu a lentidão ao host sem `www` e foi FALSEADA pela medição seguinte,
# que inverteu o resultado. Fica registrado como não apurado, e não arredondado
# para a hipótese mais próxima.
_HOST = re.compile(r"^https?://(?:www\.)?corteidh\.or\.cr", re.I)

# Tipos que entram COM TEXTO. O resto entra por metadado — decisão do Defensor
# de 2026-09-17: "guardar texto só dos casos contenciosos e pareceres".
TIPOS_COM_TEXTO_FASE2 = ("CC", "OC")

_MESES_ES = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "setiembre": "09", "octubre": "10",
    "noviembre": "11", "diciembre": "12",
}


def normalizar_host(url: str) -> str:
    return _HOST.sub("https://www.corteidh.or.cr", url, count=1)


# GRAFIAS do sufixo, por idioma. Não é zelo: o servidor usa DUAS grafias para
# o espanhol e a cascata só conhecia uma. Medido na prova de 18/09/2026 — o
# parecer `seriea_30_es.pdf` falhou em TODOS os quatro idiomas porque a
# substituição produzia `_esp.pdf` e o arquivo existente é `_es.pdf`. São 2
# pareceres e 2 supervisões nessa forma; sem as variantes, ficariam
# inalcançáveis e o relatório diria "nenhum idioma devolveu PDF", que se lê
# como documento ausente do servidor.
_GRAFIAS = {
    "por": ("por",),
    "esp": ("esp", "es"),
    "ing": ("ing", "eng"),
    "fra": ("fra", "fre"),
}


def url_do_idioma(url_catalogo: str, idioma: str) -> str | None:
    """URL do mesmo documento noutro idioma, ou None se não houver sufixo.

    `None` é RESPOSTA e não falha: 891 das 903 resoluções de supervisão têm URL
    sem sufixo de idioma (`docs/supervisiones/baena_09_03_26.pdf`), e para elas
    não existe "a versão portuguesa" a construir. Devolver a própria URL nesse
    caso seria pior que devolver nada — a sonda testaria o arquivo original e
    reportaria que o idioma pedido existe.
    """
    url = normalizar_host(url_catalogo)
    if not _SUFIXO_IDIOMA.search(url):
        return None
    return _SUFIXO_IDIOMA.sub(lambda m: f"_{idioma}{m.group(2)}.pdf", url)


def baixar_do_catalogo(url_catalogo: str, *, sessao=None, pausa_s: float = 2.0):
    """Cascata `por → esp → ing → fra` sobre a URL do catálogo.

    Difere de `baixador.baixar_melhor_idioma` num ponto que decide: aquele
    monta `f"{base}_{idioma}.pdf"` a partir de uma base sem sufixo, o que PERDE
    o sufixo numérico e impõe a caixa do molde. Aqui a URL do catálogo é a
    origem e só o idioma muda.

    Devolve `(idioma, corpo, url)`; levanta `baixador.PdfInvalido` com a causa
    de CADA idioma, para que o relatório distinga "não há tradução" de "o site
    piscou" — condutas opostas.
    """
    ses = sessao or baixador._sessao_padrao()
    primeira = url_do_idioma(url_catalogo, baixador.CASCATA[0])
    if primeira is None:
        raise baixador.PdfInvalido(
            f"{url_catalogo}: URL sem sufixo de idioma — não é documento com "
            f"versões por idioma (típico de supervisão de cumprimento)")

    causas: list[str] = []
    for i, idioma in enumerate(baixador.CASCATA):
        for grafia in _GRAFIAS.get(idioma, (idioma,)):
            url = url_do_idioma(url_catalogo, grafia)
            try:
                corpo = baixador.baixar_pdf(url, sessao=ses, tentativas=2)
            except baixador.PdfInvalido as e:
                causas.append(f"{grafia}: {e}")
                continue
            # O idioma devolvido é o CANÔNICO, não a grafia do arquivo: o
            # índice tem colunas `url_esp`/`sha256_esp`, e gravar "es" faria
            # `_url_do_idioma` no servidor procurar `url_es`, que não existe —
            # a citação sairia sem link, em silêncio.
            return idioma, corpo, url
        if i < len(baixador.CASCATA) - 1 and pausa_s:
            time.sleep(pausa_s)
    raise baixador.PdfInvalido(
        f"{url_catalogo}: nenhum de {baixador.CASCATA} devolveu PDF — "
        + " | ".join(causas))


def iso_da_data_espanhola(bruta: str | None) -> str | None:
    """"14 de mayo de 2026" -> "2026-05-14"; None se não casar.

    `None` é declaração de ignorância, e ela sobrevive até a citação: o
    `citacao.data_por_extenso` trata data ausente devolvendo "Sentença." em
    vez de "Sentença de None." — inventar data seria pior que omiti-la.
    """
    if not bruta:
        return None
    m = re.search(r"([0-9]{1,2})\s+de\s+([a-zA-Zçéíóú]+)\s+de\s+([0-9]{4})",
                  bruta)
    if not m:
        return None
    mes = _MESES_ES.get(m.group(2).lower())
    if not mes:
        return None
    return f"{m.group(3)}-{mes}-{int(m.group(1)):02d}"


def estado_do_caso(caso: str | None) -> str | None:
    """Estado demandado, LIDO do nome do caso. None quando não há.

    REVERSÃO DECLARADA de uma decisão minha de 18/09/2026. Eu havia deixado
    `estado=None` argumentando que tirá-lo do sufixo "Vs. <país>" seria
    INFERÊNCIA. Medindo a consequência, o enquadramento estava errado: o Estado
    demandado **está** no nome — "Vs. Brasil" é a designação que a própria
    Corte dá ao caso, e o `titulo_catalogo` já parseia exatamente esse trecho
    para isolar o nome. Ler campo estruturado não é inferir.

    O que a decisão errada custava, medido: o filtro `estado="Brasil"` devolvia
    **12** documentos — só os semeados à mão — enquanto o acervo já tinha
    outros casos brasileiros (*Comunidades Quilombolas de Alcântara*, *Muniz Da
    Silva*). Resultado curto se lê como ausência de precedente, que é o modo de
    falha que este subsistema inteiro existe para evitar.

    Parecer consultivo não tem parte demandada e não tem `Vs.`: devolve None, e
    `None` continua sendo ignorância declarada onde ela é real.
    """
    if not caso:
        return None
    # O ÚLTIMO `Vs.` é o que separa: nome composto pode trazer mais de um
    # (*Manuela y otros Vs. El Salvador* é o único dos 598 com dois, e ali o
    # segundo é a repetição do cabeçalho — mas aqui operamos sobre o nome JÁ
    # isolado, onde só resta o separador verdadeiro).
    partes = re.split(r"\s+Vs\.\s+", caso)
    if len(partes) < 2:
        return None
    estado = partes[-1].strip().rstrip(".").strip()
    # Um nome de país não tem dígito nem passa de ~40 caracteres; recusar o que
    # não parece país é melhor que gravar lixo num campo de filtro, porque
    # filtro com lixo devolve vazio e vazio se lê como ausência.
    if not estado or len(estado) > 40 or re.search(r"[0-9]", estado):
        return None
    return estado


def registro_do_catalogo(bruto: dict, tipo: str) -> dict:
    """Registro do catálogo com nome, etapa e Estado decompostos do título."""
    partes = titulo_catalogo.decompor(bruto.get("titulo", ""), tipo=tipo)
    return {
        "tipo": tipo,
        "caso": partes["caso"],
        "etapa": partes["etapa"],
        "serie": bruto.get("serie"),
        "numero": bruto.get("numero"),
        "data": iso_da_data_espanhola(bruto.get("data")),
        # LIDO do nome, não inferido — ver `estado_do_caso`. Vem na grafia
        # espanhola do catálogo ("Perú", "México"); o filtro da busca compara
        # sem acento e sem caixa, justamente para que "Peru" case com "Perú".
        "estado": estado_do_caso(partes["caso"]),
        "url": bruto.get("url", ""),
    }


def ja_indexado(con, registro: dict, *, com_texto: bool) -> bool:
    """Já está no índice de forma UTILIZÁVEL?

    Existe para a corrida ser RETOMÁVEL. O harness ceifa tarefa longa quando a
    memória aperta, e a colheita de 631 documentos leva horas — sem retomada,
    um kill aos 70% custaria a corrida inteira.

    O critério difere por tipo, e a diferença importa: para CC/OC exige
    `n_paragrafos > 0`, porque documento com linha e sem parágrafo não serve e
    precisa ser tentado de novo; para SS basta a linha existir, porque metadado
    é tudo o que ele vai ter.

    A IDENTIDADE É A AUTUAÇÃO, NÃO O NOME — e isto é o conserto de um defeito
    medido em 18/09/2026, com a colheita já em curso. *Série C No. 318* É
    aquele julgado, em qualquer língua em que se escreva o título dele. Casando
    por nome, os 14 documentos da Fase 1 — cujos nomes vieram dos PDFs
    portugueses OFICIAIS da própria Corte — não casariam com o nome espanhol do
    catálogo, e **10 dos 16** entrariam DE NOVO como linha nova. A busca
    devolveria o mesmo julgado duas vezes, sob dois nomes, como se fossem
    precedentes distintos:

        C-318  "Trabalhadores da Fazenda Brasil Verde Vs. Brasil"
               "Trabajadores de la Hacienda Brasil Verde Vs. Brasil"

    ARMADILHA da correção, e ela é pior que o defeito: para as 903 resoluções
    de supervisão o `numero` é NULO, então casar só por `(tipo, serie, numero)`
    trataria TODAS como o mesmo documento e a colheita indexaria uma. Por isso
    a autuação só é chave quando `serie` E `numero` existem; sem eles, o nome
    volta a ser a chave, que é o que há.
    """
    tem_autuacao = registro.get("serie") and registro.get("numero") is not None
    if tem_autuacao:
        linha = con.execute(
            "SELECT n_paragrafos FROM documento"
            " WHERE tipo IS ? AND serie IS ? AND numero IS ?",
            (registro["tipo"], registro["serie"], registro["numero"]),
        ).fetchone()
    else:
        if registro.get("caso") is None:
            return False
        # A DATA entra na chave, espelhando `inserir_documento`: sem ela, as
        # 903 resoluções de supervisão colapsam em 358 (um mesmo caso tem
        # várias, e nenhuma é autuada em Série C), e a segunda resolução de um
        # caso sai como "já indexada" tendo ficado de fora.
        linha = con.execute(
            "SELECT n_paragrafos FROM documento"
            " WHERE tipo IS ? AND serie IS ? AND numero IS ? AND caso IS ?"
            "   AND data IS ?",
            (registro["tipo"], registro["serie"], registro["numero"],
             registro["caso"], registro.get("data")),
        ).fetchone()
    if linha is None:
        return False
    return (linha["n_paragrafos"] or 0) > 0 if com_texto else True


def indexar_metadado(con, registro: dict) -> dict:
    """Indexa SEM texto — o caso das resoluções de supervisão.

    Não é meia indexação: é a decisão do Defensor de guardar texto só de casos
    contenciosos e pareceres. O `server.py` recusa busca textual em tipo fora
    de `TIPOS_COM_TEXTO` com aviso EXPLÍCITO, justamente para que um resultado
    vazio aqui nunca se leia como ausência de precedente.
    """
    if registro["caso"] is None:
        return {"caso": None, "erro": None, "idioma": None, "n_paragrafos": 0,
                "lacunas": [],
                "pulado": "nome do caso não isolado do título do catálogo"}
    url = normalizar_host(registro["url"]) if registro["url"] else None
    indice.inserir_documento(
        con, serie=registro["serie"], numero=registro["numero"],
        tipo=registro["tipo"], caso=registro["caso"],
        estado=registro.get("estado"), data=registro["data"],
        etapa=registro.get("etapa"),
        # O catálogo linka o espanhol; é o que se SABE. Não se grava `url_por`
        # por não ter sido sondada — endereço não conferido no índice viraria
        # link na citação, e link que não abre destrói a conferência na fonte.
        url_esp=url,
    )
    con.commit()
    return {"caso": registro["caso"], "erro": None, "idioma": None,
            "n_paragrafos": 0, "lacunas": [], "metadado": True}


def indexar_do_catalogo(con, registro: dict, *, pasta_texto: Path,
                        pausa_s: float = 2.0) -> dict:
    """Baixa pela URL do catálogo, extrai, segmenta e indexa UM documento.

    Gêmeo de `indexar_documento`, com uma diferença: a URL vem do catálogo em
    vez de ser montada por molde. Falhando o download, NADA entra no índice —
    documento pela metade é pior que ausente, porque a busca o acha e o
    parágrafo não existe.
    """
    if registro["caso"] is None:
        return {"caso": None, "erro": None, "idioma": None, "n_paragrafos": 0,
                "lacunas": [],
                "pulado": "nome do caso não isolado do título do catálogo"}
    if not registro["url"]:
        return {"caso": registro["caso"], "erro": None, "idioma": None,
                "n_paragrafos": 0, "lacunas": [],
                "pulado": "catálogo não trouxe URL"}

    try:
        idioma, corpo, url = baixar_do_catalogo(
            registro["url"], pausa_s=pausa_s)
    except baixador.PdfInvalido as e:
        # URL SEM SUFIXO DE IDIOMA é PULADO, não FALHA — e a distinção é a
        # mesma que o `indexar_documento` já faz para número não confirmado.
        # "Falha" diz que a fonte não respondeu; aqui a fonte respondeu tudo o
        # que tinha, e o documento é que não é do tipo que se baixa por idioma.
        #
        # Medido no fecho da corrida de 18/09/2026: o registro 598 do catálogo
        # de CASOS CONTENCIOSOS é *Tabares Toro y otros Vs. Colombia*, cuja URL
        # é `docs/supervisiones/tabares_toro_09_03_26.pdf` — uma resolução de
        # supervisão que o buscador oficial classifica como CC. Contado como
        # falha, ele fazia o `rc` sair 1 numa corrida que varreu os 598 sem um
        # único erro de rede, e `rc` que sobe por decisão declarada treina quem
        # chama a ignorar o `rc`.
        if "URL sem sufixo de idioma" in str(e):
            return {"caso": registro["caso"], "erro": None, "idioma": None,
                    "n_paragrafos": 0, "lacunas": [],
                    "pulado": "URL sem sufixo de idioma — o catálogo o lista "
                              "neste tipo, mas é documento de outro"}
        return {"caso": registro["caso"], "erro": str(e), "idioma": None,
                "n_paragrafos": 0, "lacunas": []}

    texto = ep.extrair_texto_pdf(corpo)
    paragrafos = ep.segmentar(texto)
    lacunas = ep.relatorio_lacunas(paragrafos)
    if not paragrafos:
        # PDF real que não rendeu parágrafo numerado: acontece com documento
        # digitalizado sem camada de texto. Categoria PRÓPRIA, não falha de
        # rede — as condutas são opostas (esta pede OCR, aquela pede repetir).
        return {"caso": registro["caso"], "erro": None, "idioma": idioma,
                "n_paragrafos": 0, "lacunas": [],
                "pulado": f"PDF em {idioma} sem parágrafo numerado "
                          f"(provável digitalização sem camada de texto)"}

    nome = gravar_paragrafos_txt(pasta_texto, url, paragrafos).name

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
    # Mesmos DOIS sinais de contaminação do `indexar_documento`, e pela mesma
    # razão: "Cf."/"Cfr." acha nota de rodapé vazada, e o tamanho anômalo acha
    # cabeçalho, voto ou anexo engolido — um parágrafo pode ter engolido cem
    # nomes de vítimas sem conter "Cf." nenhum. `suspeitos` é o conjunto dos
    # NÚMEROS de parágrafo, que é o que `inserir_paragrafos` espera.
    suspeitos = {p.numero for p in paragrafos if _SUSPEITO.search(p.texto)}
    suspeitos |= ep.detectar_paragrafos_grandes_demais(paragrafos)
    indice.inserir_paragrafos(con, doc_id, idioma, paragrafos,
                              suspeitos=suspeitos)
    con.commit()
    return {"caso": registro["caso"], "erro": None, "idioma": idioma,
            "n_paragrafos": len(paragrafos), "lacunas": lacunas,
            "documento_id": doc_id, "texto": str(pasta_texto / nome),
            "suspeitos": len(suspeitos)}


def _fase2(args) -> int:
    """Colhe o acervo COMPLETO pelo catálogo oficial.

    CC e OC entram com texto; SS entra por metadado — decisão do Defensor. A
    corrida leva horas e é RETOMÁVEL: cada documento é commitado sozinho, e
    `ja_indexado` pula o que já está utilizável. Relançar depois de um kill
    continua de onde parou, e é por isso que o harness ceifar a tarefa não
    custa a corrida inteira.

    O progresso vai para a SAÍDA a cada documento, com `flush`. Sem isso, um
    `.output` vazio é indistinguível de processo travado, e quem acompanha não
    sabe se espera ou se investiga.
    """
    import buscador_oficial as bo

    tipos = [t.strip().upper() for t in args.tipos.split(",") if t.strip()]
    con = indice.abrir(args.banco)
    indice.criar_schema(con)
    indice.semear_glossario(con)
    pasta = Path(args.pasta_texto)
    ses = bo._sessao_padrao()

    total = {"ok": 0, "falhas": 0, "pulados": 0, "ja": 0, "metadado": 0}
    por_idioma: dict[str, int] = {}
    t0 = time.perf_counter()

    for tipo in tipos:
        com_texto = tipo in TIPOS_COM_TEXTO_FASE2
        print(f"\n=== {tipo} ({'texto' if com_texto else 'metadado'}) ===",
              flush=True)
        try:
            brutos = bo.buscar(tipo=tipo, pagina_linhas=3000, sessao=ses)
        except Exception as e:  # noqa: BLE001
            # Falha do CATÁLOGO é diferente de falha de documento: sem o
            # catálogo não há o que colher, e continuar para o tipo seguinte
            # com zero documentos escreveria "0 colhidos" como se o acervo
            # estivesse vazio. Declara-se e segue para o próximo tipo.
            print(f"[CATALOGO FALHOU] {tipo}: {type(e).__name__}: {e}",
                  flush=True)
            total["falhas"] += 1
            continue

        print(f"catálogo: {len(brutos)} documentos", flush=True)
        if args.limite:
            brutos = brutos[: args.limite]
            print(f"limitado a {len(brutos)}", flush=True)

        for i, bruto in enumerate(brutos, 1):
            reg = registro_do_catalogo(bruto, tipo)

            if ja_indexado(con, reg, com_texto=com_texto):
                total["ja"] += 1
                if i % 50 == 0:
                    print(f"[{tipo} {i}/{len(brutos)}] ja indexados: "
                          f"{total['ja']}", flush=True)
                continue

            if not com_texto:
                rel = indexar_metadado(con, reg)
            else:
                rel = indexar_do_catalogo(con, reg, pasta_texto=pasta,
                                          pausa_s=args.pausa)

            if rel.get("pulado"):
                total["pulados"] += 1
                print(f"[{tipo} {i}] PULADO {(reg['caso'] or '?')[:48]}: "
                      f"{rel['pulado'][:90]}", flush=True)
            elif rel["erro"]:
                total["falhas"] += 1
                print(f"[{tipo} {i}] FALHA {(reg['caso'] or '?')[:48]}: "
                      f"{rel['erro'][:130]}", flush=True)
            elif rel.get("metadado"):
                total["metadado"] += 1
                if i % 50 == 0:
                    print(f"[{tipo} {i}/{len(brutos)}] metadados: "
                          f"{total['metadado']}", flush=True)
            else:
                total["ok"] += 1
                por_idioma[rel["idioma"]] = por_idioma.get(rel["idioma"], 0) + 1
                marca = f" LACUNAS={rel['lacunas'][:6]}" if rel["lacunas"] else ""
                print(f"[{tipo} {i}/{len(brutos)}] ok {rel['idioma']} "
                      f"{rel['n_paragrafos']:>4} par  "
                      f"{(reg['caso'] or '?')[:44]}{marca}", flush=True)

    _exportar_ao_fim_do_crawl(con, pasta)
    con.close()
    dt = time.perf_counter() - t0
    print(f"\n=== FASE 2 — {dt / 60:.1f} min ===", flush=True)
    for k in ("ok", "metadado", "ja", "pulados", "falhas"):
        print(f"  {k:<10} {total[k]}")
    print(f"  idiomas    {por_idioma}")
    if por_idioma:
        pt = por_idioma.get("por", 0)
        n = sum(por_idioma.values())
        print(f"  cobertura em PORTUGUES: {pt}/{n} ({100 * pt / n:.1f}%)")
    # rc != 0 SÓ por falha. Pulado é decisão declarada; "já indexado" é a
    # retomada funcionando. Fazer o rc subir por eles treinaria quem chama a
    # ignorar o rc.
    return 1 if total["falhas"] else 0


def backfill_estado(con) -> dict:
    """Preenche `estado` a partir do NOME nos documentos que o têm nulo.

    Existe porque a decisão errada (`estado=None`) já gravou 567 documentos, e
    reprocessá-los pela rede custaria horas para um dado que está no nome que
    já temos em disco. Não vai à rede.

    NÃO sobrescreve `estado` já preenchido: os 12 semeados à mão trazem o nome
    do Estado em PORTUGUÊS ("Brasil"), e o catálogo o traz em espanhol — para
    "Brasil" dá no mesmo, mas a regra vale por princípio: curadoria existente
    não se troca por leitura automática.
    """
    alvos = con.execute(
        "SELECT id, caso FROM documento WHERE estado IS NULL").fetchall()
    tocados = sem_estado = 0
    for linha in alvos:
        estado = estado_do_caso(linha["caso"])
        if estado is None:
            sem_estado += 1
            continue
        con.execute("UPDATE documento SET estado = ? WHERE id = ?",
                    (estado, linha["id"]))
        tocados += 1
    con.commit()
    return {"candidatos": len(alvos), "preenchidos": tocados,
            "sem_estado_no_nome": sem_estado}


# ===========================================================================
# FASE 3 — mapa temático semeado pelos CADERNOS DE JURISPRUDÊNCIA
# ===========================================================================


def semear_tema_de_caderno(con, caderno: dict, *, sessao=None) -> dict:
    """Baixa UM Caderno, lê quem a Corte citou nele e semeia o eixo.

    Só associa documento que JÁ ESTÁ no índice. Um Caderno cita decisões de
    todo o acervo, inclusive as que a Fase 2 não trouxe (supervisões, medidas
    provisórias) — criar linha de `documento` a partir de uma citação seria
    inventar registro a partir de referência, com nome e data que ninguém
    conferiu. O que não casa sai contado, não silenciado.
    """
    url = cadernos.url_do_caderno(caderno)
    try:
        corpo = baixador.baixar_pdf(url, sessao=sessao, tentativas=2)
    except baixador.PdfInvalido as e:
        return {"caderno": caderno["numero"], "erro": str(e),
                "documentos": 0, "paragrafos": 0, "fora_do_indice": 0}

    texto = ep.extrair_texto_pdf(corpo)
    if len(texto) < 5000:
        # PDF sem camada de texto: categoria própria, não falha de rede. As
        # condutas são opostas — esta pediria OCR, aquela pediria repetir.
        return {"caderno": caderno["numero"], "erro": None,
                "documentos": 0, "paragrafos": 0, "fora_do_indice": 0,
                "pulado": f"PDF sem camada de texto ({len(texto)} chars)"}

    consolidado = cadernos.consolidar(texto)
    fonte = cadernos.fonte_do_caderno(caderno)
    tema = caderno["rotulo"]

    docs = pars = fora = 0
    for (serie, numero), dados in consolidado.items():
        linha = con.execute(
            "SELECT id FROM documento WHERE serie = ? AND numero = ?"
            " AND n_paragrafos > 0", (serie, numero)).fetchone()
        if linha is None:
            fora += 1
            continue
        pars += indice.inserir_tema(
            con, linha["id"], tema, fonte=fonte,
            em=time.strftime("%Y-%m-%d"),
            paragrafos=dict(dados["paragrafos"]),
            n_citacoes=dados["mencoes"])
        docs += 1
    con.commit()
    return {"caderno": caderno["numero"], "tema": tema, "erro": None,
            "documentos": docs, "paragrafos": pars, "fora_do_indice": fora,
            "citados": len(consolidado)}


def semear_temas(con, *, so_numeros=None, pausa_s: float = 2.0) -> dict:
    """Semeia todos os Cadernos catalogados. Vai à rede."""
    import buscador_oficial as bo
    ses = bo._sessao_padrao()
    alvos = [c for c in cadernos.CADERNOS
             if not so_numeros or c["numero"] in so_numeros]
    total = {"cadernos": 0, "documentos": 0, "paragrafos": 0,
             "fora_do_indice": 0, "falhas": 0, "pulados": 0}
    for i, caderno in enumerate(alvos, 1):
        if i > 1 and pausa_s:
            time.sleep(pausa_s)
        r = semear_tema_de_caderno(con, caderno, sessao=ses)
        if r.get("pulado"):
            total["pulados"] += 1
            print(f"[{i}/{len(alvos)}] PULADO caderno {r['caderno']}: "
                  f"{r['pulado']}", flush=True)
            continue
        if r["erro"]:
            total["falhas"] += 1
            print(f"[{i}/{len(alvos)}] FALHA caderno {r['caderno']}: "
                  f"{r['erro'][:110]}", flush=True)
            continue
        total["cadernos"] += 1
        total["documentos"] += r["documentos"]
        total["paragrafos"] += r["paragrafos"]
        total["fora_do_indice"] += r["fora_do_indice"]
        print(f"[{i}/{len(alvos)}] caderno {r['caderno']:>2} "
              f"({r['tema'][:30]:<30}) {r['documentos']:>3} docs, "
              f"{r['paragrafos']:>4} pars, {r['fora_do_indice']:>3} fora",
              flush=True)
    return total


if __name__ == "__main__":
    sys.exit(main())
