# CJF: as duas rotas sempre, e os recursos da API nova

Data: 23/10/2026. MCP: `mcp/cjf-jurisprudencia`. Módulos: `mcp/shared/cjf_api.py`, `mcp/shared/cjf_client.py`.

## Intenção

Em 22/10/2026 o `jurisprudencia.cjf.jus.br` virou SPA (Angular) com uma API JSON
pública, e o portal JSF passou a `antiga-jurisprudencia.cjf.jus.br`, que o próprio
CJF chama de "versão antiga" e que pode ser desligado a qualquer momento. Hoje o
MCP usa o portal antigo como rota principal e a API só como reserva.

O Defensor decidiu (23/10/2026):

1. **As duas rotas rodam SEMPRE** na busca comum, e o uso real decide, com o tempo,
   qual funciona melhor. A decisão de abandonar uma delas é dele, tomada sobre um
   relatório, não agora.
2. **O resultado é uma LISTA UNIFICADA**, sem duplicatas, com a origem de cada
   julgado marcada (só portal antigo, só API nova, ou as duas).
3. **Todos os recursos novos da API** entram no MCP: busca semântica, filtros
   (período, relator, legislação, marcações), documento por id, similares e
   estatísticas.
4. **No mesmo MCP**, sem tool renomeada: o nome `cjf-jurisprudencia` está nos
   `.mcp.json`, em `skills/_shared/subagent_mcp_map.json` e nas regras PESQ-02c e
   MCP-02 do `.claude`.

## O que já foi medido (23/10/2026) e que o desenho respeita

Medido pelas funções de produção (`_buscar_documentos_jsf`, `buscar_unificada`) sobre
três buscas (`bolsa E familia E unipessoal` TRF1/3/5; `medicamento E oncologico E uniao`
TRF2/4; `repercussao ADJ geral E tema E 1234` STF/TRF1), 30 documentos por rota:

| Medida | Portal antigo | API nova |
|---|---|---|
| Documentos com número CNJ | 0 de 65 | 84 de 90 |
| TRF2, "medicamento oncologico uniao" | 0 | 84 |
| TRF1, "repercussao geral tema 1234" | 66 | 610 |
| Traz tribunal no documento | não | sim |
| Traz id (para documento/similares) | não | sim |

- **Deduplicação:** o mesmo julgado nas duas rotas casa pelo começo da ementa
  normalizada (sem acento, minúsculas, só `[a-z0-9]`, sem o rótulo "EMENTA"
  inicial, primeiros 80 caracteres): 5 de 5 no caso do TRF4. Número não serve (o
  antigo traz a classe no campo número); data não serve como chave adicional
  (diverge entre as rotas).
- **Endpoints da API** (todos sem token, base `https://jurisprudencia.cjf.jus.br/api/jurisprudencia`):
  - `POST /pesquisar` e `POST /pesquisa-semantica`: mesmo corpo. A semântica
    devolve no máximo 400 (`total.relation: eq`), em ~7 s; a comum, ~1-2 s.
  - Corpo: `query`, `filters`, `sort`, `page_size`, `current_page`,
    `process_number`, `decision_date_start`, `decision_date_end`, `date_type`
    (`DATA_DECISAO` ou `DATA_PUBLICACAO_PESQ`), `rapporteurs`, `revisors`,
    `relator_convocado`, `relator_acordaos`, `ref_legislativa`.
  - `GET /documento/{id}` e `GET /documento/{id}/campo/{campo}` (`ementa`,
    `decisao`, `inteiro_teor`; devolve `{"campo", "texto"}`). O `INTEIRO_TEOR` às
    vezes é um link para o tribunal de origem, e às vezes vem vazio.
  - `GET /similar/{id}?size=N`: ~2 s, devolve `results.hits`.
  - `GET /sugestoes/{campo}?q=`: campos `relator`, `revisor`, `relator_acordao`
    (os demais dão 404).
  - `POST /analytics` (mesmo corpo, com `search_mode`, `page_size: 0`): devolve
    `total_documents`, `julgamentos_por_ano`, `top_relatores`,
    `top_relatores_acordao`, `top_classes`, `top_tribunais`,
    `relatores_por_tribunal`.
