"""Transporte do SJUR: o ajuste chega ao pedido, o loop do FastMCP não trava a
API síncrona do Playwright, a interceptação fica na ABA, e o inteiro teor
respeita cadência e limite de taxa."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "eleitoral-jurisprudencia"
for p in (MODULO, MODULO.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import sjur_client as C  # noqa: E402
import sjur_parser as P  # noqa: E402

FIX = RAIZ / "tests" / "fixtures" / "eleitoral"
CORPO = json.loads((FIX / "corpo_pesquisa.json").read_text(encoding="utf-8"))
LIMITE = ("Erro ao tentar recuperar PDF: Excesso de requisições! Favor aguardar "
          "alguns instantes e tentar novamente.").encode("utf-8")
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF"
MULTIPART = b"--b\r\nContent-Disposition: form-data; name=\"file\"\r\n\r\n" + PDF + b"\r\n--b--\r\n"


@pytest.fixture(autouse=True)
def _cadencia_zerada(monkeypatch):
    monkeypatch.setattr(C, "_ultimo_pdf", float("-inf"))


def _kw(**extra):
    base = dict(tipos=["Acórdão"], ano_eleicao=None, tamanho=10, pagina=0, ordenacao="re")
    base.update(extra)
    return base


def test_pesquisar_entrega_url_e_ajuste_ao_executor():
    visto = {}

    def executor(busca, url_front, ajuste):
        visto.update(busca=busca, url=url_front, corpo=ajuste(CORPO))
        return {"mensagem": None, "content": [], "totalRegistros": 0}

    C.pesquisar("abuso", P.Alvo("tres", ("TRE-RJ",)), executor=executor, **_kw(tamanho=7))
    assert visto["busca"] == "abuso"
    assert visto["url"] == C.FRONT["tres"]
    assert visto["corpo"]["tamanho"] == 7
    assert visto["corpo"]["captchaToken"] == CORPO["captchaToken"]


def test_pesquisar_dentro_de_loop_roda_em_thread_propria():
    nomes = []

    def executor(busca, url_front, ajuste):
        nomes.append(threading.current_thread().name)
        return {}

    async def chamar():
        return C.pesquisar("x", P.Alvo("tse"), executor=executor, **_kw())

    asyncio.run(chamar())
    assert nomes and nomes[0].startswith("sjur")


def test_pesquisar_fora_de_loop_roda_aqui():
    nomes = []
    C.pesquisar("x", P.Alvo("tse"), executor=lambda *a: nomes.append(
        threading.current_thread().name) or {}, **_kw())
    assert nomes == [threading.current_thread().name]


class _Pedido:
    def __init__(self, metodo, dados):
        self.method, self.post_data = metodo, dados


class _Rota:
    def __init__(self, metodo, dados=None):
        self.request = _Pedido(metodo, dados)
        self.continuou = None
        self.cumprido = None

    def continue_(self, **kw):
        self.continuou = kw

    def fulfill(self, **kw):
        self.cumprido = kw


def test_reescrever_pedido_post():
    rota = _Rota("POST", json.dumps(CORPO))
    C._reescrever_pedido(rota, ajuste=lambda c: {**c, "tamanho": 3})
    assert json.loads(rota.continuou["post_data"])["tamanho"] == 3


def test_manipulador_sobrevive_ao_despacho_do_playwright():
    # O Playwright passa ao handler tantos argumentos quantos parâmetros a
    # assinatura tiver (`_impl_to_api_mapping.wrap_handler`: `args[:arg_count]`),
    # e com dois ele manda (route, request). Medido ao vivo em 16/10/2026: um
    # `partial(..., ajuste=...)` expõe `ajuste` como 2º parâmetro, o request
    # colide com ele ("got multiple values for argument 'ajuste'") e a busca
    # nunca sai.
    import inspect

    rota = _Rota("POST", json.dumps(CORPO))
    manipulador = C._manipulador_de_rota(lambda c: {**c, "tamanho": 4}, {})
    n = len(inspect.signature(manipulador).parameters)
    manipulador(*(rota, rota.request)[:n])
    assert json.loads(rota.continuou["post_data"])["tamanho"] == 4


def test_navegador_registra_o_manipulador_e_nao_um_partial():
    fonte = (MODULO / "sjur_client.py").read_text(encoding="utf-8")
    assert "pg.route(\"**\" + SUFIXO_API, _manipulador_de_rota(ajuste, estado))" in fonte


@pytest.mark.parametrize("dados,ajuste", [
    (json.dumps(CORPO), lambda c: (_ for _ in ()).throw(P.ErroSJUR("corpo da pesquisa em formato inesperado"))),
    ("nao-e-json", lambda c: c),
], ids=["ajuste-levanta", "post-nao-json"])
def test_erro_no_ajuste_responde_na_hora_e_fica_guardado(dados, ajuste):
    # I1 — exceção no handler some numa task do Playwright e a rota nunca
    # continua: o usuário esperava 60 s e lia um diagnóstico de captcha falso.
    rota = _Rota("POST", dados)
    estado = {}
    C._manipulador_de_rota(ajuste, estado)(rota)
    assert rota.cumprido is not None and rota.continuou is None
    assert estado["erro"] is not None
    with pytest.raises(P.ErroSJUR, match="ajustar o pedido"):
        C._levantar_falha_de_ajuste(estado)


def test_sem_falha_de_ajuste_nao_levanta():
    C._levantar_falha_de_ajuste({})


def test_mensagem_sem_envio_e_executavel():
    # I3 — a aba fecha no timeout: a instrução é repetir e resolver durante a espera.
    assert "Repita a busca" in C.MSG_SEM_ENVIO
    assert "enquanto a busca aguarda" in C.MSG_SEM_ENVIO


# ---------------- caminho do navegador, com página falsa (M8) ----------------

class _EsperaEsgotada(Exception):
    """Faz as vezes do TimeoutError do Playwright."""


class _Resposta:
    def __init__(self, status, payload):
        self.status, self._payload = status, payload

    def json(self):
        return self._payload


class _PaginaFalsa:
    """Imita o que o Playwright faz: o clique em "Pesquisar" dispara o POST da
    SPA, que passa pelo handler registrado em `route` antes da resposta."""

    def __init__(self, *, status=200, payload=None, sem_resposta=False, falha_no_goto=None):
        self.status, self.sem_resposta, self.falha_no_goto = status, sem_resposta, falha_no_goto
        self.payload = payload if payload is not None else {"mensagem": None, "content": [], "totalRegistros": 0}
        self.chamadas, self.fechada, self.manipulador, self.rota = [], False, None, None
        pagina = self

        class _Teclado:
            def type(self, texto, delay=0):
                pagina.chamadas.append(("type", texto))

        self.keyboard = _Teclado()

    def route(self, padrao, manipulador):
        self.manipulador = manipulador

    def goto(self, url, **kw):
        self.chamadas.append(("goto", url))
        if self.falha_no_goto:
            raise self.falha_no_goto

    def wait_for_selector(self, *a, **kw):
        pass

    def wait_for_timeout(self, *a, **kw):
        pass

    def click(self, seletor):
        self.chamadas.append(("click", seletor))

    def fill(self, seletor, valor):
        self.chamadas.append(("fill", seletor, valor))

    def get_by_role(self, papel, name):
        pagina = self

        class _Botao:
            class first:  # noqa: N801 — imita o atributo `.first` do Locator
                @staticmethod
                def click():
                    pagina.chamadas.append(("pesquisar",))
                    pagina.rota = _Rota("POST", json.dumps(CORPO))
                    pagina.manipulador(pagina.rota)

        return _Botao()

    def expect_response(self, predicado, timeout):
        pagina = self

        class _Contexto:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                if exc[0] is not None:
                    return False
                if pagina.sem_resposta:
                    raise _EsperaEsgotada()
                status = 599 if pagina.rota.cumprido is not None else pagina.status
                self.value = _Resposta(status, pagina.payload)
                return False

        return _Contexto()

    def close(self):
        self.fechada = True


def _na_pagina(pg, ajuste=lambda c: {**c, "tamanho": 7}):
    return C._pesquisar_na_pagina(pg, "AIJE OU AIME", C.FRONT["tse"], ajuste, _EsperaEsgotada)


def test_pagina_sucesso_ajusta_o_pedido_e_fecha_a_aba():
    pg = _PaginaFalsa(payload={"mensagem": None, "content": [], "totalRegistros": 0})
    assert _na_pagina(pg) == pg.payload
    assert json.loads(pg.rota.continuou["post_data"])["tamanho"] == 7
    assert pg.fechada


def test_campo_limpo_antes_de_digitar():
    # M6 — se a SPA restaurar a última busca, a nova não pode ser anexada a ela.
    pg = _PaginaFalsa()
    _na_pagina(pg)
    passos = [c[0] for c in pg.chamadas]
    assert ("fill", "#inputPesquisaLivre", "") in pg.chamadas
    assert passos.index("fill") < passos.index("type") < passos.index("pesquisar")
    assert ("type", "AIJE OU AIME") in pg.chamadas


def test_pagina_http_nao_200_e_erro_e_fecha():
    pg = _PaginaFalsa(status=503)
    with pytest.raises(P.ErroSJUR, match="HTTP 503"):
        _na_pagina(pg)
    assert pg.fechada


def test_pagina_erro_no_ajuste_sobe_o_erro_verdadeiro():
    def ajuste(c):
        raise P.ErroSJUR("corpo da pesquisa em formato inesperado")

    pg = _PaginaFalsa()
    with pytest.raises(P.ErroSJUR, match="ajustar o pedido") as exc:
        _na_pagina(pg, ajuste)
    assert C.MSG_SEM_ENVIO not in str(exc.value)
    assert pg.fechada


def test_pagina_sem_resposta_e_mensagem_do_captcha():
    pg = _PaginaFalsa(sem_resposta=True)
    with pytest.raises(P.ErroSJUR) as exc:
        _na_pagina(pg)
    assert str(exc.value) == C.MSG_SEM_ENVIO
    assert pg.fechada


def test_pagina_fecha_mesmo_quando_o_portal_nao_abre():
    pg = _PaginaFalsa(falha_no_goto=RuntimeError("net::ERR_CONNECTION_RESET"))
    with pytest.raises(RuntimeError):
        _na_pagina(pg)
    assert pg.fechada


def _abas(*paginas):
    fila = list(paginas)
    return lambda: fila.pop(0)


def test_token_que_nao_veio_tenta_de_novo_em_aba_nova():
    # Medido em 16/10/2026: o token do hCaptcha não veio em 3 de ~60 buscas ao
    # vivo, sem causa apurada (não se reproduziu em 26 buscas instrumentadas).
    # Uma nova tentativa, em aba nova, cobre a falha intermitente.
    falha, ok = _PaginaFalsa(sem_resposta=True), _PaginaFalsa()
    assert C._pesquisar_com_nova_tentativa(_abas(falha, ok), "x", C.FRONT["tse"],
                                           lambda c: c, _EsperaEsgotada) == ok.payload
    assert falha.fechada and ok.fechada


def test_token_que_nao_veio_duas_vezes_e_erro_orientador():
    a, b = _PaginaFalsa(sem_resposta=True), _PaginaFalsa(sem_resposta=True)
    with pytest.raises(P.ErroSJUR) as exc:
        C._pesquisar_com_nova_tentativa(_abas(a, b), "x", C.FRONT["tse"], lambda c: c, _EsperaEsgotada)
    assert str(exc.value) == C.MSG_SEM_ENVIO
    assert f"{C.TENTATIVAS_BUSCA} tentativas" in C.MSG_SEM_ENVIO


def test_outro_erro_nao_gasta_nova_tentativa():
    # HTTP 503 ou erro de ajuste não são o token: repetir só gastaria tempo.
    primeira = _PaginaFalsa(status=503)
    with pytest.raises(P.ErroSJUR, match="HTTP 503"):
        C._pesquisar_com_nova_tentativa(_abas(primeira), "x", C.FRONT["tse"], lambda c: c, _EsperaEsgotada)


def test_garantir_navegador_e_serializado(monkeypatch):
    # M9 — com as tools em paralelo, duas buscas podiam achar a porta CDP
    # fechada ao mesmo tempo e lançar dois Edge no mesmo perfil.
    import time as _time

    estado = {"ativos": 0, "max": 0}

    def garantir(url_inicial=None):
        estado["ativos"] += 1
        estado["max"] = max(estado["max"], estado["ativos"])
        _time.sleep(0.05)
        estado["ativos"] -= 1

    monkeypatch.setattr(C.cdp_edge, "garantir_navegador", garantir)
    fios = [threading.Thread(target=C._garantir_navegador, args=(C.FRONT["tse"],)) for _ in range(4)]
    for f in fios:
        f.start()
    for f in fios:
        f.join()
    assert estado["max"] == 1


def test_reescrever_pedido_nao_post_segue_intacto():
    rota = _Rota("OPTIONS")
    C._reescrever_pedido(rota, ajuste=lambda c: pytest.fail("não devia ajustar"))
    assert rota.continuou == {}


def test_interceptacao_fica_na_aba_e_aba_fecha():
    # Review Focus 5 — duas buscas simultâneas no mesmo Edge: o ajuste de uma
    # não pode alcançar o pedido da outra.
    fonte = (MODULO / "sjur_client.py").read_text(encoding="utf-8")
    assert "pg.route(" in fonte
    assert "ctx.route(" not in fonte
    assert "pg.close()" in fonte
    assert "browser.close()" not in fonte


def test_mensagem_sem_envio_orienta_o_defensor():
    # Review Focus 3 — desafio visual do hCaptcha.
    assert "desafio" in C.MSG_SEM_ENVIO
    assert "NÃO significa ausência de precedente" in C.MSG_SEM_ENVIO


def test_inteiro_teor_multipart():
    pdf = C.baixar_inteiro_teor("3606299", http_get=lambda url: (200, MULTIPART),
                                dormir=lambda s: None)
    assert pdf == PDF


def test_inteiro_teor_url():
    urls = []
    C.baixar_inteiro_teor("42", http_get=lambda url: urls.append(url) or (200, PDF),
                          dormir=lambda s: None)
    assert urls == [C.URL_PDF.format(codigo="42")]


def test_inteiro_teor_limite_e_nova_tentativa():
    respostas = iter([(200, LIMITE), (200, LIMITE), (200, MULTIPART)])
    sonos = []
    pdf = C.baixar_inteiro_teor("1", http_get=lambda url: next(respostas),
                                dormir=sonos.append, relogio=lambda: 1e6)
    assert pdf == PDF
    esperas_de_recuo = [s for s in sonos if s >= C.INTERVALO_MIN_PDF_S * 2]
    assert len(esperas_de_recuo) == 2


def test_inteiro_teor_limite_persistente_e_erro():
    with pytest.raises(P.ErroSJUR, match="limite de taxa"):
        C.baixar_inteiro_teor("1", http_get=lambda url: (200, LIMITE), dormir=lambda s: None)


def test_inteiro_teor_outro_erro_do_portal():
    corpo = "Erro ao tentar recuperar PDF: inexistente".encode("utf-8")
    with pytest.raises(P.ErroSJUR, match="recusou"):
        C.baixar_inteiro_teor("1", http_get=lambda url: (200, corpo), dormir=lambda s: None)


def test_inteiro_teor_http_nao_200():
    with pytest.raises(P.ErroSJUR, match="HTTP 503"):
        C.baixar_inteiro_teor("1", http_get=lambda url: (503, b"x"), dormir=lambda s: None)


@pytest.mark.parametrize("codigo", ["", "abc", "12;34", "1" * 13])
def test_inteiro_teor_codigo_invalido_nem_chama(codigo):
    with pytest.raises(ValueError):
        C.baixar_inteiro_teor(codigo, http_get=lambda url: pytest.fail("chamou"))


def test_cadencia_entre_downloads():
    tempos = iter([100.0, 100.0, 101.0, 101.0])
    sonos = []
    for _ in range(2):
        C.baixar_inteiro_teor("1", http_get=lambda url: (200, PDF), dormir=sonos.append,
                              relogio=lambda: next(tempos))
    assert sonos == [pytest.approx(C.INTERVALO_MIN_PDF_S - 1.0)]
