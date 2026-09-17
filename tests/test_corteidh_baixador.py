"""Download de PDF da Corte IDH.

O teste central é o do INSTRUMENTO QUE MENTE: quando o idioma pedido não existe,
o servidor devolve HTTP **206** com corpo HTML de erro — medido em 2026-09-17.
Ler o status code daria sucesso para um arquivo que não é PDF, e o índice
nasceria com lixo. A validação é por magic bytes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "corteidh-jurisprudencia"
if str(MODULO) not in sys.path:
    sys.path.insert(0, str(MODULO))

import baixador  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "corteidh"
HTML_206 = (FIX / "resposta_206_html.bin").read_bytes()
PDF_FALSO = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF\n"


class _RespostaFalsa:
    def __init__(self, status, corpo):
        self.status_code = status
        self.content = corpo

    def raise_for_status(self):
        pass


class _SessaoFalsa:
    """Devolve respostas roteadas por URL e conta as chamadas."""

    def __init__(self, rotas):
        self.rotas = rotas
        self.chamadas = []

    def get(self, url, **kw):
        self.chamadas.append(url)
        return self.rotas.get(url, _RespostaFalsa(404, b"nao achei"))


def test_eh_pdf_reconhece_magic_bytes():
    assert baixador.eh_pdf(PDF_FALSO) is True


def test_eh_pdf_rejeita_html_mesmo_grande():
    assert baixador.eh_pdf(HTML_206) is False


def test_eh_pdf_rejeita_vazio():
    assert baixador.eh_pdf(b"") is False


def test_http_206_com_html_nao_passa_por_pdf():
    """O modo de falha medido: status 'de sucesso' com corpo errado."""
    ses = _SessaoFalsa({"http://x/a_por.pdf": _RespostaFalsa(206, HTML_206)})
    with pytest.raises(baixador.PdfInvalido):
        baixador.baixar_pdf("http://x/a_por.pdf", sessao=ses, tentativas=1)


def test_baixar_pdf_devolve_bytes_quando_valido():
    ses = _SessaoFalsa({"http://x/a_por.pdf": _RespostaFalsa(200, PDF_FALSO)})
    assert baixador.baixar_pdf("http://x/a_por.pdf", sessao=ses) == PDF_FALSO


def test_cascata_prefere_portugues():
    ses = _SessaoFalsa({
        "http://x/seriec_149_por.pdf": _RespostaFalsa(200, PDF_FALSO),
        "http://x/seriec_149_esp.pdf": _RespostaFalsa(200, PDF_FALSO),
    })
    idioma, corpo, url = baixador.baixar_melhor_idioma(
        "http://x/seriec_149", sessao=ses, pausa_s=0)
    assert idioma == "por"
    assert url.endswith("_por.pdf")
    assert corpo == PDF_FALSO


def test_cascata_cai_para_espanhol_quando_portugues_e_html():
    ses = _SessaoFalsa({
        "http://x/seriec_349_por.pdf": _RespostaFalsa(206, HTML_206),
        "http://x/seriec_349_esp.pdf": _RespostaFalsa(200, PDF_FALSO),
    })
    idioma, corpo, url = baixador.baixar_melhor_idioma(
        "http://x/seriec_349", sessao=ses, pausa_s=0)
    assert idioma == "esp"
    assert url.endswith("_esp.pdf")


def test_cascata_tenta_na_ordem_por_esp_ing_fra():
    """`pausa_s=0` em todo teste com dublê: a pausa é comportamento de REDE,
    não contrato a asseverar, e com o default de 2s estes quatro testes
    dormiriam ~8s numa suíte que roda a cada iteração. O default de produção
    fica intacto — quem o exercita é o teste marcado `rede`, ali embaixo."""
    ses = _SessaoFalsa({"http://x/y_fra.pdf": _RespostaFalsa(200, PDF_FALSO)})
    idioma, _, _ = baixador.baixar_melhor_idioma("http://x/y", sessao=ses, pausa_s=0)
    assert idioma == "fra"
    assert ses.chamadas == [
        "http://x/y_por.pdf", "http://x/y_esp.pdf",
        "http://x/y_ing.pdf", "http://x/y_fra.pdf",
    ]


def test_cascata_sem_nenhum_idioma_estoura():
    ses = _SessaoFalsa({})
    with pytest.raises(baixador.PdfInvalido):
        baixador.baixar_melhor_idioma("http://x/z", sessao=ses, pausa_s=0)


def test_sha256_estavel():
    assert baixador.sha256(PDF_FALSO) == baixador.sha256(PDF_FALSO)
    assert baixador.sha256(PDF_FALSO) != baixador.sha256(PDF_FALSO + b" ")


@pytest.mark.rede
def test_ximenes_lopes_em_portugues_de_verdade():
    """Controle positivo contra a fonte real. Marcado `rede`: fora do CI."""
    idioma, corpo, url = baixador.baixar_melhor_idioma(
        "https://www.corteidh.or.cr/docs/casos/articulos/seriec_149")
    assert idioma == "por"
    assert baixador.eh_pdf(corpo)
    assert len(corpo) > 100_000
