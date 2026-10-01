"""Travas da base SUMU (sumulas) do STJ — SCON pelo host processo.stj.jus.br.

Sintoma de 01/10/2026: `buscar_jurisprudencia_stj(base="SUMU")` morria com
"SCON: HTTP 403 | SCON/CDP: DesafioCloudflare". O Cloudflare bloqueia
scon.stj.jus.br, mas o MESMO aplicativo SCON responde em processo.stj.jus.br
sem desafio, e a pesquisa por querystring funciona ali. A lista de sumulas
tem layout PROPRIO (`.gridSumula`), que o parser dos acordaos nao le.

As fixtures foram capturadas por `_pesquisar_scon` (a funcao de producao) no
host processo.stj.jus.br em 01/10/2026 e recortadas (criterio da pesquisa +
`#listaSumulas`, ou o bloco de aviso da busca vazia): "impenhorabilidade"
inteira (9 sumulas), "cancelada" reduzida a 4 sumulas escolhidas pelos casos de
borda, "defensoria" inteira (a 421 cancelada, marcada por `.clsINDE`/`.clsCOM`),
`pulsos ou anuidade ou "vencidas apos a sentenca"` inteira (notas inline de
revogacao e de modificacao de texto; data sem zero a esquerda), "sumula e
mutuante" inteira (texto solto, sem `.clsVerbete`, com a 603 cancelada em
`.clsCOM`) e uma busca sem resultado. Pagina publica, sem dado pessoal.

O MARKUP VARIA COM O REALCE DA BUSCA: o mesmo verbete vem em `.clsVerbete` ou
em texto solto, conforme o termo caia nele. O parser foi conferido sobre as 676
sumulas (todas extraidas, nenhuma com a fonte ou a nota dentro do verbete, 29
canceladas + 1 revogada); as fixtures guardam um exemplar de cada forma.
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
        "stj_server_sumu", _RAIZ / "stj-jurisprudencia" / "server.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def srv():
    return _carregar_servidor()


def _ler(nome):
    return (_FIXTURES / nome).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def html():
    return _ler("scon_sumu_impenhorabilidade.html")


@pytest.fixture(scope="module")
def html_cancelada():
    return _ler("scon_sumu_cancelada.html")


@pytest.fixture(scope="module")
def html_vazio():
    return _ler("scon_sumu_vazio.html")


@pytest.fixture(scope="module")
def html_defensoria():
    return _ler("scon_sumu_defensoria.html")


@pytest.fixture(scope="module")
def html_notas():
    return _ler("scon_sumu_notas.html")


@pytest.fixture(scope="module")
def html_texto_solto():
    return _ler("scon_sumu_texto_solto.html")


def _por_numero(res, numero):
    return next(r for r in res if r.numero == f"Súmula {numero}")


def _pesquisa_dublada(monkeypatch, srv, respostas, chamadas):
    """Dubla `_pesquisar_scon` por host: `respostas[host]` e' HTML ou Exception."""

    def _fake(_sess, _query, _base, _tamanho=10, scon_base=srv.SCON_BASE):
        host = srv._host_scon(scon_base)
        chamadas.append(host)
        resp = respostas[host]
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(srv, "_pesquisar_scon", _fake)


def _sem_cache_nem_log(monkeypatch, srv):
    monkeypatch.setattr(srv, "cached_http", lambda *a, **k: None)
    monkeypatch.setattr(srv, "registrar_dispositivo", lambda *a, **k: None)
    monkeypatch.setattr(srv, "log_query", lambda *a, **k: None)


