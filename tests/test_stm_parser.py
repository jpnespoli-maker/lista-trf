"""Regras puras da Consulta Judicial Unificada da JMU (jurisprudência do STM).

O portal devolve a MESMA página para zero resultados, query malformada e
parâmetro inválido (medido em 20/10/2026). Estes testes travam que essa página
vira ``total=None`` — nunca zero — e que o que o portal recusaria em silêncio
(aspas e parênteses desbalanceados, ordem e data inválidas) se barra antes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "stm-jurisprudencia"
if str(MODULO) not in sys.path:
    sys.path.insert(0, str(MODULO))

import stm_parser as P  # noqa: E402

FIX = RAIZ / "tests" / "fixtures" / "stm"


def _fixture(nome: str) -> str:
    return (FIX / nome).read_text(encoding="utf-8")


# ---------------- página de resultado ----------------

def test_resultado_traz_total_e_os_paineis():
    total, docs = P.parsear_resultados(_fixture("busca_3_paineis.html"))
    assert total == 1386
    assert [d.numero for d in docs] == [
        "7000238-07.2024.7.02.0002",
        "7000019-48.2025.7.12.0012",
        "7000428-19.2024.7.03.0203",
    ]


def test_campos_do_painel():
    _, docs = P.parsear_resultados(_fixture("busca_3_paineis.html"))
    d = docs[1]
    assert re.fullmatch(r"[0-9a-f]{32}", d.id)
    assert d.classe == "APELAÇÃO CRIMINAL"
    assert d.relator == "CARLOS VUYK DE AQUINO"
    assert d.revisor == "VERÔNICA ABDALLA STERMAN"
    assert (d.data_autuacao, d.data_julgamento, d.data_publicacao) == (
        "03/11/2025", "18/06/2026", "26/06/2026")
    assert d.assuntos.startswith("1) DIREITO PENAL MILITAR")


def test_ementa_sem_rotulo_e_integral():
    _, docs = P.parsear_resultados(_fixture("busca_3_paineis.html"))
    for d in docs:
        assert d.ementa and not d.ementa.upper().startswith("EMENTA")
        assert "  " not in d.ementa
    # A ementa do painel é a do modal `doc_type=ementa`, sem corte (conferido em
    # 6 acórdãos em 20/10/2026): o fim do texto tem de estar presente.
    assert docs[1].ementa.endswith("Decisão por unanimidade.")


def test_pagina_do_zero_nao_e_zero():
    assert P.parsear_resultados(_fixture("zero_ou_recusa.html")) == (None, [])


def test_pagina_vazia_nao_e_zero():
    assert P.parsear_resultados("") == (None, [])


# ---------------- validação da busca ----------------

@pytest.mark.parametrize("busca", ["", "   "])
def test_busca_vazia(busca):
    with pytest.raises(ValueError, match="vazia"):
        P.validar_busca(busca)


@pytest.mark.parametrize("busca", ['deserção e ("estado', '"estado de necessidade'])
def test_aspas_desbalanceadas(busca):
    with pytest.raises(ValueError, match="aspas"):
        P.validar_busca(busca)


@pytest.mark.parametrize("busca", ["deserção e (ameaça", "deserção) e (ameaça"])
def test_parenteses_desbalanceados(busca):
    with pytest.raises(ValueError, match="parênteses"):
        P.validar_busca(busca)


def test_busca_valida_volta_aparada():
    assert P.validar_busca('  deserção e ("estado de necessidade" ou ameaça) ') == \
        'deserção e ("estado de necessidade" ou ameaça)'


# ---------------- operador "não" ----------------

@pytest.mark.parametrize("busca, esperado", [
    ("deserção nao furto", "deserção não furto"),
    ("deserção NAO furto", "deserção não furto"),
    ("deserção e (nao furto)", "deserção e (não furto)"),
])
def test_nao_sem_acento_vira_operador(busca, esperado):
    # Medido em 20/10/2026: "nao" é termo para o portal (7 documentos contra
    # 4.664 de "não"); a busca encolheria calada.
    assert P.normalizar_operadores(busca) == (esperado, True)


@pytest.mark.parametrize("busca", [
    'deserção e "nao comparecimento"',  # dentro de aspas é texto
    "deserção não furto",
    "nação e naoexiste e nao-alvo",       # palavra que contém "nao"
])
def test_nao_fora_do_operador_fica(busca):
    assert P.normalizar_operadores(busca) == (busca, False)


def test_montar_params_ja_corrige():
    assert P.montar_params("deserção nao furto")["q"] == "deserção não furto"


# ---------------- parâmetros ----------------

def test_params_padrao_so_jurisprudencia():
    p = P.montar_params("deserção")
    assert p == {
        "search_filter_option": "jurisprudencia",
        "search_filter": "tudo",
        "q": "deserção",
        "order": "1",
        "start": "0",
        "rows": "10",
    }


def test_filtros_viram_fq():
    p = P.montar_params("deserção", relator="  carlos vuyk  de aquino ",
                        classe="habeas corpus criminal",
                        data_inicio="01/01/2026", data_fim="2026-06-30",
                        ordem="antigo", inicio=25, quantidade=50)
    assert p["fq_ministro_relator"] == "CARLOS VUYK DE AQUINO"
    assert p["fq_classe"] == "HABEAS CORPUS CRIMINAL"
    assert p["fq_data_decisao"] == "[2026-01-01T00:00:00Z TO 2026-06-30T23:59:59Z]"
    assert (p["order"], p["start"], p["rows"]) == ("2", "25", "50")


def test_data_so_de_um_lado_fica_aberta():
    assert P.montar_params("deserção", data_fim="31/12/2019")["fq_data_decisao"] == \
        "[* TO 2019-12-31T23:59:59Z]"
    assert "fq_data_decisao" not in P.montar_params("deserção")


@pytest.mark.parametrize("kw", [{"ordem": "relevancia"}, {"data_inicio": "2026/01/01"},
                                {"data_fim": "31/02/2026"}])
def test_o_que_o_portal_recusaria_em_silencio(kw):
    # `order` inválido devolve a página do zero (medido); data inválida idem.
    with pytest.raises(ValueError):
        P.montar_params("deserção", **kw)


# ---------------- modais ----------------

def test_texto_do_modal_preserva_as_linhas():
    txt = P.texto_modal(_fixture("modal_inteiro_teor_trecho.html"))
    linhas = txt.splitlines()
    assert linhas[:2] == ["Poder Judiciário", "STM"]
    assert "APELAÇÃO CRIMINAL Nº 7000238-07.2024.7.02.0002/SP" in linhas
    assert "\n\n\n" not in txt


def test_metadados_em_texto():
    ref = P.texto_modal(_fixture("modal_referencia_legislativa.html"))
    assert ref.startswith("Constituição da República Federativa do Brasil de 1988")
    assert "CPM (Decreto-Lei nº 1.001/1969)" in ref
    notas = P.texto_modal(_fixture("modal_notas.html"))
    assert "STF - RHC 142.608/SP" in notas
    assert "<br" not in notas


@pytest.mark.parametrize("uuid", ["", "abc", "e446475397c693c4670e2b922712967", "z" * 32])
def test_uuid_invalido(uuid):
    with pytest.raises(ValueError, match="id inválido"):
        P.validar_uuid(uuid)


def test_uuid_valido_normalizado():
    assert P.validar_uuid(" E446475397C693C4670E2B922712967B ") == "e446475397c693c4670e2b922712967b"
