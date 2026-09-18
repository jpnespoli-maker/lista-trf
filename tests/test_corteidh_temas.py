"""Mapa temático semeado pelos Cadernos de Jurisprudência (Fase 3).

O que estes testes protegem é a razão de a fase existir: o eixo temático vem da
curadoria da PRÓPRIA CORTE — os Cadernos que ela publica por tema —, e não de
heurística minha sobre o acervo. Força relativa de precedente não se extrai por
regex, e um mapa automático seria mapa inventado com aparência de curadoria.

Daí as duas travas centrais: `fonte` é OBRIGATÓRIA em toda associação, e o
"parágrafo-chave" NÃO existe.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "corteidh-jurisprudencia"
if str(MODULO) not in sys.path:
    sys.path.insert(0, str(MODULO))

import cadernos  # noqa: E402
import indice  # noqa: E402
from extrator_paragrafos import Paragrafo  # noqa: E402


@pytest.fixture()
def con():
    c = indice.abrir(":memory:")
    indice.criar_schema(c)
    yield c
    c.close()


def _doc(con, numero, caso, *, serie="C", pars=1):
    d = indice.inserir_documento(
        con, serie=serie, numero=numero, tipo="CC", caso=caso,
        estado="Brasil", data="2020-01-01")
    indice.inserir_paragrafos(
        con, d, "por", [Paragrafo(i, f"texto {i}") for i in range(1, pars + 1)])
    return d


# --- extração das citações -------------------------------------------------

def test_extrai_citacao_com_paragrafo():
    """Os Cadernos citam por AUTUAÇÃO, não por nome — e isso é facilidade.

    A autuação é a identidade do julgado neste índice, então o nome se resolve
    no próprio banco: não há casamento de nomes, nem risco de errar por grafia.
    """
    t = "conforme a Corte assentou (Série C No. 353, par. 278) e também"
    assert cadernos.extrair_citacoes(t) == [("C", 353, 278)]


@pytest.mark.parametrize("texto,esperado", [
    ("Serie C No. 149, párr. 89", ("C", 149, 89)),
    ("Série C Nº 149, par. 89", ("C", 149, 89)),
    ("Serie C N° 149, parr. 89", ("C", 149, 89)),
    ("Serie A No. 18, párrs. 112", ("A", 18, 112)),
    ("Série C No 435, § 134", ("C", 435, 134)),
])
def test_tolera_as_grafias_dos_cadernos(texto, esperado):
    """Espanhol e português, com e sem acento, `No.`/`Nº`/`N°`, `párr.`/`par.`/
    `§`. Grafia que o extrator não conhece vira citação PERDIDA, e o eixo
    perde um leading case em silêncio."""
    assert cadernos.extrair_citacoes(texto) == [esperado]


def test_repeticao_e_PRESERVADA():
    """É a contagem que mede quanto a Corte voltou àquela passagem no eixo."""
    t = "(Série C No. 353, par. 278) ... depois (Série C No. 353, par. 278)"
    assert len(cadernos.extrair_citacoes(t)) == 2


def test_mencao_SEM_paragrafo_conta_como_presenca_no_eixo():
    """O Caderno às vezes trata o julgado sem apontar passagem.

    Omiti-lo faria o mapa dizer que a Corte não o associou ao tema — asserção
    falsa por silêncio.
    """
    t = "o Caso tratado em Série C No. 200 é paradigma do eixo"
    assert cadernos.extrair_citacoes(t) == []
    assert cadernos.extrair_mencoes(t) == [("C", 200)]

    c = cadernos.consolidar(t)
    assert c[("C", 200)]["mencoes"] == 1
    assert dict(c[("C", 200)]["paragrafos"]) == {}


def test_consolidar_separa_mencao_de_passagem():
    t = ("Série C No. 149, par. 89. Mais adiante, Série C No. 149, par. 90, "
         "e ainda Série C No. 149, par. 89. Também Série C No. 200.")
    c = cadernos.consolidar(t)
    assert dict(c[("C", 149)]["paragrafos"]) == {89: 2, 90: 1}
    assert c[("C", 149)]["mencoes"] == 3
    assert c[("C", 200)]["mencoes"] == 1


# --- procedência obrigatória ----------------------------------------------

def test_tema_SEM_procedencia_e_RECUSADO(con):
    """A trava central da Fase 3.

    Associação sem procedência é palpite com aparência de curadoria. O mapa só
    vale porque cada linha diz de onde veio.
    """
    d = _doc(con, 149, "Ximenes Lopes Vs. Brasil")
    for vazia in ("", "   ", None):
        with pytest.raises(ValueError, match="procedência"):
            indice.inserir_tema(con, d, "saúde", fonte=vazia)
    assert con.execute("SELECT COUNT(*) FROM tema").fetchone()[0] == 0


def test_a_fonte_carrega_o_CADERNO_e_o_titulo_ORIGINAL():
    """O rótulo em português é curadoria deste projeto; o título é da Corte.

    Gravar a tradução como procedência faria o rótulo se passar por designação
    oficial.
    """
    c36 = next(c for c in cadernos.CADERNOS if c["numero"] == 36)
    f = cadernos.fonte_do_caderno(c36)
    assert "36" in f
    assert "Jurisprudencia sobre Brasil" in f, "o título original tem de estar"
    assert c36["rotulo"] == "Brasil"
    assert "rotulo" not in f


# --- o "parágrafo-chave" que NÃO existe -----------------------------------

def test_guarda_TODOS_os_paragrafos_e_nao_um_eleito(con):
    """REFUTA a suposição da spec §6.3, e a refutação é medida.

    A spec pedia "o parágrafo-chave de cada". Medido no Caderno 36 sobre 100
    citações: apenas 2 dos 11 documentos têm parágrafo dominante; o C-353 é
    citado 23 vezes em 20 parágrafos DISTINTOS. Eleger um seria arbítrio com
    aparência de curadoria — quem lesse "parágrafo-chave: 278" entenderia que a
    Corte o destacou, e ele foi citado duas vezes entre vinte e três.
    """
    d = _doc(con, 353, "Herzog e outros Vs. Brasil", pars=300)
    indice.inserir_tema(con, d, "Brasil", fonte="Cuadernillo 36",
                        paragrafos={278: 2, 286: 2, 251: 2, 49: 1},
                        n_citacoes=23)

    mapa = indice.mapa_tematico(con, "Brasil")
    assert len(mapa) == 1
    pars = mapa[0]["paragrafos_citados"]
    assert len(pars) == 4, "todos os parágrafos citados têm de estar"
    assert {p["paragrafo"] for p in pars} == {278, 286, 251, 49}
    # A contagem vai junto, para que a ordem não se leia como juízo de força.
    assert pars[0]["vezes"] == 2
    assert mapa[0]["n_citacoes"] == 23
    # E NÃO existe campo de parágrafo eleito.
    assert "paragrafo_chave" not in mapa[0]


def test_ordem_e_por_CITACAO_MEDIDA_e_o_numero_vai_junto(con):
    """A ordem é dado do Caderno, não score calculado por mim — e o número
    acompanha para que ninguém tome a ordem por juízo de força."""
    a = _doc(con, 353, "Herzog e outros Vs. Brasil")
    b = _doc(con, 161, "Nogueira de Carvalho Vs. Brasil")
    indice.inserir_tema(con, a, "Brasil", fonte="Cuadernillo 36", n_citacoes=23)
    indice.inserir_tema(con, b, "Brasil", fonte="Cuadernillo 36", n_citacoes=1)

    mapa = indice.mapa_tematico(con, "Brasil")
    assert [m["numero"] for m in mapa] == [353, 161]
    assert [m["n_citacoes"] for m in mapa] == [23, 1]


# --- consulta --------------------------------------------------------------

def test_mapa_tolera_acento_e_caixa(con):
    d = _doc(con, 346, "Povo Indígena Xucuru Vs. Brasil")
    indice.inserir_tema(con, d, "povos indígenas", fonte="Cuadernillo 11")
    for escrito in ("povos indígenas", "povos indigenas", "POVOS INDIGENAS"):
        assert indice.mapa_tematico(con, escrito), escrito


def test_temas_disponiveis_permite_responder_sem_VAZIO(con):
    """Pedir eixo inexistente tem de poder devolver a LISTA.

    Vazio aqui se leria como "a Corte não tratou disso", que é o modo de falha
    que este subsistema combate.
    """
    d = _doc(con, 149, "Ximenes Lopes Vs. Brasil")
    indice.inserir_tema(con, d, "Brasil", fonte="Cuadernillo 36", n_citacoes=5)

    assert indice.mapa_tematico(con, "saúde") == []
    disp = indice.temas_disponiveis(con)
    assert [t["tema"] for t in disp] == ["Brasil"]
    assert disp[0]["n_documentos"] == 1


def test_reseamear_ATUALIZA_e_nao_duplica(con):
    d = _doc(con, 149, "Ximenes Lopes Vs. Brasil")
    indice.inserir_tema(con, d, "Brasil", fonte="Cuadernillo 36",
                        paragrafos={89: 1}, n_citacoes=1)
    indice.inserir_tema(con, d, "Brasil", fonte="Cuadernillo 36",
                        paragrafos={89: 3, 90: 1}, n_citacoes=4)

    assert con.execute("SELECT COUNT(*) FROM tema").fetchone()[0] == 1
    mapa = indice.mapa_tematico(con, "Brasil")
    assert mapa[0]["n_citacoes"] == 4
    assert {p["paragrafo"]: p["vezes"] for p in mapa[0]["paragrafos_citados"]} \
        == {89: 3, 90: 1}


# --- catálogo dos Cadernos -------------------------------------------------

def test_catalogo_integro():
    assert len(cadernos.CADERNOS) >= 20
    numeros = [c["numero"] for c in cadernos.CADERNOS]
    assert len(numeros) == len(set(numeros)), "número de caderno repetido"
    for c in cadernos.CADERNOS:
        assert c["arquivo"].endswith(".pdf")
        assert c["titulo"] and c["rotulo"]
        assert cadernos.url_do_caderno(c).startswith("https://")


def test_o_caderno_do_BRASIL_esta_em_PORTUGUES():
    """O mais relevante para a DPU, e a Corte o publicou traduzido."""
    c36 = next(c for c in cadernos.CADERNOS if c["numero"] == 36)
    assert "port" in c36["arquivo"]


# --- a tool, pela ENTRADA REAL --------------------------------------------
#
# Pela porta que quem redige chama (`server._mapa_sync`), e não pelas funções
# soltas do índice: composição de funções não prova que o servidor as ligue.

import xml.etree.ElementTree as ET  # noqa: E402

import server  # noqa: E402


def test_tool_devolve_mapa_bem_formado(tmp_path, monkeypatch):
    caminho = tmp_path / "corteidh.db"
    c = indice.abrir(caminho)
    indice.criar_schema(c)
    d = indice.inserir_documento(
        c, serie="C", numero=353, tipo="CC", caso="Herzog e outros Vs. Brasil",
        estado="Brasil", data="2018-03-15",
        url_por="https://exemplo/seriec_353_por.pdf")
    indice.inserir_paragrafos(c, d, "por", [Paragrafo(1, "t")])
    indice.inserir_tema(c, d, "Brasil",
                        fonte="Cuadernillo de Jurisprudencia 36 — "
                              "Jurisprudencia sobre Brasil",
                        paragrafos={278: 2, 49: 1}, n_citacoes=23)
    c.commit()          # a convenção do módulo é: o CHAMADOR commita
    c.close()
    monkeypatch.setattr(server, "CAMINHO_BANCO", caminho)

    raiz = ET.fromstring(server._mapa_sync("Brasil"))
    assert raiz.tag == "mapa_tematico"
    caso = raiz.find("caso")
    assert caso.findtext("nome") == "Herzog e outros Vs. Brasil"
    assert caso.findtext("citado_no_caderno_vezes") == "23"
    # TODOS os parágrafos, com a contagem — nunca um eleito.
    pars = caso.findtext("paragrafos_citados")
    assert "278(2x)" in pars and "49" in pars
    # PROCEDÊNCIA visível: é o que separa curadoria de palpite.
    assert "Cuadernillo" in caso.findtext("procedencia")
    # E a citação já vem com o link de conferência.
    assert "Disponível em:" in caso.findtext("citacao_sugerida")


def test_tool_com_eixo_NAO_COMPILADO_devolve_a_LISTA(tmp_path, monkeypatch):
    """Vazio se leria como "a Corte não tratou disso".

    A verdade é outra: ela não publicou COMPILAÇÃO daquele eixo. A diferença
    muda a tese da peça, então a resposta diz qual das duas é.
    """
    caminho = tmp_path / "corteidh.db"
    c = indice.abrir(caminho)
    indice.criar_schema(c)
    d = indice.inserir_documento(
        c, serie="C", numero=149, tipo="CC", caso="X Vs. Brasil",
        estado="Brasil", data="2006-07-04")
    indice.inserir_paragrafos(c, d, "por", [Paragrafo(1, "t")])
    indice.inserir_tema(c, d, "Brasil", fonte="Cuadernillo 36")
    c.commit()
    c.close()
    monkeypatch.setattr(server, "CAMINHO_BANCO", caminho)

    saida = server._mapa_sync("saúde")
    raiz = ET.fromstring(saida)
    assert raiz.tag == "erro"
    assert raiz.get("tipo") == "eixo-nao-compilado"
    assert "NÃO SIGNIFICA QUE A CORTE NÃO TENHA JULGADO" in saida
    assert "Brasil" in saida, "tem de listar os eixos que existem"
    assert "buscar_corteidh" in saida, "tem de apontar a rota alternativa"


def test_NENHUM_arquivo_de_caderno_e_INVENTADO():
    """O nome do PDF vem MEDIDO da página oficial, nunca escrito por padrão.

    Defeito medido em 18/09/2026: a primeira versão do catálogo escrevia
    `cuadernilloN.pdf` por padrão, e **12 dos 24** divergiam — os reais
    carregam o ano (`cuadernillo4_2021.pdf`). Alguns funcionavam por acaso e o
    caderno 11 falhou com "HTTP 200 com corpo de 16.042 bytes". É a mesma
    classe da flag inventada: ou falha barulhento, ou baixa a coisa errada em
    silêncio — e a segunda é pior, porque semearia o eixo com o conteúdo de
    outro documento.
    """
    import json
    arq = MODULO / "cadernos_links.json"
    assert arq.exists(), "a lista medida de links não está versionada"
    links = json.loads(arq.read_text(encoding="utf-8"))

    for c in cadernos.CADERNOS:
        chave = str(c["numero"])
        assert chave in links, f"caderno {c['numero']} sem link medido"
        assert c["arquivo"] in links[chave]["arquivos"], (
            f"caderno {c['numero']}: {c['arquivo']!r} não consta da página "
            f"oficial — {links[chave]['arquivos']}")


def test_prefere_a_versao_PORTUGUESA_quando_existe():
    """Havendo tradução da Corte, é ela que se lê — é a língua da peça."""
    import json
    links = json.loads((MODULO / "cadernos_links.json").read_text(encoding="utf-8"))
    for c in cadernos.CADERNOS:
        disponiveis = links[str(c["numero"])]["arquivos"]
        tem_port = [a for a in disponiveis if "_port" in a.lower()]
        if tem_port:
            assert "_port" in c["arquivo"].lower(), (
                f"caderno {c['numero']} tem versão portuguesa e o catálogo "
                f"aponta para {c['arquivo']!r}")


def test_caderno_sem_link_medido_fica_de_FORA():
    """Inventar o nome é o defeito que este desenho corrige.

    Caderno ausente do catálogo se vê na contagem; nome inventado só se vê
    quando o download falha — ou pior, quando não falha.
    """
    salvo = cadernos._carregar_links
    try:
        cadernos._carregar_links = lambda: {"36": {
            "arquivos": ["cuadernillo36_2022_port1.pdf"],
            "preferido": "cuadernillo36_2022_port1.pdf"}}
        so_um = cadernos._montar_catalogo()
        assert [c["numero"] for c in so_um] == [36]
    finally:
        cadernos._carregar_links = salvo


def test_caderno_de_TERCEIRO_fica_de_fora():
    """A Fase 3 vale por ser curadoria DA CORTE. Caderno feito por outro órgão
    é publicação de terceiro SOBRE a Corte — coisa diferente.

    Medido em 18/09/2026 sobre os 36 Cadernos da página oficial: 34 são da
    Corte; o 27 traz "Elaborado por la Procuraduría de la Administración de
    Panamá" e o 39, a Procuradoria da Bolívia.

    E a medição do conteúdo confirma pelo outro lado: em 321.152 caracteres o
    27 tem ZERO citações de autuação — os 51 `párr.` dele são remissões
    INTERNAS ("supra párr. 11"), porque ele reproduz sentença em vez de
    compilar citações. Semeá-lo criaria um eixo "Panamá" VAZIO, e eixo vazio
    responde "não compilado" a quem perguntar — quando a verdade é "compilado
    por outro órgão, noutro formato".
    """
    numeros = {c["numero"] for c in cadernos.CADERNOS}
    assert 27 not in numeros, "caderno de terceiro não entra no catálogo"
    assert 36 in numeros, "o da Corte sobre o Brasil tem de estar"
