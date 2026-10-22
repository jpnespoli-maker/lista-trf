"""Travas de três defeitos do stj-jurisprudencia medidos em 22/10/2026.

1. FALSO ZERO POR ACENTO. O SCON e o portal do Informativo leem a querystring
   em ISO-8859-1; o cliente a mandava em UTF-8. "saúde e cirurgia" dava 0 e
   "saude e cirurgia" 1.113; INFJ "defensoria pública" 0 contra 164. Nenhum
   erro: a página vinha com o aviso de busca vazia, e o canário a aceitava.
2. DOCUMENTO ÚNICO. Com um só resultado o SCON abre o próprio documento
   (`.documento`, pares `.docTitulo`/`.docTexto`) em vez da lista, e o parser
   da lista extraía 0 — "reportou 1 documento(s) mas o parser extraiu 0" em
   toda busca por número (REsp 2222722).
3. NOTA FANTASMA NO INFJ. A lista do Informativo abre com um bloco de edição
   "(NÃO PUBLICADO)", rótulos vazios e CNOT=000000, que saía como resultado.

As fixtures `scon_acor_documento_unico.html` (2222722.NUM.) e
`informativo_infj_nao_publicado.html` ("bem de familia", 2 primeiros blocos)
foram capturadas pelas funções de produção em 22/10/2026 e recortadas aos blocos
que os parsers leem. Página pública, sem dado pessoal.
"""

import importlib.util
from pathlib import Path
from urllib.parse import unquote_to_bytes, urlsplit

import pytest

_RAIZ = Path(__file__).resolve().parents[1]
_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def srv():
    import sys

    if str(_RAIZ) not in sys.path:
        sys.path.insert(0, str(_RAIZ))
    spec = importlib.util.spec_from_file_location(
        "stj_server_acento", _RAIZ / "stj-jurisprudencia" / "server.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ler(nome):
    return (_FIXTURES / nome).read_text(encoding="utf-8")


def _livre(url: str) -> bytes:
    """Bytes do parâmetro `livre` como o servidor os recebe."""
    for par in urlsplit(url).query.split("&"):
        chave, _, valor = par.partition("=")
        if chave == "livre":
            return unquote_to_bytes(valor.replace("+", " "))
    raise AssertionError(f"sem livre= em {url}")


class _SessaoGravadora:
    def __init__(self):
        self.urls = []

    def get(self, url, **_kw):
        self.urls.append(url)
        return type("R", (), {
            "content": b"<html></html>", "encoding": "iso-8859-1",
            "raise_for_status": lambda self: None,
        })()


class TestQuerystringLatin1:
    def test_acento_vai_em_latin1(self, srv):
        assert _livre(srv._url_latin1("https://x/p.jsp", {"livre": "saúde e ação"})) \
            == "saúde e ação".encode("iso-8859-1")

    def test_ascii_nao_muda(self, srv):
        assert _livre(srv._url_latin1("https://x/p.jsp", {"livre": "saude e acao"})) \
            == b"saude e acao"

    def test_aspas_tipograficas_viram_ascii(self, srv):
        """Aspas curvas não existem em Latin-1; sem a troca, a frase exata sumia."""
        url = srv._url_latin1("https://x/p.jsp", {"livre": "“bem de família”"})
        assert _livre(url) == '"bem de família"'.encode("iso-8859-1")

    @pytest.mark.parametrize("pesquisar", ["scon", "informativo"])
    def test_as_rotas_http_mandam_latin1(self, srv, monkeypatch, pesquisar):
        sess = _SessaoGravadora()
        monkeypatch.setattr(srv, "_criar_session_scon", lambda: sess)
        if pesquisar == "scon":
            srv._pesquisar_scon(sess, "saúde e cirurgia", "ACOR", 3,
                                scon_base=srv.SCON_PROCESSO_BASE)
        else:
            srv._pesquisar_informativo("defensoria pública", 3)
        assert _livre(sess.urls[-1]) == (
            "saúde e cirurgia" if pesquisar == "scon" else "defensoria pública"
        ).encode("iso-8859-1")


class TestDocumentoUnico:
    @pytest.fixture(scope="class")
    def doc(self, srv):
        res, total = srv._parse_scon_resumo(_ler("scon_acor_documento_unico.html"), "ACOR", 4000)
        assert total == 1 and len(res) == 1
        return res[0]

    def test_numero_sem_uf_nem_registro(self, doc):
        assert doc.numero == "REsp 2222722"

    def test_relator_sem_matricula(self, doc):
        assert doc.relator == "Ministro SEBASTIÃO REIS JÚNIOR"

    def test_orgao_e_datas(self, doc):
        assert doc.orgao == "T6 - SEXTA TURMA"
        assert doc.data == "DJEN 23/09/2025"
        assert doc.extra["julgamento"] == "17/09/2025"

    def test_ementa_integral_sem_marca_de_truncada(self, doc):
        assert doc.conteudo.startswith("RECURSO ESPECIAL. DIREITO PENAL. DESCAMINHO.")
        assert len(doc.conteudo) > 1000
        assert "ementa_truncada" not in doc.extra

    def test_rota_nao_dispara_o_canario(self, srv, monkeypatch):
        monkeypatch.setattr(srv, "_pesquisar_scon",
                            lambda *_a, **_k: _ler("scon_acor_documento_unico.html"))
        saida, n = srv._rota_scon("2222722.NUM.", "ACOR", 10, 400,
                                  scon_base=srv.SCON_PROCESSO_BASE)
        assert n == 1 and "REsp 2222722" in saida

    def test_lista_vazia_continua_sem_documento(self, srv):
        assert srv._parse_scon_resumo("<html><body></body></html>", "ACOR", 400) == ([], 0)


class TestNotaNaoPublicada:
    def test_bloco_fantasma_nao_vira_resultado(self, srv):
        res, total = srv._parse_informativo_html(_ler("informativo_infj_nao_publicado.html"))
        assert total == 367
        assert [r.numero for r in res] == ["REsp 1.894.973-PR"]
        assert all("CNOT=000000" not in r.extra.get("url", "") for r in res)
