"""
Módulo compartilhado para servidores MCP jurídicos.
Fornece tipos base, utilitários de formatação e constantes.
"""

import re
import html as html_module
from dataclasses import dataclass, field
from typing import List, Optional
from xml.sax.saxutils import escape as xml_escape


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

TIPOS_PRECEDENTES = {
    "RG": "Repercussão Geral (STF)",
    "RR": "Recurso Repetitivo (STJ)",
    "SV": "Súmula Vinculante (STF)",
    "SUM": "Súmula (STF/STJ)",
    "IRDR": "Incidente de Resolução de Demandas Repetitivas",
    "IAC": "Incidente de Assunção de Competência",
    "PUIL": "Pedido de Uniformização de Interpretação de Lei",
    "TST": "Precedente do TST",
}

TRIBUNAIS = {
    "STF": "Supremo Tribunal Federal",
    "STJ": "Superior Tribunal de Justiça",
    "TRF1": "Tribunal Regional Federal da 1ª Região",
    "TRF2": "Tribunal Regional Federal da 2ª Região",
    "TRF3": "Tribunal Regional Federal da 3ª Região",
    "TRF4": "Tribunal Regional Federal da 4ª Região",
    "TRF5": "Tribunal Regional Federal da 5ª Região",
    "TRF6": "Tribunal Regional Federal da 6ª Região",
}


# ---------------------------------------------------------------------------
# Tipo base para resultados jurídicos
# ---------------------------------------------------------------------------

@dataclass
class BaseResultadoJuridico:
    """Estrutura unificada para resultados de pesquisa jurídica."""
    conteudo: str = ""
    fonte: str = ""
    tipo: str = ""
    orgao: str = ""
    numero: str = ""
    situacao: str = ""
    relator: str = ""
    data: str = ""
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Utilitários de texto
# ---------------------------------------------------------------------------

def limpar_texto_html(texto: str) -> str:
    """Remove tags HTML e decodifica entidades HTML."""
    if not texto:
        return ""
    texto = html_module.unescape(texto)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def truncar_por_tokens(texto: str, max_tokens: int = 1000) -> str:
    """
    Trunca texto respeitando aproximadamente o limite de tokens.
    Estimativa: 1 token ≈ 4 caracteres (padrão GPT/Claude para português).
    Trunca no último ponto final antes do limite para preservar frases.
    """
    if not texto:
        return ""
    max_chars = max_tokens * 4
    if len(texto) <= max_chars:
        return texto

    truncado = texto[:max_chars]
    ultimo_ponto = truncado.rfind(".")
    if ultimo_ponto > max_chars // 2:
        truncado = truncado[: ultimo_ponto + 1]

    return truncado + " [...]"


def sanitizar_comentario_xml(valor: str) -> str:
    """Torna um valor seguro para interpolação em comentário XML.

    A sequência ``--`` é proibida dentro de ``<!-- ... -->`` (e ``-->``
    encerraria o comentário no meio da query). Substitui por travessão.
    """
    return str(valor).replace("--", "—")


def extrair_ementa(texto: str) -> str:
    """
    Extrai a seção de ementa de um documento jurídico.
    Tenta encontrar o bloco entre 'EMENTA' e o próximo marcador de seção.
    """
    if not texto:
        return ""

    texto_limpo = limpar_texto_html(texto)

    # Padrão 1: EMENTA seguida de conteúdo até próxima seção em maiúsculas
    match = re.search(
        r"(?:EMENTA|Ementa)\s*[:\-]?\s*(.+?)(?=\n[A-Z]{4,}|\Z)",
        texto_limpo,
        re.DOTALL,
    )
    if match:
        ementa = match.group(1).strip()
        ementa = re.sub(r"\s+", " ", ementa)
        return ementa

    # Padrão 2: Primeiros 2000 chars como fallback
    return texto_limpo[:2000].strip()


# ---------------------------------------------------------------------------
# Formatação XML
# ---------------------------------------------------------------------------

def formatar_resultados_xml(
    resultados: List[BaseResultadoJuridico],
    tag_raiz: str = "resultados",
) -> str:
    """
    Formata uma lista de BaseResultadoJuridico como XML estruturado.

    Args:
        resultados: Lista de resultados a formatar.
        tag_raiz: Tag XML raiz do elemento container.

    Returns:
        String XML formatada.
    """
    linhas = [f"<{tag_raiz} total=\"{len(resultados)}\">"]

    for i, r in enumerate(resultados, 1):
        linhas.append(f'  <resultado indice="{i}">')

        if r.numero:
            linhas.append(f"    <numero>{xml_escape(r.numero)}</numero>")
        if r.tipo:
            linhas.append(f"    <tipo>{xml_escape(r.tipo)}</tipo>")
        if r.orgao:
            linhas.append(f"    <orgao>{xml_escape(r.orgao)}</orgao>")
        if r.relator:
            linhas.append(f"    <relator>{xml_escape(r.relator)}</relator>")
        if r.data:
            linhas.append(f"    <data>{xml_escape(r.data)}</data>")
        if r.fonte:
            linhas.append(f"    <fonte>{xml_escape(r.fonte)}</fonte>")
        if r.conteudo:
            linhas.append(f"    <conteudo>{xml_escape(r.conteudo)}</conteudo>")

        # Campos extras
        for chave, valor in r.extra.items():
            if valor:
                linhas.append(f"    <{chave}>{xml_escape(str(valor))}</{chave}>")

        linhas.append("  </resultado>")

    linhas.append(f"</{tag_raiz}>")
    return "\n".join(linhas)
