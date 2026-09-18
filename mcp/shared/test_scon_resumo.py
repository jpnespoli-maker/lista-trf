"""Travas do parser da lista do SCON na visualizacao RESUMO (rota CDP do STJ).

A fixture `fixtures/scon_resumo_acor.html` foi capturada do portal ao vivo em
24/08/2026, pela propria rota CDP, na consulta que confirmou o AgInt no AREsp
1.859.057/SP. Guardar a fixture e' o que permite mexer no parser sem reabrir o
navegador — e e' o que denuncia, no dia em que o portal mudar, QUAL campo parou
de sair.
"""

import importlib.util
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[1]
_FIXTURE = Path(__file__).parent / "fixtures" / "scon_resumo_acor.html"


def _carregar_servidor():
    """Importa o server.py do STJ (nome com hifen impede import normal)."""
    import sys

    if str(_RAIZ) not in sys.path:
        sys.path.insert(0, str(_RAIZ))
    spec = importlib.util.spec_from_file_location(
        "stj_server", _RAIZ / "stj-jurisprudencia" / "server.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def srv():
    return _carregar_servidor()


@pytest.fixture(scope="module")
def html():
    return _FIXTURE.read_text(encoding="utf-8")


class TestParseSconResumo:
    def test_total_da_numdocs(self, srv, html):
        _res, total = srv._parse_scon_resumo(html, "ACOR", 4000)
        assert total == 2

    def test_extrai_os_dois_itens(self, srv, html):
        res, _t = srv._parse_scon_resumo(html, "ACOR", 4000)
        assert len(res) == 2

    def test_numero_nao_vem_oco(self, srv, html):
        """O modo de falha de 24/08/2026: itens extraidos, todos sem numero."""
        res, _t = srv._parse_scon_resumo(html, "ACOR", 4000)
        assert all(r.numero for r in res)

    def test_numero_do_agint_alvo(self, srv, html):
        res, _t = srv._parse_scon_resumo(html, "ACOR", 4000)
        numeros = " | ".join(r.numero for r in res)
        assert "1859057" in numeros

    def test_relator_sem_matricula(self, srv, html):
        res, _t = srv._parse_scon_resumo(html, "ACOR", 4000)
        relatores = [r.relator for r in res]
        assert any("RAUL ARA" in r.upper() for r in relatores)
        assert all("(" not in (r or "") for r in relatores), (
            "a matricula entre parenteses deve ser removida do nome do relator"
        )

    def test_datas(self, srv, html):
        res, _t = srv._parse_scon_resumo(html, "ACOR", 4000)
        alvo = next(r for r in res if "1859057" in r.numero)
        assert "09/12/2024" in alvo.data, "data de publicacao (DJEN)"
        assert alvo.extra.get("julgamento") == "02/12/2024"

    def test_tipo_do_documento(self, srv, html):
        res, _t = srv._parse_scon_resumo(html, "ACOR", 4000)
        assert all("ACÓRDÃO" in (r.tipo or "").upper() for r in res)

    def test_ementa_presente_e_marcada_como_truncada(self, srv, html):
        """A visualizacao RESUMO corta a ementa. O parser NAO pode deixar isso
        implicito: quem citar a ementa daqui viola a regra de ementa integral."""
        res, _t = srv._parse_scon_resumo(html, "ACOR", 4000)
        alvo = next(r for r in res if "1859057" in r.numero)
        assert "PRESUNÇÃO DE VERACIDADE" in alvo.conteudo.upper()
        assert alvo.extra.get("ementa_truncada"), (
            "ementa cortada tem de vir sinalizada em extra['ementa_truncada']"
        )

    def test_html_vazio_nao_quebra(self, srv):
        res, total = srv._parse_scon_resumo("<html><body></body></html>", "ACOR", 400)
        assert res == [] and total == 0
