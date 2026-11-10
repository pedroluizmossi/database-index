# code-index

Indexador de código para repositórios git/GitHub usando [CocoIndex](https://cocoindex.io) 1.x,
embeddings **Voyage Code 4** (via LiteLLM) e **Postgres + pgvector**. Consulta por CLI ou MCP.

```
config (banco) ──git clone/fetch──▶ .repos/<nome> ──CocoIndex──▶ chunks (tree-sitter)
                                                   │
                                                   ▼
                     voyage-code-4 (input_type=document) ──▶ pgvector (HNSW) + índice full-text (GIN)
                                                                       │
                       CLI `code-index search` / MCP `search_code` ◀───┘  busca híbrida (RRF)
```

- **Incremental**: após um `git pull`, só arquivos alterados são reprocessados e só chunks novos
  geram chamadas à API da Voyage. Remover um repo (`repo remove`) apaga suas linhas no próximo sync.
- **Busca híbrida**: vetor (semântica) + full-text (identificadores), combinados por Reciprocal Rank Fusion.
- **Repositórios privados**: usa `GITHUB_TOKEN` ou, se ausente, `gh auth token`. O token só vai no
  header HTTP do comando git; nunca é gravado em `.git/config`.

## Setup

```bash
docker compose up -d          # Postgres 17 + pgvector em localhost:5432
cp .env.example .env          # preencha VOYAGE_API_KEY
uv sync
```

### Configuração (orgs, repos avulsos, fontes de documentação)

Fica no banco (tabela `config_entries`), e não em arquivo: CLI, API e interface web gravam no mesmo lugar, e
o worker vê a mudança sem reiniciar. Cadastre com `repo add`, `org add`, `docs add`/`wiki add` ou pela interface.
Repos que saírem da configuração somem do índice no próximo sync.

### Servidor no Docker (opcional)

O serviço `app` do compose (perfil `app`) roda `code-index serve` em `127.0.0.1:8765`, com a interface web
já construída. Ele lê os segredos do `.env` e usa o banco do serviço `postgres` (o `POSTGRES_URL` do `.env`
é ignorado).

```bash
docker compose --profile app up -d --build      # banco + servidor
docker compose --profile app exec app code-index token list   # CLI dentro do container
```

A pasta do projeto é montada no container **no mesmo caminho do host**: `.repos/` e `.cocoindex/` são os mesmos
do `uv run`, sem reprocessar nada. Rode o compose da raiz do projeto. Não há `gh`
na imagem: para repos privados, defina `GITHUB_TOKEN` no `.env`. Não rode `serve` no host ao mesmo tempo
(mesma porta).

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
uv run code-index org refresh [minha-org]                  # redescobre: repos novos, removidos e pushes
uv run code-index org remove minha-org --keep-clones       # tira a org (os chunks somem no próximo sync)

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

Use `--exclude` em `repo add` ou `org add` para somar padrões (`extra_exclude` na configuração).

Arquivos descartados na indexação (acima do limite de tamanho ou binários) ficam registrados com o motivo:

```bash
uv run code-index skips [repo] [--limit 200] [--json]
```

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
- **vetor** (pgvector HNSW), com o embedding do trecho mais um cabeçalho de contexto. O HNSW examina
  `SEARCH_HNSW_EF_SEARCH` vizinhos (padrão 400; com os 40 do pgvector, perdia resultados). Quando os filtros
  (repos, domínio, `kind`, linguagem, caminho) deixam até `SEARCH_EXACT_MAX_CHUNKS` trechos, a busca vetorial é
  exata, sem o índice: com filtro seletivo o HNSW devolve vizinhos errados;
- **termos**, com tokenização própria para código (`domain/search_text.py`): `HmacServiceImpl` vira
  `hmacserviceimpl hmac service impl`, então a busca acha o nome exato e pedaços dele. Nomes de símbolos e
  o caminho pesam mais que o corpo do código. O motor é escolhido conforme o banco:

  | Motor | Quando |
  |---|---|
  | `bm25` | o banco tem `pg_search` (imagem `docker/postgres`) e o índice já tem as colunas de busca |
  | `fts` | Postgres comum: full-text nativo nas mesmas colunas, com pesos A/B |
  | `fts-legacy` | tabela antiga, antes de reindexar: full-text sobre o código |

Os pesos da fusão dependem da consulta: frases favorecem o vetor (1,0 contra 0,7); consultas que parecem um
identificador (`HmacServiceImpl`, `get_user`, `Classe.metodo`) favorecem os termos (1,0 contra 0,6). A
constante do RRF é 10, não o 60 usual (`RRF_K` em `domain/search_text.py`): quase sempre uma das fontes acerta
e a outra é ruído, então o 1º lugar de uma fonte precisa valer mais que uma posição mediana nas duas (sem
rerank, MRR de 0,74 para 0,80). Os 40
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

**Avaliação.** `evals/queries.json` tem 150 perguntas com a resposta conhecida (repo, caminho e linha
opcional), a maioria sem filtro de repo. Cada pergunta pode levar os filtros da busca (`repos`, `kind`,
`language`, `path_glob`). `code-index eval` mede hit@1, hit@3, hit@k e MRR em cada modo de busca, e `--grid` compara os
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
uv run code-index docs preview docs__wiki        # páginas que entrariam, por raiz (sem baixar conteúdo)
uv run code-index sync --dry-run                 # mostra o que entraria (custo zero)
uv run code-index index docs__wiki               # baixa as páginas e indexa
uv run code-index docs links snr-email-backend   # páginas ligadas ao repo, e por quê
uv run code-index docs remove wiki
```

Vínculos gravados à mão (sem reindexar) e páginas fora da busca:

```bash
uv run code-index docs link wiki Plataforma/Email snr-email-backend --reason "descreve o serviço"
uv run code-index docs unlink wiki Plataforma/Email snr-email-backend
uv run code-index docs vinculos [--source wiki] [--repo snr-email-backend]   # vínculos gravados
uv run code-index docs ignore wiki Rascunhos/Velho --reason "obsoleta"      # some dos resultados
uv run code-index docs unignore wiki Rascunhos/Velho
```

**Escolher as páginas pela interface.** Em Documentação → Fontes, o admin abre **Escolher páginas**: a árvore
da wiki (só a lista, nada é baixado) mostra o que está na seleção, o que já está no índice, o que entra e o que sai.
Ao salvar, o servidor converte a escolha em `include`/`exclude` da fonte (`PUT /api/v1/docs/{nome}/selection`;
a prévia das regras sai com `dry_run`) e confere que as regras reproduzem exatamente a escolha. Pasta marcada
inteira vira `Pasta` + `Pasta/**`, então páginas criadas nela depois também entram; seções novas na raiz da wiki
nunca entram sozinhas. Salvar não indexa: o custo só vem no próximo sync da fonte (botão **Indexar…**, com prévia).
Na aba Páginas, o admin também tira uma página da busca (e a devolve) sem tirá-la do índice.

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

A aba **APIs** permite listar domínios e serviços, ver uma prévia e iniciar a indexação (admin); o andamento e o
histórico do job ficam na aba **Indexação**.
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
Repos com arquivo `.sdl` (plataforma Senior) também são ligados pela declaração `domain`/`service`
(origem **SDL**), mesmo com nome diferente do serviço. Isso vem dos fatos do `code-index enrich facts`.

**Onde cada operação é implementada.** Pela convenção do SDK, a operação `listBlacklistedEmails` do `.sdl`
é tratada pela classe `ListBlacklistedEmailsHandler` (`login__9` → `LoginV9Handler`). O `enrich facts`
casa operação e classe sem LLM (cerca de 80% das operações) e grava o resultado. Nos resultados de busca
de uma página de API aparece `implemented_in` (repo/arquivo:linha); na interface, "implementado em".

```bash
uv run code-index api impl email.listBlacklistedEmails     # ou só a operação, ou 'POST /email/queries/...'
```

API: `GET /api/v1/api-impls?operation=...`. MCP: `find_api_impl`.

No **Raio X**, escolher um repositório abre a **ficha do repo**: tudo o que o índice sabe sobre ele numa
página só. São sete seções: o que é (cartão, capacidades, dono, assuntos), por onde começar (entrypoints do
cartão), APIs (operações do `.sdl` com o handler localizado), arquivos (a árvore indexada com o resumo de cada
arquivo de código, filtro por caminho, resumo ou palavra-chave), documentação ligada, repos relacionados e o
estado do enriquecimento (cartão, cobertura dos resumos, índice), com o comando para completar o que falta.
A árvore vem de `GET /api/v1/repos/{nome}/files`.

Na interface, **Raio X** permite navegar entre repos, documentos e serviços de API indexados. O vínculo
de cada item ao repo mostra sua origem. Relações entre documentos e APIs por um repo em comum são
indicadas como inferidas; não comprovam que o documento descreva aquela API. A consulta usa o índice
existente e não gera embeddings.

### Cartão do repo (enriquecimento offline por agents)

Um resumo por repo (o que faz, capacidades, tags, repos relacionados) escrito por um agent em modelo
barato (Claude Code com Haiku, opencode, kiro-cli...). O `code-index` não chama LLM: ele coleta os dados,
entrega uma tarefa por vez e valida o que volta. Detalhes e próximos passos em `ENRIQUECIMENTO.md`.

```bash
uv run code-index enrich facts            # fatos sem LLM: catalog-info, manifestos, .sdl, pastas (custo zero)
uv run code-index enrich next             # reserva um repo e imprime instruções + dados para o agent
uv run code-index enrich submit <id> -f cartao.json   # valida e grava (erros legíveis se recusar)
uv run code-index enrich status           # progresso e reservas ativas
uv run code-index enrich show <repo>      # fatos e cartão
uv run code-index enrich release <id>     # devolve uma tarefa à fila
```

Prompt para o agent: *"Rode `uv run code-index enrich next`, siga as instruções e repita até não haver
tarefas (no máximo 20)."* Vários agents em paralelo não pegam o mesmo repo (reserva de
`ENRICH_LEASE_MINUTES`, padrão 20). Libere para o agent só `uv run code-index enrich|show|symbol|grep`:
o conteúdo dos repos não é confiável. O cartão volta para a fila quando o material do repo muda.

Onde o cartão aparece (sem custo de API: full-text do Postgres sobre os cartões e os fatos do SDL):

```bash
uv run code-index repo find "quem cuida da blocklist de e-mail"   # qual repo trata do assunto
uv run code-index repo list email -c                               # lista com resumo e tags
```

- API: `GET /api/v1/repos` traz `summary`, `tags` e `card_stale`; `GET /api/v1/repos/find?q=...` e
  `GET /api/v1/repos/{nome}/card`.
- MCP: `list_repos` (resumo na 1ª frase), `find_repos` e `repo_card`.
As páginas da wiki também podem ser revisadas por agents (mesma fila, outro tipo de tarefa). Cada
tarefa é um lote de até 8 páginas da mesma pasta, com os vínculos atuais de cada página e repos
candidatos achados pelo cartão. O agent sugere repos novos e dá um veredito (manter/remover) para cada
vínculo automático por menção, tag ou link. Tudo entra como **sugestão** e só vale depois de aprovado
(aba Documentação, ou pela CLI): uma remoção aprovada faz a página deixar de ser ligada àquele repo.
Rejeitar desfaz e a sugestão não volta. Vínculos gravados à mão e regras `--link` não são contestados.

```bash
uv run code-index enrich next --kind doc_links      # lote de páginas (todas, padrão) → o agent sugere
uv run code-index enrich next --kind doc_links --escopo varios       # ou: sem-vinculo | varios (2+ repos)
uv run code-index docs sugestoes [--confianca alta] [--acao add|remove]   # revisar
uv run code-index docs aprovar <doc_repo> <caminho> <repo>   # ou: docs aprovar --confianca alta [--acao add]
uv run code-index docs rejeitar <doc_repo> <caminho> <repo>
```

A qualidade dos candidatos depende dos cartões: rode a fila de cartões antes da de páginas.

**Resumos de arquivos de código (busca em português sobre código em inglês).** A busca por termos não casa
"navegador" com `browser`. Na fila `file_summaries`, um agent descreve cada arquivo de código em português
(resumo, três perguntas e palavras-chave); o `submit` valida e grava em `file_summaries`, e o resumo entra nas
colunas de busca por termos dos trechos do arquivo. O embedding não muda: **não há custo de Voyage** nem
chamada de API de modelo pelo projeto. Arquivo alterado volta para a fila (o resumo antigo continua valendo
até lá); o sync reaplica os resumos sozinho.

```bash
uv run code-index enrich next --kind file_summaries [--repo <repo>]   # lote de até 10 arquivos + instruções
uv run code-index enrich submit <id> -f resumos.json                  # valida, grava e atualiza a busca
uv run code-index enrich status                                       # resumos em dia / desatualizados / faltando
uv run code-index enrich apply-summaries                              # reaplica (o sync e o submit já fazem)
```

Laço sem interação com um CLI local (aqui o Claude Code com Haiku, sem ferramentas: o modelo só lê o texto
e devolve o JSON). Uma entrega recusada fica reservada até o lease expirar e o laço segue para o próximo lote:

```bash
while t=$(uv run code-index enrich next --kind file_summaries) && [ "$t" != "nenhuma tarefa pendente." ]; do
  id=$(printf '%s' "$t" | head -1 | awk '{print $2}')
  printf '%s' "$t" | claude -p --model haiku --tools "" | uv run code-index enrich submit "$id" --agent haiku-cli
done
```

**Enriquecer de outra máquina.** A fila também está na API, atrás do scope `enrich:write` (não dá busca nem
sync; admins o têm e podem emitir um token só com ele na aba Tokens): `POST /api/v1/enrich/next`,
`POST /api/v1/enrich/tasks/{id}/submit` (o corpo é o JSON que o modelo produziu), `.../release` e
`GET /api/v1/enrich/status`. O `scripts/enrich_worker.py` faz o laço sozinho: só precisa de Python e de um CLI
de modelo, sem o projeto instalado e sem acesso ao banco. Vale para cartões, vínculos e resumos.

```bash
export CODE_INDEX_TOKEN=cix_...        # token com enrich:write
python enrich_worker.py --server https://indice.exemplo.com --kind file_summaries [--repo <repo>] [--max N]
python enrich_worker.py --server https://indice.exemplo.com --kind card --cmd 'claude -p --model haiku --tools ""'
```

O sync continua sendo só do servidor: os clones (`.repos/`) e o estado do CocoIndex (`.cocoindex/`) ficam na
máquina dele, e uma segunda máquina escrevendo no mesmo banco reprocessaria tudo. De fora, dispare o sync pela
API (`POST /api/v1/sync`, scope `index:write`).

Onde os resumos aparecem, sempre com a mesma marca (ícone de brilho, violeta): no resultado da busca (faixa
"Resumo" acima do trecho, com as palavras-chave), no painel do arquivo aberto (resumo, perguntas que o arquivo
responde e aviso quando o arquivo mudou depois do resumo) e na aba Repositórios (atalho "Resumos N%", que abre a
lista dos resumos do repo com filtro). Na API: o campo `summary` dos resultados de `/api/v1/search` (e do
`search_code` no MCP), `GET /api/v1/summaries/file/{repo}/{caminho}`, `/api/v1/summaries/repo/{repo}` e
`/api/v1/summaries/coverage`.

Medido num piloto de 16 repos (961 arquivos), nas 28 perguntas do `eval` com resposta neles: MRR com rerank de
0,83 para 0,98 e, só por termos, de 0,42 para 0,71; nas outras perguntas, nada mudou com rerank.

- Interface: resumo e botão **Cartão** na aba Repositórios (o filtro também procura no resumo e nas tags);
  na Busca, o quadro "Repos que tratam do assunto" sugere o filtro de repo.

## Servidor HTTP: API REST + MCP

`code-index serve` sobe num único processo:
- a API REST em `/api/v1` (documentação interativa em `/docs`);
- o login no navegador (Google e/ou Microsoft) em `/auth`;
- o MCP (Streamable HTTP) em `/mcp`, com OAuth: o cliente MCP abre o navegador e recebe o token sozinho;
- o worker da fila de sync (embutido; `CODE_INDEX_SYNC_WORKER_EMBEDDED=false` desliga).

Não existe mais MCP via stdio.

Um sync pedido pela API ou pela interface entra numa fila durável no Postgres (`sync_jobs`) e é executado por um
worker: o embutido no `serve` ou um processo separado (`uv run code-index worker`, que encerra limpo com
SIGINT/SIGTERM devolvendo o job à fila). Com o worker separado, desligue o embutido e reimplante a API à vontade
durante uma indexação. Só um job fica ativo (na fila ou rodando) por vez: outro pedido recebe 409. O `code-index
sync` da CLI continua rodando direto, em primeiro plano; enquanto ele segura a trava, o worker deixa o job na fila.

```bash
# .env: CODE_INDEX_AUTH_SECRET (obrigatório), GOOGLE_* e/ou AZURE_*, CODE_INDEX_ADMIN_EMAILS, CODE_INDEX_PUBLIC_URL
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

Páginas por tipo de trabalho: **Busca**, **Repositórios** (catálogo: resumo, cartão, documentação ligada, domínio
DDD, e a ação "Indexar" por repo), **Documentação**, **APIs** e **Raio X** para todos; **Indexação** (com
`index:write`) reúne a operação do índice: job ativo (na fila ou rodando, tentativa, worker e andamento por repo),
cobertura por status e org, "Indexar lote", "Atualizar descoberta" (admin), atalhos para repos com erro/push novo
(abrem Repositórios já filtrado, ex.: `#repos?status=error` ou `#repos?push=novo`) e o histórico de jobs de todas
as fontes. O indicador de indexação no menu lateral abre essa página.

### Autenticação e permissões

| Quem | Como entra | Scopes |
|---|---|---|
| Pessoa autorizada pelo Google (e-mail em `GOOGLE_ALLOWED_EMAILS`/admins, ou domínio Workspace em `GOOGLE_ALLOWED_DOMAIN`) | `GET /auth/login?provider=google` (OIDC + PKCE; valida assinatura, `nonce` e `email_verified`) → cookie de sessão | `search:read`; admins: todos |
| Pessoa autorizada pela Microsoft (tenant `AZURE_TENANT_ID`; e-mail em `AZURE_ALLOWED_EMAILS`/admins, ou `AZURE_ALLOWED_DOMAIN`) | `GET /auth/login?provider=microsoft` (OIDC + PKCE; valida assinatura, `nonce`, `iss` e `tid` do tenant; recusa guests) → cookie de sessão | `search:read`; admins: todos |
| Cliente MCP conectado por OAuth | o cliente abre o navegador → login → consentimento → token `cix_` (kind `oauth`) + refresh token | só `search:read` |
| Agent / automação | `Authorization: Bearer cix_...` | os do token |

Com Google e Microsoft configurados, `/auth/login` mostra a escolha. A pessoa é identificada pelo e-mail: a
mesma pessoa vê os mesmos tokens entrando por qualquer um dos dois.

**Scopes:**
- `search:read`: busca, leitura de arquivos e listagens, inclusive todo o MCP;
- `index:write`: sync e reindex, que geram custo na Voyage;
- `admin`: gerencia orgs, repos e auditoria, e implica os demais scopes.

**Tokens de agent:**
- O valor é mostrado só uma vez. No banco fica apenas o HMAC-SHA256 com `CODE_INDEX_AUTH_SECRET`.
- Têm validade (padrão de 90 dias) e podem ser revogados.
- **Só pessoas logadas no navegador emitem tokens** (`POST /api/v1/tokens`), e só com scopes que elas já têm.
  Um token (de agent ou OAuth) nunca emite outro.
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
  resultado e erro (`queued` → `running` → `done`/`failed`). O worker reserva o job com um lease renovado a cada
  1/3 de `SYNC_JOB_LEASE_SECONDS` (padrão 120). Se o worker ou o servidor cair, o lease vence e o job volta à fila
  para ser retomado (o sync é incremental); só falha de vez depois de `SYNC_JOB_MAX_ATTEMPTS` (padrão 3) tentativas.
- **Rate limit por token:** leitura 120/min, busca 30/min, sync 6/hora (configurável).
- **Auditoria:** fica em `code_index.audit_log`, com quem, ação, parâmetros, status e IP. Nunca grava
  tokens nem o conteúdo retornado. Admins consultam em `GET /api/v1/audit`.
- **Segredos mascarados:** respostas de busca e de leitura de arquivo saem com a mesma máscara do índice.

### Usuários e uso

Toda credencial pertence a um usuário (`code_index.users`): quem entra pelo navegador e quem recebe um token
pela CLI. Um admin (logado no navegador) pode **desativar** um usuário na aba **Usuários**: na hora, nenhuma
credencial dele vale (sessão, tokens de agent e clientes OAuth), e ele não consegue entrar de novo até ser
reativado.

Cada chamada de tool do MCP e cada requisição à API vira um evento em `code_index.usage_events`, com qual usuário,
canal (`mcp`, `api` ou `ui`), cliente (ex.: `claude-code`, pelo `clientInfo` ou `User-Agent`), tempo, itens
devolvidos, tamanho da resposta (nas tools do MCP, vira o consumo de contexto do agent: ~bytes/4 tokens) e consumo da Voyage (tokens de
embedding/rerank e custo estimado pelo LiteLLM). Nunca grava tokens nem o conteúdo devolvido.

- **Dashboard** (aba **Uso**, só admin; as leituras do próprio painel não contam): chamadas por dia e canal, consumo por tool, clientes, consultas mais
  frequentes e **consultas sem resultado** (o melhor sinal do que falta no índice). Cada usuário vê o próprio uso
  nas configurações.
- **Retenção:** `CODE_INDEX_USAGE_RETENTION_DAYS` (padrão 365); a limpeza roda ao subir o servidor e a cada 6 h.
  `CODE_INDEX_USAGE_TRACKING=false` desliga o registro.

```bash
uv run code-index usage [--days 30] [--email usuario@x.com] [--json]
uv run code-index user list [--days 30]
uv run code-index user disable usuario@x.com      # e `user enable`
```

### API REST (resumo)

| Método e rota | Scope |
|---|---|
| `GET /api/v1/me` | search:read |
| `POST /api/v1/search` · `GET /api/v1/files/{repo}/{path}` | search:read |
| `GET /api/v1/repos` · `GET /api/v1/orgs` | search:read |
| `POST /api/v1/sync` (enfileira o job; 409 se já há um ativo) · `GET /api/v1/sync/jobs` · `GET /api/v1/sync/jobs/{id}` | index:write |
| `POST/DELETE /api/v1/repos…` · `POST/PATCH/DELETE /api/v1/orgs…` · `GET /api/v1/audit` | admin |
| `GET/POST/DELETE /api/v1/tokens` | sessão do navegador (emitir); dono ou admin (revogar, inclusive clientes OAuth) |
| `GET /api/v1/me/usage` | search:read (o próprio uso) |
| `GET /api/v1/usage` · `GET /api/v1/users` | admin |
| `POST /api/v1/users/{email}/disable` · `…/enable` | admin com sessão do navegador |
| `GET /auth/providers` | público: provedores de login ativos |

### MCP no Claude Code

**Com login no navegador (OAuth, recomendado):** adicione o servidor sem token. O cliente descobre o login
(`/.well-known/oauth-protected-resource/mcp`), se registra, abre o navegador e, depois de você entrar com
Google ou Microsoft e clicar em *Permitir*, recebe o token e o renova sozinho.

```bash
claude mcp add --scope user --transport http code-index https://<host>/mcp
# no Claude Code: /mcp → code-index → Authenticate
```

O token OAuth só lê (`search:read`), vale `CODE_INDEX_OAUTH_ACCESS_TTL_HOURS` (8 h) e é renovado pelo refresh
token (`CODE_INDEX_OAUTH_REFRESH_TTL_DAYS`, 30 dias), que é trocado a cada uso: o reuso de um refresh antigo
derruba a conexão inteira. Cada renovação confere de novo a allowlist do login e se a pessoa está ativa: quem
saiu de `GOOGLE_ALLOWED_EMAILS`/`AZURE_ALLOWED_EMAILS` (ou do domínio) perde a conexão na próxima renovação.
Para desconectar um cliente, revogue-o na aba *Tokens* (aparece como "conectado via OAuth", mesmo parado há
dias, enquanto o refresh valer). Clientes cujo retorno não é `http://localhost`/`127.0.0.1` (ex.: conectores web)
precisam do prefixo em `CODE_INDEX_OAUTH_REDIRECT_URIS`: mesmo esquema e host:porta, e o caminho do prefixo
fecha num segmento (`https://site/cb` aceita `/cb` e `/cb/x`, não `/cbx` nem `https://site@outro`).

O `.mcp.json` deste projeto já aponta para o servidor local (`http://localhost:8765/mcp`, ou `CODE_INDEX_URL`
se definida) sem token: entre pelo `/mcp` → code-index → Authenticate.

**Com token colado:** os tokens de agent também servem para o MCP. Para usar em qualquer projeto:

```bash
claude mcp add --scope user --transport http code-index https://<host>/mcp \
  --header "Authorization: Bearer cix_..."
```

Ferramentas, todas com `search:read`: `search_code` (por significado; `kind` e `include_linked_docs` como
na API), `find_symbol` (onde algo é
declarado), `grep_code` (texto exato ou regex), `read_file_lines`, `list_repos` (com o resumo do cartão),
`find_repos` (qual repo cuida de um assunto, pelo cartão), `repo_card` (cartão completo), `find_api_impl`
(operação → handler) e `related` (tudo ligado a um repo, página, operação ou arquivo). O MCP não indexa:
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

### Login Microsoft (Entra ID): configuração

Funciona junto com o Google (os dois botões aparecem) ou sozinho.

1. No portal do Azure, em *Microsoft Entra ID → App registrations → New registration*: contas *somente
   deste diretório* (single tenant); plataforma *Web*; redirect URI `<CODE_INDEX_PUBLIC_URL>/auth/callback`
   (o mesmo do Google). A Microsoft só aceita `http` em endereço local: para testar na máquina, use
   `http://localhost:8765/auth/callback` e `CODE_INDEX_PUBLIC_URL=http://localhost:8765`.
2. Em *Certificates & secrets*, crie um client secret. Anote o *Application (client) ID* e o
   *Directory (tenant) ID* (um GUID).
3. Em *API permissions*, mantenha `openid`, `email` e `profile` (Microsoft Graph, delegadas).
4. No `.env`: `AZURE_TENANT_ID` (o GUID, nunca `common`/`organizations`), `AZURE_CLIENT_ID`,
   `AZURE_CLIENT_SECRET` e quem pode entrar: `AZURE_ALLOWED_DOMAIN` (ex.: `empresa.com.br`) e/ou
   `AZURE_ALLOWED_EMAILS`. `CODE_INDEX_ADMIN_EMAILS` vale para os dois provedores.

Por que a regra é mais estrita que a do Google: no Entra o e-mail não tem `email_verified` e pode ser editado
pelo administrador de qualquer tenant ("nOAuth"). Por isso só um tenant é aceito (`iss` e `tid` exatos) e
contas convidadas (guest) são recusadas.

## Arquitetura

```
src/code_index/
  settings.py        Settings (pydantic-settings: env/.env) + bootstrap() de runtime
  domain/            puro, sem I/O: models, file_patterns, policies, auth, search_text, doc_links,
                     relations (chaves, tipos, origens), documents (tipos), repo_card, api_impl, doc_task
  infra/             mundo externo: db (pool + migrations sql/NNN_*.sql), repo_store, chunk_store (busca),
                     relation_store (relações), document_store (catálogo de documentos), enrich_store,
                     symbol_store, auth_store, config_store (configuração das fontes), git, github, grep, llm
  sources/           base.py (contratos RepoSource, DocSourcePlugin, Linker), registry.py (plugins),
                     github_org, wikijs, openapi_s3
  indexing/          content (máscara/skip), code_flow (app CocoIndex), orchestrator (sync em etapas),
                     linkers (núcleo + rebuild_relations), enrich (material do cartão)
  retrieval/         search (híbrida + rerank), files (leitura com máscara)
  services/          casos de uso, um módulo por área: ÚNICO ponto usado pelas interfaces
  interfaces/        cli/ (um módulo por grupo de comandos) e http/ (app.py monta; routes/ um APIRouter por área;
                     mcp em /mcp, security): só parsing/formatação
```

**Modelo de dados.** Toda fonte vira arquivos `.md` e passa pelo mesmo pipeline (chunks, embedding,
busca). Cada fonte de documentação é um plugin que também descreve o que materializou (tabela
`documents`: tipo e atributos, ex.: domínio/serviço/operação de uma página de API). Os vínculos são
**relações** (tabela `relations`) entre entidades com chave em texto:

| Relação | De → para | Origens |
|---|---|---|
| `documents` | `doc:<fonte>/<caminho>` → `repo:<nome>` | manual, agent, llm, regra, SDL, nome do serviço, tag, link, menção |
| `describes` | página de API → `op:<domínio>/<serviço>/<operação>` | fonte |
| `implemented_by` | operação → `code:<repo>/<caminho>:<linha>` | handler (`<op>Handler`) |
| `related_to` | `repo:` → `repo:` | cartão do repo |

Linkers recalculam as relações derivadas no fim de cada sync (menos de 1 s, sem custo). A view
`effective_relations` é a única leitura: aplica sugestões não aprovadas, remoções aprovadas e repos ou
páginas que saíram.

```bash
uv run code-index relations show snr-email-backend               # tudo ligado ao repo
uv run code-index relations show op:platform/email/sendEmail     # página da API, handler
uv run code-index relations stats                                # por tipo e origem
uv run code-index relations rebuild                              # recalcula (o sync já faz)
```

API: `GET /api/v1/relations?entity=...`, `GET /api/v1/relations/origins`, `POST /api/v1/relations/rebuild`.
MCP: `related`.

Regras:
- **Direção das dependências:** `interfaces → services → indexing/retrieval/sources → infra → domain/settings`.
  O `domain` não importa nada do projeto.
- **Configuração:** sempre via `Settings` injetado. A única exceção é o lifespan do CocoIndex, que usa
  `get_settings()`.
- **Núcleo assíncrono:** há um só `asyncio.run` por comando, na CLI.
- **Schema das nossas tabelas** (`repos`, auth, `documents`, `relations`, `repo_cards`...): evolui com uma
  migration nova em `infra/sql/` (`NNN_descricao.sql`, placeholder `{schema}`). `code_chunks`,
  `code_symbols`, `doc_pages`, `repo_aliases` e `doc_mentions` são do CocoIndex.
- **Nova fonte de documentação:** um plugin `DocSourcePlugin` em `sources/`, registrado em
  `sources/registry.py`. **Ligação nova:** um `Linker` (núcleo ou do plugin) e, se for uma origem nova, uma
  entrada em `domain/relations.ORIGINS`.
- Mudar `indexing/code_flow.py` faz o CocoIndex revarrer e re-chunkar, mas os embeddings ficam no cache
  pelo texto: só textos novos vão para a Voyage. Mantenha estáveis o nome do app, as ContextKeys, o
  subpath `("repo", nome)` e o schema da tabela.

### Domínio e serviço (DDD)

Cada repo (ou pasta de um monorepo) pode ter um dono: um domínio (`hcm`) ou serviço (`hcm/payroll`). Ele
filtra a busca (`--domain`/`--service` na CLI, `domain`/`service` na API e no MCP) e aparece em cada
resultado (`owners`). O catálogo vem dos `.sdl` (nome técnico e rótulo); não há lista oficial.

De onde vem o dono, do mais forte para o mais fraco (sem LLM e sem custo, recalculado em cada sync):

| Origem | Regra |
|---|---|
| `manual` | definido por uma pessoa (interface, API ou CLI); vence todas as outras |
| `declared` | `.sdl` do próprio repo; monorepo com um serviço por pasta ganha dono por pasta |
| `sibling` | mesmo nome-base de um repo com SDL (`x-frontend` herda de `x-backend`) |
| `api_calls` | o código chama, na maioria, as rotas `dominio/servico/{queries,actions,...}` de um serviço |
| `usage` | biblioteca usada só por repos de um domínio |

```bash
uv run code-index owner catalog                       # domínios, serviços e cobertura
uv run code-index owner list --sem-dono               # o que falta mapear
uv run code-index owner set snr-erp-legado --to financeiro --criar --motivo "ERP antigo"
uv run code-index owner set snr-erp --to hcm/payroll --path modulos/folha   # pasta de monorepo
uv run code-index owner clear snr-erp-legado          # volta ao inferido
uv run code-index search "cálculo de férias" --domain hcm --service vacation
```

Na interface, a tela de Repositórios mostra o dono e a origem, filtra por domínio ou "Sem dono" e permite
definir o dono de um repo ou de vários (seleção), inclusive de uma pasta. A atribuição exige `admin`.
Domínio/serviço fora do catálogo só com confirmação (`--criar` / `create`), para um erro de digitação não
virar um domínio novo. Os fatos usados na inferência vêm de `enrich facts` (rodado no fim de cada sync);
repo ainda não indexado só recebe dono por nome (repo irmão) ou manual.

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
