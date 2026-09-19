"""As duas declarações de dependência da raiz têm de concordar.

Este repositório declara as dependências do projeto-raiz em DOIS lugares, e os
dois são reais: o `pyproject.toml` (`[project].dependencies`), que é o que um
instalador de pacote lê, e o `requirements.txt`, que é o que de fato roda — o
`iniciar.bat` faz `pip install -r requirements.txt` antes de subir o servidor, e
o README manda o mesmo.

Duas fontes da mesma verdade derivam, e esta derivou: medido em 18/09/2026, o
`pyproject` pedia `python-docx>=1.1.0` e o `requirements.txt` pedia `>=1.2.0`.
Ninguém notou porque nada conferia. O instalado era 1.2.0, isto é, quem seguisse
o `pyproject` poderia montar um ambiente que o projeto não exercita.

O teste não escolhe qual das duas é a certa — ele exige que digam a MESMA coisa,
que é o invariante. Divergindo, quem consertar decide a direção olhando o que
está instalado e o que o código usa.

Não cobre os `optional-dependencies` do grupo `dev` (pytest e pytest-asyncio):
eles são de desenvolvimento e ficam de fora do `requirements.txt` de propósito,
porque quem roda o servidor não precisa deles.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PYPROJECT = RAIZ / "pyproject.toml"
REQUIREMENTS = RAIZ / "requirements.txt"

# PEP 503: `PyMuPDF`, `pymupdf` e `py_mupdf` são o mesmo pacote. Comparar sem
# normalizar acusaria divergência onde não há.
_SEPARADOR = re.compile(r"[-_.]+")

# Nome + especificador, ignorando extras e marcadores de ambiente, que nenhuma
# das duas fontes usa hoje. Aparecendo um, o teste falha na asserção de forma
# abaixo em vez de comparar errado calado.
_REQUISITO = re.compile(
    r"^(?P<nome>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?P<spec>[<>=!~][^;#]*)?$")


def _normalizar(nome: str) -> str:
    return _SEPARADOR.sub("-", nome).lower()


def _parsear(linha: str) -> tuple[str, str]:
    m = _REQUISITO.match(linha.strip())
    assert m, f"requisito em forma não suportada por este teste: {linha!r}"
    return _normalizar(m.group("nome")), (m.group("spec") or "").strip()


def _do_pyproject() -> dict[str, str]:
    dados = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return dict(_parsear(d) for d in dados["project"]["dependencies"])


def _do_requirements() -> dict[str, str]:
    linhas = [
        linha.strip()
        for linha in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if linha.strip() and not linha.strip().startswith("#")
    ]
    return dict(_parsear(linha) for linha in linhas)


def test_os_dois_arquivos_declaram_os_MESMOS_pacotes():
    do_py, do_req = _do_pyproject(), _do_requirements()
    so_no_pyproject = sorted(set(do_py) - set(do_req))
    so_no_requirements = sorted(set(do_req) - set(do_py))

    assert not so_no_pyproject, (
        f"declarados no pyproject.toml e ausentes do requirements.txt: "
        f"{so_no_pyproject}. Quem roda `iniciar.bat` não os instalaria.")
    assert not so_no_requirements, (
        f"declarados no requirements.txt e ausentes do pyproject.toml: "
        f"{so_no_requirements}. Quem instala o pacote não os receberia.")


def test_os_dois_arquivos_pedem_a_MESMA_versao():
    # A derivação que motivou este teste: `>=1.1.0` contra `>=1.2.0`, silenciosa
    # porque os nomes batiam e ninguém comparava o especificador.
    do_py, do_req = _do_pyproject(), _do_requirements()
    divergentes = {
        nome: (spec, do_req[nome])
        for nome, spec in do_py.items()
        if nome in do_req and spec != do_req[nome]
    }
    assert not divergentes, (
        "pyproject.toml e requirements.txt pedem versões diferentes "
        f"(pacote: (pyproject, requirements)): {divergentes}")


def test_o_lock_de_gerenciador_nao_entra_no_repo():
    # `uv.lock` seria uma TERCEIRA declaração, que nenhuma ferramenta deste repo
    # lê e ninguém regenera — lock que não se atualiza diverge em silêncio. O
    # `.gitignore` o barra; este teste garante que a linha não saia de lá sem
    # que alguém decida adotar o gerenciador junto com quem o leia.
    #
    # Pergunta-se ao GIT, e não ao texto do `.gitignore`. Medido em 18/09/2026:
    # a primeira versão deste teste procurava `uv.lock` como substring do
    # arquivo, e o COMENTÁRIO acima — que também escreve `uv.lock` — sustentava
    # a asserção depois de a regra ter sido apagada. Teste cego que sobreviveu à
    # mutação. Quem decide se um caminho é ignorado é `git check-ignore`.
    p = subprocess.run(
        ["git", "-C", str(RAIZ), "check-ignore", "-q", "uv.lock"],
        capture_output=True)
    assert p.returncode == 0, (
        "o git NÃO está ignorando uv.lock — a regra saiu do .gitignore, e o "
        "lock voltaria a aparecer como arquivo a versionar")
