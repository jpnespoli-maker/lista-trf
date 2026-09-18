"""Decompõe o título do buscador oficial em (caso, etapa) — módulo PURO.

O catálogo da Corte devolve um título corrido que embute o nome do caso, a
etapa e a autuação:

    Casos Contenciosos Corte IDH. Caso Ygarza y otros Vs. Venezuela.
    Excepciones Preliminares. Sentencia de 14 de mayo de 2026. Serie C No. 595.

A `data`, a `serie` e o `numero` já saem do `parsear_resultados`. O que falta —
e é o que decide como o documento se cita — são o **nome do caso** e a **etapa**.

**A armadilha, medida contra os 598 títulos reais:** o nome do caso contém
`Vs.`, e aquele ponto NÃO termina frase. Cortar no primeiro `. ` produziria
"Caso Ygarza y otros Vs" — citação truncada com aparência de verdadeira, que é
exatamente o defeito que este subsistema existe para evitar. Por isso o corte
se ancora em `Vs. <país>.`, e não em pontuação genérica.

**Um título tem DOIS ` Vs. `** (medido: 1 em 598), e a âncora tem de usar o
PRIMEIRO. Aquele título — *Manuela y otros Vs. El Salvador* — repete o próprio
cabeçalho no fim, na seção "Idiomas disponibles"; com regex gulosa (`.+`), o
nome capturado ia do `Caso ` inicial até a segunda ocorrência e engolia etapa,
autuação, votos e marcação `&nbsp;` — 400 caracteres de lixo no lugar do nome.
O nome do caso está no COMEÇO do título, então o corte é no primeiro
`Vs. <país>.`, com quantificador preguiçoso.

**Como isso passou por uma medição de 100%:** a primeira aferição só procurava
TRUNCAMENTO (nome acabando em `Vs`, nome curto) e era cega à SOBRECAPTURA, que
é a falha oposta. Detector que mede uma direção informa 598/598 sobre um acervo
com um nome de 400 caracteres. A aferição definitiva, em
`test_corteidh_titulo_catalogo.py`, mede as duas.

**Parecer consultivo não é caso** e não tem `Vs.` nenhum (0 de 33): o nome é a
ementa descritiva, e o que se guarda é a parte anterior ao parêntese de
remissão normativa. O corte NÃO se faz pelo início do parêntese — ele varia
("(interpretación y alcance...", "(obligaciones estatales... - interpretación",
"(Arts. 41 y 44 a 51...") — e sim pela assinatura de **citar artigo**, que é o
que uma remissão faz e um nome não.
"""

from __future__ import annotations

import re

# Rótulo do tipo com que o buscador prefixa todo título.
_PREFIXO = re.compile(
    r"^\s*(?:Casos Contenciosos|Opiniones Consultivas|"
    r"Supervisión de Cumplimiento de Sentencia|Resoluciones(?:\s+\w+)?|"
    r"Medidas Provisionales|Otros asuntos|Ficha técnica)?\s*"
    r"Corte IDH\.\s*",
    re.I,
)

# `Caso <nome> Vs. <país>.` — quantificador PREGUIÇOSO (`.+?`), para ancorar no
# PRIMEIRO `Vs. <país>.` e não na repetição do cabeçalho no fim do título.
_CASO = re.compile(r"Caso\s+(?P<nome>.+?\s+Vs\.\s+[^.]+?)\s*\.(?=\s|$)")

# Rótulo da decisão. `Interpretación de la Sentencia` vem PRIMEIRO na alternância
# porque é ela mesma a etapa — 105 dos 598 títulos são de interpretação, e
# tratá-los como etapa vazia os faria citar como se fossem a sentença de mérito,
# que é justamente o que a etapa serve para distinguir.
_INTERPRETACAO = re.compile(r"Interpretaci[óo]n de la Sentencia", re.I)
_ROTULO_DECISAO = re.compile(r"\b(?:Sentencia|Resoluci[óo]n)\b", re.I)

