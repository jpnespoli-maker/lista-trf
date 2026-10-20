"""Regras puras do Banco de Sentenças das Justiças Militares.

O que se trava, medido em 20/10/2026: (a) a busca vai ao portal com AND entre
os termos e operadores em MAIÚSCULO — o padrão dele é OU, e em minúsculo o
operador vira termo ("deserção e ameaça" 20.347 contra 108 de "deserção AND
ameaça"); (b) por_pagina só 10 ou 20, porque outro valor volta HTTP 403; (c) o
filtro de órgão resolve pela tabela da página, e o que não casa é erro.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "sentencas-militares"
if str(MODULO) not in sys.path:
    sys.path.insert(0, str(MODULO))

import sentencas_parser as P  # noqa: E402

FIX = RAIZ / "tests" / "fixtures" / "sentencas_militares"


def _fixture(nome):
    return json.loads((FIX / nome).read_text(encoding="utf-8"))


# ---------------- normalização da busca ----------------

@pytest.mark.parametrize("busca, esperado", [
    ("deserção ameaça", "deserção AND ameaça"),
    ('deserção "estado de necessidade"', 'deserção AND "estado de necessidade"'),
    ("deserção -furto", "deserção AND -furto"),
    ("deserção NOT furto", "deserção AND NOT furto"),
    ("(deserção OR insubmissão) ameaça", "( deserção OR insubmissão ) AND ameaça"),
    ("deserção AND ameaça", "deserção AND ameaça"),
    ("desert*", "desert*"),
])
def test_termos_sem_operador_viram_and(busca, esperado):
    assert P.normalizar_busca(busca)[0] == esperado


@pytest.mark.parametrize("busca, esperado", [
    ("deserção e ameaça", "deserção AND ameaça"),
    ("deserção ou insubmissão", "deserção OR insubmissão"),
    ("deserção não furto", "deserção AND NOT furto"),
    ("deserção nao furto", "deserção AND NOT furto"),
    ("deserção and ameaça", "deserção AND ameaça"),
    ("deserção E ameaça", "deserção AND ameaça"),
])
def test_operador_em_portugues_ou_minusculo_e_convertido(busca, esperado):
    query, avisos = P.normalizar_busca(busca)
    assert query == esperado
    assert any("OPERADOR CONVERTIDO" in a for a in avisos)


def test_operador_dentro_de_aspas_fica():
    query, avisos = P.normalizar_busca('"furto e roubo"')
    assert query == '"furto e roubo"'
    assert avisos == []


def test_busca_ja_correta_nao_ganha_aviso():
    assert P.normalizar_busca("deserção AND ameaça") == ("deserção AND ameaça", [])


def test_and_implicito_e_avisado():
    _, avisos = P.normalizar_busca("deserção ameaça")
    assert any("LIGADOS POR AND" in a for a in avisos)


@pytest.mark.parametrize("busca, erro", [
    ("de", "3 caracteres"),
    ('deserção "estado', "aspas"),
    ("(deserção ameaça", "parênteses"),
])
def test_busca_invalida(busca, erro):
    with pytest.raises(ValueError, match=erro):
        P.normalizar_busca(busca)


def test_termos_positivos_para_o_trecho():
    q = P.normalizar_busca('deserção "estado de necessidade" -furto NOT roubo desert*')[0]
    assert P.termos_positivos(q) == ["deserção", "estado de necessidade", "desert"]


# ---------------- corpo ----------------

def test_corpo_padrao():
    assert P.montar_corpo("deserção", ids_orgaos=[32693]) == {
        "pageOffset": 1, "pageSize": 10, "searchAfterValue": None, "order": "desc",
        "searchFilters": [{"texto": "deserção", "ids_orgaos": [32693]}],
    }


def test_corpo_com_datas_ordem_e_cursor():
    c = P.montar_corpo("deserção", ids_orgaos=None, data_inicio="01/01/2026",
                       data_fim="2026-06-30", ordem="antigo", por_pagina=20, cursor="1791409545")
    assert c["searchFilters"] == [{"texto": "deserção", "data_inicio": "2026-01-01",
                                   "data_fim": "2026-06-30"}]
    assert (c["order"], c["pageSize"], c["searchAfterValue"]) == ("asc", 20, "1791409545")


@pytest.mark.parametrize("por_pagina", [1, 2, 5, 25, 50, 100])
def test_por_pagina_fora_de_10_e_20_e_barrado(por_pagina):
    # Medido: o gateway responde 403 de corpo vazio a esses valores.
    with pytest.raises(ValueError, match="só aceita 10 ou 20"):
        P.montar_corpo("deserção", ids_orgaos=None, por_pagina=por_pagina)


@pytest.mark.parametrize("kw", [{"ordem": "relevancia"}, {"cursor": "abc"},
                                {"data_inicio": "2026/01/01"}])
def test_parametros_invalidos(kw):
    with pytest.raises(ValueError):
        P.montar_corpo("deserção", ids_orgaos=None, **kw)


def test_corpo_por_processo():
    c = P.corpo_por_processo("7000670-85.2026.7.01.0001")
    assert c["searchFilters"] == [{"texto": '"7000670-85.2026.7.01.0001"'}]
    assert c["pageSize"] in P.POR_PAGINA_ACEITOS
    with pytest.raises(ValueError, match="número de processo"):
        P.corpo_por_processo("70006708520267010001")


# ---------------- órgãos ----------------

def test_tabela_tem_as_19_auditorias_da_jmu():
    tabela = P.carregar_orgaos()
    jmu = [o for o in tabela["orgaos"] if o["tribunal"] == P.JMU]
    assert len(jmu) == 19
    assert tabela["tribunais"][str(P.JMU)] == "Justiça Militar da União"


def test_padrao_e_so_a_jmu():
    assert len(P.resolver_orgaos()) == 19


def test_todas_e_sem_filtro():
    assert P.resolver_orgaos("todas") is None


def test_estadual_exclui_a_jmu():
    ids = P.resolver_orgaos("estadual")
    jmu = set(P.resolver_orgaos("JMU"))
    assert ids and not set(ids) & jmu


@pytest.mark.parametrize("orgao, n", [
    ("1ª Auditoria da 1ª CJM", 1),
    ("1a auditoria da 1a cjm", 1),
    ("1ª CJM", 4),
    ("11ª CJM", 2),
])
def test_orgao_por_trecho_do_nome(orgao, n):
    assert len(P.resolver_orgaos("JMU", orgao)) == n


def test_1a_cjm_nao_casa_a_11a():
    ids_1 = set(P.resolver_orgaos("JMU", "1ª CJM"))
    assert not ids_1 & set(P.resolver_orgaos("JMU", "11ª CJM"))


def test_orgao_1a_auditoria_da_1a_cjm_e_o_id_medido():
    assert P.resolver_orgaos("JMU", "1ª Auditoria da 1ª CJM") == [32693]


def test_orgao_que_nao_casa_e_erro():
    with pytest.raises(ValueError, match="nenhum órgão casa"):
        P.resolver_orgaos("JMU", "Vara Federal")


def test_justica_invalida():
    with pytest.raises(ValueError, match="justica inválida"):
        P.resolver_orgaos("federal")


# ---------------- resposta ----------------

def test_resposta_parseada():
    total, docs = P.parsear_resposta(_fixture("busca_3.json"))
    assert total == 45 and len(docs) == 3
    d = docs[0]
    assert d.num_processo == "7000001-11.2026.7.01.0001"
    assert d.orgao == "3ª AUDITORIA DA 1ª CJM"
    assert d.tribunal == "STM"
    assert d.timestamp > 0
    assert d.texto.startswith("PODER JUDICIÁRIO")


def test_zero_e_zero():
    assert P.parsear_resposta(_fixture("busca_zero.json")) == (0, [])


@pytest.mark.parametrize("dados", [{}, {"sentencas": []}, [], {"total": 3}])
def test_resposta_fora_do_contrato(dados):
    with pytest.raises(ValueError, match="fora do contrato"):
        P.parsear_resposta(dados)


# ---------------- trecho ----------------

def test_trecho_em_torno_do_termo_sem_acento_nem_caixa():
    _, docs = P.parsear_resposta(_fixture("busca_3.json"))
    t = P.trecho(docs[0].texto, ["ESTADO DE NECESSIDADE"], 200)
    assert "estado\nde necessidade" in t or "estado de necessidade" in t
    assert t.startswith("[...] ")


def test_trecho_sem_ocorrencia_e_o_comeco():
    t = P.trecho("Começo do texto. " * 100, ["inexistente"], 100)
    assert t.startswith("Começo do texto.") and t.endswith("[...]")


def test_trecho_preserva_o_texto():
    texto = "Á" * 50 + " deserção " + "É" * 50
    t = P.trecho(texto, ["desercao"], 400)
    assert t == texto
