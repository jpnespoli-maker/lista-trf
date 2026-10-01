"""Travas da cobertura da CJF Unificada para o STJ e da busca por NÚMERO no SCON.

Medido em 01/10/2026 (PAJ 2021/016-09136), pela própria tool e com controles:
a CJF Unificada não indexa acórdão do STJ posterior a ~dez/2019 — "honorarios"
devolve 33.225 resultados encabeçados por dez/2019; "covid" devolve ZERO para o
STJ; "superendividamento e minimo existencial" devolve 4, o mais novo de 2019,
enquanto o SCON devolve 46, com acórdãos de 2022 a 09/2026. Na mesma CJF, o TRF4
traz 2025-2026: o corte é do índice do STJ no portal, não do cliente, que não
envia filtro de data. E a busca pelo número `2098934` dava zero na CJF; no SCON,
`livre=2098934` traz quem CITA o número, e `livre=2098934.NUM.` traz o processo.

Duas consequências, ambas aqui: (1) a rota CJF declara a cobertura no retorno;
(2) busca que é só número de processo vai ao SCON com o qualificador `.NUM.`.
"""

import importlib.util
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[1]


def _carregar_servidor():
    import sys

    if str(_RAIZ) not in sys.path:
        sys.path.insert(0, str(_RAIZ))
    spec = importlib.util.spec_from_file_location(
        "stj_server_cobertura", _RAIZ / "stj-jurisprudencia" / "server.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def srv(monkeypatch):
    mod = _carregar_servidor()
    monkeypatch.setattr(mod, "cached_http", lambda *a, **k: None)
    monkeypatch.setattr(mod, "registrar_dispositivo", lambda *a, **k: None)
    monkeypatch.setattr(mod, "log_query", lambda **k: None)
    return mod


@pytest.mark.parametrize("query, esperado", [
    ("2098934", "2098934"),
    ("2.098.934", "2098934"),
    ("REsp 2.098.934/RO", "2098934"),
    ("AgInt no AREsp 2.098.934", "2098934"),
    ("EDcl no AgInt no REsp 1884778", "1884778"),
])
def test_reconhece_busca_por_numero(srv, query, esperado):
    assert srv._numero_de_processo(query) == esperado


@pytest.mark.parametrize("query", [
    "Tema 1285",                       # número de tema, não de processo
    "honorarios e omiss$",
    "art. 85 e 18",
    "Lei 14.181",                      # número de lei
    "123",                             # curto demais para processo
])
def test_nao_confunde_busca_tematica_com_numero(srv, query):
    assert srv._numero_de_processo(query) is None


def test_numero_vai_ao_scon_com_qualificador_num(srv, monkeypatch):
    chamadas = {}

    def _scon(query, base, tamanho, mt):
        chamadas["scon"] = query
        return "<resultados/>", 1, "processo.stj.jus.br"

    def _cjf(*a, **k):
        chamadas["cjf"] = True
        return "<resultados/>", 1

    monkeypatch.setattr(srv, "_rota_scon_http", _scon)
    monkeypatch.setattr(srv, "_rota_cjf", _cjf)
    srv.buscar_jurisprudencia_stj("REsp 2.098.934/RO")
    assert chamadas.get("scon") == "2098934.NUM."
    assert "cjf" not in chamadas


def test_busca_tematica_segue_na_cjf(srv, monkeypatch):
    chamadas = {}
    monkeypatch.setattr(srv, "_rota_scon_http",
                        lambda *a, **k: chamadas.setdefault("scon", True) and ("", 0, ""))
    monkeypatch.setattr(srv, "_rota_cjf",
                        lambda q, *a, **k: (chamadas.setdefault("cjf", q), ("<r/>", 1))[1])
    srv.buscar_jurisprudencia_stj("honorarios e omiss$")
    assert chamadas.get("cjf") == "honorarios e omiss$"
    assert "scon" not in chamadas


def test_rota_cjf_declara_a_cobertura(srv, monkeypatch):
    monkeypatch.setattr(srv, "_buscar_via_cjf", lambda *a, **k: ([], 0))
    monkeypatch.setattr(srv, "relaxar_cjf", lambda q: None)
    saida, n = srv._rota_cjf("honorarios", 5, 100)
    assert n == 0
    assert "COBERTURA" in saida and "2019" in saida and "forcar_scon" in saida
