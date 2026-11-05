import { useEffect, useMemo, useState } from "react";
import { CircleCheck, CircleX, Info, LoaderCircle } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { DocsPreview, Repo, SyncJob, SyncResult } from "../api/types";
import { useIndex } from "../data/IndexProvider";
import { formatDateTime, repoShortName, timeAgo } from "../lib/utils";
import { useToast } from "./Toaster";
import { Dialog } from "./Dialog";
import { Alert, Badge, Button, Field, Skeleton, inputClass } from "./ui";

interface SyncDialogProps {
  open: boolean;
  onClose: () => void;
  /** Repos a indexar agora. Null = um lote automático dos pendentes/alterados. */
  names: string[] | null;
  defaultBatch?: number;
  onFinished: () => void;
  /** Chamado quando o job é criado no servidor (para a lista de jobs aparecer na hora). */
  onStarted?: () => void;
}

const POLL_MS = 2000;
const MAX_BATCH = 50;
const numberFormat = new Intl.NumberFormat("pt-BR");

function formatSize(kb: number | null | undefined): string {
  if (!kb) return "—";
  if (kb < 1024) return `${numberFormat.format(kb)} KB`;
  return `${(kb / 1024).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} MB`;
}

/** O que acontece com cada repo ao indexar. */
/** Prévia de uma wiki: quantas páginas entrariam, por raiz, e a lista (sem baixar conteúdo). */
function DocsPreviewBlock({ name }: { name: string }) {
  const [data, setData] = useState<DocsPreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api
      .docsPreview(name)
      .then((d) => active && setData(d))
      .catch((err) => active && setError(describeError(err)));
    return () => {
      active = false;
    };
  }, [name]);

  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Skeleton className="h-24 w-full" />;

  return (
    <section className="space-y-3 rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-medium">
          {numberFormat.format(data.total)} páginas em {data.roots.length} raízes
        </p>
        <a href={data.url} target="_blank" rel="noopener noreferrer" className="truncate text-xs text-accent-600 hover:underline dark:text-accent-400">
          {data.url}
        </a>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {data.roots.map((r) => (
          <Badge key={r.root}>
            {r.root} · {numberFormat.format(r.pages)}
          </Badge>
        ))}
      </div>
      <ul className="max-h-[30dvh] divide-y divide-zinc-100 overflow-y-auto rounded-lg border border-zinc-100 dark:divide-zinc-800 dark:border-zinc-800">
        {data.pages.map((p) => (
          <li key={p.path} className="px-2.5 py-2">
            <p className="truncate text-sm" title={p.title}>{p.title}</p>
            <p className="truncate font-mono text-xs text-zinc-500 dark:text-zinc-400">{p.path}</p>
          </li>
        ))}
      </ul>
      {data.truncated && (
        <p className="text-xs text-zinc-500 dark:text-zinc-400">
          Mostrando {numberFormat.format(data.pages.length)} de {numberFormat.format(data.total)} (o restante entra normalmente na indexação).
        </p>
      )}
    </section>
  );
}

function plan(repo: Repo | undefined): { label: string; tone: "accent" | "neutral" | "warning"; hint: string } {
  if (!repo || repo.chunks === 0) {
    const what = repo?.kind === "docs" ? "as páginas selecionadas" : "todo o código (após os filtros)";
    return { label: "1ª indexação", tone: "accent", hint: `${what} vai para a Voyage` };
  }
  if (repo.outdated) {
    return { label: "Atualizar", tone: "warning", hint: "houve push: só arquivos alterados vão para a Voyage" };
  }
  return { label: "Revarrer", tone: "neutral", hint: "sem push novo: quase nada (ou nada) vai para a Voyage" };
}

/**
 * Indexação em duas etapas: prévia sem custo (dry-run) com os detalhes de cada repo,
 * depois confirmação. Só então o job roda em segundo plano, e o diálogo acompanha até o fim.
 */
