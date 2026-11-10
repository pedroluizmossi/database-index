import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, BookOpen, Braces, ChevronRight, Database, ExternalLink, FileCode2, FileText, GitBranch, Network, RefreshCw, Search } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import { API_DOC_TYPES, type OriginKind, type Repo, type XrayData } from "../api/types";
import { FileDrawer, type FileTarget } from "../components/FileDrawer";
import { RepoSheet } from "../components/RepoSheet";
import { OriginBadges } from "../components/docs";
import { KIND_META, KindIcon } from "../components/kind";
import { XrayMap } from "../components/XrayMap";
import { Alert, Badge, Button, EmptyState, Panel, Skeleton, inputClass } from "../components/ui";
import { useIndex } from "../data/IndexProvider";
import { apiId, pageId, type XrayKind, type XrayTarget } from "../lib/xray";
import { cx } from "../lib/utils";

type Page = XrayData["pages"][number];
type Node = XrayTarget & { title: string; subtitle: string; repo?: Repo; page?: Page; operations?: Page[]; url?: string | null };
type Related = { node: Node; origins: OriginKind[]; via: string[] };
type Graph = { nodes: Node[]; byKey: Map<string, Node>; repoLinks: Map<string, Map<string, Set<OriginKind>>>; itemLinks: Map<string, Map<string, Set<OriginKind>>> };
const key = (target: XrayTarget) => `${target.kind}:${target.id}`;
function safeUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try { const parsed = new URL(url); return parsed.protocol === "http:" || parsed.protocol === "https:" ? url : null; }
  catch { return null; }
}
const KINDS: { kind: XrayKind; label: string; icon: typeof Database }[] = [
  { kind: "repo", label: "Repositórios", icon: GitBranch },
  { kind: "doc", label: "Documentos", icon: BookOpen },
  { kind: "api", label: "APIs", icon: Braces },
];

function buildGraph(data: XrayData, repos: Repo[]): Graph {
  const nodes: Node[] = repos.filter((r) => r.kind === "code").map((r) => ({
    kind: "repo", id: r.name, title: r.name.split("__").pop() ?? r.name, subtitle: r.org ?? "Repositório local", repo: r,
  }));
  const apiNodes = new Map<string, Node>();
  const pageTargets = new Map<string, XrayTarget>();
  for (const p of data.pages) {
    const pid = pageId(p.repo, p.path);
    if (API_DOC_TYPES.includes(p.type)) {
      const { domain, service } = p.attrs;
      if (!domain || !service) continue;
      const id = apiId(p.repo, domain, service);
      let node = apiNodes.get(id);
      if (!node) {
        node = { kind: "api", id, title: service, subtitle: domain, operations: [], url: p.url };
        apiNodes.set(id, node);
      }
      if (p.type === "api_operation") node.operations?.push(p);
      pageTargets.set(pid, { kind: "api", id });
    } else {
      nodes.push({ kind: "doc", id: pid, title: p.title || p.path.split("/").pop() || p.path,
        subtitle: p.path, page: p, url: p.url });
      pageTargets.set(pid, { kind: "doc", id: pid });
    }
  }
  nodes.push(...apiNodes.values());
  const byKey = new Map(nodes.map((n) => [key(n), n]));
  const repoLinks: Graph["repoLinks"] = new Map();
  const itemLinks: Graph["itemLinks"] = new Map();
  for (const link of data.links) {
    const target = pageTargets.get(pageId(link.doc_repo, link.doc_path));
    if (!target || !byKey.has(key({ kind: "repo", id: link.repo }))) continue;
    const targetKey = key(target);
    const byItem = repoLinks.get(link.repo) ?? new Map<string, Set<OriginKind>>();
    const byRepo = itemLinks.get(targetKey) ?? new Map<string, Set<OriginKind>>();
    const origins = byItem.get(targetKey) ?? new Set<OriginKind>();
    origins.add(link.origin); byItem.set(targetKey, origins); byRepo.set(link.repo, origins);
    repoLinks.set(link.repo, byItem); itemLinks.set(targetKey, byRepo);
  }
  return { nodes, byKey, repoLinks, itemLinks };
}

function related(graph: Graph, selected: Node): Related[] {
  const results = new Map<string, Related>();
  if (selected.kind === "repo") {
    for (const [nodeKey, origins] of graph.repoLinks.get(selected.id) ?? []) {
      const node = graph.byKey.get(nodeKey);
      if (node) results.set(nodeKey, { node, origins: [...origins], via: [] });
    }
  } else {
    const repos = graph.itemLinks.get(key(selected)) ?? new Map();
    for (const [repoName, origins] of repos) {
      const repoNode = graph.byKey.get(key({ kind: "repo", id: repoName }));
      if (repoNode) results.set(key(repoNode), { node: repoNode, origins: [...origins], via: [] });
      for (const [otherKey] of graph.repoLinks.get(repoName) ?? []) {
        const node = graph.byKey.get(otherKey);
        if (!node || node.kind === selected.kind || node.kind === "repo") continue;
        const previous = results.get(otherKey) ?? { node, origins: [], via: [] };
        previous.via.push(repoName); results.set(otherKey, previous);
      }
    }
  }
  return [...results.values()].sort((a, b) => a.node.title.localeCompare(b.node.title, "pt-BR"));
}

