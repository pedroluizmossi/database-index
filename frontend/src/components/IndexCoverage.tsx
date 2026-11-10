import { useMemo } from "react";
import { RefreshCw } from "lucide-react";
import type { OrgSummary, Repo, RepoStatusValue } from "../api/types";
import { cx, plural } from "../lib/utils";
import { Button, IconButton, Panel, Skeleton } from "./ui";

/** Ordem e cor de cada status na barra de cobertura. */
const COVERAGE: { status: RepoStatusValue; label: string; bar: string; key: string }[] = [
  { status: "indexed", label: "Indexados", bar: "bg-emerald-500", key: "bg-emerald-500" },
  { status: "error", label: "Com erro", bar: "bg-rose-500", key: "bg-rose-500" },
  { status: "pending", label: "Pendentes", bar: "bg-stone-300 dark:bg-stone-600", key: "border-[1.5px] border-stone-500 dark:border-stone-500" },
  { status: "ignored", label: "Ignorados", bar: "bg-stone-200 dark:bg-stone-800", key: "bg-stone-300 dark:bg-stone-600" },
];

const numberFormat = new Intl.NumberFormat("pt-BR");

export interface RepoCounts {
  total: number;
  byStatus: Record<RepoStatusValue, number>;
  files: number;
  chunks: number;
  /** Repos com push depois da última indexação. */
  outdated: number;
}

/** Totais do índice por status, arquivos, trechos e push novo. */
export function useRepoCounts(repos: Repo[] | null): RepoCounts {
  return useMemo(() => {
    const byStatus: Record<RepoStatusValue, number> = { indexed: 0, pending: 0, error: 0, ignored: 0 };
    let files = 0;
    let chunks = 0;
    let outdated = 0;
    for (const r of repos ?? []) {
      byStatus[r.status] += 1;
      files += r.files;
      chunks += r.chunks;
      if (r.outdated) outdated += 1;
    }
    return { total: repos?.length ?? 0, byStatus, files, chunks, outdated };
  }, [repos]);
}

function percentOf(part: number, total: number): number {
  return total > 0 ? Math.round((part / total) * 100) : 0;
}

/** Barra empilhada por status. A cor nunca é o único código: a legenda traz rótulo e número. */
function CoverageBar({ byStatus, total, className }: { byStatus: Record<RepoStatusValue, number>; total: number; className?: string }) {
  return (
    <div className={cx("flex h-2.5 gap-px overflow-hidden rounded-full bg-stone-100 dark:bg-stone-800", className)} aria-hidden="true">
      {COVERAGE.map(({ status, bar }) =>
        byStatus[status] > 0 && total > 0 ? (
          <span key={status} className={cx("h-full transition-[width] duration-500", bar)} style={{ width: `${(byStatus[status] / total) * 100}%` }} />
        ) : null,
      )}
    </div>
  );
}

/**
 * Cobertura do índice: quanto já foi indexado, por status e por org, trechos/arquivos e push novo.
 * Cada status (e o push novo) leva a Repositórios já filtrado; "Atualizar descoberta" só para admin.
 */
