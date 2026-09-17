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


def test_nota_de_rodape_multilinha_so_a_primeira_linha_e_removida():
    """Round 5, RECUO deliberado e medido (ver task-1-report.md e a
    "oitava armadilha" na docstring do módulo): o consumo em bloco dos
    rounds 1/3 foi retirado porque engolia corpo legítimo de parágrafo
    quando uma nota de UMA linha, já terminada, era seguida de corpo
    colado sem linha em branco (ver
    test_item_de_lista_apos_nota_de_uma_linha_sobrevive, que reproduz o
    achado da re-revisão). O preço: nota que se estende por mais de uma
    linha só tem a PRIMEIRA removida — "par. 30" (a continuação, sem
    marcador próprio) volta a vazar para o parágrafo anterior. Entre
    nota mal cortada (contaminação VISÍVEL) e corpo engolido (perda
    INVISÍVEL), este módulo escolhe a primeira."""
    texto = (
        "1. A Corte e competente para conhecer do presente caso1.\n"
        "\n"
        "1 Cfr. Caso Velasquez Rodriguez Vs. Honduras. Excecoes Preliminares.\n"
        "Sentenca de 26 de junho de 1987, par. 30.\n"
        "\n"
        "2. Prosseguindo o exame do merito.\n"
    )
    pars = ep.segmentar(texto)
    assert [p.numero for p in pars] == [1, 2]
    p1 = next(p for p in pars if p.numero == 1)
    # a linha do marcador (com "Cfr. Caso Velasquez Rodriguez...") some
    assert "Velasquez Rodriguez" not in p1.texto
    # mas a CONTINUACAO da nota, sem marcador proprio na linha, vaza —
    # o preco aceito do recuo do round 5
    assert "par. 30" in p1.texto
    assert ep.relatorio_lacunas(pars) == []


def test_nota_de_tres_linhas_so_a_primeira_e_removida_o_resto_vaza():
    """Generaliza o teste anterior: não importa se a nota tem 2 ou 3
    linhas de continuação — a janela fixa do round 5 sempre remove só a
    primeira, qualquer que seja o tamanho real da nota."""
    texto = (
        "1. Primeiro paragrafo do documento.\n"
        "\n"
        "2 Cfr. Caso Genie Lacayo Vs. Nicaragua. Excecoes Preliminares,\n"
        "Fondo, Reparaciones y Costas. Sentenca de 29 de janeiro de 1997,\n"
        "par. 45, e Caso Loayza Tamayo Vs. Peru, par. 12.\n"
        "\n"
        "2. Segundo paragrafo do documento.\n"
    )
    pars = ep.segmentar(texto)
    assert [p.numero for p in pars] == [1, 2]
    juntos = " ".join(p.texto for p in pars)
    assert "Genie Lacayo" not in juntos
    assert "Loayza Tamayo" in juntos


def test_paragrafo_seguinte_a_nota_multilinha_e_capturado_normalmente():
    """O parágrafo que vem depois da nota multilinha não pode ser afetado —
    a numeração segue normal, sem lacuna nem duplicidade."""
    texto = (
        "1. A Corte e competente para conhecer do presente caso1.\n"
        "\n"
        "1 Cfr. Caso Velasquez Rodriguez Vs. Honduras. Excecoes Preliminares.\n"
        "Sentenca de 26 de junho de 1987, par. 30.\n"
        "\n"
        "2. Prosseguindo o exame do merito.\n"
    )
    pars = ep.segmentar(texto)
    p2 = next(p for p in pars if p.numero == 2)
    assert p2.texto == "Prosseguindo o exame do merito."