export function XrayPage({ initial }: { initial: XrayTarget | null }) {
  const { repos, reload: reloadIndex } = useIndex();
  const [data, setData] = useState<XrayData | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [kind, setKind] = useState<XrayKind>(initial?.kind ?? "repo");
  const [selected, setSelected] = useState<XrayTarget | null>(initial);
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState(12);
  const [file, setFile] = useState<FileTarget | null>(null);
  const detailRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try { setData(await api.xray()); }
    catch (e) { setError(describeError(e)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (initial) { setSelected(initial); setKind(initial.kind); setQuery(""); setShown(12); } }, [initial]);
  const graph = useMemo(() => data && repos ? buildGraph(data, repos) : null, [data, repos]);
  const chosen = selected ? graph?.byKey.get(key(selected)) : undefined;
  useEffect(() => {
    if (!chosen || !window.matchMedia("(max-width: 1023px)").matches) return;
    const frame = window.requestAnimationFrame(() => detailRef.current?.scrollIntoView({ block: "start", behavior: "smooth" }));
    return () => window.cancelAnimationFrame(frame);
  }, [chosen]);
  const relations = graph && chosen ? related(graph, chosen) : [];
  const matching = graph?.nodes.filter((n) => n.kind === kind && `${n.title} ${n.subtitle}`.toLowerCase().includes(query.trim().toLowerCase()))
    .sort((a, b) => a.title.localeCompare(b.title, "pt-BR")) ?? [];
  const select = (target: XrayTarget) => {
    if (target.kind !== kind) setQuery(graph?.byKey.get(key(target))?.title ?? "");
    setSelected(target); setKind(target.kind); setShown(12);
  };
  const refresh = () => { void load(); void reloadIndex(); };

  return <div className="space-y-7">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div><h1 className="text-2xl font-semibold tracking-tight">Raio X</h1><p className="mt-1 text-sm text-stone-500 dark:text-stone-400">Relações entre repositórios, documentos e APIs.</p></div>
      <Button variant="secondary" size="sm" loading={loading} onClick={refresh}><RefreshCw className="size-3.5" /> Atualizar</Button>
    </header>
    {error && <Alert>{error}</Alert>}
    {data && !data.available && <Alert tone="info">O índice de documentação ainda não está disponível. Os vínculos aparecerão após a primeira indexação de documentação.</Alert>}
    <div className="grid items-start gap-5 lg:grid-cols-[minmax(260px,340px)_minmax(0,1fr)]">
      <Panel className="overflow-hidden">
        <div className="space-y-4 border-b border-stone-200 p-4 dark:border-stone-800">
          <div><h2 className="font-semibold">Explorar</h2><p className="mt-1 text-xs text-stone-500 dark:text-stone-400">Escolha um ponto de partida.</p></div>
          <div className="grid grid-cols-3 gap-1 rounded-xl bg-stone-100 p-1 dark:bg-stone-800" role="group" aria-label="Tipo de conteúdo">
            {KINDS.map(({ kind: k, label, icon: Icon }) => <button key={k} type="button" aria-pressed={kind === k} onClick={() => { setKind(k); setQuery(""); setShown(12); }} className={cx("flex flex-col items-center gap-1 rounded-lg px-1 py-2 text-xs font-medium transition sm:flex-row sm:justify-center", kind === k ? "bg-sheet text-stone-900 shadow-sm dark:bg-stone-700 dark:text-white" : "text-stone-500 hover:text-stone-800 dark:text-stone-400 dark:hover:text-white")}><Icon className={cx("size-3.5", KIND_META[k].text)} />{label}</button>)}
          </div>
          <div className="relative"><Search className="pointer-events-none absolute top-3 left-3 size-4 text-stone-400" aria-hidden="true" /><input className={`${inputClass} pl-9`} aria-label={`Buscar ${KINDS.find((k) => k.kind === kind)?.label.toLowerCase()}`} value={query} onChange={(e) => { setQuery(e.target.value); setShown(12); }} placeholder="Buscar pelo nome" /></div>
          <p className="text-xs text-stone-500 dark:text-stone-400">{matching.length.toLocaleString("pt-BR")} {KINDS.find((k) => k.kind === kind)?.label.toLowerCase()}</p>
        </div>
        {!graph && !error && <div className="space-y-2 p-4"><Skeleton className="h-12 w-full" /><Skeleton className="h-12 w-full" /></div>}
        {graph && matching.length === 0 && <EmptyState icon={Search} title="Nada encontrado" text="Experimente outro termo ou tipo de conteúdo." />}
        <ul className="stagger divide-y divide-stone-100 dark:divide-stone-800">{matching.slice(0, shown).map((node) => <li key={key(node)}><button type="button" onClick={() => select(node)} aria-current={chosen && key(chosen) === key(node) ? "true" : undefined} className={cx("flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-stone-50 dark:hover:bg-white/[0.03]", chosen && key(chosen) === key(node) && "bg-stone-100 shadow-[inset_3px_0_0_var(--color-accent-500)] hover:bg-stone-100 dark:bg-stone-800/60 dark:hover:bg-stone-800/60")}><KindIcon kind={node.kind} size="sm" /><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{node.title}</p><p className="truncate text-xs text-stone-500 dark:text-stone-400">{node.subtitle}</p></div><ChevronRight className="size-4 shrink-0 text-stone-400" /></button></li>)}</ul>
        {shown < matching.length && <div className="p-3"><Button variant="ghost" size="sm" onClick={() => setShown((n) => n + 12)}>Mostrar mais</Button></div>}
      </Panel>
      <div ref={detailRef} className="min-w-0 scroll-mt-32 space-y-5">
        {!chosen && <Panel className="px-6 py-16"><EmptyState icon={Network} title={selected && graph ? "Item não encontrado no índice" : "Comece por um item"} text="Escolha um repositório, documento ou API para explorar suas relações." /></Panel>}
        {chosen && <Panel className="p-5 sm:p-6"><XrayMap center={{ key: key(chosen), title: chosen.title }} neighbors={relations.map(({ node, origins, via }) => ({ key: key(node), target: { kind: node.kind, id: node.id }, title: node.title, subtitle: node.subtitle, origins, inferred: via.length > 0 }))} onSelect={select} /></Panel>}
        {chosen?.kind === "repo" && chosen.repo && graph && (() => {
          const docRows = relations.filter((r) => r.node.kind === "doc");
          const apiRows = relations.filter((r) => r.node.kind === "api");
          return <RepoSheet
            repo={chosen.repo}
            knownRepos={new Set(graph.nodes.filter((n) => n.kind === "repo").map((n) => n.id))}
            onSelectRepo={(name) => select({ kind: "repo", id: name })}
            onOpenFile={setFile}
            docs={<RelationSection label="Documentos" kind="doc" rows={docRows} onSelect={select} inferred={false} />}
            docCount={docRows.length}
            linkedApis={<RelationSection label="APIs documentadas" kind="api" rows={apiRows} onSelect={select} inferred={false} />}
            linkedApiCount={apiRows.length}
          />;
        })()}
        {chosen && !(chosen.kind === "repo" && chosen.repo) && <>
          <Panel className="overflow-hidden">
            <div className="border-b border-stone-200 p-5 sm:p-6 dark:border-stone-800">
              <div className="flex items-start gap-3"><KindIcon kind={chosen.kind} size="lg" /><div className="min-w-0 flex-1"><p className="text-xs font-medium text-stone-500 dark:text-stone-400">{chosen.kind === "repo" ? "Repositório" : chosen.kind === "doc" ? "Documento" : "API / serviço"}</p><h2 className="mt-1 break-words text-xl font-semibold tracking-tight">{chosen.title}</h2><p className="mt-1 break-all text-sm text-stone-500 dark:text-stone-400">{chosen.subtitle}</p></div></div>
              <div className="mt-5 flex flex-wrap gap-2">{chosen.repo && <Badge tone={chosen.repo.status} dot>{chosen.repo.status === "indexed" ? "Indexado" : chosen.repo.status}</Badge>}{chosen.operations && <Badge tone="accent">{chosen.operations.length} operações</Badge>}{chosen.page && <Button variant="secondary" size="sm" onClick={() => setFile({repo: chosen.page!.repo, path: chosen.page!.path})}><FileText className="size-3.5" /> Ler documento</Button>}{safeUrl(chosen.url) && <a href={safeUrl(chosen.url)!} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-xs font-medium text-accent-600 hover:underline dark:text-accent-400">Abrir origem <ExternalLink className="size-3.5" /></a>}</div>
            </div>
            <div className="grid grid-cols-2 gap-3 p-5 sm:grid-cols-3">{KINDS.filter((k) => k.kind !== chosen.kind).map(({kind: k, label, icon: Icon}) => <div key={k} className="flex items-center gap-3"><Icon className={cx("size-4", KIND_META[k].text)} /><div><p className="text-lg font-semibold tabular-nums">{relations.filter((r) => r.node.kind === k).length}</p><p className="text-xs text-stone-500 dark:text-stone-400">{k === "api" ? "APIs" : label.toLowerCase()}</p></div></div>)}</div>
          </Panel>
          {KINDS.filter((k) => k.kind !== chosen.kind).map(({kind: k, label}) => <RelationSection key={k} label={label} kind={k} rows={relations.filter((r) => r.node.kind === k)} onSelect={select} inferred={chosen.kind !== "repo" && k !== "repo"} />)}
          {chosen.kind === "api" && <Operations key={chosen.id} operations={chosen.operations ?? []} onOpen={setFile} />}
        </>}
      </div>
    </div>
    <FileDrawer target={file} onClose={() => setFile(null)} />
  </div>;
}

function Operations({ operations, onOpen }: { operations: Page[]; onOpen: (target: FileTarget) => void }) {
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState(false);
  const matches = operations.filter((p) => (p.title ?? p.path).toLowerCase().includes(query.trim().toLowerCase()))
    .sort((a, b) => (a.title ?? "").localeCompare(b.title ?? ""));
  return <Panel className="overflow-hidden"><div className="space-y-3 border-b border-stone-200 px-5 py-4 dark:border-stone-800"><div className="flex items-center gap-2"><FileCode2 className="size-4 text-accent-500" /><h3 className="font-semibold">Operações</h3><span className="text-sm text-stone-500">{operations.length}</span></div><div className="relative"><Search className="pointer-events-none absolute top-3 left-3 size-4 text-stone-400" /><input aria-label="Buscar operações" className={`${inputClass} pl-9`} value={query} onChange={(e) => { setQuery(e.target.value); setExpanded(false); }} placeholder="Buscar por método ou caminho" /></div></div>
    {matches.length ? <><ul className="divide-y divide-stone-100 dark:divide-stone-800">{matches.slice(0, expanded ? undefined : 8).map((op) => <li key={op.path}><button type="button" onClick={() => onOpen({repo: op.repo, path: op.path})} className="flex w-full items-center gap-3 px-5 py-3 text-left hover:bg-stone-50 dark:hover:bg-white/[0.03]"><span className="min-w-0 flex-1 break-all font-mono text-xs">{op.title ?? op.path}</span><span className="text-xs text-accent-600 dark:text-accent-400">Ler</span></button></li>)}</ul>{matches.length > 8 && <div className="border-t border-stone-100 px-4 py-2 dark:border-stone-800"><Button variant="ghost" size="sm" onClick={() => setExpanded(!expanded)}>{expanded ? "Mostrar menos" : `Ver todas (${matches.length})`}</Button></div>}</> : <p className="p-5 text-sm text-stone-500">Nenhuma operação encontrada.</p>}
  </Panel>;
}

function RelationSection({ label, kind, rows, onSelect, inferred }: { label: string; kind: XrayKind; rows: Related[]; onSelect: (target: XrayTarget) => void; inferred: boolean }) {
  const [expanded, setExpanded] = useState(false);
  useEffect(() => setExpanded(false), [rows.length, label]);
  return <Panel className="overflow-hidden"><div className="flex items-start gap-3 border-b border-stone-200 px-5 py-4 dark:border-stone-800"><KindIcon kind={kind} size="sm" className="mt-0.5" /><div><h3 className="font-semibold">{label} <span className="font-normal text-stone-500">· {rows.length}</span></h3>{inferred && <p className="mt-1 text-xs text-stone-500 dark:text-stone-400">Relações inferidas por repositórios em comum; não comprovam vínculo direto.</p>}</div></div>
    {rows.length === 0 ? <p className="px-5 py-6 text-sm text-stone-500 dark:text-stone-400">Nenhum vínculo encontrado.</p> : <><ul className="stagger divide-y divide-stone-100 dark:divide-stone-800">{rows.slice(0, expanded ? undefined : 6).map(({node, origins, via}) => <li key={key(node)}><button type="button" onClick={() => onSelect(node)} className="flex w-full items-center gap-3 px-5 py-3 text-left transition hover:bg-stone-50 dark:hover:bg-white/[0.03]"><div className="min-w-0 flex-1"><p className="break-words text-sm font-medium">{node.title}</p><p className="truncate text-xs text-stone-500 dark:text-stone-400">{node.subtitle}</p><div className="mt-1 flex flex-wrap items-center gap-1">{via.length ? <span className="text-xs text-amber-700 dark:text-amber-400">Inferido via {via.join(", ")}</span> : <OriginBadges origins={origins} />}</div></div><ArrowRight className="size-4 shrink-0 text-stone-400" /></button></li>)}</ul>{rows.length > 6 && <div className="border-t border-stone-100 px-4 py-2 dark:border-stone-800"><Button variant="ghost" size="sm" onClick={() => setExpanded(!expanded)}>{expanded ? "Mostrar menos" : `Ver todos (${rows.length})`}</Button></div>}</>}
  </Panel>;
}
