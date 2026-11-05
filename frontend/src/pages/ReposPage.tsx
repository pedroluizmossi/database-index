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
  RefreshCw,
  Trash2,
  Undo2,
} from "lucide-react";
import type { OrgSummary, OwnerOrigin, OwnershipCatalog, Repo, RepoProgress, RepoStatusValue, SyncJob } from "../api/types";
import { useIndex } from "../data/IndexProvider";
import { useAuth } from "../auth/AuthProvider";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import { canWrite, hasScope } from "../lib/capabilities";
import { useToast } from "../components/Toaster";
import { Dialog } from "../components/Dialog";
import { RepoDocsDialog } from "../components/RepoDocsDialog";
import { RepoCardDialog } from "../components/RepoCardDialog";
import { OwnerDialog } from "../components/OwnerDialog";
import { ORIGIN_HINT, ORIGIN_LABEL, ownerMatches, validOwner } from "../lib/ownership";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { SyncDialog } from "../components/SyncDialog";
import { JobDialog, JobsPanel, RunningJobBanner, useJobs } from "../components/JobsPanel";
import { cx, formatDateTime, plural, repoShortName, timeAgo } from "../lib/utils";
import { Alert, Badge, Button, EmptyState, Field, IconButton, Panel, ProgressBar, Segmented, Select, Skeleton, inputClass, type BadgeTone } from "../components/ui";
import type { XrayTarget } from "../lib/xray";

type StatusFilter = "all" | RepoStatusValue;
type SortKey = "name" | "status" | "owner" | "language" | "files" | "chunks" | "indexed_at";

// Filtro de domínio: todos, sem dono, ou um domínio ('hcm') / serviço ('hcm/payroll').
const ALL_OWNERS = "";
const NO_OWNER = "__sem_dono__";
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

/** Confiança do dono (vem do servidor; 'alta' é o padrão, sem filtro de origem). */
type Confidence = "alta" | "media" | "baixa";
type ConfidenceFilter = "" | Confidence;
const CONFIDENCE_LABEL: Record<Confidence, string> = { alta: "alta", media: "média", baixa: "baixa" };
const CONFIDENCE_VALUES = Object.keys(CONFIDENCE_LABEL) as Confidence[];
const ORIGIN_VALUES = Object.keys(ORIGIN_LABEL) as OwnerOrigin[];
const STATUS_VALUES: StatusFilter[] = ["all", "indexed", "pending", "error", "ignored"];
const SEARCH_MAX = 200;

/**
 * Filtros na URL: `#repos?status=pending&dominio=hcm&origem=sibling&confianca=media&q=texto`.
 * Chaves em português e valores ruins caem no padrão, sem erro.
 */
const HASH_KEYS = { status: "status", domain: "dominio", origin: "origem", confidence: "confianca", search: "q" } as const;

interface Filters {
  status: StatusFilter;
  domain: string;
  origin: OwnerOrigin | "";
  confidence: ConfidenceFilter;
  search: string;
}

const DEFAULT_FILTERS: Filters = { status: "all", domain: ALL_OWNERS, origin: "", confidence: "", search: "" };

function readFiltersFromHash(): Filters {
  const hash = window.location.hash.slice(1);
  const at = hash.indexOf("?");
  if (at < 0) return DEFAULT_FILTERS;
  const params = new URLSearchParams(hash.slice(at + 1));
  const status = params.get(HASH_KEYS.status) as StatusFilter | null;
  const domain = params.get(HASH_KEYS.domain) ?? "";
  const origin = params.get(HASH_KEYS.origin) as OwnerOrigin | null;
  const confidence = params.get(HASH_KEYS.confidence) as Confidence | null;
  return {
    status: status && STATUS_VALUES.includes(status) ? status : "all",
    domain: domain === NO_OWNER || validOwner(domain) ? domain : ALL_OWNERS,
    origin: origin && ORIGIN_VALUES.includes(origin) ? origin : "",
    confidence: confidence && CONFIDENCE_VALUES.includes(confidence) ? confidence : "",
    search: (params.get(HASH_KEYS.search) ?? "").slice(0, SEARCH_MAX),
  };
}