def test_valor_de_tabela_de_indenizacao_sobrevive_na_linha_isolada():
    """Achado 2 da revisão: a regra antiga de "linha só com número" não
    tinha gate de contexto e descartava QUALQUER linha só de dígitos —
    inclusive um valor de indenização isolado em célula própria pela
    extração de PDF (quantum é o dado mais sensível de uma peça)."""
    texto = (
        "1. A Corte fixa como indenizacao por dano material o seguinte "
        "valor:\n"
        "\n"
        "10000\n"
        "\n"
        "dolares dos Estados Unidos da America, a ser pago no prazo de um "
        "ano.\n"
        "\n"
        "2. Quanto ao dano imaterial...\n"
    )
    pars = ep.segmentar(texto)
    p1 = next(p for p in pars if p.numero == 1)
    assert "10000" in p1.texto
    assert ep.relatorio_lacunas(pars) == []


def test_numero_de_pagina_de_ate_tres_digitos_continua_descartado():
    """O conserto do round 2 não pode reabrir o que já funcionava: número de
    página plausível (até 3 dígitos), solto após linha em branco, continua
    sendo ruído. A regra que discrimina é a CONTAGEM DE DÍGITOS (até 3), não
    mais a posição — o round 1 exigia linha em branco antes, e esse gate foi
    retirado no round 2 por causar regressão (ver
    test_marcador_de_nota_em_sobrescrito_no_meio_do_paragrafo_nao_vaza)."""
    texto = (
        "1. Primeiro paragrafo que continua por varias linhas ate o fim "
        "da pagina.\n"
        "\n"
        "12\n"
        "\n"
        "2. Segundo paragrafo, ja na pagina seguinte.\n"
    )
    pars = ep.segmentar(texto)
    assert [p.numero for p in pars] == [1, 2]
    assert "12" not in " ".join(p.texto for p in pars)


def test_marcador_de_nota_em_sobrescrito_no_meio_do_paragrafo_nao_vaza():
    """Regressão do round 1: o gate "só é ruído após linha em branco" da
    regra de linha só-de-dígitos deixava passar o marcador de nota de
    rodapé em sobrescrito quando ele cai no MEIO do parágrafo — sem linha
    em branco antes —, porque o PyMuPDF às vezes extrai esse marcador como
    linha própria. Medido em PDF real: 46 de 230 parágrafos de um documento
    ganharam um dígito solto colado ao texto. O conserto do round 2 retira
    a condição de posição: linha só-de-dígitos (até 3) é ruído em QUALQUER
    posição."""
    texto = (
        "1. A vitima foi identificada como Jose Alves.\n"
        "3\n"
        "Os representantes apresentaram prova documental.\n"
        "\n"
        "2. Segundo paragrafo.\n"
    )
    pars = ep.segmentar(texto)
    p1 = next(p for p in pars if p.numero == 1)
    assert p1.texto == (
        "A vitima foi identificada como Jose Alves. "
        "Os representantes apresentaram prova documental."
    )


def test_nota_apos_nota_nao_vaza_citacao_nem_url_para_paragrafo_anterior():
    """Achado 3 da revisão (round 3): quando um bloco de rodapé de página
    tem MAIS DE UMA nota consecutiva, sem linha em branco entre elas, o
    CORPO de cada nota (a citação em si — "Cf. Autor. Obra. Disponível em:
    URL.") não casava nenhuma regra de ruído, porque começa com uma
    palavra, não com dígito. O corpo inteiro (inclusive a URL de outro
    documento) vazava para dentro do parágrafo anterior. Medido em PDF real
    (Caso Barbosa de Souza Vs. Brasil, par. 51): um bloco de 5 notas
    consecutivas (50 a 54) vazou por inteiro. Cenário reduzido aqui a duas
    notas."""
    texto = (
        "1. Entre 2006 e 2010 o Brasil ficou em setimo lugar1.\n"
        "\n"
        "50\n"
        "Cf. WAISELFISZ, Julio. Mapa da Violencia 2015. Disponivel em:\n"
        "http://www.onumulheres.org.br/mapa2015.pdf.\n"
        "51 Cf. Lei no 13.104 de 2015. Disponivel em:\n"
        "http://www.planalto.gov.br/lei13104.htm.\n"
        "\n"
        "2. A Corte pondera as alegacoes.\n"
    )
    pars = ep.segmentar(texto)
    assert [p.numero for p in pars] == [1, 2]
    p1 = next(p for p in pars if p.numero == 1)
    assert p1.texto == "Entre 2006 e 2010 o Brasil ficou em setimo lugar1."
    assert "WAISELFISZ" not in p1.texto
    assert "http" not in p1.texto
    assert ep.relatorio_lacunas(pars) == []


