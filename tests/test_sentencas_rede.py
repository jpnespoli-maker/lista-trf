"""Ao vivo contra o Banco de Sentenças das Justiças Militares. Fora da corrida
padrão: rodar com -m rede. Vermelho aqui é primeiro hipótese de portal fora ou
de contrato mudado (ler o <erro>), não de regressão."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "sentencas-militares"
for p in (MODULO, MODULO.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

_spec = importlib.util.spec_from_file_location("sentencas_server_rede", MODULO / "server.py")
S = importlib.util.module_from_spec(_spec)
sys.modules["sentencas_server_rede"] = S
_spec.loader.exec_module(S)

pytestmark = pytest.mark.rede


@pytest.fixture(autouse=True)
def _sem_cache(monkeypatch):
    monkeypatch.setattr(S, "cached_http", lambda mcp, key: None)
    monkeypatch.setattr(S, "registrar_dispositivo", lambda *a, **k: None)
    monkeypatch.setattr(S, "log_query", lambda **kw: None)


def _total(out):
    m = re.search(r"total: (\d+)", out)
    assert m, out[:300]
    return int(m.group(1))


def test_busca_jmu_e_texto_integral():
    out = S.buscar_sentencas_militares("deserção", orgao="1ª CJM")
    assert not out.startswith("<erro>"), out[:300]
    npus = re.findall(r"<numero>(\d{7}-\d{2}\.\d{4}\.7\.\d{2}\.\d{4})</numero>", out)
    assert npus, out[:500]
    texto = S.texto_sentenca_militar(npus[0], max_caracteres=3000)
    assert not texto.startswith("<erro>"), texto[:300]
    assert "Continuação" in texto or "Fim do texto" in texto


def test_and_implicito_estreita_como_o_and_explicito():
    # Medido: "deserção ameaça" 3.787 (OU do portal) contra 108 com AND.
    implicito = _total(S.buscar_sentencas_militares("deserção ameaça", justica="todas"))
    explicito = _total(S.buscar_sentencas_militares("deserção AND ameaça", justica="todas"))
    portugues = _total(S.buscar_sentencas_militares("deserção e ameaça", justica="todas"))
    assert implicito == explicito == portugues
    assert 0 < explicito < 1000


def test_por_pagina_20_e_cursor():
    p1 = S.buscar_sentencas_militares("deserção", justica="todas", por_pagina=20)
    assert p1.count("<resultado ") == 20
    cursor = re.search(r"cursor=(\d+)", p1).group(1)
    p2 = S.buscar_sentencas_militares("deserção", justica="todas", por_pagina=20, cursor=cursor)
    n1 = set(re.findall(r"<id_sentenca>([^<]+)</id_sentenca>", p1))
    n2 = set(re.findall(r"<id_sentenca>([^<]+)</id_sentenca>", p2))
    assert n2 and not n1 & n2


def test_zero_ao_vivo():
    assert "ZERO RESULTADOS" in S.buscar_sentencas_militares("xqzwvkjhttqq", justica="todas")


def test_cada_exemplo_da_ajuda_traz_resultado():
    ajuda = S.ajuda_sintaxe_sentencas_militares()
    exemplos = [ln.strip() for ln in ajuda.split("EXEMPLOS:", 1)[1].splitlines() if ln.strip()]
    assert len(exemplos) >= 3
    for ex in exemplos:
        assert _total(S.buscar_sentencas_militares(ex, justica="todas")) > 0, ex
