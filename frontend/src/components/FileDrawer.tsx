import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Check, Copy, FileWarning, FlaskConical, X } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import { parseNumberedLines } from "../lib/utils";
import { CodeLines, type CodeLine } from "./CodeLines";
import { EmptyState, IconButton, Skeleton } from "./ui";
import { FileSummaryPanel } from "./summaries";
import { TestCasesDialog } from "./TestCasesDialog";

export interface FileTarget {
  repo: string;
  path: string;
  highlight?: { start: number; end: number };
}

interface FileDrawerProps {
  target: FileTarget | null;
  onClose: () => void;
}

/**
 * Painel lateral com o arquivo inteiro. Abre no trecho destacado e fecha com Esc.
 * Arquivo coberto por testes mostra "N casos de teste": a lista abre o teste aqui mesmo, com "Voltar" ao original.
 */
export function FileDrawer({ target: opened, onClose }: FileDrawerProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [lines, setLines] = useState<CodeLine[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  // O que está à vista: o arquivo pedido ou um teste aberto a partir dele.
  const [target, setTarget] = useState<FileTarget | null>(opened);
  const [tests, setTests] = useState(0);
  const [testsOpen, setTestsOpen] = useState(false);
  const key = target ? JSON.stringify([target.repo, target.path]) : null;
  const navigated = target !== null && opened !== null && (target.repo !== opened.repo || target.path !== opened.path);

  useEffect(() => {
    setTarget(opened);
    setTestsOpen(false);
  }, [opened]);

  useEffect(() => {
    setTests(0);
    if (!target) return;
    let active = true;
    api
      .findTests(target.path, target.repo, 1)
      .then((res) => active && setTests(res.matched_by === "arquivo" ? res.total : 0))
      .catch(() => undefined); // contagem é um extra: sem ela, o painel segue igual
    return () => {
      active = false;
    };
  }, [key]);

  // Mantém o <dialog> nativo em sincronia com o alvo (modal, com foco preso e Esc).
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (opened && !dialog.open) dialog.showModal();
    if (!opened && dialog.open) dialog.close();
  }, [opened]);

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
            {navigated && opened && (
              <button
                type="button"
                onClick={() => setTarget(opened)}
                title={`Voltar para ${opened.path}`}
                className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-stone-600 hover:bg-stone-100 hover:text-accent-600 dark:text-stone-300 dark:hover:bg-stone-800 dark:hover:text-accent-400"
              >
                <ArrowLeft className="size-3.5" aria-hidden="true" />
                Voltar
              </button>
            )}
            {tests > 0 && (
              <button
                type="button"
                onClick={() => setTestsOpen(true)}
                title="Casos de teste que cobrem este arquivo (os testes não entram na busca)"
                className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-stone-600 hover:bg-stone-100 hover:text-accent-600 dark:text-stone-300 dark:hover:bg-stone-800 dark:hover:text-accent-400"
              >
                <FlaskConical className="size-3.5" aria-hidden="true" />
                {tests} {tests === 1 ? "caso de teste" : "casos de teste"}
              </button>
            )}
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
          {testsOpen && (
            <TestCasesDialog
              repo={target.repo}
              path={target.path}
              open
              onClose={() => setTestsOpen(false)}
              onOpenCase={(repo, path, line) => {
                setTestsOpen(false);
                setTarget({ repo, path, highlight: { start: line, end: line } });
              }}
            />
          )}
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
