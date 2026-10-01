"""O cjf-jurisprudencia declara que o STJ, nesta base, para em ~dez/2019.

Medido em 01/10/2026 (PAJ 2021/016-09136), no mesmo portal: "pandemia" traz 41
acórdãos do STF, milhares do TRF1/TRF3/TRF5 e só 2 do STJ, ambos até 2019;
"honorarios" no TRF2 traz acórdãos de 08/2026. O corte é do índice do STJ, e o
aviso só aparece quando o STJ está entre os tribunais pedidos.
"""

import importlib.util
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture
def srv(monkeypatch):
    import sys

    if str(_RAIZ) not in sys.path:
        sys.path.insert(0, str(_RAIZ))
    spec = importlib.util.spec_from_file_location(
        "cjf_server_cobertura", _RAIZ / "cjf-jurisprudencia" / "server.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "cached_http", lambda *a, **k: None)
    monkeypatch.setattr(mod, "registrar_dispositivo", lambda *a, **k: None)
    monkeypatch.setattr(mod, "log_query", lambda **k: None)
    monkeypatch.setattr(mod, "relaxar_cjf", lambda q: None)
    monkeypatch.setattr(mod.cjf_client, "buscar_documentos",
                        lambda busca, tribs, n: ([], {t: 0 for t in tribs}))
    return mod


def test_avisa_quando_o_stj_esta_na_busca(srv):
    saida = srv.buscar_jurisprudencia_cjf("honorarios", tribunais="STJ,TRF2")
    assert "COBERTURA" in saida and "STJ" in saida and "2019" in saida
    assert "forcar_scon" in saida


def test_avisa_no_default_que_inclui_o_stj(srv):
    assert "COBERTURA" in srv.buscar_jurisprudencia_cjf("honorarios")


def test_nao_avisa_sem_o_stj(srv):
    assert "COBERTURA" not in srv.buscar_jurisprudencia_cjf("honorarios", tribunais="TRF2,STF")
