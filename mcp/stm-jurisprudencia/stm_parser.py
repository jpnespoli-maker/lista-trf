"""Regras puras da Consulta Judicial Unificada da JMU (jurisprudência do STM).

Contrato medido em 20/10/2026 em ``jurisprudencia.stm.jus.br``:

- A busca é ``consulta.php`` por GET, com a página de resultado pronta em HTML.
  Cada acórdão vem num painel com campos marcados por ``data-id`` (``id``,
  ``numero_processo``, ``classe``, ``ministro_relator``, datas e a ``ementa``
  integral num ``blockquote``), e o total aparece como "1 - 25 de N documentos".
- ``search_filter=tudo`` aceita os operadores do portal (``e``, ``ou``, ``não``,
  aspas, ``"…"~n``, ``*``, ``?``); os filtros ``fq_*`` combinam com ela, inclusive
  ``fq_data_decisao=[início TO fim]`` (a data que o painel chama de julgamento).
- ``order=1`` é o julgamento mais recente (default do portal); ``order=2``, o mais
  antigo. ``rows`` aceita qualquer valor.
- **Zero resultados, query malformada e ``order`` inválido devolvem a MESMA
  página** — a tela inicial, sem contagem nem painel, byte a byte igual. O portal
  não distingue "não há precedente" de "consulta recusada". Por isso
  :func:`parsear_resultados` devolve ``total=None`` nesse caso, e quem chama
  decide com uma consulta de controle; nunca se lê essa página como zero.
- O inteiro teor e os metadados saem de ``modal_jurisprudencia.php`` com
  ``doc_type`` e o ``uuid`` do painel, sem login, em texto com ``<br/>``. O botão
  "Inteiro Teor" da página aponta para o eproc2g, que exige login (SSO).
  ``uuid`` ou ``doc_type`` desconhecidos devolvem corpo vazio.
"""

from __future__ import annotations

import html as html_module
import re
from dataclasses import asdict, dataclass
from datetime import datetime

from bs4 import BeautifulSoup

BASE_URL = "https://jurisprudencia.stm.jus.br/"
CONSULTA_URL = BASE_URL + "consulta.php"
MODAL_URL = BASE_URL + "modal_jurisprudencia.php"

ORDENS = {"recente": "1", "antigo": "2"}

# doc_type do modal → rótulo
TIPOS_MODAL = {
    "inteiro_teor": "Inteiro teor",
    "referencia_legislativa": "Referência legislativa",
    "notas": "Notas",
    "thesaurus": "Indexação",
}

_RE_TOTAL = re.compile(r"(\d[\d.]*)\s*-\s*(\d[\d.]*)\s+de\s+(\d[\d.]*)\s+documentos")
_RE_UUID = re.compile(r"^[0-9a-f]{32}$")
_RE_BR = re.compile(r"<br\s*/?>", re.I)


@dataclass
class AcordaoSTM:
    id: str = ""
    numero: str = ""
    classe: str = ""
    relator: str = ""
    relator_acordao: str = ""
    revisor: str = ""
    assuntos: str = ""
    data_autuacao: str = ""
    data_julgamento: str = ""
    data_publicacao: str = ""
    ementa: str = ""

    def como_dict(self) -> dict:
        return asdict(self)


def _espacos(texto: str) -> str:
    return " ".join((texto or "").split())


def validar_busca(busca: str) -> str:
    """Devolve a busca aparada, ou levanta ``ValueError``.

    O portal responde à query malformada com a tela inicial, igual à do zero:
    o que se pega aqui é o que, chegando lá, viraria um falso "não há
    precedente".
    """
    busca = (busca or "").strip()
    if not busca:
        raise ValueError("busca vazia")
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


_RE_NAO_SEM_ACENTO = re.compile(r"(?<![\w-])nao(?![\w-])", re.I)


def normalizar_operadores(busca: str) -> tuple[str, bool]:
    """Troca o operador ``nao`` sem acento, fora de aspas, por ``não``.

    O portal só reconhece a negação acentuada (``não``/``NÃO``); ``nao`` vira
    TERMO da busca, e a consulta encolhe calada — medido em 20/10/2026:
    "deserção não furto" 4.664 documentos, "deserção nao furto" 7. Os demais
    operadores não dependem de caixa (``e``/``E``, ``ou``/``OU``).
    """
    partes = busca.split('"')
    trocou = False
    for i in range(0, len(partes), 2):  # índices pares estão fora de aspas
        novo = _RE_NAO_SEM_ACENTO.sub("não", partes[i])
        trocou |= novo != partes[i]
        partes[i] = novo
    return '"'.join(partes), trocou


