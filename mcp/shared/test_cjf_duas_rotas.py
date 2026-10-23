"""Duas rotas do CJF em paralelo (portal antigo + API nova), fusão e log de comparação.

Fixtures capturadas em 23/10/2026 (`medicamento E oncologico E uniao`, TRF4):
``cjf_rotas_trf4_jsf.json`` (portal antigo, 5 documentos) e ``cjf_rotas_trf4_api.json``
(API, 10 documentos), ementa inteira e DECISAO a 300 caracteres. Os dois lados
trazem o mesmo julgado em 5 pares; no portal antigo o `numero` é o nome da classe.
"""

import json
import re
import sys
import time
from pathlib import Path

import pytest

MCP = Path(__file__).resolve().parents[1]
if str(MCP) not in sys.path:
    sys.path.insert(0, str(MCP))

from shared import cjf_api, cjf_client, cjf_duas_rotas  # noqa: E402
from shared.cjf_duas_rotas import buscar_duas_rotas, chave_ementa, fundir  # noqa: E402

_FIX = Path(__file__).parent / "fixtures"
JSF = json.loads((_FIX / "cjf_rotas_trf4_jsf.json").read_text(encoding="utf-8"))
API = json.loads((_FIX / "cjf_rotas_trf4_api.json").read_text(encoding="utf-8"))
PARES_MEDIDOS = 5

LONGA = "Previdenciario beneficio incapacidade laboral pericia medica comprovada " * 6


def _doc(ementa, relator="FULANO", **extra):
    return {"ementa": ementa, "relator": relator, **extra}


@pytest.fixture
def log(tmp_path, monkeypatch):
    destino = tmp_path / "l.jsonl"
    monkeypatch.setattr(cjf_duas_rotas, "LOG_ROTAS", destino)
    monkeypatch.delenv("DPU_QUERY_LOG_DESLIGADO", raising=False)
    return destino


def _dublar(monkeypatch, antiga=None, api=None):
    chamadas = {"api": []}

    def _antiga(termo, tribunais, n):
        return antiga(termo, tribunais, n) if antiga else ([], {})

    def _api(termo, tribunais, n):
        chamadas["api"].append(list(tribunais))
        return api(termo, tribunais, n) if api else ([], {})

    monkeypatch.setattr(cjf_client, "_buscar_documentos_jsf", _antiga)
    monkeypatch.setattr(cjf_api, "buscar_unificada", _api)
    return chamadas


def test_chave_curta_e_none_e_rotulo_ementa_sai():
    assert chave_ementa({"ementa": ""}) is None
    assert chave_ementa({"ementa": "curta demais"}) is None
    a = chave_ementa({"ementa": "EMENTA " + LONGA})
    b = chave_ementa({"ementa": "E M E N T A\n" + LONGA.upper()})
    assert a is not None and len(a) == 200
    assert a == b


def test_fundir_casa_entre_rotas():
    fundidos, pares = fundir(JSF, API, 100)
    assert pares == PARES_MEDIDOS
    ambas = [d for d in fundidos if d["origem"] == "ambas"]
    assert len(ambas) == PARES_MEDIDOS
    assert all(d["numero"][0].isdigit() for d in ambas)  # o da API, não o nome da classe
    assert len(fundidos) == len(JSF) + len(API) - PARES_MEDIDOS
    assert {d["origem"] for d in fundidos} == {"ambas", "api-nova"}


def test_fundir_intercala_comecando_pela_api():
    a1, a2 = _doc("a1"), _doc("a2")
    j1, j2 = _doc("j1"), _doc("j2")
    fundidos, pares = fundir([j1, j2], [a1, a2], 10)
    assert [d["ementa"] for d in fundidos] == ["a1", "j1", "a2", "j2"]
    assert pares == 0
    assert [d["origem"] for d in fundidos] == ["api-nova", "portal-antigo"] * 2


def test_fundir_respeita_n():
    fundidos, _ = fundir([_doc("j1"), _doc("j2")], [_doc("a1"), _doc("a2")], 3)
    assert len(fundidos) == 3


def test_ementa_vazia_nunca_funde():
    fundidos, pares = fundir([_doc("")], [_doc("")], 10)
    assert len(fundidos) == 2
    assert pares == 0


def test_mesma_rubrica_relatores_distintos_nao_funde():
    fundidos, pares = fundir([_doc(LONGA, "BELTRANO")], [_doc(LONGA, "FULANO")], 10)
    assert len(fundidos) == 2
    assert pares == 0


def test_nao_funde_dentro_da_mesma_rota():
    fundidos, pares = fundir([], [_doc(LONGA), _doc(LONGA)], 10)
    assert len(fundidos) == 2
    assert pares == 0


def test_cada_documento_casa_no_maximo_uma_vez():
    fundidos, pares = fundir([_doc(LONGA)], [_doc(LONGA), _doc(LONGA)], 10)
    assert pares == 1
    assert len(fundidos) == 2
    assert sorted(d["origem"] for d in fundidos) == ["ambas", "api-nova"]


