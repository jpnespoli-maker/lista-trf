"""Recursos da API nova do CJF: filtros (data, relator, legislação, marcações), busca
semântica e documento com id, link do inteiro teor e marcações.

Fixtures capturadas na API em 23/10/2026 e recortadas (sem INTEIRO_TEOR; DECISAO a
300 caracteres, `_id` preservado). Jurisprudência pública.
"""

import json
import sys
from pathlib import Path

MCP = Path(__file__).resolve().parents[1]
if str(MCP) not in sys.path:
    sys.path.insert(0, str(MCP))

from shared import cjf_api  # noqa: E402
from shared.cjf_api import FiltrosApi  # noqa: E402

_FIX = Path(__file__).parent / "fixtures"
FILTRADA = json.loads((_FIX / "cjf_api_filtrada.json").read_text(encoding="utf-8"))
SEMANTICA = json.loads((_FIX / "cjf_api_semantica.json").read_text(encoding="utf-8"))


def _dublar_post(monkeypatch, resposta):
    capturado = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return resposta

    def _post(url, json=None, timeout=None):
        capturado["url"] = url
        capturado["corpo"] = json
        return _Resp()

    monkeypatch.setattr(cjf_api.requests, "post", _post)
    return capturado


def test_documento_traz_id_link_e_marcacoes():
    hit = FILTRADA["results"]["hits"][0]
    d = cjf_api.documento(hit["_source"], hit["_id"])
    assert d["id"] == hit["_id"]
    assert d["precedente_relevante"] is True
    com_link = SEMANTICA["results"]["hits"][0]
    assert cjf_api.documento(com_link["_source"], com_link["_id"])["link_inteiro_teor"].startswith("http")
    sem_link = cjf_api.documento({"TRIBUNAL": "TRF4", "LINK_INTEIRO_TEOR": "", "INTEIRO_TEOR": "texto corrido"})
    assert "link_inteiro_teor" not in sem_link
    assert "id" not in sem_link and "precedente_relevante" not in sem_link


def test_documento_link_pelo_inteiro_teor_quando_url():
    d = cjf_api.documento({"TRIBUNAL": "TRF4", "INTEIRO_TEOR": "https://exemplo.jus.br/x"})
    assert d["link_inteiro_teor"] == "https://exemplo.jus.br/x"


def test_documento_representativo_controversia():
    d = cjf_api.documento({"TRIBUNAL": "TRF4", "REPRESENTATIVO_CONTROVERSIA": 1})
    assert d["representativo_controversia"] is True


def test_semantica_vai_a_rota_semantica(monkeypatch):
    cap = _dublar_post(monkeypatch, SEMANTICA)
    cjf_api.pesquisar("x y", {}, 3, extras=FiltrosApi(modo="semantica"))
    assert cap["url"].endswith("/pesquisa-semantica")


def test_filtros_viram_corpo(monkeypatch):
    cap = _dublar_post(monkeypatch, FILTRADA)
    extras = FiltrosApi(
        data_inicio="2025-01-01", data_fim="2025-12-31", tipo_data="DATA_PUBLICACAO_PESQ",
        relatores=("PAULO ROBERTO PARCA DE PINHO",), legislacao="LEI-10836",
        so_precedentes_relevantes=True, so_representativos=True,
    )
    cjf_api.pesquisar("bolsa E familia", {"TRIBUNAL": ["TRF4"]}, 3, extras=extras)
    corpo = cap["corpo"]
    assert cap["url"].endswith("/pesquisar")
    assert corpo["decision_date_start"] == "2025-01-01"
    assert corpo["decision_date_end"] == "2025-12-31"
    assert corpo["date_type"] == "DATA_PUBLICACAO_PESQ"
    assert corpo["rapporteurs"] == ["PAULO ROBERTO PARCA DE PINHO"]
    assert corpo["ref_legislativa"] == "LEI-10836"
    assert corpo["filters"]["PRECEDENTE_RELEVANTE"] == ["1"]
    assert corpo["filters"]["REPRESENTATIVO_CONTROVERSIA"] == ["1"]
    assert corpo["filters"]["TRIBUNAL"] == ["TRF4"]


def test_sem_extras_corpo_igual_ao_de_hoje(monkeypatch):
    cap = _dublar_post(monkeypatch, FILTRADA)
    filtros = {"TRIBUNAL": ["TRF4"]}
    cjf_api.pesquisar("x y", filtros, 3)
    assert cap["url"].endswith("/pesquisar")
    assert cap["corpo"]["decision_date_start"] is None
    assert cap["corpo"]["rapporteurs"] == []
    assert cap["corpo"]["date_type"] == "DATA_DECISAO"
    assert filtros == {"TRIBUNAL": ["TRF4"]}


def test_filtros_api_ativo():
    assert FiltrosApi().ativo() is False
    assert FiltrosApi(so_representativos=True).ativo() is True
    assert FiltrosApi(modo="semantica").ativo() is True


def test_buscar_api_reusa_tribunal_e_origem(monkeypatch):
    chamadas = []

    def _pesquisar(query, filtros, n, **kwargs):
        chamadas.append({"filtros": filtros, **kwargs})
        return FILTRADA

    monkeypatch.setattr(cjf_api, "pesquisar", _pesquisar)
    docs, totais = cjf_api.buscar_api("x", ["TRF4"], 3, FiltrosApi(modo="semantica"))
    assert chamadas[0]["filtros"] == {"TRIBUNAL": ["TRF4"], "ORIGEM": ["JEF:TR:4", "JEF:TRU:4"]}
    assert chamadas[0]["extras"].modo == "semantica"
    assert set(totais) == {"TRF4"}
