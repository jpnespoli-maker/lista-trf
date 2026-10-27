"""Inteiro teor dos acórdãos do TRF1 pelo arquivo do tribunal (arquivo.trf1.jus.br).

POR QUE EXISTE
--------------
A base regional do CJF devolve só a ementa. O voto, onde estão o fato que torna o
caso análogo e a razão de decidir, fica no "Acesse Aqui" de cada resultado, que
aponta para ``PesquisaMenuArquivo.asp?p1=<os 20 dígitos do CNJ>``. Medido em
27/10/2026 no RSE 0005984-10.2018.4.01.3802: a ementa dizia "residente em outro
país"; o voto contava que o réu, citado nos EUA por cooperação internacional,
aceitara comparecimento virtual bimestral e prestação pecuniária trimestral. Sem o
voto, esse fato não se lia.

O QUE O ARQUIVO SERVE (medido em 27/10/2026)
--------------------------------------------
1. ``.doc`` (Word 97, OLE2) por peça: Relatório, Voto, Ementa, Certidão. Alguns
   ``.doc`` são texto puro em cp1252 (a certidão de julgamento).
2. Processo antigo (anos 1990): páginas ``.TIFF`` digitalizadas, sem texto.
3. Processo do PJe (numeração ``1xxxxxx``): "Nenhum documento encontrado". O
   inteiro teor desses está no PJe 2G; o "Acesse Aqui" da base regional aponta
   para a consulta pública genérica, não para o documento. JEF1 idem.

TRANSPORTE
----------
O host está atrás de desafio do Cloudflare: ``requests`` e ``curl`` recebem 403
com ``cf-mitigated: challenge``. A rota é a do STJ, ``shared/cdp_edge``: Edge
lançado fora do Playwright e conectado por CDP. Os ``.doc`` baixam-se pelo
``APIRequestContext`` do MESMO contexto, que já tem o cookie do desafio.
"""

from __future__ import annotations

import asyncio
import html as html_mod
import io
import re
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List

from shared import cdp_edge

HOST = "https://arquivo.trf1.jus.br"
DOMINIO = "arquivo.trf1.jus.br"
URL_MENU = HOST + "/PesquisaMenuArquivo.asp?p1={digitos}"

_SEM_DOCUMENTOS = "Nenhum documento encontrado"
_RE_LINHA = re.compile(
    r'<td><a href="(?P<href>[^"]+)"[^>]*>(?P<rotulo>[^<]*)</a></td>\s*'
    r'<td[^>]*><a href="[^"]+"[^>]*>(?P<data>[^<]*)</a></td>',
    re.IGNORECASE,
)

# Ordem de leitura. O portal lista ao contrário (certidões primeiro, relatório
# por último), e quem lê o inteiro teor quer o relatório antes do voto.
_ORDEM = (("relat", 0), ("voto", 1), ("ementa", 2), ("acórd", 2), ("acord", 2))


class InteiroTeorIndisponivel(RuntimeError):
    """O arquivo do TRF1 não tem texto para este número, e o motivo é conhecido."""


@dataclass
class Peca:
    rotulo: str
    caminho: str
    data_publicacao: str = ""
    texto: str = ""
    aviso: str = ""


@dataclass
class InteiroTeor:
    numero: str
    url: str
    pecas: List[Peca] = field(default_factory=list)
    imagens: List[Peca] = field(default_factory=list)


def digitos_cnj(numero: str) -> str:
    """Os 20 dígitos do CNJ, que é o ``p1`` do arquivo. Levanta se não houver 20."""
    d = re.sub(r"\D", "", numero or "")
    if len(d) != 20:
        raise ValueError(
            f"número {numero!r} não é um CNJ completo (20 dígitos); use o campo "
            "<numero> devolvido por buscar_jurisprudencia_trf1"
        )
    return d


def listar_pecas(html_menu: str) -> List[Peca]:
    """PURO. As peças da tabela do menu, na ordem de leitura."""
    pecas = [
        Peca(
            rotulo=html_mod.unescape(m.group("rotulo")).strip(),
            caminho=html_mod.unescape(m.group("href")),
            data_publicacao=m.group("data").strip(),
        )
        for m in _RE_LINHA.finditer(html_menu)
    ]

    def chave(p: Peca) -> int:
        r = p.rotulo.lower()
        return next((o for prefixo, o in _ORDEM if r.startswith(prefixo)), 3)

    return sorted(pecas, key=chave)


# ---------------------------------------------------------------------------
# Texto do .doc (Word 97) pela tabela de peças
# ---------------------------------------------------------------------------