def test_ambas_completa_campos_do_stf():
    api = {**_doc(LONGA), "tribunal": "STF", "via": "api-nova", "aviso": "sem data", "numero": "ADI 1"}
    antigo = {**_doc(LONGA), "data_julgamento": "01/02/2020", "orgao_julgador": "PLENO", "numero": "ADI"}
    fundidos, pares = fundir([antigo], [api], 10)
    assert pares == 1
    (d,) = fundidos
    assert d["origem"] == "ambas"
    assert d["data_julgamento"] == "01/02/2020"
    assert d["orgao_julgador"] == "PLENO"
    assert d["numero"] == "ADI 1"
    assert "aviso" not in d
    assert "aviso" in api  # a entrada não é mutada


def test_ambas_mantem_aviso_se_continua_faltando():
    api = {**_doc(LONGA), "tribunal": "STF", "aviso": "sem data"}
    antigo = {**_doc(LONGA), "data_julgamento": "01/02/2020"}
    (d,) = fundir([antigo], [api], 10)[0]
    assert d["aviso"] == "sem data"


def test_uma_rota_falha_a_outra_responde(monkeypatch, log):
    def antiga(*_):
        raise RuntimeError("canario")

    _dublar(monkeypatch, antiga=antiga, api=lambda t, tr, n: (list(API), {"TRF4": 13}))
    r = buscar_duas_rotas("x", ["TRF4"], 10)
    assert r.antiga.ok is False
    assert "canario" in r.antiga.erro
    assert r.antiga.erro.startswith("RuntimeError: ")
    assert r.api.ok is True
    assert [d["origem"] for d in r.docs] == ["api-nova"] * len(API)


def test_rota_antiga_lenta_nao_segura_a_api(monkeypatch, log):
    def lenta(*_):
        time.sleep(2)
        return [], {}

    _dublar(monkeypatch, antiga=lenta, api=lambda t, tr, n: (list(API), {"TRF4": 13}))
    t0 = time.monotonic()
    r = buscar_duas_rotas("x", ["TRF4"], 10, teto_s=0.5)
    assert time.monotonic() - t0 < 1.5
    assert r.antiga.ok is False
    assert r.antiga.erro == "timeout"
    assert len(r.docs) == len(API)


def test_so_stj_nao_chama_api(monkeypatch, log):
    chamadas = _dublar(monkeypatch, antiga=lambda t, tr, n: ([_doc(LONGA)], {"STJ": 1}))
    r = buscar_duas_rotas("x", ["STJ"], 5)
    assert chamadas["api"] == []
    assert r.api.sem_cobertura == ["STJ"]
    assert r.api.ok is True
    assert r.api.docs == []
    assert [d["origem"] for d in r.docs] == ["portal-antigo"]


def test_stj_vai_so_ao_antigo_e_os_demais_as_duas(monkeypatch, log):
    vistos = {}

    def antiga(t, tr, n):
        vistos["antiga"] = list(tr)
        return [], {}

    chamadas = _dublar(monkeypatch, antiga=antiga)
    r = buscar_duas_rotas("x", ["STJ", "TRF4"], 5)
    assert chamadas["api"] == [["TRF4"]]
    assert vistos["antiga"] == ["STJ", "TRF4"]
    assert r.api.sem_cobertura == ["STJ"]


def test_fusao_ponta_a_ponta_com_fixtures(monkeypatch, log):
    _dublar(
        monkeypatch,
        antiga=lambda t, tr, n: (list(JSF), {"TRF4": 5}),
        api=lambda t, tr, n: (list(API), {"TRF4": 13}),
    )
    r = buscar_duas_rotas("x", ["TRF4"], 100)
    assert r.sobreposicao == PARES_MEDIDOS
    assert r.antiga.totais == {"TRF4": 5}
    assert r.api.totais == {"TRF4": 13}


def test_log_sem_texto_de_documento(monkeypatch, log):
    _dublar(
        monkeypatch,
        antiga=lambda t, tr, n: (list(JSF), {"TRF4": 5}),
        api=lambda t, tr, n: (list(API), {"TRF4": 13}),
    )
    buscar_duas_rotas("medicamento E oncologico", ["STJ", "TRF4"], 10, relaxada=True)
    linhas = log.read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 1
    reg = json.loads(linhas[0])
    assert set(reg) == {"ts", "busca", "tribunais", "relaxada", "antiga", "api", "sobreposicao"}
    chaves_rota = {"ok", "erro", "ms", "totais", "n", "com_cnj", "com_data", "com_orgao"}
    assert set(reg["antiga"]) == chaves_rota
    assert set(reg["api"]) == chaves_rota | {"sem_cobertura"}
    assert reg["relaxada"] is True
    assert reg["antiga"]["n"] == len(JSF)
    assert reg["antiga"]["com_cnj"] == 0
    assert reg["api"]["com_cnj"] == sum(1 for d in API if re.match(r"[0-9]{7}-[0-9]{2}[.][0-9]{4}[.]", d["numero"]))
    assert reg["sobreposicao"] == PARES_MEDIDOS

    texto = linhas[0]
    for d in JSF + API:
        e = " ".join(d["ementa"].split())
        for i in range(0, max(len(e) - 30, 1), 15):
            assert e[i : i + 30] not in texto


def test_log_desligado_pela_variavel(monkeypatch, log):
    monkeypatch.setenv("DPU_QUERY_LOG_DESLIGADO", "1")
    _dublar(monkeypatch)
    buscar_duas_rotas("x", ["TRF4"], 5)
    assert not log.exists()