export function SyncDialog({ open, onClose, names, defaultBatch = 5, onFinished, onStarted }: SyncDialogProps) {
  const { push } = useToast();
  const { repos } = useIndex();
  const [batch, setBatch] = useState(defaultBatch);
  const [preview, setPreview] = useState<SyncResult | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [starting, setStarting] = useState(false);
  const [job, setJob] = useState<SyncJob | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const namesKey = names?.join("|") ?? "";

  // Ao abrir (ou mudar o tamanho do lote), refaz a prévia.
  useEffect(() => {
    if (!open) return;
    let active = true;
    setPreview(null);
    setPreviewError(null);
    setJob(null);
    setStartError(null);
    setLoadingPreview(true);
    api
      .syncPreview(names, batch)
      .then((res) => active && setPreview(res))
      .catch((err: unknown) => active && setPreviewError(describeError(err)))
      .finally(() => active && setLoadingPreview(false));
    return () => {
      active = false;
    };
    // `names` é uma lista nova a cada render: a dependência é o conteúdo (namesKey).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, batch, namesKey]);

  // Acompanha o job até terminar.
  useEffect(() => {
    if (!job || job.status !== "running") return;
    const timer = window.setTimeout(() => {
      api
        .syncJob(job.id)
        .then((next) => {
          setJob(next);
          if (next.status !== "running") {
            onFinished();
            const failedRepos = next.result ? Object.keys(next.result.errors).length : 0;
            push({
              tone: next.status === "done" && failedRepos === 0 ? "success" : "error",
              title:
                next.status === "failed"
                  ? "Indexação falhou"
                  : failedRepos > 0
                    ? `Indexação concluída com ${failedRepos} erro(s)`
                    : "Indexação concluída",
              description: next.error ?? undefined,
            });
          }
        })
        .catch((err: unknown) => setJob({ ...job, status: "failed", error: describeError(err) }));
    }, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [job, onFinished, push]);

  async function start() {
    setStarting(true);
    setStartError(null);
    try {
      const started = await api.syncStart(names, batch);
      setJob({
        id: started.job_id,
        requested_by: "",
        status: "running",
        started_at: new Date().toISOString(),
        finished_at: null,
        result: null,
        error: null,
      });
      onStarted?.();
    } catch (err) {
      setStartError(describeError(err));
    } finally {
      setStarting(false);
    }
  }

  const byName = useMemo(() => new Map((repos ?? []).map((r) => [r.name, r])), [repos]);
  const selected = preview?.selected ?? [];
  const details = selected.map((name) => ({ name, repo: byName.get(name) }));
  const firstTime = details.filter((d) => plan(d.repo).tone === "accent").length;
  const totalKb = details.reduce((sum, d) => sum + (d.repo?.size_kb ?? 0), 0);
  const languages = [...new Set(details.map((d) => d.repo?.language).filter(Boolean))] as string[];

  const finished = job !== null && job.status !== "running";
  const title = names
    ? names.length === 1
      ? `Indexar ${names[0]}`
      : `Indexar ${names.length} repositórios`
    : "Indexar lote automático";

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      description="Confira o que vai ser processado. A prévia não gera custo; a indexação gera custo de embedding na Voyage."
      size="lg"
      footer={
        finished ? (
          <Button onClick={onClose}>Fechar</Button>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose} disabled={job?.status === "running"}>
              Cancelar
            </Button>
            <Button
              onClick={() => void start()}
              loading={starting || job?.status === "running"}
              disabled={loadingPreview || !preview || selected.length === 0 || previewError !== null}
            >
              {selected.length > 0 ? `Indexar ${selected.length} repo(s)` : "Nada a indexar"}
            </Button>
          </>
        )
      }
    >
      {!names && (
        <Field label={`Tamanho do lote (até ${MAX_BATCH})`} className="mb-4 max-w-xs">
          <input
            type="number"
            min={1}
            max={MAX_BATCH}
            value={batch}
            onChange={(e) => setBatch(Math.min(MAX_BATCH, Math.max(1, Number(e.target.value) || 1)))}
            className={inputClass}
            disabled={job !== null}
          />
        </Field>
      )}

      {previewError && <Alert>{previewError}</Alert>}

      {loadingPreview && (
        <div className="space-y-3">
          <p className="flex items-center gap-2 text-sm text-zinc-500 dark:text-zinc-400">
            <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
            {names ? "Calculando a prévia…" : "Consultando o GitHub para montar o lote (alguns segundos)…"}
          </p>
          <Skeleton className="h-16 w-full" />
        </div>
      )}

      {preview && !job && (
        <div className="space-y-4">
          {selected.length === 0 ? (
            <p className="text-sm text-zinc-600 dark:text-zinc-300">Nada a indexar: não há repos pendentes ou alterados.</p>
          ) : (
            <>
              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Repositórios" value={numberFormat.format(selected.length)} />
                <Stat label="1ª indexação" value={numberFormat.format(firstTime)} />
                <Stat label="Tamanho no GitHub" value={formatSize(totalKb)} hint="inclui histórico do git" />
                <Stat label="Linguagens" value={languages.length ? languages.slice(0, 3).join(", ") : "—"} />
              </dl>

              <ul className="max-h-[45dvh] divide-y divide-zinc-100 overflow-y-auto rounded-xl border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
                {details.map(({ name, repo }) => {
                  const p = plan(repo);
                  return (
                    <li key={name} className="space-y-1.5 px-3 py-2.5">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium" title={name}>
                            {repo ? repoShortName(repo.name, repo.org) : name}
                          </p>
                          <p className="truncate text-xs text-zinc-500 dark:text-zinc-400">
                            {repo?.kind === "docs"
                              ? "documentação externa (wiki)"
                              : `${repo?.org ?? "repositório avulso"} · ${repo?.language ?? "linguagem desconhecida"} · ${formatSize(repo?.size_kb)}`}
                          </p>
                        </div>
                        <Badge tone={p.tone}>{p.label}</Badge>
                      </div>
                      <p className="text-xs text-zinc-500 dark:text-zinc-400">
                        {repo && repo.chunks > 0
                          ? `${numberFormat.format(repo.chunks)} chunks hoje · indexado ${timeAgo(repo.indexed_at)}`
                          : "ainda não indexado"}
                        {repo?.pushed_at && (
                          <span title={formatDateTime(repo.pushed_at)}> · último push {timeAgo(repo.pushed_at)}</span>
                        )}
                        {" · "}
                        {p.hint}
                      </p>
                      {repo?.error && <p className="text-xs text-rose-600 dark:text-rose-400">último erro: {repo.error}</p>}
                    </li>
                  );
                })}
              </ul>

              {details
                .filter((d) => d.repo?.kind === "docs")
                .map((d) => (
                  <DocsPreviewBlock key={d.name} name={d.name} />
                ))}

              {preview.remaining > 0 && (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  {numberFormat.format(preview.remaining)} repo(s) ficam para os próximos lotes.
                </p>
              )}
              <p className="flex items-start gap-2 text-xs text-zinc-500 dark:text-zinc-400">
                <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                Testes, dependências, builds e arquivos gerados são ignorados, e segredos são mascarados antes do
                envio. Arquivos que não mudaram reaproveitam o cache: só trechos novos geram custo.
              </p>
            </>
          )}
          {startError && <Alert>{startError}</Alert>}
        </div>
      )}

      {job && (
        <div className="space-y-3">
          <p className="flex items-center gap-2 text-sm font-medium">
            {job.status === "running" && (
              <>
                <LoaderCircle className="size-4 animate-spin text-accent-500" /> Indexando… pode levar alguns minutos. Você
                pode fechar: o andamento fica na seção Jobs.
              </>
            )}
            {job.status === "done" && (
              <>
                <CircleCheck className="size-4 text-emerald-500" /> Concluído.
              </>
            )}
            {job.status === "failed" && (
              <>
                <CircleX className="size-4 text-rose-500" /> Falhou.
              </>
            )}
          </p>
          {job.error && <Alert>{job.error}</Alert>}
          {job.result && (
            <div className="space-y-2 text-sm">
              <p>
                {Object.keys(job.result.indexed).length} indexado(s), {Object.keys(job.result.errors).length} com erro.
              </p>
              {Object.entries(job.result.errors).map(([name, msg]) => (
                <p key={name} className="text-rose-600 dark:text-rose-400">
                  <span className="font-mono">{name}</span>: {msg}
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </Dialog>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-800">
      <dt className="text-xs text-zinc-500 dark:text-zinc-400">{label}</dt>
      <dd className="truncate text-sm font-semibold" title={hint ? `${value} (${hint})` : value}>
        {value}
      </dd>
    </div>
  );
}
