"""Segmentação por parágrafo das sentenças da Corte IDH.

O índice é citado por parágrafo, então segmentação errada não é defeito de
formatação: é citação errada em peça. O teste central é a MONOTONICIDADE —
numeração que salta denuncia item de lista, nota de rodapé ou número de página
capturado como parágrafo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "corteidh-jurisprudencia"
if str(MODULO) not in sys.path:
    sys.path.insert(0, str(MODULO))

import extrator_paragrafos as ep  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "corteidh" / "sentenca_sintetica.txt"


def test_segmenta_os_seis_paragrafos_e_nada_mais():
    texto = FIXTURE.read_text(encoding="utf-8")
    pars = ep.segmentar(texto)
    assert [p.numero for p in pars] == [1, 2, 3, 4, 5, 6]


def test_nao_captura_item_de_lista_como_paragrafo():
    texto = FIXTURE.read_text(encoding="utf-8")
    pars = ep.segmentar(texto)
    juntos = " ".join(p.texto for p in pars)
    # o item de lista fica DENTRO do parágrafo 3, não virou parágrafo próprio
    p3 = next(p for p in pars if p.numero == 3)
    assert "os documentos apresentados" in p3.texto
    assert juntos.count("os documentos apresentados") == 1


def test_nao_captura_nota_de_rodape_como_paragrafo():
    """Corrigido: o brief continha `... or True`, uma asserção vazia que
    sempre passa. A linha da nota de rodapé não pode aparecer no texto de
    NENHUM parágrafo — nem absorvida como continuação do parágrafo 4."""
    texto = FIXTURE.read_text(encoding="utf-8")
    pars = ep.segmentar(texto)
    assert all("Nota de rodapé que não é parágrafo" not in p.texto for p in pars)
    # o essencial: nenhum parágrafo recebeu o número 1 duas vezes
    assert len([p for p in pars if p.numero == 1]) == 1


def test_descarta_linha_numerica_sem_ponto_apos_linha_em_branco():
    """Regra nova: nota de rodapé real tem a forma de um número (1 a 3
    dígitos) seguido de espaço e texto — a MESMA forma sintática do início de
    parágrafo, só sem o ponto. O que a diferencia de uma continuação comum é
    vir logo após uma linha em branco, que no layout da Corte é onde a nota
    de rodapé se destaca do corpo do parágrafo anterior."""
    texto = (
        "1. Primeiro paragrafo do documento que continua por mais de uma "
        "linha de texto.\n"
        "\n"
        "12 Ver depoimento de fl. 45, prestado em audiência.\n"
        "\n"
        "2. Segundo paragrafo.\n"
    )
    pars = ep.segmentar(texto)
    assert [p.numero for p in pars] == [1, 2]
    assert "Ver depoimento" not in " ".join(p.texto for p in pars)


def test_continuacao_iniciada_por_numero_sobrevive_quando_nao_apos_branco():
    """A regra nova não pode comer texto legítimo: uma linha de continuação
    que começa por número (um ano, uma quantidade) e vem colada ao texto
    anterior — sem linha em branco entre elas — não é nota de rodapé e tem
    de ser preservada dentro do parágrafo corrente."""
    texto = (
        "1. O relatório anual descreve os fatos ocorridos naquele periodo.\n"
        "2019 foi o ano em que a situação se agravou, segundo testemunhas.\n"
        "5 pessoas participaram do ato conforme testemunhas ouvidas.\n"
        "\n"
        "2. Segundo paragrafo.\n"
    )
    pars = ep.segmentar(texto)
    p1 = next(p for p in pars if p.numero == 1)
    assert "2019 foi o ano em que a situação se agravou" in p1.texto
    assert "5 pessoas participaram do ato" in p1.texto


def test_descarta_numero_de_pagina():
    texto = FIXTURE.read_text(encoding="utf-8")
    pars = ep.segmentar(texto)
    assert all(p.texto.strip() != "-" for p in pars)
    assert "- 3 -" not in " ".join(p.texto for p in pars)


def test_paragrafo_apos_marcador_de_pagina_e_capturado():
    texto = FIXTURE.read_text(encoding="utf-8")
    pars = ep.segmentar(texto)
    p5 = next(p for p in pars if p.numero == 5)
    assert "continua o raciocínio anterior" in p5.texto


def test_relatorio_lacunas_aponta_numero_faltante():
    pars = [ep.Paragrafo(1, "a"), ep.Paragrafo(2, "b"), ep.Paragrafo(5, "e")]
    assert ep.relatorio_lacunas(pars) == [3, 4]


def test_relatorio_lacunas_vazio_quando_contiguo():
    pars = [ep.Paragrafo(1, "a"), ep.Paragrafo(2, "b"), ep.Paragrafo(3, "c")]
    assert ep.relatorio_lacunas(pars) == []


def test_texto_vazio_devolve_lista_vazia_sem_estourar():
    assert ep.segmentar("") == []
    assert ep.segmentar("   \n\n  ") == []
