// Formatos de resposta da API (src/code_index/interfaces/http/app.py e domain/models.py).

export type RepoStatusValue = "indexed" | "pending" | "error" | "ignored";
export type SearchMode = "hybrid" | "semantic" | "keyword";

export interface Me {
  subject: string;
  kind: "agent" | "session" | "oauth";
  scopes: string[];
  token_name: string | null;
  /** Provedor do login ("google", "microsoft") quando a sessão é do navegador. */
  provider?: string | null;
}

export type PrefValue = string | number | boolean;

/** Uma config do registro do servidor (domain/preferences.py): a tela monta o formulário a partir daqui. */
export interface PrefSchema {
  key: string;
  label: string;
  type: "choice" | "int" | "bool" | "text";
  default: PrefValue;
  applies_to: "ui" | "search";
  choices?: { value: string; label: string }[];
  minimum?: number;
  maximum?: number;
}

/** GET/PATCH /api/v1/me/settings: valores já resolvidos (padrão → escolha da pessoa). */
export interface UserSettings {
  email: string;
  display_name: string | null;
  last_login_at: string | null;
  settings: Record<string, PrefValue>;
  /** Chaves que a pessoa alterou (as demais estão no padrão). */
  changed: string[];
  schema: PrefSchema[];
}

/** Provedor de login ativo no servidor (GET /auth/providers). */
export interface LoginProvider {
  name: string;
  label: string;
}

export interface Repo {
  name: string;
  org: string | null;
  status: RepoStatusValue;
  language: string | null;
  source: string | null;
  commit: string | null;
  indexed_at: string | null;
  pushed_at: string | null;
  outdated: boolean;
  files: number;
  chunks: number;
  error: string | null;
  /** Tamanho do repositório no GitHub, em KB (inclui o histórico do git). */
  size_kb: number | null;
  /** "code" (repo git) ou "docs" (wiki). */
  kind: string;
  /** Arquivos descartados na indexação (acima do limite de tamanho, binários...). */
  skipped: number;
  /** Resumo do cartão do repo (escrito por agent; `code-index enrich`), se houver. */
  summary: string | null;
  /** Tags de domínio do cartão. */
  tags: string[];
  /** O repo mudou desde que o cartão foi escrito. */
  card_stale: boolean;
  /** Domínio/serviço do DDD dono do repo ('vendas/pedidos'); vazio = sem dono. */
  owners: string[];
  owner_origin: OwnerOrigin | null;
  owner_confidence: string | null;
}

/** De onde veio o dono: `manual` vence, `usage` é a inferência mais fraca do núcleo e as do meio vêm dos perfis de
 * organização ativos no servidor. Rótulo, dica e ordem vêm de GET /api/v1/relations/origins (`useOrigins`). */
export type OwnerOrigin = string;

export interface OwnershipService {
  name: string;
  label: string;
  repos: number;
  declared_in: string[];
  /** Criado à mão (não vem do catálogo dos perfis de organização). */
  manual: boolean;
}

export interface OwnershipDomain {
  name: string;
  label: string;
  repos: number;
  services: OwnershipService[];
}

export interface OwnershipCatalog {
  domains: OwnershipDomain[];
  coverage: { repos: number; owned: number; percent: number; by_origin: Partial<Record<OwnerOrigin, number>> };
}

export interface OwnershipRow {
  repo: string;
  /** Pasta (monorepo) com dono próprio; null = o repo inteiro. */
  path: string | null;
  owners: string[];
  origin: OwnerOrigin | null;
  confidence: string | null;
  evidence: Record<string, unknown>;
  alternatives: { owner: string; origin: OwnerOrigin; confidence: string | null }[];
}

/** Grupo de um contrato (do núcleo: query, action, signal, event, entity). */
export type ContractGroup = string;

/** Serviço declarado num contrato de API, no formato genérico (`contracts` de GET /api/v1/repos/{name}/card). */
export interface ContractService {
  /** Rótulo do formato do arquivo, dado pelo perfil de organização que o lê (ex.: ".sdl" no perfil senior). */
  format: string;
  path: string;
  domain: string;
  service: string;
  /** Grupo → nomes; só os grupos com algum, na ordem do servidor. */
  groups: Partial<Record<ContractGroup, string[]>>;
  /** Operações de uso interno. */
  private: string[];
}

