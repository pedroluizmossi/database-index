import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  CircleCheck,
  X,
  Clock,
  Database,
  GitBranch,
  Layers,
  Search,
  ServerCrash,
  ChevronLeft,
  ChevronRight,
  Play,
  Plus,
  RefreshCw,
  Trash2,
  Undo2,
  type LucideIcon,
} from "lucide-react";
import type { OrgSummary, Repo, RepoStatusValue, SyncJob } from "../api/types";
import { useIndex } from "../data/IndexProvider";
import { useAuth } from "../auth/AuthProvider";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import { canWrite, hasScope } from "../lib/capabilities";
import { useToast } from "../components/Toaster";
import { Dialog } from "../components/Dialog";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { SyncDialog } from "../components/SyncDialog";
import { JobDialog, JobsPanel, RunningJobBanner, useJobs } from "../components/JobsPanel";
import { formatDateTime, repoShortName, timeAgo, cx } from "../lib/utils";
import { Alert, Badge, Button, EmptyState, Field, IconButton, Panel, Segmented, Select, Skeleton, inputClass } from "../components/ui";

type StatusFilter = "all" | RepoStatusValue;
type SortKey = "name" | "status" | "language" | "files" | "chunks" | "indexed_at";
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

export function ReposPage() {
  const { repos, orgs, loading, reload } = useIndex();
  const { session } = useAuth();
  const { push } = useToast();
  const [syncOpen, setSyncOpen] = useState<{ names: string[] | null } | null>(null);
  // Seleção múltipla para indexar vários repos de uma vez (persiste entre páginas).
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
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

  async function unignore(r: Repo) {
    try {
      await api.unignoreRepo(r.name);
      push({ tone: "success", title: "Repo devolvido à fila", description: r.name });
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
        description: d ? `${d.total} repos · ${d.new} novo(s) · ${d.removed} removido(s)` : undefined,
      });
      await reload();
    } catch (err) {
      push({ tone: "error", title: "Não consegui atualizar a org", description: describeError(err) });
    }
  }

  const rowActions: RowActions | null = writable
    ? {
        canIndex,
        canAdmin: isAdmin,
        onIndex: (r) => setSyncOpen({ names: [r.name] }),
        onRemove: (r) => setRemoveTarget(r),
        onUnignore: (r) => void unignore(r),
      }
    : null;
  const [status, setStatus] = useState<StatusFilter>("all");
  const [term, setTerm] = useState("");
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
      .filter((r) => !needle || r.name.toLowerCase().includes(needle) || (r.language ?? "").toLowerCase().includes(needle))
      .sort((a, b) => compare(a, b, sort.key) * sort.dir);
  }, [repos, status, term, sort]);

  // Paginação: filtro, busca, ordenação ou tamanho da página voltam para a primeira página.
  useEffect(() => setPage(0), [status, term, sort, pageSize]);
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const current = Math.min(page, pageCount - 1);
  const visible = rows.slice(current * pageSize, (current + 1) * pageSize);

  const isSelectable = (r: Repo) => canIndex && r.status !== "ignored";
  const selectableOnPage = visible.filter(isSelectable);
  const pageAllSelected = selectableOnPage.length > 0 && selectableOnPage.every((r) => selected.has(r.name));
  const pageSomeSelected = selectableOnPage.some((r) => selected.has(r.name));
  const selection: Selection | null = canIndex
    ? {
        isSelected: (r) => selected.has(r.name),
        canSelect: isSelectable,
        toggle: (r) =>
          setSelected((cur) => {
            const next = new Set(cur);
            if (next.has(r.name)) next.delete(r.name);
            else next.add(r.name);
            return next;
          }),
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

  const cols = 7 + (rowActions ? 1 : 0) + (selection ? 1 : 0);

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

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Repositórios</h1>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
            Estado da indexação por organização e repositório.
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
              Adicionar repo
            </Button>
          )}
        </div>
      </div>

      {jobs.running && <RunningJobBanner job={jobs.running} onOpen={setJobDetail} />}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard icon={CircleCheck} label="Indexados" value={numberFormat.format(counts.byStatus.indexed)} hint={`de ${numberFormat.format(counts.total)} repositórios`} tone="bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" />
        <StatCard icon={Clock} label="Pendentes" value={numberFormat.format(counts.byStatus.pending)} hint="aguardam a próxima indexação" tone="bg-sky-500/10 text-sky-600 dark:text-sky-400" />
        <StatCard icon={ServerCrash} label="Com erro" value={numberFormat.format(counts.byStatus.error)} hint="veja a observação na tabela" tone="bg-rose-500/10 text-rose-600 dark:text-rose-400" />
        <StatCard icon={Layers} label="Chunks" value={numberFormat.format(counts.chunks)} hint={`${numberFormat.format(counts.files)} arquivos`} tone="bg-indigo-500/10 text-indigo-600 dark:text-indigo-400" />
      </div>

      {orgs && orgs.length > 0 && (
        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {orgs.map((org) => (
            <OrgCard key={org.name} org={org} onRefresh={isAdmin && writable ? () => void refreshOrg(org) : undefined} />
          ))}
        </section>
      )}

      <div ref={listTop} className="scroll-mt-20" />
      <Panel className="overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-zinc-200 p-4 sm:flex-row sm:items-center sm:justify-between dark:border-zinc-800">
          <Segmented label="Filtrar por status" value={status} onChange={setStatus} options={statusOptions} />
          <div className="relative w-full sm:w-72">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-zinc-400" aria-hidden="true" />
            <input
              value={term}
              onChange={(e) => setTerm(e.target.value)}
              placeholder="Buscar por nome ou linguagem"
              aria-label="Buscar repositórios"
              className={cx(inputClass, "pl-9")}
            />
          </div>
        </div>

        <div className="hidden overflow-x-auto md:block">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50/80 text-left text-xs tracking-wide text-zinc-500 uppercase dark:bg-zinc-900 dark:text-zinc-400">
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
                <SortHeader label="Linguagem" sortKey="language" sort={sort} onSort={toggleSort} className="hidden lg:table-cell" />
                <SortHeader label="Arquivos" sortKey="files" sort={sort} onSort={toggleSort} align="right" className="hidden xl:table-cell" />
                <SortHeader label="Chunks" sortKey="chunks" sort={sort} onSort={toggleSort} align="right" />
                <th className="hidden px-4 py-3 font-medium 2xl:table-cell">Commit</th>
                <SortHeader label="Indexado" sortKey="indexed_at" sort={sort} onSort={toggleSort} className="hidden lg:table-cell" />
                {rowActions && <th className="px-4 py-3 text-right font-medium">Ações</th>}
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
                <RepoRow key={r.name} repo={r} actions={rowActions} selection={selection} />
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
            <RepoCard key={r.name} repo={r} actions={rowActions} selection={selection} />
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
      {selection && selected.size > 0 && (
        <div className="pointer-events-none fixed inset-x-0 bottom-4 z-40 flex justify-center px-4">
          <div className="pointer-events-auto flex flex-wrap items-center gap-3 rounded-2xl border border-zinc-200 bg-white/95 px-4 py-3 shadow-xl shadow-zinc-900/10 backdrop-blur dark:border-zinc-700 dark:bg-zinc-900/95">
            <p className="text-sm">
              <span className="font-semibold tabular-nums">{selected.size}</span> repo(s) selecionado(s)
            </p>
            <Button
              size="sm"
              onClick={() => setSyncOpen({ names: [...selected].sort() })}
              disabled={jobs.running !== null}
              title={jobs.running ? "Já existe uma indexação em andamento" : undefined}
            >
              <Play className="size-3.5" aria-hidden="true" />
              Indexar selecionados
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
              <X className="size-3.5" aria-hidden="true" />
              Limpar
            </Button>
          </div>
        </div>
      )}
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
            title: res.result === "ignored" ? "Repo ignorado" : "Repo removido",
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
      push({ tone: "success", title: "Repo adicionado", description: `${created.name}: indexe quando quiser.` });
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
          <IconButton label="Página anterior" onClick={() => onPage(page - 1)} className={page === 0 ? "pointer-events-none opacity-40" : undefined}>
            <ChevronLeft className="size-4" />
          </IconButton>
          <span className="min-w-20 text-center tabular-nums" aria-live="polite">
            Página {page + 1} de {pageCount}
          </span>
          <IconButton
            label="Próxima página"
            onClick={() => onPage(page + 1)}
            className={page >= pageCount - 1 ? "pointer-events-none opacity-40" : undefined}
          >
            <ChevronRight className="size-4" />
          </IconButton>
        </nav>
      </div>
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  hint,
  tone,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  hint: string;
  tone: string;
}) {
  return (
    <Panel className="flex items-center gap-4 p-5">
      <div className={cx("flex size-11 shrink-0 items-center justify-center rounded-xl", tone)}>
        <Icon className="size-5" aria-hidden="true" />
      </div>
      <div className="min-w-0">
        <p className="text-xs font-medium tracking-wide text-zinc-500 uppercase dark:text-zinc-400">{label}</p>
        <p className="text-2xl leading-tight font-semibold tabular-nums">{value}</p>
        <p className="truncate text-xs text-zinc-500 dark:text-zinc-400">{hint}</p>
      </div>
    </Panel>
  );
}

