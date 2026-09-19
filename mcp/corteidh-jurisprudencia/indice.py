"""Índice local da jurisprudência da Corte IDH — SQLite + FTS5.

Fonte de verdade do `server.py`, que NÃO vai à rede. Quem povoa é o
`corteidh_crawler.py`.

A busca corre sobre todos os idiomas indexados e, na hora de devolver o trecho,
prefere o idioma pedido caindo para esp → ing → fra. Documento sem versão
portuguesa tem de aparecer, marcado com `exige_traducao`, e não desaparecer.
"""

from __future__ import annotations

import os
import re
import sqlite3
import unicodedata
from pathlib import Path

_PADRAO_NO_DISCO = (
    Path.home() / ".claude" / "DPU" / "conhecimento" / "corteidh" / "corteidh.db"
)

# `CORTEIDH_DB` existe para o acervo poder morar fora de `~/.claude`, que é a
# árvore desta máquina e não um requisito do MCP. Quem instala o servidor em
# outro projeto — ou em macOS e Linux, onde `~/.claude/DPU` não existe — aponta
# a variável e nada mais muda. Lida na importação: é processo de servidor, que
# nasce com o ambiente já definido.
CAMINHO_PADRAO = Path(os.environ.get("CORTEIDH_DB") or _PADRAO_NO_DISCO)

