"""Glossário e expansão de consulta pt → es/en/fr.

O teste que importa é `test_consulta_em_portugues_acha_paragrafo_em_espanhol`:
é a razão de a camada existir. O acervo é esmagadoramente espanhol (597 de 598
casos contenciosos linkam espanhol e ZERO linkam português), e sem expansão uma
consulta em português sobre saúde não acharia os dois leading cases do eixo.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "corteidh-jurisprudencia"
if str(MODULO) not in sys.path:
    sys.path.insert(0, str(MODULO))

import glossario_dados  # noqa: E402
import indice  # noqa: E402
from extrator_paragrafos import Paragrafo  # noqa: E402


@pytest.fixture()
def con():
    c = indice.abrir(":memory:")
    indice.criar_schema(c)
    yield c
    c.close()


@pytest.fixture()
def semeado(con):
    indice.semear_glossario(con)
    return con


# --- dados e semeadura ------------------------------------------------------

def test_dados_integros():
    """Tupla de 4 campos, sem duplicata e sem campo vazio — se qualquer uma
    dessas falhar, a expansão passa a montar consulta com termo vazio."""
    termos = glossario_dados.TERMOS
    assert termos, "glossário vazio"
    assert {len(t) for t in termos} == {4}
    pts = [t[0] for t in termos]
    assert len(pts) == len(set(pts)), "chave pt duplicada"
    assert all(all(c and c.strip() for c in t) for t in termos)


def test_semear_e_idempotente(con):
    n1 = indice.semear_glossario(con)
    total1 = con.execute("SELECT COUNT(*) FROM glossario").fetchone()[0]
    n2 = indice.semear_glossario(con)
    total2 = con.execute("SELECT COUNT(*) FROM glossario").fetchone()[0]
    assert n1 == n2 == len(glossario_dados.TERMOS)
    assert total1 == total2 == len(glossario_dados.TERMOS)


def test_fonte_vem_do_mapa_medido_nunca_inventada(semeado):
    """`fonte` é medida por `verificar_glossario_cadh.py` e versionada.

    Duas asserções, e a segunda é a que importa: nenhuma linha pode dizer
    `CADH` sem constar do mapa medido. Procedência afirmada sem medição é o
    defeito exato que o campo existe para impedir.
    """
    arq = MODULO / "glossario_fonte.json"
    assert arq.exists(), "o mapa de procedência medida não está versionado"
    medido = json.loads(arq.read_text(encoding="utf-8"))

    for pt, fonte in semeado.execute("SELECT pt, fonte FROM glossario"):
        assert fonte, f"{pt} sem procedência"
        if fonte != "curadoria":
            assert medido.get(pt) == fonte, (
                f"{pt} diz {fonte!r} e o mapa medido diz {medido.get(pt)!r}")


def test_fonte_desconhecida_cai_em_curadoria(con):
    """Termo ausente do mapa medido entra como curadoria, não como CADH."""
    indice.semear_glossario(
        con, dados=(("termo inventado", "termo inventado es",
                     "made up term", "terme inventé"),), fontes={})
    fonte = con.execute(
        "SELECT fonte FROM glossario WHERE pt = 'termo inventado'").fetchone()[0]
    assert fonte == "curadoria"


# --- expansão ---------------------------------------------------------------

def test_expande_termo_simples(semeado):
    expandida, expansoes = indice.expandir_consulta(semeado, "saúde")
    assert "salud" in expandida
    assert "health" in expandida
    assert "santé" in expandida
    assert expandida.startswith("(") and "OR" in expandida
    assert len(expansoes) == 1
    assert expansoes[0]["termo"] == "saúde"
    assert expansoes[0]["es"] == "salud"


def test_expande_sem_acento_no_pedido(semeado):
    """Quem digita 'saude' é o caso comum; não expandir seria falhar em
    silêncio justamente no uso normal."""
    expandida, expansoes = indice.expandir_consulta(semeado, "saude")
    assert "salud" in expandida
    assert len(expansoes) == 1


def test_expande_maiuscula(semeado):
    expandida, _ = indice.expandir_consulta(semeado, "SAÚDE")
    assert "salud" in expandida


def test_frase_casa_antes_da_palavra(semeado):
    """'acesso à justiça' é entrada do glossário e tem de casar INTEIRA.

    Casando palavra a palavra, 'acesso' e 'justiça' expandiriam soltos e a
    frase — que é o termo de arte — nunca seria consultada.
    """
    expandida, expansoes = indice.expandir_consulta(semeado, "acesso à justiça")
    assert len(expansoes) == 1, expansoes
    assert expansoes[0]["termo"] == "acesso à justiça"
    assert "acceso a la justicia" in expandida
    assert "access to justice" in expandida


def test_frase_vence_palavra_CONTIDA_nela(semeado):
    """Discrimina de verdade a ordem da janela — e o teste acima NÃO discrimina.

    Medido por mutação em 18/09/2026: invertendo a janela para tentar a palavra
    antes da frase, `test_frase_casa_antes_da_palavra` seguia VERDE, porque
    "acesso" isolado não é entrada do glossário e a frase acabava casando de
    qualquer modo. O caso que separa é aquele em que a palavra inicial TAMBÉM é
    entrada: "direito" e "direito à saúde" são ambas.

    Com a janela certa sai UMA expansão, a da frase, e a busca procura
    "derecho a la salud". Com a janela invertida saem DUAS — "direito" e
    "saúde" —, e a busca procura "derecho" e "salud" soltos, que casa qualquer
    parágrafo que fale de direito e qualquer um que fale de saúde.
    """
    assert semeado.execute(
        "SELECT 1 FROM glossario WHERE pt = 'direito'").fetchone()
    assert semeado.execute(
        "SELECT 1 FROM glossario WHERE pt = 'direito à saúde'").fetchone()

    expandida, expansoes = indice.expandir_consulta(semeado, "direito à saúde")
    assert [e["termo"] for e in expansoes] == ["direito à saúde"], expansoes
    assert "derecho a la salud" in expandida
    assert "right to health" in expandida

    # Mesmo par pelo outro eixo: 'prisão' e 'prisão preventiva'.
    expandida2, expansoes2 = indice.expandir_consulta(semeado, "prisão preventiva")
    assert [e["termo"] for e in expansoes2] == ["prisão preventiva"], expansoes2
    assert "pretrial detention" in expandida2


def test_frase_longa_casa(semeado):
    """A maior entrada do glossário tem 6 palavras; a janela tem de alcançá-la."""
    consulta = "pessoa em situação migratória irregular"
    expandida, expansoes = indice.expandir_consulta(semeado, consulta)
    assert len(expansoes) == 1, expansoes
    assert "persona en situación migratoria irregular" in expandida


def test_termo_igual_em_todas_as_linguas_nao_declara_expansao(semeado):
    """'tortura' é igual em pt e es; declarar expansão sugeriria que a busca
    fez algo que não fez."""
    expandida, expansoes = indice.expandir_consulta(semeado, "tortura")
    # Há 'torture' em en/fr, então ESTE termo expande. O caso sem expansão é
    # o de par idêntico em TODAS as línguas.
    assert any(e["termo"] == "tortura" for e in expansoes)
    assert "torture" in expandida

    indice.semear_glossario(
        semeado, dados=(("quilombola", "quilombola", "quilombola", "quilombola"),),
        fontes={})
    expandida2, expansoes2 = indice.expandir_consulta(semeado, "quilombola")
    assert expansoes2 == [], "par idêntico não tem o que expandir"
    assert expandida2.strip() == "quilombola"


def test_termo_fora_do_glossario_passa_intacto(semeado):
    expandida, expansoes = indice.expandir_consulta(semeado, "xyzabc")
    assert expandida == "xyzabc"
    assert expansoes == []


def test_glossario_vazio_nao_expande_nem_estoura(con):
    expandida, expansoes = indice.expandir_consulta(con, "saúde")
    assert expandida == "saúde"
    assert expansoes == []


def test_consulta_vazia(semeado):
    for vazia in ("", "   "):
        expandida, expansoes = indice.expandir_consulta(semeado, vazia)
        assert expansoes == []


def test_expandida_sobrevive_ao_para_fts(semeado):
    """CONTRATO: a consulta expandida vai para `_para_fts` e a busca não pode
    estourar. Sintaxe de OR e parêntese gerada aqui tem de atravessar."""
    for consulta in ("saúde", "acesso à justiça", "saúde OR prisão",
                     "art. 5 saúde", "Vs. Brasil saúde", "prisão, tortura"):
        expandida, _ = indice.expandir_consulta(semeado, consulta)
        padrao = indice._para_fts(expandida)
        # Não basta não estourar na conversão: tem de ser MATCH válido.
        semeado.execute(
            "SELECT COUNT(*) FROM paragrafo_fts WHERE paragrafo_fts MATCH ?",
            (padrao,)).fetchone()


# --- o teste que justifica a camada ----------------------------------------

def test_consulta_em_portugues_acha_paragrafo_em_espanhol(semeado):
    """A razão de existir: parágrafo em ESPANHOL, consulta em PORTUGUÊS.

    Sem expansão, este teste falha — e foi assim que se mediu que ele morde.
    """
    doc = indice.inserir_documento(
        semeado, serie="C", numero=349, tipo="CC",
        caso="Poblete Vilches e outros Vs. Chile", estado="Chile",
        data="2018-03-08", url_esp="https://exemplo/seriec_349_esp.pdf")
    indice.inserir_paragrafos(semeado, doc, "esp", [
        Paragrafo(118, "El derecho a la salud es un derecho protegido por el "
                       "artículo 26 de la Convención Americana."),
    ])

    # Controle: sem expansão, a consulta em português NÃO acha.
    crua = indice.buscar(semeado, consulta="saúde")
    assert crua == [], "o controle falhou: achou sem expansão, o teste não mede"

    expandida, expansoes = indice.expandir_consulta(semeado, "saúde")
    achados = indice.buscar(semeado, consulta=expandida)
    assert achados, "a expansão não achou o parágrafo em espanhol"
    assert achados[0]["caso"] == "Poblete Vilches e outros Vs. Chile"
    assert achados[0]["idioma"] == "esp"
    assert achados[0]["exige_traducao"] is True
    assert any(e["es"] == "salud" for e in expansoes)


def test_migracao_acrescenta_fonte_a_banco_legado():
    """Banco criado antes da coluna: `CREATE TABLE IF NOT EXISTS` não a
    acrescenta, e o INSERT morreria com `no such column`."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE glossario (pt TEXT PRIMARY KEY, es TEXT,"
              " en TEXT, fr TEXT)")
    c.execute("INSERT INTO glossario VALUES ('saúde','salud','health','santé')")
    antes = {r["name"] for r in c.execute("PRAGMA table_info(glossario)")}
    assert "fonte" not in antes

    feitas = indice._migrar(c)
    depois = {r["name"] for r in c.execute("PRAGMA table_info(glossario)")}
    assert "fonte" in depois
    assert "glossario.fonte" in feitas
    # A linha preexistente ganha a procedência conservadora, não 'CADH'.
    assert c.execute("SELECT fonte FROM glossario").fetchone()[0] == "curadoria"

    # Idempotente: rodar de novo não tenta acrescentar outra vez.
    assert indice._migrar(c) == []
    c.close()


