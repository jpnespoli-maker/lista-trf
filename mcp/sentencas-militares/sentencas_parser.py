"""Regras puras do Banco de Sentenças das Justiças Militares (PDPJ/CNJ).

Contrato medido em 20/10/2026 contra a API que a página pública
``bancosentencas.pdpj.jus.br`` usa (``POST sentencas/buscar`` em
``gateway.cloud.pje.jus.br/banco-sentencas/api``), sem login:

- O corpo é ``{pageOffset, pageSize, searchAfterValue, order, searchFilters:
  [{texto, data_inicio, data_fim, ids_orgaos}]}``; a resposta, ``{sentencas,
  total, status}``, e cada sentença já traz o TEXTO INTEGRAL (13 a 61 mil
  caracteres nos casos medidos), em texto puro.
- **``pageSize`` só aceita 10 ou 20.** Qualquer outro valor (1, 2, 5, 25, 50,
  100) volta **HTTP 403** de corpo vazio — tem cara de bloqueio de acesso e não
  é. Foi isso que, na sondagem, se tomou primeiro por filtro de TLS e de rede.
- **A busca é OU por padrão** (Elasticsearch): "estado de necessidade" sem
  aspas casou o acervo inteiro (20.895, porque "de" está em tudo); com aspas,
  942. "deserção ameaça" deu 3.787; "deserção AND ameaça", 108. Os operadores
  só valem em MAIÚSCULO e em inglês — "deserção e ameaça" deu 20.347 e
  "deserção and ameaça", 3.854: em minúsculo viram termo. Por isso
  :func:`normalizar_busca` converte ``e``/``ou``/``não`` (e ``and``/``or``/
  ``not``) e liga por ``AND`` os termos sem operador.
- Paginação por cursor (``search_after``): a página seguinte pede
  ``searchAfterValue`` = ``@timestamp`` do último item, com ``pageOffset=1`` e a
  mesma ordem. ``order``: ``desc`` (juntada mais recente) ou ``asc``.
- Datas em ``AAAA-MM-DD``, sobre a data de juntada.
- Zero é zero: ``total: 0`` com status 200 (ao contrário do portal do STM).
- Não há rota para uma sentença pelo id (``GET sentencas/{id}`` dá 404); o
  número do processo entre aspas a acha (total 1 no caso medido).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

API_URL = "https://gateway.cloud.pje.jus.br/banco-sentencas/api/"
BUSCAR_URL = API_URL + "sentencas/buscar"
PAGINA_PUBLICA = "https://bancosentencas.pdpj.jus.br/"

POR_PAGINA_ACEITOS = (10, 20)
ORDENS = {"recente": "desc", "antigo": "asc"}
JMU = 13309  # código da Justiça Militar da União na tabela de tribunais

_ORGAOS_JSON = Path(__file__).with_name("orgaos.json")

_OPERADORES = {
    "and": "AND", "e": "AND", "&&": "AND",
    "or": "OR", "ou": "OR", "||": "OR",
    "not": "NOT", "não": "NOT", "nao": "NOT",
}
_RE_TOKEN = re.compile(r'"[^"]*"(?:~\d+)?|\(|\)|[^\s()"]+')
_RE_NPU = re.compile(r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$")


@dataclass
class SentencaMilitar:
    id_sentenca: str = ""
    num_processo: str = ""
    orgao: str = ""
    tribunal: str = ""
    classe: str = ""
    assunto_principal: str = ""
    assuntos: list = field(default_factory=list)
    data_juntada: str = ""
    timestamp: int = 0
    texto: str = ""

    def como_dict(self) -> dict:
        return asdict(self)


# ---------------- órgãos ----------------

def _sem_acento(s: str) -> str:
    s = (s or "").replace("ª", "a").replace("º", "o")
    s = unicodedata.normalize("NFKD", s)
    return " ".join("".join(c for c in s if not unicodedata.combining(c)).lower().split())


def carregar_orgaos() -> dict:
    return json.loads(_ORGAOS_JSON.read_text(encoding="utf-8"))


def resolver_orgaos(justica: str = "JMU", orgao: str = "") -> list[int] | None:
    """ids para ``ids_orgaos``; ``None`` = sem filtro (todas as justiças, todos os
    órgãos). Levanta ``ValueError`` se ``orgao`` não casar nenhum órgão."""
    tabela = carregar_orgaos()["orgaos"]
    escopo = (justica or "JMU").strip().lower()
    if escopo == "jmu":
        candidatos = [o for o in tabela if o["tribunal"] == JMU]
    elif escopo in ("estadual", "estaduais", "jme"):
        candidatos = [o for o in tabela if o["tribunal"] != JMU]
    elif escopo in ("todas", "todos", "tudo"):
        candidatos = tabela
    else:
        raise ValueError(f"justica inválida: {justica!r} (use 'JMU', 'estadual' ou 'todas')")
    if orgao.strip():
        # Fronteira antes do trecho: "1ª CJM" não pode casar "11ª CJM".
        alvo = re.compile(r"(?<![0-9a-z])" + re.escape(_sem_acento(orgao)))
        escolhidos = [o for o in candidatos if alvo.search(_sem_acento(o["descricao"]))]
        if not escolhidos:
            exemplos = "; ".join(o["descricao"] for o in candidatos[:4])
            raise ValueError(
                f"nenhum órgão casa {orgao!r} em justica={justica!r}. Exemplos: {exemplos}. "
                f"Ver ajuda_sintaxe_sentencas_militares() para a lista."
            )
        return [o["id"] for o in escolhidos]
    if escopo in ("todas", "todos", "tudo"):
        return None
    return [o["id"] for o in candidatos]


# ---------------- busca ----------------

def _validar(busca: str) -> str:
    busca = (busca or "").strip()
    if len(busca) < 3:
        raise ValueError("busca com menos de 3 caracteres (o portal exige ao menos 3)")
    if busca.count('"') % 2:
        raise ValueError('aspas desbalanceadas (número ímpar de ")')
    nivel = 0
    for c in busca:
        nivel += (c == "(") - (c == ")")
        if nivel < 0:
            break
    if nivel:
        raise ValueError("parênteses desbalanceados")
    return busca


def normalizar_busca(busca: str) -> tuple[str, list[str]]:
    """Query que o portal entende + avisos do que se mudou.

    Converte operador em português ou minúsculo para ``AND``/``OR``/``NOT`` e
    liga por ``AND`` os termos adjacentes sem operador (o padrão do portal é
    OU, que traz o acervo inteiro).
    """
    tokens = _RE_TOKEN.findall(_validar(busca))
    saida: list[str] = []
    convertidos: list[str] = []
    implicito = False

    def operando(t: str) -> bool:
        return t not in ("AND", "OR", "NOT", "(", ")")

    for tok in tokens:
        op = _OPERADORES.get(tok.lower()) if not tok.startswith('"') else None
        if op:
            if tok != op:
                convertidos.append(tok)
            tok = op
        if saida:
            ant = saida[-1]
            if (operando(ant) or ant == ")") and (operando(tok) or tok in ("(", "NOT")):
                saida.append("AND")
                implicito = True
        saida.append(tok)
    avisos = []
    if convertidos:
        avisos.append(
            "OPERADOR CONVERTIDO: o portal só reconhece AND/OR/NOT em maiúsculo; "
            f"{', '.join(repr(c) for c in convertidos)} viraria(m) termo da busca."
        )
    if implicito:
        avisos.append(
            "TERMOS LIGADOS POR AND: o padrão do portal é OU, que traz quase o acervo "
            "inteiro; para alternativa, escreva OR."
        )
    return " ".join(saida), avisos


def termos_positivos(query: str) -> list[str]:
    """Termos para localizar o trecho (sem operadores, negados e curingas)."""
    termos, negar = [], False
    for tok in _RE_TOKEN.findall(query):
        if tok in ("AND", "OR", "(", ")"):
            continue
        if tok == "NOT":
            negar = True
            continue
        if negar or tok.startswith("-"):
            negar = False
            continue
        t = re.sub(r"~\d+$", "", tok).strip('"').lstrip("+").rstrip("*")
        if len(t) >= 3:
            termos.append(t)
    return termos


def data_iso(valor: str) -> str | None:
    valor = (valor or "").strip()
    if not valor:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(valor, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"data inválida: {valor!r} (use dd/mm/aaaa ou aaaa-mm-dd)")


def montar_corpo(query: str, *, ids_orgaos: list[int] | None, data_inicio: str = "",
                 data_fim: str = "", ordem: str = "recente", por_pagina: int = 10,
                 cursor: str = "") -> dict:
    if int(por_pagina) not in POR_PAGINA_ACEITOS:
        raise ValueError(
            f"por_pagina={por_pagina}: o portal só aceita 10 ou 20 (outro valor volta HTTP 403)"
        )
    chave = (ordem or "recente").strip().lower()
    if chave not in ORDENS:
        raise ValueError(f"ordem inválida: {ordem!r} (use 'recente' ou 'antigo')")
    cursor = (cursor or "").strip()
    if cursor and not cursor.isdigit():
        raise ValueError(f"cursor inválido: {cursor!r} — use o valor que a busca anterior devolveu")
    filtro: dict = {"texto": query}
    ini, fim = data_iso(data_inicio), data_iso(data_fim)
    if ini:
        filtro["data_inicio"] = ini
    if fim:
        filtro["data_fim"] = fim
    if ids_orgaos:
        filtro["ids_orgaos"] = list(ids_orgaos)
    return {
        "pageOffset": 1,
        "pageSize": int(por_pagina),
        "searchAfterValue": cursor or None,
        "order": ORDENS[chave],
        "searchFilters": [filtro],
    }


def corpo_por_processo(num_processo: str) -> dict:
    npu = (num_processo or "").strip()
    if not _RE_NPU.match(npu):
        raise ValueError(f"número de processo inválido: {npu!r} (formato CNJ: NNNNNNN-DD.AAAA.J.TR.OOOO)")
    return {"pageOffset": 1, "pageSize": 20, "searchAfterValue": None, "order": "desc",
            "searchFilters": [{"texto": f'"{npu}"'}]}


# ---------------- resposta ----------------

def parsear_resposta(dados) -> tuple[int, list[SentencaMilitar]]:
    """``(total, sentenças)``; ``ValueError`` se a forma não for a medida."""
    if not isinstance(dados, dict) or "total" not in dados or "sentencas" not in dados:
        chaves = list(dados)[:8] if isinstance(dados, dict) else type(dados).__name__
        raise ValueError(f"resposta fora do contrato medido (chaves: {chaves})")
    docs = []
    for s in dados["sentencas"] or []:
        docs.append(SentencaMilitar(
            id_sentenca=s.get("id_sentenca") or "",
            num_processo=s.get("num_processo") or "",
            orgao=" ".join((s.get("des_orgao") or "").split()),
            tribunal=s.get("des_tribunal") or "",
            classe=(s.get("des_classe") or "").strip(),
            assunto_principal=(s.get("des_assunto_principal") or "").strip(),
            assuntos=[a.strip() for a in s.get("des_assuntos") or [] if a],
            data_juntada=s.get("data_juntada") or "",
            timestamp=int(s.get("@timestamp") or 0),
            texto=s.get("texto") or "",
        ))
    return int(dados["total"]), docs


def trecho(texto: str, termos: list[str], tamanho: int = 800) -> str:
    """Janela do texto em torno da primeira ocorrência de um termo (sem acento e
    sem caixa); sem ocorrência, o começo do texto. O texto não se altera."""
    if not texto:
        return ""
    plano = _sem_acento_mesmo_tamanho(texto)
    pos = -1
    for t in termos:
        i = plano.find(_sem_acento_mesmo_tamanho(t))
        if i >= 0 and (pos < 0 or i < pos):
            pos = i
    if pos < 0:
        corte = texto[:tamanho]
        return corte + (" [...]" if len(texto) > tamanho else "")
    ini = max(0, pos - tamanho // 3)
    fim = min(len(texto), ini + tamanho)
    return ("[...] " if ini else "") + texto[ini:fim] + (" [...]" if fim < len(texto) else "")


def _sem_acento_mesmo_tamanho(s: str) -> str:
    """Minúsculo e sem acento, caractere por caractere (posições preservadas)."""
    out = []
    for c in s.lower():
        base = unicodedata.normalize("NFKD", c)
        base = "".join(x for x in base if not unicodedata.combining(x))
        out.append(base[:1] if base else c)
    return "".join(out)
