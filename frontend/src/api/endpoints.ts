import { getJson, getText, postJson, sendJson } from "./client";
import type {
  AuditEntry,
  XrayData,
  Hit,
  Me,
  NewToken,
  OrgDiscovery,
  OrgSummary,
  RepoInput,
  Repo,
  SearchRequest,
  SyncJob,
  DocPageRow,
  DocsPreview,
  RepoDocLink,
  SyncResult,
  SyncStart,
  TokenInfo,
  TokenRequest,
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
  repos: () => getJson<Repo[]>("/api/v1/repos"),
  orgs: () => getJson<OrgSummary[]>("/api/v1/orgs"),
  search: (body: SearchRequest) => postJson<Hit[]>("/api/v1/search", body),
  file: (repo: string, path: string) => getText(`/api/v1/files/${encodeRepoPath(repo, path)}`),
  logout: () => postJson<{ status: string }>("/auth/logout", undefined),

  // sync (index:write). A prévia (dry_run) não custa nada; a indexação gera custo na Voyage.
  // Redescobrir a org (API do GitHub, alguns segundos) só faz sentido no lote automático:
  // com repos escolhidos pelo nome, a seleção não depende dela.
  docsPreview: (name: string) => getJson<DocsPreview>(`/api/v1/docs/${encodeURIComponent(name)}/preview`),
  docsOverview: (offset = 0) => getJson<DocPageRow[]>("/api/v1/docs/overview", { query: { limit: 2000, offset, docs_only: true } }),
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

  // tokens (emitir: sessão Google; listar/revogar: dono ou admin)
  tokens: (includeInactive = false) =>
    getJson<TokenInfo[]>("/api/v1/tokens", { query: { include_inactive: includeInactive } }),
  createToken: (body: TokenRequest) => postJson<NewToken>("/api/v1/tokens", body),
  revokeToken: (id: string) => sendJson<{ id: string; result: string }>("DELETE", `/api/v1/tokens/${seg(id)}`),

  // auditoria (admin)
  audit: (limit: number, actor: string) =>
    getJson<AuditEntry[]>("/api/v1/audit", { query: { limit, actor } }),
};