# --- avisos na saída (spec §6.1.1) -----------------------------------------
#
# As duas declarações existem para que zero resultado nunca saia ambíguo:
# `<expansao_consulta>` diz o que a busca de fato procurou, e `<aviso_idioma>`
# diz quantos documentos do recorte não têm português. Sem o segundo, "nada
# encontrado" se lê como ausência de precedente quando pode ser barreira de
# língua — confusão que muda a tese da peça.

import xml.etree.ElementTree as ET  # noqa: E402

import server  # noqa: E402


def _doc(con, *, caso, numero, idioma, texto, estado="Chile", par=10):
    url = {f"url_{idioma}": f"https://exemplo/seriec_{numero}_{idioma}.pdf"}
    doc = indice.inserir_documento(
        con, serie="C", numero=numero, tipo="CC", caso=caso, estado=estado,
        data="2018-03-08", **url)
    indice.inserir_paragrafos(con, doc, idioma, [Paragrafo(par, texto)])
    return doc


def test_cobertura_conta_com_e_sem_portugues(semeado):
    _doc(semeado, caso="Só espanhol Vs. Chile", numero=349, idioma="esp",
         texto="El derecho a la salud.")
    _doc(semeado, caso="Tem português Vs. Brasil", numero=149, idioma="por",
         texto="O direito à saúde.", estado="Brasil")

    tudo = server._cobertura_de_idioma(semeado)
    assert tudo == {"total": 2, "com_por": 1, "sem_por": 1}

    # O filtro tem de valer, senão o aviso fala de um recorte que não é o
    # consultado — e o número perde o sentido.
    br = server._cobertura_de_idioma(semeado, estado="Brasil")
    assert br == {"total": 1, "com_por": 1, "sem_por": 0}


