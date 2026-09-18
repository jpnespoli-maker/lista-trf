"""Fase 2 — colheita dirigida pelo CATÁLOGO OFICIAL.

O que estes testes protegem é a ideia central da fase: a URL vem do catálogo e
só o SUFIXO DE IDIOMA muda. Montar a URL por molde fixo, como faz a Fase 1,
produziria 404 em dezenas de documentos — e 404 aqui se lê como "documento
inexistente", que é o diagnóstico errado.

As formas que o servidor de fato usa, medidas em 18/09/2026 sobre as 631 URLs:
7 formas distintas nos casos contenciosos (`http`/`https`, com/sem `www`, e 44
com `Seriec_` de S maiúsculo), sufixo numérico extra em 4 pareceres
(`_esp1.pdf`) e a grafia curta `_es.pdf` em 2 pareceres e 2 supervisões.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "corteidh-jurisprudencia"
if str(MODULO) not in sys.path:
    sys.path.insert(0, str(MODULO))

import corteidh_crawler as cc  # noqa: E402
import indice  # noqa: E402


@pytest.fixture()
def con():
    c = indice.abrir(":memory:")
    indice.criar_schema(c)
    yield c
    c.close()


# --- url_do_idioma ---------------------------------------------------------

def test_substitui_o_idioma_preservando_o_resto():
    u = "https://www.corteidh.or.cr/docs/casos/articulos/seriec_149_esp.pdf"
    assert cc.url_do_idioma(u, "por").endswith("seriec_149_por.pdf")


def test_preserva_a_CAIXA_do_nome_do_arquivo():
    """44 das 598 URLs usam `Seriec_` com S maiúsculo, e o servidor
    distingue. Normalizar a caixa produziria 404 em 44 documentos."""
    u = "http://www.corteidh.or.cr/docs/casos/articulos/Seriec_12_esp.pdf"
    saida = cc.url_do_idioma(u, "por")
    assert "Seriec_12_por.pdf" in saida
    assert "seriec_12_por" not in saida


def test_preserva_o_SUFIXO_NUMERICO():
    """4 pareceres são `_espN.pdf`. Trocar só o idioma perderia o `N` e
    apontaria para arquivo diferente."""
    u = "http://www.corteidh.or.cr/docs/opiniones/seriea_21_esp1.pdf"
    assert cc.url_do_idioma(u, "por").endswith("seriea_21_por1.pdf")


def test_normaliza_para_https_com_www():
    """521 das 598 URLs são `http://` — texto claro. A razão é SEGURANÇA;
    nenhum ganho de velocidade é afirmado (a latência foi medida errática e a
    causa não foi apurada)."""
    for u in ("http://corteidh.or.cr/docs/casos/articulos/seriec_1_esp.pdf",
              "http://www.corteidh.or.cr/docs/casos/articulos/seriec_1_esp.pdf",
              "https://corteidh.or.cr/docs/casos/articulos/seriec_1_esp.pdf"):
        saida = cc.url_do_idioma(u, "esp")
        assert saida.startswith("https://www.corteidh.or.cr/"), saida


def test_url_SEM_sufixo_devolve_None():
    """891 das 903 supervisões não têm sufixo de idioma.

    `None` é RESPOSTA. Devolver a própria URL faria a sonda testar o arquivo
    original e reportar que o idioma pedido existe — afirmação falsa.
    """
    u = "http://www.corteidh.or.cr/docs/supervisiones/baena_09_03_26.pdf"
    assert cc.url_do_idioma(u, "por") is None


def test_a_grafia_curta_es_esta_na_cascata():
    """`seriea_30_es.pdf` existe e `_esp.pdf` não.

    Medido na prova de 18/09/2026: sem a variante, aquele parecer falhava nos
    QUATRO idiomas e o relatório dizia "nenhum idioma devolveu PDF" — que se
    lê como documento ausente do servidor, quando ele está lá.
    """
    assert "es" in cc._GRAFIAS["esp"]
    assert "eng" in cc._GRAFIAS["ing"]
    assert "fre" in cc._GRAFIAS["fra"]
    u = "https://corteidh.or.cr/docs/opiniones/seriea_30_es.pdf"
    assert cc.url_do_idioma(u, "es").endswith("seriea_30_es.pdf")


# --- data ------------------------------------------------------------------

@pytest.mark.parametrize("bruta,esperado", [
    ("14 de mayo de 2026", "2026-05-14"),
    ("3 de julio de 2026", "2026-07-03"),
    ("26 de septiembre de 2006", "2006-09-26"),
    ("26 de setiembre de 2006", "2006-09-26"),   # grafia sem 'p'
    ("1 de enero de 2000", "2000-01-01"),
])
def test_data_espanhola_para_iso(bruta, esperado):
    assert cc.iso_da_data_espanhola(bruta) == esperado


@pytest.mark.parametrize("ruim", [None, "", "sem data", "14 de xxxxx de 2026",
                                  "mayo de 2026"])
def test_data_irreconhecivel_devolve_None(ruim):
    """`None` é ignorância declarada, e sobrevive até a citação: o
    `data_por_extenso` devolve "Sentença." em vez de "Sentença de None."."""
    assert cc.iso_da_data_espanhola(ruim) is None


# --- registro --------------------------------------------------------------

def test_registro_compoe_do_titulo():
    bruto = {
        "titulo": "Casos Contenciosos Corte IDH. Caso Ygarza y otros Vs. "
                  "Venezuela. Excepciones Preliminares. Sentencia de 14 de "
                  "mayo de 2026. Serie C No. 595.",
        "url": "http://corteidh.or.cr/docs/casos/articulos/seriec_595_esp.pdf",
        "data": "14 de mayo de 2026", "serie": "C", "numero": 595,
    }
    r = cc.registro_do_catalogo(bruto, "CC")
    assert r["caso"] == "Ygarza y otros Vs. Venezuela"
    assert r["etapa"] == "Excepciones Preliminares"
    assert r["data"] == "2026-05-14"
    assert r["numero"] == 595
    assert r["tipo"] == "CC"


def test_registro_LE_o_estado_do_nome():
    """REVERSÃO DECLARADA, em 18/09/2026, de uma decisão minha do mesmo dia.

    A versão anterior deste teste se chamava `test_registro_NAO_infere_o_estado`
    e exigia `estado is None`, com o argumento de que tirá-lo do sufixo
    "Vs. <país>" seria INFERÊNCIA. O enquadramento estava errado: o Estado
    demandado **está** no nome — "Vs. Venezuela" é a designação que a própria
    Corte dá ao caso —, e o `titulo_catalogo` já parseia esse mesmo trecho para
    isolar o nome. Ler campo estruturado não é inferir.

    O que a decisão errada custava, MEDIDO: o filtro `estado="Brasil"` devolvia
    **12** documentos (só os semeados à mão) com o acervo já contendo outros
    casos brasileiros — *Comunidades Quilombolas de Alcântara*, *Muniz Da
    Silva*. Resultado curto se lê como ausência de precedente, que é o modo de
    falha que este subsistema existe para evitar.

    O guard anterior reprovou esta mudança, e foi ele que forçou a reversão a
    ser declarada em vez de silenciosa — que é exatamente o seu ofício.
    """
    bruto = {"titulo": "Casos Contenciosos Corte IDH. Caso X Vs. Venezuela. "
                       "Fondo. Sentencia de 1 de enero de 2020.",
             "url": "", "data": "1 de enero de 2020", "serie": "C",
             "numero": 1}
    assert cc.registro_do_catalogo(bruto, "CC")["estado"] == "Venezuela"


# --- retomada --------------------------------------------------------------

def test_ja_indexado_exige_PARAGRAFO_para_tipo_com_texto(con):
    """Documento com linha e SEM parágrafo não serve e tem de ser tentado de
    novo — senão uma falha de download viraria "já indexado" para sempre."""
    reg = {"tipo": "CC", "serie": "C", "numero": 1, "caso": "X Vs. Y"}
    assert not cc.ja_indexado(con, reg, com_texto=True)

    indice.inserir_documento(con, serie="C", numero=1, tipo="CC",
                             caso="X Vs. Y", estado=None, data="2020-01-01")
    # Linha existe, n_paragrafos = 0 → NÃO está indexado para efeito de texto.
    assert not cc.ja_indexado(con, reg, com_texto=True)
    # Mas para tipo de METADADO, a linha basta: é tudo o que ele vai ter.
    assert cc.ja_indexado(con, reg, com_texto=False)


def test_ja_indexado_com_paragrafo(con):
    from extrator_paragrafos import Paragrafo
    doc = indice.inserir_documento(
        con, serie="C", numero=2, tipo="CC", caso="Z Vs. W",
        estado=None, data="2020-01-01")
    indice.inserir_paragrafos(con, doc, "esp", [Paragrafo(1, "texto")])
    reg = {"tipo": "CC", "serie": "C", "numero": 2, "caso": "Z Vs. W"}
    assert cc.ja_indexado(con, reg, com_texto=True)


def test_ja_indexado_falso_quando_caso_None(con):
    """Registro sem nome não se procura no índice: a chave inclui o nome, e
    `caso IS NULL` casaria qualquer linha órfã."""
    assert not cc.ja_indexado(
        con, {"tipo": "SS", "serie": None, "numero": None, "caso": None},
        com_texto=False)


# --- metadado (SS) --------------------------------------------------------

def test_indexar_metadado_grava_sem_paragrafo(con):
    reg = {"tipo": "SS", "serie": None, "numero": None,
           "caso": "Baena Ricardo y otros Vs. Panamá", "etapa": None,
           "data": "2013-03-20", "estado": None,
           "url": "http://corteidh.or.cr/docs/supervisiones/baena_20_03_13.pdf"}
    rel = cc.indexar_metadado(con, reg)
    assert rel["erro"] is None
    assert rel["metadado"] is True
    assert rel["n_paragrafos"] == 0

    linha = con.execute(
        "SELECT caso, data, url_esp, url_por, n_paragrafos, tem_por"
        " FROM documento WHERE tipo = 'SS'").fetchone()
    assert linha["caso"] == "Baena Ricardo y otros Vs. Panamá"
    assert linha["data"] == "2013-03-20"
    assert linha["n_paragrafos"] == 0
    assert linha["url_esp"].startswith("https://www.corteidh.or.cr/")
    # `url_por` NÃO se grava: não foi sondada. Endereço não conferido viraria
    # link na citação, e link que não abre destrói a conferência na fonte.
    assert linha["url_por"] is None
    assert linha["tem_por"] == 0


def test_indexar_metadado_pula_sem_nome(con):
    rel = cc.indexar_metadado(
        con, {"tipo": "SS", "serie": None, "numero": None, "caso": None,
              "etapa": None, "data": None, "estado": None, "url": ""})
    assert rel["pulado"]
    assert con.execute("SELECT COUNT(*) FROM documento").fetchone()[0] == 0


def test_tipos_com_texto_sao_so_CC_e_OC():
    """Decisão do Defensor de 2026-09-17. Travada por teste porque acrescentar
    um tipo aqui multiplica o armazenamento sem decisão."""
    assert set(cc.TIPOS_COM_TEXTO_FASE2) == {"CC", "OC"}


# --- identidade: AUTUAÇÃO, não nome ---------------------------------------
#
# Defeito medido em 18/09/2026, com a colheita já em curso: a dedup casava pelo
# NOME, e os 14 documentos da Fase 1 têm título em português — vindo dos PDFs
# `_por` OFICIAIS da Corte, não de tradução — contra o título espanhol do
# catálogo. Dez dos dezesseis entrariam de novo, e a busca devolveria o mesmo
# julgado duas vezes, sob dois nomes, como se fossem precedentes distintos.


def test_mesma_autuacao_com_NOME_EM_OUTRA_LINGUA_ja_esta_indexada(con):
    """`Série C No. 318` É aquele julgado, em qualquer língua do título."""
    from extrator_paragrafos import Paragrafo
    doc = indice.inserir_documento(
        con, serie="C", numero=318, tipo="CC",
        caso="Trabalhadores da Fazenda Brasil Verde Vs. Brasil",
        estado="Brasil", data="2016-10-20")
    indice.inserir_paragrafos(con, doc, "por", [Paragrafo(1, "texto")])

    # O catálogo dá o MESMO julgado com o nome em espanhol.
    do_catalogo = {"tipo": "CC", "serie": "C", "numero": 318,
                   "caso": "Trabajadores de la Hacienda Brasil Verde Vs. Brasil"}
    assert cc.ja_indexado(con, do_catalogo, com_texto=True), (
        "nome em outra língua tem de casar pela autuação, senão duplica")

    # E o nome curado NÃO se perde: quem já está, fica.
    assert con.execute(
        "SELECT caso FROM documento WHERE serie='C' AND numero=318"
    ).fetchone()["caso"] == "Trabalhadores da Fazenda Brasil Verde Vs. Brasil"


def test_autuacoes_DIFERENTES_do_mesmo_caso_sao_documentos_distintos(con):
    """Sentença de mérito e sentença de interpretação têm números próprios.

    *Manuela y otros Vs. El Salvador* é C-441 no mérito e C-461 na
    interpretação. Casar pelo caso, e não pela autuação, faria a segunda
    parecer já indexada — e a interpretação nunca entraria.
    """
    from extrator_paragrafos import Paragrafo
    doc = indice.inserir_documento(
        con, serie="C", numero=441, tipo="CC",
        caso="Manuela y otros Vs. El Salvador", estado=None,
        data="2021-11-02")
    indice.inserir_paragrafos(con, doc, "esp", [Paragrafo(1, "t")])

    interpretacao = {"tipo": "CC", "serie": "C", "numero": 461,
                     "caso": "Manuela y otros Vs. El Salvador"}
    assert not cc.ja_indexado(con, interpretacao, com_texto=True)


def test_SS_sem_numero_NAO_colapsam_num_documento_so(con):
    """A armadilha da correção, e ela é pior que o defeito que corrige.

    As 903 resoluções de supervisão têm `numero` NULO. Casando só por
    `(tipo, serie, numero)`, a primeira indexada faria todas as outras 902
    parecerem já presentes, e a colheita gravaria UMA. Por isso a autuação só
    é chave quando série E número existem.
    """
    primeira = {"tipo": "SS", "serie": None, "numero": None,
                "caso": "Baena Ricardo y otros Vs. Panamá", "etapa": None,
                "data": "2013-03-20", "estado": None, "url": ""}
    cc.indexar_metadado(con, primeira)
    assert cc.ja_indexado(con, primeira, com_texto=False)

    outra = dict(primeira, caso="Cantoral Benavides Vs. Perú")
    assert not cc.ja_indexado(con, outra, com_texto=False), (
        "SS distinta não pode contar como já indexada")

    cc.indexar_metadado(con, outra)
    assert con.execute(
        "SELECT COUNT(*) FROM documento WHERE tipo='SS'").fetchone()[0] == 2


def test_serie_sem_numero_cai_no_nome(con):
    """Autuação incompleta não serve de chave; o nome volta a ser o que há."""
    reg = {"tipo": "CC", "serie": "C", "numero": None, "caso": "X Vs. Y",
           "etapa": None, "data": None, "estado": None, "url": ""}
    assert not cc.ja_indexado(con, reg, com_texto=False)
    cc.indexar_metadado(con, reg)
    assert cc.ja_indexado(con, reg, com_texto=False)
    assert not cc.ja_indexado(con, dict(reg, caso="Z Vs. W"), com_texto=False)


# --- SS: a RESOLUÇÃO é o documento, não o caso ----------------------------
#
# Perda medida em 18/09/2026, com 545 documentos: os 903 registros do catálogo
# SS colapsam em 358 nomes de caso distintos, porque um mesmo caso tem várias
# resoluções de supervisão ao longo dos anos. Sem a data na chave, cada
# resolução nova ATUALIZAVA a anterior.
#
# A consequência não é só perder linha: a ficha reporta o estado do
# CUMPRIMENTO a partir da série SS, e informá-lo por uma resolução de 2013
# havendo uma de 2024 é afirmação falsa sobre o presente.


def test_duas_resolucoes_do_MESMO_caso_sao_documentos_distintos(con):
    """*Vicky Hernández* tem 3 resoluções; *Atenco*, 4. Todas do mesmo caso."""
    base = {"tipo": "SS", "serie": None, "numero": None,
            "caso": "Vicky Hernández y otras Vs. Honduras", "etapa": None,
            "estado": None, "url": ""}

    cc.indexar_metadado(con, dict(base, data="2022-11-22"))
    cc.indexar_metadado(con, dict(base, data="2023-06-14"))
    cc.indexar_metadado(con, dict(base, data="2024-09-03"))

    linhas = con.execute(
        "SELECT data FROM documento WHERE tipo='SS' ORDER BY data").fetchall()
    assert [r["data"] for r in linhas] == ["2022-11-22", "2023-06-14",
                                           "2024-09-03"], (
        "resoluções distintas do mesmo caso têm de ser linhas distintas")


def test_a_MESMA_resolucao_nao_duplica(con):
    """Idempotência preservada: mesma data, mesma linha."""
    reg = {"tipo": "SS", "serie": None, "numero": None,
           "caso": "Baena Ricardo y otros Vs. Panamá", "etapa": None,
           "data": "2013-03-20", "estado": None, "url": ""}
    cc.indexar_metadado(con, reg)
    cc.indexar_metadado(con, reg)
    assert con.execute(
        "SELECT COUNT(*) FROM documento WHERE tipo='SS'").fetchone()[0] == 1
    assert cc.ja_indexado(con, reg, com_texto=False)


def test_resolucao_de_OUTRA_data_nao_conta_como_indexada(con):
    """O sintoma exato da perda: a segunda resolução saía como já indexada."""
    reg = {"tipo": "SS", "serie": None, "numero": None,
           "caso": "Jenkins Vs. Argentina", "etapa": None,
           "data": "2020-05-12", "estado": None, "url": ""}
    cc.indexar_metadado(con, reg)
    assert cc.ja_indexado(con, reg, com_texto=False)

    outra_data = dict(reg, data="2023-11-30")
    assert not cc.ja_indexado(con, outra_data, com_texto=False), (
        "resolução de outra data não está indexada")
    cc.indexar_metadado(con, outra_data)
    assert con.execute(
        "SELECT COUNT(*) FROM documento WHERE tipo='SS'").fetchone()[0] == 2


def test_a_data_NAO_entra_na_chave_quando_ha_autuacao(con):
    """Havendo série e número, eles são a identidade.

    Acrescentar a data ali faria uma reindexação com data ausente DUPLICAR o
    julgado — o defeito inverso, e pior, porque atinge os casos contenciosos,
    que são o acervo citável.
    """
    from extrator_paragrafos import Paragrafo
    doc = indice.inserir_documento(
        con, serie="C", numero=318, tipo="CC", caso="X Vs. Brasil",
        estado="Brasil", data="2016-10-20")
    indice.inserir_paragrafos(con, doc, "por", [Paragrafo(1, "t")])

    # Reindexação SEM data: tem de casar a mesma linha, não criar outra.
    mesmo = indice.inserir_documento(
        con, serie="C", numero=318, tipo="CC", caso="X Vs. Brasil",
        estado=None, data=None)
    assert mesmo == doc
    assert con.execute(
        "SELECT COUNT(*) FROM documento WHERE tipo='CC'").fetchone()[0] == 1
    # E a data preexistente não se perde (COALESCE).
    assert con.execute(
        "SELECT data FROM documento WHERE id = ?", (doc,)
    ).fetchone()["data"] == "2016-10-20"


# --- o Estado demandado está NO NOME, e o filtro tem de achá-lo ------------
#
# REVERSÃO DECLARADA de uma decisão de 18/09/2026. Eu deixara `estado=None`
# chamando a extração de INFERÊNCIA. O enquadramento estava errado: "Vs.
# Brasil" é a designação que a própria Corte dá ao caso, e o decompositor já
# parseia esse mesmo trecho. Ler campo estruturado não é inferir.
#
# Custo medido da decisão errada: `estado="Brasil"` devolvia 12 documentos — os
# semeados à mão — com o acervo já contendo outros casos brasileiros. Resultado
# curto se lê como ausência de precedente.


@pytest.mark.parametrize("caso,esperado", [
    ("Ximenes Lopes Vs. Brasil", "Brasil"),
    ("Velásquez Paiz y otros Vs. Guatemala", "Guatemala"),
    ("Manuela y otros Vs. El Salvador", "El Salvador"),
    ("Quispialaya Vilcapoma Vs. Perú", "Perú"),
    ("Asociación Nacional de Cesantes (ANCEJUB-SUNAT) Vs. Perú", "Perú"),
])
def test_estado_sai_do_nome(caso, esperado):
    assert cc.estado_do_caso(caso) == esperado


@pytest.mark.parametrize("sem_estado", [
    None, "", "Emergencia Climática y Derechos Humanos",
    "Condición jurídica y derechos de los migrantes indocumentados",
])
def test_parecer_consultivo_NAO_tem_estado(sem_estado):
    """Parecer não tem parte demandada e não tem `Vs.`. `None` continua sendo
    ignorância declarada onde a ignorância é real."""
    assert cc.estado_do_caso(sem_estado) is None


@pytest.mark.parametrize("lixo", [
    "X Vs. Serie C No. 441",          # autuação vazada
    "X Vs. " + "a" * 60,              # longo demais para ser país
])
def test_recusa_o_que_NAO_parece_pais(lixo):
    """Gravar lixo num campo de FILTRO é pior que deixá-lo nulo: filtro com
    lixo devolve vazio, e vazio se lê como ausência de precedente."""
    assert cc.estado_do_caso(lixo) is None


def test_registro_do_catalogo_agora_traz_o_estado():
    bruto = {"titulo": "Casos Contenciosos Corte IDH. Caso Ygarza y otros Vs. "
                       "Venezuela. Excepciones Preliminares. Sentencia de 14 "
                       "de mayo de 2026. Serie C No. 595.",
             "url": "", "data": "14 de mayo de 2026", "serie": "C",
             "numero": 595}
    assert cc.registro_do_catalogo(bruto, "CC")["estado"] == "Venezuela"


def test_filtro_de_estado_TOLERA_ACENTO(con):
    """O catálogo grava "Perú"; quem consulta digita "Peru".

    Sem tolerância, o filtro devolveria VAZIO — e vazio aqui se lê como
    ausência de precedente, que é o modo de falha que o subsistema combate.
    """
    from extrator_paragrafos import Paragrafo
    doc = indice.inserir_documento(
        con, serie="C", numero=500, tipo="CC", caso="X Vs. Perú",
        estado="Perú", data="2020-01-01")
    indice.inserir_paragrafos(con, doc, "esp", [
        Paragrafo(1, "El derecho a la vida es inderogable.")])

    for escrito in ("Perú", "Peru", "PERU", "perú"):
        achados = indice.buscar(con, consulta="derecho", estado=escrito)
        assert achados, f"o filtro nao achou com estado={escrito!r}"

    # E continua DISCRIMINANDO: outro país não casa.
    assert indice.buscar(con, consulta="derecho", estado="Brasil") == []


def test_backfill_preenche_sem_sobrescrever_curadoria(con):
    """Não vai à rede, e não troca curadoria existente por leitura automática."""
    a = indice.inserir_documento(
        con, serie="C", numero=601, tipo="CC", caso="Novo Vs. Colombia",
        estado=None, data="2024-01-01")
    b = indice.inserir_documento(
        con, serie="C", numero=149, tipo="CC",
        caso="Ximenes Lopes Vs. Brasil", estado="Brasil", data="2006-07-04")
    c_oc = indice.inserir_documento(
        con, serie="A", numero=18, tipo="OC", caso="Condição Jurídica",
        estado=None, data="2003-09-17")

    r = cc.backfill_estado(con)
    assert r["preenchidos"] == 1
    assert r["sem_estado_no_nome"] == 1          # o parecer

    def estado(i):
        return con.execute("SELECT estado FROM documento WHERE id=?",
                           (i,)).fetchone()["estado"]

    assert estado(a) == "Colombia"
    assert estado(b) == "Brasil", "curadoria existente nao se sobrescreve"
    assert estado(c_oc) is None, "parecer continua sem Estado"


def test_estado_com_DOIS_separadores_pega_o_ULTIMO():
    """Especifica a escolha `partes[-1]`, que uma mutação revelou não estar
    especificada.

    Todos os nomes reais isolados têm UM `Vs.`, então `partes[1]` e
    `partes[-1]` dão o mesmo resultado e a mutação sobrevivia — o teste não
    discriminava. Este caso fixa a semântica: o Estado é o que vem depois do
    ÚLTIMO separador, não do primeiro.
    """
    assert cc.estado_do_caso("A Vs. B Vs. Chile") == "Chile"
