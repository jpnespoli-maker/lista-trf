"""Índice local da jurisprudência da Corte IDH — SQLite + FTS5.

Fonte de verdade do `server.py`, que NÃO vai à rede. Quem povoa é o
`corteidh_crawler.py`.

A busca corre sobre todos os idiomas indexados e, na hora de devolver o trecho,
prefere o idioma pedido caindo para esp → ing → fra. Documento sem versão
portuguesa tem de aparecer, marcado com `exige_traducao`, e não desaparecer.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

CAMINHO_PADRAO = (
    Path.home() / ".claude" / "DPU" / "conhecimento" / "corteidh" / "corteidh.db"
)

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
CREATE VIRTUAL TABLE IF NOT EXISTS paragrafo_fts USING fts5(
  texto, documento_id UNINDEXED, numero UNINDEXED, idioma UNINDEXED,
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
  PRIMARY KEY (documento_id, tema)
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
  pt TEXT PRIMARY KEY, es TEXT, en TEXT, fr TEXT
);
CREATE INDEX IF NOT EXISTS ix_doc_estado ON documento(estado);
CREATE INDEX IF NOT EXISTS ix_doc_tipo   ON documento(tipo);
CREATE INDEX IF NOT EXISTS ix_doc_data   ON documento(data);
"""


def abrir(caminho: Path | str | None = None) -> sqlite3.Connection:
    destino = ":memory:" if caminho == ":memory:" else Path(caminho or CAMINHO_PADRAO)
    if destino != ":memory:":
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino = str(destino)
    con = sqlite3.connect(destino)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def criar_schema(con: sqlite3.Connection) -> None:
    con.executescript(_SCHEMA)
    con.commit()


def inserir_documento(
    con: sqlite3.Connection, *, serie, numero, tipo, caso, estado, data,
    etapa=None, url_por=None, url_esp=None, url_ing=None, url_fra=None,
    tem_por=0, sha256_por=None, sha256_esp=None,
) -> int:
    """Insere ou atualiza; devolve o id. Idempotente pela UNIQUE."""
    con.execute(
        "INSERT INTO documento (serie, numero, tipo, caso, estado, data, etapa,"
        " url_por, url_esp, url_ing, url_fra, tem_por, sha256_por, sha256_esp,"
        " baixado_em)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))"
        " ON CONFLICT (tipo, serie, numero, caso) DO UPDATE SET"
        "   estado=excluded.estado, data=excluded.data, etapa=excluded.etapa,"
        "   url_por=excluded.url_por, url_esp=excluded.url_esp,"
        "   url_ing=excluded.url_ing, url_fra=excluded.url_fra,"
        "   tem_por=excluded.tem_por, sha256_por=excluded.sha256_por,"
        "   sha256_esp=excluded.sha256_esp",
        (serie, numero, tipo, caso, estado, data, etapa, url_por, url_esp,
         url_ing, url_fra, int(tem_por), sha256_por, sha256_esp),
    )
    con.commit()
    return con.execute(
        "SELECT id FROM documento WHERE tipo IS ? AND serie IS ? "
        "AND numero IS ? AND caso IS ?",
        (tipo, serie, numero, caso),
    ).fetchone()["id"]


def inserir_paragrafos(
    con, documento_id: int, idioma: str, paragrafos,
    *, suspeitos: set[int] | None = None,
) -> int:
    """Grava os parágrafos e o espelho FTS. Idempotente.

    `suspeitos` são os NÚMEROS de parágrafo a marcar com o aviso de possível
    contaminação (ver o comentário da coluna `suspeito` no schema). Quem decide
    é a Tarefa 6; aqui só se grava o que ela mandar.
    """
    marcados = suspeitos or set()
    dados = [
        (documento_id, p.numero, idioma, p.texto,
         1 if p.numero in marcados else 0)
        for p in paragrafos
    ]
    con.executemany(
        "INSERT OR REPLACE INTO paragrafo"
        " (documento_id, numero, idioma, texto, suspeito)"
        " VALUES (?,?,?,?,?)", dados,
    )
    # APAGAR antes de reinserir no FTS. A tabela `paragrafo` deduplica pela
    # chave primária, mas `paragrafo_fts` é virtual e não tem chave: um
    # `INSERT` puro duplicaria cada parágrafo a cada reindexação do mesmo
    # documento, e a busca passaria a devolver o mesmo trecho repetido. O
    # `buscar` deduplica por (documento, parágrafo) e esconderia o sintoma,
    # o que torna o defeito silencioso — daí apagar aqui, na origem.
    con.execute(
        "DELETE FROM paragrafo_fts WHERE documento_id = ? AND idioma = ?",
        (documento_id, idioma),
    )
    con.executemany(
        "INSERT INTO paragrafo_fts (texto, documento_id, numero, idioma)"
        " VALUES (?,?,?,?)",
        [(p.texto, documento_id, p.numero, idioma) for p in paragrafos],
    )
    con.execute(
        "UPDATE documento SET n_paragrafos = ("
        "  SELECT COUNT(DISTINCT numero) FROM paragrafo WHERE documento_id = ?"
        ") WHERE id = ?", (documento_id, documento_id),
    )
    con.commit()
    return len(dados)


def _para_fts(consulta: str) -> str:
    """Neutraliza pontuação que o FTS5 leria como sintaxe."""
    limpa = re.sub(r'["():*^-]', " ", consulta).strip()
    return limpa or '""'


def buscar(
    con, *, consulta: str, estado=None, tipo=None, artigo_cadh=None,
    ano_de=None, ano_ate=None, idioma_preferido="por", limite=10,
) -> list[dict]:
    sql = [
        "SELECT f.documento_id AS doc_id, f.numero AS par, f.idioma AS idi,",
        "       f.texto AS texto, d.*, bm25(paragrafo_fts) AS score",
        "  FROM paragrafo_fts f",
        "  JOIN documento d ON d.id = f.documento_id",
        " WHERE paragrafo_fts MATCH ?",
    ]
    args: list = [_para_fts(consulta)]
    if estado:
        sql.append(" AND d.estado = ?"); args.append(estado)
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
    m = re.fullmatch(r"\s*(?:s[ée]rie\s+)?([ACE])[-\s]*([0-9]{1,4})\s*",
                     caso, re.IGNORECASE)
    if m:
        row = con.execute(
            "SELECT * FROM documento WHERE serie = ? AND numero = ?",
            (m.group(1).upper(), int(m.group(2))),
        ).fetchone()
    else:
        row = con.execute(
            "SELECT * FROM documento WHERE caso LIKE ? ORDER BY data LIMIT 1",
            (f"%{caso.strip()}%",),
        ).fetchone()
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
        "idiomas": idiomas, "url_por": row["url_por"], "url_esp": row["url_esp"],
    }
