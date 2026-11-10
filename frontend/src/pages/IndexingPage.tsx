import { useCallback, useState } from "react";
import { ArrowRight, Play, RefreshCw } from "lucide-react";
import type { OrgSummary, SyncJob } from "../api/types";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { useIndex } from "../data/IndexProvider";
import { canWrite, hasScope } from "../lib/capabilities";
import { useNavigation } from "../lib/navigation";
import { reposHash, type Filters } from "../lib/repoFilters";
import { cx, plural } from "../lib/utils";
import { useToast } from "../components/Toaster";
import { ActiveJobPanel, JobDialog, JobsPanel, useJobs } from "../components/JobsPanel";
import { IndexCoverage, useRepoCounts, type RepoCounts } from "../components/IndexCoverage";
import { SyncDialog } from "../components/SyncDialog";
import { Button, Panel, Skeleton } from "../components/ui";

const numberFormat = new Intl.NumberFormat("pt-BR");

/**
 * Operação da indexação, para curadores (index:write): o job ativo (fila ou rodando, tentativa, andamento por
 * repo), o que pede atenção, a cobertura do índice e o histórico de jobs de todas as fontes. "Indexar lote" e
 * "Atualizar descoberta" (admin) ficam aqui; o catálogo dos repositórios fica em Repositórios.
 */
export function IndexingPage() {
  const { repos, orgs, loading, reload } = useIndex();
  const { session } = useAuth();
  const { push } = useToast();
  const nav = useNavigation();
  const writable = canWrite(session);
  const me = session?.me ?? null;
  const isAdmin = me !== null && hasScope(me, "admin");
  // Mesmas regras da página Repositórios: ver jobs com index:write; disparar só com login no navegador.
  const canSeeJobs = me !== null && hasScope(me, "index:write");
  const canIndex = writable && canSeeJobs;

  const onFinished = useCallback(() => void reload(), [reload]);
  const jobs = useJobs(canSeeJobs, onFinished);
  const [jobDetail, setJobDetail] = useState<SyncJob | null>(null);
  const [syncOpen, setSyncOpen] = useState(false);
  const counts = useRepoCounts(repos);

  const openRepos = nav?.can("repos") ? (f: Partial<Filters>) => nav.open("repos", reposHash(f)) : undefined;

  async function refreshOrg(org: OrgSummary) {
    try {
      const res = await api.refreshOrg(org.name);
      const d = res[org.name];
      push({
        tone: "success",
        title: `Descoberta de ${org.name} atualizada`,
        description: d ? `${d.total} repositórios · ${d.new} ${plural(d.new, "novo", "novos")} · ${d.removed} ${plural(d.removed, "removido", "removidos")}` : undefined,
      });
      await reload();
    } catch (err) {
      push({ tone: "error", title: "Não consegui atualizar a org", description: describeError(err) });
    }
  }

  const busy = jobs.running !== null;

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Indexação</h1>
          <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">
            Fila de sync, cobertura do índice e histórico de jobs. Indexar gera custo de embedding na Voyage.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            loading={loading || jobs.loading}
            onClick={() => {
              void reload();
              void jobs.refresh();
            }}
          >
            <RefreshCw className="size-3.5" aria-hidden="true" />
            Atualizar
          </Button>
          {canIndex && (
            <Button
              size="sm"
              onClick={() => setSyncOpen(true)}
              disabled={busy}
              title={busy ? `Já existe uma indexação ${jobs.running?.status === "queued" ? "na fila" : "em andamento"}` : undefined}
            >
              <Play className="size-3.5" aria-hidden="true" />
              Indexar lote
            </Button>
          )}
        </div>
      </div>

      <section aria-labelledby="indexing-active" className="space-y-3">
        <h2 id="indexing-active" className="font-mono text-[10px] tracking-[0.16em] text-stone-500 uppercase dark:text-stone-400">
          Job ativo
        </h2>
        {jobs.jobs === null && jobs.error === null ? (
          <Skeleton className="h-14 w-full rounded-xl" />
        ) : (
          <ActiveJobPanel job={jobs.running} onOpen={setJobDetail} />
        )}
      </section>

      <Attention counts={counts} loading={repos === null} onOpen={openRepos} />

      <IndexCoverage
        counts={counts}
        orgs={orgs ?? []}
        loading={repos === null && loading}
        onStatus={openRepos ? (status) => openRepos({ status }) : undefined}
        onOutdated={openRepos ? () => openRepos({ outdated: true }) : undefined}
        onRefresh={isAdmin && writable ? (org) => void refreshOrg(org) : undefined}
      />

      <JobsPanel state={jobs} onOpen={setJobDetail} />

      <JobDialog job={jobDetail} onClose={() => setJobDetail(null)} />
      <SyncDialog
        open={syncOpen}
        onClose={() => setSyncOpen(false)}
        names={null}
        onFinished={onFinished}
        onStarted={() => void jobs.refresh()}
      />
    </div>
  );
}

