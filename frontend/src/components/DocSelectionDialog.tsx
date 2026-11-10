import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, ExternalLink, FileText, Folder, FolderOpen, RotateCcw, Search } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { DocCatalog, DocCatalogPage, DocSelectionResult, DocSourceInfo } from "../api/types";
import { cx, plural } from "../lib/utils";
import { Dialog } from "./Dialog";
import { Alert, Badge, Button, Segmented, Skeleton, inputClass } from "./ui";

const numberFormat = new Intl.NumberFormat("pt-BR");
const SEARCH_LIMIT = 200;

interface TreeNode {
  /** Caminho sem idioma ("Plataforma/Email"). */
  path: string;
  name: string;
  depth: number;
  /** A pasta também pode ser uma página da wiki. */
  page: DocCatalogPage | null;
  children: TreeNode[];
  /** Páginas da subárvore, inclusive a do próprio nó. */
  pages: string[];
}

type View = "all" | "selected" | "unselected" | "changed";
type Step = "edit" | "review" | "done";

const collator = new Intl.Collator("pt-BR", { sensitivity: "base", numeric: true });

function buildTree(pages: DocCatalogPage[]): TreeNode[] {
  const root: TreeNode = { path: "", name: "", depth: -1, page: null, children: [], pages: [] };
  const index = new Map<string, TreeNode>([["", root]]);
  for (const page of pages) {
    let parent = root;
    const parts = page.path.split("/");
    parts.forEach((name, depth) => {
      const path = parent.path ? `${parent.path}/${name}` : name;
      let node = index.get(path);
      if (!node) {
        node = { path, name, depth, page: null, children: [], pages: [] };
        index.set(path, node);
        parent.children.push(node);
      }
      parent = node;
    });
    parent.page = page;
  }
  // Pastas antes das páginas soltas; ordem alfabética (pt-BR, números em ordem natural).
  const fill = (node: TreeNode): string[] => {
    node.children.sort(
      (a, b) => Number(b.children.length > 0) - Number(a.children.length > 0) || collator.compare(a.name, b.name),
    );
    node.pages = [...(node.page ? [node.page.path] : []), ...node.children.flatMap(fill)];
    return node.pages;
  };
  fill(root);
  return root.children;
}

/** Todas as pastas da árvore (para "Expandir tudo"). */
function folderPaths(nodes: TreeNode[]): string[] {
  return nodes.flatMap((n) => (n.children.length ? [n.path, ...folderPaths(n.children)] : []));
}

const normalize = (text: string) => text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();

/** Caixa de seleção com o estado "parcial" (parte da pasta escolhida). */
function TriCheckbox({ checked, partial, label, onChange }: { checked: boolean; partial: boolean; label: string; onChange: (on: boolean) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = partial;
  }, [partial]);
  return (
    <input
      ref={ref}
      type="checkbox"
      aria-label={label}
      checked={checked}
      onChange={(e) => onChange(e.target.checked)}
      className="size-4 shrink-0 accent-accent-600"
    />
  );
}

/** O que acontece com a página no próximo sync, pela escolha atual. */
function PageBadges({ page, chosen }: { page: DocCatalogPage; chosen: boolean }) {
  return (
    <>
      {chosen && !page.indexed && <Badge tone="accent">entra</Badge>}
      {!chosen && page.indexed && <Badge tone="warning">sai do índice</Badge>}
      {page.ignored && <Badge tone="ignored">fora da busca</Badge>}
    </>
  );
}

