# Servidores MCP - Dependencias do Sistema

O sistema de analise automatizada (Claude Code) depende de 3 servidores MCP para pesquisa de jurisprudencia.

## Visao Geral

| MCP | Base de Dados | Conteudo |
|-----|---------------|----------|
| `bnp-api` | BNP/PAGEA (CNJ) | Temas vinculantes, Repercussao Geral, Repetitivos |
| `julia-trf5` | JULIA (TRF5) | Jurisprudencia 1o e 2o grau da 5a Regiao |
| `cjf-jurisprudencia` | CJF Unificada | STF, STJ, todos os TRFs — **so o tribunal** |
| `trf-jurisprudencia` | eProc TRF2/TRF4/TRF6 | Turmas Recursais dos JEFs e TRU (Turma Regional de Uniformizacao) — acervo que a base unificada do CJF nao indexa. TRF2 = RJ/ES |
| `trf1-jurisprudencia` | CJF Regional 1a Regiao | Fonte `TRF1` (tribunal, duplica a unificada) e fonte `JEF1` = **Turmas Recursais**, unico acervo de TR do projeto. So existe para a 1a Regiao (`/trf2`..`/trf6` respondem 404) |
| `eleitoral-jurisprudencia` | SJUR (TSE) | Jurisprudencia da Justica Eleitoral: TSE e os 27 TREs. Busca por Edge real via CDP (a API exige o hCaptcha invisivel do portal; ~11 s por busca); inteiro teor por HTTP. Unica base do projeto com TSE/TRE |
| `stm-jurisprudencia` | Consulta Judicial Unificada da JMU | Acordaos do Superior Tribunal Militar, com ementa integral e inteiro teor (sem login). HTTP simples, ~1-3 s por busca. Nao traz decisoes das Auditorias, sumulas do STM (o DSpace nao completa o TLS pelo cliente) nem IRDR (esta no `bnp-api`). O portal devolve a mesma pagina para zero e para consulta recusada: a busca pre-valida a sintaxe e, dando zero, roda consulta de controle. `robots.txt` veda robos: so consulta sob demanda, com cache |

## Instalacao

### 1. Copiar servidores para ~/.claude/mcp-servers/

```bash
# Criar diretorio
mkdir -p ~/.claude/mcp-servers/

# Copiar cada servidor
cp -r mcp/bnp-api ~/.claude/mcp-servers/
cp -r mcp/julia-trf5 ~/.claude/mcp-servers/
cp -r mcp/cjf-jurisprudencia ~/.claude/mcp-servers/
```

### 2. Instalar dependencias Python

```bash
pip install mcp requests beautifulsoup4
```

### 3. Configurar em ~/.claude/settings.json

Adicione ao arquivo `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "bnp-api": {
      "command": "python",
      "args": ["~/.claude/mcp-servers/bnp-api/server.py"]
    },
    "cjf-jurisprudencia": {
      "command": "python",
      "args": ["~/.claude/mcp-servers/cjf-jurisprudencia/server.py"]
    },
    "julia-trf5": {
      "command": "python",
      "args": ["~/.claude/mcp-servers/julia-trf5/server.py"]
    }
  }
}
```

### 4. Reiniciar Claude Code

```bash
claude --mcp-restart
# ou feche e abra novamente
```

## Configuracao Especifica

### JULIA (TRF5) - Credenciais

O JULIA requer credenciais de acesso. Crie o arquivo:

`~/.claude/mcp-servers/julia-trf5/credentials.json`

```json
{
  "usuario": "<base64_do_usuario>",
  "senha": "<base64_da_senha>",
  "orgao": "JFCE"
}
```

**Nota:** Credenciais devem ser obtidas junto ao TRF5.

---

## Referencia Rapida

### BNP-API

**Sintaxe de busca:**
```
+termo      # Palavra obrigatoria
-termo      # Palavra excluida
"frase"     # Expressao exata
```

