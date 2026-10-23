# CJF: duas rotas sempre e recursos da API nova — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A busca comum do `cjf-jurisprudencia` consulta portal antigo e API nova em paralelo, devolve lista unificada com a origem de cada julgado e grava log de comparação; a API ganha filtros, documento, similares e estatísticas.

**Architecture:** `shared/cjf_api.py` ganha os recursos da API (filtros, documento, similares, estatísticas, sugestão de relator). Módulo novo `shared/cjf_duas_rotas.py` roda as duas rotas em threads, funde por chave de ementa e grava o log. O `server.py` liga tudo e expõe três tools novas. Script `shared/comparar_rotas_cjf.py` resume o log.

**Tech Stack:** Python 3.10+, `requests`, `concurrent.futures`, FastMCP, pytest (Python do sistema).

**Spec:** `docs/superpowers/specs/2026-10-23-cjf-duas-rotas-e-api-nova-design.md`

## Global Constraints

- Suíte: `python -m pytest mcp/shared -q -p no:cacheprovider` e `python -m pytest tests -q -m "not rede" -p no:cacheprovider`, com o Python do SISTEMA, a partir da raiz do repo (`C:/Users/jpnes/.claude/mcp-servers/lista-trf`). Linha de base: 190 e 688 verdes.
- Fixtures capturadas pelas funções de PRODUÇÃO e recortadas (sem `INTEIRO_TEOR`, `DECISAO` a 300 caracteres, poucos documentos), gravadas em `mcp/shared/fixtures/cjf_api_<assunto>.json`. Jurisprudência pública.
- Dublê de `pesquisar` aceita `**kwargs` quando o código chamar com keyword.
- Nenhum teste grava em `~/.claude/DPU/logs/`: o caminho do log é atributo de módulo e os testes o apontam a `tmp_path`.
- Log de comparação não grava ementa, nome de parte nem qualquer texto de documento.
- Zero de uma rota nunca vira "nada encontrado" quando a outra achou; rota que falhou é declarada no retorno.
- Cache só com documento E sem rota em erro (STJ sem cobertura na API não é erro). Chave `v: 3`.
- Nomes de tool existentes não mudam (`buscar_jurisprudencia_cjf`, `buscar_jurisprudencia_tnu`, `ajuda_sintaxe_cjf`).
- Um commit por task, mensagem `tipo(cjf): porquê`, terminada por `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`; commit por pathspec.

## Review Focus

1. Portal antigo lento ou travado: a resposta sai com os documentos da API e o aviso de que o antigo caiu, dentro do teto (Task 3, `test_rota_antiga_lenta_nao_segura_a_api`).
2. As duas rotas falham: `<erro>` nomeando as DUAS causas, nunca lista vazia (Task 4, `test_duas_rotas_falham_erro_declarado`).
3. Busca só com STJ: vai só ao portal antigo, sem erro de API no retorno; o log marca a API como `sem_cobertura` (Task 3, `test_so_stj_nao_chama_api`).
4. Ementa vazia ou curta nas duas rotas: os documentos nunca se fundem (Task 3, `test_ementa_vazia_nunca_funde`).
5. `documento_cjf` com id inexistente: erro declarado com o status, não texto vazio (Task 2, `test_documento_inexistente_levanta`).

---

### Task 1: API — filtros novos, busca filtrada e documento enriquecido

