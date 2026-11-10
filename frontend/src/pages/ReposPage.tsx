import { useCallback, useEffect, useId, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Boxes,
  X,
  Database,
  IdCard,
  Search,
  ServerCrash,
  ChevronLeft,
  ChevronRight,
  BookOpen,
  Network,
  Play,
  Plus,
  Trash2,
  Undo2,
} from "lucide-react";
import type { OwnerOrigin, OwnershipCatalog, Repo, RepoProgress, RepoStatusValue } from "../api/types";
import { useIndex } from "../data/IndexProvider";
import { useAuth } from "../auth/AuthProvider";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import { canWrite, hasScope } from "../lib/capabilities";
import { useToast } from "../components/Toaster";
import { Dialog } from "../components/Dialog";
import { RepoDocsDialog } from "../components/RepoDocsDialog";
import { RepoCardDialog } from "../components/RepoCardDialog";
import { RepoSummariesLink } from "../components/summaries";
import { OwnerDialog } from "../components/OwnerDialog";
import { ORIGIN_HINT, ORIGIN_LABEL, ownerMatches } from "../lib/ownership";
import {
  ALL_OWNERS,
  CONFIDENCE_LABEL,
  CONFIDENCE_VALUES,
  NO_OWNER,
  SEARCH_MAX,
  isFilterActive,
  readFiltersFromHash,
  writeFiltersToHash,
  type Confidence,
  type ConfidenceFilter,
  type Filters,
  type StatusFilter,
} from "../lib/repoFilters";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { SyncDialog } from "../components/SyncDialog";
import { ActiveJobNotice, useJobs } from "../components/JobsPanel";
import { OwnershipStrip } from "../components/OwnershipStrip";
import { useRepoCounts } from "../components/IndexCoverage";
import { cx, formatDateTime, plural, repoShortName, timeAgo } from "../lib/utils";
import { Alert, Badge, Button, EmptyState, Field, IconButton, Panel, Segmented, Select, Skeleton, inputClass, tableHeadClass, type BadgeTone } from "../components/ui";
import type { XrayTarget } from "../lib/xray";

type SortKey = "name" | "status" | "owner" | "language" | "files" | "chunks" | "indexed_at";

interface Sort {
  key: SortKey;
  dir: 1 | -1;
}

const STATUS_LABEL: Record<RepoStatusValue, string> = {
  indexed: "Indexado",
  pending: "Pendente",
  error: "Erro",
  ignored: "Ignorado",
};

const numberFormat = new Intl.NumberFormat("pt-BR");

