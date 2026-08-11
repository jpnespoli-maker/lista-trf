"""
MCP Server: JULIA - Sistema de Jurisprudência do TRF5 (Interface Pública)

Acesso ao portal público JULIA do TRF5:
  https://juliapesquisa.trf5.jus.br/julia-pesquisa/

Cobre o 2º grau do TRF5 (``G2``), as Turmas Recursais das seis seções
(``TR_AL``, ``TR_CE``, ``TR_PB``, ``TR_PE``, ``TR_RN``, ``TR_SE``) e a Turma
Regional de Uniformização da 5ª Região (``TRU``). NÃO cobre o 1º grau — a
instância ``G1`` não existe na API — nem a TNU. Não requer autenticação.

A instância é o único isolamento de órgão que a API oferece: ela vai no path,
não em parâmetro. ``orgaoJulgador`` filtra DENTRO da instância escolhida.
"""

from mcp.server.fastmcp import FastMCP
import re
import requests
import time
from typing import List, Dict, Any, Optional
from pathlib import Path
from tenacity import retry, wait_exponential, stop_after_attempt
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared import tls_sistema

tls_sistema.aplicar()
from shared.base_juridica import (
    BaseResultadoJuridico,
    formatar_resultados_xml,
    truncar_por_tokens,
    limpar_texto_html,
    sanitizar_comentario_xml,
)

# Onda 2 — cache HTTP (TTL 2d para Julia — portal mais volátil).
try:
    from shared.cache_http import cached_http, registrar_dispositivo  # type: ignore
except ImportError:
    def cached_http(*_a, **_kw):  # type: ignore
        return None

    def registrar_dispositivo(*_a, **_kw) -> None:  # type: ignore
        pass

# Onda 1 — logger.
try:
    _DPU_SCRIPTS = Path.home() / ".claude" / "DPU" / "Scripts"
    if str(_DPU_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_DPU_SCRIPTS))
    from query_logger import log_query  # type: ignore
except ImportError:
    def log_query(**_kwargs: Any) -> None:  # type: ignore
        pass

mcp = FastMCP("julia-trf5")
_JULIA_TTL_S = 2 * 86400

JULIA_API_URL = "https://juliapesquisa.trf5.jus.br/julia-pesquisa/api/v1"

SECOES_JUDICIARIAS = {
    "AL": "Alagoas",
    "CE": "Ceará",
    "PB": "Paraíba",
    "PE": "Pernambuco",
    "RN": "Rio Grande do Norte",
    "SE": "Sergipe",
}

INSTANCIAS = {
    "G2":    "TRF5 — 2º Grau (Acórdãos do Pleno e Turmas)",
    "TR_AL": "Turma Recursal — Alagoas",
    "TR_CE": "Turma Recursal — Ceará",
    "TR_PB": "Turma Recursal — Paraíba",
    "TR_PE": "Turma Recursal — Pernambuco",
    "TR_RN": "Turma Recursal — Rio Grande do Norte",
    "TR_SE": "Turma Recursal — Sergipe",
    # TRU é REGIONAL (5ª Região), não nacional — a nacional é a TNU, que este
    # portal não indexa. Chamá-la de "nacional" convida a citá-la com peso que
    # ela não tem.
    "TRU":   "Turma Regional de Uniformização — 5ª Região",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Referer": "https://juliapesquisa.trf5.jus.br/julia-pesquisa/pesquisa",
}


def _montar_params(
    pesquisa_livre: str,
    numero_processo: str = "",
    orgao_julgador: str = "",
    relator: str = "",
    data_ini: str = "",
    data_fim: str = "",
    start: int = 0,
    length: int = 10,
) -> Dict:
    """
    Monta o dicionário de parâmetros para a API DataTables do JULIA.
    O campo columns[0] é obrigatório para instâncias TR_* — sem ele a API retorna 400.
    """
    return {
        "draw": 1,
        "columns[0][data]": "codigoDocumento",
        "columns[0][name]": "",
        "columns[0][searchable]": "true",
        "columns[0][orderable]": "false",
        "columns[0][search][value]": "",
        "columns[0][search][regex]": "false",
        "start": start,
        "length": length,
        "search[value]": "",
        "search[regex]": "false",
        "pesquisaLivre": pesquisa_livre,
        "numeroProcesso": numero_processo,
        "orgaoJulgador": orgao_julgador,
        "relator": relator,
        "dataIni": data_ini,
        "dataFim": data_fim,
        "_": int(time.time() * 1000),
    }


