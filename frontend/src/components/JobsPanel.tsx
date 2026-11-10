import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowRight, ChevronRight, Clock, History, LoaderCircle, RotateCcw } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { RepoProgress, RepoProgressStatus, SyncJob, SyncProgress } from "../api/types";
import { useIndex } from "../data/IndexProvider";
import { useNavigation } from "../lib/navigation";
import { cx, formatDateTime, formatDuration, jobActive, plural, timeAgo } from "../lib/utils";
import { Dialog } from "./Dialog";
import { KindIcon, type EntityKind } from "./kind";
import { Alert, Badge, Button, EmptyState, Panel, ProgressBar, Skeleton, type BadgeTone } from "./ui";

const POLL_MS = 3000;

const STATUS: Record<SyncJob["status"], { label: string; tone: BadgeTone }> = {
  queued: { label: "Na fila", tone: "pending" },
  running: { label: "Rodando", tone: "accent" },
  done: { label: "Concluído", tone: "indexed" },
  failed: { label: "Falhou", tone: "error" },
};

/** Status exibido: um job "done" em que algum repo falhou aparece como "Com erros". */
function display(job: SyncJob): { label: string; tone: BadgeTone } {
  const errors = job.result ? Object.keys(job.result.errors).length : 0;
  if (job.status === "done" && errors > 0) return { label: "Com erros", tone: "warning" };
  return STATUS[job.status];
}

export interface JobsState {
  jobs: SyncJob[] | null;
  error: string | null;
  loading: boolean;
  running: SyncJob | null;
  refresh: () => Promise<void>;
}

/**
 * Jobs de sync recentes, vindos do servidor (persistidos no banco).
 * Enquanto houver job na fila ou rodando, atualiza sozinho; quando um job termina, chama `onJobFinished`.
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
      const nowRunning = new Set(next.filter(jobActive).map((j) => j.id));
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

  const running = jobs?.find(jobActive) ?? null;

  // Polling só enquanto há job ativo.
  useEffect(() => {
    if (!running) return;
    const timer = window.setTimeout(() => void refresh(), POLL_MS);
    return () => window.clearTimeout(timer);
  }, [running, jobs, refresh]);

  return { jobs, error, loading, running, refresh };
}

/** Tempo total desde o pedido (inclui a espera na fila e as tentativas). */
function duration(job: SyncJob): string {
  const end = job.finished_at ? Date.parse(job.finished_at) : Date.now();
  return formatDuration(end - Date.parse(job.started_at));
}

function scopeLabel(job: SyncJob): string {
  if (job.params?.openapi_domain) return `APIs de ${job.params.openapi_domain}`;
  const repos = job.params?.repos;
  if (repos && repos.length > 0) return repos.length === 1 ? repos[0] : `${repos.length} repositórios`;
  return `lote automático${job.params?.batch ? ` de até ${job.params.batch}` : ""}`;
}

/**
 * De que fonte é o job: APIs (pedido OpenAPI), documentação (todos os repos do pedido são fontes de docs) ou
 * repositórios de código (inclui o lote automático). Vem dos parâmetros e do tipo gravado do repo, não do nome.
 */
function jobSource(job: SyncJob, kindOf: (name: string) => string | undefined): EntityKind {
  if (job.params?.openapi_domain) return "api";
  const repos = job.params?.repos ?? [];
  if (repos.length > 0 && repos.every((r) => kindOf(r) === "docs")) return "doc";
  return "repo";
}

const SOURCE_LABEL: Record<EntityKind, string> = { repo: "Repositórios", doc: "Documentação", api: "APIs" };

/**
 * Tentativa atual, só quando o job já foi tentado mais de uma vez. O limite de tentativas é configuração do
 * servidor (`SYNC_JOB_MAX_ATTEMPTS`) e não vem na API: mostra só o número.
 */
function attemptLabel(job: SyncJob): string | null {
  const n = job.attempts ?? 0;
  return n > 1 ? `tentativa ${n}` : null;
}

/**
 * Job que já passou por um worker e voltou: de novo na fila (o worker caiu, foi encerrado ou a tentativa falhou),
 * ou rodando numa tentativa depois da primeira. Null = caminho normal.
 */
