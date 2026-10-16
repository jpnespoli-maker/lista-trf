"""Regras puras do SJUR (jurisprudência da Justiça Eleitoral).

O SJUR responde HTTP 200 nos modos de falha medidos em 16/10/2026: captcha
recusado ("Falha na verificação antirrobô.", lista vazia) e limite de taxa do
inteiro teor ("Excesso de requisições!"). Estes testes travam a conversão de
cada um em ERRO — um cliente que confiasse no status leria "não há precedente".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "eleitoral-jurisprudencia"
for p in (MODULO, MODULO.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import sjur_parser as P  # noqa: E402

FIX = RAIZ / "tests" / "fixtures" / "eleitoral"


def _fixture(nome: str) -> dict:
    return json.loads((FIX / nome).read_text(encoding="utf-8"))


# ---------------- tribunal e tipo ----------------

@pytest.mark.parametrize("valor", ["", "TSE", "tse", " Tse "])
def test_tribunal_tse(valor):
    assert P.normalizar_tribunal(valor) == P.Alvo("tse")


@pytest.mark.parametrize("valor", ["TREs", "tres", "TRE", "todos"])
def test_tribunal_todos_os_tres(valor):
    assert P.normalizar_tribunal(valor) == P.Alvo("tres")


@pytest.mark.parametrize("valor", ["TRE-RJ", "tre-rj", "RJ", "TRE RJ", "tre/rj"])
def test_tribunal_um_tre(valor):
    assert P.normalizar_tribunal(valor) == P.Alvo("tres", ("TRE-RJ",))


def test_tribunal_lista_sem_repeticao():
    assert P.normalizar_tribunal("TRE-RJ, ES; rj") == P.Alvo("tres", ("TRE-RJ", "TRE-ES"))


@pytest.mark.parametrize("valor", ["TRE-XX", "TRF2", "TSE,TRE-RJ"])
def test_tribunal_invalido_lista_os_validos(valor):
    with pytest.raises(ValueError, match="TRE-UF"):
        P.normalizar_tribunal(valor)


def test_rotulo():
    assert P.Alvo("tse").rotulo == "TSE"
    assert P.Alvo("tres").rotulo == "TREs (todos)"
    assert P.Alvo("tres", ("TRE-RJ", "TRE-ES")).rotulo == "TRE-RJ, TRE-ES"


def test_tipos_todos_e_lista():
    assert P.normalizar_tipos("todos") == ["Acórdão", "Resolução", "Decisão sem resolução"]
    assert P.normalizar_tipos("acordao, resolucao") == ["Acórdão", "Resolução"]
    with pytest.raises(ValueError, match="tipo_decisao"):
        P.normalizar_tipos("monocratica")


# ---------------- ajuste do corpo ----------------

def _ajustar(**kw):
    base = dict(siglas=(), tipos=["Acórdão"], ano_eleicao=None, tamanho=10, pagina=0,
                ordenacao="re")
    base.update(kw)
    return P.ajustar_corpo(_fixture("corpo_pesquisa.json"), **base)


def test_ajuste_preserva_token_e_consulta():
    original = _fixture("corpo_pesquisa.json")
    novo = _ajustar()
    assert novo["captchaToken"] == original["captchaToken"]
    q_orig = json.loads(original["termoPesquisa"])["bool"]["must"][0]
    q_novo = json.loads(novo["termoPesquisa"])["bool"]["must"][0]
    assert q_novo == q_orig
    assert original["tamanho"] == 25  # não mutou a entrada


def test_ajuste_tamanho_pagina_ordenacao():
    novo = _ajustar(tamanho=7, pagina=2, ordenacao="dj_desc")
    assert (novo["tamanho"], novo["pagina"], novo["ordenacao"]) == (7, 2, "dj_desc")


def test_ajuste_troca_tipos_sem_duplicar():
    novo = _ajustar(tipos=["Acórdão"])
    must = json.loads(novo["termoPesquisa"])["bool"]["must"]
    tipos = [c for c in must if "descricaoTipoDecisao.keyword" in c.get("terms", {})]
    assert tipos == [{"terms": {"descricaoTipoDecisao.keyword": ["Acórdão"]}}]


def test_ajuste_siglas_e_ano():
    novo = _ajustar(siglas=("TRE-RJ",), ano_eleicao=2024)
    filtros = json.loads(novo["termoPesquisa"])["bool"]["filter"]
    assert {"terms": {"siglaTribunalJE.keyword": ["TRE-RJ"]}} in filtros
    assert {"term": {"anoEleicao": 2024}} in filtros


def test_ajuste_sem_filtro_nao_inventa_clausula():
    filtros = json.loads(_ajustar()["termoPesquisa"])["bool"]["filter"]
    assert filtros == []


def test_ajuste_corpo_estranho_e_erro():
    with pytest.raises(P.ErroSJUR, match="termoPesquisa"):
        P.ajustar_corpo({"termoPesquisa": "nao-e-json"}, siglas=(), tipos=["Acórdão"],
                        ano_eleicao=None, tamanho=1, pagina=0, ordenacao="re")


# ---------------- interpretação da resposta ----------------

@pytest.mark.parametrize("nome,sigla", [("busca_tse.json", "TSE"), ("busca_tres.json", None)])
def test_interpreta_resposta_real(nome, sigla):
    payload = _fixture(nome)
    decisoes, total = P.interpretar_busca(payload)
    assert total == payload["totalRegistros"] > 0
    assert len(decisoes) == len(payload["content"])
    primeiro = payload["content"][0]
    d = decisoes[0]
    assert d.codigo_decisao == str(primeiro["codigoDecisao"])
    assert d.tribunal == primeiro["siglaTribunalJE"]
    if sigla:
        assert all(x.tribunal == sigla for x in decisoes)
    assert all(x.numero for x in decisoes)
    assert all("<" not in x.ementa for x in decisoes)
    assert all(not hasattr(x, "partes") for x in decisoes)


def test_antirrobo_e_erro_nao_lista_vazia():
    payload = {"mensagem": "Falha na verificação antirrobô.", "content": [],
               "totalRegistros": 0, "aggs": []}
    with pytest.raises(P.ErroSJUR, match="antirrobô"):
        P.interpretar_busca(payload)


def test_zero_legitimo_devolve_zero():
    assert P.interpretar_busca({"mensagem": None, "content": [], "totalRegistros": 0}) == ([], 0)


def test_total_sem_itens_e_parser_cego():
    with pytest.raises(P.ErroSJUR, match="0 itens"):
        P.interpretar_busca({"mensagem": None, "content": [], "totalRegistros": 12})


def test_itens_ocos_sao_erro():
    oco = {"siglaTribunalJE": "TSE", "codigoDecisao": 1}
    with pytest.raises(P.ErroSJUR, match="oco"):
        P.interpretar_busca({"mensagem": None, "content": [oco, oco], "totalRegistros": 2})


def test_resposta_que_nao_e_objeto_e_erro():
    with pytest.raises(P.ErroSJUR):
        P.interpretar_busca([])


def test_item_sem_cnj_usa_protocolo_e_marca():
    # Review Focus 2 — 1 de 25 itens dos TREs veio sem numeroUnicoFormatado.
    item = {"siglaTribunalJE": "TRE-SP", "siglaClasse": "RE", "numeroProcesso": "12345",
            "codigoDecisao": 9, "textoEmenta": "<p>EMENTA X.</p>"}
    (d,), _ = P.interpretar_busca({"mensagem": None, "content": [item], "totalRegistros": 1})
    assert d.sem_cnj is True
    assert d.numero == "RE nº 12345"
    assert d.ementa == "EMENTA X."


def test_relator_publicacao_e_flags():
    item = {"siglaTribunalJE": "TRE-RJ", "numeroUnicoFormatado": "0600001-00.2024.6.19.0001",
            "codigoDecisao": 5, "temInteiroTeorPDF": "true", "textoEmenta": "E.",
            "relatores": [{"descricaoCompleta": "Relator(a) Des. Fulano", "nome": "Fulano"}],
            "publicacoes": [{"siglaFontePublicacao": "DJE", "dataPublicacao": "01/02/2025"},
                            {"siglaFontePublicacao": "PSESS", "dataPublicacao": None}],
            "descricaoClasse": "Recurso Eleitoral", "siglaClasse": "REl", "anoEleicao": "2024"}
    (d,), _ = P.interpretar_busca({"mensagem": None, "content": [item], "totalRegistros": 1})
    assert d.relator == "Relator(a) Des. Fulano"
    assert d.publicacao == "DJE 01/02/2025; PSESS"
    assert d.classe == "Recurso Eleitoral (REl)"
    assert d.tem_inteiro_teor is True and d.sem_cnj is False and d.ano_eleicao == "2024"


def test_sem_dados_de_partes():
    payload = {"mensagem": None, "totalRegistros": 1,
               "content": [{"siglaTribunalJE": "TSE", "partes": [{"nome": "X"}]}]}
    limpo = P.sem_dados_de_partes(payload)
    assert "partes" not in limpo["content"][0]
    assert "partes" in payload["content"][0]  # não mutou a entrada


def test_contagem_por_tribunal():
    payload = {"aggs": [{"nome": "siglaClasse.keyword", "buckets": [{"key": "RE", "docCount": 3}]},
                        {"nome": "siglaTribunalJE.keyword",
                         "buckets": [{"key": "TRE-SP", "docCount": 4346},
                                     {"key": "TRE-MG", "docCount": 10}]}]}
    assert P.contagem_por_tribunal(payload) == {"TRE-SP": 4346, "TRE-MG": 10}
    assert P.contagem_por_tribunal({}) == {}


def test_contagem_na_fixture_de_tres():
    assert P.contagem_por_tribunal(_fixture("busca_tres.json"))


# ---------------- avisos de sintaxe ----------------

@pytest.mark.parametrize("busca", ["abuso e poder", "contas ou partido", "x nao y", "x não y"])
def test_aviso_operador_minusculo(busca):
    # Review Focus 1 — a SPA só traduz operador MAIÚSCULO.
    avisos = P.avisos_busca(busca)
    assert avisos and "MAIÚSCULO" in avisos[0]


@pytest.mark.parametrize("busca", ["abuso E poder", "contas OU partido", "abuso de poder"])
def test_sem_aviso(busca):
    assert P.avisos_busca(busca) == []


# ---------------- inteiro teor ----------------

def _pdf_minimo(texto: str) -> bytes:
    import fitz
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), texto)
    dados = doc.tobytes()
    doc.close()
    return dados


def _ate_eof(pdf: bytes) -> bytes:
    return pdf[:pdf.rfind(b"%%EOF") + len(b"%%EOF")]


def test_desembrulha_multipart():
    pdf = _pdf_minimo("ACORDAO TESTE")
    corpo = (b"--qOQabc\r\nContent-Disposition: form-data; name=\"file\"; "
             b"filename=\"inteiroTeor.pdf\"\r\nContent-Type: application/pdf\r\n\r\n"
             + pdf + b"\r\n--qOQabc--\r\n")
    assert P.desembrulhar_pdf(corpo) == _ate_eof(pdf)
    assert "ACORDAO TESTE" in P.extrair_texto_pdf(P.desembrulhar_pdf(corpo))


def test_pdf_cru_passa_direto():
    pdf = _pdf_minimo("X")
    assert P.desembrulhar_pdf(pdf) == _ate_eof(pdf)


def test_sem_pdf_e_erro():
    with pytest.raises(P.ErroSJUR, match="PDF"):
        P.desembrulhar_pdf(b"--b\r\n\r\nnada\r\n--b--")


def test_limite_de_taxa_reconhecido():
    corpo = ("Erro ao tentar recuperar PDF: Excesso de requisições! Favor aguardar "
             "alguns instantes e tentar novamente.").encode("utf-8")
    assert P.eh_limite_de_taxa(corpo) is True
    assert P.mensagem_erro_pdf(corpo).startswith("Erro ao tentar recuperar PDF")


def test_outro_erro_nao_e_limite():
    corpo = "Erro ao tentar recuperar PDF: documento inexistente".encode("utf-8")
    assert P.eh_limite_de_taxa(corpo) is False
    assert P.mensagem_erro_pdf(corpo) is not None
    assert P.mensagem_erro_pdf(_pdf_minimo("X")) is None


def test_pdf_sem_texto_e_erro():
    import fitz
    doc = fitz.open()
    doc.new_page()
    vazio = doc.tobytes()
    doc.close()
    with pytest.raises(P.ErroSJUR, match="camada de texto"):
        P.extrair_texto_pdf(vazio)


# ---------------- revisão final (16/10/2026) ----------------

@pytest.mark.parametrize("payload", [
    {},
    {"mensagem": None, "content": None, "totalRegistros": 0},
    {"error": "Internal Server Error", "status": 500},
    {"mensagem": None, "content": [], "totalRegistros": None},
])
def test_resposta_sem_a_forma_esperada_e_erro_nunca_zero(payload):
    # C2 — zero só é zero com `content` lista e `totalRegistros` inteiro.
    with pytest.raises(P.ErroSJUR, match="formato"):
        P.interpretar_busca(payload)


def test_antirrobo_orienta_e_nega_ausencia_de_precedente():
    # I3 — o modo de falha mais provável do captcha não pode soar como "nada achado".
    payload = {"mensagem": "Falha na verificação antirrobô.", "content": [],
               "totalRegistros": 0, "aggs": []}
    with pytest.raises(P.ErroSJUR) as exc:
        P.interpretar_busca(payload)
    assert "antirrobô" in str(exc.value)
    assert "NÃO significa ausência de precedente" in str(exc.value)


def test_item_real_sem_cnj():
    # I4 — o item real (TRE-PR) traz o prefixo do processo e o nº do acórdão.
    item = json.loads((FIX / "item_sem_cnj.json").read_text(encoding="utf-8"))
    (d,), _ = P.interpretar_busca({"mensagem": None, "content": [item], "totalRegistros": 1})
    assert d.sem_cnj is True
    assert d.numero == f"{item['siglaClasse']} nº {item['numeroProcesso']}, acórdão nº {item['numeroDecisao']}"


def test_numero_unico_cru_vira_cnj_formatado():
    item = {"siglaTribunalJE": "TRE-SC", "siglaClasse": "REC", "numeroUnico": "06005506020266240000",
            "numeroProcesso": "060055060", "textoEmenta": "E."}
    (d,), _ = P.interpretar_busca({"mensagem": None, "content": [item], "totalRegistros": 1})
    assert d.numero == "0600550-60.2026.6.24.0000"
    assert d.sem_cnj is False


def _dec(**kw):
    base = dict(numero="1", sem_cnj=False, tribunal="TRE-RJ", classe="", tipo_decisao="Acórdão",
                data="", relator="", publicacao="", uf="", municipio="", ano_eleicao="2024",
                codigo_decisao="1", tem_inteiro_teor=False, ementa="E.", decisao="")
    base.update(kw)
    return P.Decisao(**base)


def test_conferir_filtros_aceita_o_que_bate():
    P.conferir_filtros([_dec(), _dec(ano_eleicao="")], alvo=P.Alvo("tres", ("TRE-RJ",)),
                       tipos=["Acórdão"], ano_eleicao=2024)


@pytest.mark.parametrize("decisao,alvo,tipos,ano", [
    (_dec(tribunal="TRE-SP"), P.Alvo("tres", ("TRE-RJ",)), ["Acórdão"], None),
    (_dec(tribunal="TSE"), P.Alvo("tres"), ["Acórdão"], None),
    (_dec(tribunal="TRE-RJ"), P.Alvo("tse"), ["Acórdão"], None),
    (_dec(tipo_decisao="Resolução"), P.Alvo("tres"), ["Acórdão"], None),
    (_dec(ano_eleicao="2022"), P.Alvo("tres"), ["Acórdão"], 2024),
])
def test_conferir_filtros_acusa_ajuste_sem_efeito(decisao, alvo, tipos, ano):
    # I2 — o backend já ignorou calado um filtro (`tribunais`) no spike.
    with pytest.raises(P.ErroSJUR, match="filtro"):
        P.conferir_filtros([decisao], alvo=alvo, tipos=tipos, ano_eleicao=ano)


def test_parametro_invalido_e_classe_propria():
    # M2 — erro de parâmetro não se confunde com ValueError de JSON quebrado.
    with pytest.raises(P.ParametroInvalido):
        P.normalizar_tribunal("TRE-XX")
    with pytest.raises(P.ParametroInvalido):
        P.normalizar_tipos("monocratica")
    assert issubclass(P.ParametroInvalido, ValueError)
