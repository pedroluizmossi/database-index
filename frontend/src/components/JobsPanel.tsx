import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronRight, History, LoaderCircle } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { RepoProgress, RepoProgressStatus, SyncJob, SyncProgress } from "../api/types";
import { cx, formatDateTime, formatDuration, plural, timeAgo } from "../lib/utils";
import { Dialog } from "./Dialog";
import { Alert, Badge, Button, EmptyState, Panel, ProgressBar, Skeleton, type BadgeTone } from "./ui";

const POLL_MS = 3000;

const STATUS: Record<SyncJob["status"], { label: string; tone: BadgeTone }> = {
  running: { label: "Rodando", tone: "pending" },
  done: { label: "Concluído", tone: "indexed" },
  failed: { label: "Falhou", tone: "error" },
};

/** Status exibido: um job "done" em que algum repo falhou aparece como "Com erros". */
function display(job: SyncJob): { label: string; tone: BadgeTone } {
  const errors = job.result ? Object.keys(job.result.errors).length : 0;
  if (job.status === "done" && errors > 0) return { label: "Com erros", tone: "warning" };
  return STATUS[job.status];
}

interface JobsState {
  jobs: SyncJob[] | null;
  error: string | null;
  loading: boolean;
  running: SyncJob | null;
  refresh: () => Promise<void>;
}

/**
 * Jobs de sync recentes, vindos do servidor (persistidos no banco).
 * Enquanto houver job rodando, atualiza sozinho; quando um job termina, chama `onJobFinished`.
 * Funciona depois de recarregar a página: o estado vem do servidor, não da memória da tela.
 */
export function useJobs(enabled: boolean, onJobFinished: () => void): JobsState {
  const [jobs, setJobs] = useState<SyncJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const runningIds = useRef<Set<string>>(new Set());
  const finishedRef = useRef(onJobFinished);
  finishedRef.current = onJobFinished;

  const refresh = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    try {
      const next = await api.syncJobs(20);
      const nowRunning = new Set(next.filter((j) => j.status === "running").map((j) => j.id));
      const ended = [...runningIds.current].some((id) => !nowRunning.has(id));
      runningIds.current = nowRunning;
      setJobs(next);
      setError(null);
      if (ended) finishedRef.current();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setLoading(false);
    }
  }, [enabled]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const running = jobs?.find((j) => j.status === "running") ?? null;

  // Polling só enquanto há job rodando.
  useEffect(() => {
    if (!running) return;
    const timer = window.setTimeout(() => void refresh(), POLL_MS);
    return () => window.clearTimeout(timer);
  }, [running, jobs, refresh]);

  return { jobs, error, loading, running, refresh };
}

function duration(job: SyncJob): string {
  const end = job.finished_at ? Date.parse(job.finished_at) : Date.now();
  return formatDuration(end - Date.parse(job.started_at));
}

function scopeLabel(job: SyncJob): string {
  const repos = job.params?.repos;
  if (repos && repos.length > 0) return repos.length === 1 ? repos[0] : `${repos.length} repositórios`;
  return `lote automático${job.params?.batch ? ` de até ${job.params.batch}` : ""}`;
}

const count = new Intl.NumberFormat("pt-BR");

/** "5/25 arquivos" (sem total: só o que já foi feito). */
function filesLabel(done: number, total: number | null): string {
  return total ? `${count.format(done)}/${count.format(total)} arquivos` : `${count.format(done)} arquivos`;
}

/** Resumo de uma linha do andamento: etapa, repos e arquivos. */
function progressLine(p: SyncProgress): string {
  const parts = [p.phase_label];
  if (p.repos_total > 0) parts.push(`${p.repos_done}/${p.repos_total} repositórios`);
  if (p.phase === "index" || p.phase === "finish") parts.push(filesLabel(p.files_done, p.files_total || null));
  return parts.join(" · ");
}

function summary(job: SyncJob): string {
  if (job.status === "running") return job.progress ? progressLine(job.progress) : "em andamento…";
  if (job.result) {
    const ok = Object.keys(job.result.indexed).length;
    const bad = Object.keys(job.result.errors).length;
    if (ok === 0 && bad === 0) return "nada a indexar";
    return `${ok} ${plural(ok, "indexado", "indexados")}${bad ? ` · ${bad} com erro` : ""}`;
  }
  return job.error ?? "sem resultado";
}

