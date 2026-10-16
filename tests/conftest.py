"""Configuração comum da suíte.

`DPU_QUERY_LOG_DESLIGADO=1` antes de qualquer import: o `query_logger` lê a
variável no import e, ligado, cada teste que chama uma tool gravaria no log de
produção das queries (`~/.claude/DPU/logs/jurisprudencia_queries.jsonl`).
Trava: tests/test_isolamento_log_de_queries.py.
"""

import os

os.environ["DPU_QUERY_LOG_DESLIGADO"] = "1"