class TestParseSumulas:
    def test_total(self, srv, html):
        _res, total = srv._parse_sumulas_html(html, 4000)
        assert total == 9

    def test_extrai_as_nove(self, srv, html):
        res, _t = srv._parse_sumulas_html(html, 4000)
        assert [r.numero for r in res] == [
            f"Súmula {n}" for n in (486, 451, 449, 406, 364, 339, 328, 251, 205)
        ]

    def test_campos_da_sumula_486(self, srv, html):
        res, _t = srv._parse_sumulas_html(html, 4000)
        alvo = _por_numero(res, 486)
        assert alvo.conteudo == (
            "É impenhorável o único imóvel residencial do devedor que esteja "
            "locado a terceiros, desde que a renda obtida com a locação seja "
            "revertida para a subsistência ou a moradia da sua família."
        )
        assert alvo.tipo == "Súmula"
        assert alvo.orgao == "CORTE ESPECIAL"
        assert alvo.data == "DJe 01/08/2012"
        assert alvo.extra["julgamento"] == "28/06/2012"
        assert alvo.extra["ramo"] == "DIREITO PROCESSUAL CIVIL - BEM DE FAMÍLIA"
        assert "situacao" not in alvo.extra

    def test_realce_da_busca_nao_parte_o_verbete(self, srv, html):
        """`impenhorabilidade` vem dentro de span.highlightBrs na 364."""
        res, _t = srv._parse_sumulas_html(html, 4000)
        assert "O conceito de impenhorabilidade de bem de família" in _por_numero(res, 364).conteudo

    def test_cancelada_vai_ao_tipo_e_a_situacao(self, srv, html_cancelada):
        res, _t = srv._parse_sumulas_html(html_cancelada, 4000)
        alvo = _por_numero(res, 603)
        assert alvo.tipo == "Súmula (CANCELADA)"
        assert alvo.extra["situacao"].startswith("CANCELADA — A Segunda Seção")
        assert "REsp 1.555.722/SP" in alvo.extra["situacao"]
        assert "CANCELADA" not in alvo.conteudo, "a nota do cancelamento nao entra no verbete"
        assert alvo.data == "DJe 26/02/2018", "a data e' a da sumula, nao a do cancelamento"

    def test_vigente_na_busca_por_cancelada_nao_vira_cancelada(self, srv, html_cancelada):
        res, _t = srv._parse_sumulas_html(html_cancelada, 4000)
        alvo = _por_numero(res, 308)
        assert alvo.tipo == "Súmula"
        assert "situacao" not in alvo.extra

    def test_sumula_antiga_sem_clsverbete(self, srv, html_cancelada):
        """A 152 vem em texto solto, com a fonte "(SÚMULA 152, PRIMEIRA SEÇÃO, DJ ...)"."""
        res, _t = srv._parse_sumulas_html(html_cancelada, 4000)
        alvo = _por_numero(res, 152)
        assert alvo.conteudo == "Na venda pelo segurador, de bens salvados de sinistros, incide o ICMS."
        assert alvo.orgao == "PRIMEIRA SEÇÃO"
        assert alvo.data == "DJ 14/03/1996"
        assert alvo.tipo == "Súmula (CANCELADA)"

    def test_orgao_sem_marcacao(self, srv, html_cancelada):
        res, _t = srv._parse_sumulas_html(html_cancelada, 4000)
        assert _por_numero(res, 222).orgao == "SEGUNDA SEÇÃO"

    def test_pagina_sem_resultado_nao_quebra(self, srv, html_vazio):
        res, total = srv._parse_sumulas_html(html_vazio, 400)
        assert res == [] and total == 0

    def test_cancelada_marcada_por_clsinde_com_nota_em_clscom(self, srv, html_defensoria):
        """O modo de falha visto ao vivo: a 421 saia como vigente na busca "defensoria"."""
        res, total = srv._parse_sumulas_html(html_defensoria, 4000)
        assert total == 5 and len(res) == 5
        alvo = _por_numero(res, 421)
        assert alvo.tipo == "Súmula (CANCELADA)"
        assert "REsp 1.108.013/RJ" in alvo.extra["situacao"]
        assert alvo.conteudo == (
            "Os honorários advocatícios não são devidos à Defensoria Pública quando "
            "ela atua contra a pessoa jurídica de direito público à qual pertença."
        )
        assert [r.numero for r in res if r.tipo != "Súmula"] == ["Súmula 421"]

    def test_revogada_fica_fora_de_vigor(self, srv, html_notas):
        res, _t = srv._parse_sumulas_html(html_notas, 4000)
        alvo = _por_numero(res, 357)
        assert alvo.tipo == "Súmula (REVOGADA)"
        assert alvo.extra["situacao"].startswith("REVOGADA — A Primeira Seção")
        assert "REVOGAÇÃO" not in alvo.conteudo

    def test_redacao_modificada_traz_o_texto_vigente(self, srv, html_notas):
        res, _t = srv._parse_sumulas_html(html_notas, 4000)
        alvo = _por_numero(res, 111)
        assert alvo.tipo == "Súmula"
        assert alvo.conteudo == (
            "Os honorários advocatícios, nas ações previdenciárias, não incidem "
            "sobre as prestações vencidas após a sentença."
        )
        assert alvo.extra["observacao"].startswith("MODIFICAÇÃO DE TEXTO:")
        assert "REDAÇÃO ANTERIOR" in alvo.extra["observacao"]

    def test_texto_solto_com_nota_em_clscom(self, srv, html_texto_solto):
        """Busca "sumula e mutuante": nenhum `.clsVerbete`; a 603 traz `.clsINDE` e `.clsCOM`.
        A nota tem de sair ANTES de cortar a fonte final, senao fica no verbete."""
        res, total = srv._parse_sumulas_html(html_texto_solto, 4000)
        assert total == 6 and len(res) == 6
        assert all("(SÚMULA" not in r.conteudo and "julgado em" not in r.conteudo for r in res)
        alvo = _por_numero(res, 603)
        assert alvo.tipo == "Súmula (CANCELADA)"
        assert "REsp 1.555.722/SP" in alvo.extra["situacao"]
        assert alvo.conteudo.endswith("possui regramento legal específico e admite a retenção de percentual.")
        assert "CANCELAMENTO" not in alvo.conteudo
        assert alvo.orgao == "SEGUNDA SEÇÃO"
        assert alvo.data == "DJe 26/02/2018"

    def test_data_sem_zero_a_esquerda(self, srv, html_notas):
        res, _t = srv._parse_sumulas_html(html_notas, 4000)
        alvo = _por_numero(res, 673)
        assert alvo.data == "DJe 16/9/2024"
        assert alvo.extra["julgamento"] == "11/9/2024"