_CASCATA = ("por", "esp", "ing", "fra")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documento (
  id         INTEGER PRIMARY KEY,
  serie      TEXT,
  numero     INTEGER,
  tipo       TEXT NOT NULL,
  caso       TEXT NOT NULL,
  estado     TEXT,
  data       TEXT,
  etapa      TEXT,
  url_por    TEXT, url_esp TEXT, url_ing TEXT, url_fra TEXT,
  tem_por    INTEGER NOT NULL DEFAULT 0,
  sha256_por TEXT, sha256_esp TEXT,
  n_paragrafos INTEGER NOT NULL DEFAULT 0,
  baixado_em TEXT,
  UNIQUE (tipo, serie, numero, caso)
);
CREATE TABLE IF NOT EXISTS paragrafo (
  documento_id INTEGER NOT NULL REFERENCES documento(id),
  numero  INTEGER NOT NULL,
  idioma  TEXT NOT NULL,
  texto   TEXT NOT NULL,
  -- Aviso de que este parágrafo pode estar contaminado e NÃO deve ser
  -- transcrito verbatim sem conferência na fonte. A Tarefa 1 mediu duas
  -- formas de contaminação que sobrevivem ao extrator: nota de rodapé que
  -- volta a vazar (preço declarado do recuo — ~35-40 mil caracteres em dois
  -- documentos) e palavra deslocada por ordem de leitura do PyMuPDF (2
  -- parágrafos medidos em C-435). Quem POVOA é a Tarefa 6, e só depois de
  -- medir a precisão do sinal candidato contra os casos conhecidos — a coluna
  -- nasce aqui para não exigir migração depois.
  suspeito INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (documento_id, numero, idioma)
);
-- `content='paragrafo'` é EXTERNAL CONTENT, e não é afinação: sem ele o FTS5
-- guarda exemplar PRÓPRIO do texto, e os 108.893 parágrafos ficam no arquivo
-- duas vezes. Medido em 19/09/2026 sobre o acervo inteiro: `paragrafo.texto`
-- 137,5 MB e `paragrafo_fts_content` 138,1 MB — a cópia sozinha respondia por
-- 36% do banco, que caiu de 387,8 MB para 222,6 MB (-43%) com a troca, sem
-- diferença em nenhuma das buscas de controle. O preço é que o FTS deixa de
-- saber o texto: quem apaga entrada tem de devolver-lhe o texto ANTIGO
-- (ver `inserir_paragrafos`) e quem lê junta por `rowid` (ver `buscar`).
CREATE VIRTUAL TABLE IF NOT EXISTS paragrafo_fts USING fts5(
  texto, content='paragrafo', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS artigo_cadh (
  documento_id INTEGER NOT NULL REFERENCES documento(id),
  artigo TEXT NOT NULL, violado INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (documento_id, artigo)
);
CREATE TABLE IF NOT EXISTS tema (
  documento_id INTEGER NOT NULL REFERENCES documento(id),
  tema TEXT NOT NULL,
  fonte TEXT NOT NULL,        -- procedência obrigatória: nada entra sem ela
  em TEXT,
  -- Quantas vezes o Caderno citou este documento no eixo. É medida de PESO
  -- declarada, não ranking inventado: quem lê sabe que 23 citações e 1
  -- citação não valem o mesmo, e sabe de onde o número veio.
  n_citacoes INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (documento_id, tema)
);
-- Os parágrafos que o Caderno citou daquele documento naquele eixo.
--
-- Tabela PRÓPRIA, e não uma coluna `paragrafo_chave`, porque a medição de
-- 18/09/2026 REFUTOU a suposição da spec §6.3 de que haveria um parágrafo
-- destacado por caso: no Caderno 36 (Brasil), apenas 2 dos 11 documentos têm
-- parágrafo dominante; o C-353 é citado 23 vezes em 20 parágrafos DISTINTOS.
-- Eleger "o" parágrafo-chave ali seria arbítrio com aparência de curadoria —
-- quem lesse o campo entenderia que a Corte destacou aquela passagem.
-- Guardam-se todos, com a contagem de cada um, e a ponderação fica com quem
-- redige.
CREATE TABLE IF NOT EXISTS tema_paragrafo (
  documento_id INTEGER NOT NULL REFERENCES documento(id),
  tema TEXT NOT NULL,
  paragrafo INTEGER NOT NULL,
  vezes INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (documento_id, tema, paragrafo)
);
CREATE TABLE IF NOT EXISTS reparacao (
  documento_id INTEGER NOT NULL REFERENCES documento(id),
  ordem INTEGER NOT NULL, texto TEXT NOT NULL,
  PRIMARY KEY (documento_id, ordem)
);
CREATE TABLE IF NOT EXISTS recepcao (
  documento_id INTEGER NOT NULL REFERENCES documento(id),
  tribunal TEXT NOT NULL, processo TEXT NOT NULL,
  sentido TEXT, fonte TEXT, em TEXT,
  PRIMARY KEY (documento_id, tribunal, processo)
);
CREATE TABLE IF NOT EXISTS glossario (
  pt TEXT PRIMARY KEY, es TEXT, en TEXT, fr TEXT,
  -- Procedência, no mesmo regime de `tema.fonte`: nada entra sem ela. Aqui,
  -- porém, ela é MEDIDA e não escrita à mão — `verificar_glossario_cadh.py`
  -- confronta cada termo com o texto oficial da Convenção nas quatro línguas
  -- e grava quais confirmaram (`CADH:pt,es,en,fr`). O que nenhuma confirma sai
  -- `curadoria`, que é ignorância declarada: 60% das linhas, porque a
  -- Convenção é curta e o vocabulário de saúde, migração e prisão é
  -- jurisprudencial, não convencional.
  fonte TEXT NOT NULL DEFAULT 'curadoria'
);
CREATE INDEX IF NOT EXISTS ix_doc_estado ON documento(estado);
CREATE INDEX IF NOT EXISTS ix_doc_tipo   ON documento(tipo);
CREATE INDEX IF NOT EXISTS ix_doc_data   ON documento(data);
"""


def abrir(
    caminho: Path | str | None = None, *, permitir_legado: bool = False,
) -> sqlite3.Connection:
    destino = ":memory:" if caminho == ":memory:" else Path(caminho or CAMINHO_PADRAO)
    if destino != ":memory:":
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino = str(destino)
    con = sqlite3.connect(destino)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    if not permitir_legado:
        _barrar_fts_legado(con)
    # O Estado demandado é gravado na grafia do catálogo, que é ESPANHOLA —
    # "Perú", "México", "Panamá". Quem consulta escreve em português e muitas
    # vezes sem acento, e `d.estado = 'Peru'` não casaria "Perú": o filtro
    # devolveria VAZIO, que neste subsistema se lê como ausência de precedente.
    # Comparar sem acento e sem caixa é o que faz o filtro responder ao que se
    # pergunta. O valor GRAVADO continua sendo o original, intocado.
    con.create_function("SEM_ACENTO", 1, _sem_acento, deterministic=True)
    return con


def fts_e_legado(con: sqlite3.Connection) -> bool:
    """Diz se o banco tem o `paragrafo_fts` na forma ANTIGA, de conteúdo próprio.

    O sinal é a shadow table `paragrafo_fts_content`, que o FTS5 cria para
    guardar a cópia do texto e que **não existe** quando a tabela é declarada
    com `content=`. É sinal estrutural, não heurística.
    """
    return con.execute(
        "SELECT 1 FROM sqlite_master"
        " WHERE type = 'table' AND name = 'paragrafo_fts_content'"
    ).fetchone() is not None


def _barrar_fts_legado(con: sqlite3.Connection) -> None:
    """Recusa abrir banco antigo, porque o erro dele seria SILENCIOSO.

    Até 19/09/2026 o `paragrafo_fts` guardava cópia própria do texto e trazia
    `documento_id`, `numero` e `idioma` como colunas UNINDEXED — o `buscar` lia
    a identificação do parágrafo do próprio FTS. Com `content='paragrafo'`
    essas colunas deixam de existir e a identificação passa a vir da junção
    `p.rowid = f.rowid`. No banco antigo esse rowid é o da ordem de inserção no
    FTS, que **não** corresponde ao da tabela `paragrafo`: a junção casaria
    linha com linha errada e devolveria parágrafo trocado, com citação e
    número plausíveis, sem erro nenhum.

    Por isso barra em vez de tentar adivinhar. Peça que cita parágrafo errado
    da Corte IDH é pior que peça que não cita, e falha que se lê é mais barata
    que resultado que não se confere.
    """
    if not fts_e_legado(con):
        return
    raise RuntimeError(
        "Índice da Corte IDH no formato ANTIGO (paragrafo_fts com conteúdo "
        "próprio). Este código lê o índice por `rowid`, e no formato antigo "
        "isso devolveria PARÁGRAFO TROCADO em silêncio — por isso a abertura "
        "é recusada. Converta uma vez com:\n"
        "  python mcp/corteidh-jurisprudencia/corteidh_crawler.py --migrar-fts\n"
        "A conversão não vai à rede, preserva o acervo e reduz o banco em ~43%."
    )


def _sem_acento(texto):
    """Minúsculas e sem diacrítico; `None` atravessa como `None`.

    Registrada como função SQL, para o filtro de Estado comparar "Peru" com
    "Perú".

    O `None` é DEFENSIVO, não sustenta comportamento — e digo isto porque a
    versão anterior desta docstring afirmava que ele "precisa" devolver `None`
    para que `SEM_ACENTO(NULL)` nunca case. Medido em 18/09/2026, devolvendo
    `""` no lugar: **zero** diferenças observáveis em 13 formas de consulta. A
    razão é que `buscar` só aplica o filtro quando `estado` é truthy, e tanto
    `NULL = 'brasil'` quanto `'' = 'brasil'` são falsos. Mantém-se o `None` por
    ser a semântica SQL convencional para entrada nula, não por carregar peso.
    """
    if texto is None:
        return None
    nfkd = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def criar_schema(con: sqlite3.Connection) -> None:
    con.executescript(_SCHEMA)
    _migrar(con)
    con.commit()


def _migrar(con: sqlite3.Connection) -> list[str]:
    """Acrescenta coluna nova a banco que já existe; devolve o que mudou.

    `CREATE TABLE IF NOT EXISTS` não altera tabela existente — ele NÃO falha e
    NÃO avisa, apenas mantém o esquema antigo. Num banco criado antes de
    `glossario.fonte`, o `INSERT` com `fonte` morreria com `no such column`, e
    num `SELECT` a coluna simplesmente não existiria. Este banco é derivado e
    reconstruível (SETUP §4.1), mas reconstruir é minutos e a migração é
    milissegundos — e, sobretudo, o caminho sem migração falha de um modo que
    se lê como defeito de código.
    """
    feitas: list[str] = []
    colunas = {r["name"] for r in con.execute("PRAGMA table_info(glossario)")}
    if colunas and "fonte" not in colunas:
        con.execute("ALTER TABLE glossario ADD COLUMN fonte TEXT NOT NULL "
                    "DEFAULT 'curadoria'")
        feitas.append("glossario.fonte")
    colunas = {r["name"] for r in con.execute("PRAGMA table_info(tema)")}
    if colunas and "n_citacoes" not in colunas:
        con.execute("ALTER TABLE tema ADD COLUMN n_citacoes INTEGER NOT NULL "
                    "DEFAULT 0")
        feitas.append("tema.n_citacoes")
    return feitas


_DDL_FTS_EXTERNO = (
    "CREATE VIRTUAL TABLE paragrafo_fts USING fts5("
    " texto, content='paragrafo', content_rowid='rowid',"
    " tokenize='unicode61 remove_diacritics 2')"
)


def migrar_fts_externo(con: sqlite3.Connection) -> bool:
    """Converte o `paragrafo_fts` antigo em external-content. Idempotente.

    Devolve `True` se converteu e `False` se o banco já estava na forma nova.
    NÃO faz `VACUUM`: o espaço da cópia apagada vai para a lista livre do
    arquivo, que continua do mesmo tamanho até compactar. Quem compacta é o
    `--migrar-fts` do crawler, porque `VACUUM` não roda dentro de transação e
    precisa de espaço em disco igual ao do banco.

    O texto NÃO é tocado — sai inteiro da tabela `paragrafo`, que é a fonte. O
    que se joga fora é só o índice invertido e a cópia, ambos derivados.
    """
    if not fts_e_legado(con):
        return False
    con.execute("DROP TABLE paragrafo_fts")
    con.execute(_DDL_FTS_EXTERNO)
    con.execute("INSERT INTO paragrafo_fts (paragrafo_fts) VALUES ('rebuild')")
    con.commit()
    return True


def inserir_documento(
    con: sqlite3.Connection, *, serie, numero, tipo, caso, estado, data,
    etapa=None, url_por=None, url_esp=None, url_ing=None, url_fra=None,
    sha256_por=None, sha256_esp=None,
) -> int:
    """Insere ou atualiza; devolve o id. Idempotente INCLUSIVE sem série.

    NÃO usa `ON CONFLICT`, de propósito. Em SQL, `NULL` não é igual a si mesmo
    para efeito de `UNIQUE`, então a cláusula **não dispara** para documento
    sem série — resolução de supervisão de cumprimento, por exemplo, que é o
    que a Fase 2 indexa. O efeito medido pelo revisor em 2026-09-17 era duplo e
    silencioso: cada reindexação criava linha nova, E a atualização de
    metadados ia para uma linha órfã enquanto o `id` devolvido seguia
    apontando para a versão velha.

    A busca explícita com `IS` trata `NULL` como igual a `NULL`, que é a
    semântica desejada aqui. A `UNIQUE` do schema fica como rede para os casos
    com série preenchida, não como mecanismo principal.
    """
    # SEM AUTUAÇÃO, A DATA ENTRA NA CHAVE — e isto conserta uma perda medida em
    # 18/09/2026, de 545 documentos. Um mesmo caso tem VÁRIAS resoluções de
    # supervisão de cumprimento ao longo dos anos (*Vicky Hernández y otras Vs.
    # Honduras* tem 3; *Mujeres Víctimas de Tortura Sexual en Atenco Vs.
    # México*, 4), e nenhuma delas é autuada em Série C. Sem a data na chave, a
    # identidade cai no NOME — que é do CASO e não da RESOLUÇÃO —, e os 903
    # registros do catálogo colapsavam em 358 linhas: cada resolução nova
    # ATUALIZAVA a anterior em vez de entrar.
    #
    # A consequência não era só perder linha. A ficha do caso reporta o ESTADO
    # DO CUMPRIMENTO a partir da série SS, e informá-lo com base numa resolução
    # de 2013 havendo uma de 2024 é afirmação falsa sobre o presente — o tipo
    # de erro que a peça carrega para o juízo.
    #
    # A data só entra quando NÃO há autuação: havendo série e número, eles são
    # a identidade (Série C No. 318 é aquele julgado), e acrescentar a data
    # faria uma reindexação com data ausente duplicar o documento.
    if serie is None and numero is None:
        row = con.execute(
            "SELECT id FROM documento WHERE tipo IS ? AND serie IS ?"
            " AND numero IS ? AND caso IS ? AND data IS ?",
            (tipo, serie, numero, caso, data),
        ).fetchone()
    else:
        row = con.execute(
            "SELECT id FROM documento WHERE tipo IS ? AND serie IS ?"
            " AND numero IS ? AND caso IS ?",
            (tipo, serie, numero, caso),
        ).fetchone()

    if row is not None:
        # COALESCE em tudo: o que a chamada NÃO trouxe, preserva-se. Medido
        # pelo re-revisor: o `UPDATE` incondicional apagava um `url_esp` já
        # gravado quando a reindexação vinha só com o português — e o mesmo
        # valia para `sha256_*`. O crawler chama esta função uma vez por
        # idioma baixado, então sobrescrever com `None` perderia, a cada
        # passada, o que a passada anterior havia descoberto.
        #
        # `tem_por` deixa de ser parâmetro que pode mentir e passa a ser
        # DERIVADO do `url_por` resultante: ou existe endereço em português, ou
        # não existe, e não há terceiro estado.
        con.execute(
            "UPDATE documento SET"
            "   estado = COALESCE(?, estado), data = COALESCE(?, data),"
            "   etapa = COALESCE(?, etapa),"
            "   url_por = COALESCE(?, url_por), url_esp = COALESCE(?, url_esp),"
            "   url_ing = COALESCE(?, url_ing), url_fra = COALESCE(?, url_fra),"
            "   sha256_por = COALESCE(?, sha256_por),"
            "   sha256_esp = COALESCE(?, sha256_esp),"
            "   baixado_em = datetime('now')"
            " WHERE id = ?",
            (estado, data, etapa, url_por, url_esp, url_ing, url_fra,
             sha256_por, sha256_esp, row["id"]),
        )
        con.execute(
            "UPDATE documento SET tem_por = (url_por IS NOT NULL) WHERE id = ?",
            (row["id"],),
        )
        con.commit()
        return row["id"]

    cur = con.execute(
        "INSERT INTO documento (serie, numero, tipo, caso, estado, data, etapa,"
        " url_por, url_esp, url_ing, url_fra, tem_por, sha256_por, sha256_esp,"
        " baixado_em)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))",
        # `tem_por` é DERIVADO aqui também, e não parâmetro: ou existe endereço
        # em português para o documento, ou não existe. Parâmetro separado
        # permitiria a linha afirmar "tem português" sem ter o link — e o link
        # é o instrumento de conferência na fonte que o Defensor exigiu.
        (serie, numero, tipo, caso, estado, data, etapa, url_por, url_esp,
         url_ing, url_fra, 1 if url_por else 0, sha256_por, sha256_esp),
    )
    con.commit()
    return int(cur.lastrowid)


def inserir_paragrafos(
    con, documento_id: int, idioma: str, paragrafos,
    *, suspeitos: set[int] | None = None,
) -> int:
    """Grava os parágrafos e o espelho FTS. Idempotente.

    `suspeitos` são os NÚMEROS de parágrafo a marcar com o aviso de possível
    contaminação (ver o comentário da coluna `suspeito` no schema). Quem decide
    é a Tarefa 6; aqui só se grava o que ela mandar.

    O par retirada-mais-reinserção abaixo opera SÓ sobre os parágrafos
    passados, e isso é requisito medido, não zelo. A versão de antes de
    17/09/2026 apagava o par (documento, idioma) inteiro, então uma chamada com
    SUBCONJUNTO — a Tarefa 6 reprocessando apenas os trechos suspeitos, por
    exemplo — sumia com os demais do índice de busca embora eles continuassem
    intactos na tabela `paragrafo`. As duas tabelas dessincronizavam em
    silêncio: `ficha` e `_melhor_idioma` achavam o parágrafo, `buscar` não.
    Chavear pela chave primária conserva essa propriedade.
    """
    marcados = suspeitos or set()
    dados = [
        (documento_id, p.numero, idioma, p.texto,
         1 if p.numero in marcados else 0)
        for p in paragrafos
    ]
    chaves = [(documento_id, p.numero, idioma) for p in paragrafos]

    # RETIRAR do FTS antes de escrever, e com o texto ANTIGO em mãos. Com
    # `content=`, o FTS5 não guarda o texto: para apagar uma entrada ele exige
    # que se lhe devolva exatamente o texto que foi indexado, senão o índice
    # invertido fica com termos órfãos e a busca passa a casar parágrafo que
    # já não existe. Por isso a leitura vem ANTES do UPSERT — depois dele o
    # texto antigo já se perdeu.
    for did, num, idi in chaves:
        antigo = con.execute(
            "SELECT rowid AS rid, texto FROM paragrafo"
            " WHERE documento_id = ? AND numero = ? AND idioma = ?",
            (did, num, idi),
        ).fetchone()
        if antigo is not None:
            con.execute(
                "INSERT INTO paragrafo_fts (paragrafo_fts, rowid, texto)"
                " VALUES ('delete', ?, ?)", (antigo["rid"], antigo["texto"]),
            )

    con.executemany(
        "INSERT OR REPLACE INTO paragrafo"
        " (documento_id, numero, idioma, texto, suspeito)"
        " VALUES (?,?,?,?,?)", dados,
    )
    # REINDEXAR pelos rowids NOVOS. O `INSERT OR REPLACE` apaga a linha e a
    # reinsere, de modo que o rowid MUDA — o que foi lido no passo anterior já
    # não vale aqui. Reler é o que mantém `paragrafo` e `paragrafo_fts`
    # apontando para a mesma linha.
    for did, num, idi in chaves:
        nova = con.execute(
            "SELECT rowid AS rid, texto FROM paragrafo"
            " WHERE documento_id = ? AND numero = ? AND idioma = ?",
            (did, num, idi),
        ).fetchone()
        if nova is not None:
            con.execute(
                "INSERT INTO paragrafo_fts (rowid, texto) VALUES (?, ?)",
                (nova["rid"], nova["texto"]),
            )
    con.execute(
        "UPDATE documento SET n_paragrafos = ("
        "  SELECT COUNT(DISTINCT numero) FROM paragrafo WHERE documento_id = ?"
        ") WHERE id = ?", (documento_id, documento_id),
    )
    con.commit()
    return len(dados)


_OPERADORES = {"AND", "OR", "NOT"}
_TEM_ALNUM = re.compile(r"[0-9A-Za-zÀ-ÿ]")
_SO_ALNUM = re.compile(r"[0-9A-Za-zÀ-ÿ]+")


def _para_fts(consulta: str) -> str:
    """Converte a consulta do usuário em algo que o FTS5 SEMPRE aceita.

    LISTA NEGRA NÃO FECHA, e foi medido duas vezes. A 1ª versão neutralizava
    `["():*^-]` e estourava na vírgula. A 2ª acrescentou a vírgula — e o
    re-revisor mediu que `.`, `/`, `[`, `{` e `AND`/`OR`/`NOT` soltos seguiam
    levantando `fts5: syntax error`. Pior: **"Vs. Brasil" derrubava a busca**, e
    é o sufixo de todo caso brasileiro da Corte, presente no fixture deste
    próprio projeto. "art. 5", "CF/88", "Lei n. 8.742/93" e "20/06/2018" são o
    vocabulário normal de quem redige peça, não caso de borda — e este é o
    ÚNICO ponto de entrada de busca do servidor.

    A inversão: em vez de enumerar o que proibir, **cada termo vira FRASE entre
    aspas duplas**, e dentro de aspas o FTS5 não interpreta sintaxe alguma. As
    aspas internas se escapam dobrando. Sobrevivem de propósito duas coisas que
    a ajuda do servidor documenta: os operadores em MAIÚSCULA (`AND`/`OR`/`NOT`)
    e o `*` de prefixo — este último apenas em termo puramente alfanumérico,
    porque `"algo,"*` não é sintaxe garantida e o objetivo aqui é jamais
    estourar.

    Contrato: a consulta pode não achar nada; NÃO pode levantar exceção.
    """
    if not consulta or not consulta.strip():
        return '""'

    partes: list[str] = []
    for bruto in consulta.split():
        if bruto in _OPERADORES:
            partes.append(bruto)
            continue

        prefixo = bruto.endswith("*")
        termo = bruto[:-1] if prefixo else bruto

        if not _TEM_ALNUM.search(termo):
            continue  # token só de pontuação ("--", "/") não vira frase vazia

        if prefixo and _SO_ALNUM.fullmatch(termo) and termo not in _OPERADORES:
            partes.append(termo + "*")
            continue

        partes.append('"' + termo.replace('"', '""') + '"')

    # Operador solto na borda, ou dois seguidos, é erro de sintaxe no FTS5.
    while partes and partes[0] in _OPERADORES:
        partes.pop(0)
    while partes and partes[-1] in _OPERADORES:
        partes.pop()
    limpa: list[str] = []
    for tok in partes:
        if tok in _OPERADORES and limpa and limpa[-1] in _OPERADORES:
            continue
        limpa.append(tok)

    return " ".join(limpa) or '""'


def buscar(
    con, *, consulta: str, estado=None, tipo=None, artigo_cadh=None,
    ano_de=None, ano_ate=None, idioma_preferido="por", limite=10,
) -> list[dict]:
    # A identificação do parágrafo vem de `paragrafo`, não do FTS. Com
    # `content='paragrafo'` o índice guarda apenas o `rowid` e os termos: as
    # colunas UNINDEXED que o `SELECT` lia aqui deixaram de existir, e a ponte
    # entre um e outro é `p.rowid = f.rowid`. É a junção que a guarda de
    # `abrir` protege — sobre banco no formato antigo ela casaria linha errada.
    sql = [
        "SELECT p.documento_id AS doc_id, p.numero AS par, p.idioma AS idi,",
        "       p.texto AS texto, d.*, bm25(paragrafo_fts) AS score",
        "  FROM paragrafo_fts f",
        "  JOIN paragrafo p ON p.rowid = f.rowid",
        "  JOIN documento d ON d.id = p.documento_id",
        " WHERE paragrafo_fts MATCH ?",
    ]
    args: list = [_para_fts(consulta)]
    if estado:
        sql.append(" AND SEM_ACENTO(d.estado) = SEM_ACENTO(?)")
        args.append(estado)
    if tipo:
        sql.append(" AND d.tipo = ?"); args.append(tipo)
    if ano_de:
        sql.append(" AND CAST(substr(d.data,1,4) AS INTEGER) >= ?"); args.append(int(ano_de))
    if ano_ate:
        sql.append(" AND CAST(substr(d.data,1,4) AS INTEGER) <= ?"); args.append(int(ano_ate))
    if artigo_cadh:
        sql.append(" AND EXISTS (SELECT 1 FROM artigo_cadh a"
                   " WHERE a.documento_id = d.id AND a.artigo = ?)")
        args.append(str(artigo_cadh))
    sql.append(" ORDER BY score LIMIT ?"); args.append(int(limite) * len(_CASCATA))

    vistos: set[tuple[int, int]] = set()
    saida: list[dict] = []
    for r in con.execute("\n".join(sql), args):
        chave = (r["doc_id"], r["par"])
        if chave in vistos:
            continue
        vistos.add(chave)
        idioma, texto, suspeito = _melhor_idioma(
            con, r["doc_id"], r["par"], idioma_preferido, r["idi"], r["texto"])
        saida.append({
            "documento_id": r["doc_id"], "caso": r["caso"], "serie": r["serie"],
            "numero": r["numero"], "tipo": r["tipo"], "estado": r["estado"],
            "data": r["data"], "etapa": r["etapa"], "paragrafo": r["par"],
            "idioma": idioma, "texto": texto,
            "exige_traducao": idioma != "por",
            # Aviso de possível contaminação do trecho. Vai para a saída do
            # servidor porque quem decide se transcreve verbatim é o Defensor,
            # e ele só decide se souber.
            "suspeito": bool(suspeito),
            # Os QUATRO idiomas, porque a Tarefa 7 escolhe o link pelo idioma
            # do parágrafo devolvido (`url_{idioma}`) e não pode cair em outro.
            "url_por": r["url_por"], "url_esp": r["url_esp"],
            "url_ing": r["url_ing"], "url_fra": r["url_fra"],
        })
        if len(saida) >= limite:
            break
    return saida


def _melhor_idioma(con, doc_id, par, preferido, idioma_achado, texto_achado):
    """Devolve (idioma, texto, suspeito) do parágrafo no melhor idioma."""
    ordem = [preferido] + [i for i in _CASCATA if i != preferido]
    disponiveis = {
        r["idioma"]: (r["texto"], r["suspeito"]) for r in con.execute(
            "SELECT idioma, texto, suspeito FROM paragrafo"
            " WHERE documento_id = ? AND numero = ?",
            (doc_id, par),
        )
    }
    for idi in ordem:
        if idi in disponiveis:
            texto, suspeito = disponiveis[idi]
            return idi, texto, suspeito
    return idioma_achado, texto_achado, 0


def ficha(con, *, caso: str) -> dict | None:
    """Ficha do documento por número de série ("C-149", "Série C 149") ou por
    nome. O casamento por nome pode ser ambíguo, e a ambiguidade sai declarada
    em `outros_candidatos` — ver o comentário no ramo `else`.
    """
    outros: list[str] = []
    m = re.fullmatch(r"\s*(?:s[ée]rie\s+)?([ACE])[-\s]*([0-9]{1,4})\s*",
                     caso, re.IGNORECASE)
    if m:
        row = con.execute(
            "SELECT * FROM documento WHERE serie = ? AND numero = ?",
            (m.group(1).upper(), int(m.group(2))),
        ).fetchone()
    else:
        # Ordena pelo nome MAIS CURTO, que é o casamento mais próximo do termo
        # pedido — e não pela data, como fazia antes. "Vs. Brasil" é sufixo de
        # quase todo caso brasileiro da Corte, então ordenar por data devolvia
        # silenciosamente o mais ANTIGO: `ficha("Vs. Brasil")` respondia
        # Ximenes Lopes (2006) tendo Barbosa de Souza (2021) igualmente no
        # banco. Citar o caso errado é o pior defeito possível aqui, então a
        # ambiguidade também é DECLARADA no retorno, e não só resolvida.
        candidatos = con.execute(
            "SELECT * FROM documento WHERE caso LIKE ?"
            " ORDER BY LENGTH(caso), data",
            (f"%{caso.strip()}%",),
        ).fetchall()
        row = candidatos[0] if candidatos else None
        outros = [r["caso"] for r in candidatos[1:]]
    if row is None:
        return None

    doc_id = row["id"]
    artigos = [r["artigo"] for r in con.execute(
        "SELECT artigo FROM artigo_cadh WHERE documento_id = ? AND violado = 1"
        " ORDER BY CAST(artigo AS INTEGER)", (doc_id,))]
    reparacoes = [r["texto"] for r in con.execute(
        "SELECT texto FROM reparacao WHERE documento_id = ? ORDER BY ordem",
        (doc_id,))]
    idiomas = sorted({r["idioma"] for r in con.execute(
        "SELECT DISTINCT idioma FROM paragrafo WHERE documento_id = ?", (doc_id,))}
        | {c for c in ("por", "esp", "ing", "fra") if row[f"url_{c}"]})
    return {
        "documento_id": doc_id, "caso": row["caso"], "serie": row["serie"],
        "numero": row["numero"], "tipo": row["tipo"], "estado": row["estado"],
        "data": row["data"], "etapa": row["etapa"],
        "n_paragrafos": row["n_paragrafos"],
        "artigos_violados": artigos, "reparacoes": reparacoes,
        "idiomas": idiomas,
        # Os QUATRO idiomas, por simetria com `buscar`: sem `url_ing`/`url_fra`
        # aqui, quem montar link a partir da ficha de um caso que só tenha
        # versão inglesa ou francesa teria de consultar a tabela por fora.
        "url_por": row["url_por"], "url_esp": row["url_esp"],
        "url_ing": row["url_ing"], "url_fra": row["url_fra"],
        # Ambiguidade DECLARADA: os outros casos que casaram o mesmo termo.
        # Vazio quando o casamento foi por número de série ou foi único.
        # Resolver em silêncio faria a peça citar o caso errado sem aviso.
        "outros_candidatos": outros,
    }


# ---------------------------------------------------------------------------
# Glossário: expansão de consulta pt -> es/en/fr
#
# O acervo é esmagadoramente espanhol (medido em 18/09/2026: o buscador oficial
# linka versão espanhola em 597/598 casos contenciosos e portuguesa em ZERO
# deles), e consulta em português não casa com texto em espanhol no FTS —
# "saúde" não encontra "salud". Sem expansão, pesquisar em português sobre saúde
# acharia justamente o que menos importa: os dois leading cases do eixo,
# Poblete Vilches e Cuscul Pivaral, só existem em espanhol.
#
# ASSISTÊNCIA, NÃO GARANTIA — e é por isso que `expandir_consulta` devolve o
# mapa do que expandiu. Busca que procurou outra coisa que não o pedido tem de
# dizer o que procurou; sem isso, zero resultado é indistinguível de ausência
# de precedente, e o Defensor não tem como saber que a barreira era de língua.
# ---------------------------------------------------------------------------


def _normalizar_termo(termo: str) -> str:
    """Minúsculas e sem acento, para que 'SAÚDE' e 'saude' achem 'saúde'.

    O FTS já indexa com `remove_diacritics 2`; a chave do glossário é acentuada
    e canônica, então a comparação se faz sobre a forma normalizada dos dois
    lados. Sem isto, quem digita sem acento — o caso comum — não expande nada.
    """
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", termo.strip().lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def semear_glossario(con: sqlite3.Connection, *, dados=None, fontes=None) -> int:
    """Povoa `glossario`. Idempotente: re-semear atualiza, não duplica.

    `fontes` é o mapa pt -> procedência MEDIDA por `verificar_glossario_cadh.py`
    e versionado em `glossario_fonte.json`. Faltando a entrada, a linha entra
    como `curadoria` — nunca como `CADH`, porque afirmar procedência não medida
    é exatamente o que o campo existe para impedir.
    """
    if dados is None:
        import glossario_dados
        dados = glossario_dados.TERMOS
    if fontes is None:
        import json
        arq = Path(__file__).resolve().parent / "glossario_fonte.json"
        try:
            fontes = json.loads(arq.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            fontes = {}

    n = 0
    for pt, es, en, fr in dados:
        con.execute(
            "INSERT INTO glossario (pt, es, en, fr, fonte) VALUES (?,?,?,?,?)"
            " ON CONFLICT(pt) DO UPDATE SET es=excluded.es, en=excluded.en,"
            " fr=excluded.fr, fonte=excluded.fonte",
            (pt, es, en, fr, fontes.get(pt, "curadoria")))
        n += 1
    con.commit()
    return n


def expandir_consulta(con: sqlite3.Connection, consulta: str) -> tuple[str, list[dict]]:
    """(consulta expandida, o que foi expandido).

    Expande por FRASE antes de por palavra: "acesso à justiça" casa a entrada
    inteira do glossário, e só o que não casou como frase é tentado palavra a
    palavra. Na ordem inversa, "acesso" e "justiça" expandiriam soltos e a
    frase nunca seria consultada.

    A saída é entregue a `_para_fts`, que põe cada termo entre aspas — de modo
    que nada aqui precisa escapar sintaxe do FTS5.
    """
    if not consulta or not consulta.strip():
        return consulta, []

    linhas = {
        _normalizar_termo(r["pt"]): r
        for r in con.execute("SELECT pt, es, en, fr, fonte FROM glossario")
    }
    if not linhas:
        return consulta, []

    # Operadores do usuário passam intactos; não são termo de busca.
    palavras = consulta.split()
    expansoes: list[dict] = []
    saida: list[str] = []
    i = 0
    # Frase mais longa primeiro: o glossário tem entradas de até 6 palavras
    # ("pessoa em situação migratória irregular"), e casar a maior evita que
    # uma entrada curta contida nela roube o casamento.
    maior = max((len(p["pt"].split()) for p in linhas.values()), default=1)

    while i < len(palavras):
        casou = False
        for tamanho in range(min(maior, len(palavras) - i), 0, -1):
            trecho = " ".join(palavras[i:i + tamanho])
            linha = linhas.get(_normalizar_termo(trecho))
            if linha is None:
                continue
            alternativas = [trecho]
            for lang in ("es", "en", "fr"):
                valor = linha[lang]
                if valor and _normalizar_termo(valor) != _normalizar_termo(trecho):
                    alternativas.append(valor)
            if len(alternativas) > 1:
                saida.append("( " + " OR ".join(alternativas) + " )")
                expansoes.append({
                    "termo": trecho, "es": linha["es"], "en": linha["en"],
                    "fr": linha["fr"], "fonte": linha["fonte"],
                })
            else:
                # Entrada cujo par é idêntico em todas as línguas ("tortura",
                # "migrante"): nada a expandir, e declarar expansão vazia
                # sugeriria que a busca fez algo que não fez.
                saida.append(trecho)
            i += tamanho
            casou = True
            break
        if not casou:
            saida.append(palavras[i])
            i += 1

    return " ".join(saida), expansoes


# ---------------------------------------------------------------------------
# Mapa temático — semeado pelos Cadernos de Jurisprudência da Corte
# ---------------------------------------------------------------------------


def inserir_tema(con, documento_id: int, tema: str, *, fonte: str,
                 em: str | None = None, paragrafos=None,
                 n_citacoes: int = 0) -> int:
    """Associa um documento a um eixo temático. Idempotente.

    `fonte` é OBRIGATÓRIA e não tem default: o mapa temático só vale porque
    cada linha diz de onde veio. Uma associação sem procedência é palpite com
    aparência de curadoria, e é exatamente o que a spec §6.3 proíbe.

    `paragrafos` é um mapa {numero: vezes} — todos os que o Caderno citou, e
    não um "parágrafo-chave" eleito. Ver a docstring de `cadernos.py`: a
    medição mostrou que em 9 de 11 documentos a distribuição é plana, e
    escolher um seria arbítrio.
    """
    if not fonte or not str(fonte).strip():
        raise ValueError("tema sem procedência: `fonte` é obrigatória")
    con.execute(
        "INSERT INTO tema (documento_id, tema, fonte, em, n_citacoes)"
        " VALUES (?,?,?,?,?)"
        " ON CONFLICT(documento_id, tema) DO UPDATE SET"
        "   fonte=excluded.fonte, em=excluded.em,"
        "   n_citacoes=excluded.n_citacoes",
        (documento_id, tema, fonte, em, int(n_citacoes)))
    n = 0
    for par, vezes in (paragrafos or {}).items():
        con.execute(
            "INSERT INTO tema_paragrafo (documento_id, tema, paragrafo, vezes)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(documento_id, tema, paragrafo) DO UPDATE SET"
            "   vezes=excluded.vezes",
            (documento_id, tema, int(par), int(vezes)))
        n += 1
    return n


def temas_disponiveis(con) -> list[dict]:
    """Os eixos que existem no índice, com quantos documentos cada um tem.

    Existe para que pedir um tema inexistente possa devolver a LISTA em vez de
    resultado vazio — vazio aqui se leria como "a Corte não tratou disso".
    """
    return [dict(r) for r in con.execute(
        "SELECT tema, COUNT(*) AS n_documentos,"
        "       SUM(n_citacoes) AS n_citacoes,"
        "       MIN(fonte) AS fonte"
        "  FROM tema GROUP BY tema ORDER BY n_documentos DESC")]


def mapa_tematico(con, tema: str, *, limite: int = 20) -> list[dict]:
    """Documentos do eixo, do mais citado no Caderno ao menos.

    A ordem é por `n_citacoes`, que é dado MEDIDO no Caderno — não é score
    calculado por mim. Documento citado 23 vezes e documento citado 1 vez
    aparecem na ordem em que a Corte os tratou naquele eixo, e o número vai
    junto para que ninguém tome a ordem por juízo de força.
    """
    alvo = _sem_acento(tema)
    linhas = con.execute(
        "SELECT t.tema, t.fonte, t.em, t.n_citacoes, d.*"
        "  FROM tema t JOIN documento d ON d.id = t.documento_id"
        " WHERE SEM_ACENTO(t.tema) = ?"
        " ORDER BY t.n_citacoes DESC, d.numero"
        " LIMIT ?", (alvo, int(limite))).fetchall()

    saida = []
    for r in linhas:
        pars = con.execute(
            "SELECT paragrafo, vezes FROM tema_paragrafo"
            " WHERE documento_id = ? AND tema = ?"
            " ORDER BY vezes DESC, paragrafo",
            (r["id"], r["tema"])).fetchall()
        saida.append({
            "documento_id": r["id"], "caso": r["caso"], "serie": r["serie"],
            "numero": r["numero"], "tipo": r["tipo"], "data": r["data"],
            "estado": r["estado"], "etapa": r["etapa"],
            "tem_por": bool(r["tem_por"]),
            "url_por": r["url_por"], "url_esp": r["url_esp"],
            "url_ing": r["url_ing"], "url_fra": r["url_fra"],
            "n_paragrafos": r["n_paragrafos"],
            "tema": r["tema"], "fonte": r["fonte"], "em": r["em"],
            "n_citacoes": r["n_citacoes"],
            # TODOS os parágrafos citados, com a contagem — nunca um eleito.
            "paragrafos_citados": [
                {"paragrafo": p["paragrafo"], "vezes": p["vezes"]}
                for p in pars],
        })
    return saida
