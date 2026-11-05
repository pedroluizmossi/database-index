# Arquitetura: estado atual e proposta

Status: **implementado** em 2026-11-05 (etapas 1 a 5). Ver "Como foi implementado" no fim. A proposta
original segue abaixo como referência do raciocínio.

## Resumo

O núcleo é bom: toda fonte vira arquivos e passa pelo mesmo pipeline (chunks → embedding → busca
híbrida). O problema está na camada que **liga** as fontes. Ela cresceu por acúmulo: cada tipo de ligação
virou uma heurística dentro de uma consulta SQL, uma tabela própria ou uma regra baseada em nome.

A proposta tem três ideias, aplicadas aos poucos e sem reescrever o que funciona:

1. **Metadados tipados na origem.** A fonte grava o que sabe (ex.: domínio/serviço/operação de uma API).
   O resto do sistema não deduz mais isso de prefixos e títulos.
2. **Relações como dado.** Uma tabela `relations` substitui o `UNION` de heurísticas. Cada heurística
   vira um *linker* que grava relações. Aprovar, rejeitar e remover viram estados da relação.
3. **Fonte e linker como plugins.** Uma fonte nova = um plugin que produz documentos e, se quiser, um
   linker. Busca, X-Ray, MCP e UI leem relações genéricas.

---

## 1. Como está hoje

```
 FONTES                          MATERIALIZAÇÃO              PIPELINE (CocoIndex)          TABELAS
 GitHub org / repo avulso  ──►  clone git em .repos/<repo>  ─┐                            code_chunks (vetor + texto)
 Wiki.js (GraphQL)         ──►  .repos/docs__wiki/*.md      ─┼─► process_file ─────────►   code_symbols
 OpenAPI no S3             ──►  .repos/docs__openapi-*/*.md ─┘   (chunk, embed, símbolos)  doc_pages, repo_aliases, doc_mentions

 LIGAÇÕES (calculadas na consulta, infra/doc_link_store.links_sql)
   menção · tag · link GitHub · regra --link · nome do serviço · SDL · gravados (manual/agent/llm)
   − remoções aprovadas                                           → linked_docs, X-Ray, busca

 ENRIQUECIMENTO (nosso código, fora do CocoIndex)
   repo_cards (fatos + cartão) · api_impls (operação → handler) · enrich_tasks · doc_link_reviews

 INTERFACES: cli.py · http/app.py (REST) · http/mcp.py  →  services.py (1309 linhas)
```

### O que funciona bem (manter)

- **Tudo vira arquivo.** Wiki e OpenAPI viram `.md` com front-matter num "repo" `kind = docs`. Assim
  ganham de graça incremental, máscara de segredos, chunking, embedding, busca e `show`. Esse é o
  acerto central.
- **Pipeline incremental e barato.** Embeddings memoizados pelo texto. Um sync só mexe no que mudou.
- **Camadas** `interfaces → services → indexing/retrieval → infra → domain`, com o domínio puro e testado.
- **Enriquecimento sem LLM primeiro** (fatos, SDL → OpenAPI, handlers por nome), com agents só onde
  precisa e sempre como sugestão revisável.

---

## 2. Problemas

| # | Problema | Onde | Efeito |
|---|---|---|---|
| P1 | **Ligações não são dados.** Uma consulta com 7 heurísticas + gravados + remoções, montada em Python | `doc_link_store.links_sql` | Tipo novo de ligação = editar o SQL, `ORIGIN_ORDER`, `CONTESTABLE_ORIGINS` e o `OriginKind` do front. Consulta pesada, recalculada a cada uso (X-Ray, busca, fila da wiki) |
| P2 | **Tipo de documento deduzido do nome.** | 9 checagens de `docs__openapi-` no backend e 3 no front | Uma fonte nova com regras próprias espalha mais `if` por prefixo |
| P3 | **Dados jogados fora na origem.** A fonte OpenAPI sabe domínio/serviço/operação/método, mas só grava título e caminho | `api_impl.operation_from_title` (regex no título), `split_part(path, '/', 2)` | Frágil: um título ou caminho diferente quebra o vínculo em silêncio |
| P4 | **Contrato de fonte só para metade das fontes.** `RepoSource` cobre a descoberta do GitHub. Wiki e OpenAPI entram por `if source.type == ...` | `sources/base.py`, `orchestrator._fetch_docs` | Fonte nova = editar o orquestrador |
| P5 | **Relações espalhadas** em tabelas com regras próprias | `doc_links`, `doc_mentions`×`repo_aliases`, `api_impls`, `repo_cards.related_repos`, `doc_link_reviews` | Cada tela/tool sabe juntar um pedaço. Não existe "tudo ligado a X" |
| P6 | **Dois donos de schema.** CocoIndex (chunks, símbolos, páginas, apelidos, menções) e migrations (resto) | `code_flow.py`, `infra/sql/` | Joins entre tabelas com ciclos de vida diferentes; tabelas que "ainda não existem" (`available()`, `UndefinedTableError`) |
| P7 | **`services.py` faz tudo** | 1309 linhas | Difícil de ler e de testar por partes |