**Tools:**
- `buscar_precedentes` - Busca estruturada
- `gerar_relatorio_precedentes` - Relatorio Markdown
- `listar_tipos_precedentes` - Lista tipos disponiveis

**Tipos de precedentes:**
| Codigo | Descricao |
|--------|-----------|
| RG | Repercussao Geral (STF) |
| RR | Recurso Repetitivo (STJ) |
| SV | Sumula Vinculante |
| SUM | Sumula |
| IRDR | Demandas Repetitivas |
| IAC | Assuncao de Competencia |

---

### JULIA-TRF5

**Sintaxe de busca (operadores em minusculo):**
```
termo e outro      # AND
termo ou outro     # OR
termo nao outro    # NOT
termo prox outro   # Proximidade (ate 5 palavras, mesma ordem)
termo adj outro    # Adjacencia (ate 5 palavras, qualquer ordem)
termo$             # Truncamento (aposentad$ = aposentadoria, aposentado)
```

**Tools:**
- `buscar_julia` - Busca generica com filtros
- `relatorio_segundo_grau` - Acordaos TRF5 com ementas
- `relatorio_primeiro_grau` - Sentencas por juiz/vara
- `listar_parametros_julia` - Lista parametros

**Filtros uteis:**
| Parametro | Exemplo |
|-----------|---------|
| orgao | TRF5, JFCE, JFPE |
| instancia | G1 (1o grau), G2 (2o grau) |
| orgao_julgador | 1a TURMA, PLENO |
| relator | Nome do desembargador |

---

### CJF-JURISPRUDENCIA

**Sintaxe de busca (operadores em MAIUSCULO):**
```
termo E outro         # AND
termo OU outro        # OR
termo NAO outro       # NOT
termo ADJ outro       # Adjacente na ordem
termo PROX3 outro     # Proximo ate 3 palavras
termo COM outro       # Mesma sentenca
termo MESMO outro     # Mesmo paragrafo
termo$                # Truncamento
```

**Busca por campo:**
```
termo[EMEN]           # Na ementa
termo[REL]            # No relator
termo[TRIB]           # No tribunal
termo[ORGA]           # No orgao julgador
termo[INDE]           # Na indexacao
2024$[DTDP]           # Data da decisao
```

**Tools:**
- `buscar_jurisprudencia_cjf` - Busca estruturada
- `gerar_relatorio_cjf` - Relatorio com ementas
- `listar_tribunais_cjf` - Lista tribunais

**Tribunais:**
STF, STJ, TRF1, TRF2, TRF3, TRF4, TRF5, TRF6

---

## Testes

A partir da raiz do repositorio, com o **Python do sistema** (o `.venv` do repo nao tem `mcp` nem `curl_cffi`, e a coleta falha nos servidores):

```bash
python -m pytest tests -q -m "not rede" -p no:cacheprovider   # suite offline, segundos
python -m pytest mcp/shared -q -p no:cacheprovider             # modulos compartilhados (cache, CJF, TLS)
python -m pytest tests -q -m rede -p no:cacheprovider          # ao vivo: vai aos portais
```

- **`rede` fica fora da corrida padrao.** Ele vai aos portais pela rede, e os testes do `eleitoral-jurisprudencia` **abrem uma janela do Edge** (busca do SJUR por CDP). Vermelho nele e primeiro hipotese de terceiro (captcha, portal fora), nao de regressao: ler a mensagem do `<erro>` antes de concluir.
- **Os testes nao gravam no log de producao das queries.** O `tests/conftest.py` liga `DPU_QUERY_LOG_DESLIGADO=1`; sem ele, cada corrida gravava buscas de teste em `~/.claude/DPU/logs/jurisprudencia_queries.jsonl`, o log que mede o uso real das bases. Trava: `tests/test_isolamento_log_de_queries.py`.

---

## Fontes

| MCP | URL |
|-----|-----|
| BNP | https://pangeabnp.pdpj.jus.br/ |
| JULIA | https://julia.trf5.jus.br/julia/ |
| CJF | https://jurisprudencia.cjf.jus.br/unificada/ |