def test_paragrafo_seguinte_a_bloco_de_notas_e_capturado_por_inteiro():
    """O parágrafo que vem depois de um bloco de rodapé com várias notas
    consecutivas tem de ser capturado inteiro, sem lacuna — o "modo nota"
    tem de saber sair no marcador de página tanto quanto na linha em
    branco."""
    texto = (
        "1. Primeiro paragrafo do documento1.\n"
        "\n"
        "50\n"
        "Cf. Referencia da primeira nota do bloco.\n"
        "51 Cf. Referencia da segunda nota, colada a primeira.\n"
        "\n"
        "2. Segundo paragrafo inteiro, capturado sem perda de texto algum "
        "depois da zona de nota.\n"
    )
    pars = ep.segmentar(texto)
    p2 = next(p for p in pars if p.numero == 2)
    assert p2.texto == (
        "Segundo paragrafo inteiro, capturado sem perda de texto algum "
        "depois da zona de nota."
    )


# --- Round 5: a janela fixa de consumo (1 ou 2 linhas, nunca mais) tem
# de aguentar cada combinação plausível do que vem logo depois de uma
# nota reconhecida, sem linha em branco de separação — é o próprio
# ponto cego que a re-revisão achou. Os testes abaixo saem da
# ENUMERAÇÃO de entrada/alcance da docstring do módulo (oitava
# armadilha), não dos dois PDFs de calibração.


def test_item_de_lista_apos_nota_de_uma_linha_sobrevive():
    """Reprodução exata do achado da re-revisão contra o commit 466a59d:
    nota de UMA linha, já terminada (marcador e corpo na mesma linha,
    `_NUMERO_SEM_PONTO`), seguida — sem linha em branco — de um item de
    lista (`b)`). Antes do round 5, o consumo em bloco engolia o item
    inteiro, sem lacuna e sem sinal algum de perda."""
    texto = (
        "1. O Estado alegou o seguinte:\n"
        "\n"
        "a) os primeiros indicios seguem o laudo1.\n"
        "\n"
        "1 Cf. Laudo medico as fls. 12.\n"
        "b) o processo penal observou as garantias fundamentais.\n"
        "\n"
        "2. A Corte pondera as alegacoes.\n"
    )
    pars = ep.segmentar(texto)
    assert [p.numero for p in pars] == [1, 2]
    p1 = next(p for p in pars if p.numero == 1)
    assert "os primeiros indicios seguem o laudo" in p1.texto
    assert "o processo penal observou as garantias fundamentais" in p1.texto
    assert "Laudo medico" not in p1.texto
    assert ep.relatorio_lacunas(pars) == []


def test_item_de_lista_apos_nota_com_marcador_isolado_sobrevive():
    """A mesma combinação (item de lista colado, sem linha em branco),
    agora para o outro tipo de entrada — marcador ISOLADO cujo corpo
    está na linha seguinte (janela de exatamente 2 linhas)."""
    texto = (
        "1. O Estado alegou o seguinte:\n"
        "\n"
        "a) primeiro item da lista.\n"
        "\n"
        "50\n"
        "Cf. Nota isolada com corpo na linha seguinte.\n"
        "b) segundo item, que tem de sobreviver.\n"
        "\n"
        "2. Prosseguindo.\n"
    )
    pars = ep.segmentar(texto)
    assert [p.numero for p in pars] == [1, 2]
    p1 = next(p for p in pars if p.numero == 1)
    assert "primeiro item da lista" in p1.texto
    assert "segundo item, que tem de sobreviver" in p1.texto
    assert "Nota isolada" not in p1.texto