def _texto_word97(dados: bytes) -> str:
    """Texto do documento principal de um Word 97-2003 (MS-DOC, tabela de peças).

    O texto não está contíguo no stream ``WordDocument``: a tabela de peças
    (``Clx``, no stream ``0Table``/``1Table``) diz onde cada trecho começa e se
    está em cp1252 (comprimido, 1 byte) ou UTF-16LE.
    """
    import olefile  # import tardio: só quem baixa .doc precisa dele

    ole = olefile.OleFileIO(io.BytesIO(dados))
    wd = ole.openstream("WordDocument").read()
    if struct.unpack_from("<H", wd, 0)[0] != 0xA5EC:
        raise ValueError("stream WordDocument sem a assinatura do Word 97")
    # Word 6/95 (nFib 101-105) tem outro FIB: os deslocamentos abaixo leriam
    # lixo com cara de texto. Os .doc medidos no arquivo (2001 e 2018) são 193.
    n_fib = struct.unpack_from("<H", wd, 2)[0]
    if n_fib < 0xC0:
        raise ValueError(f"Word 6/95 (nFib {n_fib}) não suportado")
    flags = struct.unpack_from("<H", wd, 0x0A)[0]
    tabela = ole.openstream("1Table" if flags & 0x0200 else "0Table").read()

    csw = struct.unpack_from("<H", wd, 32)[0]
    rg_lw = 32 + 2 + csw * 2 + 2
    cslw = struct.unpack_from("<H", wd, rg_lw - 2)[0]
    ccp_text = struct.unpack_from("<i", wd, rg_lw + 3 * 4)[0]
    rg_fclcb = rg_lw + cslw * 4 + 2
    fc_clx, lcb_clx = struct.unpack_from("<II", wd, rg_fclcb + 33 * 8)

    clx = tabela[fc_clx:fc_clx + lcb_clx]
    i = 0
    while i < len(clx) and clx[i] == 0x01:  # Prc: modificadores, pular
        i += 3 + struct.unpack_from("<h", clx, i + 1)[0]
    if i >= len(clx) or clx[i] != 0x02:
        raise ValueError("tabela de peças (Pcdt) não encontrada no Clx")
    lcb = struct.unpack_from("<I", clx, i + 1)[0]
    plc = clx[i + 5:i + 5 + lcb]
    n = (lcb - 4) // 12
    cps = struct.unpack_from(f"<{n + 1}I", plc, 0)

    partes: List[str] = []
    for k in range(n):
        ini, fim = cps[k], min(cps[k + 1], ccp_text)
        if ini >= fim:
            continue
        fc = struct.unpack_from("<I", plc, (n + 1) * 4 + k * 8 + 2)[0]
        qtd = fim - ini
        if fc & 0x40000000:
            pos = (fc & ~0x40000000) // 2
            partes.append(wd[pos:pos + qtd].decode("cp1252", "replace"))
        else:
            partes.append(wd[fc:fc + 2 * qtd].decode("utf-16-le", "replace"))
    return "".join(partes)


_RE_CAMPO = re.compile("\x13[^\x13\x14\x15]*\x14([^\x13\x14\x15]*)\x15")
_RE_CAMPO_SEM_RESULTADO = re.compile("\x13[^\x13\x14\x15]*\x15")


