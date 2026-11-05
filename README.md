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
cp repos.example.toml repos.toml   # opcional: ou use `code-index repo add` / `org add`
```

## Uso

### Repos avulsos e indexação manual por nome

```bash
uv run code-index repo add pallets/itsdangerous            # org/repo do GitHub, URL git ou diretório
uv run code-index index meu-backend                         # indexa agora: nome curto, org/repo ou org__repo
uv run code-index index minha-org/meu-servico outro-repo
uv run code-index index https://github.com/org/novo.git    # nome desconhecido: registra e indexa
```

### Orgs do GitHub, aos poucos

```bash
uv run code-index org add minha-org                        # só descobre os repos (sem custo de embedding)
uv run code-index org add outra-org --match "api-*" --skip "*-legacy" --max-size-mb 200
uv run code-index org list                                 # progresso: indexed / pending / error / ignored

uv run code-index sync --dry-run -b 5                      # mostra o próximo lote, sem custo
uv run code-index sync -b 5                                # indexa até 5: alterados > pendentes > com erro
uv run code-index watch -b 5 --interval 900                # avança a fila aos poucos e acompanha pushes

uv run code-index repo list -s pending -o minha-org
uv run code-index repo remove minha-org__repo-x       # repo de org: marca como ignorado
uv run code-index repo unignore minha-org__repo-x
```

O estado fica em `code_index.repos` (commit indexado, revisão, último push, erro). Um sync só
clona/atualiza os repos do lote. Os demais repos indexados passam pelo pipeline com a mesma
revisão e são pulados sem percorrer arquivos. Um erro de sync **não** apaga o que já estava indexado.

Um `[[repo]]` avulso com o mesmo nome de um repo da org tem precedência (filtros próprios).

### Arquivos ignorados e conteúdo filtrado

Por padrão ficam de fora (`domain/file_patterns.py`: `DEFAULT_EXCLUDE`, `TEST_EXCLUDE` e `NOISE_EXCLUDE`):
- dependências, builds, minificados, lockfiles;
- **testes de todas as linguagens** (`src/test`, `tests/`, `__tests__`, `*Test.java`, `*.spec.ts`,
  `test_*.py`, `*_test.go`, `*Tests.cs`, `*_spec.rb`...);
- `CHANGELOG*`/`CHANGES*`/`HISTORY*`, **`openapi.*`/`swagger.*`** (OpenAPI é indexado por outra fonte)
  e migrations de outros bancos (`migration/**/H2|Oracle|SqlServer|MySql`). Fica só a de PostgreSql;
- conteúdo (`indexing/content.py`): binários com extensão de texto (byte nulo ou >10% de caracteres
  inválidos, ex.: vídeo `.ts`), CRDs do Kubernetes e yaml/json/xml acima de 100 KB.

Traduções e pastas `spec/` continuam indexadas.

Além de código, entram Terraform (`.tf`, `.tfvars`, `.hcl`; o `terraform.tfstate` fica de fora), contratos
(`.proto`, `.graphql`), `Jenkinsfile`/`.groovy`, templates Helm (`.tpl`), `.cjs`/`.mts`/`.cts` e `.less`/`.sass`.

**Como cada trecho é guardado (`indexing/context.py`):**
- o código guardado e devolvido são **linhas inteiras**, iguais às do `read_file`, e CRLF vira LF;
- o texto embedado leva um cabeçalho de contexto, com caminho, linguagem, a classe ou seção que envolve o
  trecho e o que ele declara. Exemplo: `# org__repo/src/HmacServiceImpl.java (java)` seguido de
  `# em: HmacServiceImpl · declara: sign`. A extração usa expressões regulares por linguagem e cobre
  Java/Kotlin/C#, TS/JS, Python, Go, Rust, Ruby, PHP, SQL, Terraform, `.sdl`, Kubernetes YAML e Markdown.

**Segredos são mascarados antes do embedding.** O texto que vai para a Voyage e para o Postgres já
sai com `***` no lugar de:
- senhas, tokens, `client_secret` e `api_key` (literais no código; valores em yaml, properties e env);
- chaves AWS, GitHub, GitLab, Slack, OpenAI, Anthropic, Voyage e Google, além de JWTs;
- credenciais em URLs e connection strings.

