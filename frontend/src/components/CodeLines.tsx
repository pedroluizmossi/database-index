import { useState } from "react";
import { cx } from "../lib/utils";

export interface CodeLine {
  n: number;
  text: string;
}

interface CodeLinesProps {
  lines: CodeLine[];
  /** Intervalo de linhas a destacar (inclusivo). */
  highlight?: { start: number; end: number } | null;
  /** Mostra só as N primeiras linhas até a pessoa expandir. */
  collapsedAfter?: number;
}

export function CodeLines({ lines, highlight = null, collapsedAfter }: CodeLinesProps) {
  const [expanded, setExpanded] = useState(false);
  const limit = collapsedAfter ?? Infinity;
  const collapsed = !expanded && lines.length > limit;
  const visible = collapsed ? lines.slice(0, limit) : lines;

  return (
    <div className="min-w-max font-mono text-[12.5px] leading-6">
      {visible.map((line) => {
        const hot = highlight !== null && line.n >= highlight.start && line.n <= highlight.end;
        return (
          <div
            key={line.n}
            data-hot={hot ? "true" : undefined}
            className={cx(
              "flex border-l-2 pr-4",
              hot ? "border-indigo-500 bg-indigo-500/10 dark:bg-indigo-400/10" : "border-transparent",
            )}
          >
            <span className="w-14 shrink-0 pr-4 text-right tabular-nums text-zinc-400 select-none dark:text-zinc-600">
              {line.n}
            </span>
            <span className="whitespace-pre text-zinc-800 dark:text-zinc-200">{line.text}</span>
          </div>
        );
      })}
      {collapsedAfter !== undefined && lines.length > collapsedAfter && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 block px-4 py-1 text-xs font-medium text-indigo-600 hover:underline dark:text-indigo-400"
        >
          {expanded ? "Mostrar menos" : `Mostrar mais ${lines.length - collapsedAfter} linha(s)`}
        </button>
      )}
    </div>
  );
}
