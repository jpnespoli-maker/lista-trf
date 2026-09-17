"""Índice local da Corte IDH — schema, escrita e busca FTS5.

O servidor MCP não vai à rede: ele lê este índice. Portanto o contrato aqui é o
contrato da ferramenta de busca, e o teste que mais importa é o da CASCATA DE
IDIOMA — documento sem versão portuguesa tem de devolver o parágrafo em
espanhol com `exige_traducao` verdadeiro, nunca devolver nada.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "corteidh-jurisprudencia"
if str(MODULO) not in sys.path:
    sys.path.insert(0, str(MODULO))

import indice  # noqa: E402
from extrator_paragrafos import Paragrafo  # noqa: E402


@pytest.fixture()
def con():
    c = indice.abrir(":memory:")
    indice.criar_schema(c)
    yield c
    c.close()


def _ximenes(con):
    doc_id = indice.inserir_documento(
        con, serie="C", numero=149, tipo="CC",
        caso="Ximenes Lopes Vs. Brasil", estado="Brasil", data="2006-07-04",
        etapa="Mérito, Reparações e Custas", tem_por=1,
        url_por="https://www.corteidh.or.cr/docs/casos/articulos/seriec_149_por.pdf",
        url_esp="https://www.corteidh.or.cr/docs/casos/articulos/seriec_149_esp.pdf",
    )
    indice.inserir_paragrafos(con, doc_id, "por", [
        Paragrafo(88, "O Estado tem o dever de regular e fiscalizar."),
        Paragrafo(89, "As pessoas com deficiência mental em instituição de "
                      "saúde estão em situação de especial vulnerabilidade."),
    ])
    return doc_id


def _poblete(con):
    doc_id = indice.inserir_documento(
        con, serie="C", numero=349, tipo="CC",
        caso="Poblete Vilches e outros Vs. Chile", estado="Chile",
        data="2018-03-08", tem_por=0,
        url_esp="https://www.corteidh.or.cr/docs/casos/articulos/seriec_349_esp.pdf",
    )
    indice.inserir_paragrafos(con, doc_id, "esp", [
        Paragrafo(118, "La salud es un derecho humano fundamental e "
                       "indispensable para el ejercicio de los demás."),
    ])
    return doc_id


def test_schema_cria_as_sete_tabelas(con):
    nomes = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    )}
    for t in ("documento", "paragrafo", "artigo_cadh", "tema", "reparacao",
              "recepcao", "glossario"):
        assert t in nomes, t


def test_busca_acha_paragrafo_em_portugues(con):
    _ximenes(con)
    res = indice.buscar(con, consulta="vulnerabilidade")
    assert len(res) == 1
    assert res[0]["paragrafo"] == 89
    assert res[0]["idioma"] == "por"
    assert res[0]["exige_traducao"] is False
    assert res[0]["caso"] == "Ximenes Lopes Vs. Brasil"


def test_documento_sem_portugues_devolve_espanhol_exigindo_traducao(con):
    _poblete(con)
    res = indice.buscar(con, consulta="salud")
    assert len(res) == 1
    assert res[0]["idioma"] == "esp"
    assert res[0]["exige_traducao"] is True


def test_filtro_por_estado(con):
    _ximenes(con)
    _poblete(con)
    assert len(indice.buscar(con, consulta="saúde OR salud", estado="Chile")) == 1
    assert indice.buscar(con, consulta="salud", estado="Brasil") == []


def test_filtro_por_faixa_de_ano(con):
    _ximenes(con)
    _poblete(con)
    res = indice.buscar(con, consulta="salud OR vulnerabilidade",
                        ano_de=2010, ano_ate=2020)
    assert [r["numero"] for r in res] == [349]


def test_filtro_por_artigo_da_cadh(con):
    doc_id = _ximenes(con)
    con.execute("INSERT INTO artigo_cadh (documento_id, artigo, violado) "
                "VALUES (?, ?, 1)", (doc_id, "4"))
    con.commit()
    assert len(indice.buscar(con, consulta="vulnerabilidade", artigo_cadh="4")) == 1
    assert indice.buscar(con, consulta="vulnerabilidade", artigo_cadh="26") == []


def test_busca_sem_resultado_devolve_lista_vazia(con):
    _ximenes(con)
    assert indice.buscar(con, consulta="usucapiao") == []


def test_limite_respeitado(con):
    _ximenes(con)
    assert len(indice.buscar(con, consulta="dever OR vulnerabilidade", limite=1)) == 1


def test_ficha_traz_metadados_e_artigos(con):
    doc_id = _ximenes(con)
    con.execute("INSERT INTO artigo_cadh (documento_id, artigo, violado) "
                "VALUES (?, ?, 1)", (doc_id, "5"))
    con.execute("INSERT INTO reparacao (documento_id, ordem, texto) "
                "VALUES (?, 1, 'publicar a sentença')", (doc_id,))
    con.commit()
    f = indice.ficha(con, caso="Ximenes Lopes")
    assert f["numero"] == 149
    assert f["estado"] == "Brasil"
    assert f["artigos_violados"] == ["5"]
    assert f["reparacoes"] == ["publicar a sentença"]
    assert f["idiomas"] == ["esp", "por"]


def test_ficha_por_numero_de_serie(con):
    _ximenes(con)
    assert indice.ficha(con, caso="C-149")["caso"] == "Ximenes Lopes Vs. Brasil"


def test_ficha_de_caso_inexistente_devolve_none(con):
    assert indice.ficha(con, caso="Caso Que Nao Existe") is None


def test_insercao_de_documento_e_idempotente(con):
    a = _ximenes(con)
    b = _ximenes(con)
    assert a == b
    assert con.execute("SELECT COUNT(*) FROM documento").fetchone()[0] == 1


def test_reindexar_nao_duplica_linha_no_espelho_FTS(con):
    """A tabela `paragrafo` deduplica pela chave primária; `paragrafo_fts` é
    virtual e NÃO tem chave. Sem apagar antes de reinserir, cada reindexação
    duplicaria o trecho no índice de busca — e o `buscar`, que deduplica por
    (documento, parágrafo), ESCONDERIA o sintoma. Defeito silencioso, então
    a asserção é sobre a tabela FTS e não sobre o resultado da busca."""
    _ximenes(con)
    antes = con.execute("SELECT COUNT(*) FROM paragrafo_fts").fetchone()[0]
    _ximenes(con)
    depois = con.execute("SELECT COUNT(*) FROM paragrafo_fts").fetchone()[0]
    assert antes == depois == 2
    assert len(indice.buscar(con, consulta="vulnerabilidade")) == 1


def test_suspeito_e_falso_por_padrao(con):
    _ximenes(con)
    assert indice.buscar(con, consulta="vulnerabilidade")[0]["suspeito"] is False


def test_suspeito_marcado_chega_ao_resultado_da_busca(con):
    """O aviso de possível contaminação tem de sair na busca: quem decide se
    transcreve verbatim é o Defensor, e ele só decide se souber."""
    doc_id = indice.inserir_documento(
        con, serie="C", numero=435, tipo="CC",
        caso="Barbosa de Souza e outros Vs. Brasil", estado="Brasil",
        data="2021-09-07", tem_por=1,
        url_por="https://www.corteidh.or.cr/docs/casos/articulos/seriec_435_por.pdf")
    indice.inserir_paragrafos(con, doc_id, "por", [
        Paragrafo(101, "Texto com palavra deslocada por ordem de leitura."),
        Paragrafo(102, "Texto limpo sem contaminação alguma."),
    ], suspeitos={101})

    r101 = indice.buscar(con, consulta="deslocada")[0]
    r102 = indice.buscar(con, consulta="limpo")[0]
    assert r101["suspeito"] is True
    assert r102["suspeito"] is False


def test_reindexar_atualiza_a_marca_de_suspeito(con):
    """A Tarefa 6 pode medir melhor e desmarcar — a marca não é permanente."""
    doc_id = indice.inserir_documento(
        con, serie="C", numero=1, tipo="CC", caso="X Vs. Brasil",
        estado="Brasil", data="2020-01-01", tem_por=1)
    indice.inserir_paragrafos(con, doc_id, "por",
                              [Paragrafo(7, "trecho qualquer")], suspeitos={7})
    assert indice.buscar(con, consulta="trecho")[0]["suspeito"] is True
    indice.inserir_paragrafos(con, doc_id, "por",
                              [Paragrafo(7, "trecho qualquer")], suspeitos=set())
    assert indice.buscar(con, consulta="trecho")[0]["suspeito"] is False
