import { useCallback, useEffect, useMemo, useState } from "react";
import { BookOpen, ExternalLink, Network, Plus, Search, X } from "lucide-react";
import { pageId, type XrayTarget } from "../lib/xray";
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
const PAGE_SIZE = 15;
const FETCH_SIZE = 2000;

/** Páginas da documentação indexada e os repos ligados a cada uma. Gravar e remover vínculos: só admin. */
export function DocsPage({ onXray }: { onXray: (target: XrayTarget) => void }) {
  const { session } = useAuth();
  const { push } = useToast();
  const admin = session ? hasScope(session.me, "admin") : false;
  const [rows, setRows] = useState<DocPageRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [root, setRoot] = useState("");
  const [onlyUnlinked, setOnlyUnlinked] = useState(false);
  const [page, setPage] = useState(0);
  const [adding, setAdding] = useState<DocPageRow | null>(null);

  const load = useCallback(async () => {
    try {
      const pages: DocPageRow[] = [];
      let batch: DocPageRow[];
      do {
        batch = await api.docsOverview(pages.length);
        if (pages.length > 0 && batch.length > 0 && batch[0].doc_repo === pages[0].doc_repo && batch[0].doc_path === pages[0].doc_path) {
          throw new Error("Reinicie o servidor para carregar todas as páginas de documentação.");
        }
        pages.push(...batch);
      } while (batch.length === FETCH_SIZE);
      setRows(pages.filter((row) => !row.doc_repo.startsWith("docs__openapi-")));
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
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount - 1);
  const visible = filtered.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);

  useEffect(() => { setPage(0); }, [q, root, onlyUnlinked]);

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
        {visible.map((row) => (
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
              <div className="flex items-center gap-2">
              <Button variant="secondary" size="sm" aria-label={`Raio X de ${row.title ?? row.page}`} onClick={() => onXray({kind: "doc", id: pageId(row.doc_repo, row.doc_path)})}><Network className="size-3.5" aria-hidden="true" /> Raio X</Button>
              {admin && (
                <Button variant="secondary" size="sm" onClick={() => setAdding(row)}>
                  <Plus className="size-3.5" aria-hidden="true" />
                  Vincular repo
                </Button>
              )}
              </div>
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

      {filtered.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-zinc-500 dark:text-zinc-400">
          <span>{numberFormat.format(currentPage * PAGE_SIZE + 1)}–{numberFormat.format(Math.min((currentPage + 1) * PAGE_SIZE, filtered.length))} de {numberFormat.format(filtered.length)} páginas</span>
          <nav aria-label="Paginação da documentação" className="flex items-center gap-2">
            <Button variant="secondary" size="sm" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Anterior</Button>
            <span>Página {currentPage + 1} de {pageCount}</span>
            <Button variant="secondary" size="sm" disabled={currentPage + 1 >= pageCount} onClick={() => setPage(currentPage + 1)}>Próxima</Button>
          </nav>
        </div>
      )}

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