export function ReposPage({ onXray }: { onXray: (target: XrayTarget) => void }) {
  const { repos, loading, reload } = useIndex();
  const { session } = useAuth();
  const { push } = useToast();
  const [syncOpen, setSyncOpen] = useState<{ names: string[] | null } | null>(null);
  // Seleção múltipla para indexar vários repos de uma vez (persiste entre páginas).
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  // Último checkbox marcado: âncora para o shift+clique (intervalo).
  const anchor = useRef<string | null>(null);
  const [selectionOpen, setSelectionOpen] = useState(false);
  const selectionBar = useRef<HTMLDivElement>(null);
  const selectionToggle = useRef<HTMLButtonElement>(null);
  // Filtros lidos da URL uma vez, na montagem (depois a própria página mantém a URL em dia).
  const [initial] = useState<Filters>(readFiltersFromHash);
  const [addOpen, setAddOpen] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<Repo | null>(null);
  const writable = canWrite(session);
  const me = session?.me ?? null;
  const isAdmin = me !== null && hasScope(me, "admin");
  const canIndex = writable && me !== null && hasScope(me, "index:write");

  const onFinished = useCallback(() => void reload(), [reload]);
  // Jobs: leitura para quem tem index:write (vale também para API key com esse scope). Aqui só o job ativo
  // importa (selo na linha e botões); cobertura, fila e histórico ficam na página Indexação.
  const canSeeJobs = me !== null && hasScope(me, "index:write");
  const jobs = useJobs(canSeeJobs, onFinished);
  // Selo ao vivo na linha: o status gravado do repo só muda quando o lote inteiro termina.
  const live = useMemo(() => {
    const job = jobs.running;
    if (!job) return new Map<string, RepoProgress["status"]>();
    // Na fila: os repos pedidos aparecem como "na fila" (o andamento gravado seria de uma tentativa anterior).
    if (job.status === "queued") return new Map((job.params?.repos ?? []).map((name) => [name, "queued" as const]));
    return new Map((job.progress?.repos ?? []).map((p) => [p.name, p.status] as const));
  }, [jobs.running]);

  async function unignore(r: Repo) {
    try {
      await api.unignoreRepo(r.name);
      push({ tone: "success", title: "Repositório devolvido à fila", description: r.name });
      await reload();
    } catch (err) {
      push({ tone: "error", title: "Não consegui reativar", description: describeError(err) });
    }
  }

  // Dono no DDD: catálogo (para o filtro e o diálogo) e quem está sendo editado.
  const canOwn = writable && isAdmin;
  const [catalog, setCatalog] = useState<OwnershipCatalog | null>(null);
  const [ownerTarget, setOwnerTarget] = useState<Repo[] | null>(null);
  const [ownerFilter, setOwnerFilter] = useState<string>(initial.domain);
  const loadCatalog = useCallback(() => {
    api.ownershipCatalog().then(setCatalog).catch(() => setCatalog(null));
  }, []);
  useEffect(() => loadCatalog(), [loadCatalog]);

  const rowActions: RowActions | null = writable
    ? {
        canIndex,
        canAdmin: isAdmin,
        canOwn,
        onIndex: (r) => setSyncOpen({ names: [r.name] }),
        onRemove: (r) => setRemoveTarget(r),
        onUnignore: (r) => void unignore(r),
        onOwner: (r) => setOwnerTarget([r]),
      }
    : null;
  const [status, setStatus] = useState<StatusFilter>(initial.status);
  const [originFilter, setOriginFilter] = useState<OwnerOrigin | "">(initial.origin);
  const [confidenceFilter, setConfidenceFilter] = useState<ConfidenceFilter>(initial.confidence);
  const [outdatedOnly, setOutdatedOnly] = useState(initial.outdated);
  const [term, setTerm] = useState(initial.search);
  const [sort, setSort] = useState<Sort>({ key: "name", dir: 1 });
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<PageSize>(25);
  const listTop = useRef<HTMLDivElement>(null);

  const counts = useRepoCounts(repos);

  const rows = useMemo(() => {
    const needle = term.trim().toLowerCase();
    return (repos ?? [])
      .filter((r) => status === "all" || r.status === status)
      .filter((r) =>
        ownerFilter === ALL_OWNERS ? true : ownerFilter === NO_OWNER ? r.owners.length === 0 : ownerMatches(r.owners, ownerFilter),
      )
      .filter((r) => originFilter === "" || r.owner_origin === originFilter)
      // Confiança só existe em repo com dono: com o filtro ligado, os sem dono saem.
      .filter((r) => confidenceFilter === "" || (r.owners.length > 0 && r.owner_confidence === confidenceFilter))
      .filter((r) => !outdatedOnly || r.outdated)
      .filter(
        (r) =>
          !needle ||
          r.name.toLowerCase().includes(needle) ||
          (r.language ?? "").toLowerCase().includes(needle) ||
          (r.summary ?? "").toLowerCase().includes(needle) ||
          r.tags.some((t) => t.toLowerCase().includes(needle)),
      )
      .sort((a, b) => compare(a, b, sort.key) * sort.dir || a.name.localeCompare(b.name));
  }, [repos, status, term, sort, ownerFilter, originFilter, confidenceFilter, outdatedOnly]);

  // Paginação: filtro, busca, ordenação ou tamanho da página voltam para a primeira página.
  useEffect(() => setPage(0), [status, term, sort, pageSize, ownerFilter, originFilter, confidenceFilter, outdatedOnly]);
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const current = Math.min(page, pageCount - 1);
  const visible = rows.slice(current * pageSize, (current + 1) * pageSize);

  // Filtros na URL (sem entrada nova no histórico).
  const filters: Filters = {
    status,
    domain: ownerFilter,
    origin: originFilter,
    confidence: confidenceFilter,
    outdated: outdatedOnly,
    search: term,
  };
  useEffect(() => writeFiltersToHash(filters), [status, ownerFilter, originFilter, confidenceFilter, outdatedOnly, term]);

  // Domínio vindo da URL que não existe no catálogo (ou catálogo ainda não carregou): mostra a opção, para o select não mentir.
  const knownDomains = useMemo(() => {
    const set = new Set<string>();
    for (const d of catalog?.domains ?? []) {
      set.add(d.name);
      for (const s of d.services) set.add(`${d.name}/${s.name}`);
    }
    return set;
  }, [catalog]);
  const domainUnknown = ownerFilter !== ALL_OWNERS && ownerFilter !== NO_OWNER && !knownDomains.has(ownerFilter);

  const isSelectable = (r: Repo) => (canIndex || canOwn) && r.status !== "ignored";
  const selectableOnPage = visible.filter(isSelectable);
  const pageAllSelected = selectableOnPage.length > 0 && selectableOnPage.every((r) => selected.has(r.name));
  const pageSomeSelected = selectableOnPage.some((r) => selected.has(r.name));

  /**
   * Marca/desmarca um repo. Com shift, aplica o mesmo estado a todos os selecionáveis entre a âncora
   * (último clique) e este, dentro da lista filtrada e ordenada (não só da página visível).
   */
  function pick(r: Repo, shift: boolean) {
    const willSelect = !selected.has(r.name);
    const from = anchor.current ? rows.findIndex((x) => x.name === anchor.current) : -1;
    const to = rows.indexOf(r);
    const span = shift && from >= 0 && to >= 0 ? rows.slice(Math.min(from, to), Math.max(from, to) + 1) : [r];
    setSelected((cur) => {
      const next = new Set(cur);
      for (const x of span) {
        if (!isSelectable(x)) continue;
        if (willSelect) next.add(x.name);
        else next.delete(x.name);
      }
      return next;
    });
    anchor.current = r.name;
  }

  const selection: Selection | null = canIndex || canOwn
    ? {
        isSelected: (r) => selected.has(r.name),
        canSelect: isSelectable,
        toggle: pick,
      }
    : null;

  function togglePage() {
    setSelected((cur) => {
      const next = new Set(cur);
      for (const r of selectableOnPage) {
        if (pageAllSelected) next.delete(r.name);
        else next.add(r.name);
      }
      return next;
    });
  }

  // Repos que saíram da lista (removidos/ignorados) saem da seleção.
  useEffect(() => {
    if (!repos) return;
    const valid = new Set(repos.filter((r) => r.status !== "ignored").map((r) => r.name));
    setSelected((cur) => {
      const next = new Set([...cur].filter((n) => valid.has(n)));
      return next.size === cur.size ? cur : next;
    });
  }, [repos]);

  const cols = 8 + (rowActions ? 1 : 0) + (selection ? 1 : 0);

  function goTo(next: number) {
    setPage(Math.min(Math.max(0, next), pageCount - 1));
    listTop.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  function toggleSort(key: SortKey) {
    setSort((current) =>
      current.key === key ? { key, dir: current.dir === 1 ? -1 : 1 } : { key, dir: 1 },
    );
  }

  const statusOptions: { value: StatusFilter; label: string }[] = [
    { value: "all", label: `Todos ${counts.total}` },
    { value: "indexed", label: `Indexados ${counts.byStatus.indexed}` },
    { value: "pending", label: `Pendentes ${counts.byStatus.pending}` },
    { value: "error", label: `Com erro ${counts.byStatus.error}` },
    { value: "ignored", label: `Ignorados ${counts.byStatus.ignored}` },
  ];

  const barVisible = selection !== null && selected.size > 0;

  // Painel da seleção: fecha com Escape (o foco volta ao contador) e com clique fora; some junto com a barra.
  useEffect(() => {
    if (!selectionOpen) return;
    function onPointer(e: PointerEvent) {
      if (!selectionBar.current?.contains(e.target as Node)) setSelectionOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      setSelectionOpen(false);
      selectionToggle.current?.focus();
    }
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [selectionOpen]);
  useEffect(() => {
    if (!barVisible) setSelectionOpen(false);
  }, [barVisible]);

  return (
    <div className={cx("space-y-8", barVisible && "pb-28")}>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Repositórios</h1>
          <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">
            Catálogo do índice: o que cada repositório faz, documentação ligada e domínio (DDD).
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" size="sm" loading={loading} onClick={() => void reload()}>
            Atualizar
          </Button>
          {isAdmin && (
            <Button variant="secondary" size="sm" onClick={() => setAddOpen(true)}>
              <Plus className="size-3.5" aria-hidden="true" />
              Adicionar repositório
            </Button>
          )}
        </div>
      </div>

      {jobs.running && <ActiveJobNotice job={jobs.running} />}

      {/* Faixa de domínio colada à lista: ela filtra o que está logo abaixo. */}
      <div ref={listTop} className="scroll-mt-20 space-y-3">
      <OwnershipStrip
        coverage={catalog?.coverage ?? null}
        loading={repos === null && loading}
        originFilter={originFilter}
        onOrigin={(o) => {
          setOriginFilter((cur) => (cur === o ? "" : o));
          if (originFilter !== o) listTop.current?.scrollIntoView({ block: "start", behavior: "smooth" });
        }}
        onShowUnowned={() => {
          // Sem dono: status, origem, confiança e push antigos não podem esconder a fila inteira.
          setStatus("all");
          setOriginFilter("");
          setConfidenceFilter("");
          setOutdatedOnly(false);
          setOwnerFilter(NO_OWNER);
          listTop.current?.scrollIntoView({ block: "start", behavior: "smooth" });
        }}
      />

      <Panel className="overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-stone-200 p-4 sm:flex-row sm:items-center sm:justify-between dark:border-stone-800">
          <Segmented label="Filtrar por status" value={status} onChange={setStatus} options={statusOptions} />
          <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row">
          <div className="w-full sm:w-52">
            <Select value={ownerFilter} onChange={setOwnerFilter} label="Filtrar por domínio">
              <option value={ALL_OWNERS}>Todos os domínios</option>
              <option value={NO_OWNER}>Sem domínio</option>
              {domainUnknown && <option value={ownerFilter}>{ownerFilter} (fora do catálogo)</option>}
              {(catalog?.domains ?? []).map((d) => (
                <optgroup key={d.name} label={d.label ? `${d.name} · ${d.label}` : d.name}>
                  <option value={d.name}>
                    {d.name} (todo o domínio, {d.repos})
                  </option>
                  {d.services.map((svc) => (
                    <option key={svc.name} value={`${d.name}/${svc.name}`}>
                      {d.name}/{svc.name} ({svc.repos})
                    </option>
                  ))}
                </optgroup>
              ))}
            </Select>
          </div>
          <div className="w-full sm:w-44">
            <Select value={confidenceFilter} onChange={(v) => setConfidenceFilter(v as ConfidenceFilter)} label="Filtrar por confiança do domínio">
              <option value="">Toda confiança</option>
              {CONFIDENCE_VALUES.map((c) => (
                <option key={c} value={c}>
                  {c === "alta" ? "Alta" : c === "media" ? "Média" : "Baixa"}
                </option>
              ))}
            </Select>
          </div>
          <div className="relative w-full sm:w-64">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-stone-400" aria-hidden="true" />
            <input
              value={term}
              onChange={(e) => setTerm(e.target.value.slice(0, SEARCH_MAX))}
              placeholder="Buscar repositórios"
              aria-label="Buscar repositórios"
              className={cx(inputClass, "pl-9")}
            />
          </div>
          </div>
        </div>

        {isFilterActive(filters) && (
          <ActiveFilters
            filters={filters}
            onClear={(key) => {
              if (key === "status") setStatus("all");
              else if (key === "domain") setOwnerFilter(ALL_OWNERS);
              else if (key === "origin") setOriginFilter("");
              else if (key === "confidence") setConfidenceFilter("");
              else if (key === "outdated") setOutdatedOnly(false);
              else setTerm("");
            }}
            onClearAll={() => {
              setStatus("all");
              setOwnerFilter(ALL_OWNERS);
              setOriginFilter("");
              setConfidenceFilter("");
              setOutdatedOnly(false);
              setTerm("");
            }}
          />
        )}

        {/* Colunas opcionais pela largura do painel (@container), não da janela: o conteúdo tem largura máxima, e em
            Full HD todas apareciam, a tabela passava do painel e a última coluna ficava sob "Ações" (fixa à direita). */}
        <div className="@container hidden overflow-x-auto md:block">
          <table className="w-full text-sm">
            <thead className={tableHeadClass}>
              <tr>
                {selection && (
                  <th className="w-10 py-3 pl-4">
                    <Checkbox
                      label="Selecionar todos desta página"
                      checked={pageAllSelected}
                      indeterminate={!pageAllSelected && pageSomeSelected}
                      disabled={selectableOnPage.length === 0}
                      onChange={togglePage}
                    />
                  </th>
                )}
                <SortHeader label="Repositório" sortKey="name" sort={sort} onSort={toggleSort} />
                <SortHeader label="Status" sortKey="status" sort={sort} onSort={toggleSort} />
                <SortHeader label="Domínio" sortKey="owner" sort={sort} onSort={toggleSort} className="hidden @4xl:table-cell" />
                <SortHeader label="Linguagem" sortKey="language" sort={sort} onSort={toggleSort} className="hidden @[70rem]:table-cell" />
                <SortHeader label="Arquivos" sortKey="files" sort={sort} onSort={toggleSort} align="right" className="hidden @6xl:table-cell" />
                <SortHeader label="Trechos" sortKey="chunks" sort={sort} onSort={toggleSort} align="right" />
                <th className="hidden px-4 py-3 font-medium @[80rem]:table-cell">Commit</th>
                <SortHeader label="Indexado" sortKey="indexed_at" sort={sort} onSort={toggleSort} className="hidden @3xl:table-cell" />
                {rowActions && (
                  <th className="sticky right-0 z-[2] bg-stone-50 px-4 py-3 text-right font-medium shadow-[-8px_0_8px_-8px_rgb(0_0_0/0.12)] dark:bg-stone-900">
                    Ações
                  </th>
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-stone-100 dark:divide-stone-800">
              {repos === null && loading && <SkeletonRows cols={cols} />}
              {repos === null && !loading && (
                <tr>
                  <td colSpan={cols}>
                    <EmptyState
                      icon={ServerCrash}
                      title="Não consegui carregar os repositórios"
                      action={
                        <Button variant="secondary" size="sm" onClick={() => void reload()}>
                          Tentar de novo
                        </Button>
                      }
                    />
                  </td>
                </tr>
              )}
              {repos !== null && rows.length === 0 && (
                <tr>
                  <td colSpan={cols}>
                    <EmptyState icon={Database} title="Nenhum repositório com esse filtro" />
                  </td>
                </tr>
              )}
              {visible.map((r) => (
                <RepoRow key={r.name} repo={r} live={live.get(r.name)} actions={rowActions} selection={selection} onXray={onXray} />
              ))}
            </tbody>
          </table>
        </div>

        <ul className="divide-y divide-stone-100 md:hidden dark:divide-stone-800">
          {repos === null && loading && [0, 1, 2].map((i) => (
            <li key={i} className="p-4">
              <Skeleton className="h-16 w-full" />
            </li>
          ))}
          {repos !== null && rows.length === 0 && (
            <li>
              <EmptyState icon={Database} title="Nenhum repositório com esse filtro" />
            </li>
          )}
          {visible.map((r) => (
            <RepoCard key={r.name} repo={r} live={live.get(r.name)} actions={rowActions} selection={selection} onXray={onXray} />
          ))}
        </ul>

        {repos !== null && rows.length > 0 && (
          <Pagination
            page={current}
            pageCount={pageCount}
            pageSize={pageSize}
            total={rows.length}
            unfiltered={counts.total}
            onPage={goTo}
            onPageSize={setPageSize}
          />
        )}
      </Panel>
      </div>

      {barVisible && (
        <div className="pointer-events-none fixed inset-x-0 bottom-4 z-30 flex justify-center px-4 lg:left-(--sidebar-w)">
          <div
            ref={selectionBar}
            className="pointer-events-auto relative flex max-w-full items-center gap-2 rounded-xl bg-ink-950/90 py-2 pr-2 pl-3 text-white shadow-[0_8px_24px_-8px_rgb(0_0_0/0.45)] ring-1 ring-white/10 backdrop-blur-md animate-pop sm:gap-3 sm:pl-4"
          >
            {selectionOpen && (
              <SelectionPanel
                names={[...selected].sort()}
                repos={repos ?? []}
                onRemove={(name) =>
                  setSelected((cur) => {
                    const next = new Set(cur);
                    next.delete(name);
                    return next;
                  })
                }
              />
            )}
            <p role="status" aria-live="polite" className="shrink-0 text-sm text-ink-100">
              <button
                ref={selectionToggle}
                type="button"
                aria-expanded={selectionOpen}
                aria-controls="repos-selection-panel"
                title="Ver os repositórios selecionados"
                onClick={() => setSelectionOpen((open) => !open)}
                className="rounded-md px-1 py-0.5 text-ink-100 underline decoration-ink-400 underline-offset-4 outline-none transition hover:bg-white/10 hover:text-white focus-visible:ring-2 focus-visible:ring-accent-400"
              >
                <span className="font-semibold tabular-nums">{selected.size}</span>
                <span className="sm:hidden"> sel.</span>
                <span className="hidden sm:inline"> {plural(selected.size, "repositório selecionado", "repositórios selecionados")}</span>
              </button>
            </p>
            {canIndex && (
              <Button
                size="sm"
                className="shrink-0 px-2.5 sm:px-3"
                onClick={() => setSyncOpen({ names: [...selected].sort() })}
                disabled={jobs.running !== null}
                title={jobs.running ? "Já existe uma indexação na fila ou em andamento (acompanhe em Indexação)" : undefined}
              >
                <Play className="size-3.5" aria-hidden="true" />
                <span className="sm:hidden">Indexar</span>
                <span className="hidden sm:inline">Indexar selecionados</span>
              </Button>
            )}
            {canOwn && (
              <button
                type="button"
                onClick={() => setOwnerTarget((repos ?? []).filter((r) => selected.has(r.name)))}
                className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg bg-white/10 px-2.5 text-xs font-medium text-white outline-none transition hover:bg-white/15 focus-visible:ring-2 focus-visible:ring-accent-400 sm:px-3"
              >
                <Boxes className="size-3.5" aria-hidden="true" />
                <span className="sm:hidden">Domínio</span>
                <span className="hidden sm:inline">Definir domínio</span>
              </button>
            )}
            <button
              type="button"
              onClick={() => setSelected(new Set())}
              className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-ink-300 outline-none transition hover:bg-white/10 hover:text-white focus-visible:ring-2 focus-visible:ring-accent-400 sm:px-3"
            >
              <X className="size-3.5" aria-hidden="true" />
              Limpar
            </button>
          </div>
        </div>
      )}
      <OwnerDialog
        open={ownerTarget !== null}
        repos={ownerTarget ?? []}
        catalog={catalog}
        onClose={() => setOwnerTarget(null)}
        onDone={() => {
          setSelected(new Set());
          loadCatalog();
          void reload();
        }}
      />
      <SyncDialog
        open={syncOpen !== null}
        onClose={() => setSyncOpen(null)}
        names={syncOpen?.names ?? null}
        onFinished={onFinished}
        onStarted={() => {
          setSelected(new Set());
          void jobs.refresh();
        }}
      />
      <AddRepoDialog open={addOpen} onClose={() => setAddOpen(false)} onDone={() => void reload()} />
      <ConfirmDialog
        open={removeTarget !== null}
        onClose={() => setRemoveTarget(null)}
        title={removeTarget?.org ? "Ignorar repositório" : "Remover repositório"}
        confirmLabel={removeTarget?.org ? "Ignorar" : "Remover"}
        danger
        onConfirm={async () => {
          if (!removeTarget) return;
          const res = await api.removeRepo(removeTarget.name);
          push({
            tone: "success",
            title: res.result === "ignored" ? "Repositório ignorado" : "Repositório removido",
            description: "Os trechos somem no próximo sync.",
          });
          await reload();
        }}
      >
        {removeTarget?.org ? (
          <p>
            <span className="font-mono">{removeTarget.name}</span> é de uma org: ele fica marcado como ignorado e
            pode ser reativado depois.
          </p>
        ) : (
          <p>
            Remover <span className="font-mono">{removeTarget?.name}</span> da configuração e apagar o clone local.
            Os trechos indexados somem no próximo sync.
          </p>
        )}
      </ConfirmDialog>
    </div>
  );
}

function AddRepoDialog({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const { push } = useToast();
  const [source, setSource] = useState("");
  const [name, setName] = useState("");
  const [branch, setBranch] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.addRepo({
        source: source.trim(),
        name: name.trim() || null,
        branch: branch.trim() || null,
      });
      push({ tone: "success", title: "Repositório adicionado", description: `${created.name}: indexe quando quiser.` });
      setSource("");
      setName("");
      setBranch("");
      onDone();
      onClose();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Adicionar repositório"
      description="URL git, org/repo do GitHub ou caminho de um diretório do servidor. Só o cadastro: a indexação é separada."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancelar
          </Button>
          <Button type="submit" form="add-repo-form" loading={busy} disabled={!source.trim()}>
            Adicionar
          </Button>
        </>
      }
    >
      <form id="add-repo-form" onSubmit={(e) => void submit(e)} className="space-y-4">
        <Field label="Origem">
          <input value={source} onChange={(e) => setSource(e.target.value)} className={inputClass} placeholder="org/repo ou https://…" />
        </Field>
        <Field label="Nome (opcional)">
          <input value={name} onChange={(e) => setName(e.target.value)} className={inputClass} placeholder="gerado a partir da origem" />
        </Field>
        <Field label="Branch (opcional)">
          <input value={branch} onChange={(e) => setBranch(e.target.value)} className={inputClass} placeholder="padrão do remoto" />
        </Field>
        {error && <Alert>{error}</Alert>}
      </form>
    </Dialog>
  );
}

type FilterKey = "status" | "domain" | "origin" | "confidence" | "outdated" | "search";

/** Linha "Filtros ativos": um chip por filtro (com × para tirar só ele) e um botão para limpar tudo. */
function ActiveFilters({
  filters,
  onClear,
  onClearAll,
}: {
  filters: Filters;
  onClear: (key: FilterKey) => void;
  onClearAll: () => void;
}) {
  const chips: { key: FilterKey; label: string }[] = [];
  if (filters.status !== "all") chips.push({ key: "status", label: `Status: ${STATUS_LABEL[filters.status]}` });
  if (filters.domain !== ALL_OWNERS) {
    chips.push({ key: "domain", label: `Domínio: ${filters.domain === NO_OWNER ? "sem domínio" : filters.domain}` });
  }
  if (filters.origin) chips.push({ key: "origin", label: `Origem: ${ORIGIN_LABEL[filters.origin]}` });
  if (filters.confidence) chips.push({ key: "confidence", label: `Confiança: ${CONFIDENCE_LABEL[filters.confidence]}` });
  if (filters.outdated) chips.push({ key: "outdated", label: "Com push novo" });
  if (filters.search.trim()) chips.push({ key: "search", label: `Busca: "${filters.search.trim()}"` });

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-stone-200 px-4 py-2.5 text-xs dark:border-stone-800">
      <span className="text-stone-500 dark:text-stone-400">Filtros ativos:</span>
      {chips.map((c) => (
        <span
          key={c.key}
          className="inline-flex items-center gap-1 rounded-full py-0.5 pr-0.5 pl-2.5 font-medium text-accent-700 ring-1 ring-inset ring-accent-200 bg-accent-50 dark:bg-accent-500/10 dark:text-accent-300 dark:ring-accent-500/30"
        >
          {c.label}
          <button
            type="button"
            aria-label={`Remover filtro ${c.label}`}
            onClick={() => onClear(c.key)}
            className="inline-flex size-5 items-center justify-center rounded-full outline-none transition hover:bg-accent-100 focus-visible:ring-2 focus-visible:ring-accent-500 dark:hover:bg-accent-500/20"
          >
            <X className="size-3" aria-hidden="true" />
          </button>
        </span>
      ))}
      <Button variant="ghost" size="sm" className="ml-auto" onClick={onClearAll}>
        Limpar filtros
      </Button>
    </div>
  );
}

/** Lista dos repos selecionados, para conferir a seleção (inclusive entre páginas) antes de gastar com indexação. */
function SelectionPanel({
  names,
  repos,
  onRemove,
}: {
  names: string[];
  repos: Repo[];
  onRemove: (name: string) => void;
}) {
  const byName = new Map(repos.map((r) => [r.name, r] as const));
  return (
    <div
      id="repos-selection-panel"
      role="region"
      aria-label="Repositórios selecionados"
      className="absolute bottom-full left-0 mb-3 w-72 max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl bg-ink-950 text-white shadow-[0_12px_32px_-12px_rgb(0_0_0/0.55)] ring-1 ring-white/10 animate-pop"
    >
      <p className="border-b border-white/10 px-3 py-2 text-xs text-ink-300">
        Confira antes de indexar: {numberFormat.format(names.length)} {plural(names.length, "repositório", "repositórios")}
      </p>
      <ul className="max-h-64 overflow-y-auto py-1">
        {names.map((name) => {
          const repo = byName.get(name);
          const short = repo ? repoShortName(repo.name, repo.org) : name;
          return (
            <li key={name} className="flex items-center gap-2 py-1 pr-1 pl-3">
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-100" title={name}>
                {short}
              </span>
              <button
                type="button"
                aria-label={`Tirar ${short} da seleção`}
                onClick={() => onRemove(name)}
                className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-ink-300 outline-none transition hover:bg-white/10 hover:text-white focus-visible:ring-2 focus-visible:ring-accent-400"
              >
                <X className="size-3.5" aria-hidden="true" />
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

const PAGE_SIZES = [25, 50, 100] as const;
type PageSize = (typeof PAGE_SIZES)[number];

function Pagination({
  page,
  pageCount,
  pageSize,
  total,
  unfiltered,
  onPage,
  onPageSize,
}: {
  page: number;
  pageCount: number;
  pageSize: PageSize;
  total: number;
  unfiltered: number;
  onPage: (page: number) => void;
  onPageSize: (size: PageSize) => void;
}) {
  const first = page * pageSize + 1;
  const last = Math.min(total, (page + 1) * pageSize);
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-stone-200 px-4 py-3 text-xs text-stone-500 dark:border-stone-800 dark:text-stone-400">
      <p>
        {numberFormat.format(first)}–{numberFormat.format(last)} de {numberFormat.format(total)}
        {total !== unfiltered && <> (filtrados de {numberFormat.format(unfiltered)})</>}
      </p>
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-2">
          <span className="hidden sm:inline">Por página</span>
          <div className="w-20">
            <Select value={String(pageSize)} onChange={(v) => onPageSize(Number(v) as PageSize)}>
              {PAGE_SIZES.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </Select>
          </div>
        </label>
        <nav aria-label="Paginação" className="flex items-center gap-1">
          <IconButton label="Página anterior" onClick={() => onPage(page - 1)} disabled={page === 0}>
            <ChevronLeft className="size-4" />
          </IconButton>
          <span className="min-w-20 text-center tabular-nums" aria-live="polite">
            Página {page + 1} de {pageCount}
          </span>
          <IconButton label="Próxima página" onClick={() => onPage(page + 1)} disabled={page >= pageCount - 1}>
            <ChevronRight className="size-4" />
          </IconButton>
        </nav>
      </div>
    </div>
  );
}

function SortHeader({
  label,
  sortKey,
  sort,
  onSort,
  align = "left",
  className,
}: {
  label: string;
  sortKey: SortKey;
  sort: Sort;
  onSort: (key: SortKey) => void;
  align?: "left" | "right";
  className?: string;
}) {
  const active = sort.key === sortKey;
  const Icon = !active ? ArrowUpDown : sort.dir === 1 ? ArrowUp : ArrowDown;
  const ariaSort = !active ? "none" : sort.dir === 1 ? "ascending" : "descending";
  return (
    <th aria-sort={ariaSort} className={cx("px-4 py-3 font-medium", align === "right" && "text-right", className)}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cx(
          "inline-flex items-center gap-1 rounded-md outline-none transition hover:text-stone-900 focus-visible:ring-2 focus-visible:ring-accent-500 dark:hover:text-stone-100",
          active && "text-stone-900 dark:text-stone-100",
        )}
      >
        {label}
        <Icon className="size-3 opacity-60" aria-hidden="true" />
      </button>
    </th>
  );
}

interface RowActions {
  canIndex: boolean;
  canAdmin: boolean;
  canOwn: boolean;
  onIndex: (r: Repo) => void;
  onRemove: (r: Repo) => void;
  onUnignore: (r: Repo) => void;
  onOwner: (r: Repo) => void;
}

interface Selection {
  isSelected: (r: Repo) => boolean;
  canSelect: (r: Repo) => boolean;
  /** `shift`: intervalo desde o último checkbox marcado. */
  toggle: (r: Repo, shift: boolean) => void;
}

/** Shift no clique (ou no espaço, que dispara um click nativo) chega ao onChange pelo evento nativo. */
function shiftPressed(e: ChangeEvent<HTMLInputElement>): boolean {
  return e.nativeEvent instanceof MouseEvent && e.nativeEvent.shiftKey;
}

function Checkbox({
  label,
  checked,
  indeterminate = false,
  disabled = false,
  onChange,
}: {
  label: string;
  checked: boolean;
  indeterminate?: boolean;
  disabled?: boolean;
  onChange: (shift: boolean) => void;
}) {
  return (
    <input
      type="checkbox"
      aria-label={label}
      title={label}
      checked={checked}
      disabled={disabled}
      ref={(el) => {
        if (el) el.indeterminate = indeterminate;
      }}
      onChange={(e) => onChange(shiftPressed(e))}
      className="size-4 cursor-pointer accent-accent-600 disabled:cursor-not-allowed disabled:opacity-30"
    />
  );
}

function RepoRow({ repo: r, live, actions, selection, onXray }: { repo: Repo; live?: RepoProgress["status"]; actions: RowActions | null; selection: Selection | null; onXray: (target: XrayTarget) => void }) {
  const checked = selection?.isSelected(r) ?? false;
  return (
    <tr
      className={cx(
        "group align-top transition-colors hover:bg-stone-50 dark:hover:bg-white/[0.02]",
        checked && "bg-accent-50 hover:bg-accent-50 dark:bg-accent-500/10 dark:hover:bg-accent-500/10",
      )}
    >
      {selection && (
        <td className="w-10 py-3 pl-4">
          <Checkbox
            label={`Selecionar ${repoShortName(r.name, r.org)}`}
            checked={checked}
            disabled={!selection.canSelect(r)}
            onChange={(shift) => selection.toggle(r, shift)}
          />
        </td>
      )}
      <td className="px-4 py-3">
        <div className="min-w-0 max-w-[18rem]">
            <p className="truncate font-medium text-stone-900 dark:text-stone-100" title={r.name}>
              {repoShortName(r.name, r.org)}
              {!r.org && <span className="ml-2 text-xs font-normal text-stone-500 dark:text-stone-400">avulso</span>}
            </p>
            <RepoSummary repo={r} />
            <RepoLinks repo={r} onXray={onXray} />
            {r.skipped > 0 && (
              <p
                className="text-xs text-amber-700 dark:text-amber-300"
                title="Arquivos fora da indexação: acima do limite de tamanho ou binários. Liste com: code-index skips"
              >
                {numberFormat.format(r.skipped)} {plural(r.skipped, "descartado", "descartados")}
              </p>
            )}
            {r.error && <RepoError message={r.error} />}
        </div>
      </td>
      <td className="px-4 py-3">
        <RepoStatus repo={r} live={live} />
      </td>
      <td className="hidden px-4 py-3 @4xl:table-cell">
        <OwnerCell repo={r} onOwn={ownerAction(actions, r)} />
      </td>
      <td className="hidden px-4 py-3 text-stone-600 @[70rem]:table-cell dark:text-stone-300">{r.language ?? "—"}</td>
      <td className="hidden px-4 py-3 text-right tabular-nums @6xl:table-cell">{numberFormat.format(r.files)}</td>
      <td className="px-4 py-3 text-right tabular-nums">{numberFormat.format(r.chunks)}</td>
      <td className="hidden px-4 py-3 font-mono text-xs text-stone-500 @[80rem]:table-cell dark:text-stone-400">{r.commit ?? "—"}</td>
      <td className="hidden px-4 py-3 @3xl:table-cell">
        <span title={formatDateTime(r.indexed_at)} className="block whitespace-nowrap text-stone-600 dark:text-stone-300">
          {timeAgo(r.indexed_at)}
        </span>
        {r.outdated && (
          <span className="mt-0.5 inline-flex items-center gap-1.5 text-xs whitespace-nowrap text-amber-700 dark:text-amber-300">
            <span className="size-1.5 rounded-full bg-amber-500" aria-hidden="true" />
            novo push
          </span>
        )}
      </td>
      {actions && (
        <td
          className={cx(
            "sticky right-0 z-[1] px-4 py-3 shadow-[-8px_0_8px_-8px_rgb(0_0_0/0.12)]",
            checked
              ? "bg-accent-50 group-hover:bg-accent-50 dark:bg-[color-mix(in_oklab,var(--color-accent-500)_10%,var(--color-stone-900))] dark:group-hover:bg-[color-mix(in_oklab,var(--color-accent-500)_10%,var(--color-stone-900))]"
              : "bg-sheet group-hover:bg-stone-50 dark:bg-stone-900 dark:group-hover:bg-[color-mix(in_oklab,white_2%,var(--color-stone-900))]",
          )}
        >
          <RepoActions repo={r} actions={actions} compact />
        </td>
      )}
    </tr>
  );
}

/** Pode abrir o diálogo de domínio deste repo? (mesma regra do botão Domínio das ações.) */
function canEditOwner(actions: RowActions, r: Repo): boolean {
  return actions.canOwn && r.kind === "code" && r.status !== "ignored";
}

/** Ação "definir domínio" do repo, ou undefined quando o usuário não pode editar. */
function ownerAction(actions: RowActions | null, r: Repo): (() => void) | undefined {
  if (!actions || !canEditOwner(actions, r)) return undefined;
  const { onOwner } = actions;
  return () => onOwner(r);
}

/**
 * "sem domínio": chip vazado, com texto (a cor não é o único código). Com `onOwn`, é o botão que abre o
 * diálogo de domínio; sem, é só o rótulo.
 */
function NoOwnerChip({ repoName, onOwn }: { repoName: string; onOwn?: () => void }) {
  const base =
    "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap text-stone-600 ring-1 ring-inset ring-stone-500 dark:text-stone-300";
  if (!onOwn) return <span className={base}>sem domínio</span>;
  return (
    <button
      type="button"
      onClick={onOwn}
      title={`Definir domínio de ${repoName}`}
      className={cx(
        base,
        "cursor-pointer outline-none transition hover:bg-stone-900/5 hover:text-accent-700 focus-visible:ring-2 focus-visible:ring-accent-500 dark:hover:bg-white/10 dark:hover:text-accent-300",
      )}
    >
      sem domínio
    </button>
  );
}

/** Domínio/serviço dono do repo e de onde veio (SDL, irmão, chamadas, uso, manual). */
function OwnerCell({ repo: r, onOwn }: { repo: Repo; onOwn?: () => void }) {
  if (r.owners.length === 0) {
    return <NoOwnerChip repoName={repoShortName(r.name, r.org)} onOwn={onOwn} />;
  }
  const label = r.owners.join(", ");
  return (
    <div className="min-w-0 max-w-[12rem]">
      <p className="truncate font-mono text-xs text-stone-800 dark:text-stone-200" title={label}>
        {label}
      </p>
      {r.owner_origin && (
        <p className="truncate text-xs text-stone-500 dark:text-stone-400" title={ORIGIN_HINT[r.owner_origin]}>
          {ORIGIN_LABEL[r.owner_origin]}
          <ConfidenceNote value={r.owner_confidence} />
        </p>
      )}
    </div>
  );
}

/**
 * Confiança sempre escrita ao lado da origem. Baixa é rosa (pede revisão); média fica neutra.
 * Âmbar fica reservado a "precisa reindexar" nesta interface.
 */
function ConfidenceNote({ value }: { value: string | null }) {
  if (!value) return null;
  const label = CONFIDENCE_VALUES.includes(value as Confidence) ? CONFIDENCE_LABEL[value as Confidence] : value;
  const tone =
    value === "baixa"
      ? "font-medium text-rose-700 dark:text-rose-300"
      : value === "media"
        ? "text-stone-600 dark:text-stone-300"
        : "";
  return <span className={tone}> ({label})</span>;
}

const LIVE_LABEL: Partial<Record<RepoProgress["status"], { label: string; tone: BadgeTone }>> = {
  queued: { label: "Na fila", tone: "accent" },
  fetching: { label: "Baixando", tone: "accent" },
  indexing: { label: "Indexando", tone: "accent" },
};

/**
 * Status do repo: o gravado, ou um selo do job ativo ("na fila", "indexando") enquanto ele roda. O andamento
 * (arquivos, etapa) fica na página Indexação.
 */
function RepoStatus({ repo: r, live }: { repo: Repo; live?: RepoProgress["status"] }) {
  const shown = live ? LIVE_LABEL[live] : undefined;
  return (
    <Badge tone={shown ? shown.tone : r.status} dot>
      {shown ? shown.label : STATUS_LABEL[r.status]}
    </Badge>
  );
}

/** Cartão para telas pequenas: mostra o essencial e as ações com texto. */
function RepoCard({ repo: r, live, actions, selection, onXray }: { repo: Repo; live?: RepoProgress["status"]; actions: RowActions | null; selection: Selection | null; onXray: (target: XrayTarget) => void }) {
  const checked = selection?.isSelected(r) ?? false;
  return (
    <li className={cx("space-y-3 p-4", checked && "bg-accent-50/60 dark:bg-accent-500/10")}>
      <div className="flex items-start justify-between gap-3">
        {selection && (
          <label className="flex size-6 shrink-0 cursor-pointer items-center justify-center">
            <Checkbox
              label={`Selecionar ${repoShortName(r.name, r.org)}`}
              checked={checked}
              disabled={!selection.canSelect(r)}
              onChange={(shift) => selection.toggle(r, shift)}
            />
          </label>
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium text-stone-900 dark:text-stone-100" title={r.name}>
            {repoShortName(r.name, r.org)}
            {!r.org && <span className="ml-2 text-xs font-normal text-stone-500 dark:text-stone-400">avulso</span>}
          </p>
          <RepoSummary repo={r} />
          <RepoLinks repo={r} onXray={onXray} />
          {r.skipped > 0 && (
            <p
              className="text-xs text-amber-700 dark:text-amber-300"
              title="Arquivos fora da indexação: acima do limite de tamanho ou binários. Liste com: code-index skips"
            >
              {numberFormat.format(r.skipped)} {plural(r.skipped, "descartado", "descartados")}
            </p>
          )}
        </div>
        <RepoStatus repo={r} live={live} />
      </div>
      {r.error && <RepoError message={r.error} />}
      <dl className="grid grid-cols-3 gap-2 text-xs">
        <div>
          <dt className="text-stone-500 dark:text-stone-400">Linguagem</dt>
          <dd className="truncate font-medium">{r.language ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-stone-500 dark:text-stone-400">Arquivos</dt>
          <dd className="font-medium tabular-nums">{numberFormat.format(r.files)}</dd>
        </div>
        <div>
          <dt className="text-stone-500 dark:text-stone-400">Trechos</dt>
          <dd className="font-medium tabular-nums">{numberFormat.format(r.chunks)}</dd>
        </div>
      </dl>
      <div className="text-xs">
        <span className="text-stone-500 dark:text-stone-400">Domínio </span>
        {r.owners.length ? (
          <span className="font-mono text-stone-800 dark:text-stone-200">{r.owners.join(", ")}</span>
        ) : (
          <NoOwnerChip repoName={repoShortName(r.name, r.org)} onOwn={ownerAction(actions, r)} />
        )}
        {r.owner_origin && (
          <span className="text-stone-500 dark:text-stone-400">
            {" "}· {ORIGIN_LABEL[r.owner_origin]}
            <ConfidenceNote value={r.owner_confidence} />
          </span>
        )}
      </div>
      <p className="text-xs text-stone-500 dark:text-stone-400">
        Indexado {timeAgo(r.indexed_at)}
        {r.outdated && <> · <span className="text-amber-700 dark:text-amber-300">novo push</span></>}
      </p>
      {actions && <RepoActions repo={r} actions={actions} compact={false} />}
    </li>
  );
}

/** Erro do último sync: uma linha curta; "ver erro" mostra a mensagem inteira, quebrada em linhas. */
function RepoError({ message }: { message: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className="mt-0.5 flex items-baseline justify-between gap-2 text-xs text-rose-600 dark:text-rose-400">
      <p
        id={id}
        title={open ? undefined : message}
        className={cx("min-w-0", open ? "break-words whitespace-pre-wrap" : "truncate")}
      >
        {message}
      </p>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
        className="shrink-0 rounded-md font-medium underline underline-offset-2 outline-none transition hover:text-rose-800 focus-visible:ring-2 focus-visible:ring-accent-500 dark:hover:text-rose-200"
      >
        {open ? "ocultar erro" : "ver erro"}
      </button>
    </div>
  );
}

const repoLinkClass =
  "-my-1 inline-flex items-center gap-1 rounded-md py-1 text-xs font-medium text-stone-500 underline-offset-2 outline-none transition hover:text-accent-600 hover:underline focus-visible:text-accent-600 focus-visible:underline focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:text-stone-400 dark:hover:text-accent-400 dark:focus-visible:ring-offset-stone-900";

/** Atalhos do repo: documentação ligada, cartão e Raio X. */
function RepoLinks({ repo, onXray }: { repo: Repo; onXray: (target: XrayTarget) => void }) {
  if (repo.kind !== "code") return null;
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1">
      <RepoDocsButton repo={repo} />
      <RepoCardButton repo={repo} />
      <RepoSummariesLink repo={repo.name} className={repoLinkClass} />
      <button type="button" aria-label={`Raio X de ${repoShortName(repo.name, repo.org)}`} onClick={() => onXray({ kind: "repo", id: repo.name })} className={repoLinkClass}>
        <Network className="size-3.5" aria-hidden="true" />
        Raio X
      </button>
    </div>
  );
}

/** Resumo do cartão do repo (uma linha; o texto inteiro no title). */
function RepoSummary({ repo }: { repo: Repo }) {
  if (!repo.summary) return null;
  return (
    <p className="mt-0.5 line-clamp-2 max-w-md text-xs text-stone-600 dark:text-stone-300" title={repo.summary}>
      {repo.summary}
    </p>
  );
}

/** Abre o cartão do repo (resumo, capacidades, relacionados, fatos do SDL). */
function RepoCardButton({ repo }: { repo: Repo }) {
  const [open, setOpen] = useState(false);
  if (repo.kind === "docs") return null;
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={repoLinkClass}
      >
        <IdCard className="size-3.5" aria-hidden="true" />
        Cartão
        {repo.card_stale && (
          <span className="font-normal text-stone-600 dark:text-stone-300" title="O repositório mudou desde que o cartão foi escrito">
            desatualizado
          </span>
        )}
      </button>
      {open && <RepoCardDialog repo={repo.name} open onClose={() => setOpen(false)} />}
    </>
  );
}

/** Ações de um repo. Na tabela são ícones (com rótulo acessível); no cartão, botões com texto. */
/** Abre a documentação ligada ao repo (qualquer sessão pode ver). Fontes de docs não têm essa seção. */
function RepoDocsButton({ repo }: { repo: Repo }) {
  const [open, setOpen] = useState(false);
  if (repo.kind === "docs") return null;
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={repoLinkClass}
      >
        <BookOpen className="size-3.5" aria-hidden="true" />
        Documentação
      </button>
      {open && <RepoDocsDialog repo={repo.name} open onClose={() => setOpen(false)} />}
    </>
  );
}

function RepoActions({ repo: r, actions, compact }: { repo: Repo; actions: RowActions; compact: boolean }) {
  const indexable = actions.canIndex && r.status !== "ignored";
  const reactivable = actions.canAdmin && r.status === "ignored";
  const removable = actions.canAdmin && r.status !== "ignored";
  const ownable = canEditOwner(actions, r);
  const removeLabel = r.org ? "Ignorar" : "Remover";

  if (compact) {
    return (
      <div className="flex items-center justify-end gap-0.5">
        {ownable && (
          <IconButton label={`Definir domínio de ${repoShortName(r.name, r.org)}`} onClick={() => actions.onOwner(r)}>
            <Boxes className="size-4" />
          </IconButton>
        )}
        {indexable && (
          <IconButton label={`Indexar ${repoShortName(r.name, r.org)}`} onClick={() => actions.onIndex(r)}>
            <Play className="size-4" />
          </IconButton>
        )}
        {reactivable && (
          <IconButton label={`Reativar ${repoShortName(r.name, r.org)}`} onClick={() => actions.onUnignore(r)}>
            <Undo2 className="size-4" />
          </IconButton>
        )}
        {removable && (
          <IconButton label={`${removeLabel} ${repoShortName(r.name, r.org)}`} onClick={() => actions.onRemove(r)} className="hover:text-rose-600 dark:hover:text-rose-400">
            <Trash2 className="size-4" />
          </IconButton>
        )}
      </div>
    );
  }

  if (!indexable && !reactivable && !removable && !ownable) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {ownable && (
        <Button variant="secondary" size="sm" onClick={() => actions.onOwner(r)}>
          <Boxes className="size-3.5" aria-hidden="true" />
          Domínio
        </Button>
      )}
      {indexable && (
        <Button variant="secondary" size="sm" onClick={() => actions.onIndex(r)}>
          <Play className="size-3.5" aria-hidden="true" />
          Indexar
        </Button>
      )}
      {reactivable && (
        <Button variant="secondary" size="sm" onClick={() => actions.onUnignore(r)}>
          <Undo2 className="size-3.5" aria-hidden="true" />
          Reativar
        </Button>
      )}
      {removable && (
        <Button
          variant="secondary"
          size="sm"
          onClick={() => actions.onRemove(r)}
          className="ml-auto hover:text-rose-700 focus-visible:text-rose-700 dark:hover:text-rose-300 dark:focus-visible:text-rose-300"
        >
          <Trash2 className="size-3.5" aria-hidden="true" />
          {removeLabel}
        </Button>
      )}
    </div>
  );
}

function SkeletonRows({ cols }: { cols: number }) {
  return (
    <>
      {[0, 1, 2, 3, 4].map((i) => (
        <tr key={i}>
          <td className="px-4 py-4" colSpan={cols}>
            <Skeleton className="h-5 w-full" />
          </td>
        </tr>
      ))}
    </>
  );
}

/** Chave de ordenação do domínio: donos em ordem alfabética, juntos; sem dono vira "￿" (fim). */
function ownerSortKey(r: Repo): string {
  return r.owners.length ? [...r.owners].sort().join(", ") : "￿";
}

function compare(a: Repo, b: Repo, key: SortKey): number {
  switch (key) {
    case "status":
      return a.status.localeCompare(b.status);
    case "owner":  // sem dono vai para o fim (na ordem crescente); com v\u00e1rios donos, compara o texto inteiro
      return ownerSortKey(a).localeCompare(ownerSortKey(b));
    case "language":
      return (a.language ?? "").localeCompare(b.language ?? "");
    case "files":
      return a.files - b.files;
    case "chunks":
      return a.chunks - b.chunks;
    case "indexed_at":
      return (Date.parse(a.indexed_at ?? "") || 0) - (Date.parse(b.indexed_at ?? "") || 0);
    default:
      return a.name.localeCompare(b.name);
  }
}