---

## 3. Modelo alvo

### 3.1 Fonte = plugin

```python
class Source(Protocol):
    name: str                      # "github", "wikijs", "openapi_s3", "confluence"...
    kind: Literal["code", "docs"]

    async def discover(self, cfg) -> list[SourceItem]          # o que existe (sem baixar conteúdo)
    async def fetch(self, cfg, dest: Path, only=None) -> str   # materializa arquivos; devolve a revisão
    def linkers(self) -> list[Linker]                          # opcional: ligações próprias da fonte
```

O orquestrador só chama `discover`/`fetch` pelo registro de fontes, sem `if source.type`. GitHub, wiki
e OpenAPI viram as três primeiras implementações.

### 3.2 Documento com metadados tipados

O front-matter que a fonte grava ganha `type` e `attrs`, e o pipeline guarda isso numa coluna da página:

```yaml
---
title: "POST /email/queries/listBlacklistedEmails"
type: api_operation                       # wiki_page | api_operation | api_service | ...
attrs: {domain: platform, service: email, operation: listBlacklistedEmails, kind: query, method: POST}
---
```

Acaba com P2 e P3: nada mais deduz tipo de prefixo nem operação de título.

### 3.3 Chaves de entidade

Sem tabela de entidades nem banco de grafo. Uma convenção de chave em texto basta:

| Entidade | Chave | Exemplo |
|---|---|---|
| repo | `repo:<nome>` | `repo:senior-sistemas__snr-email-backend` |
| documento | `doc:<doc_repo>/<path>` | `doc:docs__wiki/pt-br/Plataforma/Email.md` |
| serviço de API | `api:<domínio>/<serviço>` | `api:platform/email` |
| operação | `op:<domínio>/<serviço>/<operação>` | `op:platform/email/listBlacklistedEmails` |
| símbolo/arquivo | `code:<repo>/<path>:<linha>` | `code:...snr-email-backend/java/.../ListBlacklistedEmailsHandler.java:17` |

### 3.4 Tabela única de relações

```sql
CREATE TABLE relations (
    src        text NOT NULL,          -- chave de entidade
    dst        text NOT NULL,
    type       text NOT NULL,          -- documents | implements | exposes | related_to | mentions ...
    origin     text NOT NULL,          -- linker que gerou: mention, tag, url, rule, service_name, sdl,
                                       -- handler_name, llm, manual, agent
    status     text NOT NULL DEFAULT 'active',   -- active | suggested | rejected
    action     text NOT NULL DEFAULT 'add',      -- add | remove (contesta uma relação automática)
    confidence text,                   -- alta | media | baixa (quando faz sentido)
    evidence   jsonb NOT NULL DEFAULT '{}',      -- termo casado, arquivo:linha, motivo do agent...
    input_hash text,                   -- o que o linker viu (para recalcular só o que mudou)
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (src, dst, type, origin)
);
```

**Relação efetiva** (o que busca, X-Ray e MCP usam) = linhas `active` + `add`, menos os pares com uma
remoção `active`. Fica numa *view* `effective_relations`, que é a única consulta de ligação do sistema.

### 3.5 Linkers

```python
class Linker(Protocol):
    name: str                          # vira o `origin`
    contestable: bool                  # agent pode sugerir remover? (menção/tag: sim; manual/regra: não)
    def affected_by(self, change) -> bool          # mudou repo/página/cartão relevante?
    async def run(self, ctx, changed: Changes) -> list[Relation]
```

| Linker | Hoje | Família |
|---|---|---|
| `mention`, `tag`, `url` | parte de `links_sql` (menções × apelidos) | determinístico, no sync |
| `rule` (`--link`) | `origin = 'explicit'` | determinístico |
| `service_name`, `sdl` | `links_sql` (nome do serviço / `repo_cards.facts.sdl`) | determinístico |
| `handler_name` | `api_impls` | determinístico |
| `llm` (wiki → repo, operação sem handler, relacionados do cartão) | `doc_links origin=llm`, `repo_cards.related_repos` | agent, via fila `enrich` |
| `manual`, `agent` | `doc_links` | gravado por pessoa/agent |

Os determinísticos rodam no fim do sync, só para o que mudou (o pipeline já sabe o que mudou), e
**substituem** as próprias linhas (`DELETE ... WHERE origin = X AND src IN changed` + `INSERT`). Os de
agent continuam com a fila `next`/`submit` de hoje, que passa a gravar em `relations`.

### 3.6 Leitura