/** Perfil de organização ativo no servidor (`profiles` do cartão). Rótulos das origens: GET /api/v1/relations/origins. */
export interface ActiveProfile {
  name: string;
  label: string;
  origins: string[];
  /** Formatos de contrato que o perfil lê (ex.: [".sdl"] no perfil senior; vazio no default). */
  contract_formats: string[];
}

/** Fatos extraídos sem LLM (catalog-info, manifestos, pastas e as seções dos perfis de organização). */
export interface RepoFacts {
  language: string | null;
  catalog: Record<string, unknown>;
  artifacts: string[];
  /** Seção genérica de contratos (`domain/contracts.CONTRACTS_SECTION`, chave `sdl` por nome histórico), no formato
   * gravado. Não use: a interface lê `RepoCardInfo.contracts` (formato genérico); a chave sai daqui depois. */
  sdl?: Record<string, unknown>[];
  properties: { path: string; group: string; properties: string[] }[];
  modules: string[];
  has: string[];
}

export interface RelatedRepo {
  repo: string;
  /** "codigo": referência vista no código; "nome": só o nome sugere. */
  basis: "codigo" | "nome";
  why: string;
}

export interface Card {
  summary: string;
  kind: string;
  capabilities: string[];
  domain_tags: string[];
  glossary?: string[];
  related_repos?: RelatedRepo[];
  entrypoints?: string[];
  evidencias?: string[];
}

/** GET /api/v1/repos/{name}/card */
export interface ApiImpl {
  domain: string;
  service: string;
  operation: string;
  kind: string;
  symbol: string;
  path: string;
  line: number;
  origin: string;
}

export interface RepoCardInfo {
  repo: string;
  commit: string | null;
  facts: RepoFacts;
  /** Serviços de contrato (formato genérico, com o rótulo do formato). */
  contracts: ContractService[];
  impls: ApiImpl[];
  related: { repo: string; origin: string; basis?: string; why?: string }[];
  card: Card | null;
  stale: boolean;
  card_agent: string | null;
  card_at: string | null;
  facts_at: string | null;
  /** Perfis de organização ativos no servidor. */
  profiles: ActiveProfile[];
}

/** GET /api/v1/repos/find */
export interface RepoMatch {
  repo: string;
  score: number;
  summary: string | null;
  kind: string | null;
  tags: string[];
  stale: boolean;
}

export interface DocsPreview {
  name: string;
  url: string;
  locale: string;
  total: number;
  pages_in_locale: number;
  roots: { root: string; pages: number }[];
  pages: { path: string; title: string; url: string; tags: string[]; updated_at: string }[];
  truncated: boolean;
}

export interface OrgSummary {
  name: string;
  total: number;
  status: Record<RepoStatusValue, number>;
  outdated: number;
  filters: OrgFilters;
  languages: Record<string, number>;
}

export interface Hit {
  repo: string;
  path: string;
  language: string;
  start_line: number;
  end_line: number;
  score: number;
  code: string;
  /** "doc" para Markdown/RST e páginas de wiki; "code" para o resto. */
  kind: HitKind;
  /** Página original (documentação externa). */
  url: string | null;
  title: string | null;
  /** Repos ligados à página de documentação. */
  linked_repos: string[];
  /** Página de operação da API: handler que a implementa ("repo/caminho:linha"). */
  implemented_in?: string[];
  /** Resumo do arquivo em português, escrito por agent (`enrich next --kind file_summaries`), se houver. */
  summary?: string | null;
  summary_keywords?: string[];
  /** O código mudou depois do resumo: o resumo pode descrever o que não existe mais. */
  summary_stale?: boolean | null;
  /** Por quais fontes da busca o trecho veio: "vetor", "termos", "descrição" (resumo do arquivo), "cartão". */
  matched_by?: string[];
  /** Trechos de outros arquivos que chamam algo deste (grafo de chamadas), se houver. */
  callers?: number | null;
  /** Casos de teste que cobrem o arquivo (os testes ficam fora da busca; `find_tests` lista os casos). */
  tests?: number | null;
}

/** Um caso de teste (lido do clone, sem embedding): nome com o describe/classe envolvente e a linha. */
export interface TestCaseRef {
  repo: string;
  path: string;
  line: number;
  name: string;
  /** Arquivo de produção que o teste cobre. */
  tests_file?: string;
  /** Métodos do arquivo testado que o caso chama. */
  calls?: string[];
}

