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
  /** Domínio/serviço do DDD dono do repo ('platform/audit'); vazio = sem dono. */
  owners: string[];
  owner_origin: OwnerOrigin | null;
  owner_confidence: string | null;
}

/** De onde veio o dono: manual vence; o resto é inferido (SDL, repo irmão, rotas chamadas, uso de lib). */
export type OwnerOrigin = "manual" | "declared" | "sibling" | "api_calls" | "usage";

export interface OwnershipService {
  name: string;
  label: string;
  repos: number;
  declared_in: string[];
  /** Criado à mão (não existe em nenhum .sdl). */
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
  coverage: { repos: number; owned: number; percent: number; by_origin: Partial<Record<OwnerOrigin | "none", number>> };
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

export interface SdlService {
  path: string;
  domain: string;
  service: string;
  queries: string[];
  actions: string[];
  signals: string[];
  events: string[];
  entities: string[];
  private: string[];
}

/** Fatos extraídos sem LLM (catalog-info, manifestos, .sdl, .pdl, pastas). */
export interface RepoFacts {
  language: string | null;
  catalog: Record<string, unknown>;
  artifacts: string[];
  sdl: SdlService[];
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
  impls: ApiImpl[];
  related: { repo: string; origin: string; basis?: string; why?: string }[];
  card: Card | null;
  stale: boolean;
  card_agent: string | null;
  card_at: string | null;
  facts_at: string | null;
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
  filters: Record<string, unknown>;
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

export type Scope = "search:read" | "index:write" | "admin";

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
  status: "running" | "done" | "failed";
  started_at: string;
  finished_at: string | null;
  result: SyncResult | null;
  error: string | null;
  progress?: SyncProgress | null;
}

export interface SyncStart {
  job_id: string;
  status: string;
  url: string;
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

/** Origem de uma relação (manual, agent, llm, explicit, sdl, service, tag, url, mention...). A lista, os
 * rótulos e a ordem vêm de GET /api/v1/relations/origins: o front não precisa conhecer cada uma. */
export type OriginKind = string;

/** GET /api/v1/relations/origins */
export interface RelationOrigin {
  name: string;
  label: string;
  description: string;
  contestable: boolean;
  stored: boolean;
  order: number;
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
  links: DocLink[];
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
