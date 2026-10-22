"""Tools do stf-jurisprudencia com a API dublada no transporte (``requests.Session``).

O que se trava (todos os defeitos medidos contra a API real em 2026-10-22):
(a) a base vai no CORPO, como filtro do campo ``base`` — o ``?base=`` da URL é
ignorado pela API, e as quatro bases devolviam o mesmo conjunto (só decisões
monocráticas); (b) a busca alcança o texto principal de cada base (ementa,
súmula, informativo, tese) — sem isso acórdãos e informativos davam zero;
(c) o conteúdo do acórdão é a EMENTA, não a indexação, e o rótulo ``<base>``
é o do documento; (d) HTTP 400 é erro de SINTAXE, sem retry nem navegador, e
não "acesso-bloqueado"; (e) a barra (``Lei 8.742/93``) não abre regex.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "stf-jurisprudencia"
for p in (MODULO, MODULO.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

_spec = importlib.util.spec_from_file_location("stf_server", MODULO / "server.py")
S = importlib.util.module_from_spec(_spec)
sys.modules["stf_server"] = S
_spec.loader.exec_module(S)


ACORDAO = {
    "base": "acordaos",
    "titulo": "RE 567985",
    "processo_codigo_completo": "RE 567985",
    "processo_classe_processual_unificada_extenso": "RECURSO EXTRAORDINÁRIO",
    "orgao_julgador": "Tribunal Pleno",
    "relator_processo_nome": "MARCO AURÉLIO",
    "relator_acordao_nome": "GILMAR MENDES",
    "julgamento_data": "2013-04-18",
    "publicacao_data": "2013-10-03",
    "ementa_texto": "Benefício assistencial de prestação continuada ao idoso e ao deficiente.",
    "documental_indexacao_texto": "- VIDE EMENTA. FUNDAMENTAÇÃO COMPLEMENTAR.",
    "documental_tese_tema_texto": "Tema 27 - Meios de comprovação do estado de miserabilidade.",
    "documental_tese_texto": "É inconstitucional o § 3º do art. 20 da Lei 8.742/1993.",
    "inteiro_teor_url": "https://portal.stf.jus.br/jurisprudencia/obterInteiroTeor.asp?idDocumento=1",
}

SUMULA = {
    "base": "sumulas",
    "titulo": "Súmula vinculante 1",
    "sumula_numero": 1,
    "is_vinculante": True,
    "situacao_sumula": "Aprovada",
    "sumula_texto": "Ofende a garantia constitucional do ato jurídico perfeito...",
    "julgamento_data": "2007-05-30",
    "publicacao_data": "2007-06-06",
    "documental_indexacao_texto": "INDEXAÇÃO QUE NÃO É O ENUNCIADO",
}

DECISAO = {
    "base": "decisoes",
    "titulo": "ARE 1585427",
    "processo_codigo_completo": "ARE 1585427",
    "processo_classe_processual_unificada_extenso": "RECURSO EXTRAORDINÁRIO COM AGRAVO",
    "relator_processo_nome": "DIAS TOFFOLI",
    "julgamento_data": "2026-02-06",
    "publicacao_data": "2026-02-09",
    "decisao_texto": "DECISÃO: Vistos. Trata-se de agravo.",
}


NOVO_INFORMATIVO = {
    "base": "novo_informativo",
    "titulo": "RE 1234567",
    "processo_numero": 1234567,
    "orgao_julgador": "Tribunal Pleno",
    "relator_processo_nome": "GILMAR MENDES",
    "relator_acordao_nome": None,
    "julgamento_data": "2025-03-10",
    "resumo_noticia": "É constitucional a exigência de comprovação da renda familiar.",
    "documental_tese_tema_texto": 1234,
    "documental_tese_texto": None,
}


def _resposta(*fontes: dict, total: int | None = None) -> dict:
    return {"result": {"hits": {
        "total": {"value": len(fontes) if total is None else total, "relation": "eq"},
        "hits": [{"_source": f} for f in fontes],
    }}}


class _Resp:
    def __init__(self, status: int, corpo: dict | str):
        self.status_code = status
        self.text = corpo if isinstance(corpo, str) else json.dumps(corpo)
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise S.requests.HTTPError(f"{self.status_code} Client Error")


@pytest.fixture(autouse=True)
def _sem_log_de_producao(monkeypatch):
    monkeypatch.setattr(S, "log_query", lambda **kw: None)


@pytest.fixture()
def api(monkeypatch):
    """Dubla a sessão HTTP, o navegador e o cache. ``resposta`` é o que o POST devolve."""
    estado = {"resposta": _Resp(200, _resposta()), "posts": [], "playwright": 0,
              "lidos": [], "gravados": []}

    class _Sessao:
        def __init__(self):
            self.headers = {}

        def post(self, url, json=None, timeout=None):
            estado["posts"].append((url, json))
            return estado["resposta"]

    async def _playwright(query, base, tamanho):
        estado["playwright"] += 1
        raise RuntimeError("navegador nao deveria subir")

    monkeypatch.setattr(S.requests, "Session", _Sessao)
    monkeypatch.setattr(S, "_buscar_via_playwright", _playwright)
    monkeypatch.setattr(S, "cached_http", lambda mcp, key: estado["lidos"].append(key))
    monkeypatch.setattr(S, "registrar_dispositivo",
                        lambda mcp, key, body, ttl_s: estado["gravados"].append(key))
    return estado


def _buscar(**kw) -> str:
    return asyncio.run(S.buscar_jurisprudencia_stf(**kw))


# ---------------- corpo da busca ----------------

@pytest.mark.parametrize("base, valores", [
    ("acordaos", ["acordaos"]),
    ("sumulas", ["sumulas"]),
    ("decisoes-monocraticas", ["decisoes"]),
    ("informativos", ["informativos", "novo_informativo"]),
])
def test_base_vai_no_corpo_como_filtro_do_campo_base(api, base, valores):
    _buscar(query="miserabilidade", base=base)
    corpo = api["posts"][0][1]
    assert corpo["query"]["bool"]["filter"] == [{"terms": {"base": valores}}]


def test_busca_alcanca_o_texto_principal_de_cada_base():
    campos = S._payload("x", 0, 1, "acordaos")["query"]["bool"]["must"][0]["query_string"]["fields"]
    for campo in ("ementa_texto", "sumula_texto", "conteudo_texto", "documental_tese_texto",
                  "decisao_texto", "resumo_noticia"):
        assert campo in campos


def test_numero_do_processo_no_titulo_pesa_mais():
    # Sem o peso, "RE 567985" trazia antes os agravos que só CITAM o RE.
    campos = S._payload("x", 0, 1, "acordaos")["query"]["bool"]["must"][0]["query_string"]["fields"]
    assert "titulo^5" in campos and "titulo" not in campos


def test_barra_nao_abre_regex():
    q = S._payload("Lei 8.742/93", 0, 1, "acordaos")["query"]["bool"]["must"][0]["query_string"]["query"]
    assert q == "Lei 8.742\\/93"
    ja_escapada = S._payload("Lei 8.742\\/93", 0, 1, "acordaos")
    assert ja_escapada["query"]["bool"]["must"][0]["query_string"]["query"] == "Lei 8.742\\/93"


def test_total_e_exato():
    assert S._payload("x", 0, 1, "acordaos")["track_total_hits"] is True


def test_alias_decisoes_e_aceito(api):
    out = _buscar(query="x", base="decisoes")
    assert "<erro" not in out
    assert api["posts"][0][1]["query"]["bool"]["filter"] == [{"terms": {"base": ["decisoes"]}}]


def test_base_invalida_e_erro_e_nao_cai_calada_em_acordaos(api):
    out = _buscar(query="x", base="repercussao-geral")
    assert '<erro tipo="base-invalida"' in out
    assert "acordaos" in out and "sumulas" in out
    assert api["posts"] == []


def test_chave_de_cache_e_versionada_para_nao_servir_o_cache_sem_filtro(api):
    _buscar(query="x", base="acordaos")
    assert api["lidos"][0].get("v") == S._CACHE_VERSAO >= 2


# ---------------- erro de sintaxe ----------------

def test_http_400_e_erro_de_sintaxe_sem_retry_nem_navegador(api):
    api["resposta"] = _Resp(400, {"detail": "RequestError(400, 'search_phase_execution_exception', "
                                            "'Failed to parse query [\"frase sem fecho]')"})
    out = _buscar(query='"frase sem fecho')
    assert '<erro tipo="sintaxe"' in out
    assert "acesso-bloqueado" not in out
    assert "Failed to parse query" in out
    assert len(api["posts"]) == 1
    assert api["playwright"] == 0
    assert api["gravados"] == []


# ---------------- resultado ----------------

def test_acordao_traz_ementa_e_nao_indexacao(api):
    api["resposta"] = _Resp(200, _resposta(ACORDAO))
    out = _buscar(query="miserabilidade", base="acordaos")
    assert "<conteudo>Benefício assistencial de prestação continuada" in out
    assert "VIDE EMENTA" not in out
    assert "<orgao>Tribunal Pleno</orgao>" in out
    assert "<relator>MARCO AURÉLIO</relator>" in out
    assert "<redator_acordao>GILMAR MENDES</redator_acordao>" in out
    assert "<data_julgamento>2013-04-18</data_julgamento>" in out
    assert "<data_publicacao>2013-10-03</data_publicacao>" in out
    assert "<tema>Tema 27 - Meios de comprovação" in out
    assert "<tese>É inconstitucional o § 3º" in out
    assert "<base>acordaos</base>" in out


def test_redator_igual_ao_relator_nao_se_repete(api):
    api["resposta"] = _Resp(200, _resposta({**ACORDAO, "relator_acordao_nome": "MARCO AURÉLIO"}))
    assert "<redator_acordao>" not in _buscar(query="x")


def test_sumula_traz_enunciado_numero_e_situacao(api):
    api["resposta"] = _Resp(200, _resposta(SUMULA))
    out = _buscar(query="ato jurídico perfeito", base="sumulas")
    assert "<numero>Súmula vinculante 1</numero>" in out
    assert "<tipo>Súmula Vinculante</tipo>" in out
    assert "<situacao>Aprovada</situacao>" in out
    assert "<conteudo>Ofende a garantia constitucional" in out
    assert "INDEXAÇÃO QUE NÃO É O ENUNCIADO" not in out


def test_informativo_novo_com_campos_numericos_nao_quebra(api):
    # Na série novo_informativo o tema e o número do processo vêm como int.
    api["resposta"] = _Resp(200, _resposta(NOVO_INFORMATIVO))
    out = _buscar(query="renda familiar", base="informativos")
    assert "<tipo>Informativo</tipo>" in out
    assert "<numero>RE 1234567</numero>" in out
    assert "<tema>Tema 1234</tema>" in out
    assert "<conteudo>É constitucional a exigência" in out
    assert "<base>novo_informativo</base>" in out
    assert "<redator_acordao>" not in out and "<tese>" not in out


def test_rotulo_base_e_o_do_documento_e_nao_o_pedido(api):
    api["resposta"] = _Resp(200, _resposta(DECISAO))
    out = _buscar(query="x", base="acordaos")
    assert "<base>decisoes</base>" in out
    assert "<base>acordaos</base>" not in out
    assert "<conteudo>DECISÃO: Vistos." in out


def test_zero_resultados_escapa_a_query_no_xml(api):
    out = _buscar(query="a < b & c")
    assert "a &lt; b &amp; c" in out
    assert "a < b & c" not in out


# ---------------- ao vivo ----------------

@pytest.mark.rede
@pytest.mark.parametrize("base", list(S.BASES_INDICE))
def test_rede_cada_base_devolve_so_documentos_dela(base):
    data = S._buscar_via_requests("constitucional", base, 10)
    docs = data["result"]["hits"]["hits"]
    assert docs, f"base {base} sem resultado para 'constitucional'"
    assert {d["_source"]["base"] for d in docs} <= set(S.BASES_INDICE[base])


@pytest.mark.rede
def test_rede_acordao_alcancavel_e_com_ementa(monkeypatch):
    monkeypatch.setattr(S, "cached_http", lambda mcp, key: None)
    monkeypatch.setattr(S, "registrar_dispositivo", lambda *a, **k: None)
    out = asyncio.run(S.buscar_jurisprudencia_stf("miserabilidade", base="acordaos", tamanho=3))
    assert 'rota="api-direta"' in out or 'rota="playwright-fallback"' in out
    assert "<base>acordaos</base>" in out
    assert "<base>decisoes</base>" not in out


@pytest.mark.rede
@pytest.mark.parametrize("base, query", [
    ("acordaos", "RE 567985"),
    ("sumulas", "ato jurídico perfeito"),
    ("decisoes-monocraticas", "HC 184828"),
    ("informativos", "prestação continuada"),
])
def test_rede_tool_formata_cada_base_sem_quebrar(monkeypatch, base, query):
    monkeypatch.setattr(S, "cached_http", lambda mcp, key: None)
    monkeypatch.setattr(S, "registrar_dispositivo", lambda *a, **k: None)
    out = asyncio.run(S.buscar_jurisprudencia_stf(query, base=base, tamanho=10))
    assert "<erro" not in out
    assert "<resultado " in out


@pytest.mark.rede
def test_rede_busca_por_numero_traz_o_proprio_processo_primeiro(monkeypatch):
    monkeypatch.setattr(S, "cached_http", lambda mcp, key: None)
    monkeypatch.setattr(S, "registrar_dispositivo", lambda *a, **k: None)
    out = asyncio.run(S.buscar_jurisprudencia_stf("RE 567985", base="acordaos", tamanho=3))
    assert out.split("</resultado>")[0].count("<numero>RE 567985</numero>") == 1


@pytest.mark.rede
def test_rede_barra_em_numero_de_lei_nao_e_erro(monkeypatch):
    monkeypatch.setattr(S, "cached_http", lambda mcp, key: None)
    monkeypatch.setattr(S, "registrar_dispositivo", lambda *a, **k: None)
    out = asyncio.run(S.buscar_jurisprudencia_stf("Lei 8.742/93", base="acordaos", tamanho=1))
    assert "<erro" not in out
