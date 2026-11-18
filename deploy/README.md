# Deploy no Kubernetes

Desenho: Ingress → Deployment `api` (N réplicas, sem estado) → Postgres (CloudNativePG);
StatefulSet `worker` (1 réplica, `code-index worker`) executa a fila de sync; Job de migrations
como hook do Helm. A api não roda o worker embutido (`CODE_INDEX_SYNC_WORKER_EMBEDDED=false`).

| Volume | Modo | Quem monta |
|---|---|---|
| `repos` (clones, `.repos`) | RWX | worker leitura/escrita, api **só leitura** |
| `state` (CocoIndex/LMDB) | RWO, volumeClaimTemplate | só o worker. **Nunca em NFS** |

## 1. Imagens

```bash
docker build -t registry.example.com/code-index:0.1.0 .
docker build -t registry.example.com/code-index-postgres:pg17-pgsearch0.26.5 docker/postgres
docker push ...   # as duas
```

A imagem da aplicação roda como não-root pelo `securityContext` do chart (uid 10001, raiz só leitura,
`HOME=/tmp`).

## 2. Postgres (CloudNativePG)

Com o operador instalado, ajuste `imageName` e o storage em `deploy/cnpg/cluster.yaml` e aplique no
mesmo namespace da release:

```bash
kubectl -n code-index apply -f deploy/cnpg/cluster.yaml
```

O CNPG carrega `pg_search` (`shared_preload_libraries`), cria `vector`/`pg_search` no banco e gera o
Secret `code-index-db-app` (chave `uri`), que o chart usa por padrão.

## 3. Segredos

Os que hoje ficam no `.env`. Em produção, crie o Secret fora do Helm e use `secrets.existingSecret`:

```bash
kubectl -n code-index create secret generic code-index-secrets \
  --from-literal=CODE_INDEX_AUTH_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  --from-literal=VOYAGE_API_KEY=... \
  --from-literal=GITHUB_TOKEN=... \
  --from-literal=GOOGLE_CLIENT_SECRET=...      # e/ou AZURE_CLIENT_SECRET, WIKIJS_TOKEN
```

Só inclua as chaves que usar (chave vazia vira valor vazio, não "ausente"). O resto da configuração
(e-mails de admin, client IDs, domínios permitidos, modelos) vai em `config` no `values.yaml`.

A api recebe o Secret inteiro. O worker recebe só as chaves de `worker.secretKeys` (Voyage, GitHub,
Wiki.js): ele clona e lê conteúdo não confiável, e nunca vê `CODE_INDEX_AUTH_SECRET` (chave da sessão e dos
hashes dos tokens) nem os segredos do login.

Wiki.js: para cadastrar wikis pela interface/API (`POST /api/v1/docs/sources`), defina
`config.CODE_INDEX_WIKIJS_ALLOWED_HOSTS` (ex.: `wiki.empresa.com`). Sem a lista, o cadastro pela API é
recusado (o `WIKIJS_TOKEN` do servidor iria no header de qualquer URL); com ela, o token só vai para esses
hosts, inclusive no sync.

## 4. Instalação

```bash
helm upgrade --install code-index deploy/helm/code-index -n code-index \
  --set image.repository=registry.example.com/code-index --set image.tag=0.1.0 \
  --set publicHost=code-index.empresa.com \
  --set secrets.existingSecret=code-index-secrets \
  --set repos.storageClass=<storageclass RWX> \
  --set config.CODE_INDEX_ADMIN_EMAILS=voce@empresa.com \
  --set config.GOOGLE_CLIENT_ID=... --set config.GOOGLE_ALLOWED_DOMAIN=empresa.com
```

Valores principais:

- `publicHost`/`publicScheme`: viram `CODE_INDEX_PUBLIC_URL` e entram em `CODE_INDEX_ALLOWED_HOSTS`
  (junto com os nomes do Service interno, usados pela CLI remota). O redirect do login (Google/Microsoft)
  é `https://<publicHost>/auth/callback`: cadastre-o no provedor.
- `oauthRedirectUris`: `CODE_INDEX_OAUTH_REDIRECT_URIS` (ex.: callback do claude.ai).
- `api.replicas`, `api.hpa.*`, `api.forwardedAllowIps` (IPs do Ingress Controller confiáveis para
  `X-Forwarded-*`; o padrão `*` conta com a NetworkPolicy, restrinja ao CIDR dos pods se puder).
