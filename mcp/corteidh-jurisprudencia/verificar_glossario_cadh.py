"""Mede a procedência de cada linha do glossário contra o texto OFICIAL da
Convenção Americana nas quatro línguas.

**Por que medir em vez de afirmar.** Um glossário com `fonte = "CADH"` escrito à
mão afirma procedência que ninguém conferiu — e este projeto trata relato de
procedência como asserção de fato. Aqui o `fonte` de cada linha diz exatamente
quais línguas foram CONFIRMADAS no texto oficial, e o que não se confirmou sai
como `curadoria`, que é ignorância declarada e não defeito.

**O que a medição NÃO prova.** Que um termo apareça na CADH não o torna a
tradução correta do par: "pension" aparece nos textos inglês e francês por
razões diferentes. A medição prova que o termo é vocabulário da Convenção
naquela língua, não que o par pt→xx esteja certo. A correção do par é curadoria,
e está declarada como tal.

Uso:
    python verificar_glossario_cadh.py            # só mede e reporta
    python verificar_glossario_cadh.py --json <f> # grava o mapa pt -> fonte
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import glossario_dados  # noqa: E402

# Convenção Americana sobre Direitos Humanos (Pacto de San José), textos
# oficiais publicados pela OEA. Endereços conferidos na corrida que os usa —
# falhando algum, o relatório o declara e a língua sai sem confirmação, nunca
# confirmada por omissão.
FONTES = {
    "es": "https://www.oas.org/dil/esp/tratados_b-32_convencion_americana_sobre_derechos_humanos.htm",
    "en": "https://www.oas.org/dil/treaties_B-32_American_Convention_on_Human_Rights.htm",
    # O padrao /dil/port/ NAO existe (404 medido em 18/09/2026); o portugues
    # vive em /juridico/portuguese/.
    "pt": "https://www.oas.org/juridico/portuguese/treaties/b-32.htm",
    # A OEA nao publica o texto frances em HTML: /juridico/french/traites/b-32
    # devolve http=200 com 8.677 caracteres e NENHUM canario — e' pagina-indice,
    # e um teste que se contentasse com o 200 mediria contra o texto errado. O
    # frances oficial esta no registro do tratado na ONU (art. 81 da propria
    # Convencao: feita "nos idiomas espanhol, ingles, portugues e frances").
    "fr": ("https://treaties.un.org/doc/Publication/UNTS/Volume%201144/"
           "volume-1144-I-17955-French.pdf"),
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Ordem das colunas em glossario_dados.TERMOS
COLUNA = {"pt": 0, "es": 1, "en": 2, "fr": 3}


def normalizar(texto: str) -> str:
    """Minúsculas, sem acento, espaço colapsado — para casar 'saúde' com
    'SAUDE' e com 'sa&uacute;de' já decodificado."""
    sem_tag = re.sub(r"<[^>]+>", " ", texto)
    sem_ent = (sem_tag.replace("&nbsp;", " ").replace("&amp;", "&")
               .replace("&quot;", '"').replace("&#39;", "'")
               .replace("&rsquo;", "'").replace("&lsquo;", "'"))
    nfkd = unicodedata.normalize("NFKD", sem_ent)
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Apóstrofo tipográfico e hífen viram o caractere simples, senão
    # "droits de l'homme" não casa com "droits de l’homme".
    sem_acento = sem_acento.replace("’", "'").replace("‘", "'")
    sem_acento = sem_acento.replace("‐", "-").replace("‑", "-")
    return re.sub(r"\s+", " ", sem_acento).lower()


def baixar(url: str) -> tuple[str | None, str]:
    """(texto normalizado, diagnóstico). Nunca levanta: língua que não baixou
    sai sem confirmação e o relatório diz por quê."""
    try:
        import requests
    except ImportError:
        return None, "requests ausente"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=90)
    except Exception as e:  # rede, TLS, DNS
        return None, f"{type(e).__name__}"
    if r.status_code != 200:
        return None, f"http={r.status_code}"

    if r.content.startswith(b"%PDF-"):
        try:
            import fitz
        except ImportError:
            return None, "http=200 mas PDF e PyMuPDF ausente"
        doc = fitz.open(stream=r.content, filetype="pdf")
        bruto = "\n".join(p.get_text() for p in doc)
        paginas = doc.page_count
        doc.close()
        return normalizar(bruto), f"http=200 pdf paginas={paginas}"

    # A OEA serve essas páginas em latin-1 sem declarar direito.
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
        r.encoding = "utf-8" if b"utf-8" in r.content[:2000].lower() else "latin-1"
    return normalizar(r.text), f"http=200 bytes={len(r.content)}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", help="grava o mapa pt -> fonte medida")
    args = ap.parse_args()

    corpora: dict[str, str] = {}
    print("=== textos oficiais da CADH ===")
    for lang, url in FONTES.items():
        texto, diag = baixar(url)
        print(f"  {lang}: {diag}")
        if texto:
            corpora[lang] = texto
    if not corpora:
        print("\nNENHUM texto oficial baixou. Sem corpus nao ha medicao —")
        print("o glossario NAO recebe procedencia, e isso e' o resultado correto.")
        return 2

    # Calibração: um termo que TEM de estar em cada texto oficial. Sem ela, um
    # corpus truncado devolveria "nada confirmado" e o zero se leria como
    # ausência de procedência.
    # Canário DUPLO, e o segundo é o que importa: `http=200` não prova corpus.
    # Medido em 18/09/2026 — a página francesa da OEA devolve 200 com 8.677
    # caracteres e é um ÍNDICE, sem uma linha da Convenção. Aceita como corpus,
    # ela faria os 161 termos franceses saírem "não confirmados", e o número
    # apareceria como resultado da medição em vez de defeito dela. O segundo
    # canário é uma frase do ART. 1º, que índice nenhum carrega.
    print("\n=== calibracao (termo E frase do art. 1o) ===")
    canario = {
        "es": ("derechos humanos", "respetar los derechos"),
        "en": ("human rights", "to respect the rights"),
        "pt": ("direitos humanos", "respeitar os direitos"),
        "fr": ("droits de l'homme", "respecter les droits"),
    }
    cegos = []
    for lang, texto in corpora.items():
        termo, frase = canario[lang]
        ok_t = normalizar(termo) in texto
        ok_f = normalizar(frase) in texto
        print(f"  {lang}: termo={'OK' if ok_t else 'AUSENTE'} "
              f"art1={'OK' if ok_f else 'AUSENTE'}  ({len(texto)} chars)")
        if not (ok_t and ok_f):
            cegos.append(lang)
    for lang in cegos:
        print(f"  descartando {lang}: o canario nao apareceu, o corpus nao serve")
        corpora.pop(lang)
    if not corpora:
        print("\nTodos os corpora reprovaram a calibracao. Sem medicao.")
        return 2

    print("\n=== medicao por linha ===")
    mapa: dict[str, str] = {}
    hist: dict[int, int] = {}
    for tupla in glossario_dados.TERMOS:
        confirmadas = []
        for lang, texto in corpora.items():
            termo = normalizar(tupla[COLUNA[lang]])
            if termo and termo in texto:
                confirmadas.append(lang)
        ordem = [l for l in ("pt", "es", "en", "fr") if l in confirmadas]
        mapa[tupla[0]] = ("CADH:" + ",".join(ordem)) if ordem else "curadoria"
        hist[len(ordem)] = hist.get(len(ordem), 0) + 1

    total = len(glossario_dados.TERMOS)
    print(f"  linhas medidas: {total}")
    print(f"  linguas com corpus valido: {sorted(corpora)}")
    for n in sorted(hist, reverse=True):
        print(f"  confirmado em {n} lingua(s): {hist[n]:>3}"
              f"  ({100 * hist[n] / total:.1f}%)")
    curadoria = sum(1 for v in mapa.values() if v == "curadoria")
    print(f"  saem como 'curadoria' (nenhuma confirmacao): {curadoria}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(mapa, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nmapa gravado em {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
