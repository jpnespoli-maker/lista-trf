"""Resume o log das duas rotas do CJF (portal antigo x API nova) para decidir qual manter.

Lê o JSONL que `cjf_duas_rotas` grava a cada busca (`LOG_ROTAS`) e imprime, por rota,
disponibilidade, latência, taxa de número CNJ e totais por tribunal, mais a
sobreposição entre as rotas e as buscas em que só uma delas achou algo.

Linhas de busca relaxada (`relaxada: true`) ficam fora das médias e são contadas à
parte: a relaxação é uma segunda tentativa, e misturá-la distorceria as taxas.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MCP = Path(__file__).resolve().parents[1]
if str(MCP) not in sys.path:
    sys.path.insert(0, str(MCP))

from shared.cjf_duas_rotas import LOG_ROTAS  # noqa: E402

LOG_PADRAO: Path = LOG_ROTAS
_ROTAS = ("antiga", "api")


def _p90(valores: List[int]) -> Optional[int]:
    # nearest-rank: o menor valor que deixa 90% das amostras a sua esquerda
    if not valores:
        return None
    ordem = sorted(valores)
    return ordem[math.ceil(0.9 * len(ordem)) - 1]


def _nao_chamada(c: Dict[str, Any]) -> bool:
    """A API não foi chamada porque só o STJ foi pedido (ver `_api_sem_nada`)."""
    return bool(c.get("ok")) and c.get("n") == 0 and bool(c.get("sem_cobertura")) and not c.get("totais")


def _resumir_rota(chamadas: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok = [c for c in chamadas if c.get("ok")]
    # Chamada nunca feita (só-STJ) é lacuna conhecida, não falha: fica em `ok`
    # (disponibilidade), mas o ms 0 dela não mede a rota e sai da latência.
    ms = [c["ms"] for c in ok if not _nao_chamada(c)]
    docs = sum(c.get("n", 0) for c in chamadas)
    com_cnj = sum(c.get("com_cnj", 0) for c in chamadas)
    totais: Dict[str, int] = {}
    for c in chamadas:
        for trib, qtd in (c.get("totais") or {}).items():
            totais[trib] = totais.get(trib, 0) + qtd
    return {
        "chamadas": len(chamadas),
        "disponibilidade": len(ok) / len(chamadas) if chamadas else None,
        "ms_mediana": statistics.median(ms) if ms else None,
        "ms_p90": _p90(ms),
        "taxa_cnj": com_cnj / docs if docs else None,
        "soma_totais_por_tribunal": totais,
    }


def _int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _rota_valida(r: Any) -> bool:
    return (isinstance(r, dict) and isinstance(r.get("ok"), bool) and _int(r.get("n"))
            and isinstance(r.get("ms"), (int, float)) and not isinstance(r.get("ms"), bool)
            and _int(r.get("com_cnj")) and isinstance(r.get("totais"), dict)
            and all(_int(q) for q in r["totais"].values()))


def _valida(linha: Any) -> bool:
    return (isinstance(linha, dict) and isinstance(linha.get("ts"), str)
            and isinstance(linha.get("relaxada"), bool) and _int(linha.get("sobreposicao"))
            and all(_rota_valida(linha.get(r)) for r in _ROTAS))


def resumir(linhas: List[Dict[str, Any]], linhas_invalidas: int = 0) -> Dict[str, Any]:
    normais = [x for x in linhas if not x.get("relaxada")]
    resumo: Dict[str, Any] = {
        "buscas": len(normais),
        "relaxadas": len(linhas) - len(normais),
        "linhas_invalidas": linhas_invalidas,
    }
    for rota in _ROTAS:
        resumo[rota] = _resumir_rota([x[rota] for x in normais])
    # "só X achou" exige que a outra rota tenha RESPONDIDO (ok) e vindo vazia: rota que
    # falhou não achou nada porque não buscou, o que é disponibilidade, não cobertura.
    resumo["so_antiga_achou"] = sum(
        1 for x in normais
        if x["antiga"].get("n", 0) > 0 and x["api"].get("ok") and x["api"].get("n", 0) == 0
        and not _nao_chamada(x["api"]))
    resumo["so_api_achou"] = sum(
        1 for x in normais
        if x["api"].get("n", 0) > 0 and x["antiga"].get("ok") and x["antiga"].get("n", 0) == 0)
    sobre = [x.get("sobreposicao", 0) for x in normais]
    resumo["sobreposicao_media"] = sum(sobre) / len(sobre) if sobre else None
    return resumo


def ler_log(caminho: Path, desde: Optional[str] = None) -> Tuple[List[Dict[str, Any]], int]:
    """Devolve (linhas válidas, nº de linhas inválidas). Log ausente equivale a vazio."""
    if not caminho.exists():
        return [], 0
    validas: List[Dict[str, Any]] = []
    invalidas = 0
    for bruta in caminho.read_text(encoding="utf-8", errors="replace").splitlines():
        if not bruta.strip():
            continue
        try:
            linha = json.loads(bruta)
        except json.JSONDecodeError:
            invalidas += 1
            continue
        if not _valida(linha):
            invalidas += 1
            continue
        if desde and str(linha.get("ts", ""))[:10] < desde:
            continue
        validas.append(linha)
    return validas, invalidas


def _fmt(valor: Any, formato: str = "{}") -> str:
    return "—" if valor is None else formato.format(valor)


def formatar(resumo: Dict[str, Any]) -> str:
    out = [
        f"buscas: {resumo['buscas']}",
        f"relaxadas (fora das medias): {resumo['relaxadas']}",
        f"linhas invalidas: {resumo['linhas_invalidas']}",
    ]
    for rota in _ROTAS:
        r = resumo[rota]
        totais = ", ".join(f"{t}={q}" for t, q in sorted(r["soma_totais_por_tribunal"].items()))
        out += [
            "",
            f"[{rota}]",
            f"  chamadas: {r['chamadas']}",
            f"  disponibilidade: {_fmt(r['disponibilidade'], '{:.0%}')}",
            f"  ms mediana: {_fmt(r['ms_mediana'], '{:.0f}')}",
            f"  ms p90: {_fmt(r['ms_p90'], '{:.0f}')}",
            f"  taxa de CNJ: {_fmt(r['taxa_cnj'], '{:.0%}')}",
            f"  totais por tribunal: {totais or '—'}",
        ]
    out += [
        "",
        f"sobreposicao media: {_fmt(resumo['sobreposicao_media'], '{:.1f}')}",
        f"so a antiga achou: {resumo['so_antiga_achou']}",
        f"so a API achou: {resumo['so_api_achou']}",
    ]
    return "\n".join(out)


def _data_iso(texto: str) -> str:
    try:
        return date.fromisoformat(texto).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"data invalida {texto!r}; use AAAA-MM-DD")


def principal(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Compara o portal antigo e a API do CJF pelo log.")
    ap.add_argument("--log", type=Path, default=LOG_PADRAO, help="JSONL do log (padrao: %(default)s)")
    ap.add_argument("--desde", type=_data_iso, metavar="AAAA-MM-DD", help="considera so as buscas desta data em diante")
    args = ap.parse_args(argv)
    linhas, invalidas = ler_log(args.log, args.desde)
    if not linhas:
        print("nenhuma busca registrada")
        if invalidas:
            print(f"linhas invalidas: {invalidas}")
        return 0
    print(formatar(resumir(linhas, invalidas)))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # o "—" quebra no cp1252 do console do Windows
    sys.exit(principal())
