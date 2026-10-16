"""Tools do eleitoral-jurisprudencia: falha do portal vira <erro>, nunca lista
vazia; nada de `partes`; parâmetros inválidos não abrem o navegador."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "eleitoral-jurisprudencia"
for p in (MODULO, MODULO.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

_spec = importlib.util.spec_from_file_location("eleitoral_server", MODULO / "server.py")
S = importlib.util.module_from_spec(_spec)
# O @dataclass do servidor procura o módulo em sys.modules (anotações em
# string, `from __future__ import annotations`); sem o registro, a carga falha.
sys.modules["eleitoral_server"] = S
_spec.loader.exec_module(S)

from shared.cdp_edge import CDPIndisponivel  # noqa: E402
import sjur_parser as P  # noqa: E402

FIX = RAIZ / "tests" / "fixtures" / "eleitoral"


def _fixture(nome):
    return json.loads((FIX / nome).read_text(encoding="utf-8"))


@pytest.fixture()
def portal(monkeypatch):
    """Dubla o cliente e o cache; registra as chamadas."""
    estado = {"payload": _fixture("busca_tse.json"), "chamadas": [], "gravados": []}

    def pesquisar(busca, alvo, **kw):
        estado["chamadas"].append((busca, alvo, kw))
        if isinstance(estado["payload"], Exception):
            raise estado["payload"]
        return estado["payload"]

    monkeypatch.setattr(S.sjur_client, "pesquisar", pesquisar)
    monkeypatch.setattr(S, "cached_http", lambda mcp, key: None)
    monkeypatch.setattr(S, "registrar_dispositivo",
                        lambda mcp, key, body, ttl_s: estado["gravados"].append(body))
    return estado


def test_busca_tse_devolve_xml_com_cnj(portal):
    out = S.buscar_jurisprudencia_eleitoral("abuso de poder")
    primeiro = portal["payload"]["content"][0]
    assert out.count("<resultado ") == len(portal["payload"]["content"])
    assert f"<numero>{primeiro['numeroUnicoFormatado']}</numero>" in out
    assert f"<codigo_decisao>{primeiro['codigoDecisao']}</codigo_decisao>" in out
    assert "<partes" not in out  # a palavra "partes" pode estar na ementa; a TAG não
    assert "total na base" in out


def test_cache_nao_guarda_partes(portal):
    portal["payload"] = {"mensagem": None, "totalRegistros": 1, "aggs": [],
                         "content": [{"siglaTribunalJE": "TSE", "numeroUnicoFormatado": "1",
                                      "textoEmenta": "E.", "partes": [{"nome": "X"}]}]}
    S.buscar_jurisprudencia_eleitoral("x")
    assert portal["gravados"] and '"partes"' not in portal["gravados"][0]


def test_antirrobo_vira_erro_e_nao_cacheia(portal):
    portal["payload"] = {"mensagem": "Falha na verificação antirrobô.", "content": [],
                         "totalRegistros": 0, "aggs": []}
    out = S.buscar_jurisprudencia_eleitoral("x")
    assert out.startswith("<erro>") and "antirrobô" in out
    assert portal["gravados"] == []


def test_token_que_nao_veio_vira_erro_orientador(portal):
    # Review Focus 3.
    portal["payload"] = P.ErroSJUR(S.sjur_client.MSG_SEM_ENVIO)
    out = S.buscar_jurisprudencia_eleitoral("x")
    assert out.startswith("<erro>") and "desafio" in out


def test_sem_navegador_diz_que_nao_ha_alternativa(portal):
    portal["payload"] = CDPIndisponivel("porta 9222 não respondeu")
    out = S.buscar_jurisprudencia_eleitoral("x")
    assert out.startswith("<erro>") and "CJF não indexa a Justiça Eleitoral" in out


@pytest.mark.parametrize("busca", ["", "   ", "x" * 501])
def test_busca_invalida_nem_abre_navegador(portal, busca):
    # Review Focus 4.
    out = S.buscar_jurisprudencia_eleitoral(busca)
    assert out.startswith("<erro>Parâmetro inválido")
    assert portal["chamadas"] == []


@pytest.mark.parametrize("kw", [{"tribunal": "TRE-XX"}, {"tipo_decisao": "monocratica"},
                                {"ano_eleicao": "24"}, {"ordenacao": "data"}])
def test_parametros_invalidos(portal, kw):
    out = S.buscar_jurisprudencia_eleitoral("x", **kw)
    assert out.startswith("<erro>Parâmetro inválido")
    assert portal["chamadas"] == []


def test_parametros_chegam_ao_cliente(portal):
    S.buscar_jurisprudencia_eleitoral("x", tribunal="TRE-RJ", tipo_decisao="acordao",
                                      ano_eleicao="2024", max_resultados=500, pagina=2)
    _, alvo, kw = portal["chamadas"][0]
    assert alvo == P.Alvo("tres", ("TRE-RJ",))
    assert kw == {"tipos": ["Acórdão"], "ano_eleicao": 2024, "tamanho": 100, "pagina": 1,
                  "ordenacao": "re"}


def test_aviso_de_operador_minusculo_no_cabecalho(portal):
    # Review Focus 1.
    assert "MAIÚSCULO" in S.buscar_jurisprudencia_eleitoral("abuso e poder")


def test_contagem_por_tribunal_nos_tres(portal):
    portal["payload"] = _fixture("busca_tres.json")
    out = S.buscar_jurisprudencia_eleitoral("x", tribunal="TREs")
    sigla = next(iter(P.contagem_por_tribunal(portal["payload"])))
    assert "por tribunal" in out and sigla in out


def test_item_sem_cnj_marcado_no_xml_e_no_relatorio(portal):
    # Review Focus 2.
    portal["payload"] = {"mensagem": None, "totalRegistros": 1, "aggs": [],
                         "content": [{"siglaTribunalJE": "TRE-SP", "siglaClasse": "RE",
                                      "numeroProcesso": "123", "textoEmenta": "E."}]}
    assert "<sem_cnj>sim</sem_cnj>" in S.buscar_jurisprudencia_eleitoral("x", tribunal="TREs")
    assert "sem número CNJ" in S.relatorio_jurisprudencia_eleitoral("x", tribunal="TREs")


def test_zero_legitimo(portal):
    portal["payload"] = {"mensagem": None, "content": [], "totalRegistros": 0, "aggs": []}
    assert '<jurisprudencia_eleitoral total="0">' in S.buscar_jurisprudencia_eleitoral("x")
    assert "Nenhuma decisão encontrada" in S.relatorio_jurisprudencia_eleitoral("x")


def test_relatorio_traz_ementa_integra(portal):
    out = S.relatorio_jurisprudencia_eleitoral("x")
    ementa = P.interpretar_busca(portal["payload"])[0][0].ementa
    assert "### Ementa" in out and ementa in out


def test_inteiro_teor(monkeypatch):
    monkeypatch.setattr(S, "cached_http", lambda mcp, key: None)
    monkeypatch.setattr(S, "registrar_dispositivo", lambda *a, **k: None)
    import fitz
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "VOTO DO RELATOR")
    pdf = doc.tobytes()
    doc.close()
    monkeypatch.setattr(S.sjur_client, "baixar_inteiro_teor", lambda codigo: pdf)
    out = S.inteiro_teor_eleitoral("3606299", max_caracteres=5000)
    assert "VOTO DO RELATOR" in out and "3606299" in out


def test_inteiro_teor_erro(monkeypatch):
    monkeypatch.setattr(S, "cached_http", lambda mcp, key: None)

    def falha(codigo):
        raise P.ErroSJUR("limite de taxa do inteiro teor persistiu")

    monkeypatch.setattr(S.sjur_client, "baixar_inteiro_teor", falha)
    out = S.inteiro_teor_eleitoral("1")
    assert out.startswith("<erro>") and "limite de taxa" in out


def test_ajuda_sintaxe():
    ajuda = S.ajuda_sintaxe_eleitoral()
    assert "MAIÚSCULO" in ajuda and "TRE-RJ" in ajuda and "persuasiv" in ajuda
