import { useCallback, useEffect, useMemo, useState } from "react";
import { BookOpen, ExternalLink, Eye, EyeOff, ListChecks, Network, Plus, RefreshCw, Search, X } from "lucide-react";
import { pageId, type XrayTarget } from "../lib/xray";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import { API_DOC_TYPES, type DocPageRow, type DocSourceInfo, type Repo } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useIndex } from "../data/IndexProvider";
import { AddDocLinkDialog } from "../components/AddDocLinkDialog";
import { DocSelectionDialog } from "../components/DocSelectionDialog";
import { DocSuggestionsPanel } from "../components/DocSuggestionsPanel";
import { OriginBadges } from "../components/docs";
import { KindIcon } from "../components/kind";
import { SyncDialog } from "../components/SyncDialog";
import { useToast } from "../components/Toaster";
import { Alert, Badge, Button, EmptyState, IconButton, Panel, Segmented, Select, Skeleton, inputClass, type BadgeTone } from "../components/ui";
import { hasScope } from "../lib/capabilities";
import { formatDateTime, plural, timeAgo } from "../lib/utils";

const numberFormat = new Intl.NumberFormat("pt-BR");
const PAGE_SIZE = 15;
const FETCH_SIZE = 2000;
const RULES_SHOWN = 6;

type DocsView = "sources" | "pages";
type PageFilter = "all" | "unlinked" | "ignored";

const STATUS: Record<Repo["status"], { label: string; tone: BadgeTone }> = {
  indexed: { label: "Indexada", tone: "indexed" },
  pending: { label: "Aguardando sync", tone: "pending" },
  error: { label: "Com erro", tone: "error" },
  ignored: { label: "Ignorada", tone: "ignored" },
};

/** "#docs?view=fontes" abre direto nas fontes. */
function viewFromHash(): DocsView {
  return new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("view") === "fontes" ? "sources" : "pages";
}

/**
 * Documentação externa: as fontes (o que entra no índice, escolhido por página) e as páginas indexadas com os
 * repos ligados a cada uma. Escolher páginas, tirar da busca e mexer em vínculos: só admin. Indexar: index:write.
 */
