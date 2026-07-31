"""Degradação de query para RECALL nos portais remotos (auditoria 2026-07-30).

Problema medido em `DPU/logs/jurisprudencia_queries.jsonl` (23/05 a 30/07): o CJF
devolve zero em 60,4% das 1.496 chamadas e o BNP em 63,8% das 309. É a mesma
patologia que a base local sofria antes de 2026-07-09 — uma demanda escrita em
linguagem natural exige que TODOS os termos casem no mesmo documento, e o
precedente-âncora costuma casar a maioria, não todos.

A base local resolveu com `relaxar_para_or` (`DPU/Scripts/consultar_indice.py`):
zerou a busca estrita, reexecuta em OR e deixa o BM25 ordenar. Este módulo leva
a mesma ideia aos portais, **no dialeto de cada um** — não há query universal:

- **CJF Unificada** (e o STJ que passa por ela): operadores em MAIÚSCULO, e a
  junção padrão é conjuntiva. Relaxar = trocar a junção de topo por ``OU``.
  Medido no log: 1.023 das 1.496 chamadas usam ``E`` explícito.
- **BNP/PAGEA**: prefixo ``+`` marca termo OBRIGATÓRIO; termo sem prefixo é
  opcional. Relaxar = remover os ``+``, o que converte exigência em preferência
  sem tocar no resto da expressão. Medido: 256 das 309 chamadas usam ``+``.

Ambas as funções devolvem ``None`` quando não há nada a relaxar (operando único,
expressão já disjuntiva) — o chamador então não reexecuta.

Nota de manutenção: este módulo é IRMÃO de `relaxar_para_or` em
`DPU/Scripts/consultar_indice.py`, não uma importação dele — os repositórios são
distintos e o dialeto FTS5 da base local não é o dos portais. Mudança conceitual
em um deve ser espelhada no outro.
"""

from __future__ import annotations

import re
from typing import List, Optional

# Operadores de topo do dialeto CJF que atuam como junção conjuntiva. `NAO` fica
# de fora de propósito: é exclusão, e transformá-la em `OU` inverteria o sentido
# da busca (passaria a PEDIR o que se queria excluir).
_CJF_JUNCAO = {"E", "AND"}
_CJF_DISJUNCAO = {"OU", "OR"}
# Operadores posicionais são parte do operando, não junção de topo.
_CJF_POSICIONAL = re.compile(r"^(ADJ|PROX|COM|MESMO)\d*$", re.IGNORECASE)


def _operandos_de_topo(query: str) -> tuple[List[str], bool]:
    """Fatia a query em operandos de topo, preservando aspas e parênteses.

    Devolve (operandos, tinha_disjuncao). Um operando pode ser um termo, uma
    frase entre aspas, um grupo entre parênteses ou um termo com operador
    posicional colado (``assistencia ADJ5 social`` conta como UM operando).
    """
    operandos: List[str] = []
    atual: List[str] = []
    tinha_disjuncao = False
    in_quote = False
    depth = 0
    aguarda_posicional = False

    for tok in query.split():
        if not in_quote and depth == 0 and not aguarda_posicional:
            if _CJF_POSICIONAL.match(tok):
                # Operador posicional liga o operando anterior ao próximo.
                if operandos:
                    atual = [operandos.pop()]
                atual.append(tok)
                aguarda_posicional = True
                continue
            if tok.upper() in _CJF_JUNCAO:
                if atual:
                    operandos.append(" ".join(atual))
                    atual = []
                continue
            if tok.upper() in _CJF_DISJUNCAO:
                tinha_disjuncao = True
                if atual:
                    operandos.append(" ".join(atual))
                    atual = []
                continue

        atual.append(tok)
        for ch in tok:
            if ch == '"':
                in_quote = not in_quote
            elif not in_quote and ch == "(":
                depth += 1
            elif not in_quote and ch == ")":
                depth = max(0, depth - 1)

        if not in_quote and depth == 0:
            if aguarda_posicional:
                aguarda_posicional = False
            operandos.append(" ".join(atual))
            atual = []

    if atual:
        operandos.append(" ".join(atual))
    return [o for o in operandos if o.strip()], tinha_disjuncao


def relaxar_cjf(query: str) -> Optional[str]:
    """Reescreve query CJF de junção conjuntiva para ``OU`` entre os operandos.

    Devolve ``None`` quando há menos de 2 operandos de topo, quando a expressão
    contém ``NAO`` (a exclusão deixaria de fazer sentido sob disjunção) ou
    quando o resultado seria idêntico à entrada.
    """
    if not query or not query.strip():
        return None
    if re.search(r"(?:^|\s)(NAO|NOT)(?:\s|$)", query, re.IGNORECASE):
        return None
    operandos, _ = _operandos_de_topo(query)
    if len(operandos) < 2:
        return None
    relaxada = " OU ".join(operandos)
    return relaxada if relaxada.strip() != query.strip() else None


def relaxar_bnp(query: str) -> Optional[str]:
    """Remove os prefixos ``+`` (termo obrigatório) da query BNP.

    Termo sem prefixo é opcional no PAGEA, então tirar o ``+`` converte
    exigência em preferência. Preserva ``-termo`` (exclusão) intacto: relaxar
    não é passar a aceitar o que se pediu para excluir.

    Devolve ``None`` se não houver nenhum ``+`` a remover ou se restasse menos
    de 2 operandos (relaxar termo único não muda nada).
    """
    if not query or "+" not in query:
        return None
    relaxada = re.sub(r'(^|\s)\+(?=[^\s])', r"\1", query).strip()
    if relaxada == query.strip():
        return None
    if len(relaxada.split()) < 2:
        return None
    return relaxada


AVISO_RELAXADA = (
    "A busca estrita (todos os termos) não retornou nada; estes resultados vêm "
    "do relaxamento para OU — casam PARTE dos termos. Confira aderência ao caso "
    "antes de citar."
)