export interface FindTestsResult {
  /** Como o alvo casou: arquivo, classe, membro ou nome do teste (null: nada encontrado). */
  matched_by: string | null;
  total: number;
  truncated: boolean;
  tests: TestCaseRef[];
}

/** Resumo de um arquivo de código escrito por agent: entra na busca por termos. */
export interface FileSummary {
  repo: string;
  path: string;
  summary: string;
  /** Perguntas que o arquivo responde. */
  questions: string[];
  keywords: string[];
  agent: string;
  created_at: string;
  /** O arquivo mudou no índice depois que o resumo foi escrito. */
  stale?: boolean;
  /** O resumo ainda não tem vetor em dia (não entra na busca semântica até gerar). Só na lista de um repo. */
  vector_pending?: boolean;
}

/** Arquivo indexado de um repo (GET /api/v1/repos/{name}/files), com o resumo quando existe. */
export interface RepoFile {
  path: string;
  language: string;
  chunks: number;
  lines: number;
  /** A linguagem entra na fila de resumos (código; configuração e documentação ficam de fora). */
  summarizable: boolean;
  summary: string | null;
  keywords: string[];
  /** O arquivo mudou depois que o resumo foi escrito. */
  stale: boolean;
}

export interface SummaryCoverageRepo {
  repo: string;
  /** Arquivos de código indexados. */
  files: number;
  /** Com resumo em dia. */
  fresh: number;
  /** Com resumo escrito antes da última mudança do arquivo. */
  stale: number;
  /** Com resumo em dia, mas sem vetor gerado (está dentro de `fresh`). */
  unembedded?: number;
}

export interface SummaryCoverage {
  files: number;
  fresh: number;
  stale: number;
  missing: number;
  repos: SummaryCoverageRepo[];
}

export interface VectorKindCounts {
  label: string;
  /** Textos que deveriam ter vetor (perguntas dos resumos, cartões escritos). */
  sources: number;
  /** Vetor em dia: mesmo texto e mesmo modelo. */
  embedded: number;
  /** Sem vetor ou com vetor de outro texto/modelo: custa ao gerar. */
  pending: number;
  /** Vetor sem texto de origem: sai no fim do próximo sync, sem custo. */
  obsolete: number;
  /** Custo estimado dos pendentes deste tipo. */
  pending_estimate?: VectorEstimate | null;
}

/** Estimativa antes de gerar: caracteres, tokens (≈ caracteres/4) e US$ pela tabela de preços (null: sem preço). */
export interface VectorEstimate {
  chars: number;
  tokens: number;
  cost_usd: number | null;
}

export interface VectorRun {
  requested_by: string;
  started_at: string;
  finished_at: string | null;
  status: "running" | "done" | "failed";
  result: { embedded: number; deleted: number; embed_tokens: number; cost_usd: number; seconds: number } | null;
  error: string | null;
}

/** Vetores extras da busca: perguntas dos resumos e cartões dos repos (gerados sob pedido). */
export interface VectorStatus {
  model: string;
  kinds: Record<string, VectorKindCounts>;
  pending: number;
  pending_chars: number;
  pending_estimate?: VectorEstimate;
  obsolete: number;
  /** Vetores de outro modelo (EMBED_MODEL mudou): a busca os ignora até gerar de novo. */
  other_model?: number;
  /** Resumos escritos antes da última mudança do arquivo: votam com peso reduzido. */
  stale_summaries?: number;
  /** Arquivos de código: com resumo em dia, desatualizado, na fila e fora da fila (gerados ou curtos). */
  summary_coverage?: { files: number; fresh: number; stale: number; missing: number; skipped: number };
  last_embedded_at: string | null;
  /** Uma geração está rodando neste servidor. */
  running: boolean;
  last_run: VectorRun | null;
}

export type HitKind = "code" | "doc";

export interface SearchRequest {
  query: string;
  top_k: number;
  mode: SearchMode;
  rerank: boolean;
  repos: string[] | null;
  language: string | null;
  path_glob: string | null;
  kind: HitKind | null;
  include_linked_docs: boolean;
}

export type Scope = "search:read" | "index:write" | "enrich:write" | "admin";

export interface SyncRequest {
  repos: string[] | null;
  batch: number | null;
  discover: boolean;
  pull: boolean;
  dry_run: boolean;
}

/** Resultado de uma indexação (ou da prévia, com `dry_run`). */
export interface SyncResult {
  selected: string[];
  indexed: Record<string, string | null>;
  errors: Record<string, string>;
  discovered: Record<string, OrgDiscovery>;
  remaining: number;
}

