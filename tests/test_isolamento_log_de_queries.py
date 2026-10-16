"""A suíte não grava no log de produção das queries.

Os servidores MCP chamam `query_logger.log_query`, que escreve em
`~/.claude/DPU/logs/jurisprudencia_queries.jsonl` — o log que o
`analisar_queries.py` lê para medir o uso real das bases. Medido em 16/10/2026:
cada corrida desta suíte acrescentava 18 linhas da Corte IDH (buscas de teste
contadas como uso). O `conftest.py` liga `DPU_QUERY_LOG_DESLIGADO=1` antes de
qualquer import; este teste confere que o logger da sessão está de fato mudo.
"""

import sys
from pathlib import Path

SCRIPTS = Path.home() / ".claude" / "DPU" / "Scripts"


def test_logger_de_queries_desligado_na_sessao_de_teste():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import query_logger

    assert query_logger._DESLIGADO is True
