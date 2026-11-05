import type { OwnerOrigin, OwnershipCatalog } from "../api/types";

/** Rótulo curto de cada origem do dono (mesma ordem de força do servidor: manual vence). */
export const ORIGIN_LABEL: Record<OwnerOrigin, string> = {
  manual: "manual",
  declared: "SDL",
  sibling: "repositório irmão",
  api_calls: "chamadas de API",
  usage: "uso de biblioteca",
};

export const ORIGIN_HINT: Record<OwnerOrigin, string> = {
  manual: "definido por uma pessoa; vence a inferência",
  declared: "domínio/serviço declarados no .sdl do próprio repo",
  sibling: "mesmo nome-base de um repositório com SDL (ex.: x-frontend e x-backend)",
  api_calls: "o código chama, na maioria, as rotas deste serviço",
  usage: "biblioteca usada só por repositórios deste domínio",
};

/** Mesma normalização do servidor: minúsculas, espaço e hífen viram '_'. */
export function normalizeOwner(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, "_")
    .replace(/\/+$/, "");
}

const NAME = /^[a-z][a-z0-9_]{0,62}(\/[a-z][a-z0-9_]{0,62})?$/;

export function validOwner(value: string): boolean {
  return NAME.test(value);
}

export interface OwnerOption {
  value: string;
  label: string;
}

/** Domínios e serviços do catálogo, para o campo com sugestões. */
export function ownerOptions(catalog: OwnershipCatalog | null): OwnerOption[] {
  if (!catalog) return [];
  const out: OwnerOption[] = [];
  for (const d of catalog.domains) {
    out.push({ value: d.name, label: d.label ? `${d.label} (domínio)` : "domínio" });
    for (const s of d.services) out.push({ value: `${d.name}/${s.name}`, label: s.label || `serviço de ${d.name}` });
  }
  return out;
}

/** O repo pertence ao filtro? Filtro = domínio ('hcm') ou serviço ('hcm/payroll'). */
export function ownerMatches(owners: string[], filter: string): boolean {
  return owners.some((o) => o === filter || o.startsWith(`${filter}/`));
}
