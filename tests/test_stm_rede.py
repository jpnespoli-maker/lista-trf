"""Ao vivo contra a jurisprudência do STM. Fora da corrida padrão: rodar com
-m rede. Vermelho aqui é primeiro hipótese de portal fora ou de layout mudado
(ler o <erro>), não de regressão."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "stm-jurisprudencia"
for p in (MODULO, MODULO.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

_spec = importlib.util.spec_from_file_location("stm_server_rede", MODULO / "server.py")
S = importlib.util.module_from_spec(_spec)
sys.modules["stm_server_rede"] = S
_spec.loader.exec_module(S)

pytestmark = pytest.mark.rede


@pytest.fixture(autouse=True)
def _sem_cache(monkeypatch):
    monkeypatch.setattr(S, "cached_http", lambda mcp, key: None)
    monkeypatch.setattr(S, "registrar_dispositivo", lambda *a, **k: None)
    monkeypatch.setattr(S, "log_query", lambda **kw: None)


def test_busca_e_inteiro_teor_ao_vivo():
    out = S.buscar_jurisprudencia_stm('deserção e "estado de necessidade"', max_resultados=3)
    assert not out.startswith("<erro>"), out[:300]
    assert re.search(r"<numero>\d{7}-\d{2}\.\d{4}\.7\.\d{2}\.\d{4}</numero>", out)
    uuid = re.search(r"<id>([0-9a-f]{32})</id>", out).group(1)
    it = S.inteiro_teor_stm(uuid, max_caracteres=3000)
    assert not it.startswith("<erro>"), it[:300]
    assert "STM" in it


def test_zero_ao_vivo_sai_com_aviso():
    out = S.buscar_jurisprudencia_stm("xqzwvkjhttqq")
    assert "ZERO RESULTADOS" in out, out[:300]


def test_filtros_ao_vivo():
    out = S.buscar_jurisprudencia_stm("deserção", relator="CARLOS VUYK DE AQUINO",
                                      data_inicio="01/01/2026", max_resultados=5)
    assert not out.startswith("<erro>"), out[:300]
    relatores = set(re.findall(r"<relator>([^<]+)</relator>", out))
    assert relatores == {"CARLOS VUYK DE AQUINO"}
    anos = {d[-4:] for d in re.findall(r"<data>([^<]+)</data>", out)}
    assert anos and all(a >= "2026" for a in anos)


def test_cada_exemplo_da_ajuda_traz_resultado():
    ajuda = S.ajuda_sintaxe_stm()
    exemplos = [ln.strip() for ln in ajuda.split("EXEMPLOS:", 1)[1].splitlines() if ln.strip()]
    assert len(exemplos) >= 4
    for ex in exemplos:
        out = S.buscar_jurisprudencia_stm(ex, max_resultados=1)
        total = re.search(r"total na base: (\d+)", out)
        assert total and int(total.group(1)) > 0, f"exemplo da ajuda sem resultado: {ex!r}"
