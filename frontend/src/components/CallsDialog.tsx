import { useEffect, useMemo, useState } from "react";
import { ArrowDownLeft, ArrowUpRight, FileCode2, FileSearch } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { CallEdge, CallGraphResult } from "../api/types";
import { Dialog } from "./Dialog";
import { Alert, Button, EmptyState, Skeleton, inputClass } from "./ui";

const numberFormat = new Intl.NumberFormat("pt-BR");
const FILTER_FROM = 8; // filtro só aparece com lista longa

/** Como o alvo da chamada foi achado (domain/code_graph.py). Só "nome" é palpite. */
export const VIA_LABEL: Record<string, string> = {
  arquivo: "mesmo arquivo",
  pacote: "mesmo pacote",
  import: "import",
  tipo: "tipo declarado",
  herança: "herança",
  nome: "só pelo nome",
};

export type CallDirection = "callers" | "callees";

/**
 * Quem chama este arquivo, ou o que ele chama, pelo grafo de chamadas (lido do código com tree-sitter, dentro do
 * repo). Só chamadas entre arquivos diferentes. Clicar abre o outro lado: o trecho que chama, ou a definição chamada.
 */
export function CallsDialog({
  repo,
  path,
  direction,
  open,
  onClose,
  onOpen,
}: {
  repo: string;
  path: string;
  direction: CallDirection;
  open: boolean;
  onClose: () => void;
  onOpen: (repo: string, path: string, line: number) => void;
}) {
  const [data, setData] = useState<CallGraphResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    if (!open) return;
    let active = true;
    setData(null);
    setError(null);
    setFilter("");
    api
      .callGraph(path, direction, repo, true)
      .then((res) => active && setData(res))
      .catch((err: unknown) => active && setError(describeError(err)));
    return () => {
      active = false;
    };
  }, [open, repo, path, direction]);

  const groups = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const edges = (data?.edges ?? []).filter(
      (e) => !needle || e.name.toLowerCase().includes(needle) || e.path.toLowerCase().includes(needle) || e.of.name.toLowerCase().includes(needle),
    );
    const byFile = new Map<string, CallEdge[]>();
    for (const e of edges) byFile.set(e.path, [...(byFile.get(e.path) ?? []), e]);
    return [...byFile.entries()];
  }, [data, filter]);

  const total = data?.total ?? 0;
  const callers = direction === "callers";
  // Vários símbolos do arquivo aparecem como alvo: vale dizer qual em cada linha.
  const manyTargets = new Set((data?.edges ?? []).map((e) => e.of.name)).size > 1;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={callers ? "Quem chama este arquivo" : "O que este arquivo chama"}
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
          Pelo grafo de chamadas, lido do código e resolvido por import, tipo declarado e herança, só dentro deste repo.
          Chamadas por reflexão, eventos ou de outros repos não aparecem. Clique para abrir{" "}
          {callers ? "o trecho que chama" : "a definição chamada"}.
        </p>
        {error && <Alert>{error}</Alert>}
        {!data && !error && <Skeleton className="h-40 w-full" />}
        {data && total >= FILTER_FROM && (
          <input
            type="search"
            data-autofocus
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filtrar por função, método ou arquivo"
            aria-label="Filtrar chamadas"
            className={inputClass}
          />
        )}
        {data && groups.length === 0 && (
          <EmptyState
            icon={FileSearch}
            title="Nenhuma chamada"
            text={total ? "Nenhuma chamada combina com o filtro." : (data.note ?? "Nenhuma chamada ligada a este arquivo.")}
          />
        )}
        {groups.length > 0 && (
          <div className="max-h-[55dvh] space-y-4 overflow-y-auto">
            {groups.map(([file, edges]) => (
              <section key={file} className="space-y-1">
                <p className="flex items-center gap-1.5 truncate font-mono text-xs text-stone-500 dark:text-stone-400" title={file}>
                  <FileCode2 className="size-3.5 shrink-0" aria-hidden="true" />
                  <span className="truncate">{file}</span>
                </p>
                <ul className="divide-y divide-stone-100 dark:divide-stone-800">
                  {edges.map((e) => {
                    const line = callers ? e.call_line : e.line;
                    return (
                      <li key={`${e.line}-${e.name}-${e.of.name}`}>
                        <button
                          type="button"
                          onClick={() => onOpen(e.repo, e.path, line)}
                          className="flex w-full items-baseline gap-3 rounded-md px-2 py-2 text-left outline-none hover:bg-stone-100 focus-visible:ring-2 focus-visible:ring-accent-500 dark:hover:bg-stone-800/60"
                        >
                          <span className="w-12 shrink-0 text-right font-mono text-xs text-stone-400 tabular-nums">L{line}</span>
                          <span className="min-w-0 flex-1">
                            <span className="font-mono text-[13px] text-stone-800 dark:text-stone-100">{e.name}</span>
                            {manyTargets && (
                              <span className="ml-2 text-xs text-stone-500 dark:text-stone-400">
                                {callers ? "→" : "←"} <span className="font-mono">{e.of.name}</span>
                              </span>
                            )}
                            {e.calls > 1 && (
                              <span className="ml-2 text-xs text-stone-500 tabular-nums dark:text-stone-400">×{e.calls}</span>
                            )}
                          </span>
                          <span
                            className={
                              e.inferred
                                ? "shrink-0 text-[11px] font-medium text-amber-700 dark:text-amber-400"
                                : "shrink-0 text-[11px] text-stone-500 dark:text-stone-400"
                            }
                            title={e.inferred ? "Ligada só porque é a única função com esse nome no repo: confira no código." : "Como o alvo foi achado"}
                          >
                            {VIA_LABEL[e.via] ?? e.via}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>
        )}
        {data?.truncated && (
          <p className="text-xs text-stone-500 dark:text-stone-400">
            Mostrando {numberFormat.format(data.edges.length)} de {numberFormat.format(total)}.
          </p>
        )}
      </div>
    </Dialog>
  );
}

/** Ícone de cada direção: chamado por (entra) e chama (sai). */
export const DIRECTION_ICON = { callers: ArrowDownLeft, callees: ArrowUpRight } as const;
