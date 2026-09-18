"""Cliente do buscador oficial da Corte IDH.

O parser é travado contra um fixture REAL gravado da resposta do POST — HTML de
ColdFusion sem contrato nem versão, então mudança de layout tem de aparecer como
teste vermelho aqui e não como censo vazio no crawler.

O teste que mais importa é o NEGATIVO: resposta de erro do ColdFusion, ou corpo
vazio, NÃO pode devolver lista vazia como se fosse 'nada encontrado'. Denominador
zero não é resultado; é comando que não trabalhou.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "corteidh-jurisprudencia"
if str(MODULO) not in sys.path:
    sys.path.insert(0, str(MODULO))

import buscador_oficial as bo  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "corteidh"
HTML_OK = (FIX / "busca_oficial.html").read_text(encoding="utf-8")
HTML_ERRO_CF = (
    "<html><head><title>Error</title></head><body>"
    "<div id='content'>An error occurred while executing the application.</div>"
    "</body></html>"
)


def test_tipos_cobrem_os_dezoito_codigos_do_filtro():
    """Dezoito, não dezenove: o 19º valor do filtro é o `-1` ("todos os
    tipos"), que não é código de tipo. Nome que diz o que o teste confere."""
    for codigo in ("CC", "OC", "SS", "MP", "CO", "EX", "FT", "PS", "PM", "PO",
                   "FV", "FS", "MU", "SM", "SO", "RS", "OA", "RM"):
        assert codigo in bo.TIPOS, codigo


def test_parseia_resultados_do_fixture_real():
    res = bo.parsear_resultados(HTML_OK)
    assert len(res) >= 1
    primeiro = res[0]
    for chave in ("titulo", "url", "tipo", "data"):
        assert chave in primeiro, chave
    assert primeiro["titulo"].strip()


def test_erro_do_coldfusion_estoura_em_vez_de_devolver_vazio():
    """Sem isto, 'não rodou' se lê como 'não há'."""
    with pytest.raises(bo.RespostaInvalida):
        bo.parsear_resultados(HTML_ERRO_CF)


def test_corpo_vazio_estoura():
    with pytest.raises(bo.RespostaInvalida):
        bo.parsear_resultados("")


def test_sem_resultados_e_distinguivel_de_erro():
    """O buscador sinaliza 'nada encontrado' com -1 (ver js/casos.js)."""
    assert bo.parsear_resultados("-1") == []


def test_campos_do_formulario_le_defaults():
    html = ('<form><input name="Texto_busqueda_TXT" value="">'
            '<input name="page_rows" value="20">'
            '<select name="nId_estado_NUM"><option value="T">Todos</option></select>'
            '</form>')
    campos = bo.campos_do_formulario(html)
    assert campos["page_rows"] == "20"
    assert "Texto_busqueda_TXT" in campos
    assert "nId_estado_NUM" in campos


@pytest.mark.rede
def test_busca_real_devolve_casos_do_brasil():
    res = bo.buscar(texto="", tipo="CC", estado="6", pagina_linhas=50)
    assert len(res) >= 11, f"Brasil tem ao menos 11 sentenças; vieram {len(res)}"