def limpar_texto_word(bruto: str) -> str:
    """PURO. Troca os caracteres de controle do Word pelo equivalente em texto."""
    t = bruto
    for _ in range(5):  # campos aninhados: resolve de dentro para fora
        novo = _RE_CAMPO_SEM_RESULTADO.sub("", _RE_CAMPO.sub(r"\1", t))
        if novo == t:
            break
        t = novo
    t = (t.replace("\r", "\n").replace("\x0b", "\n").replace("\x0c", "\n")
          .replace("\x07", "\t").replace("\x1e", "-").replace("\x1f", "")
          .replace("\xa0", " "))
    t = re.sub(r"[\x00-\x08\x0e-\x1f]", "", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def texto_do_doc(dados: bytes) -> str:
    """PURO. Texto de um ``.doc`` do arquivo: Word 97 (OLE2) ou texto puro cp1252.

    Formato que não é nenhum dos dois levanta, em vez de devolver lixo que se
    leria como texto.
    """
    if dados[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return limpar_texto_word(_texto_word97(dados))
    if dados[:5] == b"{\\rtf" or b"\x00" in dados[:512]:
        raise ValueError(f"formato não suportado (início: {dados[:8]!r})")
    return limpar_texto_word(dados.decode("cp1252", "replace"))


# ---------------------------------------------------------------------------
# Transporte (Edge por CDP)
# ---------------------------------------------------------------------------

def obter_inteiro_teor(numero: str, espera_pos_carga_s: float = 5.0) -> InteiroTeor:
    """Abre o menu do arquivo, baixa as peças e extrai o texto.

    Mesma precaução de ``cdp_edge.obter_html``: a API síncrona do Playwright
    recusa rodar dentro do loop do FastMCP, então o trabalho vai a uma thread.
    """
    digitos = digitos_cnj(numero)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _obter(numero, digitos, espera_pos_carga_s)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="arquivo_trf1") as ex:
        return ex.submit(_obter, numero, digitos, espera_pos_carga_s).result()


def _obter(numero: str, digitos: str, espera_pos_carga_s: float) -> InteiroTeor:
    from playwright.sync_api import sync_playwright  # import tardio: opcional

    url = URL_MENU.format(digitos=digitos)
    cdp_edge.garantir_navegador(url_inicial=url)
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_edge.endpoint_cdp())
        except Exception as e:  # noqa: BLE001
            raise cdp_edge.CDPIndisponivel(f"connect_over_cdp falhou: {e}") from e
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        pg = ctx.new_page()
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=60_000)
            time.sleep(espera_pos_carga_s)
            corpo = pg.inner_text("body")
            if cdp_edge.detectar_desafio(pg.title(), corpo):
                raise cdp_edge.DesafioCloudflare(
                    "desafio do Cloudflare ativo no arquivo do TRF1 mesmo via CDP")
            html_menu = pg.content()
        finally:
            pg.close()

        resultado = InteiroTeor(numero=numero, url=url)
        pecas = listar_pecas(html_menu)
        if not pecas:
            if _SEM_DOCUMENTOS.lower() in corpo.lower():
                raise InteiroTeorIndisponivel(
                    f"o arquivo do TRF1 respondeu '{_SEM_DOCUMENTOS}' para {numero}. "
                    "Processo do PJe (numeração 1xxxxxx) e turma recursal (JEF1) não "
                    "têm inteiro teor neste arquivo: está no PJe 2G "
                    "(pje2g.trf1.jus.br/consultapublica). Não é sinal de que o "
                    "acórdão não exista.")
            raise RuntimeError(
                f"menu do arquivo sem tabela de peças e sem '{_SEM_DOCUMENTOS}' "
                f"({url}) — provável mudança no HTML; conferir listar_pecas")

        for peca in pecas:
            if not peca.caminho.lower().endswith(".doc"):
                resultado.imagens.append(peca)
                continue
            try:
                r = ctx.request.get(HOST + peca.caminho, timeout=60_000)
                if r.status != 200:
                    peca.aviso = f"HTTP {r.status} ao baixar"
                else:
                    peca.texto = texto_do_doc(r.body())
                    if not peca.texto:
                        peca.aviso = "documento sem texto extraível"
            except Exception as e:  # noqa: BLE001
                peca.aviso = f"falha ao baixar ou extrair: {e}"
            resultado.pecas.append(peca)
        return resultado


def formatar(it: InteiroTeor, max_caracteres: int) -> tuple[str, int, bool]:
    """PURO. Texto único, peça por peça, com o teto aplicado no fim.

    Devolve ``(texto, chars_total, truncado)``.
    """
    blocos: List[str] = []
    for p in it.pecas:
        cab = f"######## {p.rotulo.upper()}"
        if p.data_publicacao:
            cab += f" (publicação {p.data_publicacao})"
        corpo = p.texto if p.texto else f"[SEM TEXTO: {p.aviso}]"
        blocos.append(f"{cab}\n{corpo}")
    if it.imagens:
        blocos.append(
            f"######## PÁGINAS DIGITALIZADAS ({len(it.imagens)})\n"
            "Imagem sem camada de texto no arquivo do TRF1; esta ferramenta não "
            "faz OCR. Abrir pelo navegador:\n"
            + "\n".join(f"{p.rotulo}: {HOST}{p.caminho}" for p in it.imagens))
    texto = "\n\n".join(blocos)
    total = len(texto)
    if total > max_caracteres:
        return texto[:max_caracteres], total, True
    return texto, total, False


def obter_e_formatar(numero: str, max_caracteres: int) -> dict:
    it = obter_inteiro_teor(numero)
    texto, total, truncado = formatar(it, max_caracteres)
    return {
        "numero": it.numero,
        "url": it.url,
        "pecas": [p.rotulo for p in it.pecas] + [p.rotulo for p in it.imagens],
        "texto": texto,
        "chars_total": total,
        "truncado": truncado,
    }