export function IndexCoverage({
  counts,
  orgs,
  loading,
  onStatus,
  onOutdated,
  onRefresh,
}: {
  counts: RepoCounts;
  orgs: OrgSummary[];
  loading: boolean;
  onStatus?: (status: RepoStatusValue) => void;
  onOutdated?: () => void;
  onRefresh?: (org: OrgSummary) => void;
}) {
  if (loading) return <Skeleton className="h-40 w-full rounded-xl" />;
  const single = orgs.length === 1 && orgs[0].total === counts.total ? orgs[0] : null;
  const languages = single ? Object.entries(single.languages).slice(0, 3) : [];
  const percent = percentOf(counts.byStatus.indexed, counts.total);
  const legendItem =
    "inline-flex items-center gap-2 rounded-md px-2 py-1 text-stone-600 outline-none transition hover:bg-stone-900/5 hover:text-stone-900 focus-visible:ring-2 focus-visible:ring-accent-500 dark:text-stone-300 dark:hover:bg-white/5 dark:hover:text-white";

  return (
    <Panel className="p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <h2 className="truncate font-semibold">{single ? `Cobertura · ${single.name}` : "Cobertura do índice"}</h2>
          <p className="mt-0.5 text-sm text-stone-500 dark:text-stone-400">
            {numberFormat.format(counts.total)} {plural(counts.total, "repositório", "repositórios")}
            {languages.length > 0 && <> · {languages.map(([language, n]) => `${language} ${n}`).join(" · ")}</>}
          </p>
        </div>
        {single && onRefresh && (
          <Button variant="ghost" size="sm" onClick={() => onRefresh(single)}>
            <RefreshCw className="size-3.5" aria-hidden="true" />
            Atualizar descoberta
          </Button>
        )}
      </div>

      <div className="mt-6 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <p className="text-sm text-stone-600 dark:text-stone-300">
          <span className="text-2xl font-semibold tracking-tight text-stone-900 tabular-nums dark:text-white">{percent}%</span>
          <span className="ml-2">indexado</span>
          <span className="text-stone-500 dark:text-stone-400">
            {" "}· {numberFormat.format(counts.byStatus.indexed)} de {numberFormat.format(counts.total)}
          </span>
        </p>
        <p className="text-sm text-stone-500 tabular-nums dark:text-stone-400">
          {numberFormat.format(counts.chunks)} {plural(counts.chunks, "trecho", "trechos")} em {numberFormat.format(counts.files)}{" "}
          {plural(counts.files, "arquivo", "arquivos")}
        </p>
      </div>

      <CoverageBar byStatus={counts.byStatus} total={counts.total} className="mt-3" />

      <ul className="mt-3 flex flex-wrap gap-x-1 gap-y-1 text-sm" aria-label="Repositórios por status">
        {COVERAGE.map(({ status, label, key }) => {
          const content = (
            <>
              <span className={cx("size-2 rounded-full", key)} aria-hidden="true" />
              {label}
              <span className="font-medium text-stone-900 tabular-nums dark:text-white">{numberFormat.format(counts.byStatus[status])}</span>
            </>
          );
          return (
            <li key={status}>
              {onStatus ? (
                <button type="button" onClick={() => onStatus(status)} title={`Ver ${label.toLowerCase()} em Repositórios`} className={legendItem}>
                  {content}
                </button>
              ) : (
                <span className="inline-flex items-center gap-2 px-2 py-1 text-stone-600 dark:text-stone-300">{content}</span>
              )}
            </li>
          );
        })}
        {counts.outdated > 0 && (
          <li>
            {onOutdated ? (
              <button
                type="button"
                onClick={onOutdated}
                title="Ver em Repositórios os que tiveram push depois da última indexação"
                className={cx(legendItem, "text-amber-700 hover:text-amber-800 dark:text-amber-300 dark:hover:text-amber-200")}
              >
                <span className="size-2 rounded-full bg-amber-500" aria-hidden="true" />
                {numberFormat.format(counts.outdated)} com push novo
              </button>
            ) : (
              <span className="inline-flex items-center gap-2 px-2 py-1 text-amber-700 dark:text-amber-300">
                <span className="size-2 rounded-full bg-amber-500" aria-hidden="true" />
                {numberFormat.format(counts.outdated)} com push novo
              </span>
            )}
          </li>
        )}
      </ul>

      {!single && orgs.length > 0 && (
        <ul className="mt-5 divide-y divide-stone-100 border-t border-stone-100 dark:divide-stone-800 dark:border-stone-800" aria-label="Cobertura por org">
          {orgs.map((org) => (
            <li key={org.name} className="grid grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-4 py-3 sm:grid-cols-[minmax(0,14rem)_1fr_auto_auto]">
              <p className="truncate text-sm font-medium" title={org.name}>
                {org.name}
              </p>
              <CoverageBar byStatus={org.status} total={org.total} className="h-1.5" />
              <p className="text-sm text-stone-500 tabular-nums dark:text-stone-400">
                {percentOf(org.status.indexed, org.total)}% de {org.total}
              </p>
              {onRefresh && (
                // O wrapper esconde no celular (a classe `hidden` no próprio botão perde para o `inline-flex` dele).
                <span className="hidden sm:block">
                  <IconButton label={`Atualizar descoberta de ${org.name}`} onClick={() => onRefresh(org)}>
                    <RefreshCw className="size-4" />
                  </IconButton>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