Blocos PEM e blobs base64 viram `<... omitido>`. Variáveis (`${X}`, `{{ }}`), nomes de
constante (camelCase, PascalCase, com `_`/`.`/`-`), chaves de i18n, números e tipos não são
mascarados. A mesma máscara vale para `show` e para a tool `read_file_lines` do MCP. Editar `indexing/content.py` faz o próximo
`sync` reaplicar as regras a todos os repos, reembedando só os trechos que mudaram.

Use `--exclude` em `repo add` ou `org add` para somar padrões (`extra_exclude` no `repos.toml`).

### Filtrar e priorizar a org por linguagem

```bash
code-index org set minha-org --languages Java TypeScript Go   # filtra e define a prioridade
code-index org set minha-org --languages                      # sem argumentos: remove o filtro
code-index org list                                                 # mostra a distribuição por linguagem
```
A linguagem é a principal do repo segundo o GitHub (`none` = sem linguagem detectada). Repos que
saem pelo filtro têm os chunks apagados no próximo sync.

### Busca

```bash
uv run code-index search "onde o token expirado é rejeitado"
uv run code-index search -m keyword UserRepository -r minha-org__meu-backend
uv run code-index search "criação de tabela" -l sql -p "**/migration/**"
uv run code-index show minha-org__meu-backend/src/main/App.java:10-40
uv run code-index search "..." --no-rerank                 # só busca híbrida
uv run code-index symbol HmacServiceImpl                    # onde está declarado (também Classe.metodo)
uv run code-index symbol "HmacServiceImpl.*"                # membros da classe
uv run code-index grep "Saldo insuficiente" -p "**/*.java"  # texto exato via ripgrep (-E para regex)
uv run code-index search "envio de e-mail" --kind doc       # só documentação (README, docs/, wiki)
uv run code-index eval -v                                   # mede a qualidade da busca (evals/queries.json)
```

**Pipeline de busca.** Duas fontes, combinadas por RRF (Reciprocal Rank Fusion) **com pesos**:
- **vetor** (pgvector HNSW), com o embedding do trecho mais um cabeçalho de contexto;
- **termos**, com tokenização própria para código (`domain/search_text.py`): `HmacServiceImpl` vira
  `hmacserviceimpl hmac service impl`, então a busca acha o nome exato e pedaços dele. Nomes de símbolos e
  o caminho pesam mais que o corpo do código. O motor é escolhido conforme o banco:

  | Motor | Quando |
  |---|---|
  | `bm25` | o banco tem `pg_search` (imagem `docker/postgres`) e o índice já tem as colunas de busca |
  | `fts` | Postgres comum: full-text nativo nas mesmas colunas, com pesos A/B |
  | `fts-legacy` | tabela antiga, antes de reindexar: full-text sobre o código |

Os pesos da fusão dependem da consulta: frases favorecem o vetor (1,0 contra 0,7); consultas que parecem um
identificador (`HmacServiceImpl`, `get_user`, `Classe.metodo`) favorecem os termos (1,0 contra 0,6). Os 40
melhores (`RERANK_CANDIDATES`) vão para o reranker (`RERANK_MODEL=voyage/rerank-2.5`), que devolve os top-k.
Se o rerank falhar, a resposta usa a ordem da fusão.

O reranker é genérico e tende a preferir prosa (wiki, descrições do `.sdl`) ao código que implementa a
função. Por isso a consulta vai a ele com uma instrução pedindo código (`RERANK_INSTRUCTION=true`, padrão;
desligada quando a busca é `kind=doc`). Medido com `code-index eval --rerank-variants`: MRR de 0,76 para 0,88
e hit@3 de 0,81 para 1,00. Mandar o cabeçalho de contexto (`RERANK_HEADER`) ou reordenar só o código
(`RERANK_CODE_ONLY`) não trouxe ganho claro; ficam desligados. Consultas que parecem um identificador
(`BlockedWordsCache`, `get_user`) não vão ao reranker (`RERANK_SKIP_IDENTIFIERS=true`): a busca por termos
já acha o nome exato, e o rerank só a piorava (MRR dessas consultas de 0,87 para 0,93, sem custo de rerank).

