import type { Repo } from "../api/types";

const numberFormat = new Intl.NumberFormat("pt-BR");

/** Tamanho do repo no GitHub (KB → texto). Inclui o histórico do git: não é o tamanho do que vai para o índice. */
export function formatSize(kb: number | null | undefined): string {
  if (!kb) return "—";
  if (kb < 1024) return `${numberFormat.format(kb)} KB`;
  return `${(kb / 1024).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} MB`;
}

/** Arquivos de um repo: os do índice; sem nada indexado, a estimativa pela árvore do GitHub (`code-index repo
 * estimate`), se houver. `prefix`: "~" estimativa, "≥" árvore cortada pelo GitHub (a contagem é um mínimo). */
export function repoFiles(r: Repo): { value: number | null; prefix: "" | "~" | "≥" } {
  if (r.files > 0 || r.files_estimate == null) return { value: r.status === "indexed" || r.files > 0 ? r.files : null, prefix: "" };
  return { value: r.files_estimate, prefix: r.files_estimate_truncated ? "≥" : "~" };
}

export function formatRepoFiles(r: Repo): string {
  const { value, prefix } = repoFiles(r);
  return value == null ? "—" : `${prefix}${numberFormat.format(value)}`;
}

export const ESTIMATE_HINT =
  "Estimativa: arquivos que entrariam no índice, contados na árvore do GitHub com os filtros do sync (sem clonar). " +
  "Atualize com: code-index repo estimate";
