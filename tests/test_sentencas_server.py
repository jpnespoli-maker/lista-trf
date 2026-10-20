"""Tools do sentencas-militares com o transporte dublado (``requests.post``).

O que se trava: o corpo que sai é o do contrato (AND, 10/20, órgãos da JMU por
padrão); 403 e resposta fora da forma viram ``<erro>``, nunca lista vazia; o
texto integral sai pelo número do processo; nenhuma tool busca por parte.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "sentencas-militares"
for p in (MODULO, MODULO.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

_spec = importlib.util.spec_from_file_location("sentencas_server", MODULO / "server.py")
S = importlib.util.module_from_spec(_spec)
sys.modules["sentencas_server"] = S
_spec.loader.exec_module(S)

FIX = RAIZ / "tests" / "fixtures" / "sentencas_militares"


def _fixture(nome):
    return json.loads((FIX / nome).read_text(encoding="utf-8"))


class _Resp:
    def __init__(self, status, dados=None):
        self.status_code = status
        self._dados = dados

    def json(self):
        if self._dados is None:
            raise ValueError("sem JSON")
        return self._dados


@pytest.fixture(autouse=True)
def _sem_log_de_producao(monkeypatch):
    monkeypatch.setattr(S, "log_query", lambda **kw: None)


@pytest.fixture()
def api(monkeypatch):
    estado = {"respostas": [], "corpos": [], "gravados": []}

    def post(url, data=None, headers=None, timeout=None):
        estado["corpos"].append(json.loads(data.decode("utf-8")))
        resp = estado["respostas"].pop(0) if estado["respostas"] else _Resp(200, _fixture("busca_zero.json"))
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(S.sentencas_client.requests, "post", post)
    monkeypatch.setattr(S, "cached_http", lambda mcp, key: None)
    monkeypatch.setattr(S, "registrar_dispositivo",
                        lambda mcp, key, body, ttl_s: estado["gravados"].append(key))
    return estado


# ---------------- busca ----------------

def test_busca_envia_o_contrato_e_devolve_trecho(api):
    api["respostas"].append(_Resp(200, _fixture("busca_3.json")))
    out = S.buscar_sentencas_militares("deserção necessidade")
    corpo = api["corpos"][0]
    assert corpo["pageSize"] == 10 and corpo["order"] == "desc"
    filtro = corpo["searchFilters"][0]
    assert filtro["texto"] == "deserção AND necessidade"
    assert len(filtro["ids_orgaos"]) == 19
    assert "total: 45" in out
    assert out.count("<resultado ") == 3
    assert "<numero>7000001-11.2026.7.01.0001</numero>" in out
    assert "<id_sentenca>" in out and "<tamanho_texto>" in out
    assert "persuasiva, não precedente" in out
    assert "LIGADOS POR AND" in out


def test_cursor_da_proxima_pagina(api):
    dados = _fixture("busca_3.json")
    dados["sentencas"] = dados["sentencas"] * 4  # 12 itens, mais que a página
    dados["sentencas"] = dados["sentencas"][:10]
    api["respostas"].append(_Resp(200, dados))
    out = S.buscar_sentencas_militares("deserção")
    assert f"cursor={dados['sentencas'][-1]['@timestamp']}" in out


def test_todas_as_justicas_nao_filtra_orgao(api):
    S.buscar_sentencas_militares("deserção", justica="todas")
    assert "ids_orgaos" not in api["corpos"][0]["searchFilters"][0]


def test_orgao_especifico(api):
    S.buscar_sentencas_militares("deserção", orgao="1ª Auditoria da 1ª CJM")
    assert api["corpos"][0]["searchFilters"][0]["ids_orgaos"] == [32693]


def test_zero_avisa(api):
    out = S.buscar_sentencas_militares("xqzwvkjhtt")
    assert '<sentencas_militares total="0">' in out and "ZERO RESULTADOS" in out


def test_403_e_erro_explicado(api):
    api["respostas"].append(_Resp(403))
    out = S.buscar_sentencas_militares("deserção")
    assert out.startswith("<erro>") and "403" in out and "pageSize" in out


def test_resposta_fora_do_contrato_e_erro(api):
    api["respostas"].append(_Resp(200, {"resultado": []}))
    out = S.buscar_sentencas_militares("deserção")
    assert out.startswith("<erro>") and "fora do contrato" in out
    assert api["gravados"] == []


@pytest.mark.parametrize("kw", [
    {"busca": "de"},
    {"busca": "deserção", "por_pagina": 25},
    {"busca": "deserção", "orgao": "Vara Federal"},
    {"busca": "deserção", "justica": "federal"},
])
def test_parametro_invalido_nao_sai(api, kw):
    out = S.buscar_sentencas_militares(**kw)
    assert out.startswith("<erro>Parâmetro inválido")
    assert api["corpos"] == []


# ---------------- texto integral ----------------

def test_texto_pelo_numero_do_processo(api):
    api["respostas"].append(_Resp(200, _fixture("busca_3.json")))
    out = S.texto_sentenca_militar("7000001-11.2026.7.01.0001")
    assert api["corpos"][0]["searchFilters"][0]["texto"] == '"7000001-11.2026.7.01.0001"'
    assert "# Sentença — 7000001-11.2026.7.01.0001" in out
    assert "ABSOLVEU" in out and "[Fim do texto:" in out


def test_texto_paginado(api):
    dados = _fixture("busca_3.json")
    dados["sentencas"][0]["texto"] *= 3  # o da fixture fica abaixo do mínimo (2000)
    api["respostas"].append(_Resp(200, dados))
    out = S.texto_sentenca_militar("7000001-11.2026.7.01.0001", max_caracteres=2000)
    assert "Continuação: texto_sentenca_militar(num_processo, inicio=2000)" in out


def test_processo_sem_sentenca_e_erro(api):
    out = S.texto_sentenca_militar("7000009-99.2026.7.01.0001")
    assert out.startswith("<erro>") and "não tem sentença" in out


def test_processo_com_duas_sentencas_pede_o_id(api):
    dados = _fixture("busca_3.json")
    dados["sentencas"][1]["num_processo"] = dados["sentencas"][0]["num_processo"]
    api["respostas"].append(_Resp(200, dados))
    out = S.texto_sentenca_militar("7000001-11.2026.7.01.0001")
    assert "tem 2 sentenças" in out and "id_sentenca" in out


def test_npu_invalido_nao_sai(api):
    out = S.texto_sentenca_militar("70000011120267010001")
    assert out.startswith("<erro>Parâmetro inválido")
    assert api["corpos"] == []


# ---------------- superfície ----------------

def test_nenhuma_tool_busca_por_parte():
    tools = asyncio.run(S.mcp.list_tools())
    assert {t.name for t in tools} == {
        "buscar_sentencas_militares", "texto_sentenca_militar",
        "ajuda_sintaxe_sentencas_militares",
    }
    for t in tools:
        nomes = set(t.inputSchema.get("properties", {}))
        assert not nomes & {"parte", "nome_parte", "cpf", "reu", "acusado"}, t.name


def test_ajuda_lista_as_19_auditorias():
    ajuda = S.ajuda_sintaxe_sentencas_militares()
    assert ajuda.count("JMU - ") == 19
    assert "JME/SP" in ajuda
