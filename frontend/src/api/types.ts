// Formatos de resposta da API (src/code_index/interfaces/http/app.py e domain/models.py).

export type RepoStatusValue = "indexed" | "pending" | "error" | "ignored";
export type SearchMode = "hybrid" | "semantic" | "keyword";

export interface Me {
  subject: string;
  kind: "agent" | "session";
  scopes: string[];
  token_name: string | null;
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

export interface SyncJob {
  id: string;
  requested_by: string;
  params?: { repos?: string[] | null; batch?: number | null; discover?: boolean; pull?: boolean };
  status: "running" | "done" | "failed";
  started_at: string;
  finished_at: string | null;
  result: SyncResult | null;
  error: string | null;
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
  kind: "agent" | "session";
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

/** Origem de um vínculo: automática (explicit, tag, url, mention) ou gravada (manual, agent). */
export type OriginKind = "manual" | "agent" | "explicit" | "tag" | "url" | "mention";

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
  links: DocLink[];
}

/** Página ligada a um repo (GET /api/v1/repos/{name}/docs). */
export interface RepoDocLink {
  doc_repo: string;
  path: string;
  url: string | null;
  title: string | null;
  origins: OriginKind[];
}
