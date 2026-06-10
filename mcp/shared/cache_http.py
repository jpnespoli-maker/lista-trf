"""Cache HTTP local para MCPs de jurisprudência (Onda 2 / A1).

Armazena respostas brutas dos portais (HTML/JSON do CJF, BNP, STF, STJ, Julia,
TJRJ) em SQLite local com TTL diferenciado por MCP. Evita repesquisar a mesma
tese durante um pipeline de pesquisa (cenário comum em PI/recurso).

Decisões de design:

1. **DB separado** (``DPU/cache/jurisprudencia_http.db``) em vez de
   ``conhecimento.db``. Conhecimento.db é regenerado por
   ``gerar_indice_fts.py`` (DROP+CREATE) — armazenar cache lá seria perdê-lo
   a cada rebuild do índice. Cache HTTP é volátil por natureza, vida própria.
2. **PRIMARY KEY = (mcp, key_hash)**. O ``key_hash`` é sha256(json.dumps(key))
   garantindo idempotência mesmo se a ordem dos campos do dict variar
   (``sort_keys=True``).
3. **TTL por entrada**, não global. Cada MCP escolhe seu TTL conforme
   volatilidade da fonte (vinculantes 30d, acórdãos com data fechada 7d,
   portais voláteis 48h).
4. **Auto-purge no read + no write**: registros vencidos são descartados na
   leitura da própria chave, e a cada ``_PURGE_A_CADA_N_WRITES`` gravações um
   DELETE varre todos os vencidos do DB (sem janitor agendado). Custo
   amortizado em <1ms por acesso.
5. **Falha silenciosa**: qualquer OSError/sqlite3 erro NÃO propaga — pesquisa
   é mais importante que cache. Devolve None (cache miss) e segue.

API mínima::

    from shared.cache_http import cached_http, registrar_dispositivo

    chave = {"busca": "alta programada", "tribunais": ["STJ"]}
    body = cached_http("cjf-jurisprudencia", chave)
    if body is None:
        body = fetch_remoto(...)
        registrar_dispositivo("cjf-jurisprudencia", chave, body, ttl_s=7 * 86400)
    return body

Tabela::

    CREATE TABLE cache_http (
      mcp        TEXT NOT NULL,
      key_hash   TEXT NOT NULL,
      key_json   TEXT NOT NULL,    -- para debug
      body       TEXT NOT NULL,
      fetched_at INTEGER NOT NULL, -- epoch
      ttl_s      INTEGER NOT NULL,
      hits       INTEGER NOT NULL DEFAULT 0,
      bytes      INTEGER NOT NULL,
      PRIMARY KEY (mcp, key_hash)
    );
    CREATE INDEX ix_cache_fetched ON cache_http(fetched_at);

CLI auxiliar (estatísticas + purge manual)::

    python -m shared.cache_http --stats
    python -m shared.cache_http --purge-vencidos
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional


CLAUDE_ROOT = Path.home() / ".claude"
DB_PATH = CLAUDE_ROOT / "DPU" / "cache" / "jurisprudencia_http.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_http (
    mcp        TEXT NOT NULL,
    key_hash   TEXT NOT NULL,
    key_json   TEXT NOT NULL,
    body       TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    ttl_s      INTEGER NOT NULL,
    hits       INTEGER NOT NULL DEFAULT 0,
    bytes      INTEGER NOT NULL,
    PRIMARY KEY (mcp, key_hash)
);
CREATE INDEX IF NOT EXISTS ix_cache_fetched ON cache_http(fetched_at);
"""

# TTLs recomendados — cada MCP passa explicitamente ttl_s, estes são defaults
# documentais. Mantemos aqui para o módulo CLI/estatísticas saber agrupar.
TTL_RECOMENDADO = {
    "bnp-api": 30 * 86400,           # vinculantes (RG/RR/SV/SUM) mudam pouco
    "cjf-jurisprudencia": 7 * 86400,  # acórdãos com data fechada
    "stf-jurisprudencia": 7 * 86400,
    "stj-jurisprudencia": 7 * 86400,
    "julia-trf5": 2 * 86400,          # portal mais volátil
    "tjrj-jurisprudencia": 2 * 86400,
}


# Poda automática: a cada N gravações, apaga TODOS os vencidos do DB.
# Contador por processo — servidores MCP são processos longevos, então a
# poda acontece de fato; o primeiro write de cada processo também poda.
_PURGE_A_CADA_N_WRITES = 25
_writes_desde_purge = 0


def _hash_chave(key: dict) -> str:
    """sha256 hex sobre json canônico (sort_keys=True) da chave."""
    blob = json.dumps(key, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _con() -> Optional[sqlite3.Connection]:
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(DB_PATH), timeout=2.0)
        con.executescript(_SCHEMA)
        return con
    except (OSError, sqlite3.Error):
        return None