def test_cobertura_ignora_documento_sem_paragrafo(semeado):
    """Documento indexado só por metadado (SS) não entra na conta: ele não
    tem texto a casar, e contá-lo inflaria o denominador do aviso."""
    indice.inserir_documento(
        semeado, serie=None, numero=None, tipo="SS",
        caso="Supervisão Vs. Chile", estado="Chile", data="2020-01-01")
    assert server._cobertura_de_idioma(semeado)["total"] == 0


def test_saida_com_avisos_E_XML_BEM_FORMADO(semeado):
    """Raiz ÚNICA. Os avisos entram DENTRO de `<resultados>`.

    Anexados como irmãos, produziriam documento de duas raízes — malformado,
    ainda que legível por um modelo. Este teste faz parse de verdade; nenhum
    teste anterior do corteidh parseava a saída, então a quebra passaria.
    """
    _doc(semeado, caso="Poblete Vilches Vs. Chile", numero=349, idioma="esp",
         texto="El derecho a la salud es protegido por el artículo 26.")

    expandida, expansoes = indice.expandir_consulta(semeado, "saúde")
    linhas = indice.buscar(semeado, consulta=expandida)
    cobertura = server._cobertura_de_idioma(semeado)
    xml = server.formatar_resultados_xml(
        [server._montar_resultado(l, 600) for l in linhas])
    final = server._com_avisos(xml, consulta="saúde", expansoes=expansoes,
                               linhas=linhas, cobertura=cobertura)

    raiz = ET.fromstring(final)          # estoura se houver duas raízes
    assert raiz.tag == "resultados"
    assert raiz.find("expansao_consulta") is not None
    assert raiz.find("aviso_idioma") is not None


