"""Cadernos de Jurisprudência da Corte IDH — a curadoria temática da própria
Corte, e a fonte do mapa temático da Fase 3.

**Por que não se monta o mapa por regex sobre o acervo.** Força relativa de
precedente não se extrai de texto: qualquer heurística produziria um mapa
inventado com aparência de curadoria. Os Cadernos são compilações que a própria
Corte publica por eixo temático — "Pueblos Indígenas y Tribales", "Derechos
Humanos de las Mujeres", "Jurisprudencia sobre Brasil" —, e o que este módulo
faz é **ler quem ela citou ali**. Cada linha semeada declara o Caderno de
origem em `tema.fonte`; nada entra sem procedência.

**O que a medição de 18/09/2026 mostrou, e que REFUTA a spec §6.3.** A spec
pedia "os leading cases do eixo com o **parágrafo-chave** de cada", supondo que
haveria um parágrafo destacado por caso. Medido no Caderno 36 (Brasil), sobre
100 citações com parágrafo: apenas **2 dos 11** documentos têm parágrafo
dominante; nos outros 9 a distribuição é plana — o C-353 é citado 23 vezes em
**20 parágrafos distintos**.

Eleger "o" parágrafo-chave ali seria arbítrio com aparência de curadoria: quem
lesse "parágrafo-chave: 278" entenderia que a Corte o destacou, e ele foi citado
duas vezes entre vinte e três. Por isso se guardam **todos** os parágrafos
citados, com a contagem de cada um — que é a curadoria real, inteira, e deixa a
ponderação com quem redige.

**Formato das citações, medido:** o Caderno cita por AUTUAÇÃO ("Série C No. 353,
par. 278"), e não por nome. Isso é uma facilidade, não um obstáculo: a autuação
é a identidade do julgado neste índice, e o nome se resolve no próprio banco —
não há casamento de nomes a fazer, nem risco de errar por grafia.

**LIMITE DECLARADO — nem todo Caderno aponta a passagem no mesmo lugar.** Em
seis dos vinte e três, a autuação aparece e o parágrafo NÃO vem dentro da
citação: o Caderno fecha a referência no número de série e abre o trecho
transcrito com o número dele próprio ("Serie C No. 118" e, adiante, "112. A
Convenção Americana é um tratado..."). Medido: o caderno 17 rende 56 menções e
**zero** parágrafos por este extrator; o 23, 34 menções e zero.

Nesses casos a ASSOCIAÇÃO ao eixo funciona — que é o principal, e é o que o
mapa temático entrega — e a lista de parágrafos sai **vazia**. Isso é
deliberado: emparelhar a citação com o número que a segue seria heurística, e
nesses mesmos textos aparecem marcas de nota coladas ao número ("Serie C No.
3301" é o 330 com a nota 1), de modo que o palpite erraria e mandaria quem
redige ao parágrafo errado. Lista vazia diz "este Caderno não deu ponteiro
parseável"; lista errada manda procurar no lugar errado, e a segunda é pior.

**Cobertura do catálogo, declarada:** a página oficial menciona 36 Cadernos e
**linka o PDF de 24**. Deles, 23 entram aqui — o 27 sai pela razão anotada no
próprio catálogo. Os demais existem e não têm link direto naquela página;
alcançá-los seria trabalho de outra rodada, e o mapa diz quais eixos tem.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

BASE = "https://www.corteidh.or.cr/sitios/libros/todos/docs/"

# Citação por autuação, com parágrafo. Tolera as grafias que os Cadernos usam
# em espanhol e português: "Serie"/"Série", "No."/"N°"/"Nº", "párr."/"par."/
# "parágrafo"/"§".
_CITACAO = re.compile(
    r"S[ée]rie?\s+([AC])\s+N[o°º.]{0,3}\s*([0-9]{1,3})\s*,?\s*"
    r"(?:p[aá]rr?a?f?o?s?\.?|§)\s*([0-9]{1,4})",
    re.I,
)

# Citação SEM parágrafo — conta como menção ao documento, não à passagem.
_MENCAO = re.compile(r"S[ée]rie?\s+([AC])\s+N[o°º.]{0,3}\s*([0-9]{1,3})", re.I)


def extrair_citacoes(texto: str) -> list[tuple[str, int, int]]:
    """(serie, numero, paragrafo) de cada citação COM parágrafo, na ordem.

    Repetições são preservadas de propósito: é a contagem que mede quanto a
    Corte voltou àquela passagem naquele eixo, e é ela que o mapa reporta.
    """
    return [(s.upper(), int(n), int(p))
            for s, n, p in _CITACAO.findall(texto or "")]


def extrair_mencoes(texto: str) -> list[tuple[str, int]]:
    """(serie, numero) de toda citação, COM ou SEM parágrafo.

    Serve ao caso em que o Caderno trata o julgado e não aponta passagem: o
    documento pertence ao eixo mesmo assim, e omiti-lo faria o mapa dizer que a
    Corte não o associou ao tema — asserção falsa por silêncio.
    """
    return [(s.upper(), int(n)) for s, n in _MENCAO.findall(texto or "")]


def consolidar(texto: str) -> dict[tuple[str, int], dict]:
    """{(serie, numero): {'paragrafos': Counter, 'mencoes': int}}.

    `mencoes` conta TODA aparição da autuação; `paragrafos` só as que apontam
    passagem. Os dois números são distintos e ambos vão para o índice, porque
    dizem coisas diferentes: um mede presença no eixo, o outro mede onde.
    """
    saida: dict[tuple[str, int], dict] = {}
    for s, n in extrair_mencoes(texto):
        saida.setdefault((s, n), {"paragrafos": collections.Counter(),
                                  "mencoes": 0})["mencoes"] += 1
    for s, n, p in extrair_citacoes(texto):
        saida.setdefault((s, n), {"paragrafos": collections.Counter(),
                                  "mencoes": 0})["paragrafos"][p] += 1
    return saida


# ---------------------------------------------------------------------------
# O CATÁLOGO DOS CADERNOS
#
# Número, arquivo e título vêm MEDIDOS da página oficial de publicações
# (`publicaciones.cfm`, colhida em 18/09/2026). O rótulo em português é
# CURADORIA deste projeto — a Corte publica os títulos em espanhol —, e está
# marcado como tal: o `fonte` que vai ao índice carrega o título ORIGINAL, não
# a minha tradução.
#
# A spec §6.3 listava outros temas ("saúde", "trabalho escravo", "meio
# ambiente", "DESCA/previdência"). Aquela lista era PROJEÇÃO minha, feita antes
# de olhar a fonte, e a fonte não a confirma: não há Caderno de saúde nem de
# trabalho escravo. O que existe é o que está aqui, e o mapa temático passa a
# ter os eixos que a Corte de fato compilou.
# ---------------------------------------------------------------------------

# Número, título e rótulo são CURADORIA declarada; o ARQUIVO não.
#
# A primeira versão deste catálogo carregava o nome do arquivo escrito por
# PADRÃO (`cuadernilloN.pdf`), e a medição contra a página oficial mostrou que
# **12 dos 24 divergiam**: os reais carregam o ano (`cuadernillo4_2021.pdf`).
# Alguns funcionavam por acaso e o caderno 11 falhou com "HTTP 200 com corpo de
# 16.042 bytes" — mesma classe da flag inventada, que ou falha barulhento ou
# baixa a coisa errada em silêncio.
#
# Agora o arquivo vem de `cadernos_links.json`, colhido da página oficial de
# publicações e VERSIONADO ao lado deste módulo, preferindo a versão PORTUGUESA
# quando ela existe — que é a língua da peça. Há teste conferindo que nenhum
# arquivo do catálogo deixou de vir dali.
_TITULOS: tuple[tuple[int, str, str], ...] = (
    (4, "Derechos Humanos de las Mujeres", "mulher"),
    (5, "Niños, Niñas y Adolescentes", "criança e adolescente"),
    (7, "Control de Convencionalidad", "controle de convencionalidade"),
    (10, "Integridad Personal", "integridade pessoal"),
    (11, "Pueblos Indígenas y Tribales", "povos indígenas"),
    (13, "Protección Judicial", "proteção judicial"),
    (14, "Igualdad y no Discriminación", "igualdade e não discriminação"),
    (16, "Libertad de Pensamiento y de Expresión", "liberdade de expressão"),
    (17, "Interacción entre el Derecho Internacional de los Derechos Humanos "
         "y el Derecho Interno", "direito internacional e direito interno"),
    (18, "Casos Contenciosos sobre El Salvador", "El Salvador"),
    (19, "Derechos Humanos de las Personas LGBTI", "LGBTI"),
    (20, "Derechos Políticos", "direitos políticos"),
    (21, "Derecho a la vida", "direito à vida"),
    (23, "Corrupción y Derechos Humanos", "corrupção"),
    (24, "Jurisprudencia sobre México", "México"),
    # O caderno 27 (Panamá) FICA DE FORA, e a razão é a premissa da fase.
    #
    # A página oficial diz que ele foi "Elaborado por la Procuraduría de la
    # Administración de Panamá y la Corte IDH" — é o único dos catalogados que
    # não é curadoria da Corte, e a Fase 3 vale justamente por sê-lo. Medido
    # em 18/09/2026 sobre os 36 cadernos da página: 34 são da Corte, e só o 27
    # e o 39 têm terceiro na elaboração.
    #
    # A medição do conteúdo confirma pelo outro lado: em 321.152 caracteres o
    # caderno 27 tem ZERO citações de autuação e ZERO "Caso X Vs. Panamá"; os
    # 51 `párr.` que ele traz são remissões INTERNAS ("supra párr. 11", "infra
    # párrs. 143 a 145"), isto é, ele reproduz texto de sentença em vez de
    # compilar citações. Semeá-lo produziria um eixo "Panamá" vazio, e eixo
    # vazio no índice responde "não compilado" a quem perguntar — quando a
    # verdade é "foi compilado por outro órgão, noutro formato".
    (29, "Jurisprudencia sobre Honduras", "Honduras"),
    (30, "Personas Defensoras de Derechos Humanos",
     "pessoas defensoras de direitos humanos"),
    (31, "Medidas Provisionales Emblemáticas de la Corte IDH",
     "medidas provisórias"),
    (32, "Medidas de Reparación", "medidas de reparação"),
    (33, "Excepciones Preliminares", "exceções preliminares"),
    (34, "Jurisprudencia sobre Guatemala", "Guatemala"),
    (35, "Jurisprudencia sobre Nicaragua", "Nicarágua"),
    # O mais relevante para a DPU, e em PORTUGUÊS.
    (36, "Jurisprudencia sobre Brasil", "Brasil"),
)


def _carregar_links() -> dict:
    import json
    arq = Path(__file__).resolve().parent / "cadernos_links.json"
    try:
        return json.loads(arq.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _montar_catalogo() -> tuple[dict, ...]:
    """Catálogo com o arquivo MEDIDO. Caderno sem link medido fica de FORA.

    Ficar de fora é a conduta certa: inventar o nome é o defeito que este
    desenho corrige, e um caderno ausente do catálogo se vê na contagem,
    enquanto um nome inventado só se vê quando o download falha — ou pior,
    quando não falha e traz outra coisa.
    """
    links = _carregar_links()
    saida = []
    for numero, titulo, rotulo in _TITULOS:
        info = links.get(str(numero)) or links.get(numero)
        if not info or not info.get("preferido"):
            continue
        saida.append(dict(numero=numero, arquivo=info["preferido"],
                          titulo=titulo, rotulo=rotulo,
                          arquivos_disponiveis=tuple(info.get("arquivos", ()))))
    return tuple(saida)


CADERNOS: tuple[dict, ...] = _montar_catalogo()


def url_do_caderno(caderno: dict) -> str:
    return BASE + caderno["arquivo"]


def fonte_do_caderno(caderno: dict) -> str:
    """Procedência que vai ao índice.

    Carrega o NÚMERO e o TÍTULO ORIGINAL do Caderno, não o rótulo em
    português: o rótulo é curadoria deste projeto e não deve se passar por
    designação da Corte.
    """
    return f"Cuadernillo de Jurisprudencia {caderno['numero']} — {caderno['titulo']}"