function OrgCard({ org, onRefresh }: { org: OrgSummary; onRefresh?: () => void }) {
  const indexed = org.status.indexed;
  const percent = org.total > 0 ? Math.round((indexed / org.total) * 100) : 0;
  const topLanguages = Object.entries(org.languages).slice(0, 3);

  return (
    <Panel className="p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-semibold">{org.name}</p>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">{org.total} repositório(s)</p>
        </div>
        <span className="text-2xl font-semibold tabular-nums">{percent}%</span>
      </div>

      <div className="mt-4 h-2 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
        <div
          className="h-full rounded-full bg-linear-to-r from-indigo-500 to-violet-500 transition-all duration-500"
          style={{ width: `${percent}%` }}
        />
      </div>

      <div className="mt-4 flex flex-wrap gap-1.5">
        <Badge tone="indexed" dot>
          {indexed} indexado(s)
        </Badge>
        <Badge tone="pending" dot>
          {org.status.pending} pendente(s)
        </Badge>
        {org.status.error > 0 && (
          <Badge tone="error" dot>
            {org.status.error} com erro
          </Badge>
        )}
        {org.outdated > 0 && <Badge tone="warning">{org.outdated} desatualizado(s)</Badge>}
      </div>

      {onRefresh && (
        <Button variant="ghost" size="sm" className="mt-3 -ml-2.5" onClick={onRefresh}>
          <RefreshCw className="size-3.5" aria-hidden="true" />
          Atualizar descoberta
        </Button>
      )}

      {topLanguages.length > 0 && (
        <p className="mt-3 truncate text-xs text-zinc-500 dark:text-zinc-400">
          {topLanguages.map(([language, n]) => `${language} ${n}`).join(" · ")}
        </p>
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
  return (
    <th className={cx("px-4 py-3 font-medium", align === "right" && "text-right", className)}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cx(
          "inline-flex items-center gap-1 transition hover:text-zinc-900 dark:hover:text-zinc-100",
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
  onIndex: (r: Repo) => void;
  onRemove: (r: Repo) => void;
  onUnignore: (r: Repo) => void;
}

interface Selection {
  isSelected: (r: Repo) => boolean;
  canSelect: (r: Repo) => boolean;
  toggle: (r: Repo) => void;
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
  onChange: () => void;
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
      onChange={onChange}
      className="size-4 cursor-pointer accent-indigo-600 disabled:cursor-not-allowed disabled:opacity-30"
    />
  );
}

function RepoRow({ repo: r, actions, selection }: { repo: Repo; actions: RowActions | null; selection: Selection | null }) {
  const checked = selection?.isSelected(r) ?? false;
  return (
    <tr
      className={cx(
        "transition hover:bg-zinc-50/80 dark:hover:bg-white/[0.02]",
        checked && "bg-indigo-50/60 dark:bg-indigo-500/10",
      )}
    >
      {selection && (
        <td className="w-10 py-3 pl-4">
          <Checkbox
            label={`Selecionar ${r.name}`}
            checked={checked}
            disabled={!selection.canSelect(r)}
            onChange={() => selection.toggle(r)}
          />
        </td>
      )}
      <td className="max-w-[18rem] px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-zinc-900/5 text-zinc-500 dark:bg-white/5 dark:text-zinc-400">
            <GitBranch className="size-4" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <p className="truncate font-medium" title={r.name}>
              {repoShortName(r.name, r.org)}
            </p>
            <p className="truncate text-xs text-zinc-500 dark:text-zinc-400">{r.org ?? "repositório avulso"}</p>
            {r.error && (
              <p className="mt-0.5 truncate text-xs text-rose-600 dark:text-rose-400" title={r.error}>
                {r.error}
              </p>
            )}
          </div>
        </div>
      </td>
      <td className="px-4 py-3">
        <Badge tone={r.status} dot>
          {STATUS_LABEL[r.status]}
        </Badge>
      </td>
      <td className="hidden px-4 py-3 text-zinc-600 lg:table-cell dark:text-zinc-300">{r.language ?? "—"}</td>
      <td className="hidden px-4 py-3 text-right tabular-nums xl:table-cell">{numberFormat.format(r.files)}</td>
      <td className="px-4 py-3 text-right tabular-nums">{numberFormat.format(r.chunks)}</td>
      <td className="hidden px-4 py-3 font-mono text-xs text-zinc-500 2xl:table-cell dark:text-zinc-400">{r.commit ?? "—"}</td>
      <td className="hidden px-4 py-3 lg:table-cell">
        <div className="flex items-center gap-2">
          <span title={formatDateTime(r.indexed_at)} className="whitespace-nowrap text-zinc-600 dark:text-zinc-300">
            {timeAgo(r.indexed_at)}
          </span>
          {r.outdated && <Badge tone="warning">novo push</Badge>}
        </div>
      </td>
      {actions && (
        <td className="px-4 py-3">
          <RepoActions repo={r} actions={actions} compact />
        </td>
      )}
    </tr>
  );
}

/** Cartão para telas pequenas: mostra o essencial e as ações com texto. */
function RepoCard({ repo: r, actions, selection }: { repo: Repo; actions: RowActions | null; selection: Selection | null }) {
  const checked = selection?.isSelected(r) ?? false;
  return (
    <li className={cx("space-y-3 p-4", checked && "bg-indigo-50/60 dark:bg-indigo-500/10")}>
      <div className="flex items-start justify-between gap-3">
        {selection && (
          <div className="pt-1">
            <Checkbox
              label={`Selecionar ${r.name}`}
              checked={checked}
              disabled={!selection.canSelect(r)}
              onChange={() => selection.toggle(r)}
            />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium" title={r.name}>
            {repoShortName(r.name, r.org)}
          </p>
          <p className="truncate text-xs text-zinc-500 dark:text-zinc-400">{r.org ?? "repositório avulso"}</p>
        </div>
        <Badge tone={r.status} dot>
          {STATUS_LABEL[r.status]}
        </Badge>
      </div>
      {r.error && <p className="text-xs text-rose-600 dark:text-rose-400">{r.error}</p>}
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
          <dt className="text-zinc-500 dark:text-zinc-400">Chunks</dt>
          <dd className="font-medium tabular-nums">{numberFormat.format(r.chunks)}</dd>
        </div>
      </dl>
      <p className="text-xs text-zinc-500 dark:text-zinc-400">
        Indexado {timeAgo(r.indexed_at)}
        {r.outdated && <> · <span className="text-amber-600 dark:text-amber-400">novo push</span></>}
      </p>
      {actions && <RepoActions repo={r} actions={actions} compact={false} />}
    </li>
  );
}

/** Ações de um repo. Na tabela são ícones (com rótulo acessível); no cartão, botões com texto. */
function RepoActions({ repo: r, actions, compact }: { repo: Repo; actions: RowActions; compact: boolean }) {
  const indexable = actions.canIndex && r.status !== "ignored";
  const reactivable = actions.canAdmin && r.status === "ignored";
  const removable = actions.canAdmin && r.status !== "ignored";
  const removeLabel = r.org ? "Ignorar" : "Remover";

  if (compact) {
    return (
      <div className="flex items-center justify-end gap-0.5">
        {indexable && (
          <IconButton label={`Indexar ${r.name}`} onClick={() => actions.onIndex(r)}>
            <Play className="size-4" />
          </IconButton>
        )}
        {reactivable && (
          <IconButton label={`Reativar ${r.name}`} onClick={() => actions.onUnignore(r)}>
            <Undo2 className="size-4" />
          </IconButton>
        )}
        {removable && (
          <IconButton label={`${removeLabel} ${r.name}`} onClick={() => actions.onRemove(r)} className="hover:text-rose-600 dark:hover:text-rose-400">
            <Trash2 className="size-4" />
          </IconButton>
        )}
      </div>
    );
  }

  if (!indexable && !reactivable && !removable) return null;
  return (
    <div className="flex flex-wrap gap-2">
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
        <Button variant="secondary" size="sm" onClick={() => actions.onRemove(r)}>
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

function compare(a: Repo, b: Repo, key: SortKey): number {
  switch (key) {
    case "status":
      return a.status.localeCompare(b.status);
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
