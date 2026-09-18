"""Ferramentas MCP do corteidh-jurisprudencia.

Dois contratos travados aqui. Primeiro: o servidor NÃO vai à rede — teste
estrutural conferindo que o módulo não importa `requests`. Segundo: o XML
carrega `exige_traducao` e `citacao_sugerida`, que são o que dispara o dever de
traduzir e o que o validador-citacoes vai ler.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "corteidh-jurisprudencia"
if str(MODULO) not in sys.path:
    sys.path.insert(0, str(MODULO))
if str(MODULO.parent) not in sys.path:
    sys.path.insert(0, str(MODULO.parent))

import indice  # noqa: E402
import server  # noqa: E402
from extrator_paragrafos import Paragrafo  # noqa: E402


@pytest.fixture()
def banco(tmp_path, monkeypatch):
    caminho = tmp_path / "corteidh.db"
    con = indice.abrir(caminho)
    indice.criar_schema(con)
    doc = indice.inserir_documento(
        con, serie="C", numero=149, tipo="CC",
        caso="Ximenes Lopes Vs. Brasil", estado="Brasil", data="2006-07-04",
        etapa="Mérito, Reparações e Custas",
        url_por="https://www.corteidh.or.cr/docs/casos/articulos/seriec_149_por.pdf")
    indice.inserir_paragrafos(con, doc, "por", [
        Paragrafo(89, "As pessoas com deficiência mental internadas estão em "
                      "situação de especial vulnerabilidade.")])
    doc2 = indice.inserir_documento(
        con, serie="C", numero=349, tipo="CC",
        caso="Poblete Vilches e outros Vs. Chile", estado="Chile",
        data="2018-03-08",
        url_esp="https://www.corteidh.or.cr/docs/casos/articulos/seriec_349_esp.pdf")
    indice.inserir_paragrafos(con, doc2, "esp", [
        Paragrafo(118, "La salud es un derecho humano fundamental.")])
    con.close()
    monkeypatch.setattr(server, "CAMINHO_BANCO", caminho)
    return caminho


def test_servidor_nao_importa_requests():
    """Contrato de arquitetura: quem vai à rede é o crawler, não o servidor."""
    fonte = (MODULO / "server.py").read_text(encoding="utf-8")
    assert "import requests" not in fonte
    assert "buscador_oficial" not in fonte


def test_busca_devolve_xml_com_citacao_e_paragrafo(banco):
    xml = server._buscar_sync(consulta="vulnerabilidade")
    assert "<resultados" in xml
    assert "Ximenes Lopes Vs. Brasil" in xml
    assert "par. 89" in xml
    assert "<exige_traducao>False</exige_traducao>" in xml


def test_documento_em_espanhol_marca_exige_traducao_no_xml(banco):
    xml = server._buscar_sync(consulta="salud")
    assert "Poblete Vilches" in xml
    assert "<exige_traducao>True</exige_traducao>" in xml
    assert "<idioma>esp</idioma>" in xml


def test_busca_sem_resultado_devolve_xml_vazio_explicito(banco):
    xml = server._buscar_sync(consulta="usucapiao")
    assert 'total="0"' in xml


def test_banco_ausente_devolve_erro_orientando_o_crawler(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CAMINHO_BANCO", tmp_path / "nao-existe.db")
    xml = server._buscar_sync(consulta="qualquer")
    assert "<erro" in xml
    assert "corteidh_crawler" in xml


def test_ficha_traz_metadados(banco):
    xml = server._ficha_sync(caso="Ximenes Lopes")
    assert "Série C No. 149" in xml
    assert "Brasil" in xml


def test_ficha_de_caso_inexistente_diz_que_nao_achou(banco):
    xml = server._ficha_sync(caso="Caso Inexistente")
    assert "<erro" in xml


# --- COBERTURA declarada, não vazio silencioso (spec §4.2-bis) ------------
# Decisão do Defensor: texto só de CC e OC. Sem estes testes, pedir SS
# devolveria `total="0"`, que se lê como "não há precedente sobre isso" — e o
# Defensor concluiria ausência de precedente a partir de uma lacuna de
# engenharia. É o defeito mais grave que este projeto pode produzir, porque
# não deixa rastro.

def test_tipo_sem_texto_indexado_AVISA_em_vez_de_devolver_vazio(banco):
    xml = server._buscar_sync(consulta="cumprimento", tipo="SS")
    assert "<erro" in xml
    assert "fora-do-indice-textual" in xml
    assert 'total="0"' not in xml, \
        "vazio e não-indexado são coisas diferentes; o servidor tem de dizer qual"
    assert "ficha_caso_corteidh" in xml, "tem de apontar a via que serve"


@pytest.mark.parametrize("tipo", ["SS", "ss", "MP", "CO", "FT", "EX"])
def test_qualquer_tipo_fora_da_cobertura_avisa(banco, tipo):
    """Inclui a minúscula de propósito: quem digita `ss` merece o aviso, não
    um vazio. E inclui tipos que a Fase 2 nem indexa, porque o aviso é sobre
    a cobertura do índice, não sobre a existência do tipo."""
    xml = server._buscar_sync(consulta="qualquer", tipo=tipo)
    assert "fora-do-indice-textual" in xml


@pytest.mark.parametrize("tipo", ["CC", "OC", "cc", "oc"])
def test_tipo_com_texto_indexado_busca_normalmente(banco, tipo):
    """O negativo do teste acima: os dois tipos cobertos não podem cair no
    aviso. Sem este par, um erro no conjunto faria a busca avisar sempre — e a
    ferramenta pararia de servir, também em silêncio."""
    xml = server._buscar_sync(consulta="vulnerabilidade", tipo=tipo)
    assert "fora-do-indice-textual" not in xml
    assert "<resultados" in xml


def test_ajuda_declara_a_cobertura_textual():
    texto = server.ajuda_sintaxe_corteidh()
    assert "TIPOS COM TEXTO INDEXADO" in texto
    assert "903" in texto, "a razão da decisão é o volume; o número tem de estar lá"
    assert "ficha_caso_corteidh" in texto


def test_ajuda_menciona_a_regra_de_traducao_e_o_endereco():
    texto = server.ajuda_sintaxe_corteidh()
    assert "tradução livre" in texto
    assert "par." in texto
    assert "Disponível em" in texto
    assert "IDIOMA DO TRECHO" in texto


def test_limite_de_resultados_e_saneado(banco):
    xml = server._buscar_sync(consulta="vulnerabilidade", max_resultados=9999)
    assert "<resultados" in xml


# --- O LINK de conferência (spec §6.1-bis) ---------------------------------
# O Defensor confere na fonte; é o que impede alucinação. Dois contratos:
# o link SEMPRE sai, e é o do idioma do trecho — nunca o de outro.

def test_toda_citacao_carrega_disponivel_em(banco):
    xml = server._buscar_sync(consulta="vulnerabilidade")
    assert "Disponível em: https://www.corteidh.or.cr/" in xml


def test_link_e_o_do_idioma_do_trecho_portugues(banco):
    xml = server._buscar_sync(consulta="vulnerabilidade")
    assert "seriec_149_por.pdf" in xml
    assert "seriec_149_esp.pdf" not in xml


def test_link_e_o_do_idioma_do_trecho_espanhol(banco):
    """O caso sem versão portuguesa NÃO pode ganhar link em português."""
    xml = server._buscar_sync(consulta="salud")
    assert "seriec_349_esp.pdf" in xml
    assert "_por.pdf" not in xml


def test_link_nunca_cai_para_outro_idioma_quando_falta(banco, tmp_path, monkeypatch):
    """Sem URL no idioma do trecho, a CITAÇÃO sai sem link — ausência declarada
    é melhor que link que manda o Defensor ao documento errado.

    Note o que este teste NÃO exige: o `url_pdf_por` continua podendo aparecer,
    porque informar que existe versão portuguesa do documento é informação
    correta e útil. O que não pode é aquela URL entrar na CITAÇÃO de um trecho
    em inglês. Por isso a asserção é sobre o conteúdo do `citacao_sugerida`, e
    não sobre o XML inteiro.
    """
    import re as _re

    caminho = tmp_path / "so_ingles.db"
    con = indice.abrir(caminho)
    indice.criar_schema(con)
    doc = indice.inserir_documento(
        con, serie="C", numero=999, tipo="CC", caso="Caso Teste Vs. X",
        estado="X", data="2020-01-01",
        url_por="https://www.corteidh.or.cr/NAO-DEVE-SAIR_por.pdf")
    indice.inserir_paragrafos(con, doc, "ing", [
        Paragrafo(7, "The right to health is fundamental.")])
    con.close()
    monkeypatch.setattr(server, "CAMINHO_BANCO", caminho)

    xml = server._buscar_sync(consulta="health")
    assert "Caso Teste Vs. X" in xml
    assert "<idioma>ing</idioma>" in xml

    cit = _re.search(r"<citacao_sugerida>(.*?)</citacao_sugerida>", xml, _re.S)
    assert cit, "a citação tem de sair mesmo sem link"
    assert "Disponível em" not in cit.group(1)
    assert "NAO-DEVE-SAIR" not in cit.group(1)


def test_ficha_tambem_traz_o_link(banco):
    xml = server._ficha_sync(caso="Ximenes Lopes")
    assert "seriec_149_por.pdf" in xml


# --- TRUNCAGEM do conteudo (medida: acervo real tem paragrafo de 189 KB e --
# 358 KB, texto legitimo sem numero de paragrafo correspondente na fonte,
# marcado `suspeito` em vez de dividido -- ver extrator_paragrafos, round 6).
# `truncar_por_tokens` e a UNICA guarda entre o indice e uma resposta gigante
# despejada no contexto de quem redige a peca; mutá-lo matava ZERO testes
# antes deste bloco (Tarefa 7, item 4 da prova por mutação).

def _banco_com_paragrafo_longo(tmp_path, n_caracteres):
    """Documento com um único parágrafo de `n_caracteres`, para os testes de
    truncagem abaixo."""
    caminho = tmp_path / "corteidh.db"
    con = indice.abrir(caminho)
    indice.criar_schema(con)
    doc = indice.inserir_documento(
        con, serie="C", numero=407, tipo="CC",
        caso="Empregados da Fabrica de Fogos Vs. Brasil", estado="Brasil",
        data="2020-07-15",
        url_por="https://www.corteidh.or.cr/docs/casos/articulos/seriec_407_por.pdf")
    frase = "vulnerabilidade estrutural. "
    texto_longo = frase * (n_caracteres // len(frase) + 1)
    indice.inserir_paragrafos(con, doc, "por", [Paragrafo(318, texto_longo)])
    con.close()
    return caminho, texto_longo


def test_paragrafo_longo_sai_truncado_no_conteudo(tmp_path, monkeypatch):
    import re as _re

    caminho, texto_longo = _banco_com_paragrafo_longo(tmp_path, 20_000)
    monkeypatch.setattr(server, "CAMINHO_BANCO", caminho)

    xml = server._buscar_sync(consulta="vulnerabilidade", max_tokens_paragrafo=50)

    conteudo = _re.search(r"<conteudo>(.*?)</conteudo>", xml, _re.S)
    assert conteudo, "o resultado tem de trazer <conteudo>"
    # 50 tokens ~ 200 caracteres; bem menor que os ~20.000 do original
    assert len(conteudo.group(1)) < len(texto_longo) / 10
    # a referência não pode ser vítima do corte: par. e numero de série
    # continuam inteiros no XML
    assert "par. 318" in xml
    assert "Série C No. 407" in xml
    # e a citação sugerida, dentro de <extra>, carrega o endereço completo
    cit = _re.search(r"<citacao_sugerida>(.*?)</citacao_sugerida>", xml, _re.S)
    assert cit and "seriec_407_por.pdf" in cit.group(1)


def test_max_tokens_paragrafo_e_saneado_no_PISO(tmp_path, monkeypatch):
    """O brief fixa o piso em 50 tokens (~200 caracteres). Pedindo um valor
    abaixo disso (inclusive negativo), o servidor não pode aceitar
    literalmente -- teria de truncar quase tudo, ou nada, dependendo do
    sinal -- e sim aplicar o piso."""
    import re as _re

    caminho, texto_longo = _banco_com_paragrafo_longo(tmp_path, 20_000)
    monkeypatch.setattr(server, "CAMINHO_BANCO", caminho)

    xml = server._buscar_sync(consulta="vulnerabilidade", max_tokens_paragrafo=-100)
    conteudo = _re.search(r"<conteudo>(.*?)</conteudo>", xml, _re.S)
    assert conteudo
    # piso de 50 tokens = 200 caracteres, mais o sufixo " [...]" do corte
    assert len(conteudo.group(1)) <= 210


def test_max_tokens_paragrafo_e_saneado_no_TETO(tmp_path, monkeypatch):
    """O teto é 4000 tokens (~16.000 caracteres). Pedindo um valor
    astronômico, o servidor não pode devolver o parágrafo inteiro (30.000
    caracteres) -- o teto tem de valer mesmo sem limite pedido pelo
    chamador."""
    import re as _re

    caminho, texto_longo = _banco_com_paragrafo_longo(tmp_path, 30_000)
    monkeypatch.setattr(server, "CAMINHO_BANCO", caminho)

    xml = server._buscar_sync(consulta="vulnerabilidade", max_tokens_paragrafo=10**9)
    conteudo = _re.search(r"<conteudo>(.*?)</conteudo>", xml, _re.S)
    assert conteudo
    assert len(conteudo.group(1)) < len(texto_longo), \
        "o teto de 4000 tokens tem de cortar, mesmo pedindo um valor gigante"
    # teto de 4000 tokens = 16.000 caracteres, mais a margem do sufixo/corte
    # no último ponto final
    assert len(conteudo.group(1)) <= 16_100


# --- aviso de contaminação: o índice o tinha, a saída não o emitia ---------
#
# Medido em 18/09/2026 pela tool MCP REAL, contra o acervo já crescido: 1.639
# dos 30.610 parágrafos (5,4%) estão marcados `suspeito=1` no índice, e NENHUM
# desses avisos chegava a quem redige — `_montar_resultado` não mencionava o
# campo. A busca devolveu o par. 398 da OC-32 com nota de rodapé visivelmente
# colada ao texto, sem nenhum sinal.
#
# É o pior defeito possível aqui, porque produz citação LITERALMENTE
# VERIFICÁVEL e SUBSTANTIVAMENTE FALSA: o trecho está mesmo naquele parágrafo
# daquele PDF, passa no `validar_fontes` e no `grep`, mas é texto de RODAPÉ.

def test_paragrafo_suspeito_sai_com_AVISO(tmp_path, monkeypatch):
    import xml.etree.ElementTree as ET

    caminho = tmp_path / "corteidh.db"
    c = indice.abrir(caminho)
    indice.criar_schema(c)
    doc = indice.inserir_documento(
        c, serie="C", numero=149, tipo="CC", caso="Ximenes Lopes Vs. Brasil",
        estado="Brasil", data="2006-07-04",
        url_por="https://exemplo/seriec_149_por.pdf")
    indice.inserir_paragrafos(c, doc, "por", [
        Paragrafo(10, "Trecho limpo sobre internação psiquiátrica."),
        Paragrafo(11, "Trecho com rodape colado sobre internação e Cf. nota 37 supra."),
    ], suspeitos={11})
    c.close()
    monkeypatch.setattr(server, "CAMINHO_BANCO", caminho)

    raiz = ET.fromstring(server._buscar_sync(consulta="internação",
                                             max_resultados=10))
    por_par = {r.findtext("paragrafo"): r for r in raiz.findall("resultado")}
    assert {"10", "11"} <= set(por_par), "faltou resultado para medir"

    # O suspeito avisa, e o aviso tem de carregar as DUAS metades:
    #   (i) a ordem — conferir na fonte antes de transcrever;
    #   (ii) a RAZÃO — o trecho existe no PDF e pode não ser fundamentação.
    # Sem (ii) o leitor não entende por que conferir um texto que o `grep`
    # confirma, e é justamente essa a armadilha: a citação é literalmente
    # verificável e substantivamente falsa. Medido por mutação: assertar só
    # (i) deixava passar a remoção de (ii).
    aviso = por_par["11"].findtext("aviso_suspeito") or ""
    assert por_par["11"].findtext("suspeito") == "True"
    assert "CONFERIR NA FONTE" in aviso, aviso
    assert "não ser fundamentação" in aviso, aviso
    assert "rodapé" in aviso or "cabeçalho" in aviso, aviso

    # E o LIMPO diz que é limpo — campo omitido quando falso seria
    # indistinguível de "o servidor não apurou".
    assert por_par["10"].findtext("suspeito") == "False"
    assert not (por_par["10"].findtext("aviso_suspeito") or "")
