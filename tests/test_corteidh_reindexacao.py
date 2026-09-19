"""Reconstrução OFFLINE do índice a partir do texto guardado.

A spec §4.2 declara o texto como artefato durável e o banco como derivado, e
promete que "o banco se refaz em minutos e offline". Até 19/09/2026 essa
promessa não tinha código: o crawler ESCREVIA os `.txt` e nunca os relia, de
modo que a única rota para um banco povoado era a rede — horas contra um site
que estrangula. Estes testes cobrem a rota que falta.

O que o texto NÃO carrega são os metadados do documento (caso, data, Estado,
etapa), que vêm do catálogo oficial. Por isso a reconstrução tem duas pernas: o
`_metadados.jsonl`, exportado ao lado do texto e versionado com ele, e os
próprios `.txt`. Juntos, os dois são um retrato completo, em formato de texto,
de um banco binário que o git ignora.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "corteidh-jurisprudencia"
if str(MODULO) not in sys.path:
    sys.path.insert(0, str(MODULO))

import corteidh_crawler as cc  # noqa: E402
import indice  # noqa: E402
from extrator_paragrafos import Paragrafo  # noqa: E402


# --- o formato .txt é REVERSÍVEL -------------------------------------------
# Medido em 19/09/2026 sobre o acervo inteiro antes de escrever o parser:
# 108.893 blocos numerados nos 627 arquivos contra 108.893 parágrafos no banco,
# zero blocos órfãos e zero parágrafos cujo texto contenha linha em branco. É
# essa medição que autoriza separar blocos por linha em branco; sem ela, o
# separador seria ambíguo e o round-trip perderia trecho em silêncio.

def test_round_trip_de_paragrafo_simples():
    """Gravar e reler tem de devolver o mesmo parágrafo. É a propriedade da
    qual tudo depende: o `.txt` só é artefato durável se for reversível."""
    originais = [
        Paragrafo(1, "Primeiro trecho sobre integridade pessoal."),
        Paragrafo(2, "Segundo trecho sobre reparação integral."),
    ]
    bruto = "\n\n".join(cc._bloco_txt(p) for p in originais)
    assert cc.ler_paragrafos_do_txt(bruto) == originais


def test_round_trip_preserva_os_titulos_removidos():
    """Os cabeçalhos extraídos de dentro do parágrafo (round 6 do extrator)
    existem SÓ no objeto em memória e no `.txt`. Perdê-los na releitura
    esvaziaria a razão de o marcador ter sido inventado."""
    original = Paragrafo(
        3, "Terceiro trecho sobre garantias judiciais.",
        titulos_removidos=("II COMPETÊNCIA", "III PROCEDIMENTO"))
    relido = cc.ler_paragrafos_do_txt(cc._bloco_txt(original))
    assert relido == [original]
    assert relido[0].titulos_removidos == ("II COMPETÊNCIA", "III PROCEDIMENTO")


def test_texto_de_varias_linhas_sobrevive():
    """Quebra de linha SIMPLES dentro do parágrafo é comum e não separa bloco;
    só a linha em BRANCO separa."""
    original = Paragrafo(7, "Primeira linha\nsegunda linha\nterceira linha.")
    assert cc.ler_paragrafos_do_txt(cc._bloco_txt(original)) == [original]


def test_numero_dentro_do_texto_nao_vira_paragrafo_novo():
    """O caso que mata o mutante do parser ingênuo. Um parágrafo que começa
    com número em posição de início de linha, DEPOIS da primeira, não pode
    abrir bloco — só a linha em branco abre."""
    original = Paragrafo(
        10, "Conforme o artigo 8.\n2. O Estado alegou que não houve violação.")
    relido = cc.ler_paragrafos_do_txt(cc._bloco_txt(original))
    assert len(relido) == 1, (
        f"o parser quebrou um parágrafo em {len(relido)}: uma linha iniciada "
        f"por número NÃO abre bloco novo")
    assert relido[0].numero == 10


def test_texto_vazio_devolve_lista_vazia():
    assert cc.ler_paragrafos_do_txt("") == []
    assert cc.ler_paragrafos_do_txt("\n\n\n") == []


# --- os METADADOS, que o texto não carrega ---------------------------------
# O `.txt` guarda parágrafo. Caso, Estado, data, etapa e URL vêm do catálogo
# oficial, e sem eles a citação não se monta — que é o produto inteiro. Por
# isso a reconstrução tem duas pernas, e esta é a segunda.

def _con_povoado():
    con = indice.abrir(":memory:")
    indice.criar_schema(con)
    doc = indice.inserir_documento(
        con, serie="C", numero=149, tipo="CC",
        caso="Ximenes Lopes Vs. Brasil", estado="Brasil", data="2006-07-04",
        etapa="Mérito, Reparações e Custas",
        url_por="https://www.corteidh.or.cr/docs/casos/articulos/seriec_149_por.pdf",
        url_esp="https://www.corteidh.or.cr/docs/casos/articulos/seriec_149_esp.pdf")
    indice.inserir_paragrafos(con, doc, "por", [
        Paragrafo(1, "Trecho um sobre integridade pessoal."),
        Paragrafo(2, "Trecho dois sobre reparação integral.",
                  titulos_removidos=("II COMPETÊNCIA",)),
    ])
    indice.inserir_paragrafos(con, doc, "esp", [
        Paragrafo(1, "Parrafo uno sobre integridad personal."),
    ])
    indice.inserir_tema(con, doc, "povos indígenas", fonte="Caderno 11")
    indice.semear_glossario(con)
    return con, doc


def test_exportar_metadados_nao_leva_o_texto(tmp_path):
    """O texto já está nos `.txt`; duplicá-lo no JSONL desfaria a economia que
    justifica o desenho inteiro — e criaria duas fontes da mesma verdade."""
    con, _ = _con_povoado()
    destino = tmp_path / "_metadados.jsonl"
    cc.exportar_metadados(con, destino)

    bruto = destino.read_text(encoding="utf-8")
    assert "Ximenes Lopes Vs. Brasil" in bruto
    assert "Trecho um sobre integridade pessoal" not in bruto, (
        "o texto do parágrafo vazou para o JSONL de metadados")
    tabelas = {json.loads(linha)["_tabela"] for linha in bruto.splitlines()}
    assert "documento" in tabelas
    assert "paragrafo" not in tabelas


def test_metadados_fazem_round_trip(tmp_path):
    """Exportar e importar num banco vazio devolve as mesmas linhas."""
    con, _ = _con_povoado()
    destino = tmp_path / "_metadados.jsonl"
    cc.exportar_metadados(con, destino)

    novo = indice.abrir(":memory:")
    indice.criar_schema(novo)
    cc.importar_metadados(novo, destino)

    for tabela in ("documento", "tema", "glossario"):
        antes = con.execute('select count(*) from "%s"' % tabela).fetchone()[0]
        depois = novo.execute('select count(*) from "%s"' % tabela).fetchone()[0]
        assert antes == depois > 0, f"{tabela}: {antes} antes, {depois} depois"

    d = novo.execute("select * from documento").fetchone()
    assert d["caso"] == "Ximenes Lopes Vs. Brasil"
    assert d["estado"] == "Brasil"
    assert d["data"] == "2006-07-04"
    assert d["etapa"] == "Mérito, Reparações e Custas"
    assert d["url_por"].endswith("seriec_149_por.pdf")


def test_metadados_preservam_o_id_do_documento(tmp_path):
    """O `.txt` se liga ao documento pela URL, mas `paragrafo.documento_id` é o
    id NUMÉRICO. Id trocado na importação reataria os parágrafos ao documento
    errado — e a peça citaria o caso errado, sem erro nenhum."""
    con, doc = _con_povoado()
    destino = tmp_path / "_metadados.jsonl"
    cc.exportar_metadados(con, destino)

    novo = indice.abrir(":memory:")
    indice.criar_schema(novo)
    cc.importar_metadados(novo, destino)
    assert novo.execute("select id from documento").fetchone()["id"] == doc


def test_importar_e_idempotente(tmp_path):
    """Rodar duas vezes não duplica — a reconstrução tem de ser repetível sem
    que ninguém precise lembrar de limpar antes."""
    con, _ = _con_povoado()
    destino = tmp_path / "_metadados.jsonl"
    cc.exportar_metadados(con, destino)

    novo = indice.abrir(":memory:")
    indice.criar_schema(novo)
    cc.importar_metadados(novo, destino)
    cc.importar_metadados(novo, destino)
    assert novo.execute("select count(*) from documento").fetchone()[0] == 1


# --- a RECONSTRUÇÃO, que é o que a spec promete ----------------------------

def _acervo_em_disco(tmp_path):
    """Monta um acervo completo: banco, textos e metadados, como em produção."""
    pasta = tmp_path / "texto"
    banco = tmp_path / "corteidh.db"
    con = indice.abrir(banco)
    indice.criar_schema(con)

    doc = indice.inserir_documento(
        con, serie="C", numero=149, tipo="CC",
        caso="Ximenes Lopes Vs. Brasil", estado="Brasil", data="2006-07-04",
        etapa="Mérito, Reparações e Custas",
        url_por="https://www.corteidh.or.cr/docs/casos/articulos/seriec_149_por.pdf",
        url_esp="https://www.corteidh.or.cr/docs/casos/articulos/seriec_149_esp.pdf")
    por = [
        Paragrafo(88, "O Estado tem o dever de regular e fiscalizar."),
        Paragrafo(89, "As pessoas com deficiência mental estão em situação de "
                      "especial vulnerabilidade.",
                  titulos_removidos=("VIII REPARAÇÕES",)),
    ]
    esp = [Paragrafo(88, "El Estado tiene el deber de regular y fiscalizar.")]
    indice.inserir_paragrafos(con, doc, "por", por)
    indice.inserir_paragrafos(con, doc, "esp", esp)
    indice.inserir_tema(con, doc, "saúde mental", fonte="Caderno 12")
    indice.semear_glossario(con)

    cc.gravar_paragrafos_txt(
        pasta, "https://www.corteidh.or.cr/docs/casos/articulos/seriec_149_por.pdf", por)
    cc.gravar_paragrafos_txt(
        pasta, "https://www.corteidh.or.cr/docs/casos/articulos/seriec_149_esp.pdf", esp)
    cc.exportar_metadados(con, pasta / cc.NOME_METADADOS)
    con.close()
    return pasta, banco


def test_o_banco_se_refaz_do_texto_depois_de_perdido(tmp_path):
    """A promessa da spec §4.2, exercitada: apaga-se o banco inteiro e ele
    volta a partir do texto versionado, SEM rede.

    Não basta contar linhas — a asserção é sobre o produto, que é o parágrafo
    citável com o nome do caso e o link certos."""
    pasta, banco = _acervo_em_disco(tmp_path)
    banco.unlink()
    assert not banco.exists()

    con = indice.abrir(banco)
    indice.criar_schema(con)
    rel = cc.reindexar_do_texto(con, pasta)

    assert rel["documentos"] == 1
    assert rel["paragrafos"] == 3
    assert rel["arquivos"] == 2
    assert rel["sem_documento"] == []

    res = indice.buscar(con, consulta="vulnerabilidade")
    assert len(res) == 1
    assert res[0]["paragrafo"] == 89
    assert res[0]["caso"] == "Ximenes Lopes Vs. Brasil"
    assert res[0]["estado"] == "Brasil"
    assert res[0]["idioma"] == "por"
    assert res[0]["url_por"].endswith("seriec_149_por.pdf")


def test_a_reconstrucao_preserva_a_cascata_de_idioma(tmp_path):
    """O parágrafo 88 existe em português e espanhol. Refeito o banco, a
    cascata tem de continuar preferindo o português — se os idiomas se
    embaralharem na releitura, o defeito aparece como tradução indevida."""
    pasta, banco = _acervo_em_disco(tmp_path)
    banco.unlink()
    con = indice.abrir(banco)
    indice.criar_schema(con)
    cc.reindexar_do_texto(con, pasta)

    res = indice.buscar(con, consulta="fiscalizar OR fiscalizar")
    assert len(res) == 1
    assert res[0]["idioma"] == "por"
    assert res[0]["exige_traducao"] is False
    assert "dever de regular" in res[0]["texto"]


def test_titulos_removidos_sobrevivem_a_reconstrucao(tmp_path):
    """Eles só existem no `.txt`. Perdê-los aqui tornaria o marcador inútil."""
    pasta, banco = _acervo_em_disco(tmp_path)
    lido = cc.ler_paragrafos_do_txt(
        (pasta / "seriec_149_por.txt").read_text(encoding="utf-8"))
    assert lido[1].titulos_removidos == ("VIII REPARAÇÕES",)


def test_txt_sem_documento_nos_metadados_e_DECLARADO(tmp_path):
    """Arquivo órfão não pode ser ignorado em silêncio: sem o documento, o
    parágrafo não tem caso nem link, e some do acervo sem aviso. O relatório
    nomeia cada um — ausência declarada, não lacuna."""
    pasta, banco = _acervo_em_disco(tmp_path)
    (pasta / "seriec_999_por.txt").write_text(
        "1. Trecho de um documento que ninguém declarou.", encoding="utf-8")
    banco.unlink()
    con = indice.abrir(banco)
    indice.criar_schema(con)
    rel = cc.reindexar_do_texto(con, pasta)

    assert rel["sem_documento"] == ["seriec_999_por.txt"]
    assert rel["paragrafos"] == 3, "o órfão não pode ter entrado no índice"


def test_reindexar_sem_metadados_RECUSA(tmp_path):
    """Sem o JSONL não há caso, Estado nem URL — e um banco só com parágrafos
    responderia buscas com citação vazia. Recusar é melhor que entregar
    acervo mutilado que parece inteiro."""
    pasta, banco = _acervo_em_disco(tmp_path)
    (pasta / cc.NOME_METADADOS).unlink()
    banco.unlink()
    con = indice.abrir(banco)
    indice.criar_schema(con)
    with pytest.raises(FileNotFoundError) as erro:
        cc.reindexar_do_texto(con, pasta)
    assert cc.NOME_METADADOS in str(erro.value)


def test_reindexar_e_idempotente(tmp_path):
    """Rodar duas vezes não duplica parágrafo nem infla o índice de busca."""
    pasta, banco = _acervo_em_disco(tmp_path)
    con = indice.abrir(banco)
    cc.reindexar_do_texto(con, pasta)
    cc.reindexar_do_texto(con, pasta)
    assert con.execute("select count(*) from paragrafo").fetchone()[0] == 3
    assert len(indice.buscar(con, consulta="vulnerabilidade")) == 1


# --- a CLI, que é por onde isto se usa -------------------------------------

_TEXTO_FALSO = (
    "CORTE INTERAMERICANA\n\n"
    "1. Primeiro paragrafo do caso de teste.\n\n"
    "2. Segundo paragrafo, que fala de vulnerabilidade.\n"
)


def test_exportar_metadados_pela_linha_de_comando(tmp_path):
    pasta, banco = _acervo_em_disco(tmp_path)
    alvo = pasta / cc.NOME_METADADOS
    alvo.unlink()

    rc = cc.main(["--exportar-metadados", "--banco", str(banco),
                  "--pasta-texto", str(pasta)])
    assert rc == 0
    assert alvo.exists()
    assert "Ximenes" in alvo.read_text(encoding="utf-8")


def test_reindexar_pela_linha_de_comando(tmp_path):
    """O caminho que o Defensor de fato digita quando o banco se perde."""
    pasta, banco = _acervo_em_disco(tmp_path)
    banco.unlink()

    rc = cc.main(["--reindexar-do-texto", "--banco", str(banco),
                  "--pasta-texto", str(pasta)])
    assert rc == 0

    con = indice.abrir(banco)
    res = indice.buscar(con, consulta="vulnerabilidade")
    assert len(res) == 1
    assert res[0]["caso"] == "Ximenes Lopes Vs. Brasil"


def test_reindexar_sem_metadados_devolve_rc_e_nao_traceback(tmp_path):
    """Falta previsível se reporta como mensagem e `rc`, não como stack
    trace: quem roda isto está recuperando um acervo perdido, e traceback é a
    pior forma de dizer o que fazer em seguida."""
    pasta, banco = _acervo_em_disco(tmp_path)
    (pasta / cc.NOME_METADADOS).unlink()
    banco.unlink()

    rc = cc.main(["--reindexar-do-texto", "--banco", str(banco),
                  "--pasta-texto", str(pasta)])
    assert rc != 0


def test_semear_deixa_os_metadados_gravados(tmp_path, monkeypatch):
    """A exportação tem de acontecer NO CRAWL, e não como passo que alguém
    precise lembrar. O JSONL só serve se estiver fresco quando o banco morrer
    — pedi-lo depois da perda seria tarde."""
    import baixador

    monkeypatch.setattr(cc, "SEMENTE_CNJ", [cc.SEMENTE_CNJ[0]])
    monkeypatch.setattr(cc, "SEMENTE_OC", [])
    monkeypatch.setattr(baixador, "baixar_melhor_idioma",
                        lambda base, **kw: ("por", b"%PDF-falso", base + "_por.pdf"))
    monkeypatch.setattr(cc.ep, "extrair_texto_pdf", lambda _b: _TEXTO_FALSO)

    banco = tmp_path / "corteidh.db"
    pasta = tmp_path / "texto"
    rc = cc.main(["--semear", "--banco", str(banco), "--pasta-texto", str(pasta)])
    assert rc == 0
    assert (pasta / cc.NOME_METADADOS).exists(), (
        "o crawl terminou sem deixar os metadados — o texto ficaria sem a "
        "perna que o torna reconstruível")

    # E o par texto+metadados basta: apaga o banco e refaz.
    banco.unlink()
    con = indice.abrir(banco)
    indice.criar_schema(con)
    rel = cc.reindexar_do_texto(con, pasta)
    assert rel["paragrafos"] == 2
    assert indice.buscar(con, consulta="vulnerabilidade")[0]["caso"] == \
        "Ximenes Lopes Vs. Brasil"
