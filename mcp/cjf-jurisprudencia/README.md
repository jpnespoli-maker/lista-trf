# MCP Server: CJF Jurisprudência Unificada

Servidor MCP para acesso à jurisprudência unificada do Conselho da Justiça Federal.

## Funcionalidades

Busca decisões de todos os tribunais federais:
- STF (Supremo Tribunal Federal)
- STJ (Superior Tribunal de Justiça)
- TRF1, TRF2, TRF3, TRF4, TRF5, TRF6

## Tools Disponíveis

### `buscar_jurisprudencia_cjf`

Consulta o portal antigo e a API nova do CJF AO MESMO TEMPO (`shared/cjf_duas_rotas.py`)
e devolve uma lista unificada; cada resultado traz `<origem>` (`portal-antigo`,
`api-nova` ou `ambas`). Nenhuma rota substitui a outra (medido em 23/10/2026): a API
traz o CNJ (84 de 90 documentos) mas não cobre o STJ; o portal antigo cobre o STJ mas
quase nunca traz o CNJ (0 de 65). Rota caída vem declarada (`ROTA FORA`), e as duas
caídas viram `<erro>`. Cada busca grava uma linha em `~/.claude/DPU/logs/cjf_rotas.jsonl`
(contagens, tempos e erros, nunca texto), para decidir depois qual rota manter; o script
de comparação lê esse log.

```
Parâmetros:
- busca: operadores em MAIÚSCULO; palavras soltas são unidas por E
- tribunais: separados por vírgula (default: STF, STJ e TRF1 a TRF6)
- max_resultados: 1 a 100 (default: 10)
- max_tokens_ementa: 50 a 4000 (default: 600)
- modo: "comum" ou "semantica"
- data_inicio, data_fim: AAAA-MM-DD ou DD/MM/AAAA
- tipo_data: "decisao" ou "publicacao"
- relator, legislacao ("Lei 8.742/93", "LC 80"...)
- so_precedentes_relevantes, so_representativos
```

Os parâmetros a partir de `modo` só existem na API nova: usar qualquer um leva a busca
SÓ à API e exclui o STJ (declarado em `SEM COBERTURA`). Entrada que não se entende
(data, legislação, relator) volta como `<erro>`; a busca nunca roda sem o filtro.

### `documento_cjf`

Ementa, decisão ou inteiro teor de um `<id>` devolvido pela busca (`id`, `campo`,
`max_tokens`). Cache de 30 dias.

### `similares_cjf`

Julgados parecidos com um `<id>` (`id`, `max_resultados`, `max_tokens_ementa`). Cache de 1 dia.

### `estatisticas_cjf`

Total, julgamentos por ano, 10 relatores e 10 classes mais frequentes e tribunais de uma
busca, só na API nova (não cobre o STJ). Cache de 1 dia.

### `buscar_jurisprudencia_tnu` e `ajuda_sintaxe_cjf`

TNU (API nova, até 2026) e o guia de sintaxe, rotas e filtros.

## Metadados Extraídos

Para cada documento:

| Campo | Descrição |
|-------|-----------|
| numero | Número/classe do processo |
| classe | Tipo de processo (AC, REsp, etc.) |
| relator | Nome do relator |
| orgao_julgador | Turma/Câmara |
| data_julgamento | Data do julgamento |
| data_publicacao | Data da publicação |
| fonte_publicacao | Fonte (DJE, página) |
| ementa | **Texto completo da ementa** |
| decisao | Resultado (unânime, etc.) |

## Tecnologia

Duas rotas em paralelo:
- Portal antigo (JSF/PrimeFaces, `shared/cjf_client.py`): sessão HTTP, ViewState, AJAX e parse do HTML
- API nova (JSON, `shared/cjf_api.py`): busca, documento, similares, estatísticas e sugestão de relator
- `shared/cjf_duas_rotas.py` funde as listas (só par inequívoco) e grava o log de comparação

## Configuração

Registrado em `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "cjf-jurisprudencia": {
      "command": "python",
      "args": ["C:\\Users\\georg\\.claude\\mcp-servers\\cjf-jurisprudencia\\server.py"]
    }
  }
}
```

## Dependências

- Python 3.10+
- mcp (fastmcp)
- requests
- beautifulsoup4

## Exemplos de Uso

No Claude Code, após reiniciar:

```
"Busque jurisprudência sobre pensão por morte no TRF5"

"Gere relatório de jurisprudência do STJ sobre auxílio-doença"

"Quais tribunais estão disponíveis no CJF?"
```

## URL Fonte

`https://jurisprudencia.cjf.jus.br/unificada/`