**Files:**
- Modify: `mcp/shared/cjf_api.py` (`documento`, `pesquisar`, `_docs_com_canario`; novo `FiltrosApi`, `buscar_api`)
- Create: `mcp/shared/fixtures/cjf_api_semantica.json`, `mcp/shared/fixtures/cjf_api_filtrada.json`
- Test: `mcp/shared/test_cjf_api_recursos.py` (novo)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class FiltrosApi` com `modo: str = "comum"` (`"comum"|"semantica"`), `data_inicio: str | None = None`, `data_fim: str | None = None` (ambas `AAAA-MM-DD`), `tipo_data: str = "DATA_DECISAO"` (ou `"DATA_PUBLICACAO_PESQ"`), `relatores: tuple[str, ...] = ()`, `legislacao: str | None = None` (já no formato da API), `so_precedentes_relevantes: bool = False`, `so_representativos: bool = False`, e `def ativo(self) -> bool` (True se qualquer campo difere do padrão).
  - `pesquisar(query, filtros, n, *, ordenar_por_data=False, processo=None, extras: FiltrosApi | None = None) -> dict`: com `extras`, rota `pesquisa-semantica` quando `modo == "semantica"`; preenche `decision_date_start/end`, `date_type`, `rapporteurs`, `ref_legislativa`; acrescenta a `filters` `{"PRECEDENTE_RELEVANTE": ["1"]}` e/ou `{"REPRESENTATIVO_CONTROVERSIA": ["1"]}`.
  - `buscar_api(termo: str, tribunais: Sequence[str], max_resultados: int, extras: FiltrosApi) -> tuple[list[dict], dict[str, int]]`: o mesmo filtro TRIBUNAL+ORIGEM e totais de `buscar_unificada`; tribunal sem cobertura levanta `SemCoberturaNaApi` como lá. Reaproveitar `buscar_unificada` passando `extras` (não duplicar a montagem de filtro/totais).
  - `documento(source, doc_id: str = "") -> dict` passa a incluir, quando houver valor: `"id"`, `"link_inteiro_teor"` (`LINK_INTEIRO_TEOR`, ou `INTEIRO_TEOR` se começar por `http`), `"precedente_relevante": True` (se `PRECEDENTE_RELEVANTE` == 1), `"representativo_controversia": True` (idem).

- [ ] **Step 1: Capturar fixtures pela produção.** Script no scratchpad que importa `shared.cjf_api` e grava: (a) `pesquisar("pessoa que mora sozinha teve o bolsa familia cancelado", {}, 3, extras=FiltrosApi(modo="semantica"))` → `cjf_api_semantica.json`; (b) `pesquisar("bolsa E familia", {"TRIBUNAL": ["TRF4"], "ORIGEM": ["JEF:TR:4", "JEF:TRU:4"]}, 3, extras=FiltrosApi(data_inicio="2025-01-01", data_fim="2025-12-31", so_precedentes_relevantes=True))` → `cjf_api_filtrada.json`, garantindo ao menos um hit com `PRECEDENTE_RELEVANTE` 1 (se zero, trocar o período até haver). Como `extras` ainda não existe, capturar com `requests.post` montando o corpo à mão a partir do corpo de `pesquisar`, e reconferir depois do Step 3 que a produção monta o mesmo corpo (teste do Step 2c). Recortar como as fixtures existentes, preservando `_id`.

- [ ] **Step 2: Testes que falham** em `test_cjf_api_recursos.py`:
  - a) `test_documento_traz_id_link_e_marcacoes`: `documento(hit["_source"], hit["_id"])` de `cjf_api_filtrada.json` → `d["id"] == hit["_id"]`, `d["precedente_relevante"] is True`; doc com `LINK_INTEIRO_TEOR` vazio e `INTEIRO_TEOR` não-URL → sem chave `link_inteiro_teor`.
  - b) `test_semantica_vai_a_rota_semantica`: dublê de `requests.post` captura a URL; `pesquisar("x y", {}, 3, extras=FiltrosApi(modo="semantica"))` → URL termina em `/pesquisa-semantica`.
  - c) `test_filtros_viram_corpo`: dublê de `requests.post` captura o JSON; `FiltrosApi(data_inicio="2025-01-01", data_fim="2025-12-31", tipo_data="DATA_PUBLICACAO_PESQ", relatores=("PAULO ROBERTO PARCA DE PINHO",), legislacao="LEI-10836", so_precedentes_relevantes=True)` → `decision_date_start == "2025-01-01"`, `date_type == "DATA_PUBLICACAO_PESQ"`, `rapporteurs == ["PAULO ROBERTO PARCA DE PINHO"]`, `ref_legislativa == "LEI-10836"`, `filters["PRECEDENTE_RELEVANTE"] == ["1"]`.
  - d) `test_sem_extras_corpo_igual_ao_de_hoje`: sem `extras`, URL `/pesquisar`, `decision_date_start is None`, `rapporteurs == []`.
  - e) `test_filtros_api_ativo`: `FiltrosApi().ativo() is False`; `FiltrosApi(so_representativos=True).ativo() is True`.
  - f) `test_buscar_api_reusa_tribunal_e_origem`: dublê de `pesquisar` com `**kwargs` captura `filtros` e `extras`; `buscar_api("x", ["TRF4"], 3, FiltrosApi(modo="semantica"))` → `filtros == {"TRIBUNAL": ["TRF4"], "ORIGEM": ["JEF:TR:4", "JEF:TRU:4"]}` e `extras.modo == "semantica"`.

- [ ] **Step 3: Rodar** `python -m pytest mcp/shared/test_cjf_api_recursos.py -q -p no:cacheprovider` → FAIL (`FiltrosApi` inexistente).

- [ ] **Step 4: Implementar** as interfaces acima em `cjf_api.py`. `buscar_unificada` ganha `extras: FiltrosApi | None = None` keyword-only e o repassa; `_docs_com_canario` passa `h.get("_id", "")` a `documento`.

- [ ] **Step 5: Rodar** o arquivo novo e `mcp/shared/test_cjf_api_reserva.py` e `test_cjf_tnu.py` → PASS.

- [ ] **Step 6: Commit** `feat(cjf): filtros da API nova e documento com id, link e marcacoes`.

---

### Task 2: API — documento, similares, estatísticas, sugestão de relator e legislação

**Files:**
- Modify: `mcp/shared/cjf_api.py`
- Create: `mcp/shared/fixtures/cjf_api_documento.json`, `cjf_api_similares.json`, `cjf_api_analytics.json`
- Test: `mcp/shared/test_cjf_api_recursos.py`

**Interfaces:**
- Consumes: `FiltrosApi`, `documento` (Task 1).
- Produces:
  - `obter_documento(doc_id: str, campo: str = "") -> dict`: sem campo, `GET /documento/{id}` e devolve `documento(...)` do primeiro hit acrescido de `"inteiro_teor"` (texto do `_source`, se houver); com campo em `{"ementa","decisao","inteiro_teor"}`, `GET /documento/{id}/campo/{campo}` e devolve `{"id", "campo", "texto"}`. Campo fora do conjunto → `ValueError`. HTTP ≠ 200 ou nenhum hit → `RuntimeError` com o status.
  - `similares(doc_id: str, n: int) -> list[dict]`: `GET /similar/{id}?size=n`, documentos via `documento(source, _id)`.
  - `estatisticas(termo: str, tribunais: Sequence[str], extras: FiltrosApi) -> dict`: `POST /analytics` com o corpo de `pesquisar` mais `search_mode` (`"comum"`/`"semantica"`), `page_size: 0`, e os mesmos filtros TRIBUNAL+ORIGEM de `buscar_unificada`; devolve o JSON da API com as chaves medidas (`total_documents`, `julgamentos_por_ano`, `top_relatores`, `top_relatores_acordao`, `top_classes`, `top_tribunais`).
  - `sugerir_relator(texto: str) -> list[str]`: `GET /sugestoes/relator?q=`.
  - `normalizar_legislacao(texto: str) -> str`: "Lei 8.742/93", "lei 8742", "LEI-8742" → formato que a medição do Step 1 fixar.

- [ ] **Step 1: Medir ao vivo (scratchpad, pela API)** e anotar no docstring de `normalizar_legislacao`: total de `ref_legislativa` para `LEI-8742`, `LEI-008742`, `LEG-FED LEI-008742`, `LEI-10836`, `LEI-010836`. Fixar o formato que devolve o maior total consistente; se `LEI-8742` e `LEI-008742` derem o mesmo, usar sem zeros. Medir também se `rapporteurs` com DUAS grafias (`["LUIZ FUX", "Ministro Luiz Fux"]`) soma (OU) ou cruza (E) os totais de cada uma sozinha — anotar no docstring de `sugerir_relator`.

- [ ] **Step 2: Capturar fixtures** pela produção (`requests.get`/`post` com o corpo de `pesquisar`): documento do PUIL da TNU `0044230-77.2023.4.05.8300` (achar o id por `buscar_tnu`) → `cjf_api_documento.json`; similares desse id, `size=3` → `cjf_api_similares.json`; analytics de `"bolsa E familia E unipessoal"` → `cjf_api_analytics.json`.

- [ ] **Step 3: Testes que falham:**
  - `test_obter_documento_inteiro`: dublê de `requests.get` com a fixture → `d["id"] == <id>`, `d["numero"] == "0044230-77.2023.4.05.8300"`.
  - `test_obter_documento_campo`: resposta `{"campo": "EMENTA", "texto": "abc"}` → `{"id": <id>, "campo": "ementa", "texto": "abc"}`.
  - `test_campo_invalido`: `obter_documento("x", "relator")` → `ValueError`.
  - `test_documento_inexistente_levanta`: status 404 → `RuntimeError` contendo `"404"`.
  - `test_similares_mapeia_documentos`: fixture → `len == 3`, todos com `"id"` e `via == "api-nova"`.
  - `test_estatisticas_corpo_e_retorno`: dublê captura o corpo → `page_size == 0`, `search_mode == "comum"`, `filters["ORIGEM"]` presente para TRF; retorno tem `total_documents`.
  - `test_normalizar_legislacao` parametrizado: `"Lei 8.742/93"`, `"lei 8742"`, `"LEI-8742"` → o formato do Step 1; `"Lei 10.836/2004"` → idem para 10836.

- [ ] **Step 4: Rodar** → FAIL. **Step 5: Implementar.** **Step 6: Rodar** → PASS.

- [ ] **Step 7: Commit** `feat(cjf): documento, similares, estatisticas e sugestao de relator pela API nova`.

---

### Task 3: Duas rotas em paralelo, fusão e log de comparação

**Files:**
- Create: `mcp/shared/cjf_duas_rotas.py`
- Test: `mcp/shared/test_cjf_duas_rotas.py`
- Fixture: reaproveitar `cjf_api_trf3_trf4_com_tr.json`; para o portal antigo, capturar `_buscar_documentos_jsf("medicamento E oncologico E uniao", ["TRF4"], 10)` e `buscar_unificada` da mesma busca → `cjf_rotas_trf4_jsf.json` e `cjf_rotas_trf4_api.json` (listas de documentos já parseados, ementa inteira — necessária para a chave).

**Interfaces:**
- Consumes: `cjf_client._buscar_documentos_jsf(termo, tribunais, n) -> (docs, totais)`; `cjf_api.buscar_unificada(termo, tribunais, n) -> (docs, totais)`; `cjf_api.SemCoberturaNaApi`, `cjf_api.TRIBUNAIS_COBERTOS`.
- Produces:
  - `LOG_ROTAS: Path = Path.home() / ".claude" / "DPU" / "logs" / "cjf_rotas.jsonl"` (atributo de módulo; desligado por `DPU_QUERY_LOG_DESLIGADO=1`).
  - `chave_ementa(doc: dict) -> str | None`: ementa sem acento, minúscula, só `[a-z0-9]`, sem o rótulo "ementa" inicial (inclusive espaçado, "E M E N T A"), primeiros 200 caracteres; `None` se tiver menos de 200 caracteres normalizados.
  - `@dataclass class ResultadoRota`: `ok: bool`, `docs: list[dict]`, `totais: dict[str, int]`, `ms: int`, `erro: str | None`, `sem_cobertura: list[str]`.
  - `@dataclass class ResultadoDuasRotas`: `docs: list[dict]` (fundidos, cada um com `"origem"` ∈ `{"portal-antigo","api-nova","ambas"}`), `antiga: ResultadoRota`, `api: ResultadoRota`, `sobreposicao: int`.
  - `fundir(docs_antiga: list[dict], docs_api: list[dict], n: int) -> tuple[list[dict], int]`: intercala por posição começando pela API; par casado sai uma vez, na posição do primeiro a aparecer, com os campos da API completados campo a campo pelos do antigo onde a API não tem valor, e `origem="ambas"`; remove `"aviso"` do STF se, completado, tiver `data_julgamento` e `orgao_julgador`. Casamento só entre rotas, por pares (cada documento casa no máximo uma vez). Devolve `(lista[:n], nº de pares)`.
  - `buscar_duas_rotas(termo: str, tribunais: list[str], n: int, *, relaxada: bool = False, teto_s: float = 90.0) -> ResultadoDuasRotas`: as duas rotas em `ThreadPoolExecutor(max_workers=2)`; a API recebe só os tribunais de `TRIBUNAIS_COBERTOS` (os demais vão a `sem_cobertura`; sem nenhum coberto, a API nem é chamada e fica `ok=True`, docs vazios); exceção de rota vira `ok=False` com `erro=f"{type(e).__name__}: {e}"`; rota que não termina em `teto_s` vira `ok=False, erro="timeout"`, sem esperar por ela. Grava uma linha no log (função privada `_registrar`).
  - Linha do log (JSON, uma por chamada): `{"ts", "busca", "tribunais", "relaxada", "antiga": {"ok","erro","ms","totais","n","com_cnj","com_data","com_orgao"}, "api": {... + "sem_cobertura"}, "sobreposicao"}`. `com_cnj` conta `numero` que casa `[0-9]{7}-[0-9]{2}\.[0-9]{4}\.`.

- [ ] **Step 1: Medir a colisão da chave** (scratchpad, pela produção): para 5 buscas variadas (previdenciário, saúde, bancário, servidor, tributário), 30 documentos por rota, contar pares DISTINTOS dentro da mesma rota com a mesma `chave_ementa` de 200. Se houver algum, a chave passa a ser `(chave200, relator normalizado)` e o teste `test_mesma_rubrica_relatores_distintos_nao_funde` entra no Step 2; registrar o número medido no docstring de `chave_ementa`.

- [ ] **Step 2: Testes que falham** em `test_cjf_duas_rotas.py` (log apontado a `tmp_path` por `monkeypatch.setattr(cjf_duas_rotas, "LOG_ROTAS", tmp_path / "l.jsonl")` e `monkeypatch.delenv("DPU_QUERY_LOG_DESLIGADO", raising=False)`):
  - `test_fundir_casa_entre_rotas`: fixtures TRF4 → os documentos casados saem com `origem == "ambas"` e `numero` da API; nº de pares == o medido na captura.
  - `test_fundir_intercala_comecando_pela_api`: listas sintéticas sem casamento `[a1,a2]`, `[j1,j2]` → ordem `a1, j1, a2, j2`.
  - `test_ementa_vazia_nunca_funde`: um doc de cada rota com ementa `""` → dois documentos na saída, `sobreposicao == 0`.
  - `test_ambas_completa_campos_do_stf`: doc API do STF sem data/órgão + doc antigo com a mesma ementa e data/órgão → saída com `data_julgamento` e `orgao_julgador` do antigo e sem `"aviso"`.
  - `test_uma_rota_falha_a_outra_responde`: dublê do antigo levanta `RuntimeError("canario")` → `docs` da API, `antiga.ok is False`, `"canario" in antiga.erro`.
  - `test_rota_antiga_lenta_nao_segura_a_api`: dublê do antigo dorme 2 s, `teto_s=0.5` → retorna em < 1,5 s com `antiga.erro == "timeout"` e docs da API.
  - `test_so_stj_nao_chama_api`: dublê da API falha o teste se chamado; `buscar_duas_rotas("x", ["STJ"], 5)` → `api.sem_cobertura == ["STJ"]`, `api.ok is True`.
  - `test_stj_vai_so_ao_antigo_e_os_demais_as_duas`: tribunais `["STJ","TRF4"]` → a API recebe `["TRF4"]`.
  - `test_log_sem_texto_de_documento`: após uma chamada, a linha do log tem as chaves acima e NENHUMA string do log contém trecho de 30 caracteres de qualquer ementa da fixture.
  - `test_log_desligado_pela_variavel`: com `DPU_QUERY_LOG_DESLIGADO=1`, o arquivo não é criado.

- [ ] **Step 3: Rodar** → FAIL. **Step 4: Implementar.** **Step 5: Rodar** → PASS.

- [ ] **Step 6: Mutação.** Uma por vez, e reverter cada uma: (a) remover o `None` da chave curta → `test_ementa_vazia_nunca_funde` FAIL; (b) esperar a rota lenta (`.result()` sem timeout) → `test_rota_antiga_lenta_nao_segura_a_api` FAIL; (c) fundir também dentro da mesma rota → `test_ementa_vazia_nunca_funde` ou o de colisão FAIL.

- [ ] **Step 7: Commit** `feat(cjf): portal antigo e API nova em paralelo, lista unificada e log de comparacao`.

---

### Task 4: Servidor — busca com as duas rotas e as três tools novas

**Files:**
- Modify: `mcp/cjf-jurisprudencia/server.py`
- Modify: `mcp/cjf-jurisprudencia/README.md`
- Test: `mcp/shared/test_cjf_servidor_rotas.py` (novo; carrega o `server.py` como a fixture `srv_cjf` de `test_cjf_api_reserva.py`)

**Interfaces:**
- Consumes: `cjf_duas_rotas.buscar_duas_rotas`, `ResultadoDuasRotas`; `cjf_api.FiltrosApi`, `buscar_api`, `obter_documento`, `similares`, `estatisticas`, `sugerir_relator`, `normalizar_legislacao`.
- Produces (tools MCP):
  - `buscar_jurisprudencia_cjf(busca, tribunais="STF,STJ,TRF1,TRF2,TRF3,TRF4,TRF5,TRF6", max_resultados=10, max_tokens_ementa=600, modo="comum", data_inicio="", data_fim="", tipo_data="decisao", relator="", legislacao="", so_precedentes_relevantes=False, so_representativos=False) -> str`.
    - Sem filtro novo: `buscar_duas_rotas`. Lista vazia e `relaxar_cjf(busca)` não-nulo → `buscar_duas_rotas(relaxada, ..., relaxada=True)` e o aviso `BUSCA RELAXADA` de hoje. As duas rotas com `ok=False` → `<erro>Falha na busca CJF: portal antigo: <erro>; API nova: <erro></erro>`.
    - Com filtro novo: só `buscar_api`; datas aceitas `AAAA-MM-DD` ou `DD/MM/AAAA`; `tipo_data` `"decisao"|"publicacao"` → `DATA_DECISAO|DATA_PUBLICACAO_PESQ`; `relator` resolvido por `sugerir_relator` (lista vazia → `<erro>` "relator não encontrado: <texto>"); `legislacao` por `normalizar_legislacao`; STJ pedido → comentário `SEM COBERTURA` como hoje. Comentário `<!-- FILTROS DA API NOVA: busca só na API; portal antigo não tem estes filtros -->`.
    - Cabeçalho: `<!-- Busca: "..." | Totais portal-antigo: TRF4:5, ... | Totais api-nova: TRF4:13, ... -->`; rota caída → `<!-- ROTA FORA: portal-antigo (<erro>) -->`. Mantém `AVISO_COBERTURA_STJ` e `AVISO_VIA_API` (este quando houver documento `api-nova`/`ambas` do STF sem data).
    - Cada resultado: `extra` com `origem`, `tribunal`, `id`, `link_inteiro_teor`, `precedente_relevante` ("sim"), `representativo_controversia` ("sim").
    - Cache: chave com `v: 3`, `modo` e todos os filtros; grava só se houver documento e nenhuma rota com `ok=False`; cache hit não chama `buscar_duas_rotas` (logo não grava log).
  - `documento_cjf(id: str, campo: str = "", max_tokens: int = 0) -> str`: XML `<documento_cjf>` com os campos; `max_tokens > 0` trunca o texto. Erro → `<erro>`. Cache 30 dias.
  - `similares_cjf(id: str, max_resultados: int = 10, max_tokens_ementa: int = 600) -> str`: mesmo XML da busca, raiz `jurisprudencia_cjf_similares`. Cache 1 dia.
  - `estatisticas_cjf(busca: str, tribunais: str = "STF,TRF1,TRF2,TRF3,TRF4,TRF5,TRF6", modo: str = "comum", data_inicio: str = "", data_fim: str = "", relator: str = "") -> str`: texto legível — total, julgamentos por ano, 10 relatores, 10 classes, tribunais. Cache 1 dia.
  - Docstrings das tools e `ajuda_sintaxe_cjf` atualizados: as duas rotas e a origem, os filtros novos e que levam só à API, as três tools, o E implícito.

- [ ] **Step 1: Testes que falham** (dublês sobre `srv.cjf_duas_rotas.buscar_duas_rotas` e funções de `srv.cjf_api`; `cached_http`, `registrar_dispositivo`, `log_query` dublados como na fixture `srv_cjf`):
  - `test_busca_comum_usa_duas_rotas_e_marca_origem`: saída contém `<origem>ambas</origem>`, `Totais portal-antigo:` e `Totais api-nova:`.
  - `test_rota_fora_e_declarada`: `antiga.ok=False, erro="timeout"` → `ROTA FORA: portal-antigo` e documentos da API presentes.
  - `test_duas_rotas_falham_erro_declarado`: as duas `ok=False` → começa por `<erro>` e contém os dois erros.
  - `test_relaxa_so_com_lista_unificada_vazia`: primeira chamada com docs só do antigo → `relaxar_cjf` não chamado; primeira chamada vazia → segunda com `relaxada=True` e `BUSCA RELAXADA` na saída.
  - `test_filtro_novo_vai_so_a_api`: `data_inicio="01/01/2025"` → `buscar_duas_rotas` não chamado; `buscar_api` recebe `FiltrosApi(data_inicio="2025-01-01", ...)`; saída com `FILTROS DA API NOVA`.
  - `test_relator_nao_encontrado`: `sugerir_relator` devolve `[]` → `<erro>` com o texto pedido.
  - `test_cache_nao_grava_com_rota_em_erro`: `antiga.ok=False` → `registrar_dispositivo` não chamado; com as duas ok → chamado uma vez com `"v": 3` na chave.
  - `test_stj_sem_cobertura_nao_impede_cache`: `api.sem_cobertura=["STJ"]`, as duas ok → cacheia.
  - `test_documento_cjf_xml` / `test_documento_cjf_erro`; `test_similares_cjf_xml`; `test_estatisticas_cjf_legivel` (contém o total e um ano da fixture).

- [ ] **Step 2: Rodar** → FAIL. **Step 3: Implementar.** **Step 4: Rodar** este arquivo e `test_cjf_api_reserva.py` (os testes de servidor existentes ali dublam `cjf_client.buscar_documentos`; atualizá-los para dublar `buscar_duas_rotas`, mantendo o que asseveram: STJ declarado e só-STJ vira `<erro>`) → PASS.

- [ ] **Step 5: README** de `mcp/cjf-jurisprudencia/`: as duas rotas, o log `cjf_rotas.jsonl` e o script de comparação (Task 5), as tools novas.

- [ ] **Step 6: Commit** `feat(cjf): busca pelas duas rotas e tools documento, similares e estatisticas`.

---

### Task 5: Relatório de comparação das rotas

**Files:**
- Create: `mcp/shared/comparar_rotas_cjf.py`
- Test: `mcp/shared/test_comparar_rotas_cjf.py`

**Interfaces:**
- Consumes: o formato de linha do log (Task 3).
- Produces: CLI `python mcp/shared/comparar_rotas_cjf.py [--log CAMINHO] [--desde AAAA-MM-DD]` e função `resumir(linhas: list[dict]) -> dict` com, por rota (`antiga`, `api`): `chamadas`, `disponibilidade` (fração `ok`), `ms_mediana`, `ms_p90`, `taxa_cnj` (`com_cnj/n`), `soma_totais_por_tribunal`; e globais `sobreposicao_media`, `so_antiga_achou`, `so_api_achou` (contagem de buscas em que só uma rota teve `n > 0`). Linhas `relaxada: true` são contadas à parte (`relaxadas`), fora das médias. Log ausente ou vazio → mensagem "nenhuma busca registrada" e rc 0. Linha malformada → ignorada e contada em `linhas_invalidas`.

- [ ] **Step 1: Teste que falha** `test_resumir`: 4 linhas sintéticas (uma com antiga `ok=False`, uma relaxada, uma em que só a API achou) → `disponibilidade` da antiga `== 2/3`, `so_api_achou == 1`, `relaxadas == 1`; `test_log_vazio` e `test_linha_malformada_ignorada`.
- [ ] **Step 2: Rodar** → FAIL. **Step 3: Implementar** (`argparse`, saída em texto com uma tabela por rota). **Step 4: Rodar** → PASS; `python mcp/shared/comparar_rotas_cjf.py --help` mostra `usage:`.
- [ ] **Step 5: Commit** `feat(cjf): relatorio que compara as duas rotas pelo log`.

---

### Task 6: Fechamento — controle ao vivo, documentação do projeto e push

**Files:**
- Modify: `C:/Users/jpnes/.claude/DPU/ferramentas-mcp.md` (linha do `cjf-jurisprudencia`; repo `.claude`, commit próprio por pathspec)

- [ ] **Step 1: Controle ao vivo** (script no scratchpad que carrega o `server.py` como o `controle_vivo.py` de 23/10/2026, com `DPU_QUERY_LOG_DESLIGADO=1`): `buscar_jurisprudencia_cjf("bolsa familia unipessoal", "TRF3,TRF4")` traz `origem` dos três tipos ou ao menos duas; com `modo="semantica"`; com `data_inicio="2025-01-01", data_fim="2025-12-31"`; com `relator="parca"` (resolve para PAULO ROBERTO PARCA DE PINHO); `documento_cjf` e `similares_cjf` sobre o id do PUIL `0044230-77.2023.4.05.8300`; `estatisticas_cjf("bolsa E familia E unipessoal")`. Depois, UMA chamada sem a variável, e conferir que `~/.claude/DPU/logs/cjf_rotas.jsonl` ganhou uma linha sem texto de ementa; rodar `comparar_rotas_cjf.py` sobre ele.
- [ ] **Step 2: Suítes** do Global Constraints, redirecionadas a arquivo, sem cano: verdes, contagem acima da linha de base.
- [ ] **Step 3: `ferramentas-mcp.md`**: a linha do `cjf-jurisprudencia` deixa de dizer "não alcança Turma Recursal"; diz que consulta portal antigo e API nova, com turmas recursais pela API, e que o CNJ vem pela API; manter a recomendação do `trf1-jurisprudencia`. Raio de alcance (é texto): `grep -ln "ferramentas-mcp" C:/Users/jpnes/.claude/DPU/tests/test_*.py` e rodar esses arquivos.
- [ ] **Step 4: Commits e push**: `lista-trf` (`git push origin master`) e `.claude` (`docs(mcp): ...`, push ao fechar). O `lista-trf` não tem CI; no `.claude`, conferir o run após o push.
- [ ] **Step 5:** Avisar o Defensor de que o servidor MCP só carrega o código novo após reinício (`/mcp`) ou nova sessão.
