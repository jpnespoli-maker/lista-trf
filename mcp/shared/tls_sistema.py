"""Bundle de CA combinado (certifi + certificate store do Windows).

O Norton Web/Mail Shield intercepta o TLS de saída e re-assina os sites com
raiz própria ("Norton Web/Mail Shield Root"), instalada no store do Windows
mas ausente do bundle certifi dos venvs — requests/curl_cffi caem com
``unable to get local issuer certificate`` (diagnóstico 2026-08-04: CJF,
SCON e google interceptados; portal do STF não). Este módulo gera um PEM
único com o certifi + stores ROOT/CA do Windows, mantendo a validação
ligada tanto para cadeias reais quanto para as re-assinadas.

Uso nos servers (antes de qualquer request)::

    from shared import tls_sistema
    tls_sistema.aplicar()          # exporta REQUESTS_CA_BUNDLE/CURL_CA_BUNDLE

Clientes que não leem env (curl_cffi/libcurl) recebem o caminho explícito::

    session.get(url, verify=tls_sistema.caminho_bundle())

Limitação conhecida: a raiz do Norton viola a RFC 5280 (basicConstraints
não-crítico) e reprova com ``VERIFY_X509_STRICT`` mesmo confiada — não
ativar modo estrito, e migração dos venvs para Python 3.13+ (estrito por
default) exigirá revisitar isto.
"""

from __future__ import annotations

import os
import ssl
import tempfile
from pathlib import Path

import certifi

_CERTS_DIR = Path(__file__).resolve().parent.parent.parent / "certs"
_BUNDLE = _CERTS_DIR / "ca-bundle-sistema.pem"

# Regenera se o bundle for mais velho que isto (pega raiz nova de AV/proxy
# sem pagar a enumeração do store a cada startup de cada server).
_MAX_IDADE_S = 7 * 86400

_STORES_WINDOWS = ("ROOT", "CA")


def _certs_do_windows() -> list[str]:
    """PEMs únicos dos stores ROOT/CA do Windows; vazio fora do Windows."""
    enum = getattr(ssl, "enum_certificates", None)
    if enum is None:
        return []
    vistos: set[bytes] = set()
    pems: list[str] = []
    for store in _STORES_WINDOWS:
        try:
            certs = enum(store)
        except OSError:
            continue
        for der, encoding, _trust in certs:
            if encoding != "x509_asn" or der in vistos:
                continue
            vistos.add(der)
            pems.append(ssl.DER_cert_to_PEM_cert(der))
    return pems


def gerar(force: bool = False) -> Path:
    """Gera (ou reaproveita) o bundle combinado; devolve o caminho.

    Fora do Windows, ou se a enumeração do store não trouxer nada, devolve
    o próprio certifi — comportamento idêntico ao anterior ao módulo.
    """
    if not force and _BUNDLE.exists():
        idade = __import__("time").time() - _BUNDLE.stat().st_mtime
        if idade < _MAX_IDADE_S:
            return _BUNDLE

    extras = _certs_do_windows()
    if not extras:
        return Path(certifi.where())

    _CERTS_DIR.mkdir(parents=True, exist_ok=True)
    base = Path(certifi.where()).read_text(encoding="utf-8")
    corpo = base + "\n# --- certificate store do Windows (ROOT+CA) ---\n" + "\n".join(extras)
    # Escrita atômica: server concorrente nunca lê bundle pela metade.
    fd, tmp = tempfile.mkstemp(dir=_CERTS_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(corpo)
        os.replace(tmp, _BUNDLE)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return _BUNDLE


def caminho_bundle() -> str:
    """Caminho do bundle como str, para verify= explícito (curl_cffi)."""
    return str(gerar())


def aplicar() -> str:
    """Gera o bundle e exporta as envs que o ``requests`` honra.

    ``setdefault``: um override manual do operador (ex.: TJRJ_CA_BUNDLE ou
    REQUESTS_CA_BUNDLE já apontando para outro PEM) tem precedência.
    """
    caminho = caminho_bundle()
    os.environ.setdefault("REQUESTS_CA_BUNDLE", caminho)
    os.environ.setdefault("CURL_CA_BUNDLE", caminho)
    return caminho
