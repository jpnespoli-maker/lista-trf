"""Testes da degradação de query nos portais remotos (auditoria 2026-07-30).

Rodar: python -m pytest mcp/shared/test_relaxamento.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.relaxamento import relaxar_bnp, relaxar_cjf  # noqa: E402


# --- CJF -------------------------------------------------------------------

def test_cjf_troca_juncao_por_ou():
    assert relaxar_cjf("aparelho E prescrição E domiciliar") == "aparelho OU prescrição OU domiciliar"


def test_cjf_relaxa_termos_justapostos_sem_operador():
    # 473 das 1.496 chamadas do log não usam operador explícito.
    assert relaxar_cjf("Síndrome apneia obstrutiva sono") == "Síndrome OU apneia OU obstrutiva OU sono"


def test_cjf_preserva_frase_exata_como_um_operando():
    r = relaxar_cjf('"contribuinte falecido" E execução')
    assert r == '"contribuinte falecido" OU execução'


def test_cjf_preserva_grupo_entre_parenteses():
    r = relaxar_cjf("(medicamento OU fármaco) E fornecimento")
    assert r == "(medicamento OU fármaco) OU fornecimento"


def test_cjf_mantem_posicional_colado_ao_operando():
    # "assistencia ADJ5 social" é UM operando — relaxar não pode parti-lo.
    r = relaxar_cjf("assistencia ADJ5 social E beneficio")
    assert r == "assistencia ADJ5 social OU beneficio"


def test_cjf_nao_relaxa_query_com_negacao():
    # Sob OU, o NAO passaria a PEDIR o que se queria excluir.
    assert relaxar_cjf("laudo E pericial NAO trabalhista") is None


def test_cjf_nao_relaxa_operando_unico():
    assert relaxar_cjf("NATJUS") is None
    assert relaxar_cjf('"CONTRIBUINTE FALECIDO ANTES DA PROPOSITURA"') is None


def test_cjf_nao_relaxa_query_ja_disjuntiva():
    assert relaxar_cjf("medicamento OU fármaco") is None


def test_cjf_campo_qualificado_sobrevive():
    assert relaxar_cjf("prescrição[EMEN] E tributário") == "prescrição[EMEN] OU tributário"


# --- BNP -------------------------------------------------------------------

def test_bnp_remove_obrigatoriedade():
    assert relaxar_bnp("+pensão +militar +rateio") == "pensão militar rateio"


def test_bnp_preserva_frase_exata():
    assert relaxar_bnp('+pensão +"trato sucessivo"') == 'pensão "trato sucessivo"'


def test_bnp_preserva_exclusao():
    # `-trabalhista` continua excluindo: relaxar não é aceitar o que se excluiu.
    assert relaxar_bnp("+laudo +pericial -trabalhista") == "laudo pericial -trabalhista"


def test_bnp_sem_obrigatoriedade_nao_relaxa():
    assert relaxar_bnp("pensão militar") is None


def test_bnp_termo_unico_nao_relaxa():
    assert relaxar_bnp("+BiPAP") is None


# --- guardas gerais --------------------------------------------------------

def test_vazio_nao_quebra():
    assert relaxar_cjf("") is None
    assert relaxar_cjf("   ") is None
    assert relaxar_bnp("") is None
