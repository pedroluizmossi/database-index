import { useEffect, useRef, useState } from "react";
import { Check, Copy, FileWarning, X } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import { parseNumberedLines } from "../lib/utils";
import { CodeLines, type CodeLine } from "./CodeLines";
import { EmptyState, IconButton, Skeleton } from "./ui";

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
      className="fixed inset-y-0 right-0 left-auto m-0 h-dvh max-h-none w-full max-w-4xl open:flex flex-col border-0 bg-white p-0 text-zinc-900 shadow-2xl shadow-zinc-900/20 backdrop:bg-zinc-950/40 backdrop:backdrop-blur-sm open:animate-slide-in sm:rounded-l-2xl dark:bg-zinc-900 dark:text-zinc-100"
    >
      {target && (
        <>
          <header className="flex items-center gap-3 border-b border-zinc-200 px-5 py-4 dark:border-zinc-800">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold">{fileName}</p>
              <p className="truncate font-mono text-xs text-zinc-500 dark:text-zinc-400">
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

          <div ref={bodyRef} className="min-h-0 flex-1 overflow-auto bg-zinc-50/60 py-3 dark:bg-zinc-950/60">
            {error && <EmptyState icon={FileWarning} title="Não consegui abrir o arquivo" text={error} />}
            {!error && !lines && <LoadingLines />}
            {lines && <CodeLines lines={lines} highlight={target.highlight ?? null} />}
          </div>

          <footer className="flex items-center justify-between gap-4 border-t border-zinc-200 px-5 py-2.5 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
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