function requeueNote(job: SyncJob): string | null {
  const n = job.attempts ?? 0;
  if (job.status === "queued" && (n > 0 || job.error)) {
    return job.error ? `Devolvido à fila: ${job.error}` : "Devolvido à fila: será retomado pelo próximo worker livre.";
  }
  if (job.status === "running" && n > 1) {
    return `Retomado depois de ${n - 1} ${plural(n - 1, "tentativa que não terminou", "tentativas que não terminaram")}.`;
  }
  return null;
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
  if (job.status === "queued") return requeueNote(job) ? "de volta à fila, aguardando o worker…" : "na fila, aguardando o worker…";
  if (job.status === "running") return job.progress ? progressLine(job.progress) : "em andamento…";
  if (job.result) {
    const ok = Object.keys(job.result.indexed).length;
    const bad = Object.keys(job.result.errors).length;
    if (ok === 0 && bad === 0) return "nada a indexar";
    return `${ok} ${plural(ok, "indexado", "indexados")}${bad ? ` · ${bad} com erro` : ""}`;
  }
  return job.error ?? "sem resultado";
}

/** Aviso de job devolvido à fila ou retomado (âmbar: pede atenção, não é falha). */
function RequeueNote({ job, className }: { job: SyncJob; className?: string }) {
  const note = requeueNote(job);
  if (!note) return null;
  return (
    <p className={cx("flex items-start gap-2 text-sm text-amber-700 dark:text-amber-300", className)}>
      <RotateCcw className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
      <span className="min-w-0 break-words">{note}</span>
    </p>
  );
}

function WorkerName({ job }: { job: SyncJob }) {
  if (!job.worker) return <>—</>;
  const title = job.lease_until ? `${job.worker} · reserva válida até ${formatDateTime(job.lease_until)}` : job.worker;
  return (
    <span className="font-mono text-xs" title={title}>
      {job.worker}
    </span>
  );
}

/**
 * Job ativo em destaque (página Indexação): na fila ou rodando, tentativa, worker, etapa e o andamento de cada
 * repositório. Sem job ativo, uma linha dizendo isso.
 */
export function ActiveJobPanel({ job, onOpen }: { job: SyncJob | null; onOpen: (job: SyncJob) => void }) {
  if (!job) {
    return (
      <Panel className="flex items-center gap-3 px-5 py-4 text-sm text-stone-500 dark:text-stone-400">
        <Clock className="size-4 shrink-0" aria-hidden="true" />
        Nenhuma indexação na fila nem rodando.
      </Panel>
    );
  }
  const queued = job.status === "queued";
  // Na fila, o andamento gravado é de uma tentativa anterior: não finge que está andando.
  const p = queued ? null : (job.progress ?? null);
  const showBar = p !== null && (p.phase === "index" || p.phase === "finish") && p.files_total > 0;
  const attempt = attemptLabel(job);
  return (
    <Panel className="overflow-hidden shadow-[inset_3px_0_0_var(--color-accent-500)]">
      <div role="status" className="space-y-4 py-4 pr-4 pl-5 sm:pr-5 sm:pl-6">
        <div className="flex flex-wrap items-start gap-3">
          {queued ? (
            <Clock className="mt-0.5 size-4 shrink-0 text-accent-500" aria-hidden="true" />
          ) : (
            <LoaderCircle className="mt-0.5 size-4 shrink-0 animate-spin text-accent-500" aria-hidden="true" />
          )}
          <div className="min-w-0 flex-1">
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <Badge tone={STATUS[job.status].tone} dot>
                {queued ? "Na fila" : "Rodando"}
              </Badge>
              <span className="min-w-0 truncate font-medium">{scopeLabel(job)}</span>
              {attempt && (
                <Badge tone="warning" className="tabular-nums">
                  {attempt}
                </Badge>
              )}
            </p>
            <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">
              {queued ? "Aguardando o worker de indexação." : p ? progressLine(p) : "Em andamento…"}
            </p>
          </div>
          <Button variant="secondary" size="sm" onClick={() => onOpen(job)}>
            Ver detalhes
          </Button>
        </div>

        <RequeueNote job={job} />

        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm sm:grid-cols-4">
          <Item label="Pedido por">{job.requested_by || "—"}</Item>
          <Item label="Na fila desde">
            <span title={formatDateTime(job.started_at)}>{timeAgo(job.started_at)}</span>
          </Item>
          <Item label={queued ? "Última tentativa" : "Tentativa atual desde"}>
            {job.run_started_at ? <span title={formatDateTime(job.run_started_at)}>{timeAgo(job.run_started_at)}</span> : "—"}
          </Item>
          <Item label="Worker">
            <WorkerName job={job} />
          </Item>
        </dl>

        {showBar && p && <ProgressBar value={p.files_done} max={p.files_total} label="Arquivos processados no lote" />}
        {p && p.repos.length > 0 && (
          <Section title={`Repositórios (${p.repos_done}/${p.repos_total})`}>
            <RepoProgressList repos={p.repos} running />
          </Section>
        )}
      </div>
    </Panel>
  );
}

