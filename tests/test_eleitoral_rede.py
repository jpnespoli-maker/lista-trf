"""Ao vivo contra o SJUR. Fora da corrida padrão: rodar com -m rede, numa
máquina com sessão gráfica (a busca abre o Edge)."""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "mcp" / "eleitoral-jurisprudencia"
for p in (MODULO, MODULO.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

_spec = importlib.util.spec_from_file_location("eleitoral_server_rede", MODULO / "server.py")
S = importlib.util.module_from_spec(_spec)
sys.modules["eleitoral_server_rede"] = S  # o @dataclass do servidor exige o registro
_spec.loader.exec_module(S)

pytestmark = pytest.mark.rede


@pytest.fixture(autouse=True)
def _sem_cache(monkeypatch):
    monkeypatch.setattr(S, "cached_http", lambda mcp, key: None)
    monkeypatch.setattr(S, "registrar_dispositivo", lambda *a, **k: None)
    monkeypatch.setattr(S, "log_query", lambda **kw: None)  # não sujar a telemetria


def test_busca_tse_ao_vivo():
    out = asyncio.run(S.buscar_jurisprudencia_eleitoral('"abuso de poder econômico" E AIJE',
                                                        max_resultados=5))
    assert not out.startswith("<erro>"), out[:300]
    assert re.search(r"<numero>\d{7}-\d{2}\.\d{4}\.6\.\d{2}\.\d{4}</numero>", out)


def test_busca_tre_rj_ao_vivo():
    out = asyncio.run(S.buscar_jurisprudencia_eleitoral("propaganda E irregular",
                                                        tribunal="TRE-RJ", max_resultados=5))
    assert not out.startswith("<erro>"), out[:300]
    assert set(re.findall(r"<fonte>([^<]+)</fonte>", out)) == {"TRE-RJ"}


def test_inteiro_teor_ao_vivo():
    out = asyncio.run(S.buscar_jurisprudencia_eleitoral("inelegibilidade", max_resultados=5))
    codigos = re.findall(r"<codigo_decisao>(\d+)</codigo_decisao>\s*<tem_inteiro_teor>sim", out)
    assert codigos, "nenhuma decisão com inteiro teor na amostra"
    texto = asyncio.run(S.inteiro_teor_eleitoral(codigos[0], max_caracteres=3000))
    assert not texto.startswith("<erro>"), texto[:300]
    assert len(texto) > 500


def test_duas_buscas_simultaneas_ao_vivo():
    # M9 — em paralelo, cada busca na sua aba: nenhuma recebe o ajuste da outra.
    async def ambas():
        return await asyncio.gather(
            S.buscar_jurisprudencia_eleitoral("condutas E vedadas", max_resultados=5),
            S.buscar_jurisprudencia_eleitoral("condutas E vedadas", tribunal="TRE-RJ",
                                              max_resultados=5),
        )

    tse, tre = asyncio.run(ambas())
    assert not tse.startswith("<erro>"), tse[:300]
    assert not tre.startswith("<erro>"), tre[:300]
    assert set(re.findall(r"<fonte>([^<]+)</fonte>", tse)) == {"TSE"}
    assert set(re.findall(r"<fonte>([^<]+)</fonte>", tre)) == {"TRE-RJ"}