@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
def _buscar(instancia: str, params: Dict) -> Dict:
    session = requests.Session()
    session.headers.update(HEADERS)
    resp = session.get(
        f"{JULIA_API_URL}/documento:dt/{instancia}",
        params=params,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def _extrair_ementa_julia(texto: str) -> str:
    """Recorta a ementa do inteiro teor devolvido pelo JULIA.

    O documento repete a palavra EMENTA como cabeçalho antes do timbre e do
    número do processo, e só a terceira ocorrência costuma iniciar o texto
    ementado. Nas Turmas Recursais é assim::

        EMENTA | PODER JUDICIÁRIO Turma Recursal de Pernambuco … RECORRIDO: INSS
        EMENTA 0059104-96.2025.4.05.8300
        EMENTA: DIREITO PREVIDENCIÁRIO. BENEFÍCIOS POR INCAPACIDADE. …   ← esta

    Por isso a marca é procurada pelo que vem DEPOIS dela: descarta a ocorrência
    seguida de "PODER JUDICIÁRIO" (timbre) ou de número de processo. O corte
    final é no início do relatório/voto — o que se cita é a ementa, não o
    acórdão inteiro.

    Sem marca reconhecível, devolve o texto como veio; melhor entregar demais
    que entregar recorte errado de um julgado que vai para a peça.
    """
    if not texto:
        return ""
    for m in re.finditer(r"\bEMENTA\b:?", texto):
        seguinte = texto[m.end():m.end() + 60].strip()
        if seguinte.upper().startswith("PODER JUDICI"):
            continue
        if re.match(r"^\d{7}-?\d{2}\.?\d{4}", seguinte) or re.match(r"^\d{15,}", seguinte):
            continue
        corpo = texto[m.end():].lstrip(" :")
        fim = re.search(r"\b(RELAT[ÓO]RIO|VOTO|AC[ÓO]RD[ÃA]O)\b", corpo)
        return (corpo[:fim.start()] if fim else corpo).strip()
    return texto


def _doc_para_resultado(
    doc: Dict, max_tokens_ementa: int = 400, instancia: str = ""
) -> BaseResultadoJuridico:
    # `resumo` NÃO é a ementa: é uma linha de cabeçalho de ~120-155 caracteres
    # ("(PROCESSO: …, RECURSO INOMINADO CÍVEL, <relator>, …, JULGAMENTO: …)").
    # Como nunca vem vazia, o antigo `resumo or texto` curto-circuitava sempre e
    # o campo `texto` — 2 mil a 33 mil caracteres, onde está a EMENTA e o voto —
    # jamais era lido. Toda citação do TRF5 saía sem ementa, o que esvazia a
    # regra da ementa integral e deixa o `validar_fontes.py` sem trecho para
    # casar. Medido em 2026-08-11 nas instâncias G2, TR_PE e TRU.
    bruto = doc.get("texto") or doc.get("resumo") or ""
    texto = truncar_por_tokens(
        _extrair_ementa_julia(limpar_texto_html(bruto)), max_tokens=max_tokens_ementa
    )
    data = (doc.get("dataAssinatura") or doc.get("dataJulgamento") or "")[:10]
    # `url` vem null em toda resposta da API; sem isto o campo `fonte` saía
    # vazio. A instância é o que a peça precisa saber: 2º grau, Turma Recursal
    # ou TRU não têm o mesmo peso.
    return BaseResultadoJuridico(
        conteudo=texto,
        fonte=doc.get("url") or (f"JULIA/{instancia}" if instancia else ""),
        tipo=doc.get("tipoDocumento", ""),
        orgao=doc.get("orgaoJulgador", ""),
        numero=doc.get("numeroProcesso", ""),
        relator=doc.get("relator", ""),
        data=data,
        extra={"classe": doc.get("classeJudicial", "")},
    )


@mcp.tool()
def buscar_julia(
    termo: str,
    instancia: str = "G2",
    numero_processo: str = "",
    orgao_julgador: str = "",
    relator: str = "",
    data_inicial: str = "",
    data_final: str = "",
    max_resultados: int = 10,
    max_tokens_ementa: int = 400,
) -> str:
    """
    Busca jurisprudência no portal público JULIA do TRF5.
    Use ajuda_sintaxe_julia() para guia completo de operadores e exemplos.

    SINTAXE RÁPIDA (operadores em MINÚSCULO):
    "e", "ou", "nao", "prox", "adj", "$" (truncamento).

    INSTÂNCIAS: G2 (2º grau, padrão) | TR_AL | TR_CE | TR_PB | TR_PE | TR_RN | TR_SE | TRU

    Args:
        termo: Query com sintaxe JULIA (operadores minúsculos).
        instancia: Código da instância. Default: "G2".
        numero_processo: Número do processo específico.
        orgao_julgador: Turma/órgão julgador (ex: "1ª TURMA").
        relator: Nome do magistrado relator.
        data_inicial: Data inicial no formato YYYY-MM-DD.
        data_final: Data final no formato YYYY-MM-DD.
        max_resultados: Máximo de resultados (1–100). Default: 10.
        max_tokens_ementa: Truncamento da ementa em tokens (50-4000). Default: 400.
                           Aumente para obter ementa mais longa/íntegra.

    Returns:
        XML estruturado com os documentos encontrados.
    """
    instancia = instancia.upper()
    max_tokens_ementa = max(50, min(int(max_tokens_ementa), 4000))
    if instancia not in INSTANCIAS:
        validas = ", ".join(INSTANCIAS.keys())
        return f'<erro>Instância "{instancia}" inválida. Use: {validas}</erro>'

    t0 = time.perf_counter()
    cache_hit = False
    n_results = 0
    erro_final: Optional[str] = None
    try:
        # A1 — cache HTTP. Inclui todos os filtros que mudam o resultado.
        cache_key = {
            "mcp": "julia-trf5",
            "termo": termo,
            "instancia": instancia,
            "numero_processo": numero_processo,
            "orgao_julgador": orgao_julgador,
            "relator": relator,
            "data_inicial": data_inicial,
            "data_final": data_final,
            "max_resultados": max_resultados,
            "max_tokens_ementa": max_tokens_ementa,
            # v2: até 2026-08-11 o `conteudo` cacheado era o cabeçalho do campo
            # `resumo`, não a ementa. Sem virar a versão, o cache de 2 dias
            # continuaria servindo o resultado sem ementa já corrigido no código.
            "v": 2,
        }
        cached = cached_http("julia-trf5", cache_key)
        if cached is not None:
            cache_hit = True
            import json as _json
            payload = _json.loads(cached)
            n_results = payload.get("n", 0)
            return payload["xml"]

        params = _montar_params(
            pesquisa_livre=termo,
            numero_processo=numero_processo,
            orgao_julgador=orgao_julgador,
            relator=relator,
            data_ini=data_inicial,
            data_fim=data_final,
            length=min(max_resultados, 100),
        )
        dados = _buscar(instancia, params)
        total = dados.get("recordsTotal", 0)
        resultados = [
            _doc_para_resultado(d, max_tokens_ementa, instancia)
            for d in dados.get("data", [])
        ]
        n_results = len(resultados)
        xml = formatar_resultados_xml(resultados, "jurisprudencia_julia")
        meta = f'<!-- Busca: "{sanitizar_comentario_xml(termo)}" | Instância: {instancia} ({INSTANCIAS[instancia]}) | Total: {total} -->\n'
        saida = meta + xml
        try:
            import json as _json
            registrar_dispositivo(
                "julia-trf5", cache_key,
                _json.dumps({"xml": saida, "n": n_results}), ttl_s=_JULIA_TTL_S,
            )
        except Exception:
            pass
        return saida

    except Exception as e:
        erro_final = str(e)
        return f'<erro>Falha na busca JULIA: {str(e)}</erro>'
    finally:
        log_query(
            mcp="julia-trf5",
            tool="buscar_julia",
            query=termo,
            filtros={
                "instancia": instancia,
                "orgao_julgador": orgao_julgador,
                "relator": relator,
                "data_inicial": data_inicial,
                "data_final": data_final,
            },
            n_resultados=n_results,
            ms=int((time.perf_counter() - t0) * 1000),
            cache_hit=cache_hit,
            erro=erro_final,
        )


@mcp.tool()
def ajuda_sintaxe_julia() -> str:
    """
    Retorna guia completo de sintaxe, operadores, instâncias e exemplos para buscar_julia.
    Consulte antes de formular queries complexas.
    """
    return """
SINTAXE JULIA (TRF5) — Operadores em MINÚSCULO

OPERADORES:
  e    — ambos obrigatórios                pensão e morte
  ou   — qualquer um                       aposentadoria ou benefício
  nao  — exclui segundo                    servidor nao militar
  prox — próximos (até 5 palavras, mesma ordem)   processo prox físico
  adj  — adjacentes (até 5 palavras, qq ordem)    auxílio adj doença
  $    — wildcard de sufixo                aposentad$

INSTÂNCIAS DISPONÍVEIS:
  G2     — TRF5 2º grau (padrão)
  TR_AL  — Turma Recursal Alagoas
  TR_CE  — Turma Recursal Ceará
  TR_PB  — Turma Recursal Paraíba
  TR_PE  — Turma Recursal Pernambuco
  TR_RN  — Turma Recursal Rio Grande do Norte
  TR_SE  — Turma Recursal Sergipe
  TRU    — Turma Recursal Unificada (nacional)

EXEMPLOS (pergunta → query):
  "Pensão por morte para companheiro homoafetivo"
    → pensão e morte e (homoafetivo ou "mesmo sexo" ou "união estável")

  "Aposentadoria especial para eletricista"
    → aposentad$ e especial e (eletricista ou "energia elétrica")

  "BPC para idoso estrangeiro"
    → (bpc ou loas) e idoso e (estrangeiro ou nacionalidade)

  "INSS pode cessar auxílio-doença sem perícia?"
    → "auxílio-doença" e (cessação ou alta) e perícia

FILTROS ADICIONAIS:
  orgao_julgador="1ª TURMA"          → filtra por turma
  relator="Nome do Desembargador"    → filtra por relator
  data_inicial="2023-01-01"          → filtra por período
"""


if __name__ == "__main__":
    mcp.run()
