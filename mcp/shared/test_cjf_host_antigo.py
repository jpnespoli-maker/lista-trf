"""Os clientes do CJF diante do SPA novo: erro que diz a causa, nunca RetryError mudo.

Medido em 22/10/2026: jurisprudencia.cjf.jus.br virou SPA (Angular, "busca
semântica") e deixou de servir o `javax.faces.ViewState`. Os dois clientes JSF
falhavam em ~33 s com `RetryError[<Future ... raised ValueError>]`, que não
nomeia a causa, e a troca passou um dia sem diagnóstico. O portal JSF continua
em antiga-jurisprudencia.cjf.jus.br, para onde os dois clientes apontam agora.

A fixture `cjf_spa_index.html` é o index do SPA servido no lugar do portal,
capturado no mesmo dia. Página pública, sem dado pessoal.
"""

import sys
from pathlib import Path

import pytest
from tenacity import wait_none

MCP = Path(__file__).resolve().parents[1]
if str(MCP) not in sys.path:
    sys.path.insert(0, str(MCP))

from shared import cjf_client, cjf_regional_client  # noqa: E402

_SPA = (Path(__file__).parent / "fixtures" / "cjf_spa_index.html").read_text(encoding="utf-8")


class _Resp:
    status_code = 200
    text = _SPA

    def raise_for_status(self):
        pass


@pytest.fixture
def sem_espera(monkeypatch):
    for fn in (cjf_client.CJFSession.obter_viewstate,
               cjf_regional_client.TRF1Session.obter_viewstate):
        monkeypatch.setattr(fn.retry, "wait", wait_none())


@pytest.mark.parametrize("modulo, classe, constante", [
    (cjf_client, "CJFSession", "CJF_URL"),
    (cjf_regional_client, "TRF1Session", "TRF1_URL"),
])
def test_spa_levanta_erro_que_nomeia_a_causa(sem_espera, modulo, classe, constante):
    sessao = getattr(modulo, classe)()
    sessao.session.get = lambda *_a, **_k: _Resp()
    with pytest.raises(ValueError) as exc:
        sessao.obter_viewstate()
    assert "SPA" in str(exc.value)
    assert getattr(modulo, constante) in str(exc.value)


@pytest.mark.parametrize("url", [cjf_client.CJF_URL, cjf_regional_client.TRF1_URL])
def test_clientes_apontam_para_o_host_antigo(url):
    assert url.startswith("https://antiga-jurisprudencia.cjf.jus.br/")
