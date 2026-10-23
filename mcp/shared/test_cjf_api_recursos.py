"""Recursos da API nova do CJF: filtros (data, relator, legislação, marcações), busca
semântica e documento com id, link do inteiro teor e marcações.

Fixtures capturadas na API em 23/10/2026 e recortadas (sem INTEIRO_TEOR; DECISAO a
300 caracteres, `_id` preservado). Jurisprudência pública.
"""

import json
import sys

import pytest
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


DOCUMENTO = json.loads((_FIX / "cjf_api_documento.json").read_text(encoding="utf-8"))
SIMILARES = json.loads((_FIX / "cjf_api_similares.json").read_text(encoding="utf-8"))
ANALYTICS = json.loads((_FIX / "cjf_api_analytics.json").read_text(encoding="utf-8"))
ID_PUIL = DOCUMENTO["results"]["hits"][0]["_id"]


def _dublar_get(monkeypatch, resposta, status=200):
    capturado = {}

    class _Resp:
        status_code = status

        def raise_for_status(self):
            pass

        def json(self):
            return resposta

    def _get(url, params=None, timeout=None):
        capturado["url"] = url
        capturado["params"] = params
        return _Resp()

    monkeypatch.setattr(cjf_api.requests, "get", _get)
    return capturado


def test_obter_documento_inteiro(monkeypatch):
    cap = _dublar_get(monkeypatch, DOCUMENTO)
    d = cjf_api.obter_documento(ID_PUIL)
    assert cap["url"].endswith(f"/documento/{ID_PUIL}")
    assert d["id"] == ID_PUIL
    assert d["numero"] == "0044230-77.2023.4.05.8300"
    assert d["inteiro_teor"]


def test_obter_documento_campo(monkeypatch):
    cap = _dublar_get(monkeypatch, {"campo": "EMENTA", "texto": "abc"})
    assert cjf_api.obter_documento(ID_PUIL, "ementa") == {"id": ID_PUIL, "campo": "ementa", "texto": "abc"}
    assert cap["url"].endswith(f"/documento/{ID_PUIL}/campo/ementa")


def test_campo_invalido():
    import pytest

    with pytest.raises(ValueError):
        cjf_api.obter_documento("x", "relator")


def test_documento_inexistente_levanta(monkeypatch):
    import pytest

    _dublar_get(monkeypatch, {}, status=404)
    with pytest.raises(RuntimeError, match="404"):
        cjf_api.obter_documento("inexistente")


def test_documento_sem_hit_levanta(monkeypatch):
    import pytest

    _dublar_get(monkeypatch, {"results": {"hits": []}, "aggregations": {}})
    with pytest.raises(RuntimeError):
        cjf_api.obter_documento("vazio")


def test_similares_mapeia_documentos(monkeypatch):
    cap = _dublar_get(monkeypatch, SIMILARES)
    docs = cjf_api.similares(ID_PUIL, 3)
    assert cap["url"].endswith(f"/similar/{ID_PUIL}")
    assert cap["params"] == {"size": 3}
    assert len(docs) == 3
    assert all(d["id"] and d["via"] == "api-nova" for d in docs)


def test_id_com_barra_nao_muda_o_caminho(monkeypatch):
    cap = _dublar_get(monkeypatch, {"texto": "t"})
    cjf_api.obter_documento("../a/b?c", "ementa")
    assert cap["url"].endswith("/documento/..%2Fa%2Fb%3Fc/campo/ementa")
    cap = _dublar_get(monkeypatch, DOCUMENTO)
    cjf_api.obter_documento("a/b")
    assert cap["url"].endswith("/documento/a%2Fb")
    cap = _dublar_get(monkeypatch, SIMILARES)
    cjf_api.similares("a/b", 3)
    assert cap["url"].endswith("/similar/a%2Fb")


def test_estatisticas_corpo_e_retorno(monkeypatch):
    cap = _dublar_post(monkeypatch, ANALYTICS)
    r = cjf_api.estatisticas("bolsa familia unipessoal", ["TRF4"], FiltrosApi())
    assert cap["url"].endswith("/analytics")
    assert cap["corpo"]["page_size"] == 0
    assert cap["corpo"]["search_mode"] == "comum"
    assert cap["corpo"]["query"] == "bolsa E familia E unipessoal"
    assert cap["corpo"]["filters"]["TRIBUNAL"] == ["TRF4"]
    assert cap["corpo"]["filters"]["ORIGEM"] == ["JEF:TR:4", "JEF:TRU:4"]
    assert "total_documents" in r


def test_estatisticas_recusa_tribunal_sem_cobertura():
    import pytest

    with pytest.raises(cjf_api.SemCoberturaNaApi):
        cjf_api.estatisticas("x", ["STJ"], FiltrosApi())


def test_sugerir_relator(monkeypatch):
    cap = _dublar_get(monkeypatch, {"sugestoes": ["PAULO ROBERTO PARCA DE PINHO"]})
    assert cjf_api.sugerir_relator("parca") == ["PAULO ROBERTO PARCA DE PINHO"]
    assert cap["url"].endswith("/sugestoes/relator")
    assert cap["params"] == {"q": "parca"}


@pytest.mark.parametrize("entrada,esperado", [
    ("Lei 8.742/93", "LEI-8742"),
    ("lei 8742", "LEI-8742"),
    ("LEI-8742", "LEI-8742"),
    ("Lei 10.836/2004", "LEI-10836"),
])
def test_normalizar_legislacao(entrada, esperado):
    assert cjf_api.normalizar_legislacao(entrada) == esperado


@pytest.mark.parametrize("entrada,esperado", [
    ("Lei nº 8.742/1993", "LEI-8742"),
    ("Lei n. 8.742", "LEI-8742"),
    ("Lei no 8.742", "LEI-8742"),
    ("Lei 8.742/93, art. 20", "LEI-8742"),
    ("Lei Complementar 80/94", "LCP-80"),
    ("lei complementar nº 80", "LCP-80"),
    ("LC 80", "LCP-80"),
    ("Decreto 3.048/99", "DEC-3048"),
    ("Decreto-Lei 4.657", "DEL-4657"),
    ("Decreto Lei nº 4.657/42", "DEL-4657"),
    ("Medida Provisória 2.164-41/2001", "MPR-2164"),
    ("MP 2.215", "MPR-2215"),
])
def test_normalizar_legislacao_tipos_medidos(entrada, esperado):
    assert cjf_api.normalizar_legislacao(entrada) == esperado


@pytest.mark.parametrize("entrada", ["CF art. 203", "Constituição Federal", "Lei", "Súmula 7", "", "8742"])
def test_normalizar_legislacao_nao_entendida_levanta(entrada):
    with pytest.raises(ValueError, match="não entendida"):
        cjf_api.normalizar_legislacao(entrada)