/**
 * Atalhos para o que pede atenção: repos com erro no último sync, com push depois da última indexação e nunca
 * indexados. Cada um abre Repositórios já filtrado (o hash leva o filtro), onde fica a ação "Indexar".
 */
function Attention({
  counts,
  loading,
  onOpen,
}: {
  counts: RepoCounts;
  loading: boolean;
  onOpen?: (filters: Partial<Filters>) => void;
}) {
  if (loading) return null;
  const items: { key: string; n: number; label: string; hint: string; dot: string; filters: Partial<Filters> }[] = [
    {
      key: "error",
      n: counts.byStatus.error,
      label: "com erro",
      hint: "o último sync falhou",
      dot: "bg-rose-500",
      filters: { status: "error" },
    },
    {
      key: "outdated",
      n: counts.outdated,
      label: "com push novo",
      hint: "mudaram depois da última indexação",
      dot: "bg-amber-500",
      filters: { outdated: true },
    },
    {
      key: "pending",
      n: counts.byStatus.pending,
      label: plural(counts.byStatus.pending, "pendente", "pendentes"),
      hint: "ainda não indexados",
      dot: "border-[1.5px] border-stone-500",
      filters: { status: "pending" },
    },
  ];
  const shown = items.filter((i) => i.n > 0);

  return (
    <section aria-labelledby="indexing-attention" className="space-y-3">
      <h2 id="indexing-attention" className="font-mono text-[10px] tracking-[0.16em] text-stone-500 uppercase dark:text-stone-400">
        Pede atenção
      </h2>
      {shown.length === 0 ? (
        <Panel className="px-5 py-4 text-sm text-stone-500 dark:text-stone-400">
          Nada pede atenção: nenhum repositório com erro, push novo ou pendente.
        </Panel>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-3">
          {shown.map((item) => {
            const body = (
              <>
                <span className="flex items-baseline gap-2">
                  <span className={cx("size-2 shrink-0 translate-y-[-1px] rounded-full", item.dot)} aria-hidden="true" />
                  <span className="text-2xl font-semibold tracking-tight text-stone-900 tabular-nums dark:text-white">
                    {numberFormat.format(item.n)}
                  </span>
                  <span className="min-w-0 flex-1 text-sm text-stone-700 dark:text-stone-200">{item.label}</span>
                  {onOpen && (
                    <ArrowRight
                      className="size-4 shrink-0 self-center text-stone-400 transition group-hover:translate-x-0.5 group-hover:text-accent-600 dark:group-hover:text-accent-400"
                      aria-hidden="true"
                    />
                  )}
                </span>
                <span className="mt-1 text-xs text-stone-500 dark:text-stone-400">
                  {item.hint}
                  {onOpen && <span className="sr-only">. Ver em Repositórios</span>}
                </span>
              </>
            );
            return (
              <li key={item.key}>
                {onOpen ? (
                  <button
                    type="button"
                    onClick={() => onOpen(item.filters)}
                    title="Ver em Repositórios, já filtrado"
                    className="group flex w-full flex-col rounded-xl border border-stone-200 bg-sheet/85 px-4 py-3 text-left outline-none backdrop-blur-md transition hover:border-stone-300 hover:bg-sheet focus-visible:ring-2 focus-visible:ring-accent-500 dark:border-stone-800 dark:bg-stone-900/80 dark:hover:border-stone-700 dark:hover:bg-stone-900"
                  >
                    {body}
                  </button>
                ) : (
                  <div className="flex flex-col rounded-xl border border-stone-200 bg-sheet/85 px-4 py-3 backdrop-blur-md dark:border-stone-800 dark:bg-stone-900/80">
                    {body}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