export interface OrgDiscovery {
  new: number;
  removed: number;
  total: number;
}

export type SyncPhase = "discover" | "select" | "fetch" | "index" | "finish";
export type RepoProgressStatus = "queued" | "fetching" | "indexing" | "done" | "error" | "skipped";

export interface RepoProgress {
  name: string;
  status: RepoProgressStatus;
  /** Arquivos que entram na indexação (contagem prévia); null se não deu para contar. */
  files_total: number | null;
  files_done: number;
  /** Arquivos sem mudança (memoizados, não reprocessados). */
  files_unchanged: number;
  errors: number;
  error: string | null;
  seconds: number | null;
}

/** Retrato do andamento de um job, gravado pelo servidor enquanto ele roda. */
export interface SyncProgress {
  phase: SyncPhase;
  phase_label: string;
  repos_total: number;
  repos_done: number;
  files_total: number;
  files_done: number;
  seconds: number;
  repos: RepoProgress[];
}

export interface SyncJob {
  id: string;
  requested_by: string;
  params?: { repos?: string[] | null; batch?: number | null; discover?: boolean; pull?: boolean; openapi_domain?: string | null };
  status: "queued" | "running" | "done" | "failed";
  /** Quando foi pedido (entrou na fila). */
  started_at: string;
  finished_at: string | null;
  result: SyncResult | null;
  /** Erro final; num job de volta à fila, o motivo (tentativa que falhou, worker que parou). */
  error: string | null;
  progress?: SyncProgress | null;
  /** Reservas feitas por workers (a atual inclusive). 0 = nunca saiu da fila. */
  attempts?: number;
  /** Até quando o worker atual detém o job (renovado por heartbeat). */
  lease_until?: string | null;
  /** Worker que detém o job (host:pid:id). */
  worker?: string | null;
  /** Início da tentativa atual (ou da última). */
  run_started_at?: string | null;
}

export interface SyncStart {
  job_id: string;
  status: string;
  url: string;
}

export interface OrgInput {
  name: string;
  match?: string[];
  skip?: string[];
  languages?: string[];
  include_archived?: boolean;
  include_forks?: boolean;
  max_size_mb?: number | null;
  extra_exclude?: string[];
}

/** PATCH de uma org: lista vazia limpa o filtro; `max_size_mb = 0` remove o limite. */
export interface OrgPatch {
  languages?: string[];
  match?: string[];
  skip?: string[];
  max_size_mb?: number | null;
}

/** Filtros ativos de uma org (só os diferentes do padrão aparecem). */
export interface OrgFilters {
  languages?: string[];
  match?: string[];
  skip?: string[];
  max_size_mb?: number;
  include_forks?: boolean;
  include_archived?: boolean;
  extra_exclude?: string[];
}

export interface RepoInput {
  source: string;
  name?: string | null;
  branch?: string | null;
}

