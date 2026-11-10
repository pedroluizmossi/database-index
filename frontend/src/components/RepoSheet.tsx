import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity,
  BookOpen,
  Braces,
  ChevronRight,
  Compass,
  FileCode2,
  FileSearch,
  Folder,
  FolderOpen,
  GitBranch,
  IdCard,
  Network,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { ApiImpl, Repo, RepoCardInfo, RepoFile, SdlService, SummaryCoverageRepo } from "../api/types";
import { ORIGIN_HINT, ORIGIN_LABEL } from "../lib/ownership";
import { cx, formatDateTime, plural, repoShortName, timeAgo } from "../lib/utils";
import type { FileTarget } from "./FileDrawer";
import { CoverageMeter } from "./summaries";
import { Alert, Badge, EmptyState, Panel, Skeleton, inputClass } from "./ui";

const numberFormat = new Intl.NumberFormat("pt-BR");
const STATUS_LABEL: Record<Repo["status"], string> = {
  indexed: "Indexado",
  pending: "Pendente",
  error: "Com erro",
  ignored: "Ignorado",
};
const SECTIONS = [
  { id: "o-que-e", label: "O que é", icon: IdCard },
  { id: "comecar", label: "Por onde começar", icon: Compass },
  { id: "apis", label: "APIs", icon: Braces },
  { id: "arquivos", label: "Arquivos", icon: FileCode2 },
  { id: "docs", label: "Documentação", icon: BookOpen },
  { id: "relacionados", label: "Relacionados", icon: Network },
  { id: "saude", label: "Enriquecimento", icon: Activity },
] as const;
type SectionId = (typeof SECTIONS)[number]["id"];

interface RepoSheetProps {
  repo: Repo;
  /** Repos que têm ficha (para os relacionados virarem links). */
  knownRepos: ReadonlySet<string>;
  onSelectRepo: (name: string) => void;
  onOpenFile: (target: FileTarget) => void;
  /** Páginas e APIs ligadas ao repo (listas montadas pelo Raio X, com a origem de cada vínculo). */
  docs: ReactNode;
  docCount: number;
  linkedApis: ReactNode;
  linkedApiCount: number;
}

/**
 * Ficha do repositório: tudo o que o índice sabe sobre um repo numa página só.
 * O que é (cartão), por onde começar, APIs, arquivos com resumo, documentação, relacionados e o estado
 * do enriquecimento. Só leitura.
 */
