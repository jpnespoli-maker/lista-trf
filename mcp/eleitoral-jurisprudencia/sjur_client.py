"""Transporte do SJUR — busca por navegador real (CDP) e inteiro teor por HTTP.

BUSCA: a API (`sjur-pesquisa-api.tse.jus.br/{tse|tres}/.../public/pesquisa`)
exige o token do hCaptcha invisível do portal e, sem ele, responde HTTP 200 com
lista vazia. Medido em 16/10/2026: navegar direto à URL de resultados NÃO emite
o token; digitar e clicar em "Pesquisar" num Edge lançado FORA do Playwright
(`shared/cdp_edge.py`) emite, sem desafio visual. O cliente faz esse gesto,
ajusta o corpo do POST por `page.route` (tribunal, tipo, ano, tamanho, página —
o token é o que a SPA obteve, nunca fabricado) e lê o JSON da resposta.

INTEIRO TEOR: GET simples em `sjur-servicos`, sem token; vem em multipart e tem
limite de taxa curto, respondido também com HTTP 200.

Desenho: DPU/docs/specs/2026-10-16-mcp-jurisprudencia-eleitoral-design.md.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Callable, Optional

AQUI = Path(__file__).resolve().parent
for _p in (AQUI.parent, AQUI):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared import cdp_edge, tls_sistema  # noqa: E402
from sjur_parser import (  # noqa: E402
    Alvo,
    ErroSJUR,
    ajustar_corpo,
    desembrulhar_pdf,
    eh_limite_de_taxa,
    mensagem_erro_pdf,
)

FRONT = {
    "tse": "https://jurisprudencia.tse.jus.br/",
    "tres": "https://jurisprudencia-tres.tse.jus.br/",
}
SUFIXO_API = "/sjur-pesquisa-backend/rest/public/pesquisa"
URL_PDF = "https://sjur-servicos.tse.jus.br/sjur-servicos/rest/download/pdf/{codigo}"
ESPERA_CAPTCHA_MS = 5000
TIMEOUT_RESPOSTA_MS = 60_000
# Medido em 16/10/2026: a 2 s e a 5 s entre downloads o portal alterna PDF e
# "Excesso de requisições"; a 10 s, 6 de 6 vieram como PDF.
INTERVALO_MIN_PDF_S = 10.0
TENTATIVAS_PDF = 3
MSG_SEM_ENVIO = (
    "a busca não foi enviada em 60 s: o hCaptcha do portal não emitiu o token. "
    "Repita a busca; se a janela do Edge mostrar um desafio, resolva-o ali "
    "enquanto a busca aguarda (até 60 s — a aba fecha ao fim da espera). "
    "Isto NÃO significa ausência de precedente."
)

Executor = Callable[[str, str, Callable[[dict], dict]], dict]

_ultimo_pdf = float("-inf")
_trava_pdf = threading.Lock()


def pesquisar(
    busca: str,
    alvo: Alvo,
    *,
    tipos: list[str],
    ano_eleicao: Optional[int],
    tamanho: int,
    pagina: int,
    ordenacao: str,
    executor: Optional[Executor] = None,
) -> dict:
    """Devolve o payload JSON cru da API. Quem o interpreta é o sjur_parser."""
    ajuste = partial(
        ajustar_corpo,
        siglas=alvo.siglas,
        tipos=tipos,
        ano_eleicao=ano_eleicao,
        tamanho=tamanho,
        pagina=pagina,
        ordenacao=ordenacao,
    )
    return _fora_do_loop(executor or _executar_no_navegador, busca, FRONT[alvo.portal], ajuste)


def _fora_do_loop(fn: Callable, *args):
    """A API síncrona do Playwright recusa rodar em thread com loop asyncio em
    curso — e é aí que o FastMCP executa tool síncrona (mesmo caso do B27 do
    `cdp_edge.obter_html`)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return fn(*args)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="sjur") as ex:
        return ex.submit(fn, *args).result()


def _eh_resposta_da_pesquisa(resp) -> bool:
    return resp.url.endswith(SUFIXO_API) and resp.request.method == "POST"


def _reescrever_pedido(route, ajuste: Callable[[dict], dict]) -> None:
    pedido = route.request
    if pedido.method != "POST":
        route.continue_()
        return
    corpo = json.loads(pedido.post_data or "{}")
    route.continue_(post_data=json.dumps(ajuste(corpo), ensure_ascii=False))


