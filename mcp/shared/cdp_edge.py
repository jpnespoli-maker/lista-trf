"""Acesso a portal protegido por Cloudflare via CDP em Edge lancado FORA do Playwright.

POR QUE ESTE MODULO EXISTE
--------------------------
O SCON do STJ (`scon.stj.jus.br`) esta atras de Cloudflare Turnstile e responde
HTTP 403 a cliente HTTP, inclusive ao `curl_cffi` com impersonate. A escada que o
projeto documentava terminava em "o Defensor vence o desafio a mao" — e isso foi
FALSIFICADO em 24/08/2026: o Defensor clicou e o desafio voltou, repetidamente.
A deteccao nao e' do clique; e' do MODO DE LANCAMENTO do navegador. Navegador
iniciado por `playwright.launch()` / `launch_persistent_context()` carrega flags
de automacao (`--enable-automation`, `--disable-field-trial-config` etc.) e e'
reprovado antes de qualquer interacao humana.

O QUE FUNCIONA
--------------
Lancar o Edge COMO UM NAVEGADOR NORMAL (subprocess do sistema, sem Playwright) e
so entao conectar-se a ele por CDP. O Playwright vira cliente de um navegador que
ele nao iniciou, e as flags de automacao nunca existiram. Medido no SCON em
24/08/2026: titulo 'STJ - Jurisprudencia do STJ', sem desafio e SEM CLIQUE HUMANO.

ARMADILHA QUE CUSTOU UMA TENTATIVA
----------------------------------
Desde o Chrome/Edge 136 o `--remote-debugging-port` e' SILENCIOSAMENTE IGNORADO
quando se usa o perfil padrao — a porta simplesmente nao abre, sem mensagem de
erro. E' OBRIGATORIO passar `--user-data-dir` apontando para diretorio proprio.
Sintoma: "No connection could be made because the target machine actively
refused it" ao consultar /json/version.

LIMITES CONHECIDOS
------------------
- O navegador e' HEADED por desenho. `--headless` volta a ser sinal de deteccao,
  e foi headed que a medicao passou. Uma janela aparece na maquina do Defensor;
  a instancia e' reaproveitada entre chamadas para que apareca UMA vez.
- Nao serve a ambiente sem sessao grafica (headless de CI, cron sem desktop).
  Nesses casos `garantir_navegador` levanta `CDPIndisponivel` e o chamador deve
  cair para a rota alternativa (no STJ, o CJF Unificada).
- Perfil proprio, isolado do perfil pessoal do Defensor: nao le nem escreve
  cookies de navegacao dele.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

PORTA_PADRAO = int(os.environ.get("DPU_CDP_PORTA", "9222"))
ESPERA_PORTA_S = int(os.environ.get("DPU_CDP_ESPERA_PORTA", "25"))
DESABILITADO = os.environ.get("DPU_CDP_DESABILITADO", "") == "1"

_CANDIDATOS_EDGE = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

_SENTINELAS_DESAFIO = (
    "just a moment",
    "um momento",
    "attention required",
    "checking your browser",
    "verificando seu navegador",
)


class CDPIndisponivel(RuntimeError):
    """Nao foi possivel abrir/alcancar o navegador por CDP."""


class DesafioCloudflare(RuntimeError):
    """A pagina voltou com o desafio do Cloudflare mesmo pela via CDP."""


def perfil_padrao() -> Path:
    """Diretorio de perfil dedicado. FORA do repo (nao versionar perfil)."""
    bruto = os.environ.get("DPU_CDP_PERFIL")
    if bruto:
        return Path(bruto)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "dpu-cdp-edge"


def edge_executavel() -> Optional[str]:
    for caminho in _CANDIDATOS_EDGE:
        if Path(caminho).exists():
            return caminho
    return None


def montar_args(porta: int, perfil: Path, url: Optional[str] = None) -> list[str]:
    """Argumentos do Edge. PURO — testavel sem lancar processo.

    `--user-data-dir` NAO e' opcional: sem ele a porta de depuracao nao abre em
    Edge/Chrome >= 136. Ver docstring do modulo.
    """
    args = [
        f"--remote-debugging-port={porta}",
        f"--user-data-dir={perfil}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if url:
        args.append(url)
    return args


def detectar_desafio(titulo: str, texto: str = "") -> bool:
    """PURO. True quando a pagina e' o interstitial do Cloudflare."""
    alvo = f"{titulo or ''} {texto[:400] if texto else ''}".lower()
    return any(s in alvo for s in _SENTINELAS_DESAFIO)


