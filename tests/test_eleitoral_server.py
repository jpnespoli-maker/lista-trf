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


@pytest.fixture(autouse=True)
def _sem_log_de_producao(monkeypatch):
    # O log_query grava em ~/.claude/DPU/logs/jurisprudencia_queries.jsonl, que o
    # analisar_queries.py consome: teste não escreve em estado compartilhado.
    monkeypatch.setattr(S, "log_query", lambda **kw: None)


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
    out = S._buscar_xml("abuso de poder")
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
    S._buscar_xml("x")
    assert portal["gravados"] and '"partes"' not in portal["gravados"][0]


def test_antirrobo_vira_erro_e_nao_cacheia(portal):
    portal["payload"] = {"mensagem": "Falha na verificação antirrobô.", "content": [],
                         "totalRegistros": 0, "aggs": []}
    out = S._buscar_xml("x")
    assert out.startswith("<erro>") and "antirrobô" in out
    assert portal["gravados"] == []


def test_token_que_nao_veio_vira_erro_orientador(portal):
    # Review Focus 3.
    portal["payload"] = P.ErroSJUR(S.sjur_client.MSG_SEM_ENVIO)
    out = S._buscar_xml("x")
    assert out.startswith("<erro>") and "desafio" in out


def test_sem_navegador_diz_que_nao_ha_alternativa(portal):
    portal["payload"] = CDPIndisponivel("porta 9222 não respondeu")
    out = S._buscar_xml("x")
    assert out.startswith("<erro>") and "CJF não indexa a Justiça Eleitoral" in out


@pytest.mark.parametrize("busca", ["", "   ", "x" * 501])
def test_busca_invalida_nem_abre_navegador(portal, busca):
    # Review Focus 4.
    out = S._buscar_xml(busca)
    assert out.startswith("<erro>Parâmetro inválido")
    assert portal["chamadas"] == []


@pytest.mark.parametrize("kw", [{"tribunal": "TRE-XX"}, {"tipo_decisao": "monocratica"},
                                {"ano_eleicao": "24"}, {"ordenacao": "data"}])
def test_parametros_invalidos(portal, kw):
    out = S._buscar_xml("x", **kw)
    assert out.startswith("<erro>Parâmetro inválido")
    assert portal["chamadas"] == []


def test_parametros_chegam_ao_cliente(portal):
    S._buscar_xml("x", tribunal="TRE-RJ", tipo_decisao="acordao",
                                      ano_eleicao="2024", max_resultados=500, pagina=2)
    _, alvo, kw = portal["chamadas"][0]
    assert alvo == P.Alvo("tres", ("TRE-RJ",))
    assert kw == {"tipos": ["Acórdão"], "ano_eleicao": 2024, "tamanho": 100, "pagina": 1,
                  "ordenacao": "re"}


def test_aviso_de_operador_minusculo_no_cabecalho(portal):
    # Review Focus 1.
    assert "MAIÚSCULO" in S._buscar_xml("abuso e poder")


def test_contagem_por_tribunal_nos_tres(portal):
    portal["payload"] = _fixture("busca_tres.json")
    out = S._buscar_xml("x", tribunal="TREs")
    sigla = next(iter(P.contagem_por_tribunal(portal["payload"])))
    assert "por tribunal" in out and sigla in out


def test_item_sem_cnj_marcado_no_xml_e_no_relatorio(portal):
    # Review Focus 2.
    portal["payload"] = {"mensagem": None, "totalRegistros": 1, "aggs": [],
                         "content": [{"siglaTribunalJE": "TRE-SP", "siglaClasse": "RE",
                                      "numeroProcesso": "123", "textoEmenta": "E."}]}
    assert "<sem_cnj>sim</sem_cnj>" in S._buscar_xml("x", tribunal="TREs")
    relatorio = S._relatorio_md("x", tribunal="TREs")
    assert "sem número CNJ" in relatorio
    assert "inteiro teor" in relatorio and "protocolo" not in relatorio  # I4: não é protocolo


def test_chave_do_cache_distingue_caixa_do_operador_e_aspas():
    # C1 — o cache compartilhado normaliza o campo `busca` (minúscula, sem aspas);
    # no SJUR a caixa do operador e as aspas mudam o resultado.
    from shared.cache_http import _hash_chave

    def chave(busca):
        return _hash_chave(S._chave_busca(S._preparar(busca, "TSE", "todos", "", "re", 10, 1)))

    assert chave("aije ou aime") != chave("AIJE OU AIME")
    assert chave('"abuso de poder"') != chave("abuso de poder")
    assert chave("contas NÃO partido") != chave("contas nao partido")
    assert chave("AIJE OU AIME") == chave("AIJE OU AIME")


def test_filtro_sem_efeito_vira_erro_e_nao_cacheia(portal):
    # I2 — pediu TRE-RJ e veio a resposta de todos os TREs.
    portal["payload"] = _fixture("busca_tres.json")
    out = S._buscar_xml("x", tribunal="TRE-RJ")
    assert out.startswith("<erro>") and "filtro" in out
    assert portal["gravados"] == []


def test_cache_corrompido_nao_vira_parametro_invalido(monkeypatch):
    # M2 — JSONDecodeError é ValueError, mas não é erro da consulta.
    monkeypatch.setattr(S, "cached_http", lambda mcp, key: "nao-e-json")
    out = S._buscar_xml("abuso")
    assert out.startswith("<erro>Falha na busca eleitoral")


