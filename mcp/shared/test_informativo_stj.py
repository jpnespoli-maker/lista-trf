"""Travas da base INFJ do STJ — portal do Informativo de Jurisprudencia.

Sintoma de 01/10/2026: `buscar_jurisprudencia_stj(base="INFJ")` morria com
"SCON/CDP reportou 164 documento(s) mas o parser extraiu 0". A causa NAO era
seletor: no SCON, `pesquisar.jsp?b=INFJ` conta as notas mas renderiza a aba de
Acordaos com cada item reduzido a "Documento invalido: <n>". A base vive no
portal proprio do Informativo (processo.stj.jus.br), por HTTP, sem Cloudflare.

As fixtures sao RECORTES do HTML real capturado por `_pesquisar_informativo`
(a funcao de producao) em 01/10/2026: o bloco de paginacao (total) e as 8
primeiras notas da consulta "defensoria publica", com o markup original.
"""

import importlib.util
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[1]
_FIXTURES = Path(__file__).parent / "fixtures"


def _carregar_servidor():
    import sys

    if str(_RAIZ) not in sys.path:
        sys.path.insert(0, str(_RAIZ))
    spec = importlib.util.spec_from_file_location(
        "stj_server_infj", _RAIZ / "stj-jurisprudencia" / "server.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def srv():
    return _carregar_servidor()


@pytest.fixture(scope="module")
def html():
    return (_FIXTURES / "informativo_infj_defensoria.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def html_vazio():
    return (_FIXTURES / "informativo_infj_vazio.html").read_text(encoding="utf-8")


def _por_numero(res, trecho):
    return next(r for r in res if trecho in r.numero)


class TestParseInformativo:
    def test_total_das_notas_encontradas(self, srv, html):
        _res, total = srv._parse_informativo_html(html, 4000)
        assert total == 164

    def test_extrai_as_oito_notas(self, srv, html):
        res, _t = srv._parse_informativo_html(html, 4000)
        assert len(res) == 8

    def test_nenhuma_nota_sem_processo(self, srv, html):
        res, _t = srv._parse_informativo_html(html, 4000)
        assert all(r.numero for r in res)

    def test_campos_da_nota_identificada(self, srv, html):
        res, _t = srv._parse_informativo_html(html, 4000)
        alvo = _por_numero(res, "EAREsp 2.841.872-DF")
        assert alvo.numero == "EAREsp 2.841.872-DF"
        assert alvo.relator == "Ministro Luis Felipe Salomão"
        assert alvo.orgao == "CORTE ESPECIAL"
        assert alvo.data == "DJEN 17/8/2026"
        assert alvo.extra["julgamento"] == "5/8/2026"
        assert alvo.tipo == "Informativo de Jurisprudência n. 897"
        assert alvo.extra["data_edicao"] == "18 de agosto de 2026"
        assert alvo.extra["url"].startswith("https://processo.stj.jus.br/")
        assert "CNOT=" in alvo.extra["url"]

    def test_conteudo_e_o_destaque(self, srv, html):
        res, _t = srv._parse_informativo_html(html, 4000)
        alvo = res[0]
        assert alvo.conteudo.startswith("A habilitação da Defensoria Pública")
        assert alvo.extra["tema"].startswith("Defensoria Pública. Habilitação"), (
            "o realce da busca nao pode deixar espaco antes da pontuacao"
        )
        assert alvo.extra["ramo"] == "DIREITO PROCESSUAL CIVIL"

    def test_processo_em_segredo_mantem_relator(self, srv, html):
        res, _t = srv._parse_informativo_html(html, 4000)
        assert res[0].numero == "Processo em segredo de justiça"
        assert res[0].relator == "Ministra Nancy Andrighi"

    def test_edicao_extraordinaria_nao_vira_numero_comum(self, srv, html):
        """A extraordinaria tem numeracao propria (n. 33 != Informativo 33)."""
        res, _t = srv._parse_informativo_html(html, 4000)
        alvo = _por_numero(res, "AgRg no HC 1.050.739-SP")
        assert alvo.tipo == "Informativo de Jurisprudência - Edição Extraordinária n. 33"

    def test_orgao_herdado_da_nota_anterior_da_mesma_edicao(self, srv, html):
        """O cabecalho do orgao so vem na 1a nota do orgao; a 2a o herda."""
        res, _t = srv._parse_informativo_html(html, 4000)
        assert _por_numero(res, "AgRg no REsp 2.248.978-GO").orgao == "QUINTA TURMA"

    def test_sem_publicacao_data_fica_vazia_e_julgamento_sai(self, srv, html):
        res, _t = srv._parse_informativo_html(html, 4000)
        alvo = _por_numero(res, "REsp 2.172.497-SP")
        assert alvo.data == ""
        assert alvo.extra["julgamento"] == "12/8/2026"

    def test_pagina_sem_resultado_nao_quebra(self, srv, html_vazio):
        res, total = srv._parse_informativo_html(html_vazio, 400)
        assert res == [] and total == 0


class TestRotaInformativo:
    def test_vazio_nao_levanta(self, srv, html_vazio, monkeypatch):
        monkeypatch.setattr(srv, "_pesquisar_informativo", lambda *_a, **_k: html_vazio)
        saida, n = srv._rota_informativo("xqzwvkjh", 10, 400)
        assert n == 0
        assert "Total encontrado: 0" in saida

    def test_respeita_tamanho(self, srv, html, monkeypatch):
        monkeypatch.setattr(srv, "_pesquisar_informativo", lambda *_a, **_k: html)
        _saida, n = srv._rota_informativo("defensoria publica", 3, 400)
        assert n == 3

    def test_canario_total_sem_itens(self, srv, html, monkeypatch):
        quebrado = html.replace("clsInformativoBlocoItem", "classeQueMudou")
        monkeypatch.setattr(srv, "_pesquisar_informativo", lambda *_a, **_k: quebrado)
        with pytest.raises(RuntimeError, match="parser extraiu 0"):
            srv._rota_informativo("defensoria publica", 10, 400)

    def test_canario_item_oco(self, srv, html, monkeypatch):
        quebrado = html.replace("Processo\n", "Rotulo que mudou\n")
        monkeypatch.setattr(srv, "_pesquisar_informativo", lambda *_a, **_k: quebrado)
        with pytest.raises(RuntimeError, match="SEM processo"):
            srv._rota_informativo("defensoria publica", 10, 400)


class TestRoteamentoInfj:
    def test_infj_vai_ao_informativo_e_nao_ao_scon(self, srv, html, monkeypatch):
        def _proibido(*_a, **_k):
            raise AssertionError("INFJ nao pode ir ao SCON: la a base so tem 'Documento invalido'")

        monkeypatch.setattr(srv, "_rota_scon", _proibido)
        monkeypatch.setattr(srv, "_rota_scon_cdp", _proibido)
        monkeypatch.setattr(srv, "_pesquisar_informativo", lambda *_a, **_k: html)
        monkeypatch.setattr(srv, "cached_http", lambda *a, **k: None)
        monkeypatch.setattr(srv, "registrar_dispositivo", lambda *a, **k: None)
        monkeypatch.setattr(srv, "log_query", lambda *a, **k: None)
        saida = srv.buscar_jurisprudencia_stj("defensoria publica", base="INFJ", tamanho=5)
        assert "Informativo de Jurisprudência (processo.stj.jus.br)" in saida
        assert "EAREsp 2.841.872-DF" in saida

    def test_falha_do_portal_vira_erro_declarado(self, srv, monkeypatch):
        def _fora(*_a, **_k):
            raise ConnectionError("portal fora")

        monkeypatch.setattr(srv, "_pesquisar_informativo", _fora)
        monkeypatch.setattr(srv, "cached_http", lambda *a, **k: None)
        monkeypatch.setattr(srv, "log_query", lambda *a, **k: None)
        saida = srv.buscar_jurisprudencia_stj("defensoria publica", base="INFJ")
        assert saida.startswith("<erro>Base INFJ")
        assert "ConnectionError: portal fora" in saida
