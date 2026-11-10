import { useEffect, useRef, useState } from "react";
import { api } from "../api/endpoints";
import type { SyncJob } from "../api/types";
import { useIndex } from "../data/IndexProvider";
import { cx } from "../lib/utils";

const POLL_RUNNING_MS = 3000;
const POLL_IDLE_MS = 30000;

/**
 * Job de indexação em andamento, visto de qualquer aba (só com index:write). Consulta a cada 30 s parado e a cada
 * 3 s com job rodando; para com a aba do navegador escondida. Quando um job termina, recarrega repos e orgs.
 * `/api/v1/sync/jobs` é rota de observação: não conta como uso.
 */
export function useActiveJob(enabled: boolean): SyncJob | null {
  const { reload } = useIndex();
  const [job, setJob] = useState<SyncJob | null>(null);
  const runningId = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let timer = 0;
    let active = true;
    const tick = async () => {
      if (document.visibilityState === "visible") {
        try {
          const running = (await api.syncJobs(5)).find((j) => j.status === "running") ?? null;
          if (!active) return;
          if (runningId.current && runningId.current !== running?.id) void reload();
          runningId.current = running?.id ?? null;
          setJob(running);
        } catch {
          // sem rede ou sessão vencida: o indicador some até a próxima consulta dar certo
          if (active) setJob(null);
        }
      }
      if (active) timer = window.setTimeout(() => void tick(), runningId.current ? POLL_RUNNING_MS : POLL_IDLE_MS);
    };
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      window.clearTimeout(timer);
      void tick();
    };
    void tick();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      active = false;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [enabled, reload]);

  return job;
}

/** Fração concluída: arquivos na fase de indexação; antes disso, repositórios. Null = sem número ainda. */
function fraction(job: SyncJob): number | null {
  const p = job.progress;
  if (!p) return null;
  if ((p.phase === "index" || p.phase === "finish") && p.files_total > 0) return p.files_done / p.files_total;
  return p.repos_total > 0 ? p.repos_done / p.repos_total : null;
}

/** Ponto pulsante + andamento no menu lateral. Clicar leva à aba onde os jobs aparecem. */
export function IndexingIndicator({ job, collapsed, onOpen }: { job: SyncJob; collapsed: boolean; onOpen: () => void }) {
  const p = job.progress;
  const done = fraction(job);
  const heading = p?.phase_label ?? "Indexação";
  const detail = p && p.repos_total > 0 ? `${p.repos_done}/${p.repos_total} repositórios` : "em andamento";
  const label = `Indexação em andamento: ${heading}, ${detail}${done !== null ? ` (${Math.round(done * 100)}%)` : ""}`;
  return (
    <button
      type="button"
      onClick={onOpen}
      title={label}
      aria-label={label}
      className={cx(
        "mx-2 mb-2 block animate-fade-in rounded-md text-left outline-none transition hover:bg-white/[0.05] focus-visible:ring-2 focus-visible:ring-accent-400",
        collapsed ? "flex justify-center py-2.5" : "px-3 py-2.5",
      )}
    >
      <span className="flex items-center gap-2.5">
        <span className="relative flex size-2 shrink-0" aria-hidden="true">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-accent-400 opacity-60" />
          <span className="relative inline-flex size-2 rounded-full bg-accent-400" />
        </span>
        {!collapsed && (
          <span className="min-w-0 flex-1">
            <span className="block truncate text-xs font-medium text-white">{heading}</span>
            <span className="block truncate text-[11px] text-ink-400">{detail}</span>
          </span>
        )}
        {!collapsed && done !== null && <span className="text-[11px] text-ink-300 tabular-nums">{Math.round(done * 100)}%</span>}
      </span>
      {!collapsed && (
        <span className="mt-2 block h-1 overflow-hidden rounded-full bg-white/10" aria-hidden="true">
          {done !== null ? (
            <span className="block h-full rounded-full bg-accent-400 transition-[width] duration-500" style={{ width: `${Math.max(3, done * 100)}%` }} />
          ) : (
            <span className="block h-full w-full animate-shimmer bg-[linear-gradient(90deg,transparent,rgb(255_255_255/0.35),transparent)] bg-[length:200%_100%]" />
          )}
        </span>
      )}
    </button>
  );
}
