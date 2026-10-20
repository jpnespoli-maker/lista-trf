"""Tools do stm-jurisprudencia com o portal dublado no transporte (``_get``).

O que se trava: (a) a página sem contagem nunca vira lista vazia muda — com o
controle casando, sai zero COM aviso; sem ele, ``<erro>``; (b) parâmetro que o
portal recusaria em silêncio não chega a sair; (c) zero não entra no cache;
(d) nenhuma tool busca por parte (o portal busca processos por nome e CPF).
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "stm-jurisprudencia"
for p in (MODULO, MODULO.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

_spec = importlib.util.spec_from_file_location("stm_server", MODULO / "server.py")
S = importlib.util.module_from_spec(_spec)
sys.modules["stm_server"] = S
_spec.loader.exec_module(S)

import stm_parser as P  # noqa: E402

FIX = RAIZ / "tests" / "fixtures" / "stm"
UUID = "e446475397c693c4670e2b922712967b"


def _fixture(nome: str) -> str:
    return (FIX / nome).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _sem_log_de_producao(monkeypatch):
    monkeypatch.setattr(S, "log_query", lambda **kw: None)


@pytest.fixture()
def portal(monkeypatch):
    """Dubla o transporte e o cache. `respostas` mapeia o `q` (busca) ou o
    `doc_type` (modal) ao HTML devolvido."""
    estado = {"respostas": {}, "chamadas": [], "gravados": []}

    def _get(url, params):
        estado["chamadas"].append((url, dict(params)))
        chave = params.get("doc_type") or params.get("q")
        resp = estado["respostas"].get(chave, "")
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(S.stm_client, "_get", _get)
    monkeypatch.setattr(S, "cached_http", lambda mcp, key: None)
    monkeypatch.setattr(S, "registrar_dispositivo",
                        lambda mcp, key, body, ttl_s: estado["gravados"].append(key))
    return estado


# ---------------- busca ----------------

def test_busca_devolve_id_e_total(portal):
    portal["respostas"]["deserção"] = _fixture("busca_3_paineis.html")
    out = S.buscar_jurisprudencia_stm("deserção", max_resultados=3)
    assert "total na base: 1386" in out
    assert out.count("<resultado ") == 3
    assert "<numero>7000019-48.2025.7.12.0012</numero>" in out
    assert "<revisor>VERÔNICA ABDALLA STERMAN</revisor>" in out
    assert out.count("<id>") == 3
    assert len(portal["gravados"]) == 1


def test_zero_com_controle_casando_avisa_e_nao_entra_no_cache(portal):
    portal["respostas"]["xqzwvkjhtt"] = _fixture("zero_ou_recusa.html")
    portal["respostas"]["deserção"] = _fixture("busca_3_paineis.html")
    out = S.buscar_jurisprudencia_stm("xqzwvkjhtt")
    assert '<jurisprudencia_stm total="0">' in out
    assert "ZERO RESULTADOS" in out and "consulta de controle" in out
    assert [c[1]["q"] for c in portal["chamadas"]] == ["xqzwvkjhtt", "deserção"]
    assert portal["gravados"] == []


def test_zero_com_controle_falhando_e_erro_nao_lista_vazia(portal):
    portal["respostas"]["xqzwvkjhtt"] = _fixture("zero_ou_recusa.html")
    portal["respostas"]["deserção"] = _fixture("zero_ou_recusa.html")
    out = S.buscar_jurisprudencia_stm("xqzwvkjhtt")
    assert out.startswith("<erro>")
    assert "não há como dizer se há precedente" in out
    assert "<resultado" not in out


def test_paineis_sem_contagem_e_erro_de_layout(portal):
    html = _fixture("busca_3_paineis.html").replace("documentos", "itens")
    portal["respostas"]["deserção"] = html
    out = S.buscar_jurisprudencia_stm("deserção")
    assert out.startswith("<erro>") and "layout" in out


def test_falha_de_rede_e_erro(portal):
    portal["respostas"]["deserção"] = S.stm_client.PortalSTMIndisponivel("HTTP 503")
    out = S.buscar_jurisprudencia_stm("deserção")
    assert out.startswith("<erro>") and "HTTP 503" in out


@pytest.mark.parametrize("kw", [
    {"busca": 'deserção e ("estado'},
    {"busca": "deserção", "ordem": "relevancia"},
    {"busca": "deserção", "data_inicio": "2026/13/01"},
])
def test_parametro_invalido_nao_sai(portal, kw):
    out = S.buscar_jurisprudencia_stm(**kw)
    assert out.startswith("<erro>Parâmetro inválido")
    assert portal["chamadas"] == []


def test_nao_sem_acento_e_corrigido_e_avisado(portal):
    portal["respostas"]["deserção não furto"] = _fixture("busca_3_paineis.html")
    out = S.buscar_jurisprudencia_stm("deserção nao furto")
    assert portal["chamadas"][0][1]["q"] == "deserção não furto"
    assert "OPERADOR CORRIGIDO" in out


def test_busca_correta_nao_ganha_aviso(portal):
    portal["respostas"]["deserção"] = _fixture("busca_3_paineis.html")
    assert "OPERADOR CORRIGIDO" not in S.buscar_jurisprudencia_stm("  deserção ")


def test_relatorio_traz_ementa_integral(portal):
    portal["respostas"]["deserção"] = _fixture("busca_3_paineis.html")
    out = S.relatorio_jurisprudencia_stm("deserção", max_resultados=3)
    _, docs = P.parsear_resultados(_fixture("busca_3_paineis.html"))
    for d in docs:
        assert d.ementa in out
        assert f"`{d.id}`" in out
    assert "## 2. APELAÇÃO CRIMINAL nº 7000019-48.2025.7.12.0012" in out


def test_relatorio_do_zero_avisa(portal):
    portal["respostas"]["xqzwvkjhtt"] = _fixture("zero_ou_recusa.html")
    portal["respostas"]["deserção"] = _fixture("busca_3_paineis.html")
    out = S.relatorio_jurisprudencia_stm("xqzwvkjhtt")
    assert "ZERO RESULTADOS" in out


# ---------------- inteiro teor ----------------

def _modais(portal):
    portal["respostas"].update({
        "inteiro_teor": _fixture("modal_inteiro_teor_trecho.html"),
        "referencia_legislativa": _fixture("modal_referencia_legislativa.html"),
        "notas": _fixture("modal_notas.html"),
        "thesaurus": _fixture("modal_thesaurus.html"),
    })


def test_inteiro_teor_com_metadados(portal):
    _modais(portal)
    out = S.inteiro_teor_stm(UUID)
    assert "APELAÇÃO CRIMINAL Nº 7000238-07.2024.7.02.0002/SP" in out
    for secao in ("## Referência legislativa", "## Notas", "## Indexação"):
        assert secao in out
    assert "[Fim do texto:" in out
    pedidos = [c[1]["doc_type"] for c in portal["chamadas"]]
    assert pedidos == ["inteiro_teor", "referencia_legislativa", "notas", "thesaurus"]
    assert all(c[1]["uuid"] == UUID for c in portal["chamadas"])


def test_inteiro_teor_paginado(portal):
    # O trecho da fixture tem menos que o mínimo de `max_caracteres` (2000).
    longo = "<br/>".join([_fixture("modal_inteiro_teor_trecho.html")] * 3)
    portal["respostas"]["inteiro_teor"] = longo
    out = S.inteiro_teor_stm(UUID, incluir_metadados=False, max_caracteres=2000)
    total = len(P.texto_modal(longo))
    assert f"Continuação: inteiro_teor_stm(id, inicio=2000)" in out
    assert f"de {total} caracteres" in out
    assert len(portal["chamadas"]) == 1


def test_inteiro_teor_vazio_e_erro(portal):
    out = S.inteiro_teor_stm(UUID)
    assert out.startswith("<erro>") and "id inexistente" in out
    assert portal["gravados"] == []


def test_inteiro_teor_id_invalido_nao_sai(portal):
    out = S.inteiro_teor_stm("7000019-48.2025.7.12.0012")
    assert out.startswith("<erro>Parâmetro inválido")
    assert portal["chamadas"] == []


# ---------------- superfície ----------------

def test_nenhuma_tool_busca_por_parte():
    tools = asyncio.run(S.mcp.list_tools())
    assert {t.name for t in tools} == {
        "buscar_jurisprudencia_stm", "relatorio_jurisprudencia_stm",
        "inteiro_teor_stm", "ajuda_sintaxe_stm",
    }
    for t in tools:
        nomes = set(t.inputSchema.get("properties", {}))
        assert not nomes & {"parte", "nome_parte", "cpf", "oab", "advogado"}, t.name


def test_o_cliente_nunca_pede_feitos():
    fonte = inspect.getsource(S.stm_client) + inspect.getsource(P)
    assert '"search_filter_option": "jurisprudencia"' in fonte
    assert '"feitos"' not in fonte
