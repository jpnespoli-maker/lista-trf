"""A TNU pela API nova do CJF — `cjf_api.buscar_tnu` e a tool `buscar_jurisprudencia_tnu`.

Medido em 22/10/2026: a base da TNU no portal antigo do CJF termina em julho de
2017, e nenhum MCP do projeto alcançava a TNU posterior. A API do SPA novo tem a
TNU até 2026, filtrada por `ORGAO_JULGADOR: "TURMA NACIONAL DE UNIFORMIZAÇÃO"`
(grafia exata; "TNU" dá zero). Número CNJ no texto da busca dá ZERO; ele só casa
pelo campo `process_number`. Controle positivo: o PUIL 0044230-77.2023.4.05.8300
(Tema 379), julgado em 03/12/2025, sai pelos dois caminhos.

Fixtures capturadas por `cjf_api.pesquisar` no mesmo dia e recortadas (sem
INTEIRO_TEOR; DECISAO a 600 e EMENTA a 1.500 caracteres). Jurisprudência pública.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

MCP = Path(__file__).resolve().parents[1]
if str(MCP) not in sys.path:
    sys.path.insert(0, str(MCP))

from shared import cjf_api  # noqa: E402

_FIX = Path(__file__).parent / "fixtures"
TEMATICA = json.loads((_FIX / "cjf_api_tnu_unipessoal.json").read_text(encoding="utf-8"))
PROCESSO = json.loads((_FIX / "cjf_api_tnu_processo.json").read_text(encoding="utf-8"))
PUIL = "0044230-77.2023.4.05.8300"


def _dublar(monkeypatch, resposta):
    chamadas = []

    def _pesquisar(query, filtros, n, *, ordenar_por_data=False, processo=None):
        chamadas.append({"query": query, "filtros": filtros, "ordem": ordenar_por_data, "processo": processo})
        return resposta

    monkeypatch.setattr(cjf_api, "pesquisar", _pesquisar)
    return chamadas


class TestBuscarTnu:
    def test_tematica_filtra_pelo_orgao(self, monkeypatch):
        chamadas = _dublar(monkeypatch, TEMATICA)
        docs, total, exato = cjf_api.buscar_tnu("bolsa familia E unipessoal", 3)
        assert chamadas == [{"query": "bolsa familia E unipessoal",
                             "filtros": {"ORGAO_JULGADOR": ["TURMA NACIONAL DE UNIFORMIZAÇÃO"]},
                             "ordem": False, "processo": None}]
        assert (total, exato) == (4, True)
        assert all(d["orgao_julgador"] == "TURMA NACIONAL DE UNIFORMIZAÇÃO" for d in docs)

    @pytest.mark.parametrize("termo", [PUIL, f" {PUIL}/TNU "])
    def test_numero_cnj_vai_pelo_process_number(self, monkeypatch, termo):
        chamadas = _dublar(monkeypatch, PROCESSO)
        docs, total, _ = cjf_api.buscar_tnu(termo, 3)
        assert chamadas[0]["query"] == "" and chamadas[0]["processo"] == PUIL
        assert "03/12/2025" in [d["data_julgamento"] for d in docs]

    def test_cnj_dentro_de_frase_segue_como_texto(self, monkeypatch):
        chamadas = _dublar(monkeypatch, TEMATICA)
        cjf_api.buscar_tnu(f"tema 379 {PUIL}", 3)
        assert chamadas[0]["processo"] is None

    def test_ordenar_por_data_chega_a_api(self, monkeypatch):
        chamadas = _dublar(monkeypatch, TEMATICA)
        cjf_api.buscar_tnu("bolsa familia", 3, ordenar_por_data=True)
        assert chamadas[0]["ordem"] is True

    def test_contagem_saturada_nao_e_exata(self, monkeypatch):
        cheia = {**TEMATICA, "results": {**TEMATICA["results"], "total": {"value": 10000, "relation": "gte"}}}
        _dublar(monkeypatch, cheia)
        _docs, total, exato = cjf_api.buscar_tnu("beneficio", 3)
        assert (total, exato) == (10000, False)

    def test_canario_total_sem_documentos(self, monkeypatch):
        _dublar(monkeypatch, {**TEMATICA, "results": {**TEMATICA["results"], "hits": []}})
        with pytest.raises(RuntimeError, match="TNU"):
            cjf_api.buscar_tnu("bolsa familia", 3)


@pytest.fixture
def srv(monkeypatch):
    spec = importlib.util.spec_from_file_location("cjf_server_tnu", MCP / "cjf-jurisprudencia" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    gravados = []
    monkeypatch.setattr(mod, "cached_http", lambda *a, **k: None)
    monkeypatch.setattr(mod, "registrar_dispositivo", lambda *a, **k: gravados.append(a))
    monkeypatch.setattr(mod, "log_query", lambda **k: None)
    mod._gravados = gravados
    return mod


def test_tool_devolve_cnj_decisao_e_aviso(srv, monkeypatch):
    _dublar(monkeypatch, PROCESSO)
    saida = srv.buscar_jurisprudencia_tnu(PUIL, max_resultados=3)
    assert saida.startswith("<!-- TNU |") and "Total: 3 |" in saida
    assert "eproctnu-jur.cjf.jus.br" in saida
    assert f"<numero>{PUIL}</numero>" in saida
    assert "<decisao>" in saida
    assert f"<id>{PROCESSO['results']['hits'][0]['_id']}</id>" in saida
    assert len(srv._gravados) == 1
    assert srv._gravados[0][1]["v"] == 2  # cache de antes do <id> não serve


def test_tool_devolve_link_do_inteiro_teor(srv, monkeypatch):
    doc = {"id": "abc", "numero": PUIL, "ementa": "e", "link_inteiro_teor": "https://exemplo/teor"}
    monkeypatch.setattr(srv.cjf_api, "buscar_tnu", lambda b, n, ordenar_por_data=False: ([doc], 1, True))
    saida = srv.buscar_jurisprudencia_tnu("x")
    assert "<id>abc</id>" in saida and "<link_inteiro_teor>https://exemplo/teor</link_inteiro_teor>" in saida


def test_tool_zero_nao_se_cacheia(srv, monkeypatch):
    vazio = {"results": {"total": {"value": 0, "relation": "eq"}, "hits": []}, "aggregations": {}}
    _dublar(monkeypatch, vazio)
    saida = srv.buscar_jurisprudencia_tnu("termo inexistente")
    assert "Total: 0 |" in saida and srv._gravados == []


def test_tool_falha_vira_erro_declarado(srv, monkeypatch):
    def _fora(*_a, **_k):
        raise RuntimeError("API do CJF fora")

    monkeypatch.setattr(cjf_api, "pesquisar", _fora)
    assert srv.buscar_jurisprudencia_tnu("x").startswith("<erro>Falha na busca da TNU")