/**
 * Linha curta nas páginas de catálogo (Repositórios, APIs) enquanto há job ativo: o detalhe fica em Indexação.
 * Sem acesso à página Indexação, só informa.
 */
export function ActiveJobNotice({ job }: { job: SyncJob }) {
  const nav = useNavigation();
  const queued = job.status === "queued";
  const p = queued ? null : job.progress;
  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-stone-200 bg-sheet py-2 pr-2 pl-5 text-sm text-stone-700 shadow-[inset_3px_0_0_var(--color-accent-500)] dark:border-stone-800 dark:bg-stone-900 dark:text-stone-200"
    >
      {queued ? (
        <Clock className="size-4 shrink-0 text-accent-500" aria-hidden="true" />
      ) : (
        <LoaderCircle className="size-4 shrink-0 animate-spin text-accent-500" aria-hidden="true" />
      )}
      <p className="min-w-0 flex-[1_1_14rem] py-1 sm:truncate">
        <span className="font-medium">{queued ? "Indexação na fila" : "Indexação em andamento"}</span>
        <span className="text-stone-500 dark:text-stone-400"> · {p ? progressLine(p) : scopeLabel(job)}</span>
      </p>
      {nav?.can("indexing") && (
        <Button variant="ghost" size="sm" onClick={() => nav.open("indexing")}>
          Acompanhar em Indexação
          <ArrowRight className="size-3.5" aria-hidden="true" />
        </Button>
      )}
    </div>
  );
}

