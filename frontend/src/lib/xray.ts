export type XrayKind = "repo" | "doc" | "api";
export interface XrayTarget { kind: XrayKind; id: string }

export const pageId = (repo: string, path: string) => JSON.stringify([repo, path]);
export const apiId = (repo: string, domain: string, service: string) => JSON.stringify([repo, domain, service]);
