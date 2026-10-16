"""Regras puras do SJUR — jurisprudência da Justiça Eleitoral (TSE e TREs).

Sem rede e sem navegador: recebe o JSON da API ou os bytes do inteiro teor e
devolve estruturas. Os canários moram aqui porque o SJUR responde HTTP 200 nos
modos de falha medidos em 16/10/2026 — captcha recusado ("Falha na verificação
antirrobô.", com lista vazia) e limite de taxa do inteiro teor ("Excesso de
requisições!") —, e um cliente que confiasse no status leria "não há
precedente".

Desenho: DPU/docs/specs/2026-10-16-mcp-jurisprudencia-eleitoral-design.md.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.base_juridica import limpar_texto_html  # noqa: E402

UFS_TRE = (
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
)
# Os três valores de `descricaoTipoDecisao` medidos nas agregações do portal.
TIPOS_DECISAO = {
    "acordao": "Acórdão",
    "resolucao": "Resolução",
    "decisao_sem_resolucao": "Decisão sem resolução",
}
ORDENACOES = ("re", "dj_desc", "dj_asc", "dp_desc", "dp_asc")
TAMANHO_MAX = 100

_CAMPO_TIPO = "descricaoTipoDecisao.keyword"
_CAMPO_SIGLA = "siglaTribunalJE.keyword"
_CAMPO_ANO = "anoEleicao"
_ERRO_PDF = b"Erro ao tentar recuperar PDF"
# A SPA só traduz o operador MAIÚSCULO (tratarTextoQueryString, main.js de
# 16/10/2026); em minúsculo ele vira termo da busca.
_OPERADOR_MINUSCULO = re.compile(r"\s(e|ou|nao|não)\s")


class ErroSJUR(RuntimeError):
    """Falha do portal que NÃO pode virar lista vazia."""


class ParametroInvalido(ValueError):
    """Parâmetro da tool fora do domínio. Classe própria para não se confundir
    com o ValueError de um JSON quebrado, que é falha do portal, não da consulta."""


MSG_ANTIRROBO = (
    "o portal recusou o token do hCaptcha (verificação antirrobô). Repita a busca; "
    "se a janela do Edge mostrar um desafio, resolva-o ali enquanto a busca aguarda. "
    "Isto NÃO significa ausência de precedente."
)


@dataclass(frozen=True)
class Alvo:
    portal: str
    siglas: tuple[str, ...] = ()

    @property
    def rotulo(self) -> str:
        if self.portal == "tse":
            return "TSE"
        return ", ".join(self.siglas) if self.siglas else "TREs (todos)"


def normalizar_tribunal(valor: str) -> Alvo:
    bruto = (valor or "").strip()
    if not bruto or bruto.upper() == "TSE":
        return Alvo("tse")
    if bruto.upper() in ("TRES", "TRE", "TODOS"):
        return Alvo("tres")
    siglas: list[str] = []
    for parte in re.split(r"[,;]", bruto):
        uf = re.sub(r"^TRE[\s/\-]*", "", parte.strip().upper())
        if uf not in UFS_TRE:
            raise ParametroInvalido(
                f"tribunal inválido: {parte.strip()!r}. Use TSE, TREs ou TRE-UF "
                f"(UF entre {', '.join(UFS_TRE)})."
            )
        sigla = f"TRE-{uf}"
        if sigla not in siglas:
            siglas.append(sigla)
    return Alvo("tres", tuple(siglas))


def normalizar_tipos(valor: str) -> list[str]:
    bruto = (valor or "todos").strip().lower()
    if bruto == "todos":
        return list(TIPOS_DECISAO.values())
    rotulos: list[str] = []
    for parte in bruto.split(","):
        chave = parte.strip()
        if chave not in TIPOS_DECISAO:
            raise ParametroInvalido(
                f"tipo_decisao inválido: {chave!r}. Use todos ou {', '.join(TIPOS_DECISAO)}."
            )
        if TIPOS_DECISAO[chave] not in rotulos:
            rotulos.append(TIPOS_DECISAO[chave])
    return rotulos


def _aplicar_siglas(filtros: list, siglas: tuple[str, ...]) -> None:
    if siglas:
        filtros.append({"terms": {_CAMPO_SIGLA: list(siglas)}})


def _eh_filtro_proprio(clausula: dict) -> bool:
    return _CAMPO_SIGLA in clausula.get("terms", {}) or _CAMPO_ANO in clausula.get("term", {})


def ajustar_corpo(
    corpo: dict,
    *,
    siglas: tuple[str, ...],
    tipos: list[str],
    ano_eleicao: int | None,
    tamanho: int,
    pagina: int,
    ordenacao: str,
) -> dict:
    """Ajusta o POST que a SPA montou. NUNCA toca em `captchaToken`."""
    novo = copy.deepcopy(corpo)
    try:
        termo = json.loads(novo["termoPesquisa"])
        clausulas = termo["bool"]
    except (KeyError, TypeError, ValueError) as e:
        raise ErroSJUR(f"corpo da pesquisa em formato inesperado (termoPesquisa): {e}") from e
    must = [c for c in clausulas.get("must", []) if _CAMPO_TIPO not in c.get("terms", {})]
    must.append({"terms": {_CAMPO_TIPO: list(tipos)}})
    clausulas["must"] = must
    filtros = [c for c in clausulas.get("filter", []) if not _eh_filtro_proprio(c)]
    _aplicar_siglas(filtros, siglas)
    if ano_eleicao is not None:
        filtros.append({"term": {_CAMPO_ANO: int(ano_eleicao)}})
    clausulas["filter"] = filtros
    novo["termoPesquisa"] = json.dumps(termo, ensure_ascii=False)
    novo["tamanho"] = int(tamanho)
    novo["pagina"] = int(pagina)
    novo["ordenacao"] = ordenacao
    return novo


@dataclass
class Decisao:
    numero: str
    sem_cnj: bool
    tribunal: str
    classe: str
    tipo_decisao: str
    data: str
    relator: str
    publicacao: str
    uf: str
    municipio: str
    ano_eleicao: str
    codigo_decisao: str
    tem_inteiro_teor: bool
    ementa: str
    decisao: str

    @property
    def oca(self) -> bool:
        return not self.numero and not self.ementa


def _texto(valor) -> str:
    return "" if valor is None else str(valor).strip()


def _classe(x: dict) -> str:
    descricao, sigla = _texto(x.get("descricaoClasse")), _texto(x.get("siglaClasse"))
    if descricao and sigla:
        return f"{descricao} ({sigla})"
    return descricao or sigla


def _cnj(x: dict) -> str:
    """CNJ formatado; na falta, o `numeroUnico` cru de 20 dígitos, formatado."""
    formatado = _texto(x.get("numeroUnicoFormatado"))
    if formatado:
        return formatado
    cru = _texto(x.get("numeroUnico"))
    if re.fullmatch(r"\d{20}", cru):
        return f"{cru[:7]}-{cru[7:9]}.{cru[9:13]}.{cru[13]}.{cru[14:16]}.{cru[16:]}"
    return ""


def _numero_sem_cnj(x: dict) -> str:
    """Sem CNJ, o número do processo no SJUR (o prefixo de 9 dígitos do CNJ,
    medido em 16/10/2026) e o nº do acórdão — não é número de protocolo."""
    processo = _texto(x.get("numeroProcesso"))
    if not processo:
        return ""
    numero = f"{_texto(x.get('siglaClasse'))} nº {processo}".strip()
    acordao = _texto(x.get("numeroDecisao"))
    return f"{numero}, acórdão nº {acordao}" if acordao else numero


def _decisao(x: dict) -> Decisao:
    cnj = _cnj(x)
    numero = cnj or _numero_sem_cnj(x)
    relator = "; ".join(
        _texto(r.get("descricaoCompleta")) or _texto(r.get("nome"))
        for r in (x.get("relatores") or []) if r
    )
    publicacao = "; ".join(
        " ".join(filter(None, (_texto(p.get("siglaFontePublicacao")), _texto(p.get("dataPublicacao")))))
        for p in (x.get("publicacoes") or []) if p
    )
    return Decisao(
        numero=numero,
        sem_cnj=not cnj,
        tribunal=_texto(x.get("siglaTribunalJE")),
        classe=_classe(x),
        tipo_decisao=_texto(x.get("descricaoTipoDecisao")),
        data=_texto(x.get("dataDecisao")),
        relator=relator,
        publicacao=publicacao,
        uf=_texto(x.get("siglaUF")),
        municipio=_texto(x.get("nomeMunicipio")),
        ano_eleicao=_texto(x.get("anoEleicao")),
        codigo_decisao=_texto(x.get("codigoDecisao")),
        tem_inteiro_teor=_texto(x.get("temInteiroTeorPDF")).lower() == "true",
        ementa=limpar_texto_html(x.get("textoEmenta") or ""),
        decisao=limpar_texto_html(x.get("textoDecisao") or ""),
    )


def interpretar_busca(
    payload: dict, *, pagina: int | None = None, tamanho: int | None = None
) -> tuple[list[Decisao], int]:
    """`pagina` (0-based) e `tamanho`, quando informados, separam a página além
    do fim (erro de parâmetro) do item que sumiu dentro do alcance (canário)."""
    if not isinstance(payload, dict):
        raise ErroSJUR(f"resposta da API não é um objeto JSON ({type(payload).__name__})")
    mensagem = payload.get("mensagem")
    if mensagem:
        if "antirrob" in str(mensagem).lower():
            raise ErroSJUR(MSG_ANTIRROBO)
        raise ErroSJUR(f"o portal recusou a busca: {mensagem}")
    itens = payload.get("content")
    total = payload.get("totalRegistros")
    if not isinstance(itens, list) or not isinstance(total, int):
        # Zero só é zero quando a API diz zero: resposta sem `content` lista e
        # `totalRegistros` inteiro (erro do Spring em HTTP 200, mudança de
        # contrato) não pode virar "nenhuma decisão encontrada".
        raise ErroSJUR(
            "formato da resposta da API mudou (sem `content` lista ou "
            f"`totalRegistros` inteiro; chaves: {sorted(payload)[:8]})"
        )
    if total > 0 and not itens and pagina is not None and tamanho and pagina * tamanho >= total:
        ultima = (total - 1) // tamanho + 1
        raise ParametroInvalido(
            f"página {pagina + 1} além do fim: há {total} decisão(ões), "
            f"{ultima} página(s) de {tamanho}"
        )
    if total > 0 and not itens:
        raise ErroSJUR(
            f"o portal informou {total} decisão(ões) e devolveu 0 itens — "
            "provável mudança no JSON da API"
        )
    decisoes = [_decisao(x) for x in itens]
    if decisoes and all(d.oca for d in decisoes):
        raise ErroSJUR(
            f"{len(decisoes)} item(ns) oco(s), sem número nem ementa — "
            "campos da API mudaram"
        )
    return decisoes, total


def conferir_filtros(
    decisoes: list[Decisao], *, alvo: Alvo, tipos: list[str], ano_eleicao: int | None
) -> None:
    """Confere que o ajuste do pedido teve efeito. O backend já ignorou calado
    um filtro (`tribunais`, spike de 16/10/2026); sem esta conferência, a
    resposta de todos os TREs sairia rotulada como a de um só."""
    for d in decisoes:
        if alvo.portal == "tse" and d.tribunal != "TSE":
            erro = f"tribunal {d.tribunal!r} numa busca do TSE"
        elif alvo.portal == "tres" and not d.tribunal.startswith("TRE-"):
            erro = f"tribunal {d.tribunal!r} numa busca dos TREs"
        elif alvo.siglas and d.tribunal not in alvo.siglas:
            erro = f"tribunal {d.tribunal!r} fora de {', '.join(alvo.siglas)}"
        elif d.tipo_decisao and d.tipo_decisao not in tipos:
            erro = f"tipo {d.tipo_decisao!r} fora de {', '.join(tipos)}"
        elif ano_eleicao is not None and d.ano_eleicao and d.ano_eleicao != str(ano_eleicao):
            erro = f"eleição {d.ano_eleicao} fora de {ano_eleicao}"
        else:
            continue
        raise ErroSJUR(
            f"o portal devolveu decisão fora do filtro pedido ({erro}): o ajuste do "
            "pedido não teve efeito — resultado descartado"
        )


def sem_dados_de_partes(payload: dict) -> dict:
    limpo = dict(payload)
    limpo["content"] = [
        {k: v for k, v in item.items() if k != "partes"}
        for item in (payload.get("content") or [])
    ]
    return limpo


def contagem_por_tribunal(payload: dict) -> dict[str, int]:
    for agg in payload.get("aggs") or []:
        if agg.get("nome") == _CAMPO_SIGLA:
            return {
                b["key"]: int(b.get("docCount") or 0)
                for b in agg.get("buckets") or []
                if b.get("key")
            }
    return {}


def avisos_busca(busca: str) -> list[str]:
    # Dentro da frase exata, "e"/"ou" são palavras da frase, não operadores.
    fora_das_aspas = re.sub(r'"[^"]*"', " ", busca)
    if _OPERADOR_MINUSCULO.search(f" {fora_das_aspas} "):
        return [
            "operador em minúsculo (e, ou, nao) é buscado como TERMO; use E, OU, NÃO "
            "em MAIÚSCULO para que o portal os aplique"
        ]
    return []


def mensagem_erro_pdf(corpo: bytes) -> str | None:
    if corpo[:200].lstrip().startswith(_ERRO_PDF):
        return corpo[:300].decode("utf-8", "replace").strip()
    return None


def eh_limite_de_taxa(corpo: bytes) -> bool:
    mensagem = mensagem_erro_pdf(corpo)
    return bool(mensagem and "Excesso de requisi" in mensagem)


def desembrulhar_pdf(corpo: bytes) -> bytes:
    """O PDF vem dentro de multipart/form-data (campo `file`); recorta-o."""
    inicio = corpo.find(b"%PDF-")
    fim = corpo.rfind(b"%%EOF")
    if inicio < 0 or fim < inicio:
        raise ErroSJUR("resposta do inteiro teor sem PDF reconhecível")
    return corpo[inicio:fim + len(b"%%EOF")]


@dataclass
class TextoPDF:
    texto: str
    paginas: int
    paginas_sem_texto: list[int]


def extrair_inteiro_teor(pdf: bytes) -> TextoPDF:
    """Texto do PDF e as páginas (1-based) sem camada de texto — digitalizadas,
    cujo conteúdo falta no texto devolvido."""
    import fitz  # PyMuPDF; import tardio para o módulo carregar sem ele

    with fitz.open(stream=pdf, filetype="pdf") as doc:
        por_pagina = [pagina.get_text() for pagina in doc]
    texto = "\n".join(por_pagina).strip()
    if not texto:
        raise ErroSJUR("PDF do inteiro teor sem camada de texto (provável digitalização)")
    sem_texto = [i for i, t in enumerate(por_pagina, 1) if not t.strip()]
    return TextoPDF(texto=texto, paginas=len(por_pagina), paginas_sem_texto=sem_texto)
