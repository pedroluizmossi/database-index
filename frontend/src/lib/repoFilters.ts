import type { OwnerOrigin, RepoStatusValue } from "../api/types";
import { ORIGIN_LABEL, validOwner } from "./ownership";

/**
 * Filtros da página Repositórios, guardados no hash da URL:
 * `#repos?status=pending&dominio=hcm&origem=sibling&confianca=media&push=novo&q=texto`.
 * Chaves em português; valores ruins caem no padrão, sem erro. Outras páginas (Indexação) usam `reposHash`
 * para abrir a lista já filtrada.
 */

export type StatusFilter = "all" | RepoStatusValue;

/** Confiança do dono (vem do servidor; 'alta' é o padrão, sem filtro de origem). */
export type Confidence = "alta" | "media" | "baixa";
export type ConfidenceFilter = "" | Confidence;
export const CONFIDENCE_LABEL: Record<Confidence, string> = { alta: "alta", media: "média", baixa: "baixa" };
export const CONFIDENCE_VALUES = Object.keys(CONFIDENCE_LABEL) as Confidence[];
export const ORIGIN_VALUES = Object.keys(ORIGIN_LABEL) as OwnerOrigin[];
export const STATUS_VALUES: StatusFilter[] = ["all", "indexed", "pending", "error", "ignored"];
export const SEARCH_MAX = 200;

// Filtro de domínio: todos, sem dono, ou um domínio ('hcm') / serviço ('hcm/payroll').
export const ALL_OWNERS = "";
export const NO_OWNER = "__sem_dono__";

const HASH_KEYS = {
  status: "status",
  domain: "dominio",
  origin: "origem",
  confidence: "confianca",
  outdated: "push",
  search: "q",
} as const;
const OUTDATED_VALUE = "novo";

export interface Filters {
  status: StatusFilter;
  domain: string;
  origin: OwnerOrigin | "";
  confidence: ConfidenceFilter;
  /** Só repos com push depois da última indexação. */
  outdated: boolean;
  search: string;
}

export const DEFAULT_FILTERS: Filters = {
  status: "all",
  domain: ALL_OWNERS,
  origin: "",
  confidence: "",
  outdated: false,
  search: "",
};

export function readFiltersFromHash(): Filters {
  const hash = window.location.hash.slice(1);
  const at = hash.indexOf("?");
  if (at < 0) return DEFAULT_FILTERS;
  const params = new URLSearchParams(hash.slice(at + 1));
  const status = params.get(HASH_KEYS.status) as StatusFilter | null;
  const domain = params.get(HASH_KEYS.domain) ?? "";
  const origin = params.get(HASH_KEYS.origin) as OwnerOrigin | null;
  const confidence = params.get(HASH_KEYS.confidence) as Confidence | null;
  return {
    status: status && STATUS_VALUES.includes(status) ? status : "all",
    domain: domain === NO_OWNER || validOwner(domain) ? domain : ALL_OWNERS,
    origin: origin && ORIGIN_VALUES.includes(origin) ? origin : "",
    confidence: confidence && CONFIDENCE_VALUES.includes(confidence) ? confidence : "",
    outdated: params.get(HASH_KEYS.outdated) === OUTDATED_VALUE,
    search: (params.get(HASH_KEYS.search) ?? "").slice(0, SEARCH_MAX),
  };
}

/** Hash da página Repositórios com estes filtros (os que estão no padrão ficam de fora). */
export function reposHash(partial: Partial<Filters>): string {
  const f = { ...DEFAULT_FILTERS, ...partial };
  const params = new URLSearchParams();
  if (f.status !== "all") params.set(HASH_KEYS.status, f.status);
  if (f.domain !== ALL_OWNERS) params.set(HASH_KEYS.domain, f.domain);
  if (f.origin) params.set(HASH_KEYS.origin, f.origin);
  if (f.confidence) params.set(HASH_KEYS.confidence, f.confidence);
  if (f.outdated) params.set(HASH_KEYS.outdated, OUTDATED_VALUE);
  if (f.search.trim()) params.set(HASH_KEYS.search, f.search);
  const query = params.toString();
  return query ? `#repos?${query}` : "#repos";
}

/** Escreve os filtros no hash sem criar entrada no histórico; mantém o caminho e a busca da página. */
export function writeFiltersToHash(f: Filters) {
  const hash = reposHash(f);
  if (window.location.hash === hash) return;
  try {
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}${hash}`);
  } catch {
    // Sem histórico gravável (ambiente restrito): os filtros continuam valendo na tela.
  }
}

/** Algum filtro difere do padrão? */
export function isFilterActive(f: Filters): boolean {
  return (
    f.status !== "all" ||
    f.domain !== ALL_OWNERS ||
    f.origin !== "" ||
    f.confidence !== "" ||
    f.outdated ||
    f.search.trim() !== ""
  );
}
