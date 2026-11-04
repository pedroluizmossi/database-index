import { useEffect, useState } from "react";
import { CircleCheck, CircleX, LoaderCircle } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { SyncJob, SyncResult } from "../api/types";
import { useToast } from "./Toaster";
import { Dialog } from "./Dialog";
import { Alert, Button, Field, inputClass } from "./ui";

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

/**
 * Indexação em duas etapas: prévia sem custo (dry-run), depois confirmação.
 * Só então o job roda em segundo plano, e o diálogo acompanha até o fim.
 */
export function SyncDialog({ open, onClose, names, defaultBatch = 5, onFinished, onStarted }: SyncDialogProps) {
  const { push } = useToast();
  const [batch, setBatch] = useState(defaultBatch);
  const [preview, setPreview] = useState<SyncResult | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [starting, setStarting] = useState(false);
  const [job, setJob] = useState<SyncJob | null>(null);
  const [startError, setStartError] = useState<string | null>(null);

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
    // names é uma lista nova a cada render; comparamos pelo conteúdo.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, batch, names?.join("|")]);

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
            push({
              tone: next.status === "done" ? "success" : "error",
              title: next.status === "done" ? "Indexação concluída" : "Indexação falhou",
              description: next.error ?? undefined,
            });
          }
        })
        .catch((err: unknown) => {
          setJob({ ...job, status: "failed", error: describeError(err) });
        });
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

  const selected = preview?.selected ?? [];
  const finished = job !== null && job.status !== "running";
  const title = names ? `Indexar ${names.length === 1 ? names[0] : `${names.length} repositórios`}` : "Indexar lote";

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      description="Indexar gera custo de embedding na Voyage para os trechos novos. Confira antes."
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
      {loadingPreview && <p className="flex items-center gap-2 text-sm"><LoaderCircle className="size-4 animate-spin" /> Calculando a prévia…</p>}

      {preview && !job && (
        <div className="space-y-3">
          <p className="text-sm">
            <strong>{selected.length}</strong> repo(s) seriam processados
            {preview.remaining > 0 && <> · <span className="text-zinc-500">{preview.remaining} ficam para depois</span></>}.
          </p>
          {selected.length > 0 && (
            <ul className="max-h-60 divide-y divide-zinc-100 overflow-y-auto rounded-xl border border-zinc-200 font-mono text-xs dark:divide-zinc-800 dark:border-zinc-800">
              {selected.map((n) => (
                <li key={n} className="px-3 py-2">{n}</li>
              ))}
            </ul>
          )}
          {startError && <Alert>{startError}</Alert>}
        </div>
      )}

      {job && (
        <div className="space-y-3">
          <p className="flex items-center gap-2 text-sm font-medium">
            {job.status === "running" && <><LoaderCircle className="size-4 animate-spin text-indigo-500" /> Indexando… pode levar alguns minutos.</>}
            {job.status === "done" && <><CircleCheck className="size-4 text-emerald-500" /> Concluído.</>}
            {job.status === "failed" && <><CircleX className="size-4 text-rose-500" /> Falhou.</>}
          </p>
          {job.error && <Alert>{job.error}</Alert>}
          {job.result && (
            <div className="space-y-2 text-sm">
              <p>{Object.keys(job.result.indexed).length} indexado(s), {Object.keys(job.result.errors).length} com erro.</p>
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
