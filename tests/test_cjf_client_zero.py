"""Zero do portal CJF não se aceita de primeira.

Medido em 31/08/2026: o shard de um tribunal pode ficar às escuras e o portal
responde HTTP 200, sem erro nenhum, dizendo "Total TRF2 0 Documento(s)
encontrado(s)". Nessa sessão o TRF2 devolveu 704 para `medicamento E União` e,
dez minutos depois, 0 para QUALQUER termo — inclusive `medicamento` — enquanto
TRF1 e STJ respondiam normais. O `@retry` do cliente não alcança o caso porque
só dispara em exceção, e o canário estrutural cobre apenas o inverso
(total > 0 com parser extraindo 0).
"""

import sys
from pathlib import Path

import pytest

MCP = Path(__file__).resolve().parents[1] / "mcp"
if str(MCP) not in sys.path:
    sys.path.insert(0, str(MCP))

from shared import cjf_client  # noqa: E402


class SessaoFake:
    """Dubla a sessão JSF: devolve os HTMLs da lista, um por chamada de busca."""

    def __init__(self, htmls):
        self.htmls = list(htmls)
        self.chamadas_busca = 0
        self.viewstate = "vs-inicial"
        self._fetched_at = 1.0

    def buscar(self, termo, tribunais):
        self.chamadas_busca += 1
        if not self.htmls:
            raise AssertionError("busca chamada mais vezes do que o teste previa")
        return self.htmls.pop(0)

    def buscar_pagina(self, first, rows=10):
        return "PAGINA_VAZIA"


@pytest.fixture
def portal(monkeypatch):
    """Instala sessão fake + extratores dirigidos por tabela, sem pausa real."""

    monkeypatch.setattr(cjf_client, "_PAUSA_RECONFERIR_ZERO_S", 0)

    estado = {}

    def instalar(htmls, totais, docs):
        sessao = SessaoFake(htmls)
        estado["sessao"] = sessao
        monkeypatch.setattr(cjf_client, "get_cjf_session", lambda: sessao)
        monkeypatch.setattr(
            cjf_client, "extrair_totais", lambda h: totais.get(h, {"TRF2": 0})
        )
        monkeypatch.setattr(
            cjf_client, "extrair_documentos", lambda h: list(docs.get(h, []))
        )
        return sessao

    instalar.estado = estado
    return instalar


UM_DOC = [{"numero": "AG 123", "ementa": "IMPENHORABILIDADE. 40 SALARIOS MINIMOS."}]


class TestZeroReconferido:
    def test_zero_e_reconferido_e_a_segunda_tentativa_vence(self, portal):
        """Portal zera e, na repetição, responde: vale a resposta com acervo."""
        sessao = portal(
            htmls=["HTML_ZERO", "HTML_VIVO"],
            totais={"HTML_ZERO": {"TRF2": 0}, "HTML_VIVO": {"TRF2": 37}},
            docs={"HTML_ZERO": [], "HTML_VIVO": UM_DOC},
        )

        docs, totais = cjf_client.buscar_documentos("medicamento E União", ["TRF2"], 1)

        assert totais == {"TRF2": 37}
        assert len(docs) == 1
        assert sessao.chamadas_busca == 2, "o zero tem de ser reconferido"

    def test_viewstate_e_renovado_antes_de_reconferir(self, portal):
        """A reconferência não pode reusar o ViewState da tentativa que zerou."""
        sessao = portal(
            htmls=["HTML_ZERO", "HTML_VIVO"],
            totais={"HTML_ZERO": {"TRF2": 0}, "HTML_VIVO": {"TRF2": 5}},
            docs={"HTML_ZERO": [], "HTML_VIVO": UM_DOC},
        )

        cjf_client.buscar_documentos("medicamento", ["TRF2"], 1)

        assert sessao.viewstate is None
        assert sessao._fetched_at == 0.0

    def test_zero_confirmado_por_repeticao_e_aceito_sem_erro(self, portal):
        """Zero genuíno: repete, confirma e devolve vazio — sem levantar."""
        sessao = portal(
            htmls=["HTML_ZERO", "HTML_ZERO_2"],
            totais={"HTML_ZERO": {"TRF2": 0}, "HTML_ZERO_2": {"TRF2": 0}},
            docs={"HTML_ZERO": [], "HTML_ZERO_2": []},
        )

        docs, totais = cjf_client.buscar_documentos("termo inexistente", ["TRF2"], 5)

        assert docs == []
        assert sum(totais.values()) == 0
        assert sessao.chamadas_busca == 2

    def test_resultado_cheio_nao_paga_requisicao_extra(self, portal):
        """Caminho felizardo não pode custar uma segunda ida ao portal."""
        sessao = portal(
            htmls=["HTML_VIVO"],
            totais={"HTML_VIVO": {"TRF2": 43}},
            docs={"HTML_VIVO": UM_DOC},
        )

        docs, _ = cjf_client.buscar_documentos("canabidiol", ["TRF2"], 1)

        assert len(docs) == 1
        assert sessao.chamadas_busca == 1

    def test_falha_na_reconferencia_nao_derruba_a_busca(self, portal):
        """Se a repetição estourar, devolve o zero da primeira em vez de propagar."""
        portal(
            htmls=["HTML_ZERO"],  # a 2ª busca levanta AssertionError na SessaoFake
            totais={"HTML_ZERO": {"TRF2": 0}},
            docs={"HTML_ZERO": []},
        )

        docs, totais = cjf_client.buscar_documentos("medicamento", ["TRF2"], 1)

        assert docs == []
        assert sum(totais.values()) == 0


class TestCanarioEstruturalIntacto:
    def test_total_positivo_com_parser_zerado_ainda_levanta(self, portal):
        """A reconferência de zero não pode encobrir o canário do parser quebrado."""
        sessao = portal(
            htmls=["HTML_PARSER_QUEBRADO"],
            totais={"HTML_PARSER_QUEBRADO": {"TRF2": 13}},
            docs={"HTML_PARSER_QUEBRADO": []},
        )

        with pytest.raises(RuntimeError, match="parser extraiu 0"):
            cjf_client.buscar_documentos("qualquer", ["TRF2"], 5)

        assert sessao.chamadas_busca == 1, (
            "total>0 com parser zerado é falha de parser, não zero de portal — "
            "não deve gastar reconferência"
        )