def test_aviso_idioma_critico_quando_nada_achado(semeado):
    _doc(semeado, caso="Poblete Vilches Vs. Chile", numero=349, idioma="esp",
         texto="El derecho a la salud.")
    cobertura = server._cobertura_de_idioma(semeado)
    vazio = server.formatar_resultados_xml([])
    final = server._com_avisos(vazio, consulta="xyzabc", expansoes=[],
                               linhas=[], cobertura=cobertura)
    raiz = ET.fromstring(final)
    aviso = raiz.find("aviso_idioma")
    assert aviso is not None
    assert aviso.get("nivel") == "critico"
    assert "NAO SIGNIFICA AUSENCIA DE PRECEDENTE" in (aviso.text or "")


def test_aviso_idioma_informativo_quando_houve_resultado(semeado):
    _doc(semeado, caso="Poblete Vilches Vs. Chile", numero=349, idioma="esp",
         texto="El derecho a la salud.")
    expandida, expansoes = indice.expandir_consulta(semeado, "saúde")
    linhas = indice.buscar(semeado, consulta=expandida)
    assert linhas
    final = server._com_avisos(
        server.formatar_resultados_xml(
            [server._montar_resultado(l, 600) for l in linhas]),
        consulta="saúde", expansoes=expansoes, linhas=linhas,
        cobertura=server._cobertura_de_idioma(semeado))
    aviso = ET.fromstring(final).find("aviso_idioma")
    assert aviso.get("nivel") == "informativo"
    assert "NAO SIGNIFICA AUSENCIA" not in (aviso.text or "")


