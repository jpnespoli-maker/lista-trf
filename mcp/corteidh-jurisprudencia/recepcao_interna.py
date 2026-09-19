"""Recepção interna de um caso da Corte IDH nos tribunais superiores (spec §6.4).

É a ÚNICA parte deste MCP que vai à rede, e está isolada de propósito: falhando
ela, a busca de parágrafo — caminho crítico durante a redação — continua
servindo offline sobre o índice local. Nada aqui é importado no topo do
`server.py`; a dependência de rede entra por import tardio, dentro da função,
para que um `requests` quebrado não impeça o servidor de subir.

O QUE ELA FAZ, E SOBRETUDO O QUE NÃO FAZ
----------------------------------------
Consulta a base unificada do CJF restrita a STF e STJ, procurando acórdãos que
mencionem a Corte Interamericana e o caso pedido, e devolve os documentos
encontrados. **Não lê a postura do tribunal.** Dizer "a tese é usável" ou "há
resistência interna" a partir de contagem de ocorrências seria inferir o
*holding* de uma palavra-chave: o mesmo acórdão que cita Gomes Lund para acolher
e o que o cita para recusar casam com a mesma busca. Quem lê a ementa é quem
redige; esta ferramenta entrega a lista de onde ler.

ZERO NÃO É RESPOSTA ENQUANTO NÃO FOR CALIBRADO
----------------------------------------------
Uma busca que devolve nada pode significar duas coisas incompatíveis: o tribunal
nunca citou o caso, ou o canal está mudo. O portal do CJF responde HTTP 200 com
"0 Documento(s) encontrado(s)" quando um shard cai — foi medido em 31/08/2026, e
o `cjf_client` já reconfere o zero uma vez por isso. Aqui a calibração é a
segunda linha: aparecendo zero, roda-se um CANÁRIO (`Corte Interamericana`
sozinho, que tem de casar no STF e no STJ). Casando o canário, o zero do caso é
zero MEDIDO; não casando, a medição **não foi feita** e é isso que se declara —
nunca "não há recepção". O canário só roda quando o zero aparece, de modo que o
custo extra é pago apenas no caso em que ele decide.

AS SEMENTES SÃO PONTEIROS, NÃO TESES
------------------------------------
Os casos notórios trazem âncoras internas (ADPFs) que valem conferir junto. Elas
são **curadoria** e vão marcadas como tal: a ferramenta não afirma o que o STF
decidiu em nenhuma delas — ela as BUSCA, e o que chega ao leitor são os
documentos medidos. Âncora errada aparece como âncora sem resultado, não como
afirmação falsa.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Callable

MCP_NOME = "corteidh-jurisprudencia"

# Acórdão publicado não muda. Sete dias é o mesmo TTL que o `cache_http` já
# recomenda para stf/stj-jurisprudencia — ver `TTL_RECOMENDADO` lá.
TTL_S = 7 * 86400

TRIBUNAIS = ("STF", "STJ")

# O canário tem de casar: qualquer um dos dois tribunais já citou a Corte
# Interamericana muitas vezes. Se ele vier zero, o canal está mudo.
CANARIO = "Corte E Interamericana"

_MAX_POR_TRIBUNAL = 10
_MAX_TOKENS_NOME = 4
_MAX_EMENTA = 900

# Âncoras internas dos casos notórios — PONTEIROS de curadoria, nunca teses.
# A chave é o nome normalizado (minúscula, sem acento); o casamento é por
# substring, porque o usuário raramente digita o título integral.
SEMENTES: dict[str, tuple[str, ...]] = {
    "gomes lund": ("ADPF E 153", "ADPF E 320"),
    "herzog": ("ADPF E 153",),
    "favela nova brasilia": ("ADPF E 635",),
}

PROCEDENCIA_SEMENTES = "curadoria — CONFERIR na fonte"

VEREDITOS = ("HA_OCORRENCIA", "SEM_OCORRENCIA", "NAO_APURADO")

# Operadores do CJF. Palavra de título que coincida com um deles vira operador
# na query: medido em 18/09/2026, "Caso Comunidade Garífuna ou seus membros"
# produzia `... E Garifuna E ou E seus`, sintaxe quebrada — e query quebrada
# devolve zero indistinguível de ausência, que é o que esta ferramenta existe
# para não produzir. Descartar o termo custa recall; deixá-lo passar custa a
# medição inteira.
OPERADORES_CJF = frozenset({"e", "ou", "nao", "adj", "prox", "com", "mesmo"})

# Ruído de título ("Caso X e outros Vs. Brasil") que só reduziria a chance de
# casar. A conjunção "e" aparece aqui e em `OPERADORES_CJF` de propósito: são
# dois motivos distintos para descartá-la, e a guarda de comprimento abaixo é um
# terceiro. Redundância deliberada — nenhuma das três sozinha cobre as outras
# ("ou" tem duas letras e escapa do comprimento; "os" não é operador).
_ESTOPE = frozenset({
    "e", "y", "and", "de", "da", "do", "das", "dos", "vs", "v",
    "caso", "outro", "outros", "outra", "outras", "the", "of", "la", "el",
    "os", "as", "em", "no", "na", "nos", "nas", "los", "las", "des", "du",
})

_NAO_PALAVRA = re.compile(r"[^0-9A-Za-zÀ-ÿ]+")


def _normalizar(texto: str) -> str:
    """Minúscula, sem acento, espaços colapsados — só para COMPARAR."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto or "")
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(sem_acento.lower().split())