def _manipulador_de_rota(ajuste: Callable[[dict], dict], estado: dict) -> Callable:
    """Handler de UM parâmetro. O Playwright passa ao handler tantos argumentos
    quantos parâmetros a assinatura tiver, e com dois manda (route, request):
    um `partial(_reescrever_pedido, ajuste=...)` expõe `ajuste` como segundo
    parâmetro e o request colide com ele (medido ao vivo em 16/10/2026).

    Exceção aqui dentro roda numa task do Playwright e some, com a rota nunca
    continuada: a busca esperaria 60 s e acusaria o captcha. Por isso o erro
    fica guardado em `estado` e a rota é respondida na hora, para o
    `expect_response` voltar e o chamador levantar o erro verdadeiro."""
    def manipulador(route) -> None:
        try:
            _reescrever_pedido(route, ajuste)
        except Exception as e:  # noqa: BLE001
            estado["erro"] = e
            route.fulfill(status=599, content_type="application/json", body="{}")

    return manipulador


def _levantar_falha_de_ajuste(estado: dict) -> None:
    erro = estado.get("erro")
    if erro is not None:
        raise ErroSJUR(f"falha ao ajustar o pedido da pesquisa: {erro}") from erro


def _executar_no_navegador(busca: str, url_front: str, ajuste: Callable[[dict], dict]) -> dict:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    cdp_edge.garantir_navegador(url_inicial=url_front)
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_edge.endpoint_cdp())
        except Exception as e:  # noqa: BLE001
            raise cdp_edge.CDPIndisponivel(f"connect_over_cdp falhou: {e}") from e
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        pg = ctx.new_page()
        estado: dict = {}
        try:
            pg.route("**" + SUFIXO_API, _manipulador_de_rota(ajuste, estado))
            pg.goto(url_front, wait_until="domcontentloaded", timeout=60_000)
            pg.wait_for_selector("#inputPesquisaLivre", timeout=60_000)
            pg.wait_for_timeout(ESPERA_CAPTCHA_MS)
            pg.click("#inputPesquisaLivre")
            pg.fill("#inputPesquisaLivre", "")
            pg.keyboard.type(busca, delay=60)
            try:
                with pg.expect_response(_eh_resposta_da_pesquisa, timeout=TIMEOUT_RESPOSTA_MS) as info:
                    pg.get_by_role("button", name="Pesquisar").first.click()
            except PlaywrightTimeout as e:
                _levantar_falha_de_ajuste(estado)
                raise ErroSJUR(MSG_SEM_ENVIO) from e
            _levantar_falha_de_ajuste(estado)
            resposta = info.value
            if resposta.status != 200:
                raise ErroSJUR(f"a API do SJUR respondeu HTTP {resposta.status}")
            return resposta.json()
        finally:
            pg.close()


def _http_get(url: str) -> tuple[int, bytes]:
    from curl_cffi import requests as creq

    r = creq.get(
        url,
        impersonate="chrome",
        timeout=60,
        verify=tls_sistema.caminho_bundle(),
        headers={"Referer": FRONT["tse"]},
    )
    return r.status_code, r.content


def baixar_inteiro_teor(
    codigo: str,
    *,
    http_get: Optional[Callable[[str], tuple[int, bytes]]] = None,
    dormir: Callable[[float], None] = time.sleep,
    relogio: Callable[[], float] = time.monotonic,
) -> bytes:
    global _ultimo_pdf
    if not re.fullmatch(r"\d{1,12}", str(codigo or "")):
        raise ValueError(f"codigo_decisao inválido: {codigo!r} (esperado só dígitos)")
    http_get = http_get or _http_get
    with _trava_pdf:
        for tentativa in range(1, TENTATIVAS_PDF + 1):
            falta = INTERVALO_MIN_PDF_S - (relogio() - _ultimo_pdf)
            if falta > 0:
                dormir(falta)
            status, corpo = http_get(URL_PDF.format(codigo=codigo))
            _ultimo_pdf = relogio()
            if eh_limite_de_taxa(corpo):
                if tentativa < TENTATIVAS_PDF:
                    dormir(INTERVALO_MIN_PDF_S * 2 * tentativa)
                    continue
                raise ErroSJUR(
                    f"limite de taxa do inteiro teor persistiu após {TENTATIVAS_PDF} "
                    "tentativas — aguarde alguns minutos e repita"
                )
            erro = mensagem_erro_pdf(corpo)
            if erro:
                raise ErroSJUR(f"o portal recusou o inteiro teor: {erro}")
            if status != 200:
                raise ErroSJUR(f"HTTP {status} no inteiro teor")
            return desembrulhar_pdf(corpo)
    raise AssertionError("inalcançável")
