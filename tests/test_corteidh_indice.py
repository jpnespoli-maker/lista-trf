"""Índice local da Corte IDH — schema, escrita e busca FTS5.

O servidor MCP não vai à rede: ele lê este índice. Portanto o contrato aqui é o
contrato da ferramenta de busca, e o teste que mais importa é o da CASCATA DE
IDIOMA — documento sem versão portuguesa tem de devolver o parágrafo em
espanhol com `exige_traducao` verdadeiro, nunca devolver nada.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "corteidh-jurisprudencia"
if str(MODULO) not in sys.path:
    sys.path.insert(0, str(MODULO))

import indice  # noqa: E402
from extrator_paragrafos import Paragrafo  # noqa: E402


@pytest.fixture()
def con():
    c = indice.abrir(":memory:")
    indice.criar_schema(c)
    yield c
    c.close()


def _ximenes(con):
    doc_id = indice.inserir_documento(
        con, serie="C", numero=149, tipo="CC",
        caso="Ximenes Lopes Vs. Brasil", estado="Brasil", data="2006-07-04",
        etapa="Mérito, Reparações e Custas",
        url_por="https://www.corteidh.or.cr/docs/casos/articulos/seriec_149_por.pdf",
        url_esp="https://www.corteidh.or.cr/docs/casos/articulos/seriec_149_esp.pdf",
    )
    indice.inserir_paragrafos(con, doc_id, "por", [
        Paragrafo(88, "O Estado tem o dever de regular e fiscalizar."),
        Paragrafo(89, "As pessoas com deficiência mental em instituição de "
                      "saúde estão em situação de especial vulnerabilidade."),
    ])
    return doc_id


def _poblete(con):
    doc_id = indice.inserir_documento(
        con, serie="C", numero=349, tipo="CC",
        caso="Poblete Vilches e outros Vs. Chile", estado="Chile",
        data="2018-03-08",
        url_esp="https://www.corteidh.or.cr/docs/casos/articulos/seriec_349_esp.pdf",
    )
    indice.inserir_paragrafos(con, doc_id, "esp", [
        Paragrafo(118, "La salud es un derecho humano fundamental e "
                       "indispensable para el ejercicio de los demás."),
    ])
    return doc_id


def test_schema_cria_as_sete_tabelas(con):
    nomes = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    )}
    for t in ("documento", "paragrafo", "artigo_cadh", "tema", "reparacao",
              "recepcao", "glossario"):
        assert t in nomes, t


def test_busca_acha_paragrafo_em_portugues(con):
    _ximenes(con)
    res = indice.buscar(con, consulta="vulnerabilidade")
    assert len(res) == 1
    assert res[0]["paragrafo"] == 89
    assert res[0]["idioma"] == "por"
    assert res[0]["exige_traducao"] is False
    assert res[0]["caso"] == "Ximenes Lopes Vs. Brasil"


def test_documento_sem_portugues_devolve_espanhol_exigindo_traducao(con):
    _poblete(con)
    res = indice.buscar(con, consulta="salud")
    assert len(res) == 1
    assert res[0]["idioma"] == "esp"
    assert res[0]["exige_traducao"] is True


def test_filtro_por_estado(con):
    _ximenes(con)
    _poblete(con)
    assert len(indice.buscar(con, consulta="saúde OR salud", estado="Chile")) == 1
    assert indice.buscar(con, consulta="salud", estado="Brasil") == []


def test_filtro_por_faixa_de_ano(con):
    _ximenes(con)
    _poblete(con)
    res = indice.buscar(con, consulta="salud OR vulnerabilidade",
                        ano_de=2010, ano_ate=2020)
    assert [r["numero"] for r in res] == [349]


def test_filtro_por_artigo_da_cadh(con):
    doc_id = _ximenes(con)
    con.execute("INSERT INTO artigo_cadh (documento_id, artigo, violado) "
                "VALUES (?, ?, 1)", (doc_id, "4"))
    con.commit()
    assert len(indice.buscar(con, consulta="vulnerabilidade", artigo_cadh="4")) == 1
    assert indice.buscar(con, consulta="vulnerabilidade", artigo_cadh="26") == []


def test_busca_sem_resultado_devolve_lista_vazia(con):
    _ximenes(con)
    assert indice.buscar(con, consulta="usucapiao") == []


def test_limite_respeitado(con):
    _ximenes(con)
    assert len(indice.buscar(con, consulta="dever OR vulnerabilidade", limite=1)) == 1


def test_ficha_traz_metadados_e_artigos(con):
    doc_id = _ximenes(con)
    con.execute("INSERT INTO artigo_cadh (documento_id, artigo, violado) "
                "VALUES (?, ?, 1)", (doc_id, "5"))
    con.execute("INSERT INTO reparacao (documento_id, ordem, texto) "
                "VALUES (?, 1, 'publicar a sentença')", (doc_id,))
    con.commit()
    f = indice.ficha(con, caso="Ximenes Lopes")
    assert f["numero"] == 149
    assert f["estado"] == "Brasil"
    assert f["artigos_violados"] == ["5"]
    assert f["reparacoes"] == ["publicar a sentença"]
    assert f["idiomas"] == ["esp", "por"]


def test_ficha_por_numero_de_serie(con):
    _ximenes(con)
    assert indice.ficha(con, caso="C-149")["caso"] == "Ximenes Lopes Vs. Brasil"


def test_ficha_de_caso_inexistente_devolve_none(con):
    assert indice.ficha(con, caso="Caso Que Nao Existe") is None


def test_insercao_de_documento_e_idempotente(con):
    a = _ximenes(con)
    b = _ximenes(con)
    assert a == b
    assert con.execute("SELECT COUNT(*) FROM documento").fetchone()[0] == 1


def test_reindexar_nao_duplica_linha_no_espelho_FTS(con):
    """A tabela `paragrafo` deduplica pela chave primária; `paragrafo_fts` é
    virtual e NÃO tem chave. Sem apagar antes de reinserir, cada reindexação
    duplicaria o trecho no índice de busca — e o `buscar`, que deduplica por
    (documento, parágrafo), ESCONDERIA o sintoma. Defeito silencioso, então
    a asserção é sobre a tabela FTS e não sobre o resultado da busca."""
    _ximenes(con)
    antes = con.execute("SELECT COUNT(*) FROM paragrafo_fts").fetchone()[0]
    _ximenes(con)
    depois = con.execute("SELECT COUNT(*) FROM paragrafo_fts").fetchone()[0]
    assert antes == depois == 2
    assert len(indice.buscar(con, consulta="vulnerabilidade")) == 1


def test_suspeito_e_falso_por_padrao(con):
    _ximenes(con)
    assert indice.buscar(con, consulta="vulnerabilidade")[0]["suspeito"] is False


def test_suspeito_marcado_chega_ao_resultado_da_busca(con):
    """O aviso de possível contaminação tem de sair na busca: quem decide se
    transcreve verbatim é o Defensor, e ele só decide se souber."""
    doc_id = indice.inserir_documento(
        con, serie="C", numero=435, tipo="CC",
        caso="Barbosa de Souza e outros Vs. Brasil", estado="Brasil",
        data="2021-09-07",
        url_por="https://www.corteidh.or.cr/docs/casos/articulos/seriec_435_por.pdf")
    indice.inserir_paragrafos(con, doc_id, "por", [
        Paragrafo(101, "Texto com palavra deslocada por ordem de leitura."),
        Paragrafo(102, "Texto limpo sem contaminação alguma."),
    ], suspeitos={101})

    r101 = indice.buscar(con, consulta="deslocada")[0]
    r102 = indice.buscar(con, consulta="limpo")[0]
    assert r101["suspeito"] is True
    assert r102["suspeito"] is False


# --- A CASCATA DE IDIOMA, exercitada de verdade ----------------------------
# Buraco de cobertura achado por MUTAÇÃO: fazer a função devolver sempre o
# idioma achado no FTS, em vez de percorrer a ordem de preferência, não matava
# teste nenhum — porque nenhum teste indexava o MESMO parágrafo em dois
# idiomas. A cascata é o mecanismo que implementa a regra de idioma da spec §7
# (preferir português; não havendo, devolver o original marcando a dívida de
# tradução), então ela não pode ser a única parte não medida.

# Marcas para distinguir os dois textos nas asserções. NÃO usar
# "vulnerabilidade"/"vulnerabilidad": a espanhola é substring da portuguesa, e
# o `in` daria verdadeiro nos dois — asserção que não discrimina.
_MARCA = {"por": "situação", "esp": "situación"}

_ENCHIMENTO = (
    " O presente trecho segue por diversas linhas adicionais com termos que "
    "nao repetem a consulta, apenas para alongar o documento indexado."
)


def _texto(idioma: str, *, curto: bool) -> str:
    base = f"Ximenes Lopes em {_MARCA[idioma]} de especial vulnerabilidade."
    return base if curto else base + _ENCHIMENTO


def _con_bilingue(favorecido: str, primeiro: str):
    """Banco novo com o MESMO parágrafo em português e espanhol.

    DUAS dimensões, porque cada uma cobre um mecanismo distinto de falso
    positivo, e a primeira versão destes testes tropeçou nas duas:

    - `favorecido`: qual idioma o FTS ranqueia PRIMEIRO. O `buscar` deduplica
      por (documento, parágrafo) e fica com a primeira linha, então a cascata
      só é exercitada quando o idioma favorecido é o NÃO pedido. Quem manda no
      ranking é o `bm25`, que favorece o documento mais CURTO — medido, não
      suposto: a 1ª versão destes testes passava sob mutação porque o texto
      português era naturalmente mais curto e vencia sempre.
    - `primeiro`: a ordem de inserção, que decide o desempate quando os scores
      empatam. A 2ª versão variou só esta, e não bastou.

    O assert no fim confere a PRÓPRIA PREMISSA do teste. Se o `bm25` mudar de
    comportamento, ele falha aqui, ruidosamente — em vez de deixar o teste
    passar sem exercitar nada, que é o defeito que estas três versões
    perseguiram.
    """
    c = indice.abrir(":memory:")
    indice.criar_schema(c)
    doc_id = indice.inserir_documento(
        c, serie="C", numero=149, tipo="CC",
        caso="Ximenes Lopes Vs. Brasil", estado="Brasil", data="2006-07-04",
        url_por="https://www.corteidh.or.cr/docs/casos/articulos/seriec_149_por.pdf",
        url_esp="https://www.corteidh.or.cr/docs/casos/articulos/seriec_149_esp.pdf")

    ordem = ("por", "esp") if primeiro == "por" else ("esp", "por")
    for idi in ordem:
        indice.inserir_paragrafos(
            c, doc_id, idi,
            [Paragrafo(89, _texto(idi, curto=(idi == favorecido)))])

    # O idioma vem de `paragrafo`, e não do FTS: desde 19/09/2026 o índice é
    # `content='paragrafo'` e guarda só o rowid e os termos.
    ranking = [r["idioma"] for r in c.execute(
        "SELECT p.idioma AS idioma FROM paragrafo_fts f"
        "  JOIN paragrafo p ON p.rowid = f.rowid"
        " WHERE f.paragrafo_fts MATCH 'Ximenes'"
        " ORDER BY bm25(paragrafo_fts)")]
    assert ranking and ranking[0] == favorecido, (
        f"premissa do teste quebrada: esperava {favorecido!r} primeiro no "
        f"ranking do FTS, veio {ranking!r}"
    )
    return c, doc_id


def test_cascata_prefere_portugues_mesmo_quando_o_espanhol_pontua_mais():
    """O espanhol vem primeiro no FTS de propósito. Só passa se a cascata
    escolher o português depois — é o caso que mata o mutante."""
    for primeiro in ("por", "esp"):
        con, _ = _con_bilingue("esp", primeiro)
        try:
            res = indice.buscar(con, consulta="Ximenes")
            assert len(res) == 1, f"dedup falhou (inserção {primeiro} primeiro)"
            assert res[0]["idioma"] == "por", (
                f"espanhol favorecido, inserção {primeiro} primeiro: veio "
                f"{res[0]['idioma']!r} em vez de português")
            assert _MARCA["por"] in res[0]["texto"]
            assert res[0]["exige_traducao"] is False
        finally:
            con.close()


def test_cascata_respeita_espanhol_pedido_mesmo_quando_o_portugues_pontua_mais():
    """Espelho do anterior: o português vem primeiro no FTS, e pedir espanhol
    tem de trazer espanhol."""
    for primeiro in ("por", "esp"):
        con, _ = _con_bilingue("por", primeiro)
        try:
            res = indice.buscar(con, consulta="Ximenes", idioma_preferido="esp")
            assert len(res) == 1
            assert res[0]["idioma"] == "esp", (
                f"português favorecido, inserção {primeiro} primeiro: veio "
                f"{res[0]['idioma']!r} em vez de espanhol")
            assert _MARCA["esp"] in res[0]["texto"]
            assert res[0]["exige_traducao"] is True
        finally:
            con.close()


def test_paragrafo_sem_portugues_aparece_em_espanhol_em_vez_de_desaparecer():
    """Este NÃO mede a ordem de preferência — mede a não-omissão, que é
    requisito distinto: a cascata é por PARÁGRAFO, e documento com tradução
    parcial não pode fazer o parágrafo sem português desaparecer da busca.

    O nome diz o que ele mede, e não a família a que pertence. Um teste desta
    série já tinha sido pego prometendo mais do que media.
    """
    con, doc_id = _con_bilingue("por", "por")
    try:
        indice.inserir_paragrafos(con, doc_id, "esp", [
            Paragrafo(120, "El derecho a la salud exige supervision estatal.")])
        r120 = indice.buscar(con, consulta="supervision")[0]
        assert r120["paragrafo"] == 120
        assert r120["idioma"] == "esp"
        assert r120["exige_traducao"] is True
    finally:
        con.close()


# --- Achados da revisão da Tarefa 3 ---------------------------------------
# Nenhum destes veio de mutação: vieram de rodar o código com entrada
# plausível, porque não havia teste nestes caminhos para mutar. É a lição do
# ciclo — mutação mede o que já se testa; não acha o que ninguém testou.

def test_virgula_na_consulta_nao_derruba_a_busca(con):
    """O FTS5 só aceita vírgula dentro de NEAR(); fora disso é erro de
    sintaxe. Este é o ÚNICO ponto de entrada de busca do servidor, e uma frase
    em português corrido tem vírgula.

    Este teste asserta APENAS o contrato: não levantar exceção. Uma versão
    anterior exigia `len(res) >= 1` com uma consulta que continha a palavra
    "proteger", ausente do fixture — e como o FTS5 combina termos com AND
    implícito, zero resultado era o comportamento CORRETO. A asserção media
    outra coisa (que a consulta casasse) e reprovava o código por um defeito
    do próprio teste.
    """
    _ximenes(con)
    res = indice.buscar(con, consulta="dever de regular, fiscalizar e proteger")
    assert isinstance(res, list)


def test_virgula_e_NEUTRALIZADA_e_nao_apenas_tolerada(con):
    """Mais forte que o teste acima: com todos os termos presentes na fonte, a
    mesma consulta com e sem vírgula tem de dar o MESMO resultado. Isso prova
    que a vírgula é removida, e não que o FTS por acaso a engoliu.

    Todas as palavras abaixo existem no parágrafo 88 do fixture, de propósito:
    "O Estado tem o dever de regular e fiscalizar."
    """
    _ximenes(con)
    com = indice.buscar(con, consulta="dever de regular, fiscalizar")
    sem = indice.buscar(con, consulta="dever de regular fiscalizar")
    assert [(r["documento_id"], r["paragrafo"]) for r in com] == \
           [(r["documento_id"], r["paragrafo"]) for r in sem]
    assert len(com) == 1, "a consulta sem a vírgula casa o parágrafo 88"
    assert com[0]["paragrafo"] == 88


@pytest.mark.parametrize("consulta", [
    # vírgula e parênteses — a 1ª leva de achados
    "saúde, vida",
    'NEAR(saude vida, 5)',
    "vulnerabilidade (especial)",
    'vigiar "e" fiscalizar',
    "regular* fiscaliza^2",
    "dever-de-vigiar",
    # PONTO e BARRA — o vocabulário normal de quem redige peça, e o que a
    # lista negra ainda deixava estourar. "Vs. Brasil" está no fixture deste
    # próprio projeto e derrubava a busca.
    "Vs. Brasil",
    "art. 5",
    "CF/88",
    "Lei n. 8.742/93",
    "Resolução 240/2025",
    "20/06/2018",
    "dever de vigiar e/ou fiscalizar",
    # operador solto, em maiúscula, nas três posições
    "AND",
    "OR saúde",
    "saúde NOT",
    "saúde AND OR vida",
    # operador com prefixo de asterisco — a checagem comparava o token CRU,
    # antes de descontar o `*`, e "AND*" caía no ramo alfanumérico saindo
    # cru: para o FTS5 isso é o operador AND com um * sem operando.
    "AND*",
    "OR*",
    "NOT*",
    "a AND* b",
    "and*",
    # chaves, colchetes e mais pontuação estrutural
    "dever[vigiar]",
    "dever{vigiar}",
    "saúde: vida; morte!",
    "art. 5º, § 1º, inciso II",
    # degenerados
    "",
    "   ",
    "---",
    "///",
    '"',
])
def test_consulta_do_mundo_real_nunca_estoura(con, consulta):
    """Contrato do único ponto de entrada de busca do servidor: **pode não
    achar nada, NÃO pode levantar exceção.**

    Duas versões anteriores eram lista negra de caracteres de operador, e
    lista negra não fecha: tapada a vírgula, o `.` e a `/` seguiram
    estourando. Agora cada termo vira frase entre aspas, e estes casos são a
    medida de que a inversão funciona — não a lista do que se proibiu.
    """
    _ximenes(con)
    assert isinstance(indice.buscar(con, consulta=consulta), list)


def test_operador_OR_continua_funcionando_como_operador(con):
    """A neutralização por aspas não pode matar a sintaxe que a ajuda do
    servidor documenta. `OR` em maiúscula segue sendo união, e é como se acha
    um caso cuja versão portuguesa não existe pelo termo espanhol."""
    _ximenes(con)
    _poblete(con)
    res = indice.buscar(con, consulta="vulnerabilidade OR salud")
    assert len(res) == 2, "o OR tem de unir os dois documentos"


def test_prefixo_com_asterisco_continua_funcionando(con):
    """`regular*` casa "regular"; e não estoura em termo com pontuação."""
    _ximenes(con)
    assert len(indice.buscar(con, consulta="regul*")) >= 1
    assert isinstance(indice.buscar(con, consulta="8.742/93*"), list)


def test_operador_com_prefixo_nao_sai_cru():
    """A checagem de operador comparava o token CRU (`bruto in _OPERADORES`),
    antes de descontar o `*`. Por isso `AND*` não casava `AND`, caía no
    ramo de prefixo por ser alfanumérico e saía cru — e `AND*` é, para o
    FTS5, o operador `AND` com um `*` sem operando (`syntax error near
    "AND"`).

    Não basta não estourar: o que se quer garantir é a AUSÊNCIA do token
    cru na saída de `_para_fts`, e um resultado de busca vazio não
    distinguiria isso — poderia estar vazio por não ter estourado E por não
    ter achado nada por outro motivo qualquer. Por isso a asserção é direto
    sobre `_para_fts`, não sobre `buscar`.
    """
    assert "AND*" not in indice._para_fts("AND*").split()
    assert "OR*" not in indice._para_fts("OR*").split()
    assert "NOT*" not in indice._para_fts("NOT*").split()
    assert "AND*" not in indice._para_fts("a AND* b").split()


def test_reindexar_com_um_idioma_nao_apaga_o_endereco_do_outro(con):
    """O `UPDATE` incondicional apagava um `url_esp` já gravado quando a
    reindexação vinha só com o português. O crawler chama esta função uma vez
    por idioma baixado, então cada passada perderia o que a anterior achou —
    e o endereço é o instrumento de conferência na fonte."""
    comum = dict(serie="C", numero=149, tipo="CC",
                 caso="Ximenes Lopes Vs. Brasil", estado="Brasil",
                 data="2006-07-04")
    doc_id = indice.inserir_documento(
        con, url_esp="https://www.corteidh.or.cr/seriec_149_esp.pdf",
        sha256_esp="aaa", **comum)
    indice.inserir_documento(
        con, url_por="https://www.corteidh.or.cr/seriec_149_por.pdf",
        sha256_por="bbb", **comum)

    f = indice.ficha(con, caso="C-149")
    assert f["documento_id"] == doc_id
    assert f["url_esp"].endswith("_esp.pdf"), "o espanhol foi apagado"
    assert f["url_por"].endswith("_por.pdf")
    linha = con.execute("SELECT tem_por, sha256_esp, sha256_por FROM documento"
                        " WHERE id = ?", (doc_id,)).fetchone()
    assert linha["tem_por"] == 1, "tem_por é derivado do url_por resultante"
    assert linha["sha256_esp"] == "aaa" and linha["sha256_por"] == "bbb"


def test_documento_sem_serie_e_idempotente(con):
    """`UNIQUE` não casa NULL com NULL, então o `ON CONFLICT` não disparava
    para documento sem série — resolução de supervisão, que é o que a Fase 2
    indexa. Duplicava a cada reindexação E gravava a atualização numa linha
    órfã, com o `id` devolvido apontando para a versão velha.

    CENÁRIO CORRIGIDO em 18/09/2026, e a correção é de PREMISSA, não de
    rigor. A versão original deste teste usava duas DATAS DIFERENTES e
    afirmava que produziam UMA linha, com a segunda data sobrescrevendo. Isso
    tratava "mesmo caso, data diferente" como o mesmo documento — e é
    justamente essa suposição que perdia 545 resoluções: um caso tem várias
    resoluções de supervisão ao longo dos anos (*Vicky Hernández y otras Vs.
    Honduras* tem 3), e cada uma é documento próprio. Ver
    `test_corteidh_fase2.py::test_duas_resolucoes_do_MESMO_caso_sao_documentos_distintos`.

    O que este teste MEDIU e continua medindo é a idempotência do `NULL` na
    chave: reindexar o MESMO documento reaproveita a linha, e a atualização
    pousa na linha que o `id` aponta, nunca numa órfã. Só o cenário mudou,
    para não conflar resolução com caso.
    """
    comum = dict(serie=None, numero=None, tipo="SS",
                 caso="Gomes Lund e outros Vs. Brasil", data="2021-11-19")
    a = indice.inserir_documento(con, estado="Brasil", **comum)
    b = indice.inserir_documento(con, estado=None, **comum)

    assert a == b, "a segunda chamada tem de reaproveitar a mesma linha"
    assert con.execute("SELECT COUNT(*) FROM documento").fetchone()[0] == 1
    # A atualização foi para a linha que o id aponta, não para uma órfã — e o
    # `estado` preexistente sobrevive ao `None` da segunda chamada (COALESCE).
    linha = con.execute("SELECT data, estado FROM documento WHERE id = ?",
                        (a,)).fetchone()
    assert linha["data"] == "2021-11-19"
    assert linha["estado"] == "Brasil"


def test_reindexacao_parcial_nao_some_com_os_outros_paragrafos(con):
    """Chamada com SUBCONJUNTO de parágrafos — a Tarefa 6 reprocessando só os
    suspeitos — não pode sumir com os demais do índice de busca. As tabelas
    `paragrafo` e `paragrafo_fts` dessincronizavam em silêncio: a ficha achava
    o parágrafo, a busca não."""
    doc_id = indice.inserir_documento(
        con, serie="C", numero=333, tipo="CC",
        caso="Favela Nova Brasília Vs. Brasil", estado="Brasil",
        data="2017-02-16")
    indice.inserir_paragrafos(con, doc_id, "por", [
        Paragrafo(1, "Primeiro trecho sobre investigação policial."),
        Paragrafo(2, "Segundo trecho sobre reparação coletiva."),
        Paragrafo(3, "Terceiro trecho sobre impunidade sistêmica."),
    ])
    # reprocessa SÓ o parágrafo 3
    indice.inserir_paragrafos(con, doc_id, "por", [
        Paragrafo(3, "Terceiro trecho sobre impunidade sistêmica.")],
        suspeitos={3})

    assert len(indice.buscar(con, consulta="investigação")) == 1, \
        "o parágrafo 1 desapareceu da busca"
    assert len(indice.buscar(con, consulta="reparação")) == 1, \
        "o parágrafo 2 desapareceu da busca"
    assert indice.buscar(con, consulta="impunidade")[0]["suspeito"] is True


def test_ficha_ambigua_devolve_o_casamento_mais_proximo_e_declara_os_outros(con):
    """"Vs. Brasil" é sufixo de quase todo caso brasileiro da Corte. Resolver
    em silêncio pela data mais antiga faria a peça citar o caso errado sem
    aviso nenhum."""
    _ximenes(con)
    indice.inserir_documento(
        con, serie="C", numero=435, tipo="CC",
        caso="Barbosa de Souza e outros Vs. Brasil", estado="Brasil",
        data="2021-09-07")

    f = indice.ficha(con, caso="Vs. Brasil")
    assert f is not None
    assert len(f["outros_candidatos"]) == 1, \
        "a ambiguidade tem de ser declarada, não resolvida em silêncio"
    assert f["caso"] != f["outros_candidatos"][0]


def test_ficha_sem_ambiguidade_nao_declara_candidato(con):
    _ximenes(con)
    assert indice.ficha(con, caso="Ximenes")["outros_candidatos"] == []


def test_ficha_aceita_a_forma_Serie_C_149(con):
    """O ramo com o prefixo "Série" existia no regex e nenhum teste o exercia
    — mutação provou: removê-lo matava 0 de 19."""
    _ximenes(con)
    for forma in ("C-149", "Série C 149", "serie c 149", "C 149"):
        f = indice.ficha(con, caso=forma)
        assert f is not None, f"a forma {forma!r} não casou"
        assert f["numero"] == 149


def test_ficha_exclui_artigo_marcado_como_NAO_violado(con):
    """O filtro `violado = 1` existia sem cobertura — mutação provou:
    removê-lo matava 0 de 19."""
    doc_id = _ximenes(con)
    con.execute("INSERT INTO artigo_cadh (documento_id, artigo, violado)"
                " VALUES (?, '5', 1), (?, '21', 0)", (doc_id, doc_id))
    con.commit()
    assert indice.ficha(con, caso="Ximenes")["artigos_violados"] == ["5"]


def test_ficha_expoe_os_quatro_idiomas(con):
    doc_id = indice.inserir_documento(
        con, serie="C", numero=222, tipo="CC", caso="Y Vs. Peru",
        estado="Peru", data="2010-01-01",
        url_esp="https://www.corteidh.or.cr/x_esp.pdf",
        url_ing="https://www.corteidh.or.cr/x_ing.pdf")
    indice.inserir_paragrafos(con, doc_id, "esp", [Paragrafo(1, "texto")])
    f = indice.ficha(con, caso="Y Vs. Peru")
    for chave in ("url_por", "url_esp", "url_ing", "url_fra"):
        assert chave in f, chave
    assert f["url_ing"].endswith("_ing.pdf")


def test_reindexar_atualiza_a_marca_de_suspeito(con):
    """A Tarefa 6 pode medir melhor e desmarcar — a marca não é permanente."""
    doc_id = indice.inserir_documento(
        con, serie="C", numero=1, tipo="CC", caso="X Vs. Brasil",
        estado="Brasil", data="2020-01-01")
    indice.inserir_paragrafos(con, doc_id, "por",
                              [Paragrafo(7, "trecho qualquer")], suspeitos={7})
    assert indice.buscar(con, consulta="trecho")[0]["suspeito"] is True
    indice.inserir_paragrafos(con, doc_id, "por",
                              [Paragrafo(7, "trecho qualquer")], suspeitos=set())
    assert indice.buscar(con, consulta="trecho")[0]["suspeito"] is False


# ---------------------------------------------------------------------------
# FTS5 external-content (19/09/2026) — o índice deixou de guardar cópia do
# texto. O que estes testes protegem não é o tamanho do arquivo: é a JUNÇÃO
# por rowid, cujo erro devolveria parágrafo trocado sem levantar exceção.
# ---------------------------------------------------------------------------

_DDL_FTS_ANTIGO = (
    "CREATE VIRTUAL TABLE paragrafo_fts USING fts5("
    " texto, documento_id UNINDEXED, numero UNINDEXED, idioma UNINDEXED,"
    " tokenize='unicode61 remove_diacritics 2')"
)


def _banco_no_formato_antigo(caminho):
    """Monta em disco um banco com o FTS de conteúdo próprio, como era antes.

    Constrói pela DDL antiga em vez de usar um arquivo fixture porque o que se
    quer exercitar é a FORMA do índice, e forma se declara melhor em DDL do que
    em binário opaco.
    """
    con = indice.abrir(caminho, permitir_legado=True)
    indice.criar_schema(con)
    con.execute("DROP TABLE paragrafo_fts")
    con.execute(_DDL_FTS_ANTIGO)
    doc_id = indice.inserir_documento(
        con, serie="C", numero=149, tipo="CC",
        caso="Ximenes Lopes Vs. Brasil", estado="Brasil", data="2006-07-04")
    trechos = [
        (1, "Primeiro trecho sobre integridade pessoal."),
        (2, "Segundo trecho sobre reparacao integral."),
        (3, "Terceiro trecho sobre garantias judiciais."),
    ]
    for numero, texto in trechos:
        con.execute(
            "INSERT INTO paragrafo (documento_id, numero, idioma, texto,"
            " suspeito) VALUES (?,?,?,?,0)", (doc_id, numero, "por", texto))
        con.execute(
            "INSERT INTO paragrafo_fts (texto, documento_id, numero, idioma)"
            " VALUES (?,?,?,?)", (texto, doc_id, numero, "por"))
    con.commit()
    con.close()
    return doc_id


def test_fts_novo_nao_guarda_copia_do_texto(con):
    """A cópia respondia por 36% do acervo — 138,1 MB contra 137,5 MB de texto
    útil. O sinal de que ela sumiu é estrutural: com `content=`, o FTS5 não
    cria a shadow table `paragrafo_fts_content`."""
    tabelas = {
        r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "paragrafo_fts" in tabelas
    assert "paragrafo_fts_content" not in tabelas, (
        "o FTS voltou a guardar cópia própria do texto — confira o `content=` "
        "na DDL de `paragrafo_fts`"
    )
    assert indice.fts_e_legado(con) is False


def test_busca_devolve_o_paragrafo_certo_quando_o_rowid_nao_segue_a_ordem(con):
    """O caso que mata o mutante da junção.

    Com um só parágrafo, `p.rowid = f.rowid` acerta por acidente mesmo se a
    junção estiver errada. Aqui a ordem dos rowids é embaralhada de propósito:
    o `INSERT OR REPLACE` da reindexação apaga a linha e a reinsere no FIM,
    então o parágrafo 1 passa a ter o MAIOR rowid do documento. Junção que
    caia em outra linha devolve texto de outro parágrafo — plausível, numerado
    e errado, que é exatamente o modo de falha que a guarda de `abrir` existe
    para impedir.
    """
    doc_id = indice.inserir_documento(
        con, serie="C", numero=333, tipo="CC",
        caso="Favela Nova Brasilia Vs. Brasil", estado="Brasil",
        data="2017-02-16")
    indice.inserir_paragrafos(con, doc_id, "por", [
        Paragrafo(1, "Trecho um sobre investigacao policial."),
        Paragrafo(2, "Trecho dois sobre reparacao coletiva."),
        Paragrafo(3, "Trecho tres sobre impunidade sistemica."),
    ])
    # Reindexa SÓ o 1, que assim ganha o maior rowid da tabela.
    indice.inserir_paragrafos(
        con, doc_id, "por",
        [Paragrafo(1, "Trecho um sobre investigacao policial.")])

    rowids = {
        r["numero"]: r["rid"] for r in con.execute(
            "SELECT numero, rowid AS rid FROM paragrafo"
            " WHERE documento_id = ?", (doc_id,))
    }
    assert rowids[1] > rowids[3], (
        f"premissa do teste quebrada: esperava o parágrafo 1 com rowid maior "
        f"que o do 3 depois da reindexação, veio {rowids}"
    )

    for termo, esperado in (("investigacao", 1), ("reparacao", 2),
                            ("impunidade", 3)):
        res = indice.buscar(con, consulta=termo)
        assert len(res) == 1, f"{termo}: esperava 1 resultado, veio {len(res)}"
        assert res[0]["paragrafo"] == esperado, (
            f"{termo}: a busca devolveu o parágrafo {res[0]['paragrafo']} "
            f"quando o termo está no {esperado} — junção por rowid trocada"
        )
        assert termo in res[0]["texto"]


def test_abrir_recusa_banco_no_formato_antigo(tmp_path):
    """Banco velho com código novo devolveria parágrafo trocado em silêncio.
    Recusar é o comportamento certo, e a mensagem tem de dizer como consertar."""
    caminho = tmp_path / "antigo.db"
    _banco_no_formato_antigo(caminho)

    with pytest.raises(RuntimeError) as erro:
        indice.abrir(caminho)
    assert "--migrar-fts" in str(erro.value), (
        "a recusa precisa apontar o comando que resolve, senão ela só bloqueia"
    )

    # E não pode barrar banco novo, que é o caso comum.
    assert indice.abrir(tmp_path / "novo.db") is not None


def test_migrar_fts_externo_converte_preserva_a_busca_e_e_idempotente(tmp_path):
    """A conversão joga fora índice e cópia, ambos derivados, e reconstrói a
    partir da tabela `paragrafo`, que é a fonte. O texto não se toca."""
    caminho = tmp_path / "antigo.db"
    _banco_no_formato_antigo(caminho)

    con = indice.abrir(caminho, permitir_legado=True)
    assert indice.fts_e_legado(con) is True
    assert indice.migrar_fts_externo(con) is True
    assert indice.fts_e_legado(con) is False
    # Segunda passada não faz nada e diz que não fez.
    assert indice.migrar_fts_externo(con) is False
    con.close()

    # Depois de convertido, abre sem a permissão explícita — e busca certo.
    con = indice.abrir(caminho)
    assert con.execute("SELECT COUNT(*) FROM paragrafo").fetchone()[0] == 3
    for termo, esperado in (("integridade", 1), ("reparacao", 2),
                            ("garantias", 3)):
        res = indice.buscar(con, consulta=termo)
        assert len(res) == 1, f"{termo}: sumiu da busca após a migração"
        assert res[0]["paragrafo"] == esperado, (
            f"{termo}: veio o parágrafo {res[0]['paragrafo']}, esperava "
            f"{esperado}"
        )
    con.close()
