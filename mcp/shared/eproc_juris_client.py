"""Cliente do módulo de jurisprudência do eProc, comum a vários tribunais.

O eProc expõe a mesma tela de pesquisa de jurisprudência em toda instalação:
``externo_controlador.php?acao=jurisprudencia@jurisprudencia/pesquisar`` (GET,
para a sessão) e ``.../listar_resultados`` (POST, que já devolve os resultados
filtrados). O ``tjrj-jurisprudencia`` deste projeto fala com essa mesma tela no
TJRJ desde antes; aqui ela é parametrizada por tribunal.

O QUE ISSO ACRESCENTA
---------------------
**Turma Recursal.** A base unificada do CJF cobre STF, STJ e os seis TRFs, mas
só o tribunal — nenhum acórdão de Juizado. O eProc de TRF2, TRF4 e TRF6 tem o
filtro ``selOrigem[]`` com as opções *Turmas Recursais* e *TRU* (Turma Regional
de Uniformização), o que abre justamente o acervo que faltava. Medido em
2026-08-11 no TRF2, origem *Turmas Recursais*: 10.601 documentos para
``benefício por incapacidade``, todos de 2ª/3ª/6ª Turma Recursal do Rio de
Janeiro.

Para o defensor lotado no Rio isto vale mais que a base regional do TRF1: é a
jurisdição própria (RJ/ES), e a TRU2 uniformiza a matéria dentro da Região.

ALCANCE — TRÊS DAS SEIS REGIÕES
-------------------------------
Verificado em 2026-08-11:

- **TRF2** ``eproc.trf2.jus.br`` — TRF2, TRU2, Turmas Recursais
- **TRF4** ``jurisprudencia.trf4.jus.br/eproc2trf4`` — TRF4, TRU4, Turmas
  Recursais, Varas Federais
- **TRF6** ``eproc1g.trf6.jus.br`` — TRF6, TRU6, Turmas Recursais, Varas
  Federais
- **TRF1** — não usa eProc; a 1ª Região tem base regional própria no CJF, que é
  o servidor ``trf1-jurisprudencia`` (fonte ``JEF1``)
- **TRF5** — não usa eProc, e não precisa: o ``julia-trf5`` já cobre as Turmas
  Recursais das seis seções (instâncias ``TR_AL`` … ``TR_SE``) e a TRU da 5ª
  Região. A instância vai no path da API.
- **TRF3** — sem via por HTTP puro. Não usa eProc, e o portal
  ``web.trf3.jus.br/jurisprudencia`` fica atrás de WAF que faz *tarpit*: o TCP
  conecta, o TLS 1.3 completa, o GET é aceito e voltam zero bytes até o
  timeout. Uma resposta em ~55 tentativas (2026-08-11). O 2º grau do TRF3 sai
  pelo ``cjf-jurisprudencia``; a turma recursal do TRF3 permanece inalcançável
  — a CJF devolve 7 documentos com ``"turma recursal"[ORGA]`` numa base de 62
  mil, o que é ruído, não cobertura.

SINTAXE
-------
A do eProc, em MINÚSCULO (ao contrário do CJF): ``e``, ``ou``, ``não``,
``prox``; ``"frase exata"``; ``*`` como curinga de sufixo. Hífen é aceito
normalmente — o problema do portal regional do TRF1 não existe aqui.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
import urllib3
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TRIBUNAIS: Dict[str, str] = {
    "TRF2": "https://eproc.trf2.jus.br/eproc/",
    "TRF4": "https://jurisprudencia.trf4.jus.br/eproc2trf4/",
    "TRF6": "https://eproc1g.trf6.jus.br/eproc/",
}

# Rótulo canônico → padrão que casa o rótulo real do <option> no formulário.
# Os *values* (1, 2, 3, 4) não são fixados de propósito: são descobertos do
# HTML a cada sessão, porque variam entre instalações (o TRF2 não tem "Varas
# Federais"; o TRF4 não tem o tipo "Súmula").
ORIGENS = {
    "tribunal": r"^TRF\s*\d$",
    "tru": r"^TRU\s*\d$",
    "turmas_recursais": r"turmas?\s+recursais?",
    "varas": r"varas\s+federais",
}

CAMPOS = {"EM": "E", "E": "E", "IT": "I", "I": "I"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}

# O eProc devolve 10 por página por padrão; selTamanhoPagina aceita mais.
_TAMANHO_PAGINA = 50
_MAX_PAGINAS = 5
# Sessão (PHPSESSID) reaproveitada por 25 min; recriada após 1 h sem uso.
_SESSAO_TTL_S = 25 * 60


class EProcJurisSession:
    """Sessão com o módulo de jurisprudência do eProc de UM tribunal."""

    def __init__(self, tribunal: str) -> None:
        if tribunal not in TRIBUNAIS:
            raise ValueError(
                f"Tribunal sem eProc de jurisprudência: {tribunal!r}. "
                f"Disponíveis: {', '.join(TRIBUNAIS)}."
            )
        self.tribunal = tribunal
        self.base = TRIBUNAIS[tribunal]
        self.url_pesquisar = (
            self.base + "externo_controlador.php?acao=jurisprudencia@jurisprudencia/pesquisar"
        )
        self.url_listar = (
            self.base + "externo_controlador.php?acao=jurisprudencia@jurisprudencia/listar_resultados"
        )
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        # Certificados de tribunal costumam falhar na cadeia padrão; o
        # tjrj-jurisprudencia já convive com isso da mesma forma.
        self.session.verify = False
        self._fetched_at: float = 0.0
        self.origens: Dict[str, str] = {}
        self.tipos: Dict[str, str] = {}

    @property
    def sessao_fresca(self) -> bool:
        return bool(self._fetched_at) and (time.monotonic() - self._fetched_at) < _SESSAO_TTL_S

    @retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
    def abrir(self) -> None:
        """GET na tela de pesquisa: pega o cookie e descobre as opções."""
        resp = self.session.get(self.url_pesquisar, timeout=25)
        resp.raise_for_status()
        html = resp.text

        if "txtPesquisa" not in html:
            raise RuntimeError(
                f"A tela de jurisprudência do eProc {self.tribunal} não veio como "
                "esperado (sem campo txtPesquisa) — pode ter caído em login ou "
                "mudado de endereço."
            )

        self.origens = self._descobrir(html, "selOrigem[]", ORIGENS)
        self.tipos = self._descobrir_tipos(html)
        if "turmas_recursais" not in self.origens:
            raise RuntimeError(
                f"O eProc {self.tribunal} não ofereceu a origem 'Turmas Recursais' "
                "— a estrutura do formulário mudou. Verificar antes de confiar "
                "no resultado."
            )
        self._fetched_at = time.monotonic()

    @staticmethod
    def _opcoes(html: str, nome_select: str) -> List[Tuple[str, str]]:
        m = re.search(
            r'<select[^>]*name="' + re.escape(nome_select) + r'"[^>]*>(.*?)</select>',
            html,
            re.DOTALL,
        )
        if not m:
            return []
        pares = re.findall(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', m.group(1), re.DOTALL)
        limpos = []
        for valor, rotulo in pares:
            rotulo = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", rotulo)).strip()
            if valor and rotulo:
                limpos.append((valor, rotulo))
        return limpos

    def _descobrir(self, html: str, nome_select: str, padroes: Dict[str, str]) -> Dict[str, str]:
        achados: Dict[str, str] = {}
        for valor, rotulo in self._opcoes(html, nome_select):
            for chave, padrao in padroes.items():
                if chave not in achados and re.search(padrao, rotulo, re.IGNORECASE):
                    achados[chave] = valor
        return achados

    def _descobrir_tipos(self, html: str) -> Dict[str, str]:
        """Mapeia o rótulo do tipo de documento, normalizado, para o value."""
        tipos: Dict[str, str] = {}
        for valor, rotulo in self._opcoes(html, "selTipoDocumento[]"):
            tipos[_normalizar(rotulo)] = valor
        return tipos

    @retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
    def buscar(
        self,
        termo: str,
        origem: str,
        campo: str = "EM",
        tipo_documento: Optional[str] = None,
        data_inicio: Optional[str] = None,
        data_fim: Optional[str] = None,
        pagina: int = 1,
        tamanho_pagina: int = _TAMANHO_PAGINA,
    ) -> str:
        if not self.sessao_fresca:
            self.abrir()

        rdo = CAMPOS.get(campo.upper(), "E")
        dados: Dict[str, Any] = {
            "txtPesquisa": termo,
            "hdnExibirPesquisaAvancada": "1",
            "rdoCampo": rdo,
            "selOrigem[]": self.origens[origem],
            "selTamanhoPagina": str(tamanho_pagina),
        }
        # `chkCaput` restringe a busca ao caput/ementa e ANULA o inteiro teor:
        # medido no TRF2 em 2026-08-11 com o termo "fibromialgia" — 123
        # resultados com o checkbox (idêntico em rdoCampo=E e I) contra 2.576
        # sem ele em rdoCampo=I. Enviá-lo sempre faria de `campo="IT"` uma
        # opção inerte, que promete abrangência e devolve a busca por ementa.
        if rdo == "E":
            dados["chkCaput"] = "on"
        if pagina > 1:
            dados["hdnPaginaAtual"] = str(pagina)
        if tipo_documento:
            chave = _normalizar(tipo_documento)
            if chave not in self.tipos:
                raise ValueError(
                    f"Tipo de documento {tipo_documento!r} não existe no eProc "
                    f"{self.tribunal}. Disponíveis: {', '.join(sorted(self.tipos))}."
                )
            dados["selTipoDocumento[]"] = self.tipos[chave]
        if data_inicio:
            dados["dtDecisaoInicio"] = data_inicio
            dados["hdnDecisaoInicio"] = data_inicio
        if data_fim:
            dados["dtDecisaoFim"] = data_fim
            dados["hdnDecisaoFim"] = data_fim

        resp = self.session.post(
            self.url_listar,
            data=dados,
            headers={**HEADERS, "Referer": self.url_pesquisar},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.text


def _normalizar(texto: str) -> str:
    """Minúsculo, sem acento e sem pontuação — para casar rótulo com parâmetro."""
    t = texto.strip().lower()
    for de, para in (("á", "a"), ("â", "a"), ("ã", "a"), ("é", "e"), ("ê", "e"),
                     ("í", "i"), ("ó", "o"), ("ô", "o"), ("õ", "o"), ("ú", "u"),
                     ("ç", "c")):
        t = t.replace(de, para)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def extrair_total(html: str) -> int:
    m = re.search(r"(\d[\d.]*)\s*documentos?\s*encontrados?", html, re.IGNORECASE)
    return int(m.group(1).replace(".", "")) if m else 0


def extrair_documentos(html: str) -> List[Dict[str, str]]:
    """Extrai os cartões de resultado.

    Cada resultado é ``<div id="resultado{n}" class="... resultadoItem">`` com
    pares ``<div class="resLabel">RÓTULO</div><div class="resValue">…</div>``.
    Os rótulos observados: PROCESSO, UF, ÓRGÃO JULGADOR, DATA DO JULGAMENTO,
    DATA DA PUBLICAÇÃO, RELATOR, DECISÃO, EMENTA.

    Lê pelo RÓTULO, e não por posição: é assim que o mesmo parser serve TRF2,
    TRF4 e TRF6, que não trazem exatamente o mesmo conjunto de campos. (O
    ``tjrj-jurisprudencia`` deduz o órgão julgador de ``data-citacao`` porque no
    TJRJ ele não vem como rótulo; nos TRFs vem, e o caminho direto é melhor.)
    """
    soup = BeautifulSoup(html, "html.parser")
    documentos: List[Dict[str, str]] = []

    for div in soup.find_all("div", id=re.compile(r"^resultado\d")):
        doc: Dict[str, str] = {}

        tipo = div.find("div", class_="resValueTipoJurisprudencia")
        if tipo:
            doc["tipo"] = tipo.get_text().strip()

        num = div.find("a", class_="numero-processo")
        if num:
            doc["numero"] = num.get_text().strip()

        for rotulo in div.find_all("div", class_="resLabel"):
            valor = rotulo.find_next_sibling("div", class_="resValue")
            if not valor:
                continue
            chave = _normalizar(rotulo.get_text())
            doc[chave] = re.sub(r"\s+", " ", valor.get_text()).strip()

        # O eProc flexiona o rótulo: "RELATOR" ou "RELATORA" conforme quem
        # julgou. Ler só "relator" fazia o nome sumir da citação sempre que a
        # relatora era mulher — metade dos julgados do TRF4 nesta amostra.
        # Canoniza em `relator` e guarda o tratamento para a peça grafá-lo certo.
        if "relatora" in doc:
            doc.setdefault("relator", doc["relatora"])
            doc["relator_tratamento"] = "Relatora"
        elif "relator" in doc:
            doc["relator_tratamento"] = "Relator"

        if doc.get("numero") or doc.get("ementa"):
            documentos.append(doc)

    return documentos


# ---------------------------------------------------------------------------
# Sessões compartilhadas, uma por tribunal
# ---------------------------------------------------------------------------

_SESSOES: Dict[str, EProcJurisSession] = {}


def get_sessao(tribunal: str) -> EProcJurisSession:
    sess = _SESSOES.get(tribunal)
    if sess is not None and sess._fetched_at and (time.monotonic() - sess._fetched_at) > 3600:
        try:
            sess.session.close()
        except Exception:
            pass
        sess = None
    if sess is None:
        sess = EProcJurisSession(tribunal)
        _SESSOES[tribunal] = sess
    return sess


# ---------------------------------------------------------------------------
# API de alto nível
# ---------------------------------------------------------------------------

def normalizar_tribunal(tribunal: str) -> str:
    t = str(tribunal).strip().upper().replace("-", "").replace(" ", "")
    if t in TRIBUNAIS:
        return t
    raise ValueError(
        f"Tribunal inválido: {tribunal!r}. Este servidor cobre {', '.join(TRIBUNAIS)}. "
        "TRF1 tem base própria (servidor trf1-jurisprudencia); TRF5, o julia-trf5; "
        "TRF3 não tem jurisprudência acessível por HTTP."
    )


def normalizar_origem(origem: str) -> str:
    o = _normalizar(origem).replace(" ", "_")
    apelidos = {
        "tr": "turmas_recursais",
        "turma_recursal": "turmas_recursais",
        "turmas_recursais": "turmas_recursais",
        "recursal": "turmas_recursais",
        "tribunal": "tribunal",
        "trf": "tribunal",
        "tru": "tru",
        "uniformizacao": "tru",
        "varas": "varas",
        "varas_federais": "varas",
    }
    if o not in apelidos:
        raise ValueError(
            f"Origem inválida: {origem!r}. Use turmas_recursais, tru, tribunal ou varas."
        )
    return apelidos[o]


def buscar_documentos(
    tribunal: str,
    busca: str,
    origem: str = "turmas_recursais",
    campo: str = "EM",
    tipo_documento: Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    max_resultados: int = 10,
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """Busca no eProc do tribunal e devolve ``(documentos, meta)``.

    Salvaguardas:
      - origem inexistente no tribunal vira ``ValueError`` com a lista real;
      - canário estrutural: o eProc informa N documentos e o parser extrai 0 →
        ``RuntimeError``, porque devolver lista vazia aí seria indistinguível
        de "não há precedente";
      - paginação por ``hdnPaginaAtual`` (verificada: respeita o filtro de
        origem, ao contrário do endpoint ``ajax_paginar_resultado``, que o
        ``tjrj-jurisprudencia`` evita justamente por ignorá-lo).
    """
    trib = normalizar_tribunal(tribunal)
    orig = normalizar_origem(origem)

    sess = get_sessao(trib)
    if not sess.sessao_fresca:
        sess.abrir()
    if orig not in sess.origens:
        raise ValueError(
            f"O eProc {trib} não oferece a origem {orig!r}. "
            f"Disponíveis: {', '.join(sorted(sess.origens))}."
        )

    html = sess.buscar(busca, orig, campo, tipo_documento, data_inicio, data_fim)
    total = extrair_total(html)
    docs = extrair_documentos(html)

    if total > 0 and not docs:
        raise RuntimeError(
            f"O eProc {trib} informou {total} documento(s) mas o parser extraiu 0 "
            "— provável mudança no HTML. Verificar shared/eproc_juris_client."
            "extrair_documentos antes de concluir que não há precedente."
        )

    pagina = 1
    vistos = {d.get("numero") for d in docs}
    while len(docs) < max_resultados and len(docs) < total and pagina < _MAX_PAGINAS:
        pagina += 1
        try:
            html_extra = sess.buscar(
                busca, orig, campo, tipo_documento, data_inicio, data_fim, pagina=pagina
            )
        except Exception:
            break
        novos = [d for d in extrair_documentos(html_extra) if d.get("numero") not in vistos]
        if not novos:
            break
        vistos.update(d.get("numero") for d in novos)
        docs.extend(novos)

    meta: Dict[str, Any] = {
        "tribunal": trib,
        "origem": orig,
        "total": total,
        "origens_disponiveis": sorted(sess.origens),
        "tipos_disponiveis": sorted(sess.tipos),
    }
    return docs[:max_resultados], meta