def data_solr(valor: str, *, fim: bool) -> str:
    """``dd/mm/aaaa`` ou ``aaaa-mm-dd`` → instante Solr; vazio → ``*``."""
    valor = (valor or "").strip()
    if not valor:
        return "*"
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(valor, fmt).date()
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"data inválida: {valor!r} (use dd/mm/aaaa ou aaaa-mm-dd)")
    hora = "23:59:59" if fim else "00:00:00"
    return f"{d.isoformat()}T{hora}Z"


def normalizar_ordem(ordem: str) -> str:
    chave = (ordem or "recente").strip().lower()
    if chave not in ORDENS:
        raise ValueError(f"ordem inválida: {ordem!r} (use 'recente' ou 'antigo')")
    return ORDENS[chave]


def montar_params(
    busca: str,
    *,
    relator: str = "",
    classe: str = "",
    data_inicio: str = "",
    data_fim: str = "",
    ordem: str = "recente",
    inicio: int = 0,
    quantidade: int = 10,
) -> dict:
    """Parâmetros de ``consulta.php``; levanta ``ValueError`` no que o portal
    recusaria em silêncio."""
    params = {
        "search_filter_option": "jurisprudencia",
        "search_filter": "tudo",
        "q": normalizar_operadores(validar_busca(busca))[0],
        "order": normalizar_ordem(ordem),
        "start": str(max(0, int(inicio))),
        "rows": str(max(1, int(quantidade))),
    }
    if relator.strip():
        params["fq_ministro_relator"] = _espacos(relator).upper()
    if classe.strip():
        params["fq_classe"] = _espacos(classe).upper()
    ini, fim = data_solr(data_inicio, fim=False), data_solr(data_fim, fim=True)
    if ini != "*" or fim != "*":
        params["fq_data_decisao"] = f"[{ini} TO {fim}]"
    return params


def _campo(painel, data_id: str) -> str:
    el = painel.select_one(f'[data-id="{data_id}"]')
    return _espacos(el.get_text(" ", strip=True)) if el else ""


def _dd_por_rotulo(painel, rotulo: str) -> str:
    for dt in painel.find_all("dt"):
        if _espacos(dt.get_text()).rstrip(":").lower() == rotulo.lower():
            dd = dt.find_next_sibling("dd")
            return _espacos(dd.get_text(" ", strip=True)) if dd else ""
    return ""


def _ementa(painel) -> str:
    texto = _campo(painel, "ementa")
    return re.sub(r"^EMENTA\s*:\s*", "", texto)


def parsear_resultados(html: str) -> tuple[int | None, list[AcordaoSTM]]:
    """``(total, acórdãos)``. ``total`` é ``None`` quando a página não traz
    contagem — zero OU consulta recusada, que o portal não distingue."""
    soup = BeautifulSoup(html or "", "html.parser")
    docs: list[AcordaoSTM] = []
    for marca in soup.select('span[data-id="numero_processo"]'):
        painel = marca.find_parent(class_="panel")
        if painel is None:
            continue
        docs.append(AcordaoSTM(
            id=_campo(painel, "id"),
            numero=_campo(painel, "numero_processo"),
            classe=_campo(painel, "classe"),
            relator=_campo(painel, "ministro_relator"),
            relator_acordao=_campo(painel, "ministro_relator_acordao"),
            revisor=_dd_por_rotulo(painel, "Revisor(a)"),
            assuntos=_dd_por_rotulo(painel, "Assuntos"),
            data_autuacao=_campo(painel, "data_autuacao"),
            data_julgamento=_campo(painel, "data_julgamento"),
            data_publicacao=_campo(painel, "data_publicacao"),
            ementa=_ementa(painel),
        ))
    m = _RE_TOTAL.search(_espacos(soup.get_text(" ", strip=True)))
    total = int(m.group(3).replace(".", "")) if m else None
    return total, docs


def validar_uuid(uuid: str) -> str:
    uuid = (uuid or "").strip().lower()
    if not _RE_UUID.match(uuid):
        raise ValueError(
            f"id inválido: {uuid!r} — use o <id> devolvido pela busca (32 hexadecimais)"
        )
    return uuid


def texto_modal(html: str) -> str:
    """Corpo do modal em texto, com as quebras de linha do original.

    O inteiro teor vem do PDF do acórdão, quebrado na largura da página: as
    linhas se preservam como estão (aspa longa se confere por trecho curto).
    """
    texto = _RE_BR.sub("\n", html or "")
    texto = BeautifulSoup(texto, "html.parser").get_text()
    texto = html_module.unescape(texto)
    linhas = [ln.rstrip() for ln in texto.replace("\r", "").split("\n")]
    texto = "\n".join(linhas).strip()
    return re.sub(r"\n{3,}", "\n\n", texto)
