import { useEffect, useRef, useState } from "react";
import { Check, Copy, FileWarning, X } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import { parseNumberedLines } from "../lib/utils";
import { CodeLines, type CodeLine } from "./CodeLines";
import { EmptyState, IconButton, Skeleton } from "./ui";
import { FileSummaryPanel } from "./summaries";

export interface FileTarget {
  repo: string;
  path: string;
  highlight?: { start: number; end: number };
}

interface FileDrawerProps {
  target: FileTarget | null;
  onClose: () => void;
}

/** Painel lateral com o arquivo inteiro. Abre no trecho destacado e fecha com Esc. */
export function FileDrawer({ target, onClose }: FileDrawerProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [lines, setLines] = useState<CodeLine[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const key = target ? JSON.stringify([target.repo, target.path]) : null;

  // Mantém o <dialog> nativo em sincronia com o alvo (modal, com foco preso e Esc).
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (target && !dialog.open) dialog.showModal();
    if (!target && dialog.open) dialog.close();
  }, [target]);

  useEffect(() => {
    if (!target) return;
    let active = true;
    setLines(null);
    setError(null);
    api
      .file(target.repo, target.path)
      .then((text) => {
        if (active) setLines(parseNumberedLines(text));
      })
      .catch((err: unknown) => {
        if (active) setError(describeError(err));
      });
    return () => {
      active = false;
    };
    // `key` identifica o arquivo; trocar só o trecho destacado não refaz a requisição.
  }, [key]);

  useEffect(() => {
    if (!lines) return;
    bodyRef.current?.querySelector<HTMLElement>('[data-hot="true"]')?.scrollIntoView({ block: "center" });
  }, [lines]);

  async function copyPath() {
    if (!target) return;
    try {
      await navigator.clipboard.writeText(`${target.repo}/${target.path}`);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard indisponível: nada a fazer
    }
  }

  const fileName = target?.path.split("/").pop() ?? "";

  return (
    <dialog
      ref={dialogRef}
      onClose={onClose}
      className="fixed inset-y-0 right-0 left-auto m-0 h-dvh max-h-none w-full max-w-4xl open:flex flex-col border-0 bg-sheet p-0 text-stone-900 shadow-[-16px_0_40px_-12px_rgb(0_0_0/0.25)] backdrop:bg-ink-950/50 open:animate-slide-in sm:rounded-l-xl dark:bg-stone-900 dark:text-stone-100 dark:backdrop:bg-black/60"
    >
      {target && (
        <>
          <header className="flex items-center gap-3 border-b border-stone-200 px-5 py-4 dark:border-stone-800">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold">{fileName}</p>
              <p className="truncate font-mono text-xs text-stone-500 dark:text-stone-400">
                {target.repo}/{target.path}
              </p>
            </div>
            <IconButton label={copied ? "Caminho copiado" : "Copiar caminho"} onClick={copyPath}>
              {copied ? <Check className="size-4 text-emerald-500" /> : <Copy className="size-4" />}
            </IconButton>
            <IconButton label="Fechar" onClick={() => dialogRef.current?.close()}>
              <X className="size-4" />
            </IconButton>
          </header>

          <FileSummaryPanel repo={target.repo} path={target.path} />

          <div ref={bodyRef} className="min-h-0 flex-1 overflow-auto bg-stone-50/60 py-3 dark:bg-stone-950/60">
            {error && <EmptyState icon={FileWarning} title="Não consegui abrir o arquivo" text={error} />}
            {!error && !lines && <LoadingLines />}
            {lines && <CodeLines lines={lines} highlight={target.highlight ?? null} />}
          </div>

          <footer className="flex items-center justify-between gap-4 border-t border-stone-200 px-5 py-2.5 text-xs text-stone-500 dark:border-stone-800 dark:text-stone-400">
            <span>{lines ? `${lines.length} linhas` : "Carregando…"}</span>
            {target.highlight && (
              <span>
                Trecho destacado: linhas {target.highlight.start}–{target.highlight.end}
              </span>
            )}
          </footer>
        </>
      )}
    </dialog>
  );
}

function LoadingLines() {
  return (
    <div className="space-y-2.5 px-6 py-2">
      {Array.from({ length: 14 }, (_, i) => (
        <Skeleton key={i} className="h-3.5" style={{ width: `${35 + ((i * 37) % 55)}%` }} />
      ))}
    </div>
  );
}
