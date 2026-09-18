"""Crawler da Corte IDH — indexação de um documento, ponta a ponta com dublê.

O teste não vai à rede: injeta bytes de PDF e confere que o documento entrou no
índice com o idioma certo, o sha256 gravado e os parágrafos buscáveis. O teste
NEGATIVO é o que importa mais: documento cujo download falha não pode entrar no
índice pela metade.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "corteidh-jurisprudencia"
if str(MODULO) not in sys.path:
    sys.path.insert(0, str(MODULO))

import baixador  # noqa: E402
import corteidh_crawler as cc  # noqa: E402
import indice  # noqa: E402

TEXTO = (
    "CORTE INTERAMERICANA\n\n"
    "1. Primeiro paragrafo do caso de teste.\n\n"
    "2. Segundo paragrafo, que fala de vulnerabilidade.\n"
)

# Mesma estrutura de TEXTO, mas com um cabeçalho de secao real (2+ linhas
# seguidas em CAIXA ALTA, fechado por linha em branco) grudado ao final do
# paragrafo 1 -- o padrao que extrator_paragrafos.segmentar (round 6) separa
# em Paragrafo.titulos_removidos em vez de deixar vazar para o corpo.
TEXTO_COM_TITULO = (
    "CORTE INTERAMERICANA\n\n"
    "1. Primeiro paragrafo, antes do titulo de secao.\n\n"
    "III\n"
    "COMPETENCIA\n\n"
    "2. Segundo paragrafo, depois do titulo removido.\n"
)


@pytest.fixture()
def con():
    c = indice.abrir(":memory:")
    indice.criar_schema(c)
    yield c
    c.close()


def test_semente_cnj_tem_os_onze_casos_brasileiros():
    assert len(cc.SEMENTE_CNJ) == 11
    assert all(d["estado"] == "Brasil" for d in cc.SEMENTE_CNJ)
    assert {d["numero"] for d in cc.SEMENTE_CNJ} == {
        149, 161, 200, 203, 219, 318, 333, 346, 353, 407, 435}


def test_indexa_documento_em_portugues(con, tmp_path, monkeypatch):
    monkeypatch.setattr(baixador, "baixar_melhor_idioma",
                        lambda base, **kw: ("por", b"%PDF-falso", base + "_por.pdf"))
    monkeypatch.setattr(cc.ep, "extrair_texto_pdf", lambda _b: TEXTO)

    rel = cc.indexar_documento(con, cc.SEMENTE_CNJ[0], pasta_texto=tmp_path)

    assert rel["idioma"] == "por"
    assert rel["n_paragrafos"] == 2
    assert rel["lacunas"] == []
    res = indice.buscar(con, consulta="vulnerabilidade")
    assert len(res) == 1
    assert res[0]["caso"] == "Ximenes Lopes Vs. Brasil"
    assert res[0]["exige_traducao"] is False
    assert res[0]["paragrafo"] == 2


def test_documento_so_em_espanhol_marca_exige_traducao(con, tmp_path, monkeypatch):
    monkeypatch.setattr(baixador, "baixar_melhor_idioma",
                        lambda base, **kw: ("esp", b"%PDF-falso", base + "_esp.pdf"))
    monkeypatch.setattr(cc.ep, "extrair_texto_pdf", lambda _b: TEXTO)

    rel = cc.indexar_documento(con, cc.SEMENTE_CNJ[0], pasta_texto=tmp_path)
    assert rel["idioma"] == "esp"
    assert indice.buscar(con, consulta="vulnerabilidade")[0]["exige_traducao"] is True


def test_registro_sem_numero_confirmado_e_PULADO_e_nao_falha(con, tmp_path, monkeypatch):
    """`numero=None` é declaração de ignorância (o Vélez Loor, cujo número de
    série ninguém leu na capa). Sem a guarda, `_base_url` montaria
    `seriec_None`, o download falharia e o log diria que a FONTE não respondeu
    — diagnóstico errado, e do tipo que manda alguém investigar o site.

    A guarda também não pode ir à rede: se for, o teste passa mesmo quebrada.
    Por isso o dublê de download ESTOURA se chamado."""
    def _nao_devia_ser_chamado(base, **kw):
        raise AssertionError(f"tentou baixar {base!r} sem número confirmado")

    monkeypatch.setattr(baixador, "baixar_melhor_idioma", _nao_devia_ser_chamado)

    registro = dict(serie="C", numero=None, tipo="CC", estado="Panamá",
                    data="2010-11-23", caso="Vélez Loor Vs. Panamá")
    rel = cc.indexar_documento(con, registro, pasta_texto=tmp_path)

    assert rel.get("pulado"), "tinha de sair como pulado"
    assert not rel["erro"], "pulado NÃO é falha"
    assert con.execute("SELECT COUNT(*) FROM documento").fetchone()[0] == 0
    assert list(tmp_path.glob("*")) == [], "nada escrito em disco"


def test_download_que_falha_nao_deixa_documento_pela_metade(con, tmp_path, monkeypatch):
    def _explode(base, **kw):
        raise baixador.PdfInvalido("nenhum idioma")

    monkeypatch.setattr(baixador, "baixar_melhor_idioma", _explode)
    rel = cc.indexar_documento(con, cc.SEMENTE_CNJ[0], pasta_texto=tmp_path)

    assert rel["erro"]
    assert con.execute("SELECT COUNT(*) FROM documento").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM paragrafo").fetchone()[0] == 0


def test_grava_o_TEXTO_e_nao_o_PDF(con, tmp_path, monkeypatch):
    """Decisão da spec §4.2: o PDF é transitório e não toca o disco; o texto é
    o artefato durável. O nome do arquivo espelha o do PDF de origem, trocando
    a extensão, para que a procedência se leia no próprio nome."""
    monkeypatch.setattr(baixador, "baixar_melhor_idioma",
                        lambda base, **kw: ("por", b"%PDF-falso", base + "_por.pdf"))
    monkeypatch.setattr(cc.ep, "extrair_texto_pdf", lambda _b: TEXTO)

    cc.indexar_documento(con, cc.SEMENTE_CNJ[0], pasta_texto=tmp_path)

    assert (tmp_path / "seriec_149_por.txt").exists()
    assert not (tmp_path / "seriec_149_por.pdf").exists(), \
        "o PDF não pode ser persistido"
    assert list(tmp_path.glob("*.pdf")) == [], "nenhum PDF em disco"


def test_o_texto_gravado_preserva_o_NUMERO_do_paragrafo(con, tmp_path, monkeypatch):
    """O número do parágrafo é a unidade de citação do projeto ("par. 89"), e
    o arquivo de texto é o que sobrevive ao PDF — se ele perder o número, a
    reconstrução do índice perde a capacidade de citar."""
    monkeypatch.setattr(baixador, "baixar_melhor_idioma",
                        lambda base, **kw: ("por", b"%PDF-falso", base + "_por.pdf"))
    monkeypatch.setattr(cc.ep, "extrair_texto_pdf", lambda _b: TEXTO)

    cc.indexar_documento(con, cc.SEMENTE_CNJ[0], pasta_texto=tmp_path)
    conteudo = (tmp_path / "seriec_149_por.txt").read_text(encoding="utf-8")

    assert conteudo.startswith("1. "), "o primeiro bloco tem de abrir com o número"
    assert "\n\n2. " in conteudo, "os blocos se separam por linha em branco"
    # e o texto do parágrafo sobrevive íntegro
    assert "vulnerabilidade" in conteudo


def test_titulo_removido_sobrevive_no_txt_e_e_distinguivel_do_texto(
    con, tmp_path, monkeypatch,
):
    """O cabecalho de secao que o extrator separa em `titulos_removidos`
    (round 6) nao pode desaparecer no artefato durável: se ele vive só no
    objeto em memória, o `.txt` grava texto sabidamente incompleto, e a
    aposta do projeto (§4.2 -- o índice se reconstrói OFFLINE a partir do
    `.txt`, sem voltar ao PDF) fica mais fraca em silêncio."""
    monkeypatch.setattr(baixador, "baixar_melhor_idioma",
                        lambda base, **kw: ("por", b"%PDF-falso", base + "_por.pdf"))
    monkeypatch.setattr(cc.ep, "extrair_texto_pdf", lambda _b: TEXTO_COM_TITULO)

    cc.indexar_documento(con, cc.SEMENTE_CNJ[0], pasta_texto=tmp_path)
    conteudo = (tmp_path / "seriec_149_por.txt").read_text(encoding="utf-8")
    linhas = conteudo.splitlines()

    assert "[titulo removido] III COMPETENCIA" in conteudo, \
        "o cabecalho tem de estar gravado no .txt, não só no objeto"
    # imediatamente ANTES do bloco do paragrafo a que pertencia
    idx = linhas.index("[titulo removido] III COMPETENCIA")
    assert linhas[idx + 1] == "1. Primeiro paragrafo, antes do titulo de secao."
    # inequivocamente DISTINTO do texto do paragrafo: nunca comeca por
    # "N. " (o contrato de numero do paragrafo) nem é o próprio texto
    assert not linhas[idx].startswith("1. ")
    assert "III COMPETENCIA" not in (
        "1. Primeiro paragrafo, antes do titulo de secao."
    )
    # o contrato do NÚMERO do parágrafo, travado no teste-irmão, sobrevive
    assert conteudo.startswith("[titulo removido] "), \
        "a marca abre o arquivo porque pertence ao paragrafo 1, mas o " \
        "bloco do paragrafo 1 em si continua abrindo com '1. '"
    assert "\n2. Segundo paragrafo" in conteudo


def test_documento_sem_titulo_removido_nao_grava_marcador(con, tmp_path, monkeypatch):
    """O negativo do teste acima: documento cujo extrator não separou
    cabeçalho nenhum não pode ganhar marcador vazio no `.txt` -- um
    marcador sem conteúdo seria informação inventada, não ausência
    declarada."""
    monkeypatch.setattr(baixador, "baixar_melhor_idioma",
                        lambda base, **kw: ("por", b"%PDF-falso", base + "_por.pdf"))
    monkeypatch.setattr(cc.ep, "extrair_texto_pdf", lambda _b: TEXTO)

    cc.indexar_documento(con, cc.SEMENTE_CNJ[0], pasta_texto=tmp_path)
    conteudo = (tmp_path / "seriec_149_por.txt").read_text(encoding="utf-8")

    assert "[titulo removido" not in conteudo, \
        "nenhuma linha de marca sem titulo real por tras dela"


def test_sha256_do_PDF_descartado_fica_no_indice(con, tmp_path, monkeypatch):
    """Salvaguarda de apagar o PDF: guardado o sha256, um download futuro pode
    ser PROVADO idêntico ao que foi indexado. Sem isso, reaquisição seria
    aposta — e os defeitos que exigem geometria de página só se atacam com o
    PDF na mão."""
    corpo = b"%PDF-falso-mas-estavel"
    monkeypatch.setattr(baixador, "baixar_melhor_idioma",
                        lambda base, **kw: ("por", corpo, base + "_por.pdf"))
    monkeypatch.setattr(cc.ep, "extrair_texto_pdf", lambda _b: TEXTO)

    rel = cc.indexar_documento(con, cc.SEMENTE_CNJ[0], pasta_texto=tmp_path)
    linha = con.execute("SELECT sha256_por FROM documento WHERE id = ?",
                        (rel["documento_id"],)).fetchone()
    assert linha["sha256_por"] == baixador.sha256(corpo)


def test_main_devolve_rc_zero_com_sucesso_e_pulado_sem_falha(tmp_path, monkeypatch):
    """Buraco de mutação: nenhum teste chamava `main()`, então mutar "contar
    `pulado` como falha no `rc`" não derrubava teste nenhum. `pulado` é
    decisão declarada (número de série não confirmado), não erro — o `rc`
    tem de continuar 0 quando só há sucesso e pulado, sem falha real."""
    monkeypatch.setattr(cc, "SEMENTE_CNJ", [cc.SEMENTE_CNJ[0]])
    monkeypatch.setattr(cc, "SEMENTE_OC", [dict(
        serie="C", numero=None, tipo="CC", estado="Panamá",
        data="2010-11-23", caso="Pulado de teste",
    )])
    monkeypatch.setattr(baixador, "baixar_melhor_idioma",
                        lambda base, **kw: ("por", b"%PDF-falso", base + "_por.pdf"))
    monkeypatch.setattr(cc.ep, "extrair_texto_pdf", lambda _b: TEXTO)

    banco = str(tmp_path / "corteidh.db")
    rc = cc.main(["--semear", "--banco", banco, "--pasta-texto", str(tmp_path)])
    assert rc == 0


def test_main_devolve_rc_diferente_de_zero_com_falha_real(tmp_path, monkeypatch):
    """O contraponto do teste acima: falha de download DE VERDADE tem de
    subir o `rc`, para quem chama o crawler não ignorar um documento que
    entrou pela metade (ou não entrou) no índice."""
    monkeypatch.setattr(cc, "SEMENTE_CNJ", [cc.SEMENTE_CNJ[0]])
    monkeypatch.setattr(cc, "SEMENTE_OC", [])

    def _explode(base, **kw):
        raise baixador.PdfInvalido("nenhum idioma")

    monkeypatch.setattr(baixador, "baixar_melhor_idioma", _explode)

    banco = str(tmp_path / "corteidh.db")
    rc = cc.main(["--semear", "--banco", banco, "--pasta-texto", str(tmp_path)])
    assert rc != 0


def test_reindexar_o_mesmo_documento_nao_duplica(con, tmp_path, monkeypatch):
    monkeypatch.setattr(baixador, "baixar_melhor_idioma",
                        lambda base, **kw: ("por", b"%PDF-falso", base + "_por.pdf"))
    monkeypatch.setattr(cc.ep, "extrair_texto_pdf", lambda _b: TEXTO)

    cc.indexar_documento(con, cc.SEMENTE_CNJ[0], pasta_texto=tmp_path)
    cc.indexar_documento(con, cc.SEMENTE_CNJ[0], pasta_texto=tmp_path)
    assert con.execute("SELECT COUNT(*) FROM documento").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM paragrafo").fetchone()[0] == 2
