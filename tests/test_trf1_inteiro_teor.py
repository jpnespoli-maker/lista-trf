"""Inteiro teor do TRF1 pelo arquivo do tribunal: menu, extração do .doc e formato.

A base regional do CJF devolve só a ementa. Medido em 27/10/2026 no RSE
0005984-10.2018.4.01.3802: a ementa dizia "residente em outro país", e só o
voto contava que o réu, citado nos EUA, aceitara comparecimento virtual
bimestral. O arquivo serve o voto em ``.doc`` Word 97, cujo texto se lê pela
tabela de peças — em cp1252 comprimido OU em UTF-16, conforme quem gravou.

As fixtures em ``fixtures/trf1/`` são SINTÉTICAS e sem metadado de autor: uma
gravada pelo Word (peça cp1252 com campo HYPERLINK), outra pelo LibreOffice
(peça UTF-16 com caractere fora do cp1252). O transporte (Edge por CDP) não se
testa aqui; foi medido ponta a ponta contra o arquivo real.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "trf1-jurisprudencia"
for p in (MODULO, MODULO.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import arquivo_trf1 as A  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "trf1"

# Forma do menu real (medida em 27/10/2026): o portal lista as certidões
# primeiro e o relatório por último, com a data de publicação na 2ª coluna.
MENU = """
<table>
<tr><td><a href="/AGText/2018/0005984/doc1.doc" target="_blank">Certid&atilde;o</a></td>
    <td align="center"><a href="/AGText/2018/0005984/doc1.doc">11/01/2019</a></td></tr>
<tr><td><a href="/AGText/2018/0005984/doc2.doc" target="_blank">Ementa</a></td>
    <td align="center"><a href="/AGText/2018/0005984/doc2.doc">11/01/2019</a></td></tr>
<tr><td><a href="/AGText/2018/0005984/doc3.doc" target="_blank">Voto</a></td>
    <td align="center"><a href="/AGText/2018/0005984/doc3.doc">11/01/2019</a></td></tr>
<tr><td><a href="/AGText/2018/0005984/doc4.doc" target="_blank">Relat&oacute;rio</a></td>
    <td align="center"><a href="/AGText/2018/0005984/doc4.doc">11/01/2019</a></td></tr>
</table>
"""

MENU_TIFF = """
<tr><td><a href="/AGText/1996/0025609/pag001.TIFF">P&aacute;gina 1</a></td>
    <td><a href="/AGText/1996/0025609/pag001.TIFF">01/01/1997</a></td></tr>
