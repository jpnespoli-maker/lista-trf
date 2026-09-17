"""Cliente do buscador oficial da Corte IDH (ColdFusion).

Endpoint descoberto em `https://www.corteidh.or.cr/js/casos.js`:
    POST get_jurisprudencia_search_tipo.cfm   (busca)
    POST buscador_mas_info.cfm                (detalhe — não exercitado)

Payload descoberto por medição em 2026-09-17 (ver
`.superpowers/sdd/2026-09-17-mcp-corteidh-fase-0-1/task-5-report.md`):
o ColdFusion erra ("An error occurred while executing the application")
sempre que `nId_Tipo_Jurisprudencia` OU `nId_estado_NUM` chegam como string
vazia — os dois campos são obrigatórios, mas aceitam os valores "todos"
(`-1` e `T`, respectivamente). Os demais campos do formulário podem ir com
o default lido da própria página. O `Texto_busqueda_TXT` pode ficar vazio
sem erro. O layout de sucesso usa `<li class="tr_normal search-result row">`
— não `class="search-result"` sozinho — por isso o parser casa a classe por
substring, nunca por prefixo exato.

Este é o ÚNICO módulo do pacote que vai à rede para descobrir o acervo, e ele é
usado só pelo crawler. O `server.py` não o importa.

Cadência: varre tipo × ano com pausa. Pedir muito de uma vez rende HTTP 522 —
medido em 2026-09-17, e era ritmo, não bloqueio.
"""

from __future__ import annotations

import re
import time

PAGINA = "https://www.corteidh.or.cr/jurisprudencia-search.cfm"
ENDPOINT = "https://www.corteidh.or.cr/get_jurisprudencia_search_tipo.cfm"
DETALHE = "https://www.corteidh.or.cr/buscador_mas_info.cfm"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Os 19 valores do filtro, lidos da página em 2026-09-17.
TIPOS = {
    "-1": "Todos los tipos", "CC": "Casos Contenciosos", "CO": "Resoluciones",
    "EX": "Escritos principales", "FT": "Ficha técnica",
    "FV": "Fondo de Asistencia Legal de Víctimas CC",
    "MP": "Medidas Provisionales",
    "MU": "Medidas Urgentes emitidas por el Presidente",
    "SM": "Solicitud de medidas provisionales",
    "OC": "Opiniones Consultivas",
    "SO": "Solicitud de Opiniones Consultivas",
    "RS": "Rechazo de solicitud de una Opinión Consultiva",
    "OA": "Otros asuntos", "SS": "Supervisión de Cumplimiento de Sentencia",
    "PS": "Resoluciones SCS", "PM": "Resoluciones MP", "PO": "Resoluciones OC",
    "FS": "Fondo de Asistencia Legal de Víctimas SCS",
    "RM": "Rechazo de la Solicitud de MP",
}

# Estado é numérico no formulário; Brasil = 6, 'T' = todos.
ESTADO_BRASIL = "6"
ESTADO_TODOS = "T"

_ERRO_CF = "An error occurred while executing"


class RespostaInvalida(Exception):
    """A resposta não é uma lista de resultados — erro, vazio ou layout novo.

    Existe para que 'não rodou' nunca se leia como 'não há'.
    """


def _sessao_padrao():
    import requests

    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "es,pt;q=0.8,en;q=0.6",
        "Referer": PAGINA + "?lang=es",
        "Origin": "https://www.corteidh.or.cr",
        "X-Requested-With": "XMLHttpRequest",
    })
    return s


def campos_do_formulario(html: str) -> dict[str, str]:
    """Lê todo campo `name=` da página com o seu `value=` default.

    Mandar o formulário COMPLETO é o que evita o erro do ColdFusion: ele não
    tolera `nId_Tipo_Jurisprudencia` ou `nId_estado_NUM` ausentes/vazios.
    """
    campos: dict[str, str] = {}
    for m in re.finditer(r"<(?:input|select|textarea)\b([^>]*)>", html, re.I):
        attrs = m.group(1)
        nome = re.search(r"name=[\"']([^\"']+)[\"']", attrs, re.I)
        if not nome:
            continue
        val = re.search(r"value=[\"']([^\"']*)[\"']", attrs, re.I)
        campos[nome.group(1)] = val.group(1) if val else ""
    return campos


def parsear_resultados(html: str) -> list[dict]:
    """HTML da resposta → registros. Estoura em erro; `-1` é 'nada encontrado'."""
    if html is None:
        raise RespostaInvalida("corpo None")
    bruto = html.strip()
    if not bruto:
        raise RespostaInvalida("corpo vazio — o POST não trabalhou")
    if _ERRO_CF in bruto:
        raise RespostaInvalida("erro do ColdFusion: payload incompleto ou campo inválido")
    if bruto == "-1":
        return []

    itens = re.findall(r'<li[^>]*class="[^"]*search-result[^"]*"[^>]*>(.*?)</li>',
                       bruto, re.S | re.I)
    if not itens:
        raise RespostaInvalida(
            "nenhum bloco search-result e nem o sinal -1 — layout mudou"
        )

    saida = []
    for bloco in itens:
        url = re.search(r'href=[\"\']([^\"\']+)[\"\']', bloco, re.I)
        titulo = re.sub(r"<[^>]+>", " ", bloco)
        titulo = re.sub(r"\s+", " ", titulo).strip()
        data = re.search(r"([0-9]{1,2}\s+de\s+[a-zç]+\s+de\s+[0-9]{4})",
                         titulo, re.I)
        serie = re.search(r"Serie\s+([ACE])\s+No\.?\s*([0-9]{1,4})", titulo, re.I)
        saida.append({
            "titulo": titulo,
            "url": url.group(1) if url else "",
            "tipo": "CC" if "seriec" in (url.group(1) if url else "") else "",
            "data": data.group(1) if data else "",
            "serie": serie.group(1).upper() if serie else None,
            "numero": int(serie.group(2)) if serie else None,
        })
    return saida


def buscar(
    *, texto: str = "", tipo: str = "-1", estado: str = ESTADO_TODOS,
    ano_de: int | None = None, ano_ate: int | None = None,
    pagina_linhas: int = 50, lang: str = "es", sessao=None,
    pausa_s: float = 3.0,
) -> list[dict]:
    """Uma consulta ao buscador oficial. Manda o formulário COMPLETO."""
    ses = sessao or _sessao_padrao()
    pagina = ses.get(PAGINA, params={"lang": lang}, timeout=60)
    campos = campos_do_formulario(pagina.text)
    if not campos:
        raise RespostaInvalida("não li campo nenhum do formulário")

    campos.update({
        "Texto_busqueda_TXT": texto,
        "nId_Tipo_Jurisprudencia": tipo,
        "nId_estado_NUM": estado,
        "page_rows": str(pagina_linhas),
        "lang": lang,
    })
    if ano_de:
        campos["sYear"] = str(ano_de)
    if ano_ate:
        campos["sYear2"] = str(ano_ate)

    time.sleep(pausa_s)
    r = ses.post(ENDPOINT, data=campos, timeout=120)
    return parsear_resultados(r.text)