Os termos ignoram acentos ("validacao" acha "validação"), e a consulta também procura o singular
("tokens" acha "token"). Na resposta, trechos vizinhos do mesmo arquivo são fundidos (no máximo 2 por
arquivo), um trecho idêntico em vários repos (boilerplate) aparece uma vez, com os outros em `also_in`, e
as páginas de documentação ficam limitadas a um quarto do top-k (mínimo 2; `--max-docs` muda isso).

**Avaliação.** `evals/queries.json` tem perguntas com a resposta conhecida (repo, caminho e linha
opcional). `code-index eval` mede hit@1, hit@3, hit@k e MRR em cada modo de busca, e `--grid` compara os
pesos da fusão sem rerank. Rode depois de mexer na busca ou no chunking. Cada pergunta custa um embedding
de consulta e, nos modos com rerank, uma chamada ao reranker (centavos).

**Símbolos.** A tabela `code_symbols` é preenchida na indexação, sem custo de API, com as declarações de
cada arquivo: nome, tipo, classe envolvente, linha e assinatura.

**Grep.** Roda o ripgrep nos clones locais, com os mesmos filtros de arquivo do índice. Respeita o
`.gitignore`, ignora arquivos ocultos e binários, mascara segredos e tem limites de resultado e tempo.

### Documentação externa (Wiki.js) ligada aos repos

