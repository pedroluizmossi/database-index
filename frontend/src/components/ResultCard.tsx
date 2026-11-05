import { useMemo, useState } from "react";
import { BookOpen, Check, Copy, ExternalLink, FileText } from "lucide-react";
import type { Hit } from "../api/types";
import { Badge, Button, IconButton } from "./ui";
import { CodeLines, type CodeLine } from "./CodeLines";

interface ResultCardProps {
  hit: Hit;
  /** Maior score da lista, para desenhar o medidor de relevância de forma relativa. */
  maxScore: number;
  onOpen: () => void;
  /** Abre o handler de uma operação da API (repo, caminho, linha). */
  onOpenImpl?: (repo: string, path: string, line: number) => void;
}

export function ResultCard({ hit, maxScore, onOpen, onOpenImpl }: ResultCardProps) {
  const lines = useMemo<CodeLine[]>(
    () => hit.code.split("\n").map((text, i) => ({ n: hit.start_line + i, text })),
    [hit],
  );
  const [copied, setCopied] = useState(false);
  const percent = maxScore > 0 ? Math.round((hit.score / maxScore) * 100) : 0;

  async function copyCode() {
    try {
      await navigator.clipboard.writeText(hit.code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard indisponível (contexto não seguro): nada a fazer
    }
  }

  return (
    <article className="animate-fade-up overflow-hidden rounded-2xl border border-zinc-200/80 bg-white shadow-sm transition hover:border-indigo-300/70 hover:shadow-lg hover:shadow-indigo-500/5 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-indigo-500/30">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 pt-4 pb-3">
        <Badge tone="accent" className="max-w-full">
          <span className="truncate">{hit.repo}</span>
        </Badge>
        <button
          type="button"
          onClick={onOpen}
          title={hit.path}
          className="min-w-0 truncate font-mono text-[13px] text-zinc-700 underline-offset-4 hover:text-indigo-600 hover:underline dark:text-zinc-300 dark:hover:text-indigo-400"
        >
          {hit.path}
        </button>
        <span className="font-mono text-xs text-zinc-400 tabular-nums">
          L{hit.start_line}–{hit.end_line}
        </span>
        <div className="ml-auto flex items-center gap-4">
          <ScoreMeter percent={percent} score={hit.score} />
          {hit.kind === "doc" ? (
            <Badge tone="pending">
              <BookOpen className="size-3" aria-hidden="true" />
              doc
            </Badge>
          ) : (
            <Badge>{hit.language}</Badge>
          )}
        </div>
      </header>

      {(hit.url || hit.linked_repos.length > 0 || (hit.implemented_in?.length ?? 0) > 0) && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 pb-3 text-xs text-zinc-500 dark:text-zinc-400">
          {hit.url && (
            <a
              href={hit.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex min-w-0 items-center gap-1 font-medium text-indigo-600 hover:underline dark:text-indigo-400"
            >
              <span className="truncate">{hit.title || hit.url}</span>
              <ExternalLink className="size-3 shrink-0" aria-hidden="true" />
            </a>
          )}
          {hit.linked_repos.length > 0 && (
            <span className="flex min-w-0 flex-wrap items-center gap-1.5">
              ligado a
              {hit.linked_repos.map((r) => (
                <Badge key={r}>{r}</Badge>
              ))}
            </span>
          )}
          {(hit.implemented_in ?? []).map((ref) => {
            const impl = parseImpl(ref);
            return (
              <button
                key={ref}
                type="button"
                title={ref}
                onClick={() => impl && onOpenImpl?.(impl.repo, impl.path, impl.line)}
                className="inline-flex min-w-0 items-center gap-1 font-medium text-emerald-700 hover:underline dark:text-emerald-400"
              >
                implementado em <span className="truncate font-mono">{impl ? `${impl.path.split("/").pop()}:${impl.line}` : ref}</span>
              </button>
            );
          })}
        </div>
      )}

      <div className="overflow-x-auto border-y border-zinc-100 bg-zinc-50/70 py-2 dark:border-zinc-800 dark:bg-zinc-950/50">
        <CodeLines lines={lines} collapsedAfter={8} />
      </div>

      <footer className="flex items-center justify-between gap-3 px-3 py-2.5">
        <IconButton label={copied ? "Copiado" : "Copiar código"} onClick={copyCode}>
          {copied ? <Check className="size-4 text-emerald-500" /> : <Copy className="size-4" />}
        </IconButton>
        <Button variant="secondary" size="sm" onClick={onOpen}>
          <FileText className="size-3.5" aria-hidden="true" />
          Abrir arquivo
        </Button>
      </footer>
    </article>
  );
}

function ScoreMeter({ percent, score }: { percent: number; score: number }) {
  return (
    <span className="flex items-center gap-2" title="Relevância: quanto maior, mais próximo da busca">
      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
        <span
          className="block h-full rounded-full bg-linear-to-r from-indigo-500 to-violet-500"
          style={{ width: `${percent}%` }}
        />
      </span>
      <span className="font-mono text-[11px] text-zinc-500 tabular-nums">{score.toFixed(2)}</span>
    </span>
  );
}

/** "repo/caminho/arquivo.java:42" → partes. */
function parseImpl(ref: string): { repo: string; path: string; line: number } | null {
  const m = /^([^/]+)\/(.+):(\d+)$/.exec(ref);
  return m ? { repo: m[1], path: m[2], line: Number(m[3]) } : null;
}