def test_zero_no_xml_orienta_a_revisar_a_sintaxe(portal):
    # M4 — é a tool que o subagente mais usa; zero sem orientação vira "não há precedente".
    portal["payload"] = {"mensagem": None, "content": [], "totalRegistros": 0, "aggs": []}
    assert "revise a sintaxe" in S._buscar_xml("x")


def test_zero_legitimo(portal):
    portal["payload"] = {"mensagem": None, "content": [], "totalRegistros": 0, "aggs": []}
    assert '<jurisprudencia_eleitoral total="0">' in S._buscar_xml("x")
    assert "Nenhuma decisão encontrada" in S._relatorio_md("x")


def test_relatorio_traz_ementa_integra(portal):
    out = S._relatorio_md("x")
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
    out = S._inteiro_teor_txt("3606299", max_caracteres=5000)
    assert "VOTO DO RELATOR" in out and "3606299" in out


def test_inteiro_teor_erro(monkeypatch):
    monkeypatch.setattr(S, "cached_http", lambda mcp, key: None)

    def falha(codigo):
        raise P.ErroSJUR("limite de taxa do inteiro teor persistiu")

    monkeypatch.setattr(S.sjur_client, "baixar_inteiro_teor", falha)
    out = S._inteiro_teor_txt("1")
    assert out.startswith("<erro>") and "limite de taxa" in out


def test_ajuda_sintaxe():
    ajuda = S.ajuda_sintaxe_eleitoral()
    assert "MAIÚSCULO" in ajuda and "TRE-RJ" in ajuda and "persuasiv" in ajuda


def test_pagina_alem_do_fim_vira_parametro_invalido(portal):
    # M3 — o parser recebe página e tamanho da consulta.
    # 12 decisões em páginas de 10: a página 2 existe (itens 11-12); a 3, não.
    portal["payload"] = {"mensagem": None, "content": [], "totalRegistros": 12, "aggs": []}
    out = S._buscar_xml("x", pagina=3)
    assert out.startswith("<erro>Parâmetro inválido") and "além do fim" in out


def _pdf(*textos):
    import fitz
    doc = fitz.open()
    for t in textos:
        pagina = doc.new_page()
        if t:
            pagina.insert_text((72, 72), t)
    dados = doc.tobytes()
    doc.close()
    return dados


def test_inteiro_teor_avisa_paginas_sem_texto(monkeypatch):
    # M7 — voto com página digitalizada: o texto devolvido está incompleto.
    gravado = {}
    monkeypatch.setattr(S, "cached_http", lambda mcp, key: None)
    monkeypatch.setattr(S, "registrar_dispositivo",
                        lambda mcp, key, body, ttl_s: gravado.update(body=body))
    monkeypatch.setattr(S.sjur_client, "baixar_inteiro_teor", lambda codigo: _pdf("VOTO", "", "FIM"))
    out = S._inteiro_teor_txt("1")
    assert "INCOMPLETO" in out and "1 de 3 página(s)" in out and "digitalizadas): 2." in out
    # O aviso sobrevive ao cache.
    monkeypatch.setattr(S, "cached_http", lambda mcp, key: gravado["body"])
    monkeypatch.setattr(S.sjur_client, "baixar_inteiro_teor", lambda codigo: pytest.fail("baixou de novo"))
    assert "INCOMPLETO" in S._inteiro_teor_txt("1")


def test_inteiro_teor_completo_sem_aviso(monkeypatch):
    monkeypatch.setattr(S, "cached_http", lambda mcp, key: None)
    monkeypatch.setattr(S, "registrar_dispositivo", lambda *a, **k: None)
    monkeypatch.setattr(S.sjur_client, "baixar_inteiro_teor", lambda codigo: _pdf("VOTO", "FIM"))
    assert "INCOMPLETO" not in S._inteiro_teor_txt("1")


def test_tools_sao_async_e_rodam_em_paralelo(monkeypatch):
    # M9 — tool síncrona rodava direto no loop do FastMCP: duas buscas no mesmo
    # processo ficavam em fila (~11 s cada). Agora o trabalho vai a uma thread.
    import asyncio
    import time

    def lento(*args, **kw):
        time.sleep(0.3)
        return "ok"

    monkeypatch.setattr(S, "_buscar_xml", lento)

    async def tres_buscas():
        return await asyncio.gather(*(S.buscar_jurisprudencia_eleitoral("x") for _ in range(3)))

    t0 = time.perf_counter()
    assert asyncio.run(tres_buscas()) == ["ok", "ok", "ok"]
    assert time.perf_counter() - t0 < 0.75


def test_tool_async_repassa_os_parametros(monkeypatch):
    import asyncio

    recebidos = {}
    monkeypatch.setattr(S, "_buscar_xml", lambda *a: recebidos.update(args=a) or "ok")
    asyncio.run(S.buscar_jurisprudencia_eleitoral("abuso", tribunal="TRE-RJ", pagina=2))
    assert recebidos["args"] == ("abuso", "TRE-RJ", "todos", "", "re", 10, 2, 600)


def test_schema_das_tools_preserva_os_parametros():
    import asyncio

    tools = {t.name: t for t in asyncio.run(S.mcp.list_tools())}
    props = tools["buscar_jurisprudencia_eleitoral"].inputSchema["properties"]
    assert {"busca", "tribunal", "tipo_decisao", "ano_eleicao", "ordenacao",
            "max_resultados", "pagina", "max_tokens_ementa"} <= set(props)
    assert "codigo_decisao" in tools["inteiro_teor_eleitoral"].inputSchema["properties"]