def test_corpo_colado_apos_nota_de_uma_linha_sobrevive():
    """"O que vem depois" nem sempre é item de lista — pode ser prosa
    comum colada sem linha em branco. Tem de sobreviver do mesmo jeito."""
    texto = (
        "1. Primeiro trecho do paragrafo.\n"
        "1 Cf. Nota curta e completa.\n"
        "Prosa comum que continua o paragrafo sem marcador algum.\n"
        "\n"
        "2. Segundo paragrafo.\n"
    )
    pars = ep.segmentar(texto)
    p1 = next(p for p in pars if p.numero == 1)
    assert "Prosa comum que continua o paragrafo" in p1.texto
    assert "Nota curta e completa" not in p1.texto


def test_marcador_de_pagina_logo_apos_nota_de_uma_linha_e_reconhecido():
    """Marcador de página colado (sem linha em branco) logo após uma
    nota de uma linha: o marcador de página tem de continuar sendo
    reconhecido no próximo giro do laço principal, não pela nota."""
    texto = (
        "1. Texto do paragrafo antes da nota1.\n"
        "1 Cf. Nota curta e completa.\n"
        "- 5 -\n"
        "2. Segundo paragrafo apos a pagina.\n"
    )
    pars = ep.segmentar(texto)
    assert [p.numero for p in pars] == [1, 2]
    p1 = next(p for p in pars if p.numero == 1)
    p2 = next(p for p in pars if p.numero == 2)
    assert p1.texto == "Texto do paragrafo antes da nota1."
    assert p2.texto == "Segundo paragrafo apos a pagina."


def test_inicio_de_paragrafo_logo_apos_nota_de_uma_linha_e_capturado():
    """Início de parágrafo exato colado (sem linha em branco) logo após
    uma nota de uma linha: o próximo parágrafo tem de ser capturado
    normalmente, sem lacuna nem duplicidade."""
    texto = (
        "1. Texto do paragrafo antes da nota1.\n"
        "1 Cf. Nota curta e completa.\n"
        "2. Segundo paragrafo, capturado mesmo sem linha em branco antes.\n"
    )
    pars = ep.segmentar(texto)
    assert [p.numero for p in pars] == [1, 2]
    p2 = next(p for p in pars if p.numero == 2)
    assert p2.texto == (
        "Segundo paragrafo, capturado mesmo sem linha em branco antes."
    )
    assert ep.relatorio_lacunas(pars) == []


def test_janela_do_marcador_isolado_nao_avanca_alem_de_duas_linhas():
    """Prova de que a janela do marcador isolado é EXATAMENTE 2 linhas,
    não "até parar de parecer nota": uma terceira linha que também
    começa por "Cf." mas não tem número próprio não é uma nova nota —
    é corpo (ou lixo) que tem de sobreviver, porque a janela já fechou."""
    texto = (
        "1. Primeiro paragrafo1.\n"
        "\n"
        "50\n"
        "Cf. Primeira nota, contida so nesta linha e na anterior.\n"
        "Cf. Isto nao e nova nota (sem numero antes) e deve sobreviver.\n"
        "\n"
        "2. Segundo paragrafo.\n"
    )
    pars = ep.segmentar(texto)
    p1 = next(p for p in pars if p.numero == 1)
    assert "Isto nao e nova nota" in p1.texto
    assert "Primeira nota, contida" not in p1.texto