- `repos.storageClass` (RWX, confira qual existe no cluster), `repos.size`, `repos.existingClaim`.
- `worker.cocoindex.storageClass`/`size`: disco de bloco RWO.
- `ingress.*`: TLS e anotações; `/mcp` tem um Ingress próprio sem buffering e com timeout de 1 h
  (anotações do ingress-nginx; troque se usar outro controller).
- `networkPolicy.*`: só api, worker e migrations falam com o Postgres (o Cluster do CNPG precisa estar
  no mesmo namespace); saída liberada na 443 para Voyage, GitHub, Wiki.js e login. Ajuste o
  namespace do Ingress Controller e do operador do CNPG.
- `database.existingSecret`/`key` ou `database.url`.

O Job de migrations (`code-index db migrate`) roda antes de cada install/upgrade; api e worker só sobem
depois dele. Ele roda antes do ConfigMap existir, então recebe o mapa `config` direto no `env` (ex.:
`PG_SCHEMA`, se você trocar o schema). O Cluster do CNPG e o Secret do banco precisam existir antes do
`helm install`.

O Ingress principal tem `proxy-read-timeout` de 30 min: `POST /api/v1/scans/{kind}?wait=true` (usado por
`code-index graph|tests scan` em modo remoto) e `POST /api/v1/enrich/facts` esperam o trabalho na própria
requisição.

## 5. Primeiro acesso e primeiro token

1. Abra `https://<publicHost>` e entre com um e-mail de `CODE_INDEX_ADMIN_EMAILS`.
2. Cadastre as fontes na aba **Fontes** e peça a indexação na aba **Indexação** (o worker executa; custa
   tokens da Voyage).
3. Tokens `cix_...` para agents e CLI são emitidos pela sessão no navegador, com scopes que a pessoa já tem.
   Clientes MCP podem usar o OAuth (`https://<publicHost>/mcp`).

### Sync periódico (opcional)

Emita na interface um token com scope `index:write` e guarde-o:

```bash
kubectl -n code-index create secret generic code-index-sync-token --from-literal=token=cix_...
helm upgrade code-index deploy/helm/code-index -n code-index --reuse-values --set syncCron.enabled=true
```

O CronJob roda `code-index sync` em modo remoto (`CODE_INDEX_URL` = Service interno,
`CODE_INDEX_TOKEN`): só pede o job à api; quem indexa é o worker.

## Operação

- Logs: `kubectl logs deploy/code-index-api`, `kubectl logs sts/code-index-worker`.
- O worker devolve o job em andamento à fila no SIGTERM (`worker.terminationGracePeriodSeconds`).
- **Trava do sync:** advisory lock no Postgres (`code_index.sync.<schema>`), numa conexão reservada durante
  o sync; vale entre pods e para a CLI (`kubectl exec`). O pool liga keepalive TCP no servidor: se o nó do
  worker cair, a sessão (e a trava) some em ~2 min. Se a conexão da trava cair no meio, o sync percebe em até
  30 s, é interrompido e o job volta à fila (o trabalho já entregue a uma thread do CocoIndex termina antes).
  Cada sync ocupa uma conexão do pool (padrão do asyncpg: 10) durante toda a execução.
- **Remoções pela api:** a api monta os clones só para leitura. Remover repo, org ou fonte de documentação
  pela interface/API apaga a configuração e as linhas do banco na hora; as pastas ficam, e o worker apaga as
  órfãs (clone git ou pasta de docs que não estão mais na configuração nem na tabela de repos) no fim do
  próximo sync.
- **Leituras dos clones** (`POST /api/v1/scans/{kind}`) e `vectors embed` rodam no pod da api que recebeu o
  pedido, e o estado `running`/`last_run` é desse processo. Uma leitura de cada tipo por vez em todo o
  cluster (advisory lock); `wait=true` é o caminho confiável com várias réplicas.
- **Rate limit:** contado no Postgres (tabela UNLOGGED `rate_limits`, janela fixa), igual em todas as
  réplicas; volta vazio depois de failover. Se a contagem passar de `CODE_INDEX_RATE_DB_TIMEOUT` (0,5 s), a
  requisição passa (fail-open).
- Volume RWX em NFS: o `fsGroup` pode ser ignorado; o uid 10001 precisa poder gravar na raiz do volume.
- `readOnlyRootFilesystem`: se alguma biblioteca gravar fora de `/tmp`, `/data` ou `HOME`, desligue em
  `securityContext`.
- `helm uninstall` mantém o PVC dos clones (`helm.sh/resource-policy: keep`) e o do CocoIndex
  (volumeClaimTemplate). Apagar o do CocoIndex força reprocessar tudo.
