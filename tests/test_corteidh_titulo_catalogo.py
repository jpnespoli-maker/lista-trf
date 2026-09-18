"""Decomposição do título do catálogo oficial em (caso, etapa).

Este arquivo existe porque o decompositor **passou por uma medição de 100%
estando errado**: a primeira aferição só procurava TRUNCAMENTO (nome acabando
em `Vs`, nome curto) e era cega à SOBRECAPTURA, que é a falha oposta. Com regex
gulosa, o título de *Manuela y otros Vs. El Salvador* — o único dos 598 com
dois ` Vs. `, porque repete o cabeçalho no fim — produziu um "nome de caso" de
400 caracteres com votos e `&nbsp;` dentro, e a medição informou 598/598.

Por isso aqui se mede nas DUAS direções, e o critério de sobrecaptura é LIXO
ESTRUTURAL, não comprimento: há caso da Corte com nome legitimamente extenso
(*ANCEJUB-SUNAT Vs. Perú*, 129 caracteres), e reprovar por tamanho reprovaria o
nome correto.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "corteidh-jurisprudencia"
if str(MODULO) not in sys.path:
    sys.path.insert(0, str(MODULO))

import titulo_catalogo as tc  # noqa: E402

# Marcas que NUNCA pertencem a um nome de caso. Presença = sobrecaptura.
LIXO = re.compile(
    r"Sentencia de|Resoluci[óo]n de|Serie\s+[ACE]\s+No|&nbsp;|Voto:|"
    r"Resumen|Idiomas disponibles|Opini[óo]n Consultiva\s+OC-",
    re.I,
)


# --- casos contenciosos ----------------------------------------------------

def test_caso_simples():
    d = tc.decompor(
        "Casos Contenciosos Corte IDH. Caso Ygarza y otros Vs. Venezuela. "
        "Excepciones Preliminares. Sentencia de 14 de mayo de 2026. "
        "Serie C No. 595. &nbsp;&nbsp;", tipo="CC")
    assert d["caso"] == "Ygarza y otros Vs. Venezuela"
    assert d["etapa"] == "Excepciones Preliminares"


def test_o_ponto_de_Vs_NAO_termina_o_nome():
    """`Vs.` tem ponto e não é fim de frase. Cortar na primeira pontuação
    produziria "Caso Ygarza y otros Vs" — citação truncada com aparência de
    verdadeira."""
    d = tc.decompor(
        "Casos Contenciosos Corte IDH. Caso Vargas Areco Vs. Paraguay. "
        "Sentencia de 26 de septiembre de 2006. Serie C No. 155.", tipo="CC")
    assert d["caso"] == "Vargas Areco Vs. Paraguay"
    assert not d["caso"].endswith("Vs")


def test_DOIS_Vs_no_titulo_ancora_no_PRIMEIRO():
    """O título repete o cabeçalho no fim; a âncora é o PRIMEIRO `Vs. <país>.`.

    Com quantificador guloso o nome ia até a segunda ocorrência e engolia
    etapa, autuação, votos e marcação — 400 caracteres de lixo. É o caso que a
    medição de 100% não viu.
    """
    # Cauda FIEL ao título real (838 caracteres no catálogo): a repetição do
    # cabeçalho no fim vem seguida de ponto e de mais uma decisão. A primeira
    # versão deste teste cortava a cauda antes do ponto — e aí a regex gulosa
    # RETROCEDIA até a primeira ocorrência, dando o resultado certo por
    # acidente. A mutação sobreviveu, e foi ela que revelou o teste cego.
    titulo = (
        "Casos Contenciosos Corte IDH. Caso Manuela y otros Vs. El Salvador. "
        "Excepciones preliminares, Fondo, Reparaciones y Costas. Sentencia de "
        "2 de noviembre de 2021. Serie C No. 441. &nbsp;&nbsp; Voto: Juez "
        "Sierra Porto &nbsp; Idiomas disponibles Corte IDH. Caso Manuela y "
        "otros Vs. El Salvador. Interpretación de la Sentencia de Excepciones "
        "Preliminares, Fondo, Reparaciones y Costas. Sentencia de 27 de julio "
        "de 2022. Serie C No. 461. &nbsp;")
    d = tc.decompor(titulo, tipo="CC")
    assert d["caso"] == "Manuela y otros Vs. El Salvador"
    assert not LIXO.search(d["caso"]), d["caso"]
    assert len(d["caso"]) < 60


def test_nome_longo_LEGITIMO_nao_e_truncado():
    """Comprimento não é defeito: este é o nome oficial do caso."""
    d = tc.decompor(
        "Casos Contenciosos Corte IDH. Caso Asociación Nacional de Cesantes y "
        "Jubilados de la Superintendencia Nacional de Administración "
        "Tributaria (ANCEJUB-SUNAT) Vs. Perú. Excepciones Preliminares, "
        "Fondo, Reparaciones y Costas. Sentencia de 21 de noviembre de 2019. "
        "Serie C No. 394.", tipo="CC")
    assert d["caso"].startswith("Asociación Nacional de Cesantes")
    assert d["caso"].endswith("Vs. Perú")
    assert not LIXO.search(d["caso"])


def test_interpretacao_e_a_ETAPA_nao_etapa_vazia():
    """105 dos 598 títulos são de interpretação.

    Tratá-los como etapa vazia os faria citar como se fossem a sentença de
    mérito — e a etapa existe justamente para distinguir as duas.
    """
    d = tc.decompor(
        "Casos Contenciosos Corte IDH. Caso Rodríguez Pighi y otros Vs. Perú. "
        "Interpretación de la Sentencia de Excepciones Preliminares, Fondo, "
        "Reparaciones y Costas. Sentencia de 3 de julio de 2026. "
        "Serie C No. 598.", tipo="CC")
    assert d["caso"] == "Rodríguez Pighi y otros Vs. Perú"
    assert d["etapa"] is not None
    assert d["etapa"].startswith("Interpretação da Sentença de ")
    # O bug do "de de": o título diz "Sentencia DE Excepciones" e o rótulo
    # montado já termina em "de". Medido em 105 títulos.
    assert " de de " not in d["etapa"], d["etapa"]
    assert "Excepciones Preliminares" in d["etapa"]


def test_sem_etapa_rotulada_devolve_None():
    """13 dos 598 vão do nome direto para "Sentencia de <data>": não há etapa,
    e None é a resposta correta — não um rótulo inventado."""
    d = tc.decompor(
        "Casos Contenciosos Corte IDH. Caso Huacón Baidal y otros Vs. "
        "Ecuador. Sentencia de 4 de octubre de 2022. Serie C No. 466.",
        tipo="CC")
    assert d["caso"] == "Huacón Baidal y otros Vs. Ecuador"
    assert d["etapa"] is None


def test_titulo_irreconhecivel_devolve_None_nao_palpite():
    """Nome que não se isolou sai None. Documento indexado sob nome truncado é
    pior que sem nome: a citação sai plausível e errada."""
    for ruim in ("", "   ", "lixo qualquer sem estrutura",
                 "Casos Contenciosos Corte IDH. Sentencia de 2020."):
        d = tc.decompor(ruim, tipo="CC")
        assert d["caso"] is None, (ruim, d)


# --- pareceres consultivos -------------------------------------------------

def test_oc_corta_a_remissao_normativa():
    """O parentético cita ARTIGO: é remissão, não nome.

    Sem o corte, um nome saiu com 654 caracteres, arrastando a lista inteira de
    artigos e a cauda "Opinión Consultiva OC-27/21 de 5 de mayo de 2021".
    """
    d = tc.decompor(
        "Opiniones Consultivas Corte IDH. Derechos a la libertad sindical, "
        "negociación colectiva y huelga, y su relación con otros derechos, "
        "con perspectiva de género (interpretación y alcance de los artículos "
        "13, 15, 16, 24, 25 y 26, en relación con los artículos 1.1 y 2 de la "
        "Convención Americana sobre Derechos Humanos). Opinión Consultiva "
        "OC-27/21 de 5 de mayo de 2021", tipo="OC")
    assert d["caso"].startswith("Derechos a la libertad sindical")
    assert d["caso"].endswith("perspectiva de género")
    assert "artículo" not in d["caso"]
    assert not LIXO.search(d["caso"]), d["caso"]
    assert d["etapa"] is None


def test_oc_com_cauda_e_SEM_parentese():
    """10 dos 33 pareceres têm a cauda `Opinión Consultiva OC-N/YY` e NENHUM
    parentético de remissão — entre eles a **OC-18/03**, que está no corpus.

    Este teste existe porque a mutação que desliga o corte da cauda
    SOBREVIVEU ao teste anterior: lá a cauda vinha depois do parêntese, e o
    corte do parêntese a removia por tabela. Sem este caso, o corte da cauda
    parecia código morto — e removê-lo teria estragado 10 nomes.
    """
    d = tc.decompor(
        "Opiniones Consultivas Corte IDH. Condición jurídica y derechos de "
        "los migrantes indocumentados. Opinión Consultiva OC-18/03 de 17 de "
        "septiembre de 2003. Serie A No. 18. &nbsp;&nbsp;", tipo="OC")
    assert d["caso"] == "Condición jurídica y derechos de los migrantes indocumentados"
    assert "OC-18" not in d["caso"]
    assert not LIXO.search(d["caso"]), d["caso"]


def test_oc_cujo_NOME_comeca_por_artigo():
    """*Artículo 55 de la Convención Americana* — o nome cita artigo, e a
    regra de corte por remissão não pode comê-lo. Ela só age DENTRO de
    parêntese, e aqui não há."""
    d = tc.decompor(
        "Opiniones Consultivas Corte IDH. Artículo 55 de la Convención "
        "Americana sobre Derechos Humanos. Opinión Consultiva OC-20/09 de 29 "
        "de septiembre de 2009. Serie A No. 20.", tipo="OC")
    assert d["caso"] == "Artículo 55 de la Convención Americana sobre Derechos Humanos"


def test_oc_remissao_que_NAO_comeca_por_interpretacion():
    """O parêntese não começa sempre por "interpretación".

    Em *Medio ambiente y derechos humanos* abre por "(obligaciones
    estatales... - interpretación y alcance de los artículos 4.1 y 5.1...)", e
    casar o início do parêntese deixava 328 caracteres de remissão no nome.
    """
    d = tc.decompor(
        "Opiniones Consultivas Corte IDH. Medio ambiente y derechos humanos "
        "(obligaciones estatales en relación con el medio ambiente en el marco "
        "de la protección y garantía de los derechos a la vida y a la "
        "integridad personal - interpretación y alcance de los artículos 4.1 "
        "y 5.1, en relación con los artículos 1.1 y 2 de la Convención "
        "Americana sobre Derechos Humanos)", tipo="OC")
    assert d["caso"] == "Medio ambiente y derechos humanos"


def test_oc_remissao_com_Arts_abreviado():
    """`Arts.` abreviado é remissão igual."""
    d = tc.decompor(
        "Opiniones Consultivas Corte IDH. Control de legalidad en el "
        "ejercicio de las atribuciones de la Comisión Interamericana de "
        "Derechos Humanos (Arts. 41 y 44 a 51 de la Convención Americana "
        "sobre Derechos Humanos)", tipo="OC")
    assert d["caso"].endswith("Derechos Humanos")
    assert "Arts." not in d["caso"]
    assert len(d["caso"]) < 130


def test_oc_nao_recebe_prefixo_de_caso():
    """Parecer consultivo não é caso e não tem `Vs.` — 0 de 33 medidos."""
    d = tc.decompor(
        "Opiniones Consultivas Corte IDH. Emergencia Climática y Derechos "
        "Humanos", tipo="OC")
    assert d["caso"] == "Emergencia Climática y Derechos Humanos"
    assert "Vs." not in d["caso"]


def test_oc_nome_de_duas_frases_sobrevive():
    """A Corte dá título de duas frases a alguns pareceres; o ponto do meio não
    é fim de nome."""
    d = tc.decompor(
        "Opiniones Consultivas Corte IDH. Identidad de género, e igualdad y "
        "no discriminación a parejas del mismo sexo. Obligaciones estatales "
        "en relación con el cambio de nombre (interpretación y alcance de los "
        "artículos 1.1, 3, 7 de la Convención)", tipo="OC")
    assert "Obligaciones estatales" in d["caso"]
    assert "artículo" not in d["caso"]


# --- invariante de saída ---------------------------------------------------

def test_nenhum_nome_carrega_lixo_estrutural():
    """Invariante que vale para os dois tipos, e é o que separa citação
    conferível de citação que PARECE inventada."""
    amostras = [
        ("CC", "Casos Contenciosos Corte IDH. Caso X y otros Vs. Perú. Fondo. "
               "Sentencia de 1 de enero de 2020. Serie C No. 1. &nbsp;"),
        ("OC", "Opiniones Consultivas Corte IDH. Tema qualquer (interpretación "
               "y alcance de los artículos 1 y 2). Serie A No. 9."),
    ]
    for tipo, titulo in amostras:
        caso = tc.decompor(titulo, tipo=tipo)["caso"]
        assert caso, (tipo, titulo)
        assert not LIXO.search(caso), (tipo, caso)
        assert caso == caso.strip()
        assert not caso.endswith(".")