- **Filtros medidos:**
  - período: `decision_date_start/end` no formato `AAAA-MM-DD` (99 para 12 num ano);
  - relator: exige o nome EXATO como o site o guarda ("PARCA" 0; "PAULO ROBERTO
    PARCA DE PINHO" 3); a sugestão resolve o nome;
  - legislação: `LEI-10836` (22) e `LEG-FED LEI-008742` (75) funcionam; o campo
    no documento vem como `LEG-FED   SUMSTF-000279`, com zeros à esquerda. A Lei
    14.601 dá zero em qualquer formato (provavelmente não indexada ainda);
  - marcações: `filters: {"PRECEDENTE_RELEVANTE": ["1"]}` (57) e
    `{"REPRESENTATIVO_CONTROVERSIA": ["1"]}` (3).
- O que a correção de hoje (commit `15d028b`) já garante e não muda: o E explícito
  em palavras soltas, as turmas recursais pelo filtro `ORIGEM`, os totais somando
  `tr_by_regiao`/`tru_by_regiao`, o STJ recusado com `SemCoberturaNaApi`.

## Objetivos

1. **Busca comum com as duas rotas em paralelo.** `buscar_jurisprudencia_cjf` sem
   filtro novo consulta portal antigo e API ao mesmo tempo (threads); a espera é a
   da rota mais lenta, não a soma. Uma rota que falha não derruba a outra: o
   retorno diz qual caiu e por quê.
2. **Lista unificada.** Intercalar pela posição em cada rota (1º da API, 1º do
   antigo, 2º da API...), removendo duplicatas pela chave de ementa. O documento
   que está nas duas rotas sai uma vez, com os campos da API (que trazem CNJ e
   id) e `origem="ambas"`. Cada julgado leva `<origem>` (`portal-antigo`,
   `api-nova`, `ambas`). O cabeçalho traz os totais por tribunal das DUAS rotas.
   O corte final respeita `max_resultados`.
3. **Log de comparação.** Cada busca comum grava uma linha em
   `~/.claude/DPU/logs/cjf_rotas.jsonl`, por rota: ok/erro (com a mensagem), ms,
   totais por tribunal, nº de documentos, quantos com CNJ, com data, com órgão;
   e a sobreposição (quantos documentos casaram). Não grava texto de ementa.
   Desligável pela mesma variável dos testes (`DPU_QUERY_LOG_DESLIGADO=1`), para
   a suíte não poluir o log de produção.
4. **Relatório de comparação.** Script (`mcp/shared/comparar_rotas_cjf.py` ou
   equivalente) que lê o log e imprime, por rota: disponibilidade, mediana e p90
   do tempo, cobertura por tribunal (soma de totais), taxa de CNJ, taxa de
   sobreposição, e nº de buscas em que só uma rota achou algo. Com `--desde`.
5. **Filtros novos em `buscar_jurisprudencia_cjf`**, todos opcionais:
   `modo` (`"comum"` padrão, `"semantica"`), `data_inicio`, `data_fim`
   (`AAAA-MM-DD` ou `DD/MM/AAAA`), `tipo_data` (`decisao` padrão, `publicacao`),
   `relator` (texto livre, resolvido pela sugestão), `legislacao` (texto como
   "Lei 8.742/93", normalizado ao formato que a API aceita), `so_precedentes_relevantes`,
   `so_representativos`. Usado qualquer um deles, a busca vai SÓ à API (o portal
   antigo não os tem), e o retorno avisa isso; não entra no log de comparação.
6. **Resultado mais rico** para documento da API: `<id>`, número CNJ, tribunal,
   link do inteiro teor, `precedente_relevante`, `representativo_controversia`.
7. **Tool `documento_cjf(id, campo="")`**: o julgado inteiro (sem campo) ou um
   campo (`ementa`, `decisao`, `inteiro_teor`), SEM truncar por padrão
   (`max_tokens` opcional).
8. **Tool `similares_cjf(id, max_resultados=10)`**: os julgados mais parecidos,
   no mesmo formato da busca.
9. **Tool `estatisticas_cjf(busca, tribunais, modo, filtros...)`**: os agregados
   do `/analytics`, legíveis.
10. Atualizar `ajuda_sintaxe_cjf`, o docstring da tool, o `mcp/cjf-jurisprudencia/README.md`
    e a linha do `cjf-jurisprudencia` em `~/.claude/DPU/ferramentas-mcp.md`, que hoje
    diz "não alcança Turma Recursal" e passa a ser falsa (cartão INFRA-03: a prosa
    que descreve script se confere no script).

## Não objetivos

- **Escolher a rota vencedora agora.** O log existe para isso; a decisão é do Defensor.
- **Comparar a TNU**: o portal antigo termina em 2017. `buscar_jurisprudencia_tnu`
  continua só na API (pode ganhar os filtros novos se sair barato; não é exigido).
- **Comparar o STJ**: a API quase não o tem. Pedido o STJ, ele sai só do portal
  antigo e entra no log como cobertura ausente da API, sem mais análise.
- **O `trf1-jurisprudencia`** (que também tem as duas rotas): fica para depois, se
  o relatório daqui justificar.
- **O chat do portal**: pelo código do site, parece exigir login; não foi sondado.

## Anti-objetivos (falharia mesmo funcionando)

- Zero de uma rota tratado como "nada encontrado" quando a outra achou.
- Falha de uma rota devolvida como resultado vazio, sem dizer qual caiu.
- Log de comparação que grava ementa, nome de parte ou qualquer conteúdo, ou que
  a suíte de testes polui.
- Cache de 7 dias guardando resultado em que uma rota falhou (gruda o defeito,
  como o falso zero de 31/08/2026). Regra: só se cacheia resposta com as duas
  rotas ok e pelo menos um documento.
- Tool nova sem controle positivo ao vivo.

## Restrições

- Python do sistema roda a suíte (`python -m pytest mcp/shared -q -p no:cacheprovider`
  e `python -m pytest tests -q -m "not rede" -p no:cacheprovider`); o `.venv` do
  repo não tem `mcp`. O servidor roda pelo venv `~/.claude/mcp-venvs/cjf-jurisprudencia`.
- Testes primeiro; fixtures capturadas pelas funções de produção e recortadas
  (sem inteiro teor, decisão a 300 caracteres), como as existentes; provas por
  mutação nas regras centrais (deduplicação, cache, rota que falha).
- Controle ao vivo de cada tool nova pela função registrada no servidor, não pela
  camada de baixo.
- Um commit por assunto; push ao fechar.

## Decisões tomadas pelo autor do desenho (não pelo Defensor), após a revisão

1. **Deduplicação só ENTRE as rotas, nunca dentro de uma.** Cada rota já devolve
   documentos distintos; fundir dentro dela sumiria precedente. Entre rotas, a
   fusão é por pares (um documento do antigo casa com no máximo um da API). A
   chave é a ementa normalizada com **200 caracteres**, não 80, porque nos TRFs
   os primeiros 80 costumam ser só a cadeia de rubricas ("PREVIDENCIÁRIO.
   BENEFÍCIO ASSISTENCIAL. LOAS..."); 200 casou os mesmos 5 de 5 do TRF4. Antes
   de fixar, MEDIR a colisão: dentro de uma mesma rota, quantos pares distintos
   dividem a chave de 200 em amostras reais; havendo colisão, acrescentar o
   relator normalizado à chave. Ementa vazia nunca casa.
2. **Cache.** O STJ sem cobertura na API é AUSÊNCIA CONHECIDA, não falha: não
   impede o cache. Impede o cache a rota que levantou erro (rede, contrato,
   canário). Buscas só pela API (filtros, semântica) cacheiam com a mesma regra
   (só com documento), chave `v: 3` incluindo modo e filtros.
   `documento_cjf` cacheia 30 dias (o julgado não muda); `similares_cjf` e
   `estatisticas_cjf`, 1 dia.
3. **Relaxamento para OU só quando a lista UNIFICADA fica vazia**, e então nas
   duas rotas. Zero de uma rota com a outra achando é dado de comparação e vai ao
   log como está. O log registra a busca estrita; se houve relaxamento, uma
   segunda linha com `relaxada: true`.
4. **Documento nas duas rotas: API com prioridade, completada campo a campo pelo
   portal antigo** onde a API não traz valor (data e órgão do STF). O aviso do STF
   sai só se, após completar, ainda faltar data ou órgão.

## Deixado ao construtor

- Estrutura interna (módulo novo para a fusão das rotas ou dentro de `cjf_client`).
- Número de threads, timeouts por rota (o atual é 60 s na API).
- Normalização exata de `legislacao` (medir `LEI-8742` × `LEI-008742` antes de fixar).
- O que fazer com várias sugestões de relator (passar todas na lista `rapporteurs`
  se a API as somar; medir).
- Formato do relatório do script.