/** Histórico dos últimos jobs de todas as fontes, com detalhe ao clicar. */
export function JobsPanel({ state, onOpen }: { state: JobsState; onOpen: (job: SyncJob) => void }) {
  const { jobs, error, loading, refresh } = state;
  const { repos } = useIndex();
  const kinds = useMemo(() => new Map((repos ?? []).map((r) => [r.name, r.kind] as const)), [repos]);
  return (
    <Panel className="overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-stone-200 px-4 py-3 dark:border-stone-800">
        <div>
          <h2 className="font-semibold">Histórico de jobs</h2>
          <p className="text-xs text-stone-500 dark:text-stone-400">
            Últimos 20 de todas as fontes (repositórios, documentação e APIs), guardados no servidor.
          </p>
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
        <ul className="divide-y divide-stone-100 dark:divide-stone-800">
          {jobs.map((job) => {
            const source = jobSource(job, (name) => kinds.get(name));
            const attempt = attemptLabel(job);
            const shown = display(job);
            return (
              <li key={job.id}>
                <button
                  type="button"
                  onClick={() => onOpen(job)}
                  className="flex w-full items-center gap-3 px-4 py-3 text-left outline-none transition hover:bg-stone-50 focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-inset dark:hover:bg-white/[0.02]"
                >
                  <KindIcon kind={source} size="sm" label={SOURCE_LABEL[source]} />
                  <Badge tone={shown.tone} dot className="justify-center sm:min-w-24">
                    {shown.label}
                  </Badge>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      <span className="sr-only">{SOURCE_LABEL[source]}: </span>
                      {scopeLabel(job)}
                    </p>
                    <p className="truncate text-xs text-stone-500 dark:text-stone-400">
                      <span title={formatDateTime(job.started_at)}>{timeAgo(job.started_at)}</span> · {duration(job)} ·{" "}
                      {job.requested_by}
                      {attempt && <span className="text-amber-700 dark:text-amber-300"> · {attempt}</span>}
                    </p>
                  </div>
                  <p
                    className={cx(
                      "hidden max-w-[45%] truncate text-sm sm:block",
                      job.status === "failed"
                        ? "text-rose-600 dark:text-rose-400"
                        : shown.tone === "warning" || requeueNote(job)
                          ? "text-amber-600 dark:text-amber-400"
                          : "text-stone-600 dark:text-stone-300",
                    )}
                    title={summary(job)}
                  >
                    {summary(job)}
                  </p>
                  <ChevronRight className="size-4 shrink-0 text-stone-400" aria-hidden="true" />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

/** Detalhe de um job. Se ele ainda estiver na fila ou rodando, acompanha até terminar. */
export function JobDialog({ job, onClose }: { job: SyncJob | null; onClose: () => void }) {
  const [current, setCurrent] = useState<SyncJob | null>(job);

  useEffect(() => setCurrent(job), [job]);

  useEffect(() => {
    if (!current || !jobActive(current)) return;
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
            <Item label="Pedido em">{formatDateTime(current.started_at)}</Item>
            <Item label="Início da tentativa">{current.run_started_at ? formatDateTime(current.run_started_at) : "—"}</Item>
            <Item label="Fim">{current.finished_at ? formatDateTime(current.finished_at) : "—"}</Item>
            <Item label="Duração">{duration(current)}</Item>
            <Item label="Tentativa">{current.attempts ? <span className="tabular-nums">{current.attempts}</span> : "—"}</Item>
            <Item label="Worker">
              <WorkerName job={current} />
            </Item>
          </dl>

          {requeueNote(current) ? <RequeueNote job={current} /> : current.error && <Alert>{current.error}</Alert>}

          {current.progress && current.progress.repos.length > 0 && (
            <Section title={`Repositórios (${current.progress.repos_done}/${current.progress.repos_total})`}>
              <RepoProgressList repos={current.progress.repos} running={current.status === "running"} />
            </Section>
          )}

          {result && (
            <>
              <Section title={`Indexados (${indexed.length})`}>
                {indexed.length === 0 ? (
                  <p className="text-stone-500 dark:text-stone-400">Nenhum.</p>
                ) : (
                  <ul className="divide-y divide-stone-100 rounded-xl border border-stone-200 font-mono text-xs dark:divide-stone-800 dark:border-stone-800">
                    {indexed.map(([name, commit]) => (
                      <li key={name} className="flex justify-between gap-3 px-3 py-2">
                        <span className="truncate">{name}</span>
                        <span className="text-stone-500">{commit ? commit.slice(0, 7) : "—"}</span>
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
                <p className="text-stone-500 dark:text-stone-400">{result.remaining} {plural(result.remaining, "repositório ficou", "repositórios ficaram")} para os próximos lotes.</p>
              )}
            </>
          )}

          {jobActive(current) && (
            <p className="flex items-center gap-2 text-stone-500 dark:text-stone-400">
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
    <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 dark:divide-stone-800 dark:border-stone-800">
      {repos.map((r) => {
        const st = REPO_STATUS[r.status];
        // Job fora de execução com repo ainda "indexando" = o retrato final não chegou; não finge andamento.
        const stale = !running && (r.status === "indexing" || r.status === "fetching" || r.status === "queued");
        return (
          <li key={r.name} className="space-y-1.5 px-3 py-2.5">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="min-w-0 basis-full truncate font-mono text-xs sm:flex-1 sm:basis-auto" title={r.name}>
                {r.name}
              </span>
              <span className="shrink-0 text-xs text-stone-500 tabular-nums dark:text-stone-400">
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
      <dt className="text-xs text-stone-500 dark:text-stone-400">{label}</dt>
      <dd className="mt-0.5 truncate">{children}</dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold text-stone-700 dark:text-stone-200">{title}</h3>
      {children}
    </section>
  );
}
