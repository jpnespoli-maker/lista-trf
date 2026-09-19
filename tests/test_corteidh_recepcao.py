"""Recepção interna de caso da Corte IDH no STF e no STJ (spec §6.4).

O que estes testes protegem é a distinção que dá razão de ser à ferramenta: ela
mede ONDE LER, e não O QUE FOI DECIDIDO. Duas travas centrais, e as duas são
invariantes desta família de falhas:

1. ZERO NÃO CALIBRADO NÃO É AUSÊNCIA. Canal mudo devolve `NAO_APURADO`, com
   essas palavras, e jamais `SEM_OCORRENCIA`. É a mesma doutrina do §"a irmã
   pelo outro ângulo": denominador zero não é resultado, é comando que não
   trabalhou.
2. MEDIÇÃO NÃO FEITA NÃO ENTRA EM CACHE. Guardar um `NAO_APURADO` por sete dias
   converteria uma queda momentânea do portal em resposta fixa — o erro exato
   que o TTL existe para evitar.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "corteidh-jurisprudencia"
if str(MODULO) not in sys.path:
    sys.path.insert(0, str(MODULO))

import recepcao_interna as ri  # noqa: E402


def _doc(numero: str, ementa: str = "ementa qualquer") -> dict:
    return {
        "numero": numero, "classe": "ADPF", "relator": "Min. Fulano",
        "orgao_julgador": "Tribunal Pleno", "data_julgamento": "29/04/2010",
        "ementa": ementa,
    }


class _Buscador:
    """Dublê de `cjf_client.buscar_documentos` com roteiro por termo."""

    def __init__(self, roteiro: dict, *, explode: bool = False):
        self.roteiro = roteiro
        self.explode = explode
        self.chamadas: list[tuple[str, list]] = []

    def __call__(self, termo, tribunais, maximo):
        self.chamadas.append((termo, list(tribunais)))
        if self.explode:
            raise RuntimeError("portal fora do ar")
        docs = self.roteiro.get(termo, [])
        return docs, {t: len(docs) for t in tribunais}


def _sem_cache():
    return (lambda _k: None), (lambda _k, _b: None)


# ---------------------------------------------------------------------------
# Construção da consulta
# ---------------------------------------------------------------------------

def test_nucleo_corta_no_vs_e_descarta_o_prefixo_caso():
    assert ri.nucleo_do_caso(
        "Caso Gomes Lund e outros Vs. Brasil") == "Gomes Lund e outros"


def test_parecer_consultivo_nao_tem_vs_e_o_nucleo_e_o_titulo_inteiro():
    # OC não segue a forma "Caso X Vs. Y". Cortar por uma forma presumida
    # deixaria o núcleo vazio, e consulta vazia não mede nada.
    titulo = "Condición jurídica y derechos de los migrantes indocumentados"
    assert ri.nucleo_do_caso(titulo) == titulo
    assert ri.consulta_do_caso(titulo) != ""


# Operadores do CJF escritos À MÃO, de propósito: comparar contra `_ESTOPE`
# faria a asserção se mover junto com o que ela deveria vigiar — a lista sai da
# produção e o teste passa a concordar com o defeito. Alvo de medição tem de ser
# fixo, e no caminho do que se mede.
_OPERADORES_CJF = {"e", "ou", "nao", "adj", "prox", "com", "mesmo"}


def test_operador_do_cjf_nao_vaza_como_TERMO_da_consulta():
    # "e" é a conjunção do portal. Passando como palavra do nome, a query vira
    # `Corte E Interamericana E Gomes E Lund E e` — sintaxe quebrada, que
    # devolve zero indistinguível de ausência.
    consulta = ri.consulta_do_caso("Caso Gomes Lund e outros Vs. Brasil")
    termos = [t for t in consulta.split(" E ") if t]

    assert consulta.startswith("Corte E Interamericana E ")
    assert "Gomes" in termos and "Lund" in termos
    for termo in termos:
        assert ri._normalizar(termo) not in _OPERADORES_CJF, (
            f"o termo {termo!r} é operador do CJF e quebra a consulta")


@pytest.mark.parametrize("titulo, vazado", [
    ("Caso Comunidade Garífuna ou seus membros Vs. Honduras", "ou"),
    ("Caso Alguém com outros Vs. Peru", "com"),
    ("Caso Fulano nao identificado Vs. Chile", "nao"),
])
def test_operador_de_DUAS_letras_tambem_nao_vaza(titulo, vazado):
    # A guarda de comprimento (`len > 1`) cobre "e" e "y", e por isso a lista de
    # vazias parecia redundante. Não é: "ou", "com" e "nao" têm duas letras ou
    # mais e escapavam das duas — medido em 18/09/2026, o título da Comunidade
    # Garífuna produzia `... E Garifuna E ou E seus`.
    termos = [t for t in ri.consulta_do_caso(titulo).split(" E ") if t]
    assert vazado not in [ri._normalizar(t) for t in termos]


def test_nome_so_de_ruido_devolve_consulta_vazia_e_nao_mede():
    r = ri.consultar("Caso de os", buscar=_Buscador({}), usar_cache=False)
    assert r["veredito"] == "NAO_APURADO"
    assert r["consulta"] == ""


@pytest.mark.parametrize("titulo", [
    "Caso J. Vs. Perú",
    "Caso B. Vs. El Salvador",
])
def test_vitima_anonimizada_RECUSA_a_medicao_em_vez_de_medir_mal(titulo):
    # Casos reais da Corte, cujo nome é uma inicial. Aceitá-la produziria
    # `Corte E Interamericana E J` — consulta que não distingue o caso de nada,
    # e cujo zero o canário validaria como AUSÊNCIA MEDIDA. O pior desfecho
    # possível é a resposta confiante e errada; a recusa declarada é melhor.
    b = _Buscador({ri.CANARIO: [_doc("HC 1")]})
    r = ri.consultar(titulo, buscar=b, usar_cache=False)

    assert r["consulta"] == ""
    assert r["veredito"] == "NAO_APURADO"
    assert r["veredito"] != "SEM_OCORRENCIA"
    assert b.chamadas == [], "não se gasta requisição em consulta que não mede"
    assert "anonimizada" in r["motivo"]


# ---------------------------------------------------------------------------
# A trava 1: zero só vale calibrado
# ---------------------------------------------------------------------------

def test_ha_ocorrencia_nao_gasta_canario():
    consulta = ri.consulta_do_caso("Gomes Lund Vs. Brasil")
    b = _Buscador({consulta: [_doc("ADPF 153")]})
    ler, gravar = _sem_cache()
    r = ri.consultar("Gomes Lund Vs. Brasil", buscar=b, cache_ler=ler,
                     cache_gravar=gravar)

    assert r["veredito"] == "HA_OCORRENCIA"
    # O canário custa uma requisição e só decide quando há zero. Havendo
    # documento, o canal está provado pelo próprio resultado.
    assert ri.CANARIO not in [t for t, _ in b.chamadas]


def test_zero_com_canario_vivo_e_ausencia_MEDIDA():
    b = _Buscador({ri.CANARIO: [_doc("HC 1")]})
    ler, gravar = _sem_cache()
    r = ri.consultar("Caso Inexistente Vs. Brasil", buscar=b, cache_ler=ler,
                     cache_gravar=gravar)

    assert r["veredito"] == "SEM_OCORRENCIA"
    assert ri.CANARIO in [t for t, _ in b.chamadas]


def test_zero_com_canario_MUDO_e_NAO_APURADO_nunca_ausencia():
    # O caso que dá razão de ser à ferramenta: o CJF responde HTTP 200 com zero
    # quando um shard cai. Sem o canário, isso vira "o STF nunca citou este
    # caso" — afirmação falsa com aparência de medição.
    b = _Buscador({})  # nem o caso nem o canário casam
    ler, gravar = _sem_cache()
    r = ri.consultar("Gomes Lund Vs. Brasil", buscar=b, cache_ler=ler,
                     cache_gravar=gravar)

    assert r["veredito"] == "NAO_APURADO"
    assert "NÃO foi feita" in r["motivo"]
    assert r["veredito"] != "SEM_OCORRENCIA"


def test_canario_que_levanta_excecao_tambem_e_NAO_APURADO():
    class _SoOCanarioExplode(_Buscador):
        def __call__(self, termo, tribunais, maximo):
            if termo == ri.CANARIO:
                raise RuntimeError("timeout")
            return [], {t: 0 for t in tribunais}

    ler, gravar = _sem_cache()
    r = ri.consultar("Gomes Lund Vs. Brasil", buscar=_SoOCanarioExplode({}),
                     cache_ler=ler, cache_gravar=gravar)
    assert r["veredito"] == "NAO_APURADO"


def test_erro_de_rede_nao_levanta_e_declara_que_nao_apurou():
    # Isolamento (spec §6.4): falhando a rede, a tool devolve veredito, não
    # exceção — quem a chama é o servidor MCP, e derrubá-lo levaria junto a
    # busca de parágrafo, que é offline e não tem culpa.
    ler, gravar = _sem_cache()
    r = ri.consultar("Gomes Lund Vs. Brasil",
                     buscar=_Buscador({}, explode=True),
                     cache_ler=ler, cache_gravar=gravar)
    assert r["veredito"] == "NAO_APURADO"
    assert "RuntimeError" in r["motivo"]


# ---------------------------------------------------------------------------
# A trava 2: medição não feita não entra em cache
# ---------------------------------------------------------------------------

def test_medicao_realizada_entra_em_cache():
    gravados: dict = {}
    consulta = ri.consulta_do_caso("Gomes Lund Vs. Brasil")
    ri.consultar("Gomes Lund Vs. Brasil",
                 buscar=_Buscador({consulta: [_doc("ADPF 153")]}),
                 cache_ler=lambda _k: None,
                 cache_gravar=lambda k, b: gravados.update({"k": k, "b": b}))
    assert gravados, "medição realizada tem de ser guardada"
    assert json.loads(gravados["b"])["veredito"] == "HA_OCORRENCIA"


def test_NAO_APURADO_nao_entra_em_cache():
    gravados: dict = {}
    ri.consultar("Gomes Lund Vs. Brasil", buscar=_Buscador({}),
                 cache_ler=lambda _k: None,
                 cache_gravar=lambda k, b: gravados.update({"k": k, "b": b}))
    assert not gravados, (
        "cachear canal mudo por 7 dias fixa a queda do portal como resposta")


def test_zero_medido_entra_em_cache():
    # Distingue-se do anterior: aqui houve medição, e o resultado dela é zero.
    gravados: dict = {}
    ri.consultar("Caso Inexistente Vs. Brasil",
                 buscar=_Buscador({ri.CANARIO: [_doc("HC 1")]}),
                 cache_ler=lambda _k: None,
                 cache_gravar=lambda k, b: gravados.update({"b": b}))
    assert json.loads(gravados["b"])["veredito"] == "SEM_OCORRENCIA"


def test_cache_hit_nao_vai_a_rede_e_se_declara():
    guardado = json.dumps({"caso": "X", "veredito": "HA_OCORRENCIA",
                           "documentos": [], "cache": "miss"})
    b = _Buscador({})
    r = ri.consultar("Gomes Lund Vs. Brasil", buscar=b,
                     cache_ler=lambda _k: guardado,
                     cache_gravar=lambda k, v: None)
    assert r["cache"] == "hit"
    assert b.chamadas == []


def test_cache_corrompido_e_miss_e_nao_derruba():
    consulta = ri.consulta_do_caso("Gomes Lund Vs. Brasil")
    r = ri.consultar("Gomes Lund Vs. Brasil",
                     buscar=_Buscador({consulta: [_doc("ADPF 153")]}),
                     cache_ler=lambda _k: "{isto nao e json",
                     cache_gravar=lambda k, v: None)
    assert r["veredito"] == "HA_OCORRENCIA"


# ---------------------------------------------------------------------------
# Sementes: ponteiro de curadoria, nunca tese
# ---------------------------------------------------------------------------

def test_caso_notorio_traz_ancora_e_ela_e_BUSCADA():
    # A semente não afirma o que o STF decidiu na ADPF 153 — ela a procura.
    # Âncora errada aparece como âncora sem resultado, não como afirmação falsa.
    consulta = ri.consulta_do_caso("Gomes Lund Vs. Brasil")
    b = _Buscador({consulta: [_doc("RE 1")], "ADPF E 153": [_doc("ADPF 153")]})
    ler, gravar = _sem_cache()
    r = ri.consultar("Caso Gomes Lund e outros Vs. Brasil", buscar=b,
                     cache_ler=ler, cache_gravar=gravar)

    termos = [t for t, _ in b.chamadas]
    assert "ADPF E 153" in termos and "ADPF E 320" in termos
    assert [a["termo"] for a in r["ancoras"]] == ["ADPF E 153", "ADPF E 320"]
    assert r["ancoras"][0]["documentos"], "a âncora que casou tem de trazer o doc"
    assert r["ancoras"][1]["documentos"] == [], "âncora sem resultado é vazia"
    assert r["ancoras_procedencia"] == ri.PROCEDENCIA_SEMENTES


def test_caso_sem_semente_nao_inventa_ancora():
    consulta = ri.consulta_do_caso("Caso Ximenes Lopes Vs. Brasil")
    r = ri.consultar("Caso Ximenes Lopes Vs. Brasil",
                     buscar=_Buscador({consulta: [_doc("RE 1")]}),
                     cache_ler=lambda _k: None, cache_gravar=lambda k, v: None)
    assert r["ancoras"] == []


def test_ancora_nao_e_consultada_quando_a_medicao_falhou():
    # Não faz sentido gastar requisição de âncora quando o canal está mudo — e,
    # pior, os achados dela dariam ao leitor a impressão de medição feita.
    b = _Buscador({})
    ler, gravar = _sem_cache()
    ri.consultar("Caso Gomes Lund e outros Vs. Brasil", buscar=b,
                 cache_ler=ler, cache_gravar=gravar)
    assert "ADPF E 153" not in [t for t, _ in b.chamadas]


def test_semente_casa_sem_acento_e_sem_caixa():
    assert ri.ancoras_do_caso("caso FAVELA NOVA BRASÍLIA vs. brasil")


# ---------------------------------------------------------------------------
# Atribuição e forma do resultado
# ---------------------------------------------------------------------------

def test_cada_documento_sabe_de_que_tribunal_veio():
    # Sem isto, "o STF resiste e o STJ acolhe" fica indistinguível do inverso.
    consulta = ri.consulta_do_caso("Gomes Lund Vs. Brasil")
    r = ri.consultar("Gomes Lund Vs. Brasil",
                     buscar=_Buscador({consulta: [_doc("ADPF 153")]}),
                     cache_ler=lambda _k: None, cache_gravar=lambda k, v: None)
    assert sorted(d["tribunal"] for d in r["documentos"]) == ["STF", "STJ"]


def test_ementa_longa_e_truncada_e_o_truncamento_se_DECLARA():
    consulta = ri.consulta_do_caso("Gomes Lund Vs. Brasil")
    longa = "x" * (ri._MAX_EMENTA + 500)
    r = ri.consultar("Gomes Lund Vs. Brasil",
                     buscar=_Buscador({consulta: [_doc("ADPF 153", longa)]}),
                     cache_ler=lambda _k: None, cache_gravar=lambda k, v: None)
    d = r["documentos"][0]
    assert len(d["ementa"]) == ri._MAX_EMENTA
    assert d["ementa_truncada"] is True


def test_o_motivo_de_HA_OCORRENCIA_recusa_inferir_postura():
    # A frase importa: a lista de acórdãos sem esta ressalva se lê como
    # veredito de recepção, que é a inferência que a ferramenta não faz.
    consulta = ri.consulta_do_caso("Gomes Lund Vs. Brasil")
    r = ri.consultar("Gomes Lund Vs. Brasil",
                     buscar=_Buscador({consulta: [_doc("ADPF 153")]}),
                     cache_ler=lambda _k: None, cache_gravar=lambda k, v: None)
    assert "POSTURA" in r["motivo"]
