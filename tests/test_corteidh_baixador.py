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
    """O modo de falha medido contra o site real: status 'de sucesso' com
    corpo errado."""
    ses = _SessaoFalsa({"http://x/a_por.pdf": _RespostaFalsa(206, HTML_206)})
    with pytest.raises(baixador.PdfInvalido):
        baixador.baixar_pdf("http://x/a_por.pdf", sessao=ses, tentativas=1)


def test_status_200_com_corpo_HTML_tambem_e_recusado():
    """Este é o teste que prova que a validação é por CONTEÚDO e não por
    status — e sem ele a tarefa inteira passava por cima do seu próprio ponto.

    Medido por mutação: trocar os magic bytes por `status_code == 200` matava
    **ZERO** testes. O irmão acima usa 206 porque foi o que o site devolveu,
    e um mutante que exigisse 200 rejeitaria o 206 pelo motivo errado — o
    fixture tornava as duas hipóteses indistinguíveis.

    Com o 200 aqui, a única implementação que passa nos dois é a que olha o
    corpo. É a trava contra alguém "simplificar" para uma checagem de status.
    """
    ses = _SessaoFalsa({"http://x/a_por.pdf": _RespostaFalsa(200, HTML_206)})
    with pytest.raises(baixador.PdfInvalido):
        baixador.baixar_pdf("http://x/a_por.pdf", sessao=ses, tentativas=1)


def test_cascata_recusa_200_com_html_e_segue_para_o_proximo_idioma():
    """O mesmo pelo lado da cascata: 200 com HTML no português não pode ser
    aceito como 'achei', tem de cair para o espanhol."""
    ses = _SessaoFalsa({
        "http://x/seriec_349_por.pdf": _RespostaFalsa(200, HTML_206),
        "http://x/seriec_349_esp.pdf": _RespostaFalsa(200, PDF_FALSO),
    })
    idioma, _, url = baixador.baixar_melhor_idioma(
        "http://x/seriec_349", sessao=ses, pausa_s=0)
    assert idioma == "esp"
    assert url.endswith("_esp.pdf")


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


# --- O caminho de RETENTATIVA, que não tinha teste nenhum ------------------
# Provado por mutação na revisão: zerar `_TRANSITORIOS` matava 0 testes, e
# trocar o `except Exception` por `raise` matava 0. Ou seja, o backoff inteiro
# podia ser apagado sem a suíte acusar — justamente o mecanismo que existe
# porque o site estrangula quem o martela (522/403 medidos).

