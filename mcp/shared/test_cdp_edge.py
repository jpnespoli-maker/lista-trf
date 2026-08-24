"""Travas do `shared.cdp_edge` — so' as partes PURAS (nada lanca navegador).

O que estes testes protegem, e por que:

1. `--user-data-dir` no comando. E' a armadilha que custou uma tentativa em
   24/08/2026: desde Edge/Chrome 136 a porta de depuracao e' ignorada em
   silencio no perfil padrao. Se alguem "simplificar" `montar_args` removendo a
   flag, a rota CDP para de funcionar SEM erro — devolve so' "conexao recusada".

2. `detectar_desafio` nos DOIS sentidos. Cada sentinela reconhecida vem pareada
   com a pagina LEGITIMA que nao pode ser confundida com desafio: guarda que
   dispara em pagina boa faz a rota CDP ser abandonada por engano e cair no CJF,
   que e' justamente a fonte que nao tem o julgado.
"""

from pathlib import Path

import pytest

from shared import cdp_edge


class TestMontarArgs:
    def test_user_data_dir_sempre_presente(self):
        args = cdp_edge.montar_args(9222, Path("C:/tmp/perfil"))
        assert any(a.startswith("--user-data-dir=") for a in args), (
            "sem --user-data-dir a porta CDP nao abre em Edge >= 136, e falha "
            "em silencio"
        )

    def test_porta_no_comando(self):
        args = cdp_edge.montar_args(9333, Path("C:/tmp/perfil"))
        assert "--remote-debugging-port=9333" in args

    def test_url_opcional(self):
        sem = cdp_edge.montar_args(9222, Path("C:/tmp/p"))
        com = cdp_edge.montar_args(9222, Path("C:/tmp/p"), "https://exemplo.br/x")
        assert "https://exemplo.br/x" in com
        assert not any(a.startswith("http") for a in sem)

    def test_perfil_respeitado(self):
        args = cdp_edge.montar_args(9222, Path("C:/tmp/perfil-xyz"))
        assert f"--user-data-dir={Path('C:/tmp/perfil-xyz')}" in args


class TestDetectarDesafio:
    @pytest.mark.parametrize(
        "titulo",
        [
            "Just a moment...",
            "just a moment",
            "Um momento...",
            "Attention Required! | Cloudflare",
        ],
    )
    def test_reconhece_interstitial(self, titulo):
        assert cdp_edge.detectar_desafio(titulo) is True

    @pytest.mark.parametrize(
        "titulo",
        [
            "STJ - Jurisprudência do STJ",
            "Pesquisa de Jurisprudência",
            "",
        ],
    )
    def test_nao_dispara_em_pagina_legitima(self, titulo):
        """O par obrigatorio: o caso vizinho que NAO pode ser tratado como desafio."""
        assert cdp_edge.detectar_desafio(titulo) is False

    def test_sentinela_no_corpo_tambem_conta(self):
        assert cdp_edge.detectar_desafio("", "Checking your browser before accessing")

    def test_corpo_longo_nao_dispara_por_mencao_tardia(self):
        """So' o inicio do corpo e' inspecionado: ementa que cite 'um momento'
        no meio do texto nao pode ser lida como interstitial."""
        corpo = ("acordao " * 200) + "um momento"
        assert cdp_edge.detectar_desafio("STJ - Jurisprudência do STJ", corpo) is False


class TestPortaResponde:
    def test_porta_fechada_devolve_false(self):
        # porta alta improvavel de estar em uso; nao deve levantar excecao
        assert cdp_edge.porta_responde(59999, timeout=0.5) is False


class TestPerfilPadrao:
    def test_respeita_env(self, monkeypatch):
        monkeypatch.setenv("DPU_CDP_PERFIL", "D:/perfil-teste")
        assert cdp_edge.perfil_padrao() == Path("D:/perfil-teste")

    def test_fora_do_repositorio(self, monkeypatch):
        """Perfil de navegador nao pode nascer dentro do working tree."""
        monkeypatch.delenv("DPU_CDP_PERFIL", raising=False)
        destino = cdp_edge.perfil_padrao()
        raiz_repo = Path(cdp_edge.__file__).resolve().parents[2]
        assert raiz_repo not in destino.resolve().parents


class TestGarantirNavegador:
    def test_desligado_por_env_levanta_indisponivel(self, monkeypatch):
        monkeypatch.setattr(cdp_edge, "DESABILITADO", True)
        with pytest.raises(cdp_edge.CDPIndisponivel):
            cdp_edge.garantir_navegador(porta=59999)

    def test_porta_ja_de_pe_nao_lanca_processo(self, monkeypatch):
        chamou = {"popen": False}
        monkeypatch.setattr(cdp_edge, "DESABILITADO", False)
        monkeypatch.setattr(cdp_edge, "porta_responde", lambda *a, **k: True)

        def _nao_deve_chamar(*_a, **_k):  # pragma: no cover
            chamou["popen"] = True
            raise AssertionError("nao deveria lancar navegador com a porta de pe")

        monkeypatch.setattr(cdp_edge.subprocess, "Popen", _nao_deve_chamar)
        cdp_edge.garantir_navegador(porta=9222)
        assert chamou["popen"] is False
