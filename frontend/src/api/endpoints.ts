import { getJson, getText, postJson, sendJson } from "./client";
import type {
  AuditEntry,
  XrayData,
  Hit,
  LoginProvider,
  Me,
  NewToken,
  OrgDiscovery,
  OwnershipCatalog,
  OwnershipRow,
  OrgSummary,
  RepoInput,
  Repo,
  RepoCardInfo,
  RelationOrigin,
  DocSuggestion,
  RepoMatch,
  SearchRequest,
  SyncJob,
  DocPageRow,
  DocCatalog,
  DocSelectionResult,
  DocSourceInfo,
  DocsPreview,
  RepoDocLink,
  SyncResult,
  SyncStart,
  TokenInfo,
  TokenRequest,
  PrefValue,
  UserSettings,
  UsageReport,
  UserStatus,
  UserUsage,
  FileSummary,
  RepoFile,
  SummaryCoverage,
} from "./types";

/** Caminho de arquivo: `repo/dir/arquivo.ext`, com cada parte codificada. */
function encodeRepoPath(repo: string, path: string): string {
  return [repo, ...path.split("/")].map(encodeURIComponent).join("/");
}

const seg = (value: string) => encodeURIComponent(value);

export const api = {
  xray: () => getJson<XrayData>("/api/v1/xray"),
  openapiCatalog: () => getJson<{ domain: string; service: string; key: string; size: number; url: string }[]>("/api/v1/openapi/catalog"),
  openapiStatus: () => getJson<{ domain: string; repo: string; all_services: boolean; selected_services: string[]; status: string; indexed_at: string | null; error: string | null; services: { service: string; operations: number }[] }[]>("/api/v1/openapi/status"),
  openapiPreview: (domain: string, services: string[] | null) =>
    postJson<{ domain: string; services: string[]; bytes: number }>("/api/v1/openapi/preview", { domain, services }),
  openapiIndex: (domain: string, services: string[] | null) =>
    postJson<SyncStart>("/api/v1/openapi/index", { domain, services }),
  // leitura
  me: (token?: string) => getJson<Me>("/api/v1/me", { token }),
  mySettings: () => getJson<UserSettings>("/api/v1/me/settings"),
  /** `null` volta a config ao padrão. Só com login no navegador. */
  updateMySettings: (settings: Record<string, PrefValue | null>) =>
    sendJson<UserSettings>("PATCH", "/api/v1/me/settings", { body: { settings } }),
  loginProviders: () => getJson<LoginProvider[]>("/auth/providers"),
  repos: () => getJson<Repo[]>("/api/v1/repos"),
  orgs: () => getJson<OrgSummary[]>("/api/v1/orgs"),
  search: (body: SearchRequest) => postJson<Hit[]>("/api/v1/search", body),
  file: (repo: string, path: string) => getText(`/api/v1/files/${encodeRepoPath(repo, path)}`),
  // resumos de arquivos escritos por agent (entram na busca por termos)
  fileSummary: (repo: string, path: string) => getJson<FileSummary | null>(`/api/v1/summaries/file/${encodeRepoPath(repo, path)}`),
  repoFiles: (repo: string) => getJson<RepoFile[]>(`/api/v1/repos/${seg(repo)}/files`),
  repoSummaries: (repo: string) => getJson<FileSummary[]>(`/api/v1/summaries/repo/${seg(repo)}`),
  summaryCoverage: () => getJson<SummaryCoverage>("/api/v1/summaries/coverage"),
  logout: () => postJson<{ status: string }>("/auth/logout", undefined),

  // sync (index:write). A prévia (dry_run) não custa nada; a indexação gera custo na Voyage.
  // Redescobrir a org (API do GitHub, alguns segundos) só faz sentido no lote automático:
  // com repos escolhidos pelo nome, a seleção não depende dela.
  docsPreview: (name: string) => getJson<DocsPreview>(`/api/v1/docs/${encodeURIComponent(name)}/preview`),
  docsOverview: (offset = 0) => getJson<DocPageRow[]>("/api/v1/docs/overview", { query: { limit: 2000, offset, docs_only: true } }),
  docSources: () => getJson<DocSourceInfo[]>("/api/v1/docs/sources"),
  // escolha das páginas de uma wiki (admin): lista sem custo; gravar só troca as regras (vale no próximo sync)
  docCatalog: (name: string) => getJson<DocCatalog>(`/api/v1/docs/${seg(name)}/catalog`),
  setDocSelection: (name: string, pages: string[], dryRun: boolean) =>
    sendJson<DocSelectionResult>("PUT", `/api/v1/docs/${seg(name)}/selection`, { body: { pages, dry_run: dryRun } }),
  ignoreDoc: (body: { doc_repo: string; doc_path: string; reason?: string }) =>
    postJson<{ ignored: boolean }>("/api/v1/docs/ignored", body),
  unignoreDoc: (q: { doc_repo: string; doc_path: string }) =>
    sendJson<{ ignored: boolean }>("DELETE", `/api/v1/docs/ignored?${new URLSearchParams(q).toString()}`),
  relationOrigins: () => getJson<RelationOrigin[]>("/api/v1/relations/origins"),
  docSuggestions: () => getJson<DocSuggestion[]>("/api/v1/docs/suggestions"),
  reviewDocSuggestion: (body: { doc_repo: string; doc_path: string; repo: string; approve: boolean }) =>
    postJson<{ ok: boolean }>("/api/v1/docs/suggestions/review", body),
  repoCard: (name: string) => getJson<RepoCardInfo>(`/api/v1/repos/${seg(name)}/card`),
  findRepos: (q: string, limit = 5) => getJson<RepoMatch[]>("/api/v1/repos/find", { query: { q, limit } }),
  repoDocs: (name: string) => getJson<RepoDocLink[]>(`/api/v1/repos/${seg(name)}/docs`),
  addDocLink: (body: { doc_repo: string; doc_path: string; repo: string; reason: string }) =>
    postJson<unknown>("/api/v1/docs/links", body),
  removeDocLink: (q: { doc_repo: string; doc_path: string; repo: string }) =>
    sendJson<{ removed: boolean }>("DELETE", `/api/v1/docs/links?${new URLSearchParams(q).toString()}`),
  syncPreview: (repos: string[] | null, batch: number) =>
    postJson<SyncResult>("/api/v1/sync", { repos, batch, discover: repos === null, pull: true, dry_run: true }),
  syncStart: (repos: string[] | null, batch: number) =>
    postJson<SyncStart>("/api/v1/sync", { repos, batch, discover: repos === null, pull: true, dry_run: false }),
  syncJob: (id: string) => getJson<SyncJob>(`/api/v1/sync/jobs/${seg(id)}`),
  syncJobs: (limit = 20) => getJson<SyncJob[]>("/api/v1/sync/jobs", { query: { limit } }),

  // repos e orgs (admin)
  addRepo: (body: RepoInput) => postJson<{ name: string }>("/api/v1/repos", body),
  removeRepo: (name: string, keepClone = false) =>
    sendJson<{ name: string; result: "removed" | "ignored" }>("DELETE", `/api/v1/repos/${seg(name)}`, {
      query: { keep_clone: keepClone },
    }),
  unignoreRepo: (name: string) => postJson<{ name: string }>(`/api/v1/repos/${seg(name)}/unignore`, undefined),
  refreshOrg: (name: string) =>
    sendJson<Record<string, OrgDiscovery>>("POST", "/api/v1/orgs/refresh", { query: { name } }),

  // dono no DDD (domínio/serviço): leitura para todos; atribuição manual (admin)
  ownershipCatalog: () => getJson<OwnershipCatalog>("/api/v1/ownership/catalog"),
  ownership: (repo: string) => getJson<OwnershipRow[]>("/api/v1/ownership", { query: { repo } }),
  setOwner: (body: { repos: string[]; owner: string; path?: string | null; create?: boolean; reason?: string }) =>
    sendJson<{ updated: number; owner: string; entities: string[] }>("PUT", "/api/v1/ownership", { body }),
  clearOwner: (body: { repos: string[]; path?: string | null }) =>
    postJson<{ cleared: number; entities: string[] }>("/api/v1/ownership/clear", body),

  // tokens (emitir: sessão do navegador; listar/revogar: dono ou admin)
  tokens: (includeInactive = false) =>
    getJson<TokenInfo[]>("/api/v1/tokens", { query: { include_inactive: includeInactive } }),
  createToken: (body: TokenRequest) => postJson<NewToken>("/api/v1/tokens", body),
  revokeToken: (id: string) => sendJson<{ id: string; result: string }>("DELETE", `/api/v1/tokens/${seg(id)}`),

  // auditoria (admin)
  audit: (limit: number, actor: string) =>
    getJson<AuditEntry[]>("/api/v1/audit", { query: { limit, actor } }),

  // uso: o próprio (qualquer sessão) e o de todos (admin)
  myUsage: (days: number) => getJson<UsageReport>("/api/v1/me/usage", { query: { days } }),
  usage: (days: number, email?: string) => getJson<UsageReport>("/api/v1/usage", { query: { days, email } }),

  // usuários (admin; desativar/reativar só com login no navegador)
  users: (days: number) => getJson<UserUsage[]>("/api/v1/users", { query: { days } }),
  disableUser: (email: string) => postJson<UserStatus>(`/api/v1/users/${seg(email)}/disable`, undefined),
  enableUser: (email: string) => postJson<UserStatus>(`/api/v1/users/${seg(email)}/enable`, undefined),
};