# Lixo de marcação que o buscador deixa no fim.
_LIXO = re.compile(r"(?:&nbsp;|&amp;nbsp;|\s)+$")


def _limpar(texto: str) -> str:
    return _LIXO.sub("", re.sub(r"\s+", " ", texto or "")).strip()


def decompor(titulo: str, *, tipo: str = "CC") -> dict:
    """{'caso', 'etapa'} — ambos podem vir None, e None é resposta.

    Nome que não se conseguiu isolar sai `None`, nunca um palpite: documento
    indexado sob nome truncado é pior que documento sem nome, porque a citação
    sai plausível e errada.
    """
    bruto = _limpar(titulo)
    corpo = _PREFIXO.sub("", bruto, count=1)

    if tipo.upper() == "OC":
        # Nome = tudo até o parêntese de remissão normativa, ou até a autuação.
        #
        # MEDIDO: a divisão era sensível à CAIXA (`Interpretación` maiúsculo) e
        # 24 dos 33 títulos usam minúscula — esses carregavam o parentético
        # inteiro mais a cauda "Opinión Consultiva OC-N/YY de <data>", e um
        # nome saiu com 654 caracteres. Daí `re.I` e o corte também na cauda da
        # autuação, que é referência e não nome.
        for corte in (r"\s*\.\s*Opini[óo]n Consultiva\s+OC-",
                      r"\s*Serie\s+[ACE]\s+No"):
            nome = re.split(corte, corpo, maxsplit=1, flags=re.I)[0]
            if nome != corpo:
                corpo = nome
        # O parêntese da remissão normativa não é parte do nome, e ele NÃO
        # começa sempre por "interpretación": em *Medio ambiente y derechos
        # humanos* abre por "(obligaciones estatales... - interpretación y
        # alcance de los artículos 4.1 y 5.1...)", de modo que casar o início
        # deixava 328 caracteres de remissão dentro do nome. A assinatura
        # confiável é CITAR ARTIGO — é isso que uma remissão faz e um nome não.
        abre = corpo.find("(")
        # `Arts.` abreviado conta: em *Control de legalidad...* a remissão vem
        # como "(Arts. 41 y 44 a 51 de la Convención Americana...)".
        if abre > 0 and re.search(r"\bart(?:[íi]culos?|s?\.)\s",
                                  corpo[abre:], re.I):
            corpo = corpo[:abre]
        nome = _limpar(corpo).rstrip(".")
        return {"caso": nome or None, "etapa": None}

    m = _CASO.search(corpo)
    if not m:
        return {"caso": None, "etapa": None}

    # Âncora no PRIMEIRO `Vs. <país>.`, porque o nome está no começo do título
    # e o cabeçalho pode se repetir no fim (ver o docstring).
    nome = _limpar(m.group("nome"))
    resto = corpo[m.end():]

    etapa = None
    mi = _INTERPRETACAO.search(resto)
    if mi and mi.start() <= 2:
        # "Interpretación de la Sentencia de <o que se interpreta>." A etapa é
        # a interpretação, e o que vem depois do "de" é a decisão interpretada.
        alvo = _limpar(resto[mi.end():]).lstrip(". ")
        # O título diz "Interpretación de la Sentencia DE Excepciones...", e o
        # rótulo que se monta já termina em "de" — sem remover este, a etapa
        # sai "Interpretação da Sentença de de Excepciones", medido em 105 dos
        # 598 títulos.
        alvo = re.sub(r"^de\s+", "", alvo, flags=re.I)
        alvo = re.split(r"\s*Sentencia de\s", alvo, maxsplit=1)[0]
        alvo = _limpar(alvo).rstrip(",").rstrip(".")
        etapa = ("Interpretação da Sentença de " + alvo) if alvo \
            else "Interpretação da Sentença"
    else:
        md = _ROTULO_DECISAO.search(resto)
        if md and md.start() > 0:
            etapa = _limpar(resto[: md.start()]).rstrip(",").rstrip(".")
            etapa = etapa or None

    return {"caso": nome or None, "etapa": etapa}