function RuleList({ title, include, exclude }: { title: string; include: string[]; exclude: string[] }) {
  return (
    <div className="min-w-0 space-y-2 rounded-xl border border-stone-200 p-3 dark:border-stone-800">
      <p className="text-xs font-medium text-stone-500 dark:text-stone-400">{title}</p>
      {include.length === 0 && exclude.length === 0 ? (
        <p className="text-sm">Todas as páginas do idioma</p>
      ) : (
        <ul className="max-h-56 space-y-0.5 overflow-y-auto font-mono text-xs">
          {include.length === 0 && <li className="text-stone-500 dark:text-stone-400">inclui: todas as páginas</li>}
          {include.map((g) => (
            <li key={`i${g}`} className="truncate" title={g}>
              <span className="text-emerald-600 dark:text-emerald-400">+ </span>
              {g}
            </li>
          ))}
          {exclude.map((g) => (
            <li key={`e${g}`} className="truncate" title={g}>
              <span className="text-rose-600 dark:text-rose-400">− </span>
              {g}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface DocSelectionDialogProps {
  /** Fonte a editar; null fecha. */
  source: DocSourceInfo | null;
  onClose: () => void;
  /** Seleção gravada (a lista de fontes muda). */
  onSaved: () => void;
  /** Abre a indexação da fonte (só com index:write). */
  onIndex: ((source: DocSourceInfo) => void) | null;
}

/**
 * Escolha das páginas de uma wiki em árvore. Nada é baixado nem indexado aqui: gravar troca as regras
 * include/exclude da fonte (calculadas no servidor), e o próximo sync baixa o que entrou e apaga o que saiu.
 */
export function DocSelectionDialog({ source, onClose, onSaved, onIndex }: DocSelectionDialogProps) {
  const [catalog, setCatalog] = useState<DocCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [view, setView] = useState<View>("all");
  const [step, setStep] = useState<Step>("edit");
  const [review, setReview] = useState<DocSelectionResult | null>(null);
  const [busy, setBusy] = useState(false);
  const name = source?.name ?? null;

  useEffect(() => {
    if (!name) return;
    let active = true;
    setCatalog(null);
    setError(null);
    setQuery("");
    setView("all");
    setStep("edit");
    setReview(null);
    setExpanded(new Set());
    api
      .docCatalog(name)
      .then((c) => {
        if (!active) return;
        setCatalog(c);
        setChosen(new Set(c.pages.filter((p) => p.selected).map((p) => p.path)));
      })
      .catch((err: unknown) => active && setError(describeError(err)));
    return () => {
      active = false;
    };
  }, [name]);

  const pages = catalog?.pages;
  const byPath = useMemo(() => new Map((pages ?? []).map((p) => [p.path, p])), [pages]);
  const tree = useMemo(() => buildTree(pages ?? []), [pages]);
  const initial = useMemo(() => new Set((pages ?? []).filter((p) => p.selected).map((p) => p.path)), [pages]);

  const stats = useMemo(() => {
    let indexed = 0;
    let entering = 0;
    let leaving = 0;
    let changed = 0;
    for (const p of pages ?? []) {
      const on = chosen.has(p.path);
      if (p.indexed) indexed++;
      if (on && !p.indexed) entering++;
      if (!on && p.indexed) leaving++;
      if (on !== initial.has(p.path)) changed++;
    }
    return { indexed, entering, leaving, changed };
  }, [pages, chosen, initial]);

  const pagePasses = useMemo(() => {
    const tests: Record<View, (path: string) => boolean> = {
      all: () => true,
      selected: (path) => chosen.has(path),
      unselected: (path) => !chosen.has(path),
      changed: (path) => chosen.has(path) !== initial.has(path),
    };
    return tests[view];
  }, [view, chosen, initial]);

  const matches = useMemo(() => {
    const needle = normalize(query.trim());
    if (!needle || !pages) return null;
    return pages.filter((p) => pagePasses(p.path) && (normalize(p.title).includes(needle) || normalize(p.path).includes(needle)));
  }, [query, pages, pagePasses]);

  // Linhas visíveis da árvore: a pasta aparece se alguma página dela passa no filtro. Em "Alterações", tudo aberto.
  const rows = useMemo(() => {
    const out: { node: TreeNode; self: boolean }[] = [];
    const walk = (nodes: TreeNode[]) => {
      for (const node of nodes) {
        if (!node.pages.some(pagePasses)) continue;
        out.push({ node, self: false });
        if (node.children.length > 0 && (view === "changed" || expanded.has(node.path))) {
          if (node.page && pagePasses(node.page.path)) out.push({ node, self: true });
          walk(node.children);
        }
      }
    };
    walk(tree);
    return out;
  }, [tree, expanded, pagePasses, view]);

  function setMany(paths: string[], on: boolean) {
    setChosen((prev) => {
      const next = new Set(prev);
      for (const p of paths) {
        if (on) next.add(p);
        else next.delete(p);
      }
      return next;
    });
  }

  function toggleExpanded(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  async function goReview() {
    if (!source) return;
    setBusy(true);
    setError(null);
    try {
      setReview(await api.setDocSelection(source.name, [...chosen], true));
      setStep("review");
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!source) return;
    setBusy(true);
    setError(null);
    try {
      setReview(await api.setDocSelection(source.name, [...chosen], false));
      setStep("done");
      onSaved();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  const total = pages?.length ?? 0;
  const footer =
    step === "done" ? (
      <>
        <Button variant="secondary" onClick={onClose}>
          Fechar
        </Button>
        {onIndex && source && <Button onClick={() => onIndex(source)}>Indexar agora…</Button>}
      </>
    ) : step === "review" ? (
      <>
        <Button variant="ghost" onClick={() => setStep("edit")} disabled={busy}>
          Voltar
        </Button>
        <Button onClick={() => void save()} loading={busy} disabled={!review?.changed}>
          Salvar seleção
        </Button>
      </>
    ) : (
      <>
        <Button variant="ghost" onClick={onClose}>
          Cancelar
        </Button>
        <Button onClick={() => void goReview()} loading={busy} disabled={!catalog || chosen.size === 0 || stats.changed === 0}>
          {stats.changed > 0 ? `Revisar ${numberFormat.format(stats.changed)} ${plural(stats.changed, "alteração", "alterações")}` : "Sem alterações"}
        </Button>
      </>
    );

  return (
    <Dialog
      open={source !== null}
      onClose={onClose}
      title={source ? `Páginas de ${source.name}` : "Páginas"}
      description="Marque o que entra no índice. Salvar não indexa nada: o próximo sync da fonte baixa as páginas novas e apaga as que saíram."
      size="xl"
      footer={footer}
    >
      {error && (
        <div className="mb-4">
          <Alert>{error}</Alert>
        </div>
      )}

      {!catalog && !error && (
        <div className="space-y-2">
          <Skeleton className="h-10 w-full" />
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      )}

      {catalog && step === "edit" && (
        <div className="space-y-3">
          <p className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm text-stone-600 tabular-nums dark:text-stone-300">
            <span>
              <strong className="text-stone-900 dark:text-white">{numberFormat.format(chosen.size)}</strong> de {numberFormat.format(total)} selecionadas
            </span>
            <span>{numberFormat.format(stats.indexed)} no índice hoje</span>
            <span className="text-accent-700 dark:text-accent-300" title="Baixadas e enviadas à Voyage no próximo sync (custo de embedding)">
              +{numberFormat.format(stats.entering)} {plural(stats.entering, "entra", "entram")} no próximo sync
            </span>
            <span className="text-amber-700 dark:text-amber-300" title="Apagadas do índice no próximo sync, sem custo">
              −{numberFormat.format(stats.leaving)} {plural(stats.leaving, "sai", "saem")} do índice
            </span>
          </p>

          <div className="flex flex-wrap items-center gap-3">
            <div className="relative min-w-[14rem] flex-1">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-stone-400" aria-hidden="true" />
              <input
                className={`${inputClass} pl-9`}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Buscar página por título ou caminho"
                aria-label="Buscar página da wiki"
                data-autofocus
              />
            </div>
            <Segmented
              label="Mostrar"
              value={view}
              onChange={setView}
              options={[
                { value: "all", label: "Todas" },
                { value: "selected", label: "Selecionadas" },
                { value: "unselected", label: "Fora" },
                { value: "changed", label: `Alterações ${stats.changed}` },
              ]}
            />
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-stone-500 dark:text-stone-400">
            <p>Pasta marcada inteira inclui também as páginas criadas nela depois. Seções novas na raiz da wiki nunca entram sozinhas.</p>
            <div className="flex items-center gap-1">
              {!matches && view !== "changed" && (
                <>
                  <Button variant="ghost" size="sm" onClick={() => setExpanded(new Set(folderPaths(tree)))}>
                    Expandir tudo
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setExpanded(new Set())}>
                    Recolher
                  </Button>
                </>
              )}
              <Button variant="ghost" size="sm" disabled={stats.changed === 0} onClick={() => setChosen(new Set(initial))}>
                <RotateCcw className="size-3.5" aria-hidden="true" />
                Desfazer alterações
              </Button>
            </div>
          </div>

          {matches ? (
            <SearchResults matches={matches} chosen={chosen} onSet={setMany} />
          ) : rows.length === 0 ? (
            <p className="rounded-xl border border-dashed border-stone-300 p-6 text-center text-sm text-stone-500 dark:border-stone-700 dark:text-stone-400">
              Nenhuma página neste filtro.
            </p>
          ) : (
            <ul aria-label={`Páginas de ${catalog.name}`} className="max-h-[44dvh] overflow-y-auto rounded-xl border border-stone-200 py-1 dark:border-stone-800">
              {rows.map(({ node, self }) =>
                self && node.page ? (
                  <PageRow key={`${node.path}#self`} page={node.page} depth={node.depth + 1} label="Página da pasta" chosen={chosen.has(node.path)} onSet={setMany} />
                ) : node.children.length === 0 && node.page ? (
                  <PageRow key={node.path} page={node.page} depth={node.depth} chosen={chosen.has(node.path)} onSet={setMany} />
                ) : (
                  <FolderRow
                    key={node.path}
                    node={node}
                    open={view === "changed" || expanded.has(node.path)}
                    onToggle={() => toggleExpanded(node.path)}
                    chosen={chosen}
                    byPath={byPath}
                    onSet={setMany}
                  />
                ),
              )}
            </ul>
          )}
        </div>
      )}

      {catalog && step !== "edit" && review && (
        <div className="space-y-4 text-sm">
          {step === "done" ? (
            <Alert tone="info">
              Seleção salva: {numberFormat.format(review.selected)} {plural(review.selected, "página", "páginas")}. Nada foi indexado ainda.
            </Alert>
          ) : !review.changed ? (
            <Alert tone="info">As regras calculadas são as mesmas de hoje: nada a salvar.</Alert>
          ) : null}
          <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Stat label="Páginas na seleção" value={numberFormat.format(review.selected)} />
            <Stat label="Entram na seleção" value={`+${numberFormat.format(review.added)}`} tone="accent" />
            <Stat label="Saem da seleção" value={`−${numberFormat.format(review.removed)}`} tone="warning" />
          </dl>
          <div className="grid gap-3 sm:grid-cols-2">
            <RuleList title="Regras de hoje" include={review.previous.include} exclude={review.previous.exclude} />
            <RuleList title={step === "done" ? "Regras gravadas" : "Regras novas"} include={review.include} exclude={review.exclude} />
          </div>
          <p className="text-stone-600 dark:text-stone-300">
            No próximo sync desta fonte, {numberFormat.format(stats.entering)} {plural(stats.entering, "página nova é baixada e vai", "páginas novas são baixadas e vão")} para a Voyage
            (custo de embedding), e {numberFormat.format(stats.leaving)} {plural(stats.leaving, "página sai", "páginas saem")} do índice, sem custo.
          </p>
        </div>
      )}
    </Dialog>
  );
}

function Stat({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "accent" | "warning" }) {
  return (
    <div className="rounded-xl border border-stone-200 px-3 py-2 dark:border-stone-800">
      <dt className="text-xs text-stone-500 dark:text-stone-400">
        {label}
        {hint && <span className="text-stone-400 dark:text-stone-500"> · {hint}</span>}
      </dt>
      <dd
        className={cx(
          "text-lg font-semibold tabular-nums",
          tone === "accent" && "text-accent-700 dark:text-accent-300",
          tone === "warning" && "text-amber-700 dark:text-amber-300",
        )}
      >
        {value}
      </dd>
    </div>
  );
}

const rowClass = "flex min-h-9 items-center gap-2 pr-3 hover:bg-stone-50 dark:hover:bg-stone-800/60";
const indent = (depth: number) => ({ paddingLeft: `${0.75 + depth * 1.25}rem` });

function FolderRow({
  node,
  open,
  onToggle,
  chosen,
  byPath,
  onSet,
}: {
  node: TreeNode;
  open: boolean;
  onToggle: () => void;
  chosen: Set<string>;
  byPath: Map<string, DocCatalogPage>;
  onSet: (paths: string[], on: boolean) => void;
}) {
  let selected = 0;
  let entering = 0;
  let leaving = 0;
  for (const path of node.pages) {
    const on = chosen.has(path);
    const indexed = byPath.get(path)?.indexed ?? false;
    if (on) selected++;
    if (on && !indexed) entering++;
    if (!on && indexed) leaving++;
  }
  const all = selected === node.pages.length;
  const Icon = open ? FolderOpen : Folder;
  return (
    <li className={rowClass} style={indent(node.depth)}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-label={`Pasta ${node.path}`}
        className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-stone-500 hover:bg-stone-900/5 dark:hover:bg-white/10"
      >
        <ChevronRight className={cx("size-4 transition-transform", open && "rotate-90")} aria-hidden="true" />
      </button>
      <TriCheckbox checked={all} partial={selected > 0 && !all} label={`Selecionar a pasta ${node.path}`} onChange={(on) => onSet(node.pages, on)} />
      <Icon className="size-4 shrink-0 text-stone-400" aria-hidden="true" />
      <button type="button" tabIndex={-1} onClick={onToggle} className="min-w-0 flex-1 truncate text-left text-sm font-medium" title={node.path}>
        {node.page?.title && node.page.title !== node.name ? (
          <>
            {node.page.title} <span className="font-mono text-xs font-normal text-stone-500 dark:text-stone-400">{node.name}</span>
          </>
        ) : (
          node.name
        )}
      </button>
      <span className="flex shrink-0 items-center gap-2 text-xs tabular-nums">
        {entering > 0 && <span className="text-accent-700 dark:text-accent-300">+{numberFormat.format(entering)}</span>}
        {leaving > 0 && <span className="text-amber-700 dark:text-amber-300">−{numberFormat.format(leaving)}</span>}
        <span className="text-stone-500 dark:text-stone-400">
          {numberFormat.format(selected)}/{numberFormat.format(node.pages.length)}
        </span>
      </span>
    </li>
  );
}

function PageRow({
  page,
  depth,
  label,
  chosen,
  onSet,
}: {
  page: DocCatalogPage;
  depth: number;
  label?: string;
  chosen: boolean;
  onSet: (paths: string[], on: boolean) => void;
}) {
  const name = page.path.split("/").pop() ?? page.path;
  return (
    <li className={rowClass} style={indent(depth)}>
      <span className="size-6 shrink-0" aria-hidden="true" />
      <TriCheckbox checked={chosen} partial={false} label={`Selecionar ${page.path}`} onChange={(on) => onSet([page.path], on)} />
      <FileText className="size-4 shrink-0 text-stone-400" aria-hidden="true" />
      <span className="min-w-0 flex-1 truncate text-sm" title={page.path}>
        {label ? <span className="text-stone-500 dark:text-stone-400">{label}: </span> : null}
        {page.title}
        {!label && page.title !== name && <span className="ml-1.5 font-mono text-xs text-stone-500 dark:text-stone-400">{name}</span>}
      </span>
      <span className="flex shrink-0 items-center gap-1.5">
        <PageBadges page={page} chosen={chosen} />
        <a href={page.url} target="_blank" rel="noopener noreferrer" aria-label={`Abrir ${page.title} na wiki`} className="text-stone-400 hover:text-accent-500">
          <ExternalLink className="size-3.5" aria-hidden="true" />
        </a>
      </span>
    </li>
  );
}

function SearchResults({
  matches,
  chosen,
  onSet,
}: {
  matches: DocCatalogPage[];
  chosen: Set<string>;
  onSet: (paths: string[], on: boolean) => void;
}) {
  const shown = matches.slice(0, SEARCH_LIMIT);
  const all = matches.map((m) => m.path);
  const selected = all.filter((p) => chosen.has(p)).length;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="text-stone-600 dark:text-stone-300">
          {numberFormat.format(matches.length)} {plural(matches.length, "página encontrada", "páginas encontradas")} · {numberFormat.format(selected)} {plural(selected, "selecionada", "selecionadas")}
        </span>
        {matches.length > 0 && (
          <span className="flex gap-1">
            <Button variant="secondary" size="sm" disabled={selected === matches.length} onClick={() => onSet(all, true)}>
              Marcar {matches.length > 1 ? "todas" : "a página"}
            </Button>
            <Button variant="secondary" size="sm" disabled={selected === 0} onClick={() => onSet(all, false)}>
              Desmarcar {matches.length > 1 ? "todas" : "a página"}
            </Button>
          </span>
        )}
      </div>
      {matches.length === 0 ? (
        <p className="rounded-xl border border-dashed border-stone-300 p-6 text-center text-sm text-stone-500 dark:border-stone-700 dark:text-stone-400">
          Nenhuma página com esse título ou caminho.
        </p>
      ) : (
        <ul className="max-h-[44dvh] divide-y divide-stone-100 overflow-y-auto rounded-xl border border-stone-200 dark:divide-stone-800 dark:border-stone-800">
          {shown.map((page) => {
            const on = chosen.has(page.path);
            return (
              <li key={page.path} className="flex items-center gap-3 px-3 py-2 hover:bg-stone-50 dark:hover:bg-stone-800/60">
                <TriCheckbox checked={on} partial={false} label={`Selecionar ${page.path}`} onChange={(next) => onSet([page.path], next)} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm">{page.title}</p>
                  <p className="truncate font-mono text-xs text-stone-500 dark:text-stone-400">{page.path}</p>
                </div>
                <span className="flex shrink-0 items-center gap-1.5">
                  <PageBadges page={page} chosen={on} />
                  <a href={page.url} target="_blank" rel="noopener noreferrer" aria-label={`Abrir ${page.title} na wiki`} className="text-stone-400 hover:text-accent-500">
                    <ExternalLink className="size-3.5" aria-hidden="true" />
                  </a>
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {matches.length > SEARCH_LIMIT && (
        <p className="text-xs text-stone-500 dark:text-stone-400">
          Mostrando {numberFormat.format(SEARCH_LIMIT)} de {numberFormat.format(matches.length)}. Marcar/desmarcar vale para todas as encontradas.
        </p>
      )}
    </div>
  );
}
