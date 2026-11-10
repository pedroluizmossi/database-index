import { useMemo, useState } from "react";
import { BookOpen, Check, Copy, ExternalLink, FileText } from "lucide-react";
import type { Hit } from "../api/types";
import { Badge, Button, IconButton } from "./ui";
import { CodeLines, type CodeLine } from "./CodeLines";
import { HitSummary } from "./summaries";
import { KindIcon } from "./kind";

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
    <article className="overflow-hidden rounded-xl border border-stone-200 bg-sheet transition-colors hover:border-stone-300 dark:border-stone-800 dark:bg-stone-900 dark:hover:border-stone-700">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 pt-4 pb-3">
        <KindIcon kind={hit.kind === "doc" ? "doc" : "repo"} size="sm" label={hit.kind === "doc" ? "Documentação" : "Código"} />
        <Badge tone="accent" className="max-w-full">
          <span className="truncate">{hit.repo}</span>
        </Badge>
        <button
          type="button"
          onClick={onOpen}
          title={hit.path}
          className="min-w-0 truncate font-mono text-[13px] text-stone-700 underline-offset-4 hover:text-accent-600 hover:underline dark:text-stone-300 dark:hover:text-accent-400"
        >
          {hit.path}
        </button>
        <span className="font-mono text-xs text-stone-400 tabular-nums">
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
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 pb-3 text-xs text-stone-500 dark:text-stone-400">
          {hit.url && (
            <a
              href={hit.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex min-w-0 items-center gap-1 font-medium text-accent-600 hover:underline dark:text-accent-400"
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

      {hit.summary && <HitSummary summary={hit.summary} keywords={hit.summary_keywords ?? []} />}

      <div className="overflow-x-auto border-y border-stone-100 bg-stone-50 py-2 dark:border-stone-800 dark:bg-stone-950/50">
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
      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-800">
        <span
          className="block h-full rounded-full bg-accent-600"
          style={{ width: `${percent}%` }}
        />
      </span>
      <span className="font-mono text-[11px] text-stone-500 tabular-nums">{score.toFixed(2)}</span>
    </span>
  );
}

/** "repo/caminho/arquivo.java:42" → partes. */
function parseImpl(ref: string): { repo: string; path: string; line: number } | null {
  const m = /^([^/]+)\/(.+):(\d+)$/.exec(ref);
  return m ? { repo: m[1], path: m[2], line: Number(m[3]) } : null;
}
