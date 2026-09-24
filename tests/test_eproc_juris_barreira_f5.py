"""Barreira anti-robô da F5 no eProc de jurisprudência se nomeia como tal.

Medido em 24/09/2026: o ``eproc1g.trf6.jus.br`` passou a responder HTTP 200
com uma página de ~7 KB ("Please enable JavaScript", scripts ``/TSPD/``) no
lugar da tela de pesquisa. A mensagem antiga dizia "pode ter caído em login ou
mudado de endereço", e mandava procurar no lugar errado.
"""

import sys
from pathlib import Path

import pytest

MCP = Path(__file__).resolve().parents[1] / "mcp"
if str(MCP) not in sys.path:
    sys.path.insert(0, str(MCP))

from shared import eproc_juris_client as ejc  # noqa: E402

PAGINA_F5 = (
    '<html><head><script type="text/javascript" '
    'src="/TSPD/0839abc?type=8"></script></head><body><noscript>Please enable '
    "JavaScript to view the page content.</noscript></body></html>"
)
PAGINA_OUTRA = "<html><head><title>Login</title></head><body>entrar</body></html>"


class RespostaFake:
    def __init__(self, texto):
        self.text = texto

    def raise_for_status(self):
        pass


def _abrir_sem_retry(sess):
    # `abrir` tem @retry (espera exponencial, e embrulha em RetryError); o
    # teste chama a função crua.
    return ejc.EProcJurisSession.abrir.__wrapped__(sess)


def test_barreira_f5_e_nomeada(monkeypatch):
    sess = ejc.EProcJurisSession("TRF6")
    monkeypatch.setattr(sess.session, "get", lambda *a, **k: RespostaFake(PAGINA_F5))
    with pytest.raises(RuntimeError, match="barreira anti-robô"):
        _abrir_sem_retry(sess)


def test_pagina_sem_tela_e_sem_f5_mantem_a_mensagem_generica(monkeypatch):
    sess = ejc.EProcJurisSession("TRF6")
    monkeypatch.setattr(sess.session, "get", lambda *a, **k: RespostaFake(PAGINA_OUTRA))
    with pytest.raises(RuntimeError, match="sem campo txtPesquisa"):
        _abrir_sem_retry(sess)


def test_trf6_nao_aponta_para_host_atras_da_barreira():
    assert "eproc1g.trf6" not in ejc.TRIBUNAIS["TRF6"]
    assert "eproc2g.trf6" not in ejc.TRIBUNAIS["TRF6"]
