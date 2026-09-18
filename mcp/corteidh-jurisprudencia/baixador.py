"""Download de PDF do site oficial da Corte IDH.

Medido em 2026-09-17: quando o idioma pedido não existe, o servidor devolve
HTTP **206 com corpo HTML de erro**, não 404. Por isso a única validação
aceitável é por magic bytes — status code aqui é instrumento que mente.

Os HTTP 522/403 da sondagem inicial foram ritmo, não bloqueio: em cadência
civilizada o site respondeu na primeira tentativa em todas as chamadas. Daí o
backoff e a pausa entre idiomas.
"""

from __future__ import annotations

import hashlib
import time

CASCATA = ("por", "esp", "ing", "fra")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
CABECALHOS = {
    "User-Agent": UA,
    "Accept": "application/pdf,*/*",
    "Accept-Language": "pt-BR,pt;q=0.9,es;q=0.8,en;q=0.7",
    "Referer": "https://www.corteidh.or.cr/",
}
# 403 e 522 estão aqui por MEDIÇÃO: na sondagem de 2026-09-17 foram ritmo, não
# bloqueio — em cadência civilizada o site respondeu na primeira tentativa. O
# 504 entra por COERÊNCIA com 502/503/522/524, e isto se declara: NÃO há
# medição de que a Corte o devolva. Sem ele, um gateway timeout seria
# diagnosticado como "não é PDF", que é a confusão que esta tarefa combate.
_TRANSITORIOS = (403, 429, 502, 503, 504, 522, 524)


class PdfInvalido(Exception):
    """O corpo recebido não é um PDF, qualquer que tenha sido o status."""


def eh_pdf(corpo: bytes) -> bool:
    return bool(corpo) and corpo[:5] == b"%PDF-"


def sha256(corpo: bytes) -> str:
    return hashlib.sha256(corpo).hexdigest()


def _sessao_padrao():
    import requests

    s = requests.Session()
    s.headers.update(CABECALHOS)
    return s


def baixar_pdf(url: str, *, sessao=None, tentativas: int = 3) -> bytes:
    ses = sessao or _sessao_padrao()
    espera = 4.0
    # Sentinela explícita para `tentativas < 1`: sem ela, o `range` vazio faz a
    # função levantar "desistiu depois de 0 — " com a causa em branco, e quem
    # ler o log conclui que o site não respondeu quando na verdade nenhuma
    # chamada foi feita. Erro de quem chama, mas o diagnóstico é de quem lê.
    ultimo = "nenhuma tentativa foi feita (tentativas < 1)"
    for n in range(1, tentativas + 1):
        try:
            r = ses.get(url, timeout=120)
        except Exception as e:  # noqa: BLE001
            ultimo = f"{type(e).__name__}: {e}"
        else:
            if eh_pdf(r.content):
                return r.content
            if r.status_code in _TRANSITORIOS:
                ultimo = f"HTTP {r.status_code} transitorio"
            else:
                raise PdfInvalido(
                    f"{url}: HTTP {r.status_code} com corpo de "
                    f"{len(r.content)} bytes que nao comeca por %PDF-"
                )
        if n < tentativas:
            time.sleep(espera)
            espera *= 2
    raise PdfInvalido(f"{url}: desistiu depois de {tentativas} — {ultimo}")


def baixar_melhor_idioma(
    base_url_sem_sufixo: str, *, sessao=None, pausa_s: float = 2.0
) -> tuple[str, bytes, str]:
    """Tenta `_por` → `_esp` → `_ing` → `_fra`; devolve o primeiro PDF real.

    A exceção final carrega a CAUSA DE CADA IDIOMA, e isso não é luxo de
    mensagem: o crawler precisa distinguir "este documento não tem tradução
    nesses idiomas" de "o site estava fora do ar", e as duas condutas são
    OPOSTAS — a primeira se registra e segue, a segunda se repete depois.
    Sem as causas, as duas produzem texto idêntico, e o relatório do crawler
    diria "documento indisponível" para um site que apenas piscou.
    """
    ses = sessao or _sessao_padrao()
    causas: list[str] = []
    for i, idioma in enumerate(CASCATA):
        url = f"{base_url_sem_sufixo}_{idioma}.pdf"
        try:
            corpo = baixar_pdf(url, sessao=ses, tentativas=2)
        except PdfInvalido as e:
            causas.append(f"{idioma}: {e}")
            if i < len(CASCATA) - 1 and pausa_s:
                time.sleep(pausa_s)
            continue
        return idioma, corpo, url
    raise PdfInvalido(
        f"{base_url_sem_sufixo}: nenhum dos idiomas {CASCATA} devolveu PDF"
        + " — " + " | ".join(causas)
    )
