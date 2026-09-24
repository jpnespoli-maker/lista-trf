"""Inteiro teor pelo eProc de jurisprudência: id no cartão e download com canário.

Até 24/09/2026 o cliente descartava o link do inteiro teor, e o voto — onde está
a razão de decidir e o fato que torna o caso análogo — não se lia pelo MCP. No
PAJ 2025/077-00524 dois precedentes foram rotulados favoráveis sem o voto, e
nenhum era análogo.

Medido na mesma data: id inexistente volta HTTP 200 com a página GENÉRICA do
eProc; só o título ``Documento:<n>`` distingue o voto. Os testes abaixo travam
esse canário.
"""

import sys
from pathlib import Path

import pytest

MCP = Path(__file__).resolve().parents[1] / "mcp"
if str(MCP) not in sys.path:
    sys.path.insert(0, str(MCP))

from shared import eproc_juris_client as ejc  # noqa: E402

CARTAO = """
<div class="card mb-3 resultadoItem" id="resultado511762972862891953851258841889">
  <a href="javascript:void(0)" class="text-dark inteiroTeor"
     data-link="externo_controlador.php?acao=jurisprudencia@jurisprudencia/download_inteiro_teor&id_jurisprudencia=511762972862891953851258841889&termosPesquisados=eA==">
     <i class="material-icons">article</i></a>
  <a class="numero-processo" href="#">5057681-28.2024.4.02.5101/RJ</a>
  <div class="resLabel">EMENTA</div><div class="resValue">RECURSO CONHECIDO E PROVIDO.</div>
</div>
"""

CARTAO_SEM_ICONE = """
<div class="card resultadoItem" id="resultado721771520552383891218345354660">
  <a class="numero-processo" href="#">5025111-59.2023.4.04.7205/SC</a>
  <div class="resLabel">DECISÃO</div><div class="resValue">DAR PARCIAL PROVIMENTO.</div>
</div>
"""

DOCUMENTO = (
    '<?xml version="1.0" encoding="ISO-8859-1"?><html><head><title>Documento:510014226320'
    "</title><script>var x=1;</script></head><body><p>VOTO</p><p>Vê-se que a decisão de "
    "origem violou os preceitos legais.</p></body></html>"
)
GENERICA = "<html><head><title>eproc</title></head><body>Ir para conteúdo Pesquisar processo</body></html>"


class _Resp:
    def __init__(self, html, tipo="text/html; charset= ISO-8859-1"):
        self.content = html.encode("iso-8859-1")
        self.headers = {"content-type": tipo}

    def raise_for_status(self):
        pass


class _Sessao:
    def __init__(self, resp):
        self.sessao_fresca = True
        self.base = "https://eproc.trf2.jus.br/eproc/"
        self.url_pesquisar = self.base + "externo_controlador.php?acao=x"
        self.pedidos = []
        resp_ = resp

        class _S:
            def get(inner, url, **kw):
                self.pedidos.append(url)
                return resp_
        self.session = _S()


def _com_sessao(monkeypatch, resp):
    sess = _Sessao(resp)
    monkeypatch.setattr(ejc, "get_sessao", lambda trib: sess)
    return sess


# -------------------------------------------------------------- id no cartão

def test_id_do_inteiro_teor_sai_do_link_do_icone():
    doc = ejc.extrair_documentos(CARTAO)[0]
    assert doc["id_inteiro_teor"] == "511762972862891953851258841889"
    assert doc["numero"].startswith("5057681")


def test_sem_icone_o_id_sai_do_proprio_div():
    doc = ejc.extrair_documentos(CARTAO_SEM_ICONE)[0]
    assert doc["id_inteiro_teor"] == "721771520552383891218345354660"


# ------------------------------------------------------------------ download

def test_baixa_o_voto_e_tira_script(monkeypatch):
    sess = _com_sessao(monkeypatch, _Resp(DOCUMENTO))
    r = ejc.baixar_inteiro_teor("TRF2", "511762972862891953851258841889")
    assert "Vê-se que a decisão de origem violou" in r["texto"]
    assert "var x" not in r["texto"]
    assert r["truncado"] is False
    assert "id_jurisprudencia=511762972862891953851258841889" in sess.pedidos[0]


def test_truncamento_e_declarado(monkeypatch):
    _com_sessao(monkeypatch, _Resp(DOCUMENTO.replace("VOTO", "VOTO " * 800)))
    r = ejc.baixar_inteiro_teor("TRF2", "511762972862891953851258841889", max_caracteres=1000)
    assert r["truncado"] is True
    assert len(r["texto"]) == 1000
    assert r["chars_total"] > 1000


def test_pagina_generica_do_portal_nao_passa_por_voto(monkeypatch):
    """O canário: id inexistente volta 200 com o menu do eProc."""
    _com_sessao(monkeypatch, _Resp(GENERICA))
    with pytest.raises(RuntimeError, match="não devolveu inteiro teor"):
        ejc.baixar_inteiro_teor("TRF2", "511762972862891953851258841880")


def test_formato_nao_html_falha_com_o_tipo_na_mensagem(monkeypatch):
    _com_sessao(monkeypatch, _Resp(DOCUMENTO, tipo="application/pdf"))
    with pytest.raises(RuntimeError, match="application/pdf"):
        ejc.baixar_inteiro_teor("TRF2", "511762972862891953851258841889")


@pytest.mark.parametrize("ruim", ["", "abc", "12345", "511762&acao=outra", "1" * 41])
def test_id_invalido_e_recusado_sem_ir_a_rede(monkeypatch, ruim):
    sess = _com_sessao(monkeypatch, _Resp(DOCUMENTO))
    with pytest.raises(ValueError, match="id_inteiro_teor inválido"):
        ejc.baixar_inteiro_teor("TRF2", ruim)
    assert sess.pedidos == []
