import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { FlaskConical, Network, RefreshCw } from "lucide-react";
import type { ScanRun, ScansStatus } from "../api/types";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import { plural, timeAgo } from "../lib/utils";
import { useToast } from "./Toaster";
import { Button, Panel, Skeleton } from "./ui";

const numberFormat = new Intl.NumberFormat("pt-BR");
const POLL_MS = 2000;
type Kind = "graph" | "tests";
const KIND_LABEL: Record<Kind, string> = { graph: "Grafo de chamadas", tests: "Casos de teste" };

function percent(part: number, total: number): string {
  return total ? `${((part / total) * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%` : "0%";
}

/**
 * Leituras dos clones, sem custo de API: o grafo de chamadas (quem chama quem) e os casos de teste. O fim de cada
 * sync já relê os repos que mudaram; aqui dá para ler agora (o que nunca foi lido ou mudou) ou reler tudo.
 */
export function ScansPanel({ canRun }: { canRun: boolean }) {
  const { push } = useToast();
  const [status, setStatus] = useState<ScansStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState<Kind | null>(null);
  const wasRunning = useRef<Record<Kind, boolean>>({ graph: false, tests: false });

  const load = useCallback(async () => {
    try {
      const st = await api.scansStatus();
      setStatus(st);
      setError(null);
      for (const kind of ["graph", "tests"] as const) {
        const run = st[kind].last_run;
        if (wasRunning.current[kind] && !st[kind].running && run) push(finishedToast(kind, run));
        wasRunning.current[kind] = st[kind].running;
      }
    } catch (err) {
      setError(describeError(err));
    }
  }, [push]);

  useEffect(() => {
    void load();
  }, [load]);

  const running = status !== null && (status.graph.running || status.tests.running);
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(id);
  }, [running, load]);

  async function start(kind: Kind, force: boolean) {
    setStarting(kind);
    try {
      const st = await api.startScan(kind, force);
      setStatus(st);
      wasRunning.current[kind] = st[kind].running;
    } catch (err) {
      push({ tone: "error", title: "Não consegui iniciar a leitura", description: describeError(err) });
    } finally {
      setStarting(null);
    }
  }

  return (
    <section aria-labelledby="indexing-scans" className="space-y-3">
      <div>
        <h2 id="indexing-scans" className="font-mono text-[10px] tracking-[0.16em] text-stone-500 uppercase dark:text-stone-400">
          Leituras do código (sem custo)
        </h2>
        <p className="mt-1 text-xs text-stone-500 dark:text-stone-400">
          Lidas direto dos clones, sem embedding. O fim de cada sync relê os repos que mudaram.
        </p>
      </div>
      {status === null && error === null && <Skeleton className="h-24 w-full rounded-xl" />}
      {error !== null && status === null && (
        <Panel className="px-5 py-4 text-sm text-rose-600 dark:text-rose-400">Não consegui ler o estado: {error}</Panel>
      )}
      {status !== null && (
        <Panel className="divide-y divide-stone-200 dark:divide-stone-800">
          <ScanRow
            icon={<Network className="size-4 text-stone-400" aria-hidden="true" />}
            label={KIND_LABEL.graph}
            empty={status.graph.repos === 0}
            emptyText="Ainda não lido: sem ele, não há “quem chama” no arquivo nem prioridade na fila de resumos."
            detail={
              <>
                {numberFormat.format(status.graph.edges)} {plural(status.graph.edges, "ligação", "ligações")} em{" "}
                {numberFormat.format(status.graph.repos)} {plural(status.graph.repos, "repo", "repos")} ·{" "}
                {percent(status.graph.resolved, status.graph.calls)} das chamadas ligadas (o resto é de bibliotecas)
                {status.graph.inferred > 0 && <> · {numberFormat.format(status.graph.inferred)} só pelo nome</>}
              </>
            }
            lastScan={status.graph.last_scan}
            run={status.graph.last_run}
            running={status.graph.running}
            canRun={canRun}
            starting={starting === "graph"}
            onStart={(force) => void start("graph", force)}
          />
          <ScanRow
            icon={<FlaskConical className="size-4 text-stone-400" aria-hidden="true" />}
            label={KIND_LABEL.tests}
            empty={status.tests.repos === 0}
            emptyText="Ainda não lidos: sem eles, não há “casos de teste” nos resultados nem no arquivo."
            detail={
              <>
                {numberFormat.format(status.tests.cases)} {plural(status.tests.cases, "caso", "casos")} em{" "}
                {numberFormat.format(status.tests.files)} {plural(status.tests.files, "arquivo", "arquivos")} de teste ·{" "}
                {percent(status.tests.linked, status.tests.cases)} ligados ao código testado
              </>
            }
            lastScan={status.tests.last_scan}
            run={status.tests.last_run}
            running={status.tests.running}
            canRun={canRun}
            starting={starting === "tests"}
            onStart={(force) => void start("tests", force)}
          />
        </Panel>
      )}
    </section>
  );
}

function finishedToast(kind: Kind, run: ScanRun) {
  if (run.status === "failed") {
    return { tone: "error" as const, title: `A leitura de ${KIND_LABEL[kind].toLowerCase()} falhou`, description: run.error ?? undefined };
  }
  const repos = run.result?.repos ?? 0;
  return {
    tone: "success" as const,
    title: `${KIND_LABEL[kind]} atualizado`,
    description: `${numberFormat.format(repos)} ${plural(repos, "repo lido", "repos lidos")}`,
  };
}

function ScanRow({
  icon,
  label,
  empty,
  emptyText,
  detail,
  lastScan,
  run,
  running,
  canRun,
  starting,
  onStart,
}: {
  icon: ReactNode;
  label: string;
  empty: boolean;
  emptyText: string;
  detail: ReactNode;
  lastScan: string | null;
  run: ScanRun | null;
  running: boolean;
  canRun: boolean;
  starting: boolean;
  onStart: (force: boolean) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3">
      {icon}
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="text-sm text-stone-700 dark:text-stone-200">{label}</p>
        <p className="text-xs text-stone-500 dark:text-stone-400">
          {empty ? <span className="text-amber-700 dark:text-amber-400">{emptyText}</span> : detail}
          {lastScan && <> · lido {timeAgo(lastScan)}</>}
          {run?.status === "failed" && !running && (
            <span className="text-rose-600 dark:text-rose-400"> · a última leitura falhou: {run.error}</span>
          )}
        </p>
      </div>
      {canRun && (
        <div className="flex items-center gap-3">
          {!empty && !running && (
            <button
              type="button"
              onClick={() => onStart(true)}
              className="text-xs font-medium text-stone-500 underline-offset-2 hover:text-accent-600 hover:underline dark:text-stone-400 dark:hover:text-accent-400"
              title="Relê todos os repos, mesmo os que não mudaram (sem custo de API)"
            >
              reler tudo
            </button>
          )}
          <Button
            size="sm"
            variant="secondary"
            loading={starting || running}
            disabled={running}
            onClick={() => onStart(false)}
            title="Lê os repos que mudaram ou nunca foram lidos (sem custo de API)"
          >
            <RefreshCw className="size-3.5" aria-hidden="true" />
            {running ? "Lendo…" : empty ? "Ler agora" : "Atualizar"}
          </Button>
        </div>
      )}
    </div>
  );
}
