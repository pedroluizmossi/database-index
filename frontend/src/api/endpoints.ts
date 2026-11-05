import { getJson, getText, postJson, sendJson } from "./client";
import type {
  AuditEntry,
  Hit,
  Me,
  NewToken,
  OrgDiscovery,
  OrgSummary,
  RepoInput,
  Repo,
  SearchRequest,
  SyncJob,
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