Páginas de uma wiki [Wiki.js](https://js.wiki) 2.x entram no índice como um repo `kind = "docs"`
(`docs__<nome>`), lado a lado com o código. O acesso é pela API GraphQL (não precisa de storage git).
O token precisa das permissões `read:pages` e `read:source`. O cadastro lê uma página publicada
acessível para verificar ambas; uma wiki sem páginas acessíveis precisa de uma página publicada antes do cadastro.

```bash
# .env: WIKIJS_TOKEN=<API key com read:pages e read:source> (Administração → API Access)
uv run code-index docs add wiki --url https://wiki.exemplo.com --locale pt-br \
  --include 'Plataforma/**' --exclude 'Rascunhos/**' \
  --link 'Plataforma/Email/**=snr-email-backend,snr-email-frontend'
uv run code-index docs list
uv run code-index sync --dry-run                 # mostra o que entraria (custo zero)
uv run code-index index docs__wiki               # baixa as páginas e indexa
uv run code-index docs links snr-email-backend   # páginas ligadas ao repo, e por quê
uv run code-index docs remove wiki
```

- **Incremental:** a cada sync, só a lista de páginas é consultada. O conteúdo só é baixado para páginas
  novas, com caminho alterado ou com `updatedAt` diferente, e as páginas removidas (ou que saíram dos filtros) são apagadas.
  Cada página vira `.repos/docs__<nome>/<locale>/<caminho>.md`, com front-matter (título, URL, tags). Como
  no código, só trechos novos vão à Voyage.
- **Páginas não publicadas ou privadas** são ignoradas. HTML (editor visual) é convertido para Markdown.
- **URL da página:** resultados de documentação externa trazem `url` (a página original), `title` e
  `linked_repos`.

**Vínculos página → repo.** São calculados na consulta, sem custo de API, então não ficam desatualizados.
Valem para qualquer stack:

| Origem | Como liga |
|---|---|
| `explicit` | regra `--link 'caminho_glob=repo_glob,...'` (o repo casa com ou sem a org) |
| `tag` | tag da página igual a um **apelido** do repo |
| `url` | link `github.com/<org>/<repo>` na página |
| `mention` | termo do texto igual a um apelido **forte** do repo |

Apelidos de um repo saem do nome (`org__repo`, `org/repo`, `repo` e `repo` sem prefixo curto como `snr-`) e
dos manifestos: `package.json`, `pom.xml`, `pyproject.toml`, `setup.cfg`, `go.mod`, `Cargo.toml`,
`settings.gradle(.kts)` e `catalog-info.yaml` (Backstage). Um apelido é **forte** quando é específico:
8+ caracteres com `-`, `_`, `.`, `/` ou `@` (ex.: `email-backend`), ou 14+ caracteres. Nomes genéricos
(`email`, `core`) só ligam por tag ou regra explícita.

**Na busca:** filtrar por repo (`-r`, `repos`) traz também as páginas ligadas a ele. Use `--no-linked-docs`
(`include_linked_docs=false`) para desligar. `--kind code|doc` (`kind`) restringe a código ou a documentação;
README, `docs/` e outros Markdown/RST/AsciiDoc dos repos contam como `doc`.

### APIs Swagger no S3

A aba **APIs** permite listar domínios e serviços, ver uma prévia e iniciar a indexação (admin).
A listagem e a prévia não geram embeddings. Pela CLI:

```bash
uv run code-index api domains
uv run code-index api services --domain platform
uv run code-index api index --domain platform --service scheduler --dry-run
uv run code-index api index --domain platform --service scheduler
uv run code-index api index --domain platform --dry-run
uv run code-index api index --domain platform
```

Repita `--service` para selecionar vários serviços. Sem `--service`, seleciona o domínio inteiro,
incluindo serviços adicionados no futuro, quando a fonte for sincronizada novamente. A seleção é
persistida como fonte `docs__openapi-<domínio>`; seleções individuais são cumulativas. Uma execução
com serviços específicos atualiza somente esses serviços e preserva os demais já indexados.
Para atualizar a seleção persistida inteira use `code-index index docs__openapi-platform`.
O download e a conversão são incrementais por ETag; cada operação vira um documento pesquisável.
As operações preservam todos os campos e as referências locais alcançáveis são incluídas, com detecção
de ciclos. Referências externas só são buscadas via HTTPS dentro do mesmo bucket configurado, sem
redirecionamentos. As que não puderem ser resolvidas ficam sinalizadas no documento. Fontes que usam
referências externas são verificadas novamente a cada sync. A indexação gera custos de embeddings; a prévia não.

A descrição completa do serviço fica em uma página de visão geral; cada operação recebe somente a
primeira linha. Operações e visão geral são ligadas por nome ao repo do serviço quando existe um único
repo compatível (ignorando o prefixo `snr-`, separadores e o sufixo `-service` ou `-backend`).
Quando há ambiguidade, nenhuma ligação automática é feita; vínculos manuais continuam disponíveis.

Na interface, **Raio X** permite navegar entre repos, documentos e serviços de API indexados. O vínculo
de cada item ao repo mostra sua origem. Relações entre documentos e APIs por um repo em comum são
indicadas como inferidas; não comprovam que o documento descreva aquela API. A consulta usa o índice
existente e não gera embeddings.

## Servidor HTTP: API REST + MCP

`code-index serve` sobe num único processo:
- a API REST em `/api/v1` (documentação interativa em `/docs`);
- o login Google em `/auth`;
- o MCP (Streamable HTTP) em `/mcp`.

Não existe mais MCP via stdio.

```bash
# .env: CODE_INDEX_AUTH_SECRET (obrigatório), GOOGLE_CLIENT_ID/SECRET, CODE_INDEX_ADMIN_EMAILS, CODE_INDEX_PUBLIC_URL
uv run code-index serve                      # escuta em 127.0.0.1:8765; ponha um proxy com TLS na frente
```

O comando `code-index` vive no ambiente do projeto: use `uv run code-index ...` na pasta do projeto, ou
instale-o globalmente com `uv tool install -e .` (editável: acompanha o código).

### Interface web (React)

Código em `frontend/` (React 19, Tailwind 4, Vite). O `code-index serve` entrega o build em `/ui`.

```bash
cd frontend
pnpm install
pnpm build     # gera frontend/dist; sem isso o serve sobe, mas /ui não existe
pnpm dev       # desenvolvimento em http://localhost:5173/ui/, com /api repassado ao servidor
```

Entre com uma API key (`cix_...`). A página só chama `/api/v1` com essa chave. O `pnpm dev` aponta para
`http://127.0.0.1:8765`; use `CODE_INDEX_API` para outra porta.

### Autenticação e permissões

| Quem | Como entra | Scopes |
|---|---|---|
| Pessoa autorizada (e-mail em `GOOGLE_ALLOWED_EMAILS`/admins, ou domínio Workspace em `GOOGLE_ALLOWED_DOMAIN`) | `GET /auth/login` (Google OIDC + PKCE; valida assinatura, `nonce` e `email_verified`) → cookie de sessão | `search:read`; admins: todos |
| Agent / automação | `Authorization: Bearer cix_...` | os do token |

**Scopes:**
- `search:read`: busca, leitura de arquivos e listagens, inclusive todo o MCP;
- `index:write`: sync e reindex, que geram custo na Voyage;
- `admin`: gerencia orgs, repos e auditoria, e implica os demais scopes.

**Tokens de agent:**
- O valor é mostrado só uma vez. No banco fica apenas o HMAC-SHA256 com `CODE_INDEX_AUTH_SECRET`.
- Têm validade (padrão de 90 dias) e podem ser revogados.
- **Só pessoas logadas pelo Google emitem tokens** (`POST /api/v1/tokens`), e só com scopes que elas já têm.
  Um token nunca emite outro.
- O primeiro token (bootstrap) é emitido pelo operador no servidor:
  ```bash
  uv run code-index token create "claude-code" --owner voce@gmail.com            # search:read
  uv run code-index token create "ci-indexador" --owner voce@gmail.com --scopes search:read index:write
  uv run code-index token list [--all]
  uv run code-index token revoke <id>
  ```

**Outras proteções:**
- **Hosts e origens:** o header `Host` precisa estar na lista permitida (proteção contra DNS rebinding na API
  e no MCP).
- **CSRF:** requisições autenticadas por cookie que alteram estado exigem `Origin` igual a `CODE_INDEX_PUBLIC_URL`.
- **Token só no header:** token na URL não é aceito.
- **Registro de repos:** `sync`/`reindex` só registram repos desconhecidos para admins. Com `index:write`
  dá para indexar apenas repos já cadastrados.
- **Jobs persistentes:** cada sync vira um registro em `code_index.sync_jobs`, com quem pediu, parâmetros,
  resultado e erro. Um reinício do servidor no meio de uma indexação marca o job como `failed`.
- **Rate limit por token:** leitura 120/min, busca 30/min, sync 6/hora (configurável).
- **Auditoria:** fica em `code_index.audit_log`, com quem, ação, parâmetros, status e IP. Nunca grava
  tokens nem o conteúdo retornado. Admins consultam em `GET /api/v1/audit`.
- **Segredos mascarados:** respostas de busca e de leitura de arquivo saem com a mesma máscara do índice.

### API REST (resumo)

| Método e rota | Scope |
|---|---|
| `GET /api/v1/me` | search:read |
| `POST /api/v1/search` · `GET /api/v1/files/{repo}/{path}` | search:read |
| `GET /api/v1/repos` · `GET /api/v1/orgs` | search:read |
| `POST /api/v1/sync` (job em background) · `GET /api/v1/sync/jobs` · `GET /api/v1/sync/jobs/{id}` | index:write |
| `POST/DELETE /api/v1/repos…` · `POST/PATCH/DELETE /api/v1/orgs…` · `GET /api/v1/audit` | admin |
| `GET/POST/DELETE /api/v1/tokens` | sessão Google (emitir); dono ou admin (revogar) |

### MCP no Claude Code

Os tokens de agent servem para o MCP. O `.mcp.json` deste projeto lê `CODE_INDEX_URL` e `CODE_INDEX_TOKEN`
do ambiente. Para usar em qualquer projeto:

```bash
claude mcp add --scope user --transport http code-index https://<host>/mcp \
  --header "Authorization: Bearer cix_..."
```

Ferramentas, todas com `search:read`: `search_code` (por significado; `kind` e `include_linked_docs` como
na API), `find_symbol` (onde algo é
declarado), `grep_code` (texto exato ou regex), `read_file_lines` e `list_repos`. O MCP não indexa:
sync fica na API e na interface, que mostram a prévia e pedem confirmação.

### Login Google: configuração

1. No Google Cloud Console (pode ser um projeto pessoal), crie um OAuth client do tipo *Web application*.
   Na tela de consentimento, use *External* em modo *Testing* e adicione a sua conta como *test user*.
   Assim só contas cadastradas conseguem passar pelo Google.
2. Em *Authorized redirect URI*, coloque `<CODE_INDEX_PUBLIC_URL>/auth/callback`
   (local: `http://127.0.0.1:8765/auth/callback`).
3. No `.env`, preencha `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` e quem pode entrar:
   - `CODE_INDEX_ADMIN_EMAILS`: admins, que entram com todos os scopes;
   - `GOOGLE_ALLOWED_EMAILS`: outras contas, que entram só com leitura;
   - `GOOGLE_ALLOWED_DOMAIN` (opcional): um domínio Google Workspace inteiro, validado pelo claim `hd`.

   Sem nenhuma dessas configurações, ninguém entra.

Depois do login, o `/docs` funciona como painel: as chamadas usam o cookie de sessão.

## Arquitetura

```
src/code_index/
  settings.py        Settings (pydantic-settings: env/.env) + bootstrap() de runtime
  domain/            puro, sem I/O: models, file_patterns (o que indexar), policies (lote, nomes)
  infra/             mundo externo: db (pool + migrations sql/NNN_*.sql), repo_store, chunk_store,
                     config_file (repos.toml), git, github, llm (LiteLLM: embed/rerank)
  sources/           de onde vêm os repos (RepoSource): github_org, wikijs (docs via GraphQL)
  indexing/          content (máscara/skip), code_flow (app CocoIndex), orchestrator (sync em etapas)
  retrieval/         search (híbrida + rerank), files (leitura com máscara)
  services.py        casos de uso: ÚNICO ponto usado pelas interfaces
  interfaces/        cli.py e http/ (app FastAPI, mcp em /mcp, security): só parsing/formatação
  domain/auth.py     scopes e regras de emissão de token (puro)
  infra/auth_store   tokens (hash HMAC) e auditoria; infra/google_oidc: login Google
  domain/doc_links   apelidos de repos (manifestos) e menções das páginas; infra/doc_link_store: vínculos
```

Regras:
- **Direção das dependências:** `interfaces → services → indexing/retrieval/sources → infra → domain/settings`.
  O `domain` não importa nada do projeto.
- **Configuração:** sempre via `Settings` injetado. A única exceção é o lifespan do CocoIndex, que usa
  `get_settings()`.
- **Núcleo assíncrono:** há um só `asyncio.run` por comando, na CLI.
- **Schema da tabela `repos`:** evolui com uma migration nova em `infra/sql/` (`NNN_descricao.sql`,
  placeholder `{schema}`). A tabela `code_chunks` é gerenciada pelo CocoIndex.
- **Nova fonte de dados** (ex.: OpenAPI): crie um `RepoSource`/fluxo próprio em `sources/` e `indexing/`
  e exponha pelo `services.py`.
- Mudar `indexing/code_flow.py` faz o CocoIndex revarrer e re-chunkar, mas os embeddings ficam no cache
  pelo texto: só textos novos vão para a Voyage. Mantenha estáveis o nome do app, as ContextKeys, o
  subpath `("repo", nome)` e o schema da tabela.

## Desenvolvimento

```bash
uv run pytest -q                      # unitários + integração (a integração é pulada sem o Postgres)
uv run pytest -q -m "not integration" # só unitários
uv run mypy src
```

Os testes de integração usam o schema `code_index_test`, pastas temporárias e embeddings/rerank falsos.
Não tocam no índice real e não chamam a Voyage. Para testar o caminho BM25 contra outro Postgres (por
exemplo, a imagem `docker/postgres` numa porta livre), use
`CODE_INDEX_TEST_PG_URL=postgres://...:5433/... uv run pytest -q -m integration`.

### Postgres com BM25 (`pg_search`)

O `docker-compose.yml` constrói `docker/postgres`: a mesma `pgvector/pgvector:pg17` de antes, mais a extensão
`pg_search` do ParadeDB (`.deb` oficial com SHA-256 conferido, carregada em `shared_preload_libraries`).
O volume e a versão do pgvector não mudam. Para trocar a imagem do seu banco:

```bash
docker compose build postgres && docker compose up -d postgres   # recria o container com o mesmo volume
```

O servidor cria a extensão e o índice BM25 sozinho, na inicialização e após cada sync. Sem a extensão,
a busca por termos usa o full-text do Postgres.

## Notas

- Trocar `EMBED_MODEL`/`EMBED_DIMENSIONS` invalida o cache de embeddings automaticamente:
  o próximo `sync` reembeda tudo (e cobra de novo na Voyage).
- Estado incremental do CocoIndex fica em `.cocoindex/` (LMDB). Se apagá-lo, rode `sync --full`.
- Todos os comandos (inclusive `repo add` e `show`) precisam do Postgres no ar.
- Tabela: `code_index.code_chunks (repo, path, id, language, code, start_line, end_line, embedding)`.
