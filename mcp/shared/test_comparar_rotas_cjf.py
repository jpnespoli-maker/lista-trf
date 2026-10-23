"""Relatório que compara as duas rotas do CJF a partir do log de buscas."""

import json
import sys
from pathlib import Path

import pytest

MCP = Path(__file__).resolve().parents[1]
if str(MCP) not in sys.path:
    sys.path.insert(0, str(MCP))

from shared import comparar_rotas_cjf  # noqa: E402
from shared.comparar_rotas_cjf import principal, resumir  # noqa: E402


def _rota(ok=True, ms=1000, n=5, com_cnj=0, totais=None, erro=None, **extra):
    r = {"ok": ok, "erro": erro, "ms": ms, "totais": totais if totais is not None else {"TRF4": n},
         "n": n, "com_cnj": com_cnj, "com_data": n, "com_orgao": n}
    r.update(extra)
    return r


def _linha(antiga, api, relaxada=False, sobreposicao=0, ts="2026-10-23T17:00:00+00:00"):
    return {"ts": ts, "busca": "x", "tribunais": ["TRF4"], "relaxada": relaxada,
            "antiga": antiga, "api": api, "sobreposicao": sobreposicao}


def _quatro():
    return [
        _linha(_rota(ms=1000, n=5), _rota(ms=500, n=10, com_cnj=9, sem_cobertura=[]), sobreposicao=4),
        _linha(_rota(ok=False, ms=9000, n=0, totais={}, erro="timeout"),
               _rota(ms=700, n=10, com_cnj=10, sem_cobertura=[])),
        _linha(_rota(ms=3000, n=0, totais={}), _rota(ms=900, n=8, com_cnj=8, sem_cobertura=[])),
        _linha(_rota(n=3), _rota(n=3, com_cnj=3, sem_cobertura=[]), relaxada=True, sobreposicao=1),
    ]


def test_resumir():
    r = resumir(_quatro())
    assert r["relaxadas"] == 1
    assert r["buscas"] == 3
    assert r["antiga"]["chamadas"] == 3
    assert r["antiga"]["disponibilidade"] == pytest.approx(2 / 3)
    assert r["api"]["disponibilidade"] == 1.0
    assert r["so_api_achou"] == 1
    assert r["so_antiga_achou"] == 0
    assert r["sobreposicao_media"] == pytest.approx(4 / 3)


def test_latencia_so_de_chamadas_ok():
    r = resumir(_quatro())
    # a chamada que falhou (9000 ms) fica fora: medianas de 1000 e 3000
    assert r["antiga"]["ms_mediana"] == 2000
    assert r["antiga"]["ms_p90"] == 3000


def test_taxa_cnj_e_indisponivel_sem_documento():
    r = resumir(_quatro())
    assert r["api"]["taxa_cnj"] == pytest.approx(27 / 28)
    assert r["antiga"]["taxa_cnj"] == 0.0
    vazio = resumir([_linha(_rota(n=0, totais={}), _rota(n=0, totais={}, sem_cobertura=[]))])
    assert vazio["antiga"]["taxa_cnj"] is None


def test_soma_totais_por_tribunal():
    linhas = [
        _linha(_rota(totais={"TRF4": 5}), _rota(totais={"TRF4": 13, "TRF1": 2}, sem_cobertura=[])),
        _linha(_rota(totais={"TRF4": 1}), _rota(totais={"TRF4": 1}, sem_cobertura=[])),
    ]
    r = resumir(linhas)
    assert r["antiga"]["soma_totais_por_tribunal"] == {"TRF4": 6}
    assert r["api"]["soma_totais_por_tribunal"] == {"TRF4": 14, "TRF1": 2}


def test_api_nao_chamada_so_stj_conta_como_disponivel():
    stj = _linha(_rota(n=4), _rota(n=0, totais={}, sem_cobertura=["STJ"]))
    r = resumir([stj])
    assert r["api"]["disponibilidade"] == 1.0
    assert r["api"]["chamadas"] == 1


def test_log_vazio(tmp_path, capsys):
    log = tmp_path / "nao_existe.jsonl"
    assert principal(["--log", str(log)]) == 0
    assert "nenhuma busca registrada" in capsys.readouterr().out
    log.write_text("", encoding="utf-8")
    assert principal(["--log", str(log)]) == 0
    assert "nenhuma busca registrada" in capsys.readouterr().out