def nucleo_do_caso(caso: str) -> str:
    """'Caso Gomes Lund e outros Vs. Brasil' → 'Gomes Lund e outros'.

    Parecer consultivo não tem `Vs.`, e aí o núcleo é o título inteiro — o corte
    é pelo que existe, não por uma forma presumida.
    """
    texto = (caso or "").strip()
    texto = re.sub(r"^\s*Caso\s+", "", texto, flags=re.I)
    partes = re.split(r"\s+Vs\.\s+", texto, maxsplit=1, flags=re.I)
    return partes[0].strip(" .")


def termos_do_nome(caso: str) -> list[str]:
    """Tokens significativos do nome, na ordem, no máximo `_MAX_TOKENS_NOME`.

    LIMITE DECLARADO — caso de vítima anonimizada não é pesquisável aqui. A
    Corte tem casos cujo nome é uma inicial ("Caso J. Vs. Perú", "Caso B. Vs.
    El Salvador", "Caso V.R.P., V.P.C. y otros Vs. Nicaragua"), e a guarda de
    comprimento os deixa sem token nenhum. Isso é deliberado: aceitar a inicial
    produziria `Corte E Interamericana E J`, uma consulta que não distingue o
    caso de nada — e cujo zero o canário validaria como AUSÊNCIA MEDIDA. Recusar
    a medição é pior serviço que medir bem, e melhor que medir errado com cara
    de certo. A tool devolve `NAO_APURADO` dizendo por quê.
    """
    tokens = [t for t in _NAO_PALAVRA.split(nucleo_do_caso(caso)) if t]
    uteis = [
        t for t in tokens
        if _normalizar(t) not in _ESTOPE
        and _normalizar(t) not in OPERADORES_CJF
        and len(t) > 1
    ]
    return uteis[:_MAX_TOKENS_NOME]


def consulta_do_caso(caso: str) -> str:
    """Conjunção simples — `Corte E Interamericana E <tokens>`.

    Sem aspas de frase e sem operador posicional: os dois existem no CJF, mas a
    sintaxe deste portal não foi medida para eles neste projeto, e query que não
    casa devolve zero indistinguível de ausência — exatamente o que esta
    ferramenta existe para não produzir.
    """
    termos = termos_do_nome(caso)
    if not termos:
        return ""
    return " E ".join(["Corte", "Interamericana", *termos])


def ancoras_do_caso(caso: str) -> tuple[str, ...]:
    alvo = _normalizar(caso)
    for chave, ancoras in SEMENTES.items():
        if chave in alvo:
            return ancoras
    return ()


def _doc_enxuto(doc: dict, tribunal: str) -> dict:
    ementa = (doc.get("ementa") or "").strip()
    return {
        "tribunal": tribunal,
        "numero": doc.get("numero", ""),
        "classe": doc.get("classe", ""),
        "relator": doc.get("relator", ""),
        "orgao_julgador": doc.get("orgao_julgador", ""),
        "data_julgamento": doc.get("data_julgamento", ""),
        "ementa": ementa[:_MAX_EMENTA],
        "ementa_truncada": len(ementa) > _MAX_EMENTA,
    }


def _buscar_padrao(termo: str, tribunais: list[str], maximo: int):
    """Import TARDIO: o caminho crítico do MCP não pode depender disto."""
    from shared import cjf_client  # noqa: PLC0415

    return cjf_client.buscar_documentos(termo, tribunais, maximo)


def _cache_ler_padrao(key: dict) -> str | None:
    try:
        from shared import cache_http  # noqa: PLC0415

        return cache_http.cached_http(MCP_NOME, key)
    except Exception:
        return None


def _cache_gravar_padrao(key: dict, body: str) -> None:
    try:
        from shared import cache_http  # noqa: PLC0415

        cache_http.registrar_dispositivo(MCP_NOME, key, body, ttl_s=TTL_S)
    except Exception:
        return


def _chave(caso: str) -> dict:
    return {"tool": "recepcao_interna", "caso": _normalizar(caso)}


