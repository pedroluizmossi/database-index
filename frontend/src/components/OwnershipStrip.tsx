import { Boxes } from "lucide-react";
import type { OwnerOrigin, OwnershipCatalog } from "../api/types";
import { ORIGIN_HINT, ORIGIN_LABEL } from "../lib/ownership";
import { ORIGIN_VALUES } from "../lib/repoFilters";
import { cx } from "../lib/utils";
import { Skeleton } from "./ui";

const numberFormat = new Intl.NumberFormat("pt-BR");

/**
 * Faixa de curadoria de domínio (DDD) na página Repositórios: quanto tem domínio/serviço, de que origem veio
 * (clique filtra a lista) e um atalho para os sem domínio.
 */
export function OwnershipStrip({
  coverage,
  loading,
  originFilter,
  onOrigin,
  onShowUnowned,
}: {
  coverage: OwnershipCatalog["coverage"] | null;
  loading: boolean;
  originFilter: OwnerOrigin | "";
  onOrigin: (origin: OwnerOrigin) => void;
  onShowUnowned: () => void;
}) {
  if (loading && !coverage) return <Skeleton className="h-12 w-full rounded-xl" />;
  if (!coverage || coverage.repos === 0) return null;
  return (
    <section
      aria-label="Domínio (DDD)"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-stone-200 bg-sheet/85 px-4 py-2.5 text-sm backdrop-blur-md dark:border-stone-800 dark:bg-stone-900/80"
    >
      <Boxes className="size-4 shrink-0 text-stone-500 dark:text-stone-400" aria-hidden="true" />
      <span className="text-stone-600 dark:text-stone-300">
        <span className="font-medium text-stone-900 tabular-nums dark:text-white">{coverage.percent}%</span> com domínio/serviço
        <span className="text-stone-500 dark:text-stone-400">
          {" "}· {numberFormat.format(coverage.owned)} de {numberFormat.format(coverage.repos)}
        </span>
      </span>
      <ul className="flex flex-wrap items-center gap-x-0.5 gap-y-1 text-xs" aria-label="Repositórios por origem do domínio">
        {ORIGIN_VALUES.filter((o) => (coverage.by_origin[o] ?? 0) > 0).map((o) => {
          const pressed = originFilter === o;
          return (
            <li key={o}>
              <button
                type="button"
                aria-pressed={pressed}
                onClick={() => onOrigin(o)}
                title={`${ORIGIN_HINT[o]}. ${pressed ? "Clique para tirar o filtro." : "Clique para ver só estes."}`}
                className={cx(
                  "inline-flex items-center gap-1.5 rounded-md px-2 py-1 outline-none transition focus-visible:ring-2 focus-visible:ring-accent-500",
                  pressed
                    ? "bg-accent-50 font-medium text-accent-700 dark:bg-accent-500/10 dark:text-accent-300"
                    : "text-stone-600 hover:bg-stone-900/5 hover:text-stone-900 dark:text-stone-300 dark:hover:bg-white/5 dark:hover:text-white",
                )}
              >
                {ORIGIN_LABEL[o]}
                <span className="font-medium text-stone-900 tabular-nums dark:text-white">{numberFormat.format(coverage.by_origin[o] ?? 0)}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {coverage.repos > coverage.owned && (
        <button
          type="button"
          onClick={onShowUnowned}
          className="ml-auto rounded-md px-2 py-1 text-sm font-medium text-accent-700 outline-none transition hover:bg-accent-50 focus-visible:ring-2 focus-visible:ring-accent-500 dark:text-accent-300 dark:hover:bg-accent-500/10"
        >
          Ver {numberFormat.format(coverage.repos - coverage.owned)} sem domínio
        </button>
      )}
    </section>
  );
}