def porta_responde(porta: int = PORTA_PADRAO, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://localhost:{porta}/json/version", timeout=timeout
        ) as r:
            json.loads(r.read().decode("utf-8", "replace"))
            return True
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return False


def garantir_navegador(
    porta: int = PORTA_PADRAO,
    perfil: Optional[Path] = None,
    url_inicial: Optional[str] = None,
) -> None:
    """Garante um Edge ouvindo CDP em `porta`. Reaproveita o que ja estiver de pe."""
    if DESABILITADO:
        raise CDPIndisponivel("rota CDP desligada por DPU_CDP_DESABILITADO=1")
    if porta_responde(porta):
        return
    exe = edge_executavel()
    if not exe:
        raise CDPIndisponivel("msedge.exe nao encontrado nos caminhos conhecidos")
    perfil = perfil or perfil_padrao()
    perfil.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen(  # noqa: S603
            [exe, *montar_args(porta, perfil, url_inicial)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
    except OSError as e:
        raise CDPIndisponivel(f"falha ao lancar o Edge: {e}") from e

    gasto = 0.0
    while gasto < ESPERA_PORTA_S:
        if porta_responde(porta):
            return
        time.sleep(1.0)
        gasto += 1.0
    raise CDPIndisponivel(
        f"porta CDP {porta} nao respondeu em {ESPERA_PORTA_S}s "
        "(sem sessao grafica? --user-data-dir ausente?)"
    )


def _pagina_do_dominio(ctx, dominio: str):
    for pg in ctx.pages:
        if dominio in (pg.url or ""):
            return pg
    return None


def obter_html(
    url_alvo: str,
    dominio: str,
    *,
    url_base: Optional[str] = None,
    porta: int = PORTA_PADRAO,
    espera_pos_carga_s: float = 5.0,
    seletor_busca: Optional[str] = None,
    termo: Optional[str] = None,
) -> tuple[str, str]:
    """Abre `url_alvo` pelo navegador CDP e devolve (titulo, html).

    `url_base` (a home do portal) e' visitada ANTES quando a aba ainda nao esta
    no dominio: portal que exige sessao — o SCON e' um — responde a URL de
    consulta redirecionando para a home quando o cookie de sessao ainda nao
    existe, e o resultado seria lido como "zero resultados".

    `seletor_busca`+`termo` sao o plano B, para quando a querystring nao basta:
    preenche o campo e submete com Enter.

    A navegacao para `url_alvo` acontece SEMPRE — reaproveitar a aba sem
    renavegar devolveria o resultado da consulta anterior na consulta seguinte.
    """
    from playwright.sync_api import sync_playwright  # import tardio: opcional

    garantir_navegador(porta=porta, url_inicial=url_base or url_alvo)
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{porta}")
        except Exception as e:  # noqa: BLE001
            raise CDPIndisponivel(f"connect_over_cdp falhou: {e}") from e
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        pg = _pagina_do_dominio(ctx, dominio) or ctx.new_page()

        if url_base and dominio not in (pg.url or ""):
            pg.goto(url_base, wait_until="domcontentloaded", timeout=60_000)
            time.sleep(espera_pos_carga_s)

        pg.goto(url_alvo, wait_until="domcontentloaded", timeout=60_000)
        time.sleep(espera_pos_carga_s)

        if seletor_busca and termo and pg.locator(seletor_busca).count():
            pg.fill(seletor_busca, termo)
            pg.keyboard.press("Enter")
            time.sleep(espera_pos_carga_s + 2)

        titulo = pg.title()
        html = pg.content()
        corpo = ""
        try:
            corpo = pg.inner_text("body")[:400]
        except Exception:  # noqa: BLE001
            pass
        if detectar_desafio(titulo, corpo):
            raise DesafioCloudflare(
                f"desafio ativo mesmo via CDP (titulo={titulo!r}) — "
                "conferir se o Edge foi lancado fora do Playwright"
            )
        return titulo, html
