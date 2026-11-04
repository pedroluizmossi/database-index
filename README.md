# code-index

Indexador de código para repositórios git/GitHub usando [CocoIndex](https://cocoindex.io) 1.x,
embeddings **Voyage Code 4** (via LiteLLM) e **Postgres + pgvector**. Consulta por CLI ou MCP.

```
repos.toml ──git clone/fetch──▶ .repos/<nome> ──CocoIndex──▶ chunks (tree-sitter)
                                                   │
                                                   ▼
                     voyage-code-4 (input_type=document) ──▶ pgvector (HNSW) + índice full-text (GIN)
                                                                       │
                       CLI `code-index search` / MCP `search_code` ◀───┘  busca híbrida (RRF)
```

- **Incremental**: após um `git pull`, só arquivos alterados são reprocessados e só chunks novos
  geram chamadas à API da Voyage. Remover um repo do `repos.toml` apaga suas linhas no próximo sync.
- **Busca híbrida**: vetor (semântica) + full-text (identificadores), combinados por Reciprocal Rank Fusion.
- **Repositórios privados**: usa `GITHUB_TOKEN` ou, se ausente, `gh auth token`. O token só vai no
  header HTTP do comando git; nunca é gravado em `.git/config`.

## Setup

```bash
docker compose up -d          # Postgres 17 + pgvector em localhost:5432
cp .env.example .env          # preencha VOYAGE_API_KEY
uv sync
```

## Uso

### Repos avulsos e indexação manual por nome

```bash
uv run code-index repo add pallets/itsdangerous            # org/repo do GitHub, URL git ou diretório
uv run code-index index snr-functions-backend              # indexa agora: nome curto, org/repo ou org__repo
uv run code-index index senior-sistemas/snr-iassist outro-repo
uv run code-index index https://github.com/org/novo.git    # nome desconhecido: registra e indexa
```

### Orgs do GitHub, aos poucos

```bash
uv run code-index org add senior-sistemas                  # só descobre os repos (sem custo de embedding)
uv run code-index org add outra-org --match "snr-*" --skip "*-legacy" --max-size-mb 200
uv run code-index org list                                 # progresso: indexed / pending / error / ignored

uv run code-index sync --dry-run -b 5                      # mostra o próximo lote, sem custo
uv run code-index sync -b 5                                # indexa até 5: alterados > pendentes > com erro
uv run code-index watch -b 5 --interval 900                # avança a fila aos poucos e acompanha pushes

uv run code-index repo list -s pending -o senior-sistemas
uv run code-index repo remove senior-sistemas__repo-x       # repo de org: marca como ignorado
uv run code-index repo unignore senior-sistemas__repo-x
```

O estado fica em `code_index.repos` (commit indexado, revisão, último push, erro). Um sync só
clona/atualiza os repos do lote. Os demais repos indexados passam pelo pipeline com a mesma
revisão e são pulados sem percorrer arquivos. Um erro de sync **não** apaga o que já estava indexado.

Um `[[repo]]` avulso com o mesmo nome de um repo da org tem precedência (filtros próprios).

### Arquivos ignorados e conteúdo filtrado

Por padrão ficam de fora (`config.py`: `DEFAULT_EXCLUDE`, `TEST_EXCLUDE` e `NOISE_EXCLUDE`):
- dependências, builds, minificados, lockfiles;
- **testes de todas as linguagens** (`src/test`, `tests/`, `__tests__`, `*Test.java`, `*.spec.ts`,
  `test_*.py`, `*_test.go`, `*Tests.cs`, `*_spec.rb`...);
- `CHANGELOG*`/`CHANGES*`/`HISTORY*`, **`openapi.*`/`swagger.*`** (OpenAPI é indexado por outra fonte)
  e migrations de outros bancos (`migration/**/H2|Oracle|SqlServer|MySql`). Fica só a de PostgreSql;
- conteúdo (`content.py`): CRDs do Kubernetes e yaml/json/xml acima de 100 KB.

Traduções e pastas `spec/` continuam indexadas.

**Segredos são mascarados antes do embedding.** O texto que vai para a Voyage e para o Postgres já
sai com `***` no lugar de:
- senhas, tokens, `client_secret` e `api_key` (literais no código; valores em yaml, properties e env);
- chaves AWS, GitHub, GitLab, Slack, OpenAI, Anthropic, Voyage e Google, além de JWTs;
- credenciais em URLs e connection strings.

Blocos PEM e blobs base64 viram `<... omitido>`. Variáveis (`${X}`, `{{ }}`), nomes de
constante, chaves de i18n, números e tipos não são mascarados. Editar `content.py` faz o próximo
`sync` reaplicar as regras a todos os repos, reembedando só os trechos que mudaram.

Use `--exclude` em `repo add` ou `org add` para somar padrões (`extra_exclude` no `repos.toml`).

### Filtrar e priorizar a org por linguagem

```bash
code-index org set senior-sistemas --languages Java TypeScript Go   # filtra e define a prioridade
code-index org set senior-sistemas --languages                      # sem argumentos: remove o filtro
code-index org list                                                 # mostra a distribuição por linguagem
```
A linguagem é a principal do repo segundo o GitHub (`none` = sem linguagem detectada). Repos que
saem pelo filtro têm os chunks apagados no próximo sync.

### Busca

```bash
uv run code-index search "onde o token expirado é rejeitado"
uv run code-index search -m keyword UpdateAllLambdasRuntimeHandler -r senior-sistemas__snr-functions-backend
uv run code-index search "criação de tabela" -l sql -p "**/migration/**"
uv run code-index show senior-sistemas__snr-functions-backend/functions.sdl:381-404
uv run code-index search "..." --no-rerank                 # só busca híbrida
```

Pipeline de busca: vetor (top 80) + full-text (top 80) são combinados por RRF. Os 40 melhores
(`RERANK_CANDIDATES`) vão para o reranker (`RERANK_MODEL=voyage/rerank-2.5`), que devolve os top-k:
8 na CLI (`-k`) e 8 no MCP (`top_k`). Se o rerank falhar, a resposta usa a ordem híbrida.

## MCP (Claude Code)

O `.mcp.json` na raiz já registra o servidor para este projeto. Para usar em qualquer projeto:

```bash
claude mcp add --scope user code-index -- uv run --directory /home/pedro/Projetos/database-index code-index-mcp
```

Ferramentas: `search_code`, `read_file_lines`, `list_repos` (filtros por status/org/nome) e
`reindex` (nomes específicos ou um lote dos pendentes).

## Notas

- Trocar `EMBED_MODEL`/`EMBED_DIMENSIONS` invalida o cache de embeddings automaticamente:
  o próximo `sync` reembeda tudo (e cobra de novo na Voyage).
- Estado incremental do CocoIndex fica em `.cocoindex/` (LMDB). Se apagá-lo, rode `sync --full`.
- Tabela: `code_index.code_chunks (repo, path, id, language, code, start_line, end_line, embedding)`.
