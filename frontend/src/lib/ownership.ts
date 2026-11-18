import type { OwnerOrigin, OwnershipCatalog, RelationOrigin } from "../api/types";

/** Origens registradas no servidor (núcleo + perfis ativos), de `useOrigins()`: o front não fixa nenhuma. */
export type OriginMeta = Map<string, RelationOrigin>;

/** Mesma forma de nome de origem do servidor (domain/relations). */
const ORIGIN_NAME = /^[a-z][a-z0-9_]{0,39}$/;

export function validOrigin(value: string): boolean {
  return ORIGIN_NAME.test(value);
}

/** Rótulo curto da origem do dono (o próprio nome, se o servidor não a conhece). */
export function originLabel(meta: OriginMeta, origin: OwnerOrigin): string {
  return meta.get(origin)?.label ?? origin;
}

export function originHint(meta: OriginMeta, origin: OwnerOrigin): string {
  return meta.get(origin)?.description ?? "";
}

/** Ordem de força do servidor (manual primeiro); origem desconhecida vai para o fim. */
export function originRank(meta: OriginMeta, origin: OwnerOrigin): number {
  return meta.get(origin)?.order ?? Number.MAX_SAFE_INTEGER;
}

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
