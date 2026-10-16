"""Testes da normalização da chave do cache HTTP (auditoria 2026-07-30).

Contexto: o cache acertava 0,8% (4 hits em 402 entradas) porque a chave era a
query literal. Estes testes travam as duas metades da correção — o que DEVE
colidir (mesma busca escrita de outro jeito) e, mais importante, o que NÃO
pode colidir (buscas de significado diferente, e tamanhos diferentes).

Rodar: python -m pytest mcp/shared/test_cache_http_chave.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.cache_http import _hash_chave, normalizar_texto_busca  # noqa: E402


def _k(query, **extra):
    base = {"mcp": "stj-jurisprudencia", "query": query, "base": "ACOR", "tamanho": 10}
    base.update(extra)
    return _hash_chave(base)


# --- deve colidir ----------------------------------------------------------

def test_ordem_dos_termos_nao_cria_entrada_nova():
    assert _k("CPAP apneia sono") == _k("apneia sono CPAP")


def test_acento_e_caixa_nao_criam_entrada_nova():
    assert _k("PRESCRIÇÃO execução fiscal") == _k("prescricao Execucao Fiscal")


def test_pontuacao_e_espaco_extra_nao_criam_entrada_nova():
    assert _k("prescrição, execução fiscal") == _k("prescrição   execução  fiscal")


def test_termo_repetido_nao_cria_entrada_nova():
    assert _k("consignado consignado fraude") == _k("consignado fraude")


def test_normalizacao_alcanca_campo_aninhado_do_bnp():
    # O BNP encaixa a busca em filtro.buscaGeral — a normalização é recursiva.
    a = _hash_chave({"mcp": "bnp-api", "filtro": {"buscaGeral": "CPAP apneia sono"}})
    b = _hash_chave({"mcp": "bnp-api", "filtro": {"buscaGeral": "sono CPAP apneia"}})
    assert a == b


# --- NÃO pode colidir ------------------------------------------------------

def test_negacao_preserva_a_ordem():
    # "a nao b" e "b nao a" são buscas diferentes: com negação não se ordena.
    assert _k("aposentadoria nao militar") != _k("militar nao aposentadoria")


def test_adjacencia_preserva_a_ordem():
    assert _k("assistencia adj5 social") != _k("social adj5 assistencia")


def test_frase_exata_preserva_a_ordem():
    assert _k('"prestação continuada" beneficio') != _k('beneficio "continuada prestação"')


def test_operadores_diferentes_nao_colidem():
    # "e" e "ou" permanecem como tokens, então a distinção sobrevive à ordenação.
    assert _k("BPC e assistencia") != _k("BPC ou assistencia")


def test_caixa_do_operador_nao_colide():
    # Retro de 16/10/2026: no CJF (e no SJUR) operador em minúsculo é TERMO
    # literal — `medicamento e uniao` encolhe ou zera, `medicamento E uniao` não.
    # Colidindo, o zero da busca mal escrita voltava para a corrigida.
    assert _k("medicamento E uniao") != _k("medicamento e uniao")
    assert _k("contas NAO partido") != _k("contas nao partido")
    assert _k("x AND y") != _k("x and y")


def test_ordem_entre_e_e_ou_preserva_a_precedencia():
    # `a E b OU c` e `a OU b E c` agrupam diferente; ordenar os tokens as igualava.
    assert _k("a E b OU c") != _k("a OU b E c")
    assert _k("a e b ou c") != _k("a ou b e c")
    assert _k("x AND y OR z") != _k("x OR y AND z")


def test_caixa_do_termo_comum_continua_livre_com_operador():
    # Só a caixa do OPERADOR se preserva; a do termo segue normalizada.
    assert _k("Medicamento E União") == _k("medicamento E uniao")


def test_tamanho_continua_na_chave():
    # Servir 3 resultados a quem pediu 10 seria regressão silenciosa.
    assert _k("prescrição", tamanho=3) != _k("prescrição", tamanho=10)


def test_base_continua_na_chave():
    assert _k("consignado", base="ACOR") != _k("consignado", base="SUMU")


# --- a normalização não deve corromper a expressão -------------------------

def test_truncamento_e_prefixo_sobrevivem():
    # `$` (BRS) e `*`/`+` (BNP) fazem parte da expressão, não são pontuação.
    assert "assist$" in normalizar_texto_busca("assist$ social")
    assert "+apneia" in normalizar_texto_busca("+apneia +sono")


def test_texto_vazio_passa_intacto():
    assert normalizar_texto_busca("") == ""
    assert normalizar_texto_busca("   ") == "   "