def consultar(
    caso: str,
    *,
    buscar: Callable[..., Any] | None = None,
    cache_ler: Callable[[dict], Any] | None = None,
    cache_gravar: Callable[[dict, str], None] | None = None,
    usar_cache: bool = True,
) -> dict:
    """Mede a recepção de `caso` no STF e no STJ. NUNCA levanta exceção.

    As três injeções existem para que o teste não vá à rede — e é a `buscar`
    que os testes substituem para exercitar canal mudo, zero medido e erro.
    """
    buscar = buscar or _buscar_padrao
    cache_ler = cache_ler or _cache_ler_padrao
    cache_gravar = cache_gravar or _cache_gravar_padrao

    consulta = consulta_do_caso(caso)
    base: dict[str, Any] = {
        "caso": (caso or "").strip(),
        "consulta": consulta,
        "veredito": "NAO_APURADO",
        "motivo": "",
        "documentos": [],
        "totais": {},
        "ancoras": [],
        "ancoras_procedencia": PROCEDENCIA_SEMENTES,
        "cache": "miss",
    }

    if not consulta:
        base["motivo"] = (
            "Nome de caso sem termo pesquisável — nada a medir, e por isso "
            "nada se afirma. É o que acontece com vítima anonimizada ('Caso J. "
            "Vs. Perú'): a inicial não distingue o caso de nada, e medir com "
            "ela produziria uma ausência falsa com cara de medida. Confira à "
            "mão no CJF, ou passe o título como aparece na ficha (ex.: 'Gomes "
            "Lund e outros Vs. Brasil')."
        )
        return base

    chave = _chave(caso)
    if usar_cache:
        corpo = cache_ler(chave)
        if corpo:
            try:
                guardado = json.loads(corpo)
                guardado["cache"] = "hit"
                return guardado
            except (ValueError, TypeError):
                pass  # cache corrompido é cache miss, não é erro do usuário

    documentos: list[dict] = []
    totais: dict[str, int] = {}
    try:
        for tribunal in TRIBUNAIS:
            docs, tot = buscar(consulta, [tribunal], _MAX_POR_TRIBUNAL)
            documentos.extend(_doc_enxuto(d, tribunal) for d in docs or [])
            totais[tribunal] = int(sum((tot or {}).values()))
    except Exception as exc:  # rede, parser, canário estrutural do cjf_client
        base["motivo"] = (
            f"A consulta ao CJF falhou ({type(exc).__name__}: {exc}). A "
            f"recepção interna NÃO foi apurada — o silêncio aqui não significa "
            f"ausência de precedente."
        )
        return base

    base["documentos"] = documentos
    base["totais"] = totais

    if documentos:
        base["veredito"] = "HA_OCORRENCIA"
        base["motivo"] = (
            f"{len(documentos)} acórdão(s) mencionam a Corte Interamericana e "
            f"este caso. A POSTURA de cada um se lê na ementa — esta busca não "
            f"a infere."
        )
    else:
        # Zero: calibrar antes de chamar de ausência.
        try:
            canario_docs, canario_tot = buscar(
                CANARIO, list(TRIBUNAIS), _MAX_POR_TRIBUNAL)
            canario_ok = bool(canario_docs) or sum(
                (canario_tot or {}).values()) > 0
        except Exception:
            canario_ok = False

        if canario_ok:
            base["veredito"] = "SEM_OCORRENCIA"
            base["motivo"] = (
                "Zero MEDIDO: o canário respondeu, logo o canal está de pé, e "
                "nenhum acórdão do STF ou do STJ indexado no CJF casa com esta "
                "busca. Ausência de citação não é endosso nem rejeição — pode "
                "ser caso ainda não invocado internamente."
            )
        else:
            base["motivo"] = (
                "O canário veio mudo: o CJF respondeu sem acusar sequer os "
                "acórdãos que citam a Corte Interamericana em geral. A medição "
                "NÃO foi feita, e este zero não vale como ausência."
            )
            return base  # NAO_APURADO não se guarda em cache

    for ancora in ancoras_do_caso(caso):
        try:
            docs, _tot = buscar(ancora, list(TRIBUNAIS), _MAX_POR_TRIBUNAL)
            achados = [_doc_enxuto(d, "STF/STJ") for d in docs or []]
        except Exception:
            achados = []
        base["ancoras"].append({
            "termo": ancora,
            "procedencia": PROCEDENCIA_SEMENTES,
            "documentos": achados,
        })

    if usar_cache:
        # Só medição REALIZADA entra em cache. Guardar um `NAO_APURADO` por
        # sete dias transformaria uma queda momentânea do portal em resposta
        # fixa — o erro exato que o TTL deveria evitar.
        try:
            cache_gravar(chave, json.dumps(base, ensure_ascii=False))
        except Exception:
            base["cache"] = "nao-gravado"

    return base