export function RepoSheet({ repo, knownRepos, onSelectRepo, onOpenFile, docs, docCount, linkedApis, linkedApiCount }: RepoSheetProps) {
  const [info, setInfo] = useState<RepoCardInfo | null>(null);
  const [infoState, setInfoState] = useState<"loading" | "ready" | "missing">("loading");
  const [files, setFiles] = useState<RepoFile[] | null>(null);
  const [filesError, setFilesError] = useState<string | null>(null);
  const [fileFilter, setFileFilter] = useState("");

  useEffect(() => {
    let active = true;
    setInfo(null);
    setInfoState("loading");
    setFiles(null);
    setFilesError(null);
    setFileFilter("");
    api
      .repoCard(repo.name)
      .then((data) => {
        if (!active) return;
        setInfo(data);
        setInfoState("ready");
      })
      .catch(() => active && setInfoState("missing")); // 404: fatos ainda não coletados
    api
      .repoFiles(repo.name)
      .then((data) => active && setFiles(data))
      .catch((err: unknown) => active && setFilesError(describeError(err)));
    return () => {
      active = false;
    };
  }, [repo.name]);

  const card = info?.card ?? null;
  const coverage = useMemo<SummaryCoverageRepo | null>(() => {
    if (!files) return null;
    const code = files.filter((f) => f.summarizable);
    const withSummary = code.filter((f) => f.summary);
    const stale = withSummary.filter((f) => f.stale).length;
    return { repo: repo.name, files: code.length, fresh: withSummary.length - stale, stale };
  }, [files, repo.name]);
  const operations = useMemo(() => (info ? info.facts.sdl.reduce((n, s) => n + opCount(s), 0) : 0), [info]);
  const open = (path: string, line?: number) =>
    onOpenFile({ repo: repo.name, path, highlight: line ? { start: line, end: line } : undefined });
  const jump = (id: SectionId) => document.getElementById(`ficha-${id}`)?.scrollIntoView({ block: "start", behavior: "smooth" });

  /** Entrypoint do cartão: abre o arquivo; se for uma pasta, filtra a árvore por ela. */
  function openEntrypoint(path: string) {
    const clean = path.replace(/\/+$/, "");
    if (files?.some((f) => f.path === clean)) open(clean);
    else {
      setFileFilter(clean);
      jump("arquivos");
    }
  }
  const entrypointKind = (path: string): "file" | "folder" | "missing" => {
    const clean = path.replace(/\/+$/, "");
    if (!files) return "file";
    if (files.some((f) => f.path === clean)) return "file";
    return files.some((f) => f.path.startsWith(`${clean}/`)) ? "folder" : "missing";
  };

  return (
    <div className="space-y-5">
      {/* 1. O que é */}
      <Panel className="overflow-hidden">
        <div id="ficha-o-que-e" className="scroll-mt-32 space-y-5 p-5 sm:p-6">
          <div className="flex items-start gap-3">
            <GitBranch className="mt-1 size-5 shrink-0 text-stone-400" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium text-stone-500 dark:text-stone-400">
                Repositório{card?.kind ? ` · ${card.kind}` : ""}
              </p>
              <h2 className="mt-1 text-xl font-semibold tracking-tight break-words">{repoShortName(repo.name, repo.org)}</h2>
              <p className="mt-1 text-sm break-all text-stone-500 dark:text-stone-400">{repo.org ?? "Repositório local"}</p>
            </div>
            <Badge tone={repo.status} dot>
              {STATUS_LABEL[repo.status]}
            </Badge>
          </div>

          {infoState === "loading" && <Skeleton className="h-16 w-full" />}
          {card ? (
            <p className="max-w-3xl text-[15px] leading-relaxed text-stone-800 dark:text-stone-100">{card.summary}</p>
          ) : (
            infoState !== "loading" && (
              <Alert tone="info">
                Este repo ainda não tem cartão. Um agent escreve com{" "}
                <code className="font-mono text-xs">uv run code-index enrich next --repo {repoShortName(repo.name, repo.org)}</code>.
              </Alert>
            )
          )}

          {card && card.capabilities.length > 0 && (
            <div>
              <h3 className="mb-2 text-xs font-medium text-stone-500 dark:text-stone-400">O que ele sabe fazer</h3>
              <ul className="grid gap-x-6 gap-y-1.5 text-sm text-stone-700 sm:grid-cols-2 dark:text-stone-200">
                {card.capabilities.map((c) => (
                  <li key={c} className="flex gap-2">
                    <span className="mt-2 size-1 shrink-0 rounded-full bg-accent-500" aria-hidden="true" />
                    {c}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <dl className="grid gap-x-8 gap-y-4 text-sm sm:grid-cols-2">
            <Fact label="Dono (domínio/serviço)">
              {repo.owners.length === 0 ? (
                <span className="text-stone-500 dark:text-stone-400">sem dono definido</span>
              ) : (
                <span className="flex flex-wrap items-center gap-1.5">
                  {repo.owners.map((o) => (
                    <Badge key={o} tone="accent">
                      {o}
                    </Badge>
                  ))}
                  {repo.owner_origin && (
                    <span className="text-xs text-stone-500 dark:text-stone-400" title={ORIGIN_HINT[repo.owner_origin]}>
                      {ORIGIN_LABEL[repo.owner_origin]}
                      {repo.owner_confidence ? ` · confiança ${repo.owner_confidence}` : ""}
                    </span>
                  )}
                </span>
              )}
            </Fact>
            <Fact label="Tamanho no índice">
              {repo.language ?? "—"} · {numberFormat.format(repo.files)} {plural(repo.files, "arquivo", "arquivos")} ·{" "}
              {numberFormat.format(repo.chunks)} {plural(repo.chunks, "trecho", "trechos")}
            </Fact>
            {card && card.domain_tags.length > 0 && (
              <Fact label="Assuntos" className="sm:col-span-2">
                <span className="flex flex-wrap gap-1.5">
                  {card.domain_tags.map((t) => (
                    <Badge key={t}>{t}</Badge>
                  ))}
                </span>
              </Fact>
            )}
          </dl>

          {card?.glossary && card.glossary.length > 0 && (
            <details className="group text-sm">
              <summary className="cursor-pointer text-xs font-medium text-stone-500 select-none hover:text-stone-700 dark:text-stone-400 dark:hover:text-stone-200">
                Glossário ({card.glossary.length} {plural(card.glossary.length, "termo", "termos")})
              </summary>
              <ul className="mt-2 grid gap-x-6 gap-y-1 text-stone-700 sm:grid-cols-2 dark:text-stone-200">
                {card.glossary.map((g) => (
                  <li key={g}>{g}</li>
                ))}
              </ul>
            </details>
          )}
        </div>

        <nav aria-label="Seções da ficha" className="flex flex-wrap gap-1.5 border-t border-stone-200 bg-stone-50/70 px-4 py-2.5 dark:border-stone-800 dark:bg-stone-950/40">
          {SECTIONS.slice(1).map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => jump(id)}
              className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-stone-600 transition hover:bg-sheet hover:text-accent-600 dark:text-stone-300 dark:hover:bg-stone-800 dark:hover:text-accent-400"
            >
              <Icon className="size-3.5" aria-hidden="true" />
              {label}
              <SectionCount
                value={
                  id === "comecar" ? (card?.entrypoints?.length ?? null)
                  : id === "apis" ? (info ? operations : null)
                  : id === "arquivos" ? (files?.length ?? null)
                  : id === "docs" ? docCount
                  : id === "relacionados" ? (info?.related.length ?? null)
                  : null
                }
              />
            </button>
          ))}
        </nav>
      </Panel>

      {/* 2. Por onde começar */}
      <Section id="comecar" icon={Compass} title="Por onde começar" hint="Arquivos e pastas que o cartão indica para a primeira leitura.">
        {!card?.entrypoints?.length ? (
          <Empty>{card ? "O cartão não indica pontos de entrada." : "Aparece quando o repo tiver cartão."}</Empty>
        ) : (
          <ul className="divide-y divide-stone-100 dark:divide-stone-800">
            {card.entrypoints.map((path) => {
              const kind = entrypointKind(path);
              const Icon = kind === "folder" ? Folder : FileCode2;
              return (
                <li key={path}>
                  <button
                    type="button"
                    disabled={kind === "missing"}
                    onClick={() => openEntrypoint(path)}
                    title={kind === "missing" ? "Este caminho não está no índice" : kind === "folder" ? "Ver os arquivos desta pasta" : "Abrir o arquivo"}
                    className="flex w-full items-center gap-3 px-5 py-2.5 text-left transition hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-white/[0.03]"
                  >
                    <Icon className="size-4 shrink-0 text-stone-400" aria-hidden="true" />
                    <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-stone-700 dark:text-stone-200">{path}</span>
                    <FileSummaryHint file={files?.find((f) => f.path === path.replace(/\/+$/, ""))} />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </Section>

      {/* 3. APIs */}
      <Section
        id="apis"
        icon={Braces}
        title="APIs"
        count={info ? operations : undefined}
        hint="Operações declaradas no contrato (.sdl) e o handler que implementa cada uma, quando foi encontrado pelo nome."
      >
        {infoState === "loading" && <Skeleton className="m-5 h-16" />}
        {info && info.facts.sdl.length === 0 && <Empty>Este repo não declara operações (sem arquivo .sdl).</Empty>}
        {infoState === "missing" && <Empty>Os fatos deste repo ainda não foram coletados.</Empty>}
        {info?.facts.sdl.map((service) => (
          <SdlServiceBlock key={`${service.path}|${service.service}`} service={service} impls={info.impls} onOpen={open} />
        ))}
      </Section>
      {linkedApiCount > 0 && linkedApis}

      {/* 4. Arquivos */}
      <Section
        id="arquivos"
        icon={FileCode2}
        title="Arquivos"
        count={files?.length}
        hint="A árvore indexada do repo. Arquivos de código trazem o resumo do que fazem, quando já foi escrito."
      >
        {filesError && (
          <div className="p-5">
            <Alert>{filesError}</Alert>
          </div>
        )}
        {!files && !filesError && <Skeleton className="m-5 h-40" />}
        {files && <FileExplorer key={repo.name} files={files} filter={fileFilter} onFilter={setFileFilter} onOpen={(path) => open(path)} />}
      </Section>

      {/* 5. Documentação ligada */}
      <div id="ficha-docs" className="scroll-mt-32">
        {docs}
      </div>

      {/* 6. Repos relacionados */}
      <Section
        id="relacionados"
        icon={Network}
        title="Repos relacionados"
        count={info?.related.length}
        hint="Indicados pelo cartão (referência vista no código ou pelo nome) e pelas relações do índice."
      >
        {!info?.related.length ? (
          <Empty>Nenhum repo relacionado conhecido.</Empty>
        ) : (
          <ul className="divide-y divide-stone-100 dark:divide-stone-800">
            {info.related.map((r) => {
              const known = knownRepos.has(r.repo);
              return (
                <li key={`${r.repo}|${r.origin}`}>
                  <button
                    type="button"
                    disabled={!known}
                    onClick={() => onSelectRepo(r.repo)}
                    title={known ? "Abrir a ficha deste repo" : "Repo fora do índice"}
                    className="flex w-full items-center gap-3 px-5 py-3 text-left transition hover:bg-stone-50 disabled:cursor-default disabled:hover:bg-transparent dark:hover:bg-white/[0.03]"
                  >
                    <GitBranch className="size-4 shrink-0 text-stone-400" aria-hidden="true" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-stone-900 dark:text-stone-100">{r.repo.split("__").pop()}</span>
                      {r.why && <span className="block text-xs text-stone-500 dark:text-stone-400">{r.why}</span>}
                    </span>
                    {r.basis && <Badge tone={r.basis === "codigo" ? "indexed" : "neutral"}>{r.basis === "codigo" ? "visto no código" : "pelo nome"}</Badge>}
                    {known && <ChevronRight className="size-4 shrink-0 text-stone-400" aria-hidden="true" />}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </Section>

      {/* 7. Saúde do enriquecimento */}
      <Section id="saude" icon={Activity} title="Enriquecimento" hint="O que agents já escreveram sobre este repo e o que ainda falta.">
        <dl className="divide-y divide-stone-100 dark:divide-stone-800">
          <Health
            label="Cartão do repo"
            tone={!card ? "missing" : info?.stale ? "stale" : "ok"}
            status={!card ? "sem cartão" : info?.stale ? "desatualizado" : "em dia"}
            detail={
              card
                ? `${info?.card_agent ? `${info.card_agent} · ` : ""}${formatDateTime(info?.card_at ?? null)}${
                    info?.stale ? " · o repo mudou depois que o cartão foi escrito" : ""
                  }`
                : "Resumo, capacidades e relacionados aparecem quando um agent escrever o cartão."
            }
            command={!card || info?.stale ? `uv run code-index enrich next --repo ${repoShortName(repo.name, repo.org)}` : undefined}
          />
          <Health
            label="Resumos dos arquivos"
            tone={!coverage || coverage.files === 0 ? "none" : coverage.fresh === coverage.files ? "ok" : coverage.fresh + coverage.stale === 0 ? "missing" : "stale"}
            status={
              !coverage ? "carregando"
              : coverage.files === 0 ? "sem arquivos de código"
              : `${numberFormat.format(coverage.fresh + coverage.stale)} de ${numberFormat.format(coverage.files)}`
            }
            detail={coverage && coverage.files > 0 ? <CoverageMeter coverage={coverage} /> : "Só arquivos de código entram na fila de resumos."}
            command={
              coverage && coverage.files > 0 && coverage.fresh < coverage.files
                ? `uv run code-index enrich next --kind file_summaries --repo ${repoShortName(repo.name, repo.org)}`
                : undefined
            }
          />
          <Health
            label="Documentação ligada"
            tone={docCount > 0 ? "ok" : "none"}
            status={`${numberFormat.format(docCount)} ${plural(docCount, "página", "páginas")}`}
            detail={`${numberFormat.format(linkedApiCount)} ${plural(linkedApiCount, "API documentada", "APIs documentadas")} · vínculos são revistos na aba Documentação.`}
          />
          <Health
            label="Índice"
            tone={repo.status === "indexed" && !repo.outdated ? "ok" : repo.status === "error" ? "missing" : "stale"}
            status={repo.outdated ? "há push novo" : STATUS_LABEL[repo.status].toLowerCase()}
            detail={`${repo.indexed_at ? `indexado ${timeAgo(repo.indexed_at)}` : "ainda não indexado"}${repo.commit ? ` · commit ${repo.commit.slice(0, 7)}` : ""}${
              info?.facts_at ? ` · fatos de ${formatDateTime(info.facts_at)}` : ""
            }`}
          />
        </dl>
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------- peças

function Section({ id, icon: Icon, title, count, hint, children }: { id: SectionId; icon: LucideIcon; title: string; count?: number; hint: string; children: ReactNode }) {
  return (
    <Panel className="overflow-hidden">
      <div id={`ficha-${id}`} className="scroll-mt-32">
        <div className="flex items-start gap-3 border-b border-stone-200 px-5 py-4 dark:border-stone-800">
          <Icon className="mt-0.5 size-4 shrink-0 text-accent-500" aria-hidden="true" />
          <div className="min-w-0">
            <h3 className="font-semibold">
              {title}
              {count !== undefined && <span className="ml-2 font-normal text-stone-400 tabular-nums">{numberFormat.format(count)}</span>}
            </h3>
            <p className="mt-0.5 text-xs text-stone-500 dark:text-stone-400">{hint}</p>
          </div>
        </div>
        {children}
      </div>
    </Panel>
  );
}

function SectionCount({ value }: { value: number | null }) {
  if (value === null) return null;
  return <span className="text-stone-400 tabular-nums">{numberFormat.format(value)}</span>;
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="px-5 py-6 text-sm text-stone-500 dark:text-stone-400">{children}</p>;
}

function Fact({ label, className, children }: { label: string; className?: string; children: ReactNode }) {
  return (
    <div className={className}>
      <dt className="mb-1 text-xs font-medium text-stone-500 dark:text-stone-400">{label}</dt>
      <dd className="text-stone-800 dark:text-stone-100">{children}</dd>
    </div>
  );
}

const HEALTH_DOT = { ok: "bg-emerald-500", stale: "bg-amber-400", missing: "bg-rose-400", none: "bg-stone-300 dark:bg-stone-600" };

function Health({ label, tone, status, detail, command }: { label: string; tone: keyof typeof HEALTH_DOT; status: string; detail: ReactNode; command?: string }) {
  return (
    <div className="grid gap-x-6 gap-y-1.5 px-5 py-3.5 sm:grid-cols-[13rem_minmax(0,1fr)]">
      <dt className="flex items-center gap-2 text-sm font-medium">
        <span className={cx("size-2 shrink-0 rounded-full", HEALTH_DOT[tone])} aria-hidden="true" />
        {label}
      </dt>
      <dd className="min-w-0 space-y-1.5 text-sm">
        <p className="font-medium text-stone-800 dark:text-stone-100">{status}</p>
        <div className="text-xs text-stone-500 dark:text-stone-400">{detail}</div>
        {command && (
          <code className="block overflow-x-auto rounded-md bg-stone-100 px-2 py-1 font-mono text-[11px] whitespace-nowrap text-stone-700 dark:bg-stone-800 dark:text-stone-200">
            {command}
          </code>
        )}
      </dd>
    </div>
  );
}

function FileSummaryHint({ file }: { file: RepoFile | undefined }) {
  if (!file?.summary) return null;
  return (
    <span className="hidden max-w-[55%] items-center gap-1.5 truncate text-xs text-stone-500 md:inline-flex dark:text-stone-400" title={file.summary}>
      <Sparkles className="size-3 shrink-0 text-violet-500" aria-hidden="true" />
      <span className="truncate">{file.summary}</span>
    </span>
  );
}

// ---------------------------------------------------------------- APIs

const OP_GROUPS: { key: "queries" | "actions" | "signals" | "events"; label: string }[] = [
  { key: "queries", label: "Consultas" },
  { key: "actions", label: "Ações" },
  { key: "signals", label: "Sinais" },
  { key: "events", label: "Eventos" },
];

function opCount(s: SdlService): number {
  return s.queries.length + s.actions.length + s.signals.length + s.events.length;
}

function SdlServiceBlock({ service, impls, onOpen }: { service: SdlService; impls: ApiImpl[]; onOpen: (path: string, line?: number) => void }) {
  const byOp = useMemo(() => {
    const map = new Map<string, ApiImpl>();
    for (const i of impls) if (i.service === service.service && i.domain === service.domain) map.set(i.operation.toLowerCase(), i);
    return map;
  }, [impls, service]);
  const total = opCount(service);
  return (
    <div className="space-y-4 border-b border-stone-100 px-5 py-4 last:border-b-0 dark:border-stone-800">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Badge tone="accent">
          {service.domain}/{service.service}
        </Badge>
        <button type="button" onClick={() => onOpen(service.path)} className="font-mono text-xs text-stone-500 underline-offset-2 hover:text-accent-600 hover:underline dark:text-stone-400">
          {service.path}
        </button>
        <span className="ml-auto text-xs text-stone-500 dark:text-stone-400">
          {byOp.size} de {total} com handler localizado
        </span>
      </div>
      {OP_GROUPS.filter((g) => service[g.key].length > 0).map((g) => (
        <div key={g.key}>
          <p className="mb-1.5 text-xs font-medium text-stone-500 dark:text-stone-400">
            {g.label} <span className="font-normal tabular-nums">{service[g.key].length}</span>
          </p>
          <ul className="flex flex-wrap gap-1.5">
            {service[g.key].map((op) => {
              const impl = byOp.get(op.toLowerCase());
              return (
                <li key={op}>
                  {impl ? (
                    <button
                      type="button"
                      onClick={() => onOpen(impl.path, impl.line)}
                      title={`${impl.symbol} · ${impl.path}:${impl.line}`}
                      className="inline-flex items-center gap-1.5 rounded-md bg-emerald-500/10 px-2 py-1 font-mono text-xs text-emerald-800 ring-1 ring-emerald-600/20 transition ring-inset hover:bg-emerald-500/20 dark:text-emerald-300"
                    >
                      {op}
                      <span className="font-sans text-[11px] text-emerald-700/80 dark:text-emerald-400/80">→ {impl.symbol}</span>
                    </button>
                  ) : (
                    <span className="inline-flex rounded-md bg-stone-500/10 px-2 py-1 font-mono text-xs text-stone-700 ring-1 ring-stone-500/15 ring-inset dark:text-stone-300">{op}</span>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- árvore de arquivos

interface Dir {
  name: string;
  path: string;
  dirs: Dir[];
  files: RepoFile[];
  total: number;
  summarized: number;
}

/** Monta a árvore e junta pastas que só têm uma subpasta ("src/main/java/br/com") numa linha só. */
function buildTree(files: RepoFile[]): Dir {
  const root: Dir = { name: "", path: "", dirs: [], files: [], total: 0, summarized: 0 };
  const index = new Map<string, Dir>([["", root]]);
  for (const file of files) {
    const parts = file.path.split("/");
    let dir = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const path = parts.slice(0, i + 1).join("/");
      let next = index.get(path);
      if (!next) {
        next = { name: parts[i], path, dirs: [], files: [], total: 0, summarized: 0 };
        index.set(path, next);
        dir.dirs.push(next);
      }
      dir = next;
    }
    dir.files.push(file);
  }
  const finish = (dir: Dir): Dir => {
    dir.dirs = dir.dirs.map(finish);
    while (dir.path && dir.dirs.length === 1 && dir.files.length === 0) {
      const only = dir.dirs[0];
      dir = { ...only, name: `${dir.name}/${only.name}` };
    }
    dir.total = dir.files.length + dir.dirs.reduce((n, d) => n + d.total, 0);
    dir.summarized = dir.files.filter((f) => f.summary).length + dir.dirs.reduce((n, d) => n + d.summarized, 0);
    return dir;
  };
  return finish(root);
}

const FLAT_LIMIT = 150;

function FileExplorer({ files, filter, onFilter, onOpen }: { files: RepoFile[]; filter: string; onFilter: (value: string) => void; onOpen: (path: string) => void }) {
  const [onlySummarized, setOnlySummarized] = useState(false);
  const visible = useMemo(() => (onlySummarized ? files.filter((f) => f.summary) : files), [files, onlySummarized]);
  const tree = useMemo(() => buildTree(visible), [visible]);
  // Abre o primeiro nível: dá a visão do repo sem despejar tudo.
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(buildTree(files).dirs.map((d) => d.path)));
  const withSummary = useMemo(() => files.filter((f) => f.summary).length, [files]);
  const needle = filter.trim().toLowerCase();
  const matches = useMemo(
    () =>
      needle
        ? visible.filter(
            (f) => f.path.toLowerCase().includes(needle) || (f.summary ?? "").toLowerCase().includes(needle) || f.keywords.some((k) => k.toLowerCase().includes(needle)),
          )
        : null,
    [visible, needle],
  );
  const toggle = (path: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (!next.delete(path)) next.add(path);
      return next;
    });

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 border-b border-stone-100 px-5 py-3 dark:border-stone-800">
        <input
          type="search"
          value={filter}
          onChange={(e) => onFilter(e.target.value)}
          placeholder="Filtrar por caminho, resumo ou palavra-chave"
          aria-label="Filtrar arquivos"
          className={cx(inputClass, "h-9 max-w-md flex-1")}
        />
        <label className="inline-flex cursor-pointer items-center gap-2 text-xs font-medium text-stone-600 select-none dark:text-stone-300">
          <input type="checkbox" checked={onlySummarized} onChange={(e) => setOnlySummarized(e.target.checked)} className="size-3.5 accent-violet-600" />
          <Sparkles className="size-3.5 text-violet-500" aria-hidden="true" />
          Só com resumo
          <span className="font-normal text-stone-400 tabular-nums">{numberFormat.format(withSummary)}</span>
        </label>
      </div>

      {matches ? (
        matches.length === 0 ? (
          <EmptyState icon={FileSearch} title="Nenhum arquivo encontrado" text="Nenhum caminho, resumo ou palavra-chave combina com o filtro." />
        ) : (
          <>
            <ul className="divide-y divide-stone-100 dark:divide-stone-800">
              {matches.slice(0, FLAT_LIMIT).map((f) => (
                <FileRow key={f.path} file={f} label={f.path} depth={0} onOpen={onOpen} expandedSummary />
              ))}
            </ul>
            {matches.length > FLAT_LIMIT && (
              <p className="px-5 py-3 text-xs text-stone-500 dark:text-stone-400">
                Mostrando {FLAT_LIMIT} de {numberFormat.format(matches.length)}. Refine o filtro para ver os demais.
              </p>
            )}
          </>
        )
      ) : tree.total === 0 ? (
        <Empty>{onlySummarized ? "Nenhum arquivo deste repo tem resumo ainda." : "Nenhum arquivo indexado."}</Empty>
      ) : (
        <ul className="py-1.5">
          <DirContents dir={tree} depth={0} expanded={expanded} onToggle={toggle} onOpen={onOpen} />
        </ul>
      )}
    </div>
  );
}

function DirContents({ dir, depth, expanded, onToggle, onOpen }: { dir: Dir; depth: number; expanded: Set<string>; onToggle: (path: string) => void; onOpen: (path: string) => void }) {
  return (
    <>
      {dir.dirs.map((d) => {
        const isOpen = expanded.has(d.path);
        const FolderIcon = isOpen ? FolderOpen : Folder;
        return (
          <li key={d.path}>
            <button
              type="button"
              aria-expanded={isOpen}
              onClick={() => onToggle(d.path)}
              style={{ paddingLeft: `${1.25 + depth * 1.1}rem` }}
              className="flex w-full items-center gap-2 py-1.5 pr-5 text-left transition hover:bg-stone-50 dark:hover:bg-white/[0.03]"
            >
              <ChevronRight className={cx("size-3.5 shrink-0 text-stone-400 transition-transform", isOpen && "rotate-90")} aria-hidden="true" />
              <FolderIcon className="size-4 shrink-0 text-stone-400" aria-hidden="true" />
              <span className="min-w-0 truncate font-mono text-[13px] font-medium text-stone-800 dark:text-stone-100">{d.name}</span>
              <span className="ml-auto flex shrink-0 items-center gap-3 text-[11px] text-stone-400 tabular-nums">
                {d.summarized > 0 && (
                  <span className="inline-flex items-center gap-1 text-violet-600 dark:text-violet-300" title={`${d.summarized} com resumo`}>
                    <Sparkles className="size-3" aria-hidden="true" />
                    {numberFormat.format(d.summarized)}
                  </span>
                )}
                {numberFormat.format(d.total)} {plural(d.total, "arquivo", "arquivos")}
              </span>
            </button>
            {isOpen && (
              <ul>
                <DirContents dir={d} depth={depth + 1} expanded={expanded} onToggle={onToggle} onOpen={onOpen} />
              </ul>
            )}
          </li>
        );
      })}
      {dir.files.map((f) => (
        <FileRow key={f.path} file={f} label={f.path.split("/").pop() ?? f.path} depth={depth} onOpen={onOpen} />
      ))}
    </>
  );
}

function FileRow({ file, label, depth, onOpen, expandedSummary = false }: { file: RepoFile; label: string; depth: number; onOpen: (path: string) => void; expandedSummary?: boolean }) {
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(file.path)}
        title={file.path}
        style={{ paddingLeft: `${1.25 + depth * 1.1 + 1.375}rem` }}
        className="group flex w-full items-start gap-2 py-1.5 pr-5 text-left transition hover:bg-stone-50 dark:hover:bg-white/[0.03]"
      >
        <FileCode2 className={cx("mt-0.5 size-4 shrink-0", file.summary ? "text-violet-500" : "text-stone-300 dark:text-stone-600")} aria-hidden="true" />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2">
            <span className="min-w-0 truncate font-mono text-[13px] text-stone-800 group-hover:text-accent-600 dark:text-stone-100 dark:group-hover:text-accent-400">{label}</span>
            {file.stale && (
              <span className="shrink-0 text-[11px] font-medium text-amber-700 dark:text-amber-300" title="O arquivo mudou depois que o resumo foi escrito">
                resumo desatualizado
              </span>
            )}
          </span>
          {file.summary && (
            <span className={cx("mt-0.5 block text-xs leading-relaxed text-stone-600 dark:text-stone-300", !expandedSummary && "line-clamp-2")}>{file.summary}</span>
          )}
          {expandedSummary && file.keywords.length > 0 && (
            <span className="mt-1.5 flex flex-wrap gap-1.5">
              {file.keywords.slice(0, 6).map((k) => (
                <span key={k} className="rounded-full bg-violet-500/10 px-2 py-0.5 text-[11px] font-medium text-violet-700 ring-1 ring-violet-600/15 ring-inset dark:text-violet-300">
                  {k}
                </span>
              ))}
            </span>
          )}
        </span>
        <span className="mt-0.5 shrink-0 text-[11px] text-stone-400 tabular-nums">{numberFormat.format(file.lines)} linhas</span>
      </button>
    </li>
  );
}
