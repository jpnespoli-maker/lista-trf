"""Portal antigo do CJF e API nova em PARALELO, lista unificada e log de comparação.

Por que duas rotas ao mesmo tempo (medido em 23/10/2026): o portal antigo devolveu
0 números CNJ em 65 documentos (o `numero` é o nome da classe) e a API, 84 de 90;
já o portal antigo cobre o STJ, que a API não cobre. Nenhuma das duas substitui a
outra, e o log (`LOG_ROTAS`) registra, por busca, o que cada uma devolveu, para
decidir depois qual rota vale a pena manter.

O mesmo julgado vindo das duas rotas NÃO tem chave de identidade comum: número e data
não servem (o antigo traz a classe no lugar do número, e a data falta em parte da
API). Casa-se pelo começo da ementa normalizada (5 de 5 pares no TRF4, 23/10/2026) e
pelo relator.

O log nunca grava texto de documento: só contagens, tempos e erros.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from shared import cjf_api, cjf_client

LOG_ROTAS: Path = Path.home() / ".claude" / "DPU" / "logs" / "cjf_rotas.jsonl"

_TAMANHO_CHAVE = 200
_RE_CNJ = re.compile(r"[0-9]{7}-[0-9]{2}[.][0-9]{4}[.]")
_CAMPOS_NAO_COMPLETADOS = ("indice",)


@dataclass
class ResultadoRota:
    ok: bool
    docs: List[Dict[str, Any]] = field(default_factory=list)
    totais: Dict[str, int] = field(default_factory=dict)
    ms: int = 0
    erro: Optional[str] = None
    sem_cobertura: List[str] = field(default_factory=list)


@dataclass
class ResultadoDuasRotas:
    docs: List[Dict[str, Any]]
    antiga: ResultadoRota
    api: ResultadoRota
    sobreposicao: int


def _normalizar(texto: str) -> str:
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c)
    )
    return re.sub(r"[^a-z0-9]", "", sem_acento.lower())


def chave_ementa(doc: Dict[str, Any]) -> Optional[str]:
    """Primeiros 200 caracteres da ementa normalizada (sem acento, minúscula, só
    ``[a-z0-9]``, sem o rótulo "ementa" inicial, inclusive espaçado). ``None`` se a
    ementa normalizada tiver menos de 200 caracteres: ementa curta ou vazia nunca funde.

    Colisão medida em 23/10/2026 (5 buscas, TRF2+TRF4, 30 documentos por rota, 245
    documentos): 14 pares DISTINTOS com a mesma chave de 200 dentro da mesma rota (rubrica
    padrão repetida em julgados diferentes), 3 deles de relatores diferentes. Por isso o
    casamento entre rotas usa (chave, relator normalizado) e é um para um.
    """
    texto = _normalizar(doc.get("ementa") or "")
    if texto.startswith("ementa"):
        texto = texto[len("ementa"):]
    if len(texto) < _TAMANHO_CHAVE:
        return None
    return texto[:_TAMANHO_CHAVE]


def _chave_par(doc: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    chave = chave_ementa(doc)
    if chave is None:
        return None
    return chave, _normalizar(doc.get("relator") or "")


def _completar(api: Dict[str, Any], antigo: Dict[str, Any]) -> Dict[str, Any]:
    doc = dict(api)
    for campo, valor in antigo.items():
        if campo in _CAMPOS_NAO_COMPLETADOS:
            continue
        if valor and not doc.get(campo):
            doc[campo] = valor
    if doc.get("data_julgamento") and doc.get("orgao_julgador"):
        doc.pop("aviso", None)
    doc["origem"] = "ambas"
    return doc


def fundir(
    docs_antiga: List[Dict[str, Any]], docs_api: List[Dict[str, Any]], n: int
) -> Tuple[List[Dict[str, Any]], int]:
    """Intercala por posição começando pela API; o par casado sai uma vez, na posição
    do primeiro a aparecer. Casamento só entre rotas, um para um. Não muta a entrada."""
    livres: Dict[Tuple[str, str], List[int]] = {}
    for j, doc in enumerate(docs_antiga):
        chave = _chave_par(doc)
        if chave is not None:
            livres.setdefault(chave, []).append(j)

    parceiro_da_api: Dict[int, int] = {}
    parceiro_do_antigo: Dict[int, int] = {}
    for i, doc in enumerate(docs_api):
        chave = _chave_par(doc)
        if chave is not None and livres.get(chave):
            j = livres[chave].pop(0)
            parceiro_da_api[i] = j
            parceiro_do_antigo[j] = i

    posicoes = [(2 * i, "api", i) for i in range(len(docs_api))]
    posicoes += [(2 * j + 1, "antiga", j) for j in range(len(docs_antiga))]
    posicoes.sort()

    saida: List[Dict[str, Any]] = []
    emitidos: set = set()
    for _, rota, idx in posicoes:
        if rota == "api":
            if idx in parceiro_da_api:
                if idx not in emitidos:
                    emitidos.add(idx)
                    saida.append(_completar(docs_api[idx], docs_antiga[parceiro_da_api[idx]]))
            else:
                saida.append({**docs_api[idx], "origem": "api-nova"})
        else:
            if idx in parceiro_do_antigo:
                i = parceiro_do_antigo[idx]
                if i not in emitidos:
                    emitidos.add(i)
                    saida.append(_completar(docs_api[i], docs_antiga[idx]))
            else:
                saida.append({**docs_antiga[idx], "origem": "portal-antigo"})
    return saida[:n], len(parceiro_da_api)


def _rodar(chamada: Callable[[], Tuple[List[Dict[str, Any]], Dict[str, int]]]) -> ResultadoRota:
    t0 = time.monotonic()
    try:
        docs, totais = chamada()
    except Exception as e:  # a falha de uma rota não pode derrubar a outra
        return ResultadoRota(
            ok=False, ms=int((time.monotonic() - t0) * 1000), erro=f"{type(e).__name__}: {e}"
        )
    return ResultadoRota(
        ok=True, docs=list(docs), totais=dict(totais), ms=int((time.monotonic() - t0) * 1000)
    )


def _api_sem_nada(sem_cobertura: List[str]) -> ResultadoRota:
    return ResultadoRota(ok=True, sem_cobertura=sem_cobertura)


def buscar_duas_rotas(
    termo: str,
    tribunais: Sequence[str],
    n: int,
    *,
    relaxada: bool = False,
    teto_s: float = 90.0,
) -> ResultadoDuasRotas:
    pedidos = list(tribunais)
    cobertos = [t for t in pedidos if t.strip().upper() in cjf_api.TRIBUNAIS_COBERTOS]
    sem_cobertura = [t for t in pedidos if t not in cobertos]

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        f_antiga = executor.submit(
            _rodar, lambda: cjf_client._buscar_documentos_jsf(termo, pedidos, n)
        )
        f_api = (
            executor.submit(_rodar, lambda: cjf_api.buscar_unificada(termo, cobertos, n))
            if cobertos
            else None
        )
        esperar = [f for f in (f_antiga, f_api) if f is not None]
        wait(esperar, timeout=teto_s)
        antiga = _resultado(f_antiga, teto_s)
        api = _resultado(f_api, teto_s) if f_api is not None else _api_sem_nada([])
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    api.sem_cobertura = sem_cobertura

    docs, sobreposicao = fundir(antiga.docs, api.docs, n)
    resultado = ResultadoDuasRotas(docs=docs, antiga=antiga, api=api, sobreposicao=sobreposicao)
    _registrar(termo, pedidos, relaxada, resultado)
    return resultado


def _resultado(futuro, teto_s: float) -> ResultadoRota:
    if futuro.done():
        return futuro.result()
    return ResultadoRota(ok=False, ms=int(teto_s * 1000), erro="timeout")


def _resumo_rota(rota: ResultadoRota) -> Dict[str, Any]:
    return {
        "ok": rota.ok,
        "erro": rota.erro,
        "ms": rota.ms,
        "totais": rota.totais,
        "n": len(rota.docs),
        "com_cnj": sum(1 for d in rota.docs if _RE_CNJ.match(d.get("numero") or "")),
        "com_data": sum(1 for d in rota.docs if d.get("data_julgamento")),
        "com_orgao": sum(1 for d in rota.docs if d.get("orgao_julgador")),
    }


def _registrar(
    termo: str, tribunais: List[str], relaxada: bool, resultado: ResultadoDuasRotas
) -> None:
    """Uma linha JSON por chamada, sem texto de documento. Falha de gravação não
    derruba a busca."""
    if os.environ.get("DPU_QUERY_LOG_DESLIGADO") == "1":
        return
    registro = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "busca": termo,
        "tribunais": tribunais,
        "relaxada": relaxada,
        "antiga": _resumo_rota(resultado.antiga),
        "api": {**_resumo_rota(resultado.api), "sem_cobertura": resultado.api.sem_cobertura},
        "sobreposicao": resultado.sobreposicao,
    }
    try:
        LOG_ROTAS.parent.mkdir(parents=True, exist_ok=True)
        with LOG_ROTAS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except OSError:
        pass