class _SessaoSequencial:
    """Devolve, em ordem, uma resposta por chamada. Item que seja exceção é
    levantado — é assim que se dubla `ConnectionError`/timeout de rede."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = 0

    def get(self, url, **kw):
        self.chamadas += 1
        item = self.respostas.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_status_transitorio_e_retentado_e_o_pdf_seguinte_e_aceito(monkeypatch):
    """503 na primeira, PDF na segunda: tem de devolver o PDF. Sem o ramo
    `_TRANSITORIOS`, o 503 viraria PdfInvalido imediato e nada seria
    retentado."""
    monkeypatch.setattr(baixador.time, "sleep", lambda _s: None)
    ses = _SessaoSequencial([
        _RespostaFalsa(503, b"<html>indisponivel</html>"),
        _RespostaFalsa(200, PDF_FALSO),
    ])
    assert baixador.baixar_pdf("http://x/a.pdf", sessao=ses, tentativas=3) == PDF_FALSO
    assert ses.chamadas == 2


def test_excecao_de_rede_e_retentada(monkeypatch):
    """Timeout na primeira, PDF na segunda. Sem o `except Exception`, a
    primeira exceção subiria e o backoff nunca aconteceria."""
    monkeypatch.setattr(baixador.time, "sleep", lambda _s: None)
    ses = _SessaoSequencial([
        ConnectionError("conexao caiu"),
        _RespostaFalsa(200, PDF_FALSO),
    ])
    assert baixador.baixar_pdf("http://x/a.pdf", sessao=ses, tentativas=3) == PDF_FALSO
    assert ses.chamadas == 2


def test_transitorio_ate_o_fim_estoura_dizendo_qual_foi(monkeypatch):
    monkeypatch.setattr(baixador.time, "sleep", lambda _s: None)
    ses = _SessaoSequencial([_RespostaFalsa(503, b"x")] * 2)
    with pytest.raises(baixador.PdfInvalido) as exc:
        baixador.baixar_pdf("http://x/a.pdf", sessao=ses, tentativas=2)
    assert "503" in str(exc.value)
    assert ses.chamadas == 2


def test_status_permanente_nao_e_retentado(monkeypatch):
    """404 não é transitório: estoura na primeira, sem gastar espera."""
    monkeypatch.setattr(baixador.time, "sleep", lambda _s: None)
    ses = _SessaoSequencial([_RespostaFalsa(404, b"nao achei")])
    with pytest.raises(baixador.PdfInvalido):
        baixador.baixar_pdf("http://x/a.pdf", sessao=ses, tentativas=3)
    assert ses.chamadas == 1, "status permanente não pode ser retentado"


# --- A CAUSA por idioma, que o crawler precisa para decidir ----------------

def test_cascata_esgotada_diz_a_causa_de_CADA_idioma(monkeypatch):
    """Site fora do ar e documento sem tradução produziam mensagem IDÊNTICA —
    e as condutas são opostas: a primeira se repete depois, a segunda se
    registra e segue. A mensagem final tem de nomear a causa por idioma."""
    monkeypatch.setattr(baixador.time, "sleep", lambda _s: None)
    ses = _SessaoSequencial([_RespostaFalsa(503, b"fora do ar")] * 8)
    with pytest.raises(baixador.PdfInvalido) as exc:
        baixador.baixar_melhor_idioma("http://x/seriec_999", sessao=ses,
                                      pausa_s=0)
    msg = str(exc.value)
    for idioma in ("por", "esp", "ing", "fra"):
        assert f"{idioma}:" in msg, f"faltou a causa de {idioma}: {msg}"
    assert "503" in msg, "a mensagem tem de deixar ver que foi indisponibilidade"


def test_causa_de_idioma_ausente_e_DISTINGUIVEL_de_site_fora_do_ar(monkeypatch):
    """O par que prova a discriminação: os dois cenários esgotam a cascata,
    e as mensagens NÃO podem ser iguais."""
    monkeypatch.setattr(baixador.time, "sleep", lambda _s: None)

    fora = _SessaoSequencial([_RespostaFalsa(503, b"fora")] * 8)
    with pytest.raises(baixador.PdfInvalido) as e_fora:
        baixador.baixar_melhor_idioma("http://x/s_1", sessao=fora, pausa_s=0)

    ausente = _SessaoSequencial([_RespostaFalsa(206, HTML_206)] * 4)
    with pytest.raises(baixador.PdfInvalido) as e_ausente:
        baixador.baixar_melhor_idioma("http://x/s_1", sessao=ausente, pausa_s=0)

    assert str(e_fora.value) != str(e_ausente.value)
    assert "503" in str(e_fora.value)
    assert "206" in str(e_ausente.value)


def test_tentativas_zero_diz_que_nenhuma_chamada_foi_feita():
    """Erro de quem chama, mas o diagnóstico é de quem lê o log: sem isto
    saía 'desistiu depois de 0 — ' com a causa em branco."""
    ses = _SessaoSequencial([])
    with pytest.raises(baixador.PdfInvalido) as exc:
        baixador.baixar_pdf("http://x/a.pdf", sessao=ses, tentativas=0)
    assert "nenhuma tentativa" in str(exc.value)
    assert ses.chamadas == 0


@pytest.mark.rede
def test_ximenes_lopes_em_portugues_de_verdade():
    """Controle positivo contra a fonte real. Marcado `rede`: fora do CI."""
    idioma, corpo, url = baixador.baixar_melhor_idioma(
        "https://www.corteidh.or.cr/docs/casos/articulos/seriec_149")
    assert idioma == "por"
    assert baixador.eh_pdf(corpo)
    assert len(corpo) > 100_000