/** Escreve os filtros no hash sem criar entrada no histórico; mantém o caminho e a busca da página. */
function writeFiltersToHash(f: Filters) {
  const params = new URLSearchParams();
  if (f.status !== "all") params.set(HASH_KEYS.status, f.status);
  if (f.domain !== ALL_OWNERS) params.set(HASH_KEYS.domain, f.domain);
  if (f.origin) params.set(HASH_KEYS.origin, f.origin);
  if (f.confidence) params.set(HASH_KEYS.confidence, f.confidence);
  if (f.search.trim()) params.set(HASH_KEYS.search, f.search);
  const query = params.toString();
  const hash = query ? `#repos?${query}` : "#repos";
  if (window.location.hash === hash) return;
  try {
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}${hash}`);
  } catch {
    // Sem histórico gravável (ambiente restrito): os filtros continuam valendo na tela.
  }
}

/** Algum filtro difere do padrão? */
function isFilterActive(f: Filters): boolean {
  return f.status !== "all" || f.domain !== ALL_OWNERS || f.origin !== "" || f.confidence !== "" || f.search.trim() !== "";
}

const numberFormat = new Intl.NumberFormat("pt-BR");

export function ReposPage({ onXray }: { onXray: (target: XrayTarget) => void }) {
  const { repos, orgs, loading, reload } = useIndex();
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
  // Jobs: leitura para quem tem index:write (vale também para API key com esse scope).
  const canSeeJobs = me !== null && hasScope(me, "index:write");
  const jobs = useJobs(canSeeJobs, onFinished);
  const [jobDetail, setJobDetail] = useState<SyncJob | null>(null);
  // Andamento ao vivo do job: o status gravado do repo só muda quando o lote inteiro termina.
  const live = useMemo(
    () => new Map((jobs.running?.progress?.repos ?? []).map((p) => [p.name, p] as const)),
    [jobs.running],
  );

  async function unignore(r: Repo) {
    try {
      await api.unignoreRepo(r.name);
      push({ tone: "success", title: "Repositório devolvido à fila", description: r.name });
      await reload();
    } catch (err) {
      push({ tone: "error", title: "Não consegui reativar", description: describeError(err) });
    }
  }

  async function refreshOrg(org: OrgSummary) {
    try {
      const res = await api.refreshOrg(org.name);
      const d = res[org.name];
      push({
        tone: "success",
        title: `Descoberta de ${org.name} atualizada`,
        description: d ? `${d.total} repositórios · ${d.new} ${plural(d.new, "novo", "novos")} · ${d.removed} ${plural(d.removed, "removido", "removidos")}` : undefined,
      });
      await reload();
    } catch (err) {
      push({ tone: "error", title: "Não consegui atualizar a org", description: describeError(err) });
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
  const [term, setTerm] = useState(initial.search);
  const [sort, setSort] = useState<Sort>({ key: "name", dir: 1 });
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<PageSize>(25);
  const listTop = useRef<HTMLDivElement>(null);

  const counts = useMemo(() => {
    const byStatus: Record<RepoStatusValue, number> = { indexed: 0, pending: 0, error: 0, ignored: 0 };
    let files = 0;
    let chunks = 0;
    for (const r of repos ?? []) {
      byStatus[r.status] += 1;
      files += r.files;
      chunks += r.chunks;
    }
    return { total: repos?.length ?? 0, byStatus, files, chunks };
  }, [repos]);

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
      .filter(
        (r) =>
          !needle ||
          r.name.toLowerCase().includes(needle) ||
          (r.language ?? "").toLowerCase().includes(needle) ||
          (r.summary ?? "").toLowerCase().includes(needle) ||
          r.tags.some((t) => t.toLowerCase().includes(needle)),
      )
      .sort((a, b) => compare(a, b, sort.key) * sort.dir || a.name.localeCompare(b.name));
  }, [repos, status, term, sort, ownerFilter, originFilter, confidenceFilter]);

  // Paginação: filtro, busca, ordenação ou tamanho da página voltam para a primeira página.
  useEffect(() => setPage(0), [status, term, sort, pageSize, ownerFilter, originFilter, confidenceFilter]);
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const current = Math.min(page, pageCount - 1);
  const visible = rows.slice(current * pageSize, (current + 1) * pageSize);

  // Filtros na URL (sem entrada nova no histórico).
  const filters: Filters = { status, domain: ownerFilter, origin: originFilter, confidence: confidenceFilter, search: term };
  useEffect(() => writeFiltersToHash(filters), [status, ownerFilter, originFilter, confidenceFilter, term]);

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
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
            Indexação e domínio (DDD) de cada repositório.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" size="sm" loading={loading} onClick={() => void reload()}>
            Atualizar
          </Button>
          {canIndex && (
            <Button
              size="sm"
              onClick={() => setSyncOpen({ names: null })}
              disabled={jobs.running !== null}
              title={jobs.running ? "Já existe uma indexação em andamento" : undefined}
            >
              <Play className="size-3.5" aria-hidden="true" />
              Indexar lote
            </Button>
          )}
          {isAdmin && (
            <Button variant="secondary" size="sm" onClick={() => setAddOpen(true)}>
              <Plus className="size-3.5" aria-hidden="true" />
              Adicionar repositório
            </Button>
          )}
        </div>
      </div>

      {jobs.running && <RunningJobBanner job={jobs.running} onOpen={setJobDetail} />}

      <Coverage
        counts={counts}
        orgs={orgs ?? []}
        outdated={(repos ?? []).filter((r) => r.outdated).length}
        loading={repos === null && loading}
        onFilter={(value) => {
          setStatus(value);
          listTop.current?.scrollIntoView({ block: "start", behavior: "smooth" });
        }}
        onRefresh={isAdmin && writable ? (org) => void refreshOrg(org) : undefined}
        ownership={catalog?.coverage ?? null}
        originFilter={originFilter}
        onOrigin={(o) => {
          setOriginFilter((cur) => (cur === o ? "" : o));
          if (originFilter !== o) listTop.current?.scrollIntoView({ block: "start", behavior: "smooth" });
        }}
        onShowUnowned={() => {
          // Sem dono: status, origem e confiança antigos não podem esconder a fila inteira.
          setStatus("all");
          setOriginFilter("");
          setConfidenceFilter("");
          setOwnerFilter(NO_OWNER);
          listTop.current?.scrollIntoView({ block: "start", behavior: "smooth" });
        }}
      />

      <div ref={listTop} className="scroll-mt-20" />
      <Panel className="overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-zinc-200 p-4 sm:flex-row sm:items-center sm:justify-between dark:border-zinc-800">
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
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-zinc-400" aria-hidden="true" />
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
              else setTerm("");
            }}
            onClearAll={() => {
              setStatus("all");
              setOwnerFilter(ALL_OWNERS);
              setOriginFilter("");
              setConfidenceFilter("");
              setTerm("");
            }}
          />
        )}

        <div className="hidden overflow-x-auto md:block">
          <table className="w-full text-sm">
            <thead className="border-b border-zinc-200 bg-zinc-50 text-left text-xs text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900/60 dark:text-zinc-400">
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
                <SortHeader label="Domínio" sortKey="owner" sort={sort} onSort={toggleSort} className="hidden lg:table-cell" />
                <SortHeader label="Linguagem" sortKey="language" sort={sort} onSort={toggleSort} className="hidden xl:table-cell" />
                <SortHeader label="Arquivos" sortKey="files" sort={sort} onSort={toggleSort} align="right" className="hidden 2xl:table-cell" />
                <SortHeader label="Trechos" sortKey="chunks" sort={sort} onSort={toggleSort} align="right" />
                <th className="hidden px-4 py-3 font-medium 2xl:table-cell">Commit</th>
                <SortHeader label="Indexado" sortKey="indexed_at" sort={sort} onSort={toggleSort} className="hidden lg:table-cell" />
                {rowActions && (
                  <th className="sticky right-0 z-[2] bg-zinc-50 px-4 py-3 text-right font-medium shadow-[-8px_0_8px_-8px_rgb(0_0_0/0.12)] dark:bg-zinc-900">
                    Ações
                  </th>
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
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

        <ul className="divide-y divide-zinc-100 md:hidden dark:divide-zinc-800">
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

      {canSeeJobs && <JobsPanel state={jobs} onOpen={setJobDetail} />}

      <JobDialog job={jobDetail} onClose={() => setJobDetail(null)} />
      {barVisible && (
        <div className="pointer-events-none fixed inset-x-0 bottom-4 z-40 flex justify-center px-4">
          <div
            ref={selectionBar}
            className="pointer-events-auto relative flex max-w-full items-center gap-2 rounded-xl bg-ink-950 py-2 pr-2 pl-3 text-white shadow-[0_8px_24px_-8px_rgb(0_0_0/0.45)] ring-1 ring-white/10 animate-pop sm:gap-3 sm:pl-4"
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
                title={jobs.running ? "Já existe uma indexação em andamento" : undefined}
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
            Remover <span className="font-mono">{removeTarget?.name}</span> do repos.toml e apagar o clone local.
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

type FilterKey = "status" | "domain" | "origin" | "confidence" | "search";

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
  if (filters.search.trim()) chips.push({ key: "search", label: `Busca: "${filters.search.trim()}"` });

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-zinc-200 px-4 py-2.5 text-xs dark:border-zinc-800">
      <span className="text-zinc-500 dark:text-zinc-400">Filtros ativos:</span>
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
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-zinc-200 px-4 py-3 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
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

/** Ordem e cor de cada status na barra de cobertura. */
const COVERAGE: { status: RepoStatusValue; label: string; bar: string; key: string }[] = [
  { status: "indexed", label: "Indexados", bar: "bg-emerald-500", key: "bg-emerald-500" },
  { status: "error", label: "Com erro", bar: "bg-rose-500", key: "bg-rose-500" },
  { status: "pending", label: "Pendentes", bar: "bg-zinc-300 dark:bg-zinc-600", key: "border-[1.5px] border-zinc-500 dark:border-zinc-500" },
  { status: "ignored", label: "Ignorados", bar: "bg-zinc-200 dark:bg-zinc-800", key: "bg-zinc-300 dark:bg-zinc-600" },
];

interface Counts {
  total: number;
  byStatus: Record<RepoStatusValue, number>;
  files: number;
  chunks: number;
}

function percentOf(part: number, total: number): number {
  return total > 0 ? Math.round((part / total) * 100) : 0;
}

/** Barra empilhada por status. A cor nunca é o único código: a legenda traz rótulo e número. */
function CoverageBar({ byStatus, total, className }: { byStatus: Record<RepoStatusValue, number>; total: number; className?: string }) {
  return (
    <div className={cx("flex h-2.5 gap-px overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800", className)} aria-hidden="true">
      {COVERAGE.map(({ status, bar }) =>
        byStatus[status] > 0 && total > 0 ? (
          <span key={status} className={cx("h-full transition-[width] duration-500", bar)} style={{ width: `${(byStatus[status] / total) * 100}%` }} />
        ) : null,
      )}
    </div>
  );
}

/** Resumo do índice: quanto já foi indexado, por status e por org. Substitui cards soltos. */
function Coverage({
  counts,
  orgs,
  outdated,
  loading,
  onFilter,
  onRefresh,
  ownership,
  originFilter,
  onOrigin,
  onShowUnowned,
}: {
  counts: Counts;
  orgs: OrgSummary[];
  outdated: number;
  loading: boolean;
  onFilter: (status: StatusFilter) => void;
  onRefresh?: (org: OrgSummary) => void;
  ownership: OwnershipCatalog["coverage"] | null;
  originFilter: OwnerOrigin | "";
  onOrigin: (origin: OwnerOrigin) => void;
  onShowUnowned: () => void;
}) {
  if (loading) return <Skeleton className="h-40 w-full rounded-xl" />;
  const single = orgs.length === 1 && orgs[0].total === counts.total ? orgs[0] : null;
  const languages = single ? Object.entries(single.languages).slice(0, 3) : [];
  const percent = percentOf(counts.byStatus.indexed, counts.total);

  return (
    <Panel className="p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <h2 className="truncate font-semibold">{single ? single.name : "Cobertura do índice"}</h2>
          <p className="mt-0.5 text-sm text-zinc-500 dark:text-zinc-400">
            {numberFormat.format(counts.total)} {plural(counts.total, "repositório", "repositórios")}
            {languages.length > 0 && <> · {languages.map(([language, n]) => `${language} ${n}`).join(" · ")}</>}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {single && onRefresh && (
            <Button variant="ghost" size="sm" onClick={() => onRefresh(single)}>
              <RefreshCw className="size-3.5" aria-hidden="true" />
              Atualizar descoberta
            </Button>
          )}
        </div>
      </div>

      <div className="mt-6 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <p className="text-sm text-zinc-600 dark:text-zinc-300">
          <span className="text-2xl font-semibold tracking-tight text-zinc-900 tabular-nums dark:text-white">{percent}%</span>
          <span className="ml-2">indexado</span>
          <span className="text-zinc-500 dark:text-zinc-400">
            {" "}· {numberFormat.format(counts.byStatus.indexed)} de {numberFormat.format(counts.total)}
          </span>
        </p>
        <p className="text-sm text-zinc-500 tabular-nums dark:text-zinc-400">
          {numberFormat.format(counts.chunks)} {plural(counts.chunks, "trecho", "trechos")} em {numberFormat.format(counts.files)} {plural(counts.files, "arquivo", "arquivos")}
        </p>
      </div>

      <CoverageBar byStatus={counts.byStatus} total={counts.total} className="mt-3" />

      <ul className="mt-3 flex flex-wrap gap-x-1 gap-y-1 text-sm" aria-label="Repositórios por status">
        {COVERAGE.map(({ status, label, key }) => (
          <li key={status}>
            <button
              type="button"
              onClick={() => onFilter(status)}
              title={`Mostrar só ${label.toLowerCase()}`}
              className="inline-flex items-center gap-2 rounded-md px-2 py-1 text-zinc-600 outline-none transition hover:bg-zinc-900/5 hover:text-zinc-900 focus-visible:ring-2 focus-visible:ring-accent-500 dark:text-zinc-300 dark:hover:bg-white/5 dark:hover:text-white"
            >
              <span className={cx("size-2 rounded-full", key)} aria-hidden="true" />
              {label}
              <span className="font-medium text-zinc-900 tabular-nums dark:text-white">{numberFormat.format(counts.byStatus[status])}</span>
            </button>
          </li>
        ))}
        {outdated > 0 && (
          <li className="inline-flex items-center gap-2 px-2 py-1 text-amber-700 dark:text-amber-300">
            <span className="size-2 rounded-full bg-amber-500" aria-hidden="true" />
            {numberFormat.format(outdated)} com push novo
          </li>
        )}
      </ul>

      {ownership && ownership.repos > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-zinc-100 pt-4 text-sm dark:border-zinc-800">
          <span className="text-zinc-600 dark:text-zinc-300">
            <span className="font-medium text-zinc-900 tabular-nums dark:text-white">{ownership.percent}%</span> com
            domínio/serviço
            <span className="text-zinc-500 dark:text-zinc-400">
              {" "}· {numberFormat.format(ownership.owned)} de {numberFormat.format(ownership.repos)}
            </span>
          </span>
          <ul className="flex flex-wrap items-center gap-x-0.5 gap-y-1 text-xs" aria-label="Repositórios por origem do domínio">
            {ORIGIN_VALUES.filter((o) => (ownership.by_origin[o] ?? 0) > 0).map((o) => {
              const pressed = originFilter === o;
              return (
                <li key={o}>
                  <button
                    type="button"
                    aria-pressed={pressed}
                    onClick={() => onOrigin(o)}
                    title={`${ORIGIN_HINT[o]}. ${pressed ? "Clique para tirar o filtro." : "Clique para ver só estes."}`}
                    className={cx(
                      "inline-flex items-center gap-1.5 rounded-md px-2 py-1 outline-none transition focus-visible:ring-2 focus-visible:ring-accent-500",
                      pressed
                        ? "bg-accent-50 font-medium text-accent-700 dark:bg-accent-500/10 dark:text-accent-300"
                        : "text-zinc-600 hover:bg-zinc-900/5 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-white/5 dark:hover:text-white",
                    )}
                  >
                    {ORIGIN_LABEL[o]}
                    <span className="font-medium text-zinc-900 tabular-nums dark:text-white">{numberFormat.format(ownership.by_origin[o] ?? 0)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
          {ownership.repos > ownership.owned && (
            <button
              type="button"
              onClick={onShowUnowned}
              className="ml-auto rounded-md px-2 py-1 text-sm font-medium text-accent-700 outline-none transition hover:bg-accent-50 focus-visible:ring-2 focus-visible:ring-accent-500 dark:text-accent-300 dark:hover:bg-accent-500/10"
            >
              Ver {numberFormat.format(ownership.repos - ownership.owned)} sem domínio
            </button>
          )}
        </div>
      )}

      {!single && orgs.length > 0 && (
        <ul className="mt-5 divide-y divide-zinc-100 border-t border-zinc-100 dark:divide-zinc-800 dark:border-zinc-800">
          {orgs.map((org) => (
            <li key={org.name} className="grid grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-4 py-3 sm:grid-cols-[minmax(0,14rem)_1fr_auto_auto]">
              <p className="truncate text-sm font-medium" title={org.name}>{org.name}</p>
              <CoverageBar byStatus={org.status} total={org.total} className="h-1.5" />
              <p className="text-sm text-zinc-500 tabular-nums dark:text-zinc-400">
                {percentOf(org.status.indexed, org.total)}% de {org.total}
              </p>
              {onRefresh && (
                <IconButton label={`Atualizar descoberta de ${org.name}`} onClick={() => onRefresh(org)} className="hidden sm:inline-flex">
                  <RefreshCw className="size-4" />
                </IconButton>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
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
          "inline-flex items-center gap-1 rounded-md outline-none transition hover:text-zinc-900 focus-visible:ring-2 focus-visible:ring-accent-500 dark:hover:text-zinc-100",
          active && "text-zinc-900 dark:text-zinc-100",
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

function RepoRow({ repo: r, live, actions, selection, onXray }: { repo: Repo; live?: RepoProgress; actions: RowActions | null; selection: Selection | null; onXray: (target: XrayTarget) => void }) {
  const checked = selection?.isSelected(r) ?? false;
  return (
    <tr
      className={cx(
        "group align-top transition-colors hover:bg-zinc-50 dark:hover:bg-white/[0.02]",
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
            <p className="truncate font-medium text-zinc-900 dark:text-zinc-100" title={r.name}>
              {repoShortName(r.name, r.org)}
              {!r.org && <span className="ml-2 text-xs font-normal text-zinc-500 dark:text-zinc-400">avulso</span>}
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
      <td className="hidden px-4 py-3 lg:table-cell">
        <OwnerCell repo={r} onOwn={ownerAction(actions, r)} />
      </td>
      <td className="hidden px-4 py-3 text-zinc-600 xl:table-cell dark:text-zinc-300">{r.language ?? "—"}</td>
      <td className="hidden px-4 py-3 text-right tabular-nums 2xl:table-cell">{numberFormat.format(r.files)}</td>
      <td className="px-4 py-3 text-right tabular-nums">{numberFormat.format(r.chunks)}</td>
      <td className="hidden px-4 py-3 font-mono text-xs text-zinc-500 2xl:table-cell dark:text-zinc-400">{r.commit ?? "—"}</td>
      <td className="hidden px-4 py-3 lg:table-cell">
        <span title={formatDateTime(r.indexed_at)} className="block whitespace-nowrap text-zinc-600 dark:text-zinc-300">
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
              ? "bg-accent-50 group-hover:bg-accent-50 dark:bg-[color-mix(in_oklab,var(--color-accent-500)_10%,var(--color-zinc-900))] dark:group-hover:bg-[color-mix(in_oklab,var(--color-accent-500)_10%,var(--color-zinc-900))]"
              : "bg-white group-hover:bg-zinc-50 dark:bg-zinc-900 dark:group-hover:bg-[color-mix(in_oklab,white_2%,var(--color-zinc-900))]",
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
    "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap text-zinc-600 ring-1 ring-inset ring-zinc-500 dark:text-zinc-300";
  if (!onOwn) return <span className={base}>sem domínio</span>;
  return (
    <button
      type="button"
      onClick={onOwn}
      title={`Definir domínio de ${repoName}`}
      className={cx(
        base,
        "cursor-pointer outline-none transition hover:bg-zinc-900/5 hover:text-accent-700 focus-visible:ring-2 focus-visible:ring-accent-500 dark:hover:bg-white/10 dark:hover:text-accent-300",
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
      <p className="truncate font-mono text-xs text-zinc-800 dark:text-zinc-200" title={label}>
        {label}
      </p>
      {r.owner_origin && (
        <p className="truncate text-xs text-zinc-500 dark:text-zinc-400" title={ORIGIN_HINT[r.owner_origin]}>
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
        ? "text-zinc-600 dark:text-zinc-300"
        : "";
  return <span className={tone}> ({label})</span>;
}

const LIVE_LABEL: Partial<Record<RepoProgress["status"], { label: string; tone: BadgeTone }>> = {
  queued: { label: "Na fila", tone: "accent" },
  fetching: { label: "Baixando", tone: "accent" },
  indexing: { label: "Indexando", tone: "accent" },
  done: { label: "Pronto", tone: "indexed" },
  error: { label: "Erro", tone: "error" },
};

/** Status do repo: o gravado, ou o andamento do job em curso (com barra de arquivos) enquanto ele roda. */
function RepoStatus({ repo: r, live }: { repo: Repo; live?: RepoProgress }) {
  const shown = live ? LIVE_LABEL[live.status] : undefined;
  if (!live || !shown) {
    return (
      <Badge tone={r.status} dot>
        {STATUS_LABEL[r.status]}
      </Badge>
    );
  }
  return (
    <div className="w-32 space-y-1.5">
      <Badge tone={shown.tone} dot>
        {shown.label}
      </Badge>
      {live.status === "indexing" && live.files_total !== null && live.files_total > 0 && (
        <>
          <ProgressBar value={live.files_done} max={live.files_total} label={`Arquivos de ${repoShortName(r.name, r.org)}`} />
          <p className="text-xs text-zinc-500 tabular-nums dark:text-zinc-400">
            {numberFormat.format(live.files_done)}/{numberFormat.format(live.files_total)} arquivos
          </p>
        </>
      )}
    </div>
  );
}

/** Cartão para telas pequenas: mostra o essencial e as ações com texto. */
function RepoCard({ repo: r, live, actions, selection, onXray }: { repo: Repo; live?: RepoProgress; actions: RowActions | null; selection: Selection | null; onXray: (target: XrayTarget) => void }) {
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
          <p className="truncate font-medium text-zinc-900 dark:text-zinc-100" title={r.name}>
            {repoShortName(r.name, r.org)}
            {!r.org && <span className="ml-2 text-xs font-normal text-zinc-500 dark:text-zinc-400">avulso</span>}
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
          <dt className="text-zinc-500 dark:text-zinc-400">Linguagem</dt>
          <dd className="truncate font-medium">{r.language ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-zinc-500 dark:text-zinc-400">Arquivos</dt>
          <dd className="font-medium tabular-nums">{numberFormat.format(r.files)}</dd>
        </div>
        <div>
          <dt className="text-zinc-500 dark:text-zinc-400">Trechos</dt>
          <dd className="font-medium tabular-nums">{numberFormat.format(r.chunks)}</dd>
        </div>
      </dl>
      <div className="text-xs">
        <span className="text-zinc-500 dark:text-zinc-400">Domínio </span>
        {r.owners.length ? (
          <span className="font-mono text-zinc-800 dark:text-zinc-200">{r.owners.join(", ")}</span>
        ) : (
          <NoOwnerChip repoName={repoShortName(r.name, r.org)} onOwn={ownerAction(actions, r)} />
        )}
        {r.owner_origin && (
          <span className="text-zinc-500 dark:text-zinc-400">
            {" "}· {ORIGIN_LABEL[r.owner_origin]}
            <ConfidenceNote value={r.owner_confidence} />
          </span>
        )}
      </div>
      <p className="text-xs text-zinc-500 dark:text-zinc-400">
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
  "-my-1 inline-flex items-center gap-1 rounded-md py-1 text-xs font-medium text-zinc-500 underline-offset-2 outline-none transition hover:text-accent-600 hover:underline focus-visible:text-accent-600 focus-visible:underline focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:text-zinc-400 dark:hover:text-accent-400 dark:focus-visible:ring-offset-zinc-900";

/** Atalhos do repo: documentação ligada, cartão e Raio X. */
function RepoLinks({ repo, onXray }: { repo: Repo; onXray: (target: XrayTarget) => void }) {
  if (repo.kind !== "code") return null;
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1">
      <RepoDocsButton repo={repo} />
      <RepoCardButton repo={repo} />
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
    <p className="mt-0.5 line-clamp-2 max-w-md text-xs text-zinc-600 dark:text-zinc-300" title={repo.summary}>
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
          <span className="font-normal text-zinc-600 dark:text-zinc-300" title="O repositório mudou desde que o cartão foi escrito">
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