def test_sem_aviso_quando_todo_o_recorte_tem_portugues(semeado):
    """Aviso que aparece sempre é aviso que ninguém lê."""
    _doc(semeado, caso="Ximenes Lopes Vs. Brasil", numero=149, idioma="por",
         texto="O direito à saúde.", estado="Brasil")
    final = server._com_avisos(
        server.formatar_resultados_xml([]), consulta="saúde", expansoes=[],
        linhas=[], cobertura=server._cobertura_de_idioma(semeado))
    assert ET.fromstring(final).find("aviso_idioma") is None


def test_expansao_declara_a_procedencia_do_par(semeado):
    """Termo `curadoria` tem de sair marcado: o Defensor precisa saber que o
    par não foi confirmado no texto oficial da Convenção."""
    expandida, expansoes = indice.expandir_consulta(semeado, "saúde mental")
    final = server._com_avisos(
        server.formatar_resultados_xml([]), consulta="saúde mental",
        expansoes=expansoes, linhas=[], cobertura={"total": 0, "com_por": 0,
                                                   "sem_por": 0})
    termo = ET.fromstring(final).find("expansao_consulta/termo")
    assert termo is not None
    assert termo.get("pt") == "saúde mental"
    assert termo.get("fonte")


def test_sem_expansao_nao_emite_bloco(semeado):
    final = server._com_avisos(
        server.formatar_resultados_xml([]), consulta="xyzabc", expansoes=[],
        linhas=[], cobertura={"total": 0, "com_por": 0, "sem_por": 0})
    assert ET.fromstring(final).find("expansao_consulta") is None


# --- a LIGAÇÃO no servidor, não só a composição das funções ----------------
#
# Refinamento vindo de sessão par (claude-d7) em 18/09/2026, e que se aplica
# aqui: importar a produção NÃO basta quando se importa uma camada INTERNA cuja
# pré-condição vive no chamador. Os testes acima exercitam
# `expandir_consulta` + `indice.buscar` em sequência, o que prova que as duas
# funções COMPÕEM — e não prova que o servidor as chame. Parasse
# `_buscar_sync` de expandir, todos eles seguiriam verdes e a busca voltaria a
# não achar texto em espanhol, em silêncio.


def test_o_SERVIDOR_expande_de_fato(semeado, tmp_path, monkeypatch):
    """Pela porta do servidor (`_buscar_sync`), não pelas funções soltas."""
    from extrator_paragrafos import Paragrafo

    caminho = tmp_path / "corteidh.db"
    c = indice.abrir(caminho)
    indice.criar_schema(c)
    indice.semear_glossario(c)
    doc = indice.inserir_documento(
        c, serie="C", numero=349, tipo="CC",
        caso="Poblete Vilches y otros Vs. Chile", estado="Chile",
        data="2018-03-08", url_esp="https://exemplo/seriec_349_esp.pdf")
    indice.inserir_paragrafos(c, doc, "esp", [
        Paragrafo(118, "El derecho a la salud es protegido por el artículo 26"
                       " de la Convención Americana."),
    ])
    c.close()

    monkeypatch.setattr(server, "CAMINHO_BANCO", caminho)

    # Consulta em PORTUGUÊS sobre documento em ESPANHOL, pela porta real.
    xml = server._buscar_sync(consulta="saúde", max_resultados=5)
    raiz = ET.fromstring(xml)
    assert int(raiz.get("total")) >= 1, (
        "o servidor não expandiu: busca em português não achou texto espanhol")
    assert raiz.find("expansao_consulta") is not None, (
        "expandiu sem DECLARAR — busca que procurou outra coisa tem de dizê-lo")

    # Controle da premissa: sem expansão, a mesma consulta não acharia nada.
    # Sem este controle o teste passaria também num índice que casasse
    # "saúde" com "salud" por outro mecanismo, e não mediria a expansão.
    c2 = indice.abrir(caminho)
    try:
        assert indice.buscar(c2, consulta="saúde") == [], (
            "o controle falhou: a busca CRUA já achava, o teste não mede")
    finally:
        c2.close()