"""


class TestDigitosCnj:
    def test_cnj_formatado_vira_20_digitos(self) -> None:
        assert A.digitos_cnj("0005984-10.2018.4.01.3802") == "00059841020184013802"

    def test_numero_incompleto_levanta(self) -> None:
        with pytest.raises(ValueError, match="20 dígitos"):
            A.digitos_cnj("123")


class TestListarPecas:
    def test_ordem_de_leitura_relatorio_voto_ementa_certidao(self) -> None:
        rotulos = [p.rotulo for p in A.listar_pecas(MENU)]
        assert rotulos == ["Relatório", "Voto", "Ementa", "Certidão"]

    def test_caminho_e_data_preservados(self) -> None:
        voto = next(p for p in A.listar_pecas(MENU) if p.rotulo == "Voto")
        assert voto.caminho == "/AGText/2018/0005984/doc3.doc"
        assert voto.data_publicacao == "11/01/2019"

    def test_menu_sem_tabela_da_lista_vazia(self) -> None:
        assert A.listar_pecas("<p>Nenhum documento encontrado</p>") == []

    def test_pagina_tiff_e_listada(self) -> None:
        [p] = A.listar_pecas(MENU_TIFF)
        assert p.caminho.endswith(".TIFF")


@pytest.fixture
def olefile():
    # Dependência do venv do trf1-jurisprudencia, não da raiz: sem ela só os
    # testes de .doc pulam, e os puros seguem rodando.
    return pytest.importorskip("olefile")


@pytest.mark.usefixtures("olefile")
class TestTextoDoDoc:
    def test_word_peca_cp1252_com_campo_hyperlink(self) -> None:
        texto = A.texto_do_doc((FIXTURES / "cp1252_campo_word.doc").read_bytes())
        assert texto == (
            "VOTO\nA condição de comparecimento é cumprida por meio eletrônico; "
            "ver o portal de exemplo.\nÉ como voto.")

    def test_libreoffice_peca_utf16_fora_do_cp1252(self) -> None:
        texto = A.texto_do_doc(
            (FIXTURES / "sintetico_utf16_libreoffice.doc").read_bytes())
        assert texto.startswith("RELATÓRIO\nO recorrente reside em Lisboa")
        assert "≥ 1 ano — símbolo Ω" in texto

    def test_word_6_95_levanta_em_vez_de_ler_lixo(self, tmp_path: Path, olefile) -> None:
        alvo = tmp_path / "word95.doc"
        alvo.write_bytes((FIXTURES / "cp1252_campo_word.doc").read_bytes())
        ole = olefile.OleFileIO(str(alvo), write_mode=True)
        wd = bytearray(ole.openstream("WordDocument").read())
        struct.pack_into("<H", wd, 2, 104)  # nFib do Word 95
        ole.write_stream("WordDocument", bytes(wd))
        ole.close()
        with pytest.raises(ValueError, match="nFib 104"):
            A.texto_do_doc(alvo.read_bytes())


class TestTextoPuroEFormatoRecusado:
    def test_certidao_em_texto_puro_cp1252(self) -> None:
        bruto = "CERTIDÃO\r\nCertifico que a Turma, à unanimidade, deu provimento.\r\n"
        assert A.texto_do_doc(bruto.encode("cp1252")) == (
            "CERTIDÃO\n\nCertifico que a Turma, à unanimidade, deu provimento.")

    def test_rtf_levanta(self) -> None:
        with pytest.raises(ValueError, match="formato não suportado"):
            A.texto_do_doc(b"{\\rtf1\\ansi texto}")

    def test_binario_com_nul_levanta(self) -> None:
        with pytest.raises(ValueError, match="formato não suportado"):
            A.texto_do_doc(b"II*\x00" + b"\x00" * 100)


class TestLimparTextoWord:
    def test_campo_fica_so_com_o_resultado(self) -> None:
        bruto = "ver \x13 HYPERLINK \"https://x.invalid\" \x14o portal\x15."
        assert A.limpar_texto_word(bruto) == "ver o portal."

    def test_campo_sem_resultado_some(self) -> None:
        assert A.limpar_texto_word("a\x13 PAGE \x15b") == "ab"

    def test_campo_aninhado(self) -> None:
        bruto = "x\x13 IF \x13 PAGE \x141\x15 \x14dentro\x15y"
        assert A.limpar_texto_word(bruto) == "xdentroy"

    def test_controles_de_paragrafo_e_celula(self) -> None:
        assert A.limpar_texto_word("a\rb\x0bc\x07d") == "a\nb\nc\td"


class TestFormatar:
    def _teor(self) -> A.InteiroTeor:
        return A.InteiroTeor(
            numero="0005984-10.2018.4.01.3802",
            url=A.URL_MENU.format(digitos="00059841020184013802"),
            pecas=[
                A.Peca("Voto", "/v.doc", "11/01/2019", texto="É como voto."),
                A.Peca("Certidão", "/c.doc", aviso="HTTP 404 ao baixar"),
            ],
        )

    def test_cabecalho_por_peca_e_aviso_quando_sem_texto(self) -> None:
        texto, total, truncado = A.formatar(self._teor(), 10_000)
        assert "######## VOTO (publicação 11/01/2019)\nÉ como voto." in texto
        assert "######## CERTIDÃO\n[SEM TEXTO: HTTP 404 ao baixar]" in texto
        assert (total, truncado) == (len(texto), False)

    def test_teto_trunca_e_informa_o_total(self) -> None:
        cheio, total, _ = A.formatar(self._teor(), 10_000)
        texto, total_t, truncado = A.formatar(self._teor(), 20)
        assert texto == cheio[:20]
        assert (total_t, truncado) == (total, True)

    def test_paginas_digitalizadas_listadas_com_link(self) -> None:
        it = A.InteiroTeor(numero="n", url="u", imagens=[
            A.Peca("Página 1", "/AGText/1996/0025609/pag001.TIFF")])
        texto, _, _ = A.formatar(it, 10_000)
        assert "PÁGINAS DIGITALIZADAS (1)" in texto
        assert "Página 1: https://arquivo.trf1.jus.br/AGText/1996/0025609/pag001.TIFF" in texto