def test_corpo_com_numero_no_meio_sobrevive_mesmo_sem_citacao_reconhecivel():
    """A restrição que não se negocia: o "modo nota" amplo (qualquer linha
    só-de-dígitos dispara consumo até o próximo sinal) foi TESTADO e
    REJEITADO, porque um documento real (Caso Ximenes Lopes Vs. Brasil,
    2006) usa número de página SOLTO, sem traços — sintaticamente idêntico
    ao marcador de nota — e ele pode cair no MEIO de uma lista de itens do
    próprio corpo do parágrafo (quebra de página no meio da lista), sem
    linha em branco antes da continuação. Medido: a regra ampla engoliria
    23 linhas de argumento do Estado (itens b a e de uma lista, mais o
    título da seção seguinte). A regra adotada só entra em modo-nota
    quando reconhece "Cf."/"Cfr." logo em seguida — este cenário não tem
    esse sinal, então o número de página é descartado sozinho e a lista
    sobrevive por inteiro."""
    texto = (
        "1. O Estado alegou o seguinte:\n"
        "\n"
        "61\n"
        "os responsaveis pelos maus-tratos nao foram identificados;\n"
        "b) o processo penal observou as garantias fundamentais;\n"
        "c) a investigacao nao acarretou prejuizo algum.\n"
        "\n"
        "2. A Corte pondera as alegacoes.\n"
    )
    pars = ep.segmentar(texto)
    assert [p.numero for p in pars] == [1, 2]
    p1 = next(p for p in pars if p.numero == 1)
    assert "os responsaveis pelos maus-tratos" in p1.texto
    assert "garantias fundamentais" in p1.texto
    assert "investigacao nao acarretou prejuizo algum" in p1.texto
    assert "61" not in p1.texto


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


def test_url_com_disponivel_em_fundida_no_meio_da_frase_e_removida():
    """Achado 4 (round 4): "Disponível em: <URL>" é literalmente o
    marcador que um verificador de fase posterior usa como gate duro (toda
    citação da Corte tem de carregar endereço conferível). Medido em PDF
    real: nota que não começa por "Cf."/"Cfr." (ex.: "Ver Declaração
    de...") continua vazando por inteiro, inclusive o seu próprio
    "Disponível em: <URL>" de fechamento — e esse fragmento, sozinho,
    bastaria para satisfazer o gate sem que o parágrafo tenha trazido
    citação nenhuma. A limpeza é de STRING (o artefato está fundido no
    meio da frase, sem fronteira de linha), aplicada depois da junção do
    parágrafo."""
    texto = (
        "1. O Tribunal considera que a justificação é razoável. Disponível "
        "em: https://exemplo.org/pagina.html. Portanto, admite o pedido.\n"
    )
    pars = ep.segmentar(texto)
    p1 = pars[0]
    assert "http" not in p1.texto
    assert "Disponível em" not in p1.texto
    # a frase ao redor fica legivel, sem espaço duplo onde a URL saiu
    assert p1.texto == (
        "O Tribunal considera que a justificação é razoável. "
        "Portanto, admite o pedido."
    )


def test_url_nua_sem_disponivel_em_e_removida_do_meio_da_frase():
    """Forma mais rara do Achado 4: URL NUA (sem "Disponível em:" antes)
    fundida no meio de uma frase, substituindo aparentemente o número de
    referência em sobrescrito de uma nota — medido no par. 51 de um
    documento de calibração ("...qualificaram o Brasil <URL>. como o país
    com a quinta taxa..."). Sem a URL, a frase fica gramaticalmente
    perfeita, o que é a evidência de que o artefato substituiu um
    marcador, não um trecho de prosa real."""
    texto = (
        "1. As organizações qualificaram o Brasil "
        "http://www.exemplo.org/pagina.html. como o país com a maior "
        "taxa.\n"
    )
    pars = ep.segmentar(texto)
    p1 = pars[0]
    assert "http" not in p1.texto
    assert p1.texto == (
        "As organizações qualificaram o Brasil como o país com a maior "
        "taxa."
    )


def test_disponivel_em_sem_url_sobrevive():
    """A restrição que não se negocia: se o parágrafo genuinamente disser
    "Disponível em" sem URL nenhuma depois, isso não pode ser removido —
    só entra em jogo havendo "http://"/"https://" de fato."""
    texto = (
        "1. O relatório está Disponível em: anexo próprio, sem link "
        "nenhum nesta frase.\n"
    )
    pars = ep.segmentar(texto)
    p1 = pars[0]
    assert "Disponível em" in p1.texto
