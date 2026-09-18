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


def test_registro_NAO_infere_o_estado():
    """O catálogo não devolve o Estado em coluna própria.

    Extraí-lo do sufixo "Vs. <país>" seria INFERÊNCIA, e o filtro `estado` da
    busca passaria a operar sobre dado inferido sem dizê-lo. `None` declara
    que não se sabe.
    """
    bruto = {"titulo": "Casos Contenciosos Corte IDH. Caso X Vs. Venezuela. "
                       "Fondo. Sentencia de 1 de enero de 2020.",
             "url": "", "data": "1 de enero de 2020", "serie": "C",
             "numero": 1}
    assert cc.registro_do_catalogo(bruto, "CC")["estado"] is None


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
