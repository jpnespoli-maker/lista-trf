"""Testes do bundle de CA combinado (certifi + store do Windows).

Contexto: Norton Web/Mail Shield re-assina o TLS de saída (raiz no store do
Windows, fora do certifi) — sem o bundle combinado, requests/curl_cffi caem
com "unable to get local issuer certificate" (2026-08-04).
"""

import os
import ssl
import sys
from pathlib import Path

import certifi
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared import tls_sistema  # noqa: E402

_EH_WINDOWS = sys.platform == "win32"


def _conta_certs(pem_path: str) -> int:
    return Path(pem_path).read_text(encoding="utf-8").count("BEGIN CERTIFICATE")


def test_gerar_devolve_caminho_existente():
    caminho = tls_sistema.gerar(force=True)
    assert caminho.exists()


@pytest.mark.skipif(not _EH_WINDOWS, reason="store do Windows só existe no win32")
def test_bundle_windows_amplia_o_certifi():
    caminho = tls_sistema.gerar(force=True)
    assert caminho != Path(certifi.where())
    assert _conta_certs(str(caminho)) > _conta_certs(certifi.where())


def test_bundle_e_pem_valido_para_ssl():
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cafile=str(tls_sistema.gerar()))


def test_cache_reaproveita_bundle_recente():
    a = tls_sistema.gerar(force=True)
    mtime = a.stat().st_mtime
    b = tls_sistema.gerar()
    assert b == a
    assert b.stat().st_mtime == mtime


def test_aplicar_exporta_envs_sem_sobrescrever_override(monkeypatch):
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    caminho = tls_sistema.aplicar()
    assert os.environ["REQUESTS_CA_BUNDLE"] == caminho
    assert os.environ["CURL_CA_BUNDLE"] == caminho

    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "C:/override.pem")
    tls_sistema.aplicar()
    assert os.environ["REQUESTS_CA_BUNDLE"] == "C:/override.pem"