- `linked_docs(repo)`, X-Ray, `linked_repos`/`implemented_in` na busca, "documentação ligada", `repo_card`:
  todos viram `SELECT ... FROM effective_relations WHERE src = $1 OR dst = $1`.
- MCP: uma tool genérica `related(entidade, tipo?)` pode substituir partes de `find_api_impl` e de
  `repo_card`. As tools atuais continuam como atalhos.
- Front: `OriginKind` sai do código e vem da API (lista de linkers com rótulo e se é contestável).

---

## 4. Adicionar uma fonte, antes e depois

Exemplo: **Confluence** (páginas) ou **ADRs num repo**.

| Passo | Hoje | Com o modelo alvo |
|---|---|---|
| Baixar e materializar | módulo em `sources/` + `if` no `orchestrator._fetch_docs` + tipo no `DocSource` | plugin `Source` registrado |
| Busca, menções, tags | funcionam (é `kind = docs`) | iguais |
| Tipo/atributos próprios | prefixo no nome do repo + `if` espalhados | `type`/`attrs` no front-matter |
| Ligação específica (ex.: página cita uma issue Jira) | editar `links_sql`, `ORIGIN_ORDER`, front | um `Linker` novo |
| X-Ray, MCP, UI mostrarem a ligação | editar cada tela | automático (relações genéricas) |
| Fila de agent para revisar | adaptar `doc_task`/`enrich_store` | reaproveita: o linker `llm` recebe a entidade nova |

---

## 5. Plano de migração (incremental; cada etapa entrega e pode parar ali)

Nenhuma etapa reembeda nada. Relações e metadados não passam pela Voyage. Onde o schema do CocoIndex
muda, as linhas são recalculadas, mas os embeddings vêm do cache pelo texto. **Confirmar com `sync
--dry-run` e com o contador de chamadas da Voyage num repo de teste antes de rodar no índice real.**

### Etapa 1: `relations` + view, em paralelo ao que existe
- Migration com `relations` e `effective_relations`.
- Linkers determinísticos gravam em `relations` no fim do sync, **sem** desligar `links_sql`.
- Teste de equivalência: o conjunto (doc, repo, origem) de `links_sql` é igual ao de `effective_relations`.
- **Pronto quando:** os dois conjuntos batem no índice real (script de comparação).

### Etapa 2: leitores trocam para a view
- `linked_docs`, `annotate` (busca), X-Ray, `docs_overview`, fila da wiki e `find_api_impl` leem
  `effective_relations`.
- `doc_links` (manual/agent/llm, sugestões e remoções) migra para `relations`. `api_impls` vira
  `type = implements`.
- `links_sql` e `ORIGIN_ORDER` são apagados. `OriginKind` passa a vir da API.
- **Pronto quando:** a suíte de integração passa sem `links_sql`; `eval` sem regressão.

### Etapa 3: metadados tipados
- Wiki e OpenAPI gravam `type`/`attrs` no front-matter. `doc_pages` ganha `doc_type`/`attrs` (subir a
  `version` do `process_file`).
- Remover `operation_from_title`, `split_part(path, ...)` e as checagens de `docs__openapi-`.
- **Pronto quando:** `grep "docs__openapi-"` só aparece na própria fonte OpenAPI.

### Etapa 4: contrato `Source`
- `RepoSource` vira `Source` (`discover`/`fetch`/`linkers`). GitHub, wiki e OpenAPI implementam.
- O orquestrador perde os `if source.type`.
- **Pronto quando:** uma fonte de teste (pasta de `.md` local) entra só registrando o plugin.

### Etapa 5: dividir `services.py`
- `services/` com `repos.py`, `docs.py`, `search.py`, `enrich.py`, `auth.py` e uma fachada fina que
  as interfaces continuam usando (sem mudar CLI/API/MCP).
- Pode ser feita a qualquer momento; fica por último porque não muda comportamento.

---

## 6. Trade-offs e riscos

- **Recalcular vs. calcular na consulta.** Hoje as ligações nunca ficam desatualizadas, porque são
  calculadas na hora. Gravadas, dependem do linker rodar quando algo muda. Mitigação: rodar no fim de
  todo sync, a partir da lista de mudanças, e oferecer `code-index links rebuild` (sem custo).
- **Ordem entre linkers.** O `sdl` depende dos fatos do cartão, e o `llm` depende de cartões e
  relações. O registro de linkers declara dependências simples (lista ordenada basta).
- **Volume.** As menções geram mais linhas do que as outras origens. As 674 menções atuais são poucas;
  com mais wiki, indexar `relations (dst)` e `(src)` resolve.
- **Esforço.** As etapas 1 e 2 são o grosso: mexem em `doc_link_store`, `chunk_store`, `enrich_store`,
  `services` e no front do X-Ray e da Documentação. As etapas 3 e 4 são menores. Nenhuma exige
  reindexar com custo.