class TestRotaScon:
    def test_tenta_processo_primeiro_e_declara_o_host(self, srv, html, monkeypatch):
        chamadas = []
        _pesquisa_dublada(monkeypatch, srv, {"processo.stj.jus.br": html}, chamadas)
        saida, n, host = srv._rota_scon_http("impenhorabilidade", "SUMU", 10, 400)
        assert chamadas == ["processo.stj.jus.br"]
        assert host == "processo.stj.jus.br"
        assert n == 9
        assert saida.startswith(
            "<!-- STJ/SCON (processo.stj.jus.br) | Base: Súmulas | Total encontrado: 9 | Exibindo: 9 -->"
        )
        assert "<numero>Súmula 486</numero>" in saida

    def test_processo_falhando_cai_no_scon(self, srv, html, monkeypatch):
        chamadas = []
        _pesquisa_dublada(
            monkeypatch, srv,
            {"processo.stj.jus.br": ConnectionError("fora"), "scon.stj.jus.br": html},
            chamadas,
        )
        saida, _n, host = srv._rota_scon_http("impenhorabilidade", "SUMU", 10, 400)
        assert chamadas == ["processo.stj.jus.br", "scon.stj.jus.br"]
        assert host == "scon.stj.jus.br"
        assert "STJ/SCON (scon.stj.jus.br)" in saida

    def test_todos_falhando_levanta_com_cada_host(self, srv, monkeypatch):
        _pesquisa_dublada(
            monkeypatch, srv,
            {"processo.stj.jus.br": ConnectionError("fora"),
             "scon.stj.jus.br": RuntimeError("HTTP 403")},
            [],
        )
        with pytest.raises(RuntimeError) as exc:
            srv._rota_scon_http("impenhorabilidade", "SUMU", 10, 400)
        assert "processo.stj.jus.br: ConnectionError: fora" in str(exc.value)
        assert "scon.stj.jus.br: RuntimeError: HTTP 403" in str(exc.value)

    def test_vazio_nao_levanta(self, srv, html_vazio, monkeypatch):
        _pesquisa_dublada(monkeypatch, srv, {"processo.stj.jus.br": html_vazio}, [])
        saida, n = srv._rota_scon("xqzwvkjhqq", "SUMU", 10, 400, scon_base=srv.SCON_PROCESSO_BASE)
        assert n == 0
        assert "Total encontrado: 0" in saida

    def test_respeita_tamanho(self, srv, html, monkeypatch):
        _pesquisa_dublada(monkeypatch, srv, {"processo.stj.jus.br": html}, [])
        _saida, n = srv._rota_scon("impenhorabilidade", "SUMU", 3, 400, scon_base=srv.SCON_PROCESSO_BASE)
        assert n == 3

    def test_canario_total_sem_itens(self, srv, html, monkeypatch):
        quebrado = html.replace("gridSumula", "classeQueMudou")
        _pesquisa_dublada(monkeypatch, srv, {"processo.stj.jus.br": quebrado}, [])
        with pytest.raises(RuntimeError, match="parser extraiu 0"):
            srv._rota_scon("impenhorabilidade", "SUMU", 10, 400, scon_base=srv.SCON_PROCESSO_BASE)

    def test_canario_item_oco(self, srv, html, monkeypatch):
        quebrado = html.replace('class="numeroSumula"', 'class="rotuloQueMudou"')
        _pesquisa_dublada(monkeypatch, srv, {"processo.stj.jus.br": quebrado}, [])
        with pytest.raises(RuntimeError, match="SEM número"):
            srv._rota_scon("impenhorabilidade", "SUMU", 10, 400, scon_base=srv.SCON_PROCESSO_BASE)

    def test_canario_falso_zero(self, srv, monkeypatch):
        """Pagina sem contagem, sem itens e sem o aviso de busca vazia nao e' zero."""
        tela_inicial = "<html><head><title>STJ - Súmulas do STJ</title></head><body>formulário</body></html>"
        _pesquisa_dublada(monkeypatch, srv, {"processo.stj.jus.br": tela_inicial}, [])
        with pytest.raises(RuntimeError, match="não é lista de resultados"):
            srv._rota_scon("impenhorabilidade", "SUMU", 10, 400, scon_base=srv.SCON_PROCESSO_BASE)

    def test_acor_pelo_mesmo_host(self, srv, monkeypatch):
        """ACOR no host processo usa o parser do RESUMO (rotulos), nao o de sumulas."""
        acor = _ler("scon_resumo_acor.html")
        _pesquisa_dublada(monkeypatch, srv, {"processo.stj.jus.br": acor}, [])
        saida, n, host = srv._rota_scon_http("AgInt no AREsp 1859057", "ACOR", 10, 400)
        assert host == "processo.stj.jus.br"
        assert n > 0
        assert "Base: Acórdãos" in saida


