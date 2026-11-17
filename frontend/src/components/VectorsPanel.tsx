import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Sparkles } from "lucide-react";
import type { VectorEstimate, VectorStatus } from "../api/types";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import { cx, plural, timeAgo } from "../lib/utils";
import { useToast } from "./Toaster";
import { Button, Panel, Skeleton } from "./ui";

const numberFormat = new Intl.NumberFormat("pt-BR");
const usdFormat = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "USD" });
const POLL_MS = 3000;

/** Custo estimado antes de gerar (a cobrança real vem da Voyage): "~US$ 0,42" ou "< US$ 0,01". */
function cost(e: VectorEstimate | null | undefined): string | null {
  if (!e || e.cost_usd === null) return null;
  return e.cost_usd < 0.01 ? "< US$ 0,01" : `~${usdFormat.format(e.cost_usd)}`;
}

function percent(part: number, total: number): string {
  return total ? `${((part / total) * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%` : "0%";
}

/**
 * Vetores extras da busca (resumos e perguntas dos arquivos, cartões dos repos). Nada é embedado quando um resumo
 * ou cartão muda: o que diverge fica pendente aqui, com o custo estimado, e "Gerar" (custo na Voyage) roda em
 * segundo plano no servidor, para todos os tipos ou só um.
 */
export function VectorsPanel({ canRun }: { canRun: boolean }) {
  const { push } = useToast();
  const [status, setStatus] = useState<VectorStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const wasRunning = useRef(false);

  const load = useCallback(async () => {
    try {
      const st = await api.vectorStatus();
      setStatus(st);
      setError(null);
      if (wasRunning.current && !st.running && st.last_run) {
        const run = st.last_run;
        push(
          run.status === "failed"
            ? { tone: "error", title: "A geração de vetores falhou", description: run.error ?? undefined }
            : {
                tone: "success",
                title: "Vetores gerados",
                description: run.result
                  ? `${numberFormat.format(run.result.embedded)} ${plural(run.result.embedded, "gerado", "gerados")} · ${numberFormat.format(run.result.deleted)} ${plural(run.result.deleted, "apagado", "apagados")}`
                  : undefined,
              },
        );
      }
      wasRunning.current = st.running;
    } catch (err) {
      setError(describeError(err));
    }
  }, [push]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!status?.running) return;
    const id = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(id);
  }, [status?.running, load]);

  async function start(kind?: string) {
    setStarting(true);
    try {
      const st = await api.vectorEmbed(kind ? { kinds: [kind] } : undefined);
      setStatus(st);
      wasRunning.current = st.running;
    } catch (err) {
      push({ tone: "error", title: "Não consegui iniciar a geração", description: describeError(err) });
    } finally {
      setStarting(false);
    }
  }

  const kinds = status ? Object.entries(status.kinds) : [];
  const nothingYet = status !== null && kinds.every(([, c]) => c.sources === 0 && c.obsolete === 0);

  return (
    <section aria-labelledby="indexing-vectors" className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="indexing-vectors" className="font-mono text-[10px] tracking-[0.16em] text-stone-500 uppercase dark:text-stone-400">
            Vetores de resumos e cartões
          </h2>
          <p className="mt-1 text-xs text-stone-500 dark:text-stone-400">
            O resumo de cada arquivo, as perguntas dele e os cartões dos repos também entram na busca semântica.
            Resumo ou cartão novo fica pendente até alguém gerar.
          </p>
        </div>
        {canRun && status && (status.pending > 0 || status.obsolete > 0 || status.running) && (
          <Button
            size="sm"
            variant="secondary"
            loading={starting || status.running}
            disabled={status.running}
            onClick={() => void start()}
            title={status.pending > 0 ? "Gera os vetores pendentes (custo de embedding na Voyage)" : "Apaga os vetores obsoletos (sem custo)"}
          >
            <Sparkles className="size-3.5" aria-hidden="true" />
            {status.running
              ? "Gerando…"
              : status.pending > 0
                ? `Gerar ${numberFormat.format(status.pending)} ${plural(status.pending, "pendente", "pendentes")}${cost(status.pending_estimate) ? ` · ${cost(status.pending_estimate)}` : ""}`
                : "Limpar obsoletos"}
          </Button>
        )}
      </div>

      {status === null && error === null && <Skeleton className="h-20 w-full rounded-xl" />}
      {error !== null && status === null && (
        <Panel className="px-5 py-4 text-sm text-rose-600 dark:text-rose-400">Não consegui ler o estado: {error}</Panel>
      )}
      {status !== null && nothingYet && (
        <Panel className="px-5 py-4 text-sm text-stone-500 dark:text-stone-400">
          Nenhum resumo de arquivo nem cartão de repo escrito ainda: não há o que vetorizar.
        </Panel>
      )}
      {status !== null && !nothingYet && (
        <Panel className="divide-y divide-stone-200 dark:divide-stone-800">
          {kinds.map(([kind, c]) => (
            <KindRow
              key={kind}
              label={c.label}
              sources={c.sources}
              embedded={c.embedded}
              pending={c.pending}
              obsolete={c.obsolete}
              estimate={cost(c.pending_estimate)}
              onGenerate={canRun && !status.running && c.pending > 0 && c.pending < status.pending ? () => void start(kind) : undefined}
            />
          ))}
          {status.summary_coverage && status.summary_coverage.files > 0 && (
            <p className="px-5 py-3 text-xs text-stone-500 dark:text-stone-400">
              Resumos: {numberFormat.format(status.summary_coverage.fresh + status.summary_coverage.stale)} de{" "}
              {numberFormat.format(status.summary_coverage.files)} arquivos de código (
              {percent(status.summary_coverage.fresh + status.summary_coverage.stale, status.summary_coverage.files)})
              {status.summary_coverage.skipped > 0 && (
                <> · {numberFormat.format(status.summary_coverage.skipped)} fora da fila (gerados ou curtos)</>
              )}
              {(status.stale_summaries ?? 0) > 0 && (
                <span className="text-amber-700 dark:text-amber-400">
                  {" "}
                  · {numberFormat.format(status.stale_summaries ?? 0)}{" "}
                  {plural(status.stale_summaries ?? 0, "desatualizado", "desatualizados")}: o código mudou, o resumo vota
                  menos até ser refeito
                </span>
              )}
            </p>
          )}
          {(status.other_model ?? 0) > 0 && (
            <p className="px-5 py-3 text-xs text-amber-700 dark:text-amber-400">
              {numberFormat.format(status.other_model ?? 0)} {plural(status.other_model ?? 0, "vetor", "vetores")} de outro
              modelo: a busca não usa os vetores extras até gerar de novo com {status.model}.
            </p>
          )}
          <p className="px-5 py-3 text-xs text-stone-500 dark:text-stone-400">
            Última geração: {status.last_embedded_at ? timeAgo(status.last_embedded_at) : "nunca"}
            {status.pending > 0 && (
              <>
                {" "}
                · ~{numberFormat.format(status.pending_chars)} caracteres a embedar
                {cost(status.pending_estimate) && <> ({cost(status.pending_estimate)})</>}
              </>
            )}
            {status.last_run?.status === "failed" && (
              <span className="text-rose-600 dark:text-rose-400"> · a última geração falhou: {status.last_run.error}</span>
            )}
          </p>
        </Panel>
      )}
    </section>
  );
}