def test_linha_malformada_ignorada(tmp_path, capsys):
    log = tmp_path / "rotas.jsonl"
    boas = [json.dumps(linha) for linha in _quatro()[:2]]
    log.write_text("\n".join([boas[0], "{isto nao e json", '{"ts": "x"}', boas[1]]) + "\n",
                   encoding="utf-8")
    assert principal(["--log", str(log)]) == 0
    saida = capsys.readouterr().out
    assert "linhas invalidas: 2" in saida
    assert "chamadas: 2" in saida


def test_desde_filtra_pela_data(tmp_path, capsys):
    log = tmp_path / "rotas.jsonl"
    velha = _linha(_rota(), _rota(sem_cobertura=[]), ts="2026-10-01T10:00:00+00:00")
    nova = _linha(_rota(), _rota(sem_cobertura=[]), ts="2026-10-23T10:00:00+00:00")
    log.write_text(json.dumps(velha) + "\n" + json.dumps(nova) + "\n", encoding="utf-8")
    assert principal(["--log", str(log), "--desde", "2026-10-10"]) == 0
    assert "buscas: 1" in capsys.readouterr().out


def _normais_e_stj():
    return [
        _linha(_rota(ms=1000, n=5), _rota(ms=500, n=4, com_cnj=4, sem_cobertura=[])),
        _linha(_rota(ms=1000, n=5), _rota(ms=700, n=4, com_cnj=4, sem_cobertura=[])),
    ]


def test_api_nao_chamada_fora_da_latencia_e_da_cobertura():
    stj = _linha(_rota(n=4), _rota(ms=0, n=0, totais={}, sem_cobertura=["STJ"]))
    base = resumir(_normais_e_stj())
    r = resumir(_normais_e_stj() + [stj])
    assert r["api"]["ms_mediana"] == base["api"]["ms_mediana"] == 600
    assert r["api"]["ms_p90"] == base["api"]["ms_p90"]
    assert r["so_antiga_achou"] == 0
    assert r["api"]["chamadas"] == 3
    assert r["api"]["disponibilidade"] == 1.0


def test_api_vazia_de_verdade_conta_em_so_antiga_achou():
    vazia = _linha(_rota(n=4), _rota(n=0, totais={}, sem_cobertura=[]))
    assert resumir([vazia])["so_antiga_achou"] == 1


def test_api_que_falhou_nao_conta_em_so_antiga_achou():
    falha = _linha(_rota(n=4), _rota(ok=False, n=0, totais={}, erro="timeout", sem_cobertura=[]))
    r = resumir([falha])
    assert r["so_antiga_achou"] == 0
    assert r["api"]["disponibilidade"] == 0.0


@pytest.mark.parametrize("data", ["abc", "23/10/2026", "2026-13-40"])
def test_desde_invalido_e_erro_de_argparse(data, capsys, tmp_path):
    with pytest.raises(SystemExit) as e:
        principal(["--log", str(tmp_path / "x.jsonl"), "--desde", data])
    assert e.value.code == 2
    assert "data invalida" in capsys.readouterr().err


@pytest.mark.parametrize("campo,valor", [("n", None), ("totais", []), ("ms", "1"), ("ok", 1)])
def test_tipo_errado_vai_para_linhas_invalidas(campo, valor, tmp_path, capsys):
    ruim = _linha(_rota(), _rota(sem_cobertura=[]))
    ruim["antiga"][campo] = valor
    boa = _linha(_rota(), _rota(sem_cobertura=[]))
    log = tmp_path / "rotas.jsonl"
    log.write_text(json.dumps(ruim) + "\n" + json.dumps(boa) + "\n", encoding="utf-8")
    assert principal(["--log", str(log)]) == 0
    saida = capsys.readouterr().out
    assert "linhas invalidas: 1" in saida
    assert "buscas: 1" in saida


def test_utf8_invalido_nao_derruba(tmp_path, capsys):
    log = tmp_path / "rotas.jsonl"
    log.write_bytes(b"\xff\xfe lixo\n" + json.dumps(_quatro()[0]).encode() + b"\n")
    assert principal(["--log", str(log)]) == 0
    assert "linhas invalidas: 1" in capsys.readouterr().out


def test_log_default_e_o_do_modulo_das_rotas():
    from shared import cjf_duas_rotas
    assert comparar_rotas_cjf.LOG_PADRAO == cjf_duas_rotas.LOG_ROTAS