export interface TokenInfo {
  id: string;
  kind: "agent" | "session" | "oauth";
  name: string;
  owner_email: string;
  prefix: string;
  scopes: string[];
  active: boolean;
  created_at: string | null;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface NewToken extends TokenInfo {
  token: string;
  aviso: string;
}

export interface TokenRequest {
  name: string;
  scopes: Scope[];
  ttl_days: number | null;
}

export interface AuditEntry {
  id: number;
  at: string;
  actor: string;
  token_id: string | null;
  action: string;
  params: Record<string, unknown> | null;
  status: number | null;
  client_ip: string | null;
}

/** Origem de uma relação (manual, agent, llm, explicit, service, tag, url, mention e as dos perfis, ex.: sdl no
 * perfil senior). A lista, os
 * rótulos e a ordem vêm de GET /api/v1/relations/origins: o front não precisa conhecer cada uma. */
export type OriginKind = string;

/** GET /api/v1/relations/origins */
export interface RelationOrigin {
  name: string;
  label: string;
  description: string;
  contestable: boolean;
  stored: boolean;
  /** Declarada pelo próprio repo (ex.: o .sdl do repo): sobrepor com atribuição manual pede aviso. */
  authoritative: boolean;
  order: number;
  /** Perfil de organização que registrou a origem; null = núcleo. */
  profile: string | null;
}

/** Tipo do documento, informado pela fonte (wiki_page, api_operation, api_overview...). */
export type DocType = string;
export const API_DOC_TYPES: readonly DocType[] = ["api_operation", "api_overview"];

export interface DocLink {
  repo: string;
  origins: OriginKind[];
  reason: string | null;
  /** Vínculo gravado (manual/agent): pode ser removido pela tela. */
  removable: boolean;
}

/** Página indexada da documentação, com os repos ligados (GET /api/v1/docs/overview). */
export interface DocPageRow {
  doc_repo: string;
  doc_path: string;
  page: string;
  root: string;
  title: string | null;
  url: string | null;
  type: DocType;
  attrs: Record<string, string>;
  /** Fora da busca (continua no índice). */
  ignored: boolean;
  links: DocLink[];
}

/** Fonte de documentação cadastrada (GET /api/v1/docs/sources). */
export interface DocSourceInfo {
  name: string;
  /** Nome do repo de documentação ("docs__wiki"): status e indexação vêm de /api/v1/repos. */
  repo: string;
  type: string;
  label: string;
  url: string;
  locale: string;
  /** Globs de caminho de página; vazio = todas. */
  include: string[];
  exclude: string[];
  /** Documentos no índice. */
  documents: number;
  /** Páginas fora da busca. */
  ignored: number;
  /** A fonte permite escolher páginas na interface (wikis). */
  page_selection: boolean;
}

/** Página publicada na wiki (GET /api/v1/docs/{name}/catalog). */
export interface DocCatalogPage {
  /** Caminho sem idioma ("Plataforma/Email"). */
  path: string;
  title: string;
  url: string;
  updated_at: string;
  /** Entra pela seleção atual da fonte. */
  selected: boolean;
  /** Já está no índice. */
  indexed: boolean;
  ignored: boolean;
}

export interface DocCatalog {
  name: string;
  repo: string;
  url: string;
  locale: string;
  include: string[];
  exclude: string[];
  pages: DocCatalogPage[];
}

/** PUT /api/v1/docs/{name}/selection */
export interface DocSelectionResult {
  name: string;
  include: string[];
  exclude: string[];
  previous: { include: string[]; exclude: string[] };
  selected: number;
  /** Páginas que entram (vão para a Voyage no próximo sync). */
  added: number;
  /** Páginas que saem do índice no próximo sync. */
  removed: number;
  /** As regras mudaram. */
  changed: boolean;
  saved: boolean;
}

export interface XrayData {
  available: boolean;
  pages: { repo: string; path: string; title: string | null; url: string | null; type: DocType;
    attrs: Record<string, string> }[];
  links: { doc_repo: string; doc_path: string; repo: string; origin: OriginKind }[];
}

/** Página ligada a um repo (GET /api/v1/repos/{name}/docs). */
export interface RepoDocLink {
  doc_repo: string;
  path: string;
  url: string | null;
  title: string | null;
  origins: OriginKind[];
}

/** Vínculo página → repo sugerido por LLM (GET /api/v1/docs/suggestions). */
export interface DocSuggestion {
  doc_repo: string;
  doc_path: string;
  repo: string;
  /** add: novo vínculo; remove: tirar um vínculo automático (menção/tag). */
  action: "add" | "remove";
  reason: string;
  basis: "pagina_cita" | "assunto" | null;
  confidence: "alta" | "media" | "baixa" | null;
  agent: string;
  status: "suggested" | "active" | "rejected";
  created_at: string;
  title: string | null;
  url: string | null;
}

/** Canal de uso: `mcp` = tool do MCP; `api` = REST com token de agent; `ui` = esta interface (sessão do navegador). */
export type UsageChannel = "mcp" | "api" | "ui";

export interface UsageTotals {
  calls: number;
  errors: number;
  users: number;
  clients: number;
  mcp_calls: number;
  avg_ms: number;
  p95_ms: number;
  /** Tamanho de todas as respostas (MCP, API e interface). */
  response_bytes: number;
  /** Só respostas de tools do MCP: o que entrou na janela de contexto de agents. */
  context_bytes: number;
  embed_tokens: number;
  rerank_tokens: number;
  cost_usd: number;
  empty_results: number;
  /** ≈ context_bytes / bytes_per_token: quanto da janela de contexto dos agents foi consumido (só MCP). */
  context_tokens: number;
}

export interface UsageDay {
  /** YYYY-MM-DD */
  day: string;
  mcp: number;
  api: number;
  ui: number;
  errors: number;
  users: number;
  cost_usd: number;
  /** Só MCP (contexto de agents). */
  context_bytes: number;
}

export interface UsageAction {
  channel: UsageChannel;
  action: string;
  calls: number;
  errors: number;
  users: number;
  p50_ms: number;
  p95_ms: number;
  avg_results: number | null;
  empty_results: number;
  response_bytes: number;
  avg_response_bytes: number;
  embed_tokens: number;
  rerank_tokens: number;
  cost_usd: number;
  last_at: string;
  /** Só no canal MCP; null na API e na interface (a resposta não entra no contexto de um agent). */
  context_tokens: number | null;
  avg_context_tokens: number | null;
}

export interface UsageClient {
  client: string;
  channel: string;
  calls: number;
  users: number;
  last_version: string | null;
  last_at: string;
}

export interface UsageQuery {
  action: string;
  query: string;
  calls: number;
  users: number;
  avg_results: number | null;
  last_at: string;
}

export interface UsageEvent {
  id: number;
  at: string;
  email: string | null;
  channel: string;
  action: string;
  client: string | null;
  client_version: string | null;
  token_kind: string | null;
  status: number;
  duration_ms: number;
  result_count: number | null;
  response_bytes: number | null;
  embed_tokens: number;
  rerank_tokens: number;
  cost_usd: number;
  params: Record<string, unknown> | null;
}

/** GET /api/v1/usage (admin) e GET /api/v1/me/usage. */
export interface UsageReport {
  days: number;
  generated_at: string;
  bytes_per_token: number;
  totals: UsageTotals;
  /** Um item por dia (zerado quando não houve uso), do mais antigo ao mais recente. */
  daily: UsageDay[];
  by_action: UsageAction[];
  by_client: UsageClient[];
  top_queries: UsageQuery[];
  /** Consultas que voltaram sem resultado: o sinal para melhorar o índice. */
  empty_queries: UsageQuery[];
  recent: UsageEvent[];
}

/** GET /api/v1/users (admin): usuário, papel, estado e uso no período. */
export interface UserUsage {
  id: string;
  email: string;
  display_name: string | null;
  created_at: string;
  last_login_at: string | null;
  last_seen_at: string | null;
  disabled_at: string | null;
  active: boolean;
  role: "admin" | "leitura";
  active_tokens: number;
  calls: number;
  mcp_calls: number;
  api_calls: number;
  ui_calls: number;
  errors: number;
  /** Só MCP. */
  context_bytes: number;
  context_tokens: number;
  llm_tokens: number;
  cost_usd: number;
  last_at: string | null;
  top_action: string | null;
  clients: string[];
}

export interface UserStatus {
  email: string;
  active: boolean;
  disabled_at: string | null;
}

/** Uma aresta do grafo de chamadas, do ponto de vista de quem pergunta: em "callers", quem chama; em
 * "callees", quem é chamado. `of` é o outro lado (o símbolo consultado). */
export interface CallEdge {
  repo: string;
  path: string;
  line: number;
  name: string;
  /** Linha da chamada (no arquivo de quem chama). */
  call_line: number;
  calls: number;
  /** Como o alvo foi achado: arquivo, pacote, import, tipo, herança ou nome (inferida). */
  via: string;
  inferred: boolean;
  of: { path: string; line: number; name: string };
}

export interface CallGraphResult {
  direction: "callers" | "callees";
  total: number;
  truncated: boolean;
  edges: CallEdge[];
  note?: string;
}

export interface CallCounts {
  callers: number;
  callees: number;
}

/** Arquivo muito chamado por outros arquivos do repo, com o membro mais chamado. */
export interface CentralFile {
  path: string;
  callers: number;
  files: number;
  top: { name: string | null; line: number | null };
}

export interface ScanRun {
  requested_by: string;
  force: boolean;
  started_at: string;
  finished_at: string | null;
  status: "running" | "done" | "failed";
  result: Record<string, number> | null;
  error: string | null;
}

/** Leituras dos clones (sem custo de API): grafo de chamadas e casos de teste. */
export interface ScansStatus {
  graph: {
    repos: number;
    files: number;
    calls: number;
    resolved: number;
    inferred: number;
    edges: number;
    last_scan: string | null;
    running: boolean;
    last_run: ScanRun | null;
  };
  tests: {
    repos: number;
    files: number;
    cases: number;
    linked: number;
    last_scan: string | null;
    running: boolean;
    last_run: ScanRun | null;
  };
}
