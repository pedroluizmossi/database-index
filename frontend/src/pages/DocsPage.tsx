import { useCallback, useEffect, useMemo, useState } from "react";
import { BookOpen, ExternalLink, Plus, Search, X } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { DocPageRow } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { AddDocLinkDialog } from "../components/AddDocLinkDialog";
import { OriginBadges } from "../components/docs";
import { useToast } from "../components/Toaster";
import { Alert, Button, EmptyState, IconButton, Panel, Select, Skeleton, Switch, inputClass } from "../components/ui";
import { hasScope } from "../lib/capabilities";

const numberFormat = new Intl.NumberFormat("pt-BR");

/** Páginas da documentação indexada e os repos ligados a cada uma. Gravar e remover vínculos: só admin. */
export function DocsPage() {
  const { session } = useAuth();
  const { push } = useToast();
  const admin = session ? hasScope(session.me, "admin") : false;
  const [rows, setRows] = useState<DocPageRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [root, setRoot] = useState("");
  const [onlyUnlinked, setOnlyUnlinked] = useState(false);
  const [adding, setAdding] = useState<DocPageRow | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api.docsOverview());
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const roots = useMemo(
    () => [...new Set((rows ?? []).map((r) => r.root))].sort((a, b) => a.localeCompare(b, "pt-BR")),
    [rows],
  );
  const unlinkedCount = useMemo(() => (rows ?? []).filter((r) => r.links.length === 0).length, [rows]);
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (rows ?? []).filter(
      (r) =>
        (!root || r.root === root) &&
        (!onlyUnlinked || r.links.length === 0) &&
        (!needle || (r.title ?? "").toLowerCase().includes(needle) || r.page.toLowerCase().includes(needle)),
    );
  }, [rows, q, root, onlyUnlinked]);

  async function unlink(row: DocPageRow, repo: string) {
    try {
      await api.removeDocLink({ doc_repo: row.doc_repo, doc_path: row.doc_path, repo });
      push({ tone: "success", title: "Vínculo removido", description: `${row.title ?? row.page} ↔ ${repo}` });
      await load();
    } catch (err) {
      push({ tone: "error", title: "Não consegui remover o vínculo", description: describeError(err) });
    }
  }

  return (
    <section className="mx-auto max-w-6xl space-y-6 px-4 py-8 sm:px-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Documentação</h1>
          <p className="text-sm text-zinc-500 dark:text-zinc-400">Páginas indexadas e os repos ligados a cada uma.</p>
        </div>
        {rows && (
          <p className="text-sm text-zinc-500 tabular-nums dark:text-zinc-400">
            {numberFormat.format(rows.length)} páginas · {numberFormat.format(unlinkedCount)} sem vínculo
          </p>
        )}
      </header>

      <Panel className="flex flex-wrap items-center gap-3 p-3">
        <div className="relative min-w-[14rem] flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-zinc-400" aria-hidden="true" />
          <input
            className={`${inputClass} pl-9`}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Buscar por título ou caminho"
            aria-label="Buscar página"
          />
        </div>
        <div className="w-52">
          <Select value={root} onChange={setRoot}>
            <option value="">Todas as raízes</option>
            {roots.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex items-center gap-2">
          <Switch checked={onlyUnlinked} onChange={setOnlyUnlinked} label="Mostrar só páginas sem vínculo" />
          <span className="text-sm text-zinc-600 dark:text-zinc-300">Só sem vínculo</span>
        </div>
      </Panel>

      {error && <Alert>{error}</Alert>}
      {rows === null && !error && (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-24 w-full rounded-2xl" />
          ))}
        </div>
      )}
      {rows && filtered.length === 0 && (
        <EmptyState
          icon={BookOpen}
          title="Nenhuma página"
          text={
            rows.length === 0
              ? "Nenhuma página indexada ainda. Indexe uma fonte de documentação (code-index index docs__<nome>)."
              : "Nenhuma página bate com os filtros."
          }
        />
      )}

      <ul className="space-y-3">
        {filtered.map((row) => (
          <li
            key={`${row.doc_repo}|${row.doc_path}`}
            className="space-y-3 rounded-2xl border border-zinc-200/80 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                {row.url ? (
                  <a
                    href={row.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                  >
                    <span className="truncate">{row.title ?? row.page}</span>
                    <ExternalLink className="size-3.5 shrink-0" aria-hidden="true" />
                  </a>
                ) : (
                  <p className="font-medium">{row.title ?? row.page}</p>
                )}
                <p className="truncate font-mono text-xs text-zinc-500 dark:text-zinc-400">
                  {row.root} · {row.page}
                </p>
              </div>
              {admin && (
                <Button variant="secondary" size="sm" onClick={() => setAdding(row)}>
                  <Plus className="size-3.5" aria-hidden="true" />
                  Vincular repo
                </Button>
              )}
            </div>

            {row.links.length === 0 ? (
              <p className="text-xs text-zinc-500 dark:text-zinc-400">Sem vínculo com repos.</p>
            ) : (
              <ul className="flex flex-wrap gap-2">
                {row.links.map((link) => (
                  <li
                    key={link.repo}
                    className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-zinc-200 px-2.5 py-1 text-xs dark:border-zinc-700"
                  >
                    <span className="truncate font-medium">{link.repo}</span>
                    <OriginBadges origins={link.origins} />
                    {link.reason && (
                      <span title={link.reason} className="max-w-[16rem] truncate text-zinc-500 dark:text-zinc-400">
                        — {link.reason}
                      </span>
                    )}
                    {admin && link.removable && (
                      <IconButton label={`Remover vínculo com ${link.repo}`} onClick={() => void unlink(row, link.repo)}>
                        <X className="size-3.5" />
                      </IconButton>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>

      <AddDocLinkDialog
        page={adding}
        onClose={() => setAdding(null)}
        onDone={() => {
          setAdding(null);
          void load();
        }}
      />
    </section>
  );
}
