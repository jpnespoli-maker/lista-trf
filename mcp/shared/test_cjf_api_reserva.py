"""A API nova do CJF como RESERVA dos clientes JSF (shared/cjf_api.py).

Medido em 22/10/2026: o jurisprudencia.cjf.jus.br virou SPA e os clientes JSF
passaram a antiga-jurisprudencia.cjf.jus.br. Quando esse host também virar SPA,
a busca segue pela API nova, sem intervenção. A API cobre STF e os seis TRFs; o
STJ é resíduo (2 contra 33.225 no portal antigo, "honorarios") e é RECUSADO,
nunca devolvido como zero. O STF vem sem data nem órgão.

Fixtures capturadas por `cjf_api.pesquisar` no mesmo dia e recortadas (sem
INTEIRO_TEOR, DECISAO a 300 caracteres, dois documentos): "repercussao geral"
em TRF2+STF; "bolsa familia" sem filtro (agregação de turma recursal da 1ª
Região) e filtrada pelos 24 órgãos dela. Jurisprudência pública.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

MCP = Path(__file__).resolve().parents[1]
if str(MCP) not in sys.path:
    sys.path.insert(0, str(MCP))

from shared import cjf_api, cjf_client, cjf_regional_client  # noqa: E402

_FIX = Path(__file__).parent / "fixtures"


def _ler(nome):
    return json.loads((_FIX / nome).read_text(encoding="utf-8"))


TRF2_STF = _ler("cjf_api_trf2_stf.json")
JEF1_BASE = _ler("cjf_api_jef1_base.json")
JEF1_ORGAOS = _ler("cjf_api_jef1_orgaos.json")
# 23/10/2026, "bolsa E familia E unipessoal" filtrada por TRF3+TRF4 com as
# turmas recursais das duas Regiões; três documentos, um de cada espécie.
TRF3_TRF4_COM_TR = _ler("cjf_api_trf3_trf4_com_tr.json")


class TestDocumento:
    def test_turma_recursal_com_data_e_orgao(self):
        d = cjf_api.documento(JEF1_ORGAOS["results"]["hits"][0]["_source"])
        assert d["numero"] == "0028857-21.2019.4.01.3300"
        assert d["orgao_julgador"] == "PRIMEIRA TURMA RECURSAL - BA"
        assert d["data_julgamento"] == "29/04/2022"
        assert d["tribunal"] == "JEF1" and d["via"] == "api-nova"
        assert "aviso" not in d

    def test_stf_degradado_vem_com_aviso(self):
        d = cjf_api.documento(TRF2_STF["results"]["hits"][0]["_source"])
        assert d["tribunal"] == "STF"
        assert d["numero"] == "672655"
        assert not d["ementa"].startswith("EMENTA")
        assert "data_julgamento" not in d and "orgao_julgador" not in d
        assert "STF" in d["aviso"]


class TestUnificada:
    def test_stj_e_recusado_com_os_cobertos(self, monkeypatch):
        monkeypatch.setattr(cjf_api, "pesquisar", lambda *a, **k: pytest.fail("não deveria buscar"))
        with pytest.raises(cjf_api.SemCoberturaNaApi) as exc:
            cjf_api.buscar_unificada("x", ["STF", "STJ", "TRF2"], 5)
        assert exc.value.sem_cobertura == ["STJ"]
        assert exc.value.cobertos == ["STF", "TRF2"]
        assert "stj-jurisprudencia" in str(exc.value)

    def test_totais_dos_baldes_sem_caixa(self, monkeypatch):
        chamadas = []
        monkeypatch.setattr(cjf_api, "pesquisar",
                            lambda q, f, n: (chamadas.append(f), TRF2_STF)[1])
        docs, totais = cjf_api.buscar_unificada("repercussao geral", ["trf2", "STF"], 2)
        assert chamadas == [{"TRIBUNAL": ["TRF2", "STF"], "ORIGEM": ["JEF:TR:2", "JEF:TRU:2"]}]
        assert totais == {"TRF2": 9266, "STF": 9324}
        assert [d["via"] for d in docs] == ["api-nova", "api-nova"]

    def test_trf_inclui_as_turmas_recursais_da_regiao(self, monkeypatch):
        """O filtro TRIBUNAL só alcança o 2º grau: sem ORIGEM, 43 dos 59 julgados
        do TRF3 nesta busca (os das turmas recursais de SP) sumiam, e o TRF4
        saía com zero. O portal antigo conta as turmas no próprio TRF."""
        chamadas = []
        monkeypatch.setattr(cjf_api, "pesquisar",
                            lambda q, f, n: (chamadas.append(f), TRF3_TRF4_COM_TR)[1])
        docs, totais = cjf_api.buscar_unificada("bolsa E familia E unipessoal", ["TRF3", "TRF4"], 3)
        assert chamadas == [{
            "TRIBUNAL": ["TRF3", "TRF4"],
            "ORIGEM": ["JEF:TR:3", "JEF:TRU:3", "JEF:TR:4", "JEF:TRU:4"],
        }]
        assert totais == {"TRF3": 59, "TRF4": 3}
        assert sum(totais.values()) == TRF3_TRF4_COM_TR["results"]["total"]["value"]
        assert any("TURMA RECURSAL" in d["orgao_julgador"] for d in docs)

    def test_stf_sozinho_nao_leva_origem(self, monkeypatch):
        chamadas = []
        monkeypatch.setattr(cjf_api, "pesquisar",
                            lambda q, f, n: (chamadas.append(f), TRF2_STF)[1])
        cjf_api.buscar_unificada("repercussao geral", ["STF"], 2)
        assert chamadas == [{"TRIBUNAL": ["STF"]}]

    def test_canario_total_sem_documentos(self, monkeypatch):
        oco = {**TRF2_STF, "results": {**TRF2_STF["results"], "hits": []}}
        monkeypatch.setattr(cjf_api, "pesquisar", lambda *a, **k: oco)
        with pytest.raises(RuntimeError, match="não tratar como 'nada encontrado'"):
            cjf_api.buscar_unificada("repercussao geral", ["TRF2"], 2)


class TestOperadorExplicito:
    """Palavras soltas iam como estavam, e a API, quando o E daria zero, passa
    a OU sem avisar: "bolsa familia unipessoal" no TRF4 devolvia 2.590 julgados
    contra 0 da busca com E (23/10/2026). Com o E explícito o zero chega ao
    servidor do MCP, que relaxa com o aviso de BUSCA RELAXADA."""

    @pytest.mark.parametrize("busca, enviada", [
        ("bolsa familia unipessoal", "bolsa E familia E unipessoal"),
        ("  bolsa   familia ", "bolsa E familia"),
        ("aposentad$ especial", "aposentad$ E especial"),
        ("auxílio-doença cessação", "auxílio-doença E cessação"),
        ("Tema 379", "Tema E 379"),
        # Minúsculo não é operador na API ("unipessoal e xyzzyqwe" relaxa como
        # sem operador); o "e" que sobra é neutro: 625 com ele e sem ele.
        ("pensao e morte", "pensao E e E morte"),
        ("unipessoal", "unipessoal"),
        ("", ""),
    ])
    def test_palavras_soltas_ganham_e(self, busca, enviada):
        assert cjf_api._e_explicito(busca) == enviada

    @pytest.mark.parametrize("busca", [
        "bolsa E familia",
        "bolsa OU familia",
        "bolsa ADJ familia unipessoal",
        "aposentadoria PROX3 invalidez",
        "servidor NAO militar",
        '"bolsa familia" unipessoal',
        "(bolsa familia) unipessoal",
        "pensao[EMEN] morte",
    ])
    def test_busca_com_operador_vai_intacta(self, busca):
        assert cjf_api._e_explicito(busca) == busca

    def test_pesquisar_envia_a_busca_com_e(self, monkeypatch):
        enviados = []
        resp = type("R", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"results": {}, "aggregations": {}},
        })()
        monkeypatch.setattr(cjf_api.requests, "post",
                            lambda url, json, timeout: (enviados.append(json), resp)[1])
        cjf_api.pesquisar("bolsa familia unipessoal", {}, 1)
        assert enviados[0]["query"] == "bolsa E familia E unipessoal"


class TestRegional:
    def test_jef1_filtra_pelos_orgaos_de_turma_recursal(self, monkeypatch):
        chamadas = []

        def _pesquisar(q, f, n):
            chamadas.append(f)
            return JEF1_BASE if len(chamadas) == 1 else JEF1_ORGAOS

        monkeypatch.setattr(cjf_api, "pesquisar", _pesquisar)
        docs, total = cjf_api.buscar_regional("bolsa familia", "JEF1", list(cjf_regional_client.TIPOS_DOCUMENTO), 2)
        assert total == 1502
        assert len(chamadas[1]["ORGAO_JULGADOR"]) == 24
        assert all(d["fonte"] == "JEF1" and "RECURSAL" in d["orgao_julgador"] for d in docs)

    @pytest.mark.parametrize("tipos, esperado", [
        (["ACORDAO", "SUMULA", "ARGUICAO", "DECISAOMONO"], {}),
        (["ACORDAO"], {"TIPO_DOCUMENTO": ["ACÓRDÃO"]}),
    ])
    def test_tipos(self, tipos, esperado):
        assert cjf_api._filtro_tipos(tipos) == esperado

    def test_tipo_sem_equivalente_e_recusado(self):
        with pytest.raises(RuntimeError, match="SUMULA"):
            cjf_api._filtro_tipos(["SUMULA"])


class _SessaoSpa:
    viewstate = None
    _fetched_at = 0.0

    def buscar(self, *_a, **_k):
        raise cjf_api.PortalJsfIndisponivel("SPA")


class TestDesvioAutomatico:
    def test_unificada_vai_a_api_quando_o_jsf_serve_spa(self, monkeypatch):
        monkeypatch.setattr(cjf_client, "get_cjf_session", lambda: _SessaoSpa())
        monkeypatch.setattr(cjf_api, "buscar_unificada", lambda t, tr, n: (["doc"], {"TRF2": 1}))
        assert cjf_client.buscar_documentos("x", ["TRF2"], 1) == (["doc"], {"TRF2": 1})

    def test_regional_vai_a_api_e_declara_a_via(self, monkeypatch):
        def _jsf(*_a, **_k):
            raise cjf_api.PortalJsfIndisponivel("SPA")

        monkeypatch.setattr(cjf_regional_client, "_buscar_uma_fonte", _jsf)
        monkeypatch.setattr(cjf_api, "buscar_regional",
                            lambda t, f, tp, n: ([{"numero": f, "ementa": f}], 7))
        docs, meta = cjf_regional_client.buscar_documentos("x", ["TRF1", "JEF1"], ["ACORDAO"], 4)
        assert meta["via"] == "api-nova" and meta["por_fonte"] == {"TRF1": 7, "JEF1": 7}

    @pytest.mark.parametrize("modulo, classe", [
        (cjf_client, "CJFSession"), (cjf_regional_client, "TRF1Session"),
    ])
    def test_spa_nao_se_tenta_de_novo(self, modulo, classe):
        """Página sem ViewState não se conserta repetindo: um GET só."""
        gets = []
        resp = type("R", (), {"text": "<html>SPA</html>", "raise_for_status": lambda self: None})()
        sessao = getattr(modulo, classe)()
        sessao.session.get = lambda *a, **k: (gets.append(1), resp)[1]
        with pytest.raises(cjf_api.PortalJsfIndisponivel):
            sessao.obter_viewstate()
        assert len(gets) == 1


@pytest.fixture
def srv_cjf(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "cjf_server_reserva", MCP / "cjf-jurisprudencia" / "server.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "cached_http", lambda *a, **k: None)
    monkeypatch.setattr(mod, "registrar_dispositivo", lambda *a, **k: None)
    monkeypatch.setattr(mod, "log_query", lambda **k: None)
    monkeypatch.setattr(mod, "relaxar_cjf", lambda q: None)
    return mod


def _duas_rotas(docs, antiga_ok=True, api_ok=True, sem_cobertura=()):
    from shared.cjf_duas_rotas import ResultadoDuasRotas, ResultadoRota

    return ResultadoDuasRotas(
        docs=docs,
        antiga=ResultadoRota(ok=antiga_ok, totais={"TRF1": 3}, erro=None if antiga_ok else "SPA"),
        api=ResultadoRota(ok=api_ok, totais={"TRF1": 3}, erro=None if api_ok else "HTTP 500",
                          sem_cobertura=list(sem_cobertura)),
        sobreposicao=0,
    )


def test_servidor_declara_o_stj_quando_a_api_nao_o_cobre(srv_cjf, monkeypatch):
    doc = {**cjf_api.documento(TRF2_STF["results"]["hits"][0]["_source"]), "origem": "api-nova"}
    chamadas = []

    def _duas(busca, tribs, n, **_k):
        chamadas.append(list(tribs))
        return _duas_rotas([doc], sem_cobertura=["STJ"])

    monkeypatch.setattr(srv_cjf.cjf_duas_rotas, "buscar_duas_rotas", _duas)
    saida = srv_cjf.buscar_jurisprudencia_cjf("bolsa familia", tribunais="STJ,STF")
    assert chamadas == [["STJ", "STF"]]
    assert "COBERTURA" in saida and "STJ" in saida
    assert "FONTE: API nova do CJF" in saida
    assert "<erro>" not in saida


def test_servidor_so_stj_com_portal_antigo_fora_e_erro_declarado(srv_cjf, monkeypatch):
    monkeypatch.setattr(
        srv_cjf.cjf_duas_rotas, "buscar_duas_rotas",
        lambda *_a, **_k: _duas_rotas([], antiga_ok=False, sem_cobertura=["STJ"]),
    )
    saida = srv_cjf.buscar_jurisprudencia_cjf("honorarios", tribunais="STJ")
    assert saida.startswith("<erro>") and "STJ" in saida and "SPA" in saida


def test_servidor_so_stj_com_portal_antigo_no_ar_traz_docs_e_avisa(srv_cjf, monkeypatch):
    doc = {"numero": "REsp", "ementa": "e", "origem": "portal-antigo", "orgao_julgador": "T1"}
    monkeypatch.setattr(
        srv_cjf.cjf_duas_rotas, "buscar_duas_rotas",
        lambda *_a, **_k: _duas_rotas([doc], sem_cobertura=["STJ"]),
    )
    saida = srv_cjf.buscar_jurisprudencia_cjf("honorarios", tribunais="STJ")
    assert "<erro>" not in saida and "COBERTURA" in saida and "STJ" in saida
