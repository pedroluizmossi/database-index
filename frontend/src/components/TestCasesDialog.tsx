import { useEffect, useMemo, useState } from "react";
import { FileSearch, FlaskConical } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { FindTestsResult, TestCaseRef } from "../api/types";
import { plural } from "../lib/utils";
import { Dialog } from "./Dialog";
import { Alert, Button, EmptyState, Skeleton, inputClass } from "./ui";

const numberFormat = new Intl.NumberFormat("pt-BR");
const FILTER_FROM = 8; // filtro só aparece com lista longa

/**
 * Casos de teste que cobrem um arquivo de produção. Os testes ficam fora da busca e dos embeddings; esta lista vem
 * da leitura dos clones no sync (nome, describe/classe envolvente e linha). Clicar num caso abre o teste.
 */
export function TestCasesDialog({
  repo,
  path,
  open,
  onClose,
  onOpenCase,
}: {
  repo: string;
  path: string;
  open: boolean;
  onClose: () => void;
  onOpenCase?: (repo: string, path: string, line: number) => void;
}) {
  const [data, setData] = useState<FindTestsResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    if (!open) return;
    let active = true;
    setData(null);
    setError(null);
    setFilter("");
    api
      .findTests(path, repo)
      .then((res) => active && setData(res))
      .catch((err: unknown) => active && setError(describeError(err)));
    return () => {
      active = false;
    };
  }, [open, repo, path]);

  const groups = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const cases = (data?.tests ?? []).filter(
      (t) =>
        !needle ||
        t.name.toLowerCase().includes(needle) ||
        t.path.toLowerCase().includes(needle) ||
        (t.calls ?? []).some((c) => c.toLowerCase().includes(needle)),
    );
    const byFile = new Map<string, TestCaseRef[]>();
    for (const t of cases) byFile.set(t.path, [...(byFile.get(t.path) ?? []), t]);
    return [...byFile.entries()];
  }, [data, filter]);

  const total = data?.total ?? 0;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Casos de teste"
      description={`${repo}/${path}`}
      size="lg"
      footer={
        <Button variant="ghost" onClick={onClose}>
          Fechar
        </Button>
      }
    >
      <div className="space-y-4">
        <p className="text-xs text-stone-500 dark:text-stone-400">
          Testes que cobrem este arquivo, lidos do repositório (não entram na busca). Os métodos ao lado de cada caso
          são os deste arquivo que ele chama. Clique para abrir o teste.
        </p>
        {error && <Alert>{error}</Alert>}
        {!data && !error && <Skeleton className="h-40 w-full" />}
        {data && total >= FILTER_FROM && (
          <input
            type="search"
            data-autofocus
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filtrar por nome do teste, arquivo ou método"
            aria-label="Filtrar casos de teste"
            className={inputClass}
          />
        )}
        {data && groups.length === 0 && (
          <EmptyState
            icon={FileSearch}
            title="Nenhum caso de teste"
            text={total ? "Nenhum caso combina com o filtro." : "Nenhum teste ligado a este arquivo."}
          />
        )}
        {groups.length > 0 && (
          <div className="max-h-[55dvh] space-y-4 overflow-y-auto">
            {groups.map(([file, cases]) => (
              <section key={file} className="space-y-1">
                <p className="flex items-center gap-1.5 truncate font-mono text-xs text-stone-500 dark:text-stone-400" title={file}>
                  <FlaskConical className="size-3.5 shrink-0" aria-hidden="true" />
                  <span className="truncate">{file}</span>
                </p>
                <ul className="divide-y divide-stone-100 dark:divide-stone-800">
                  {cases.map((t) => (
                    <li key={`${t.line}-${t.name}`}>
                      <button
                        type="button"
                        disabled={!onOpenCase}
                        onClick={() => onOpenCase?.(t.repo, t.path, t.line)}
                        className="flex w-full items-baseline gap-3 rounded-md px-2 py-2 text-left outline-none hover:bg-stone-100 focus-visible:ring-2 focus-visible:ring-accent-500 disabled:cursor-default disabled:hover:bg-transparent dark:hover:bg-stone-800/60"
                      >
                        <span className="w-12 shrink-0 text-right font-mono text-xs text-stone-400 tabular-nums">L{t.line}</span>
                        <span className="min-w-0 flex-1 text-sm text-stone-800 dark:text-stone-100">{t.name}</span>
                        {(t.calls?.length ?? 0) > 0 && (
                          <span className="hidden shrink-0 flex-wrap justify-end gap-1 sm:flex">
                            {t.calls!.slice(0, 3).map((c) => (
                              <code key={c} className="rounded bg-stone-100 px-1.5 py-0.5 text-[11px] text-stone-600 dark:bg-stone-800 dark:text-stone-300">
                                {c}
                              </code>
                            ))}
                          </span>
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
        {data?.truncated && (
          <p className="text-xs text-stone-500 dark:text-stone-400">
            Mostrando {numberFormat.format(data.tests.length)} de {numberFormat.format(total)}{" "}
            {plural(total, "caso", "casos")}.
          </p>
        )}
      </div>
    </Dialog>
  );
}