/** Faixa no topo da página enquanto um job roda: etapa, repo atual e barra de arquivos. */
export function RunningJobBanner({ job, onOpen }: { job: SyncJob; onOpen: (job: SyncJob) => void }) {
  const p = job.progress ?? null;
  const current = p?.repos.find((r) => r.status === "indexing" || r.status === "fetching") ?? null;
  const showBar = p !== null && (p.phase === "index" || p.phase === "finish") && p.files_total > 0;
  return (
    <div
      role="status"
      className="space-y-3 rounded-xl border border-accent-200 bg-accent-50 px-4 py-3 text-sm text-accent-900 dark:border-accent-900/60 dark:bg-accent-950/40 dark:text-accent-100"
    >
      <div className="flex flex-wrap items-center gap-3">
        <LoaderCircle className="size-4 shrink-0 animate-spin" aria-hidden="true" />
        <p className="min-w-0 flex-1">
          <span className="font-medium">Indexação em andamento</span>
          {p ? <> · {progressLine(p)}</> : <> ({scopeLabel(job)})</>}
          <span className="text-accent-800/80 dark:text-accent-200/80">
            {" "}· {job.requested_by}, {timeAgo(job.started_at)}
          </span>
        </p>
        <Button variant="ghost" size="sm" onClick={() => onOpen(job)}>
          Ver detalhes
        </Button>
      </div>
      {showBar && p && (
        <div className="space-y-1.5">
          <ProgressBar value={p.files_done} max={p.files_total} label="Arquivos processados no lote" />
          {current && (
            <p className="truncate text-xs text-accent-800 dark:text-accent-200">
              {current.status === "fetching" ? "Baixando" : "Indexando"}{" "}
              <span className="font-mono">{current.name}</span>
              {current.status === "indexing" && <> · {filesLabel(current.files_done, current.files_total)}</>}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** Lista dos últimos jobs, com detalhe ao clicar. */
export function JobsPanel({ state, onOpen }: { state: JobsState; onOpen: (job: SyncJob) => void }) {
  const { jobs, error, loading, refresh } = state;
  return (
    <Panel className="overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
        <div>
          <h2 className="font-semibold">Jobs de indexação</h2>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">Últimos 20, guardados no servidor.</p>
        </div>
        <Button variant="secondary" size="sm" loading={loading} onClick={() => void refresh()}>
          Atualizar
        </Button>
      </div>

      {error && (
        <div className="p-4">
          <Alert>{error}</Alert>
        </div>
      )}
      {jobs === null && !error && (
        <div className="space-y-2 p-4">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      )}
      {jobs !== null && jobs.length === 0 && <EmptyState icon={History} title="Nenhuma indexação ainda" />}

      {jobs !== null && jobs.length > 0 && (
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {jobs.map((job) => (
            <li key={job.id}>
              <button
                type="button"
                onClick={() => onOpen(job)}
                className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-zinc-50 dark:hover:bg-white/[0.02]"
              >
                <Badge tone={display(job).tone} dot>
                  {display(job).label}
                </Badge>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{scopeLabel(job)}</p>
                  <p className="truncate text-xs text-zinc-500 dark:text-zinc-400">
                    <span title={formatDateTime(job.started_at)}>{timeAgo(job.started_at)}</span> · {duration(job)} ·{" "}
                    {job.requested_by}
                  </p>
                </div>
                <p
                  className={cx(
                    "hidden max-w-[45%] truncate text-sm sm:block",
                    job.status === "failed"
                      ? "text-rose-600 dark:text-rose-400"
                      : display(job).tone === "warning"
                        ? "text-amber-600 dark:text-amber-400"
                        : "text-zinc-600 dark:text-zinc-300",
                  )}
                  title={summary(job)}
                >
                  {summary(job)}
                </p>
                <ChevronRight className="size-4 shrink-0 text-zinc-400" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

/** Detalhe de um job. Se ele ainda estiver rodando, acompanha até terminar. */
export function JobDialog({ job, onClose }: { job: SyncJob | null; onClose: () => void }) {
  const [current, setCurrent] = useState<SyncJob | null>(job);

  useEffect(() => setCurrent(job), [job]);

  useEffect(() => {
    if (!current || current.status !== "running") return;
    const timer = window.setTimeout(() => {
      api.syncJob(current.id).then(setCurrent).catch(() => undefined);
    }, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [current]);

  const result = current?.result ?? null;
  const indexed = result ? Object.entries(result.indexed) : [];
  const errors = result ? Object.entries(result.errors) : [];

  return (
    <Dialog
      open={job !== null}
      onClose={onClose}
      title="Job de indexação"
      size="lg"
      footer={<Button onClick={onClose}>Fechar</Button>}
    >
      {current && (
        <div className="space-y-5 text-sm">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
            <Item label="Status">
              <Badge tone={display(current).tone} dot>
                {display(current).label}
              </Badge>
            </Item>
            <Item label="Escopo">{scopeLabel(current)}</Item>
            <Item label="Pedido por">{current.requested_by}</Item>
            <Item label="Início">{formatDateTime(current.started_at)}</Item>
            <Item label="Fim">{current.finished_at ? formatDateTime(current.finished_at) : "—"}</Item>
            <Item label="Duração">{duration(current)}</Item>
          </dl>

          {current.error && <Alert>{current.error}</Alert>}

          {current.progress && current.progress.repos.length > 0 && (
            <Section title={`Repositórios (${current.progress.repos_done}/${current.progress.repos_total})`}>
              <RepoProgressList repos={current.progress.repos} running={current.status === "running"} />
            </Section>
          )}

          {result && (
            <>
              <Section title={`Indexados (${indexed.length})`}>
                {indexed.length === 0 ? (
                  <p className="text-zinc-500 dark:text-zinc-400">Nenhum.</p>
                ) : (
                  <ul className="divide-y divide-zinc-100 rounded-xl border border-zinc-200 font-mono text-xs dark:divide-zinc-800 dark:border-zinc-800">
                    {indexed.map(([name, commit]) => (
                      <li key={name} className="flex justify-between gap-3 px-3 py-2">
                        <span className="truncate">{name}</span>
                        <span className="text-zinc-500">{commit ? commit.slice(0, 7) : "—"}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </Section>
              {errors.length > 0 && (
                <Section title={`Com erro (${errors.length})`}>
                  <ul className="space-y-2">
                    {errors.map(([name, msg]) => (
                      <li key={name} className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 dark:border-rose-900/60 dark:bg-rose-950/40">
                        <p className="font-mono text-xs font-medium">{name}</p>
                        <p className="mt-0.5 text-xs break-words text-rose-700 dark:text-rose-300">{msg}</p>
                      </li>
                    ))}
                  </ul>
                </Section>
              )}
              {result.remaining > 0 && (
                <p className="text-zinc-500 dark:text-zinc-400">{result.remaining} {plural(result.remaining, "repositório ficou", "repositórios ficaram")} para os próximos lotes.</p>
              )}
            </>
          )}

          {current.status === "running" && (
            <p className="flex items-center gap-2 text-zinc-500 dark:text-zinc-400">
              <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> Acompanhando… atualiza a cada {POLL_MS / 1000} s.
            </p>
          )}
        </div>
      )}
    </Dialog>
  );
}

const REPO_STATUS: Record<RepoProgressStatus, { label: string; tone: BadgeTone }> = {
  queued: { label: "Na fila", tone: "pending" },
  fetching: { label: "Baixando", tone: "accent" },
  indexing: { label: "Indexando", tone: "accent" },
  done: { label: "Pronto", tone: "indexed" },
  error: { label: "Erro", tone: "error" },
  skipped: { label: "Pulado", tone: "ignored" },
};

/** Situação de cada repo do lote: status, barra de arquivos e tempo. */
function RepoProgressList({ repos, running }: { repos: RepoProgress[]; running: boolean }) {
  return (
    <ul className="divide-y divide-zinc-100 rounded-lg border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
      {repos.map((r) => {
        const st = REPO_STATUS[r.status];
        // Job encerrado com repo ainda "indexando" = o retrato final não chegou; não finge andamento.
        const stale = !running && (r.status === "indexing" || r.status === "fetching" || r.status === "queued");
        return (
          <li key={r.name} className="space-y-1.5 px-3 py-2.5">
            <div className="flex items-center gap-3">
              <span className="min-w-0 flex-1 truncate font-mono text-xs" title={r.name}>
                {r.name}
              </span>
              <span className="shrink-0 text-xs text-zinc-500 tabular-nums dark:text-zinc-400">
                {filesLabel(r.files_done, r.files_total)}
                {r.files_unchanged > 0 && <> · {count.format(r.files_unchanged)} sem mudança</>}
                {r.seconds !== null && <> · {formatDuration(r.seconds * 1000)}</>}
              </span>
              <Badge tone={stale ? "ignored" : st.tone} dot>
                {stale ? "Interrompido" : st.label}
              </Badge>
            </div>
            {(r.status === "indexing" || r.status === "done") && r.files_total !== null && r.files_total > 0 && (
              <ProgressBar
                value={r.files_done}
                max={r.files_total}
                tone={r.status === "done" ? "indexed" : "accent"}
                label={`Arquivos de ${r.name}`}
              />
            )}
            {r.error && <p className="text-xs break-words text-rose-700 dark:text-rose-300">{r.error}</p>}
          </li>
        );
      })}
    </ul>
  );
}

function Item({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-zinc-500 dark:text-zinc-400">{label}</dt>
      <dd className="mt-0.5 truncate">{children}</dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold text-zinc-700 dark:text-zinc-200">{title}</h3>
      {children}
    </section>
  );
}