function KindRow({
  label,
  sources,
  embedded,
  pending,
  obsolete,
  estimate,
  onGenerate,
}: {
  label: string;
  sources: number;
  embedded: number;
  pending: number;
  obsolete: number;
  estimate: string | null;
  onGenerate?: () => void;
}) {
  const width = (n: number) => `${sources ? (n / sources) * 100 : 0}%`;
  return (
    <div className="grid gap-2 px-5 py-3 sm:grid-cols-[14rem_1fr] sm:items-center">
      <span className="text-sm text-stone-700 dark:text-stone-200">{label}</span>
      <div className="space-y-1.5">
        <div
          className="flex h-1.5 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-800"
          role="img"
          aria-label={`${embedded} de ${sources} com vetor em dia, ${pending} aguardando`}
        >
          <span className="h-full bg-violet-500" style={{ width: width(embedded) }} />
          <span className="h-full bg-amber-400" style={{ width: width(pending) }} />
        </div>
        <p className="text-xs text-stone-500 dark:text-stone-400">
          <Legend dot="bg-violet-500">{numberFormat.format(embedded)} em dia</Legend>
          <Legend dot="bg-amber-400">
            {numberFormat.format(pending)} aguardando embedding{pending > 0 && estimate ? ` (${estimate})` : ""}
          </Legend>
          {obsolete > 0 && <Legend dot="bg-stone-300 dark:bg-stone-700">{numberFormat.format(obsolete)} {plural(obsolete, "obsoleto", "obsoletos")}</Legend>}
          {onGenerate && (
            <button
              type="button"
              onClick={onGenerate}
              className="font-medium text-accent-600 underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-accent-500 dark:text-accent-400"
              title="Gera só os pendentes deste tipo (custo de embedding na Voyage)"
            >
              gerar só estes
            </button>
          )}
        </p>
      </div>
    </div>
  );
}

function Legend({ dot, children }: { dot: string; children: ReactNode }) {
  return (
    <span className="mr-4 inline-flex items-center gap-1.5 whitespace-nowrap">
      <span className={cx("size-1.5 rounded-full", dot)} aria-hidden="true" />
      {children}
    </span>
  );
}