- **Não fazer:** banco de grafo, reescrita de uma vez, tabela genérica de entidades (as chaves em texto
  bastam) e mexer no pipeline de chunks/embedding, que está bom.

## 7. Decisões em aberto

1. **Nome do tipo de relação:** usar um vocabulário pequeno e fixo (`documents`, `implements`,
   `exposes`, `related_to`) ou deixar livre por linker? Recomendo fixo, com a lista na API.
2. **Menções como relação ou como evidência?** As menções fracas, que hoje não ligam, poderiam virar
   relações `status = 'suggested'` para a fila de agent revisar. Recomendo começar só com as que ligam hoje.
3. **Relacionados entre repos** (`repo_cards.related_repos`): migram para `relations` (`repo → repo`,
   `related_to`) na etapa 2 ou ficam no cartão? Recomendo migrar: o X-Ray passa a mostrar repo ↔ repo.
4. **Ordem frente à fila de cartões/wiki:** as filas funcionam hoje e migram junto na etapa 2. Dá para
   rodar os agents antes, pois o que eles gravam (`doc_links`, `api_impls`) é migrado sem perda.

---

## 8. Como foi implementado (2026-11-05)

**Backup antes de começar:** `.backups/20261105-032516/` (dump do Postgres, estado do CocoIndex,
`repos.toml`, código). A pasta está no `.gitignore`.

**Migração dos dados** (`infra/sql/010_relations_documents.sql`): cria `documents`, `relations`, a view
`effective_relations` e `enrich_reviews`; copia `doc_links` → `relations` (manual/agent e sugestões de LLM,
com status e ação), `api_impls` → `relations implemented_by`, `doc_ignores` → `documents.ignored`,
`doc_link_reviews` → `enrich_reviews`; e **apaga as quatro tabelas antigas**. No primeiro uso depois da
migração, o serviço calcula o catálogo de documentos e as relações derivadas (`ensure_relations`).

**Equivalência conferida no índice real** contra uma linha de base gravada antes da migração:
- vínculos página → repo: 1982 antes, 1982 depois, **idênticos** (mesma página, repo e origem: 674
  menção, 459 tag, 429 nome do serviço, 411 SDL, 8 link, 1 manual);
- operação → handler: 866 = 866 idênticos; páginas ignoradas: 33 = 33;
- relações novas que o modelo permite: 553 página de API → operação, 7 repo → repo (cartões).
Teste automatizado da cópia: `tests/integration/test_relation_model.py::test_migracao_010_copia_dados_e_apaga_tabelas`.

**Desempenho:** recálculo completo (catálogo + 7 linkers) em ~0,6 s no índice real, depois de reescrever o
linker de apelidos (anti-join com CTEs materializadas: de 105 s para 0,09 s). Leituras pela view: 5 a
60 ms.

**Diferenças em relação à proposta**
- **Metadados tipados sem reprocessar:** em vez de reescrever o front-matter dos `.md` (o que faria o
  CocoIndex re-chunkar e poderia gastar Voyage), cada plugin descreve o que já materializou
  (`documents()` lê manifesto e front-matter locais). `doc_pages` continua como saída do pipeline (o que
  foi indexado); `documents` é o catálogo da fonte (o que existe e o que é).
- **Nomes de origem mantidos** (`explicit`, `service`, `sdl`, `tag`, `url`, `mention`, `manual`, `agent`,
  `llm`) para preservar dados e telas; novas: `source` (declarado pela fonte), `handler`, `card`.
- **Contestável por linha:** a coluna `relations.contestable` (vinda do registro de origens) evita repetir
  a lista de origens na view.
- **Vínculo manual sobre remoção aprovada:** a remoção anula só as origens contestáveis; o manual vale.
- **`services/` em módulos por área** (mixins de `CodeIndexService`): `repos`, `apis` (OpenAPI), `wiki`,
  `docs`, `search`, `sync`, `enrich`, `auth`, `base`. CLI, API e MCP não mudaram de interface.
- **Recalcular quando muda:** no fim de todo sync (com coleta de fatos dos repos alterados), ao ignorar,
  reativar ou remover repos e orgs, ao remover fonte, ao entregar cartão (só o linker `card`) e em
  `relations rebuild`.

**Novidades visíveis:** `relations show|stats|rebuild` (CLI), `GET /api/v1/relations`,
`/api/v1/relations/origins`, `POST /api/v1/relations/rebuild`, tool MCP `related`; X-Ray e Documentação
usam o tipo do documento; rótulos de origem vêm da API.

**Decisões em aberto (seção 7), como ficaram:** vocabulário de tipos fixo (`documents`, `describes`,
`implemented_by`, `related_to`); menções fracas não viraram sugestão; relacionados do cartão migraram para
`related_to`; as filas de agents funcionam sobre o modelo novo.