export function DocsPage({ onXray }: { onXray: (target: XrayTarget) => void }) {
  const { session } = useAuth();
  const { push } = useToast();
  const { repos, reload: reloadIndex } = useIndex();
  const admin = session ? hasScope(session.me, "admin") : false;
  const canIndex = session ? hasScope(session.me, "index:write") : false;
  const [view, setView] = useState<DocsView>(viewFromHash);
  const [sources, setSources] = useState<DocSourceInfo[] | null>(null);
  const [sourcesError, setSourcesError] = useState<string | null>(null);
  const [rows, setRows] = useState<DocPageRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [root, setRoot] = useState("");
  const [docRepo, setDocRepo] = useState("");
  const [filter, setFilter] = useState<PageFilter>("all");
  const [page, setPage] = useState(0);
  const [adding, setAdding] = useState<DocPageRow | null>(null);
  const [editing, setEditing] = useState<DocSourceInfo | null>(null);
  const [syncing, setSyncing] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const loadSources = useCallback(async () => {
    try {
      setSources(await api.docSources());
      setSourcesError(null);
    } catch (err) {
      setSourcesError(describeError(err));
    }
  }, []);

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
      setRows(pages.filter((row) => !API_DOC_TYPES.includes(row.type)));
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }, []);

  useEffect(() => {
    void load();
    void loadSources();
  }, [load, loadSources]);

  function changeView(next: DocsView) {
    setView(next);
    window.history.replaceState(null, "", next === "sources" ? "#docs?view=fontes" : "#docs");
  }

  const repoByName = useMemo(() => new Map((repos ?? []).map((r) => [r.name, r])), [repos]);
  const docRepos = useMemo(() => [...new Set((rows ?? []).map((r) => r.doc_repo))].sort(), [rows]);
  const roots = useMemo(
    () =>
      [...new Set((rows ?? []).filter((r) => !docRepo || r.doc_repo === docRepo).map((r) => r.root))].sort((a, b) =>
        a.localeCompare(b, "pt-BR"),
      ),
    [rows, docRepo],
  );
  const unlinkedCount = useMemo(() => (rows ?? []).filter((r) => r.links.length === 0).length, [rows]);
  const ignoredCount = useMemo(() => (rows ?? []).filter((r) => r.ignored).length, [rows]);
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (rows ?? []).filter(
      (r) =>
        (!docRepo || r.doc_repo === docRepo) &&
        (!root || r.root === root) &&
        (filter !== "unlinked" || r.links.length === 0) &&
        (filter !== "ignored" || r.ignored) &&
        (!needle || (r.title ?? "").toLowerCase().includes(needle) || r.page.toLowerCase().includes(needle)),
    );
  }, [rows, q, root, docRepo, filter]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount - 1);
  const visible = filtered.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);

  useEffect(() => {
    setPage(0);
  }, [q, root, docRepo, filter]);

  async function unlink(row: DocPageRow, repo: string) {
    try {
      await api.removeDocLink({ doc_repo: row.doc_repo, doc_path: row.doc_path, repo });
      push({ tone: "success", title: "Vínculo removido", description: `${row.title ?? row.page} ↔ ${repo}` });
      await load();
    } catch (err) {
      push({ tone: "error", title: "Não consegui remover o vínculo", description: describeError(err) });
    }
  }

  async function setIgnored(row: DocPageRow, ignored: boolean) {
    const key = `${row.doc_repo}|${row.doc_path}`;
    setBusyKey(key);
    try {
      const target = { doc_repo: row.doc_repo, doc_path: row.doc_path };
      if (ignored) await api.ignoreDoc(target);
      else await api.unignoreDoc(target);
      setRows((cur) => cur?.map((r) => (r.doc_repo === row.doc_repo && r.doc_path === row.doc_path ? { ...r, ignored } : r)) ?? cur);
      push({
        tone: "success",
        title: ignored ? "Página fora da busca" : "Página de volta à busca",
        description: row.title ?? row.page,
        action: { label: "Desfazer", onClick: () => void setIgnored({ ...row, ignored }, !ignored) },
      });
      void loadSources();
    } catch (err) {
      push({ tone: "error", title: "Não consegui mudar a página", description: describeError(err) });
    } finally {
      setBusyKey(null);
    }
  }

  const refreshAll = useCallback(() => {
    void load();
    void loadSources();
    void reloadIndex();
  }, [load, loadSources, reloadIndex]);

  return (
    <section className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Documentação</h1>
          <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">
            O que entra no índice de cada fonte, e as páginas indexadas com os repos ligados a cada uma.
          </p>
        </div>
        <Segmented
          label="Seção da documentação"
          value={view}
          onChange={changeView}
          options={[
            { value: "pages", label: rows ? `Páginas ${numberFormat.format(rows.length)}` : "Páginas" },
            { value: "sources", label: sources ? `Fontes ${sources.length}` : "Fontes" },
          ]}
        />
      </header>

      {view === "sources" ? (
        <SourcesView
          sources={sources}
          error={sourcesError}
          repoByName={repoByName}
          admin={admin}
          canIndex={canIndex}
          onChoose={setEditing}
          onIndex={(s) => setSyncing(s.repo)}
        />
      ) : (
        <>
          <DocSuggestionsPanel admin={admin} onChanged={() => void load()} />

          <Panel className="space-y-3 p-3">
            <div className="flex flex-wrap items-center gap-3">
              <div className="relative min-w-[14rem] flex-1">
                <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-stone-400" aria-hidden="true" />
                <input
                  className={`${inputClass} pl-9`}
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="Buscar por título ou caminho"
                  aria-label="Buscar página"
                />
              </div>
              {docRepos.length > 1 && (
                <div className="w-44">
                  <Select
                    value={docRepo}
                    onChange={(v) => {
                      setDocRepo(v);
                      setRoot("");
                    }}
                    label="Fonte"
                  >
                    <option value="">Todas as fontes</option>
                    {docRepos.map((d) => (
                      <option key={d} value={d}>
                        {d.replace(/^docs__/, "")}
                      </option>
                    ))}
                  </Select>
                </div>
              )}
              <div className="w-52">
                <Select value={root} onChange={setRoot} label="Seção">
                  <option value="">Todas as seções</option>
                  {roots.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </Select>
              </div>
            </div>
            <Segmented
              label="Filtrar páginas"
              value={filter}
              onChange={setFilter}
              options={[
                { value: "all", label: "Todas" },
                { value: "unlinked", label: `Sem vínculo ${numberFormat.format(unlinkedCount)}` },
                { value: "ignored", label: `Fora da busca ${numberFormat.format(ignoredCount)}` },
              ]}
            />
          </Panel>

          {error && <Alert>{error}</Alert>}
          {rows === null && !error && (
            <div className="space-y-3">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-24 w-full rounded-xl" />
              ))}
            </div>
          )}
          {rows && filtered.length === 0 && (
            <EmptyState
              icon={BookOpen}
              title="Nenhuma página"
              text={
                rows.length === 0
                  ? "Nenhuma página indexada ainda. Em Fontes, escolha as páginas de uma wiki e indexe a fonte."
                  : "Nenhuma página bate com os filtros."
              }
              action={
                rows.length === 0 ? (
                  <Button variant="secondary" onClick={() => changeView("sources")}>
                    Ver fontes
                  </Button>
                ) : undefined
              }
            />
          )}

          <ul className="stagger space-y-3">
            {visible.map((row) => {
              const key = `${row.doc_repo}|${row.doc_path}`;
              return (
                <li
                  key={key}
                  className="space-y-3 rounded-xl border border-stone-200 bg-sheet p-4 dark:border-stone-800 dark:bg-stone-900"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex min-w-0 items-center gap-2">
                        <KindIcon kind="doc" size="sm" />
                        {row.url ? (
                          <a
                            href={row.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex min-w-0 items-center gap-1 font-medium text-accent-600 hover:underline dark:text-accent-400"
                          >
                            <span className="truncate">{row.title ?? row.page}</span>
                            <ExternalLink className="size-3.5 shrink-0" aria-hidden="true" />
                          </a>
                        ) : (
                          <p className="truncate font-medium">{row.title ?? row.page}</p>
                        )}
                        {row.ignored && <Badge tone="ignored">Fora da busca</Badge>}
                      </div>
                      <p className="truncate font-mono text-xs text-stone-500 dark:text-stone-400">
                        {docRepos.length > 1 && `${row.doc_repo.replace(/^docs__/, "")} · `}
                        {row.page}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <Button
                        variant="secondary"
                        size="sm"
                        aria-label={`Raio X de ${row.title ?? row.page}`}
                        onClick={() => onXray({ kind: "doc", id: pageId(row.doc_repo, row.doc_path) })}
                      >
                        <Network className="size-3.5" aria-hidden="true" /> Raio X
                      </Button>
                      {admin && (
                        <Button
                          variant="secondary"
                          size="sm"
                          loading={busyKey === key}
                          onClick={() => void setIgnored(row, !row.ignored)}
                          title={row.ignored ? "A página volta aos resultados da busca" : "A página continua no índice, mas sai dos resultados da busca"}
                        >
                          {row.ignored ? <Eye className="size-3.5" aria-hidden="true" /> : <EyeOff className="size-3.5" aria-hidden="true" />}
                          {row.ignored ? "Voltar à busca" : "Tirar da busca"}
                        </Button>
                      )}
                      {admin && (
                        <Button variant="secondary" size="sm" onClick={() => setAdding(row)}>
                          <Plus className="size-3.5" aria-hidden="true" />
                          Vincular repo
                        </Button>
                      )}
                    </div>
                  </div>

                  {row.links.length === 0 ? (
                    <p className="text-xs text-stone-500 dark:text-stone-400">Sem vínculo com repos.</p>
                  ) : (
                    <ul className="flex flex-wrap gap-2">
                      {row.links.map((link) => (
                        <li
                          key={link.repo}
                          className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-stone-200 px-2.5 py-1 text-xs dark:border-stone-700"
                        >
                          <span className="truncate font-medium">{link.repo}</span>
                          <OriginBadges origins={link.origins} />
                          {link.reason && (
                            <span title={link.reason} className="max-w-[16rem] truncate text-stone-500 dark:text-stone-400">
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
              );
            })}
          </ul>

          {filtered.length > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-stone-500 dark:text-stone-400">
              <span>
                {numberFormat.format(currentPage * PAGE_SIZE + 1)}–{numberFormat.format(Math.min((currentPage + 1) * PAGE_SIZE, filtered.length))} de{" "}
                {numberFormat.format(filtered.length)} páginas
              </span>
              <nav aria-label="Paginação da documentação" className="flex items-center gap-2">
                <Button variant="secondary" size="sm" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>
                  Anterior
                </Button>
                <span>
                  Página {currentPage + 1} de {pageCount}
                </span>
                <Button variant="secondary" size="sm" disabled={currentPage + 1 >= pageCount} onClick={() => setPage(currentPage + 1)}>
                  Próxima
                </Button>
              </nav>
            </div>
          )}
        </>
      )}

      <AddDocLinkDialog
        page={adding}
        onClose={() => setAdding(null)}
        onDone={() => {
          setAdding(null);
          void load();
        }}
      />
      <DocSelectionDialog
        source={editing}
        onClose={() => setEditing(null)}
        onSaved={() => void loadSources()}
        onIndex={
          canIndex
            ? (s) => {
                setEditing(null);
                setSyncing(s.repo);
              }
            : null
        }
      />
      <SyncDialog open={syncing !== null} onClose={() => setSyncing(null)} names={syncing ? [syncing] : null} onFinished={refreshAll} />
    </section>
  );
}

function SourcesView({
  sources,
  error,
  repoByName,
  admin,
  canIndex,
  onChoose,
  onIndex,
}: {
  sources: DocSourceInfo[] | null;
  error: string | null;
  repoByName: Map<string, Repo>;
  admin: boolean;
  canIndex: boolean;
  onChoose: (source: DocSourceInfo) => void;
  onIndex: (source: DocSourceInfo) => void;
}) {
  if (error) return <Alert>{error}</Alert>;
  if (!sources) {
    return (
      <div className="space-y-3">
        {[0, 1].map((i) => (
          <Skeleton key={i} className="h-40 w-full rounded-xl" />
        ))}
      </div>
    );
  }
  if (sources.length === 0) {
    return (
      <EmptyState
        icon={BookOpen}
        title="Nenhuma fonte de documentação"
        text="Cadastre uma wiki pela linha de comando (code-index docs add <nome> --url https://...). Depois, escolha aqui as páginas que entram."
      />
    );
  }
  return (
    <div className="stagger space-y-4">
      {sources.map((s) => (
        <SourceCard
          key={s.name}
          source={s}
          repo={repoByName.get(s.repo)}
          admin={admin}
          canIndex={canIndex}
          onChoose={() => onChoose(s)}
          onIndex={() => onIndex(s)}
        />
      ))}
    </div>
  );
}

function SourceCard({
  source: s,
  repo,
  admin,
  canIndex,
  onChoose,
  onIndex,
}: {
  source: DocSourceInfo;
  repo: Repo | undefined;
  admin: boolean;
  canIndex: boolean;
  onChoose: () => void;
  onIndex: () => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const status = STATUS[repo?.status ?? "pending"];
  const rules = [...s.include.map((g) => ({ g, kind: "include" as const })), ...s.exclude.map((g) => ({ g, kind: "exclude" as const }))];
  const shown = showAll ? rules : rules.slice(0, RULES_SHOWN);
  return (
    <Panel className="overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-stone-200 p-5 dark:border-stone-800">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-semibold">{s.name}</h2>
            <Badge>{s.label}</Badge>
            <Badge tone={status.tone} dot>
              {status.label}
            </Badge>
          </div>
          <p className="flex flex-wrap items-center gap-x-2 text-sm text-stone-500 dark:text-stone-400">
            <a href={s.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-accent-600 hover:underline dark:text-accent-400">
              {s.url}
              <ExternalLink className="size-3.5" aria-hidden="true" />
            </a>
            <span>· {s.page_selection ? `idioma ${s.locale}` : `domínio ${s.locale}`}</span>
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {s.page_selection && admin && (
            <Button variant="secondary" onClick={onChoose}>
              <ListChecks className="size-4" aria-hidden="true" />
              Escolher páginas
            </Button>
          )}
          {canIndex && (
            <Button variant="secondary" onClick={onIndex} title="Abre a prévia; o custo na Voyage só acontece depois de confirmar">
              <RefreshCw className="size-4" aria-hidden="true" />
              Indexar…
            </Button>
          )}
        </div>
      </div>

      <dl className="grid divide-stone-200 sm:grid-cols-3 sm:divide-x dark:divide-stone-800">
        <Fact label={s.page_selection ? "páginas no índice" : "documentos no índice"} value={numberFormat.format(s.documents)} />
        <Fact label="fora da busca" value={numberFormat.format(s.ignored)} />
        <Fact
          label="última indexação"
          value={repo?.indexed_at ? timeAgo(repo.indexed_at) : "nunca"}
          title={repo?.indexed_at ? formatDateTime(repo.indexed_at) : undefined}
        />
      </dl>

      <div className="space-y-2 border-t border-stone-200 p-5 dark:border-stone-800">
        {repo?.error && <Alert>{repo.error}</Alert>}
        <p className="text-xs font-medium text-stone-500 dark:text-stone-400">
          {s.page_selection ? "O que entra (caminhos da wiki)" : "Serviços escolhidos"}
        </p>
        {rules.length === 0 ? (
          <p className="text-sm">{s.page_selection ? "Todas as páginas publicadas no idioma, inclusive as novas." : "O domínio inteiro, inclusive serviços novos."}</p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {shown.map(({ g, kind }) => (
              <li key={`${kind}${g}`}>
                <span
                  className={
                    kind === "include"
                      ? "inline-flex items-center gap-1 rounded-md bg-emerald-500/10 px-2 py-0.5 font-mono text-xs text-emerald-800 dark:text-emerald-300"
                      : "inline-flex items-center gap-1 rounded-md bg-rose-500/10 px-2 py-0.5 font-mono text-xs text-rose-800 dark:text-rose-300"
                  }
                  title={kind === "include" ? "entra" : "fica de fora"}
                >
                  {kind === "include" ? "+" : "−"} {g}
                </span>
              </li>
            ))}
            {rules.length > RULES_SHOWN && (
              <li>
                <button type="button" onClick={() => setShowAll(!showAll)} className="px-1 text-xs font-medium text-accent-600 hover:underline dark:text-accent-400">
                  {showAll ? "Mostrar menos" : `+${rules.length - RULES_SHOWN} ${plural(rules.length - RULES_SHOWN, "regra", "regras")}`}
                </button>
              </li>
            )}
          </ul>
        )}
        {!s.page_selection && <p className="text-xs text-stone-500 dark:text-stone-400">A seleção desta fonte é feita na aba APIs.</p>}
      </div>
    </Panel>
  );
}

function Fact({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="flex flex-col-reverse gap-0.5 border-stone-200 px-5 py-4 not-first:border-t sm:not-first:border-t-0 dark:border-stone-800" title={title}>
      <dt className="text-sm text-stone-600 dark:text-stone-300">{label}</dt>
      <dd className="text-xl font-semibold tracking-tight text-stone-900 tabular-nums dark:text-white">{value}</dd>
    </div>
  );
}
