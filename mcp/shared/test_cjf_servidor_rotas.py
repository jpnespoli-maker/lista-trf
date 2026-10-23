"""Servidor cjf-jurisprudencia: busca pelas duas rotas e as tools documento,
similares e estatisticas. Sem rede e sem gravar o log de comparação: o
`buscar_duas_rotas` e as funções do `cjf_api` são dublados."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

MCP = Path(__file__).resolve().parents[1]
if str(MCP) not in sys.path:
    sys.path.insert(0, str(MCP))

from shared import cjf_api, cjf_duas_rotas  # noqa: E402
from shared.cjf_duas_rotas import ResultadoDuasRotas, ResultadoRota  # noqa: E402

_FIX = Path(__file__).parent / "fixtures"
ANALYTICS = json.loads((_FIX / "cjf_api_analytics.json").read_text(encoding="utf-8"))


def _doc_api(**extra):
    base = {
        "id": "abc123", "numero": "5000123-45.2024.4.04.7000", "classe": "Rcijef",
        "relator": "GERSON LUIZ ROCHA", "orgao_julgador": "TR 4ª Região",
        "data_julgamento": "10/03/2026", "ementa": "Ementa da API.",
        "tribunal": "TRF4", "via": "api-nova", "origem": "ambas",
        "link_inteiro_teor": "https://exemplo/teor",
    }
    base.update(extra)
    return base


def _resultado(docs=None, antiga_ok=True, api_ok=True, sem_cobertura=None,
               antiga_docs=None, api_docs=None):
    return ResultadoDuasRotas(
        docs=list(docs if docs is not None else [_doc_api()]),
        antiga=ResultadoRota(ok=antiga_ok, docs=antiga_docs or [], totais={"TRF4": 5} if antiga_ok else {},
                             erro=None if antiga_ok else "timeout"),
        api=ResultadoRota(ok=api_ok, docs=api_docs or [], totais={"TRF4": 13} if api_ok else {},
                          erro=None if api_ok else "HTTP 500", sem_cobertura=sem_cobertura or []),
        sobreposicao=1,
    )


@pytest.fixture
def srv(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "cjf_server_rotas", MCP / "cjf-jurisprudencia" / "server.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._gravados = []
    mod._chamadas = []
    monkeypatch.setattr(mod, "cached_http", lambda *a, **k: None)
    monkeypatch.setattr(
        mod, "registrar_dispositivo", lambda mcp, chave, valor, ttl_s=0: mod._gravados.append((chave, valor, ttl_s))
    )
    monkeypatch.setattr(mod, "log_query", lambda **k: None)
    monkeypatch.setattr(mod, "relaxar_cjf", lambda q: None)

    def _duas(termo, tribunais, n, *, relaxada=False, teto_s=90.0):
        mod._chamadas.append({"termo": termo, "tribunais": list(tribunais), "relaxada": relaxada})
        return mod._proximo()

    mod._proximo = lambda: _resultado()
    monkeypatch.setattr(mod.cjf_duas_rotas, "buscar_duas_rotas", _duas)
    return mod


def test_busca_comum_usa_duas_rotas_e_marca_origem(srv):
    saida = srv.buscar_jurisprudencia_cjf("bolsa familia", tribunais="TRF4")
    assert "<origem>ambas</origem>" in saida
    assert "Totais portal-antigo: TRF4:5" in saida and "Totais api-nova: TRF4:13" in saida
    assert "<id>abc123</id>" in saida and "<tribunal>TRF4</tribunal>" in saida
    assert "<link_inteiro_teor>https://exemplo/teor</link_inteiro_teor>" in saida
    assert "ROTA FORA" not in saida


def test_marcas_de_precedente_saem_como_sim(srv):
    srv._proximo = lambda: _resultado([_doc_api(precedente_relevante=True, representativo_controversia=True)])
    saida = srv.buscar_jurisprudencia_cjf("x", tribunais="TRF4")
    assert "<precedente_relevante>sim</precedente_relevante>" in saida
    assert "<representativo_controversia>sim</representativo_controversia>" in saida


def test_rota_fora_e_declarada(srv):
    srv._proximo = lambda: _resultado(antiga_ok=False)
    saida = srv.buscar_jurisprudencia_cjf("x", tribunais="TRF4")
    assert "ROTA FORA: portal-antigo (timeout)" in saida
    assert "<origem>ambas</origem>" in saida and "<erro>" not in saida


def test_duas_rotas_falham_erro_declarado(srv):
    srv._proximo = lambda: _resultado(docs=[], antiga_ok=False, api_ok=False)
    saida = srv.buscar_jurisprudencia_cjf("x", tribunais="TRF4")
    assert saida.startswith("<erro>")
    assert "portal antigo: timeout" in saida and "API nova: HTTP 500" in saida


def test_so_stj_com_portal_antigo_fora_e_erro_com_as_duas_causas(srv):
    srv._proximo = lambda: _resultado(docs=[], antiga_ok=False, sem_cobertura=["STJ"])
    saida = srv.buscar_jurisprudencia_cjf("honorarios", tribunais="STJ")
    assert saida.startswith("<erro>")
    assert "portal antigo: timeout" in saida and "STJ" in saida and "API nova" in saida


def test_so_stj_com_portal_antigo_no_ar_traz_docs_e_avisa(srv):
    doc = {"numero": "REsp", "ementa": "e", "origem": "portal-antigo", "orgao_julgador": "T1"}
    srv._proximo = lambda: _resultado(docs=[doc], sem_cobertura=["STJ"])
    saida = srv.buscar_jurisprudencia_cjf("honorarios", tribunais="STJ")
    assert "<erro>" not in saida and "<origem>portal-antigo</origem>" in saida
    assert "COBERTURA" in saida and "2019" in saida


def test_relaxa_so_com_lista_unificada_vazia(srv, monkeypatch):
    relaxou = []
    monkeypatch.setattr(srv, "relaxar_cjf", lambda q: (relaxou.append(q), "a OU b")[1])
    # Só o portal antigo trouxe documento: a lista unificada NÃO está vazia.
    srv._proximo = lambda: _resultado(docs=[{"ementa": "e", "origem": "portal-antigo"}])
    saida = srv.buscar_jurisprudencia_cjf("a b", tribunais="TRF4")
    assert relaxou == [] and len(srv._chamadas) == 1 and "BUSCA RELAXADA" not in saida

    srv._chamadas.clear()
    respostas = iter([_resultado(docs=[]), _resultado()])
    srv._proximo = lambda: next(respostas)
    saida = srv.buscar_jurisprudencia_cjf("a b", tribunais="TRF4")
    assert [c["relaxada"] for c in srv._chamadas] == [False, True]
    assert srv._chamadas[1]["termo"] == "a OU b"
    assert 'BUSCA RELAXADA: "a OU b"' in saida


def test_filtro_novo_vai_so_a_api(srv, monkeypatch):
    recebido = {}

    def _api(busca, tribs, n, extras):
        recebido.update(busca=busca, tribs=list(tribs), extras=extras)
        return [_doc_api(origem=None)], {"TRF4": 2}

    monkeypatch.setattr(srv.cjf_api, "buscar_api", _api)
    saida = srv.buscar_jurisprudencia_cjf("bolsa", tribunais="TRF4", data_inicio="01/01/2025")
    assert srv._chamadas == []
    assert recebido["extras"] == cjf_api.FiltrosApi(data_inicio="2025-01-01")
    assert "FILTROS DA API NOVA" in saida and "<origem>api-nova</origem>" in saida
    assert "Totais portal-antigo" not in saida


def test_filtro_novo_com_stj_busca_nos_cobertos_e_declara(srv, monkeypatch):
    chamadas = []

    def _api(busca, tribs, n, extras):
        chamadas.append(list(tribs))
        if "STJ" in tribs:
            raise cjf_api.SemCoberturaNaApi(["STJ"], [t for t in tribs if t != "STJ"])
        return [_doc_api()], {"TRF4": 1}

    monkeypatch.setattr(srv.cjf_api, "buscar_api", _api)
    saida = srv.buscar_jurisprudencia_cjf("x", tribunais="STJ,TRF4", so_representativos=True)
    assert chamadas == [["STJ", "TRF4"], ["TRF4"]]
    assert "SEM COBERTURA" in saida and "<erro>" not in saida


@pytest.mark.parametrize("campo, valor", [
    ("data_inicio", "ontem"), ("data_fim", "2025-13-45"),
    ("modo", "fuzzy"), ("tipo_data", "sessao"),
])
def test_entrada_invalida_e_erro_declarado(srv, campo, valor):
    saida = srv.buscar_jurisprudencia_cjf("x", tribunais="TRF4", **{campo: valor})
    assert saida.startswith("<erro>") and valor in saida
    assert srv._chamadas == []


def test_legislacao_nao_entendida_e_erro_e_nao_busca_sem_filtro(srv, monkeypatch):
    monkeypatch.setattr(srv.cjf_api, "buscar_api", lambda *a, **k: pytest.fail("buscou sem o filtro"))
    saida = srv.buscar_jurisprudencia_cjf("x", tribunais="TRF4", legislacao="portaria 12")
    assert saida.startswith("<erro>") and "não entendida" in saida and "Formas aceitas" in saida
    assert srv._chamadas == []


def test_legislacao_entra_normalizada(srv, monkeypatch):
    recebido = {}
    monkeypatch.setattr(srv.cjf_api, "buscar_api",
                        lambda b, t, n, extras: (recebido.update(extras=extras), ([_doc_api()], {"TRF4": 1}))[1])
    srv.buscar_jurisprudencia_cjf("x", tribunais="TRF4", legislacao="Lei 8.742/93")
    assert recebido["extras"].legislacao == "LEI-8742"


def test_relator_nao_encontrado(srv, monkeypatch):
    monkeypatch.setattr(srv.cjf_api, "sugerir_relator", lambda t: [])
    saida = srv.buscar_jurisprudencia_cjf("x", tribunais="TRF4", relator="Fulano Inexistente")
    assert saida.startswith("<erro>") and "relator não encontrado: Fulano Inexistente" in saida


def test_varias_grafias_de_relator_vao_todas(srv, monkeypatch):
    recebido = {}
    monkeypatch.setattr(srv.cjf_api, "sugerir_relator", lambda t: ["LUIZ FUX", "Ministro Luiz Fux"])
    monkeypatch.setattr(srv.cjf_api, "buscar_api",
                        lambda b, t, n, extras: (recebido.update(extras=extras), ([_doc_api()], {"STF": 1}))[1])
    srv.buscar_jurisprudencia_cjf("x", tribunais="STF", relator="fux")
    assert recebido["extras"].relatores == ("LUIZ FUX", "Ministro Luiz Fux")


def test_cache_nao_grava_com_rota_em_erro(srv):
    srv._proximo = lambda: _resultado(antiga_ok=False)
    srv.buscar_jurisprudencia_cjf("x", tribunais="TRF4")
    assert srv._gravados == []

    srv._proximo = lambda: _resultado()
    srv.buscar_jurisprudencia_cjf("x", tribunais="TRF4")
    assert len(srv._gravados) == 1
    chave = srv._gravados[0][0]
    assert chave["v"] == 3 and "filtros" in chave and chave["filtros"]["modo"] == "comum"


def test_stj_sem_cobertura_nao_impede_cache(srv):
    srv._proximo = lambda: _resultado(sem_cobertura=["STJ"])
    srv.buscar_jurisprudencia_cjf("x", tribunais="STJ,TRF4")
    assert len(srv._gravados) == 1


def test_cache_hit_nao_chama_as_rotas(srv, monkeypatch):
    payload = {"docs": [_doc_api()], "totais_antiga": {"TRF4": 5}, "totais_api": {"TRF4": 13}}
    monkeypatch.setattr(srv, "cached_http", lambda *a, **k: json.dumps(payload))
    saida = srv.buscar_jurisprudencia_cjf("x", tribunais="TRF4")
    assert srv._chamadas == [] and "<origem>ambas</origem>" in saida


def test_aviso_stf_sem_data(srv):
    stf = _doc_api(tribunal="STF", origem="api-nova", data_julgamento="", orgao_julgador="")
    srv._proximo = lambda: _resultado(docs=[stf])
    assert "julgado do STF" in srv.buscar_jurisprudencia_cjf("x", tribunais="STF")


def test_documento_cjf_xml(srv, monkeypatch):
    monkeypatch.setattr(srv.cjf_api, "obter_documento",
                        lambda i, c="": {"id": i, "ementa": "Ementa & texto", "inteiro_teor": "palavra " * 500})
    saida = srv.documento_cjf("abc")
    assert saida.startswith("<documento_cjf>") and "<id>abc</id>" in saida
    assert "Ementa &amp; texto" in saida
    curto = srv.documento_cjf("abc", max_tokens=50)
    assert len(curto) < len(saida)
    assert srv._gravados[0][2] == 30 * 86400


def test_documento_cjf_erro(srv, monkeypatch):
    def _falha(i, c=""):
        raise RuntimeError("API nova do CJF respondeu HTTP 404 em /documento/x")

    monkeypatch.setattr(srv.cjf_api, "obter_documento", _falha)
    saida = srv.documento_cjf("x")
    assert saida.startswith("<erro>") and "404" in saida and srv._gravados == []


def test_similares_cjf_xml(srv, monkeypatch):
    monkeypatch.setattr(srv.cjf_api, "similares", lambda i, n: [_doc_api(origem=None)])
    saida = srv.similares_cjf("abc", max_resultados=3)
    assert "<jurisprudencia_cjf_similares" in saida and "<id>abc123</id>" in saida
    assert srv._gravados[0][2] == 86400


def test_estatisticas_cjf_legivel(srv, monkeypatch):
    recebido = {}
    monkeypatch.setattr(srv.cjf_api, "estatisticas",
                        lambda b, t, extras: (recebido.update(t=list(t)), ANALYTICS)[1])
    saida = srv.estatisticas_cjf("bolsa familia", tribunais="TRF4")
    assert "Total de documentos: 3" in saida and "2026: 3" in saida
    assert "GERSON LUIZ ROCHA" in saida and recebido["t"] == ["TRF4"]
    assert srv._gravados[0][2] == 86400


def test_estatisticas_cjf_stj_e_erro_declarado(srv, monkeypatch):
    def _recusa(b, t, extras):
        raise cjf_api.SemCoberturaNaApi(["STJ"], [])

    monkeypatch.setattr(srv.cjf_api, "estatisticas", _recusa)
    assert srv.estatisticas_cjf("x", tribunais="STJ").startswith("<erro>")


def test_ajuda_descreve_rotas_filtros_e_tools(srv):
    ajuda = srv.ajuda_sintaxe_cjf()
    for trecho in ("portal-antigo", "api-nova", "MAIÚSCULO", "unidas por E", "documento_cjf",
                   "similares_cjf", "estatisticas_cjf", "EXCLUI o STJ"):
        assert trecho in ajuda
    assert "costuma vir vazio" not in srv.buscar_jurisprudencia_cjf.__doc__
