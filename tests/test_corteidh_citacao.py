"""Forma canônica da citação da Corte IDH.

Citação com a forma errada parece inventada mesmo sendo verdadeira — e é o
validador-citacoes que vai ler esta string. Por isso a forma é travada por
teste, não deixada ao gosto de quem chama.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "corteidh-jurisprudencia"
if str(MODULO) not in sys.path:
    sys.path.insert(0, str(MODULO))

import citacao  # noqa: E402


def test_sentenca_com_paragrafo():
    assert citacao.formatar_citacao(
        caso="Ximenes Lopes Vs. Brasil", data="2006-07-04",
        serie="C", numero=149, paragrafo=89,
    ) == ("Corte IDH. Caso Ximenes Lopes Vs. Brasil. Sentença de 4 de julho "
          "de 2006. Série C No. 149, par. 89.")


def test_sentenca_sem_paragrafo():
    assert citacao.formatar_citacao(
        caso="Ximenes Lopes Vs. Brasil", data="2006-07-04",
        serie="C", numero=149,
    ) == ("Corte IDH. Caso Ximenes Lopes Vs. Brasil. Sentença de 4 de julho "
          "de 2006. Série C No. 149.")


def test_etapa_entra_antes_da_data():
    assert citacao.formatar_citacao(
        caso="Trabalhadores da Fazenda Brasil Verde Vs. Brasil",
        data="2016-10-20", serie="C", numero=318, paragrafo=318,
        etapa="Exceções Preliminares, Mérito, Reparações e Custas",
    ) == ("Corte IDH. Caso Trabalhadores da Fazenda Brasil Verde Vs. Brasil. "
          "Exceções Preliminares, Mérito, Reparações e Custas. Sentença de "
          "20 de outubro de 2016. Série C No. 318, par. 318.")


def test_opiniao_consultiva_usa_parecer_e_serie_a():
    assert citacao.formatar_citacao(
        caso="Condição Jurídica e Direitos dos Migrantes Indocumentados",
        data="2003-09-17", serie="A", numero=18, paragrafo=112, tipo="OC",
    ) == ("Corte IDH. Condição Jurídica e Direitos dos Migrantes "
          "Indocumentados. Parecer Consultivo OC-18/03 de 17 de setembro "
          "de 2003. Série A No. 18, par. 112.")


def test_documento_sem_serie_omite_o_trecho_da_serie():
    assert citacao.formatar_citacao(
        caso="Caso Gomes Lund e outros Vs. Brasil", data="2021-11-19",
        serie=None, numero=None, tipo="SS",
    ) == ("Corte IDH. Caso Gomes Lund e outros Vs. Brasil. Supervisão de "
          "Cumprimento de Sentença. Resolução de 19 de novembro de 2021.")


@pytest.mark.parametrize("iso,esperado", [
    ("2006-07-04", "4 de julho de 2006"),
    ("2018-03-08", "8 de março de 2018"),
    ("2010-11-24", "24 de novembro de 2010"),
    ("2020-07-15", "15 de julho de 2020"),
])
def test_data_por_extenso_em_portugues(iso, esperado):
    assert citacao.data_por_extenso(iso) == esperado


def test_data_invalida_devolve_o_bruto_sem_estourar():
    assert citacao.data_por_extenso("sem data") == "sem data"


# --- O LINK é parte da citação (spec §6.1-bis) -----------------------------
# O Defensor confere na fonte, e conferir na fonte é o que impede alucinação.
# O endereço entra DENTRO da string que se cola na peça, e não como campo
# separado que quem redige possa esquecer de copiar.

URL_149_POR = "https://www.corteidh.or.cr/docs/casos/articulos/seriec_149_por.pdf"
URL_349_ESP = "https://www.corteidh.or.cr/docs/casos/articulos/seriec_349_esp.pdf"


def test_url_entra_como_disponivel_em_no_fim():
    assert citacao.formatar_citacao(
        caso="Ximenes Lopes Vs. Brasil", data="2006-07-04",
        serie="C", numero=149, paragrafo=89, url=URL_149_POR,
    ) == ("Corte IDH. Caso Ximenes Lopes Vs. Brasil. Sentença de 4 de julho "
          f"de 2006. Série C No. 149, par. 89. Disponível em: {URL_149_POR}")


def test_url_em_espanhol_e_a_que_sai_quando_e_a_passada():
    """O link é o do IDIOMA DO TRECHO — quem chama escolhe, a função obedece."""
    saida = citacao.formatar_citacao(
        caso="Poblete Vilches e outros Vs. Chile", data="2018-03-08",
        serie="C", numero=349, paragrafo=118, url=URL_349_ESP,
    )
    assert saida.endswith(f"Disponível em: {URL_349_ESP}")
    assert "_por.pdf" not in saida


def test_sem_url_a_citacao_nao_inventa_link():
    """Função pura: não adivinha endereço. A obrigação de passar é de quem chama."""
    saida = citacao.formatar_citacao(
        caso="Ximenes Lopes Vs. Brasil", data="2006-07-04",
        serie="C", numero=149, paragrafo=89,
    )
    assert "Disponível em" not in saida
    assert "http" not in saida


def test_url_em_branco_equivale_a_ausente():
    for vazia in ("", "   ", None):
        saida = citacao.formatar_citacao(
            caso="X Vs. Brasil", data="2006-07-04", serie="C", numero=1,
            url=vazia)
        assert "Disponível em" not in saida


def test_url_tambem_sai_em_documento_sem_serie():
    saida = citacao.formatar_citacao(
        caso="Caso Gomes Lund e outros Vs. Brasil", data="2021-11-19",
        serie=None, numero=None, tipo="SS", url=URL_149_POR,
    )
    assert saida.endswith(f"Disponível em: {URL_149_POR}")
    assert "Resolução de 19 de novembro de 2021." in saida