class TestRoteamentoSumu:
    def test_sumu_sai_pelo_processo_sem_cdp(self, srv, html, monkeypatch):
        def _proibido(*_a, **_k):
            raise AssertionError("processo.stj.jus.br respondeu; o CDP nao pode ser chamado")

        chamadas = []
        _pesquisa_dublada(monkeypatch, srv, {"processo.stj.jus.br": html}, chamadas)
        monkeypatch.setattr(srv, "_rota_scon_cdp", _proibido)
        _sem_cache_nem_log(monkeypatch, srv)
        saida = srv.buscar_jurisprudencia_stj("impenhorabilidade", base="SUMU")
        assert chamadas == ["processo.stj.jus.br"]
        assert saida.startswith("<!-- STJ/SCON (processo.stj.jus.br) | Base: Súmulas")

    def test_sumu_ordem_processo_scon_cdp(self, srv, monkeypatch):
        chamadas = []
        _pesquisa_dublada(
            monkeypatch, srv,
            {"processo.stj.jus.br": ConnectionError("fora"),
             "scon.stj.jus.br": RuntimeError("HTTP 403")},
            chamadas,
        )

        def _cdp(*_a, **_k):
            chamadas.append("cdp")
            return "<!-- STJ/SCON via navegador (CDP) -->", 1

        monkeypatch.setattr(srv, "_rota_scon_cdp", _cdp)
        monkeypatch.setattr(srv, "cdp_edge", object())  # rota CDP presente mesmo sem playwright
        _sem_cache_nem_log(monkeypatch, srv)
        saida = srv.buscar_jurisprudencia_stj("impenhorabilidade", base="SUMU")
        assert chamadas == ["processo.stj.jus.br", "scon.stj.jus.br", "cdp"]
        assert "via navegador (CDP)" in saida

    def test_acor_padrao_continua_no_cjf(self, srv, monkeypatch):
        def _proibido(*_a, **_k):
            raise AssertionError("ACOR sem forcar_scon nao vai ao SCON")

        monkeypatch.setattr(srv, "_pesquisar_scon", _proibido)
        monkeypatch.setattr(srv, "_rota_cjf", lambda *a, **k: ("<!-- STJ via CJF Unificada -->", 1))
        _sem_cache_nem_log(monkeypatch, srv)
        saida = srv.buscar_jurisprudencia_stj("bem de familia", base="ACOR")
        assert saida.startswith("<!-- STJ via CJF Unificada")

    def test_acor_forcar_scon_vai_ao_processo(self, srv, monkeypatch):
        chamadas = []
        _pesquisa_dublada(
            monkeypatch, srv, {"processo.stj.jus.br": _ler("scon_resumo_acor.html")}, chamadas
        )
        _sem_cache_nem_log(monkeypatch, srv)
        saida = srv.buscar_jurisprudencia_stj("AgInt no AREsp 1859057", base="ACOR", forcar_scon=True)
        assert chamadas == ["processo.stj.jus.br"]
        assert saida.startswith("<!-- STJ/SCON (processo.stj.jus.br) | Base: Acórdãos")