def cached_http(mcp: str, key: dict) -> Optional[str]:
    """Devolve body cacheado se válido (fetched_at + ttl_s >= now), senão None.

    Em qualquer erro de I/O, devolve None silenciosamente (cache miss).
    Registros vencidos são removidos no momento da leitura.
    """
    con = _con()
    if con is None:
        return None
    try:
        key_hash = _hash_chave(key)
        now = int(time.time())
        row = con.execute(
            "SELECT body, fetched_at, ttl_s FROM cache_http "
            "WHERE mcp = ? AND key_hash = ?",
            (mcp, key_hash),
        ).fetchone()
        if not row:
            return None
        body, fetched_at, ttl_s = row
        if fetched_at + ttl_s < now:
            # Expirado — purgar e retornar miss.
            con.execute(
                "DELETE FROM cache_http WHERE mcp = ? AND key_hash = ?",
                (mcp, key_hash),
            )
            con.commit()
            return None
        # Hit — incrementa contador.
        con.execute(
            "UPDATE cache_http SET hits = hits + 1 "
            "WHERE mcp = ? AND key_hash = ?",
            (mcp, key_hash),
        )
        con.commit()
        return body
    except sqlite3.Error:
        return None
    finally:
        con.close()


def registrar_dispositivo(
    mcp: str,
    key: dict,
    body: str,
    *,
    ttl_s: int,
) -> None:
    """Grava (mcp, key_hash) → body com TTL. Idempotente — REPLACE."""
    if not body or not isinstance(body, str):
        return
    con = _con()
    if con is None:
        return
    global _writes_desde_purge
    try:
        key_hash = _hash_chave(key)
        key_json = json.dumps(key, sort_keys=True, ensure_ascii=False, default=str)
        now = int(time.time())
        con.execute(
            "INSERT OR REPLACE INTO cache_http "
            "(mcp, key_hash, key_json, body, fetched_at, ttl_s, hits, bytes) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (mcp, key_hash, key_json, body, now, int(ttl_s), len(body)),
        )
        # Poda barata dos vencidos a cada N writes (e no primeiro do processo).
        if _writes_desde_purge % _PURGE_A_CADA_N_WRITES == 0:
            con.execute(
                "DELETE FROM cache_http WHERE fetched_at + ttl_s < ?", (now,)
            )
        _writes_desde_purge += 1
        con.commit()
    except sqlite3.Error:
        pass
    finally:
        con.close()


def purgar_vencidos() -> int:
    """Apaga registros vencidos. Devolve quantidade removida."""
    con = _con()
    if con is None:
        return 0
    try:
        now = int(time.time())
        cur = con.execute(
            "DELETE FROM cache_http WHERE fetched_at + ttl_s < ?",
            (now,),
        )
        con.commit()
        return cur.rowcount or 0
    except sqlite3.Error:
        return 0
    finally:
        con.close()


def estatisticas() -> dict:
    """Resumo por MCP: total / vivos / vencidos / hits / bytes."""
    con = _con()
    if con is None:
        return {"db": str(DB_PATH), "erro": "indisponível"}
    try:
        now = int(time.time())
        linhas = con.execute(
            "SELECT mcp, "
            "       COUNT(*) AS total, "
            "       SUM(CASE WHEN fetched_at + ttl_s >= ? THEN 1 ELSE 0 END) AS vivos, "
            "       SUM(hits) AS hits, "
            "       SUM(bytes) AS bytes "
            "FROM cache_http GROUP BY mcp",
            (now,),
        ).fetchall()
        return {
            "db": str(DB_PATH),
            "ttl_recomendado_s": TTL_RECOMENDADO,
            "por_mcp": [
                {
                    "mcp": r[0],
                    "total": r[1],
                    "vivos": r[2] or 0,
                    "vencidos": (r[1] or 0) - (r[2] or 0),
                    "hits": r[3] or 0,
                    "bytes": r[4] or 0,
                }
                for r in linhas
            ],
        }
    except sqlite3.Error as e:
        return {"db": str(DB_PATH), "erro": str(e)}
    finally:
        con.close()


def main() -> int:  # pragma: no cover
    import argparse

    p = argparse.ArgumentParser(description="CLI auxiliar do cache HTTP de jurisprudência")
    p.add_argument("--stats", action="store_true", help="resumo por MCP")
    p.add_argument("--purge-vencidos", action="store_true", help="apaga vencidos")
    args = p.parse_args()
    if args.stats:
        print(json.dumps(estatisticas(), ensure_ascii=False, indent=2))
        return 0
    if args.purge_vencidos:
        n = purgar_vencidos()
        print(json.dumps({"removidos": n}, ensure_ascii=False))
        return 0
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
