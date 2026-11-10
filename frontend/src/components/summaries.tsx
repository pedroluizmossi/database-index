import { useEffect, useMemo, useState, type ReactNode } from "react";
import { FileSearch, Sparkles } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { FileSummary, SummaryCoverage, SummaryCoverageRepo } from "../api/types";
import { cx, formatDateTime, plural } from "../lib/utils";
import { Dialog } from "./Dialog";
import { Alert, Button, EmptyState, Skeleton, inputClass } from "./ui";

/**
 * Resumos de arquivos escritos por agent (`enrich next --kind file_summaries`).
 * Tudo o que vem deles usa a mesma marca (ícone de brilho + violeta), para ser reconhecido de relance
 * e não se confundir com o que saiu do código.
 */
const TINT = "border-violet-200 bg-violet-50/70 dark:border-violet-500/25 dark:bg-violet-500/10";
const INK = "text-violet-700 dark:text-violet-300";
const numberFormat = new Intl.NumberFormat("pt-BR");
const ORIGIN_HINT = "Escrito por um agent a partir do código, em português. Entra na busca por termos; pode conter erros.";

/** Selo "Resumo": identifica o texto como descrição gerada, não como conteúdo do arquivo. */
export function SummaryMark({ label = "Resumo", className }: { label?: string; className?: string }) {
  return (
    <span
      title={ORIGIN_HINT}
      className={cx("inline-flex shrink-0 items-center gap-1 text-[11px] font-semibold tracking-wide uppercase", INK, className)}
    >
      <Sparkles className="size-3.5" aria-hidden="true" />
      {label}
    </span>
  );
}

export function KeywordChips({ keywords, max }: { keywords: string[]; max?: number }) {
  const shown = max ? keywords.slice(0, max) : keywords;
  const hidden = keywords.length - shown.length;
  if (shown.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Palavras-chave do resumo">
      {shown.map((k) => (
        <li
          key={k}
          className="rounded-full bg-violet-500/10 px-2 py-0.5 text-[11px] font-medium text-violet-700 ring-1 ring-violet-600/15 ring-inset dark:text-violet-300 dark:ring-violet-400/20"
        >
          {k}
        </li>
      ))}
      {hidden > 0 && (
        <li className="px-1 py-0.5 text-[11px] text-stone-500 dark:text-stone-400" title={keywords.slice(shown.length).join(", ")}>
          +{hidden}
        </li>
      )}
    </ul>
  );
}

/** Faixa do resumo num resultado de busca: o que o arquivo faz, antes do trecho de código. */
export function HitSummary({ summary, keywords }: { summary: string; keywords: string[] }) {
  return (
    <div className={cx("mx-4 mb-3 space-y-2 rounded-lg border px-3 py-2.5", TINT)}>
      <p className="text-sm leading-relaxed text-stone-700 dark:text-stone-200">
        <SummaryMark className="mr-2 align-[-2px]" />
        {summary}
      </p>
      <KeywordChips keywords={keywords} max={6} />
    </div>
  );
}

/** Painel do resumo no arquivo aberto: texto, perguntas que o arquivo responde e palavras-chave. */
export function FileSummaryPanel({ repo, path }: { repo: string; path: string }) {
  const [data, setData] = useState<FileSummary | null>(null);

  useEffect(() => {
    let active = true;
    setData(null);
    // Sem resumo (ou com erro) o painel simplesmente não aparece: o arquivo é o conteúdo principal.
    api
      .fileSummary(repo, path)
      .then((s) => active && setData(s))
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [repo, path]);

  if (!data) return null;
  return (
    <section aria-label="Resumo do arquivo" className={cx("space-y-3 border-b px-5 py-4", TINT)}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <SummaryMark label="Resumo do arquivo" />
        {data.stale && (
          <span
            className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-amber-600/20 ring-inset dark:text-amber-300"
            title="O arquivo mudou depois que este resumo foi escrito. Ele volta para a fila de resumos."
          >
            arquivo mudou desde o resumo
          </span>
        )}
        <span className="ml-auto text-[11px] text-stone-500 dark:text-stone-400">
          {data.agent ? `${data.agent} · ` : ""}
          {formatDateTime(data.created_at)}
        </span>
      </div>
      <p className="max-w-3xl text-sm leading-relaxed text-stone-800 dark:text-stone-100">{data.summary}</p>
      {data.questions.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium text-stone-500 dark:text-stone-400">Perguntas que este arquivo responde</p>
          <ul className="space-y-0.5 text-sm text-stone-700 dark:text-stone-200">
            {data.questions.map((q) => (
              <li key={q} className="flex gap-2">
                <span className={cx("select-none", INK)} aria-hidden="true">
                  ›
                </span>
                {q}
              </li>
            ))}
          </ul>
        </div>
      )}
      <KeywordChips keywords={data.keywords} />
    </section>
  );
}

// ---------------------------------------------------------------- cobertura por repo

let coverageRequest: Promise<Map<string, SummaryCoverageRepo>> | null = null;

/** Uma requisição por carregamento da página, compartilhada por todas as linhas da tabela. */
function loadCoverage(): Promise<Map<string, SummaryCoverageRepo>> {
  coverageRequest ??= api
    .summaryCoverage()
    .then((c: SummaryCoverage) => new Map(c.repos.map((r) => [r.repo, r])))
    .catch(() => {
      coverageRequest = null; // tenta de novo na próxima montagem
      return new Map<string, SummaryCoverageRepo>();
    });
  return coverageRequest;
}

function useRepoCoverage(repo: string): SummaryCoverageRepo | null {
  const [row, setRow] = useState<SummaryCoverageRepo | null>(null);
  useEffect(() => {
    let active = true;
    void loadCoverage().then((map) => active && setRow(map.get(repo) ?? null));
    return () => {
      active = false;
    };
  }, [repo]);
  return row;
}

/**
 * Atalho na linha do repo: quantos arquivos de código têm resumo. Abre a lista dos resumos.
 * Não aparece em repos sem arquivos de código nem enquanto nenhum arquivo do repo foi resumido.
 */
export function RepoSummariesLink({ repo, className }: { repo: string; className?: string }) {
  const row = useRepoCoverage(repo);
  const [open, setOpen] = useState(false);
  if (!row || row.fresh + row.stale === 0) return null;
  const done = row.fresh + row.stale;
  const percent = Math.round((done / row.files) * 100);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={`${numberFormat.format(done)} de ${numberFormat.format(row.files)} arquivos de código com resumo${
          row.stale ? ` (${numberFormat.format(row.stale)} ${plural(row.stale, "desatualizado", "desatualizados")})` : ""
        }`}
        className={cx(className, "hover:text-violet-600! dark:hover:text-violet-300!")}
      >
        <Sparkles className={cx("size-3.5", INK)} aria-hidden="true" />
        Resumos
        <span className={cx("font-semibold tabular-nums", INK)}>{percent}%</span>
      </button>
      {open && <RepoSummariesDialog repo={repo} coverage={row} open onClose={() => setOpen(false)} />}
    </>
  );
}

export function CoverageMeter({ coverage }: { coverage: SummaryCoverageRepo }) {
  const done = coverage.fresh + coverage.stale;
  const width = (n: number) => `${coverage.files ? (n / coverage.files) * 100 : 0}%`;
  return (
    <div className="space-y-1.5">
      <div
        className="flex h-1.5 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-800"
        role="img"
        aria-label={`${done} de ${coverage.files} arquivos de código com resumo`}
      >
        <span className="h-full bg-violet-500" style={{ width: width(coverage.fresh) }} />
        <span className="h-full bg-amber-400" style={{ width: width(coverage.stale) }} />
      </div>
      <p className="text-xs text-stone-500 dark:text-stone-400">
        <Legend dot="bg-violet-500">{numberFormat.format(coverage.fresh)} em dia</Legend>
        {coverage.stale > 0 && <Legend dot="bg-amber-400">{numberFormat.format(coverage.stale)} desatualizados</Legend>}
        <Legend dot="bg-stone-300 dark:bg-stone-700">
          {numberFormat.format(coverage.files - done)} sem resumo
        </Legend>
      </p>
    </div>
  );
}

function Legend({ dot, children }: { dot: string; children: ReactNode }) {
  return (
    <span className="mr-4 inline-flex items-center gap-1.5 whitespace-nowrap">
      <span className={cx("size-1.5 rounded-full", dot)} aria-hidden="true" />
      {children}
    </span>
  );
}

/** Resumos dos arquivos de um repo, com filtro por caminho, texto ou palavra-chave. Só leitura. */
export function RepoSummariesDialog({
  repo,
  coverage,
  open,
  onClose,
}: {
  repo: string;
  coverage: SummaryCoverageRepo;
  open: boolean;
  onClose: () => void;
}) {
  const [items, setItems] = useState<FileSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    if (!open) return;
    let active = true;
    setItems(null);
    setError(null);
    api
      .repoSummaries(repo)
      .then((data) => active && setItems(data))
      .catch((err: unknown) => active && setError(describeError(err)));
    return () => {
      active = false;
    };
  }, [open, repo]);

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!items || !needle) return items;
    return items.filter(
      (s) =>
        s.path.toLowerCase().includes(needle) ||
        s.summary.toLowerCase().includes(needle) ||
        s.keywords.some((k) => k.toLowerCase().includes(needle)),
    );
  }, [items, filter]);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Resumos dos arquivos"
      description={repo}
      size="xl"
      footer={
        <Button variant="ghost" onClick={onClose}>
          Fechar
        </Button>
      }
    >
      <div className="space-y-4">
        <CoverageMeter coverage={coverage} />
        <p className="text-xs text-stone-500 dark:text-stone-400">{ORIGIN_HINT}</p>
        {error && <Alert>{error}</Alert>}
        {!items && !error && <Skeleton className="h-40 w-full" />}
        {items && (
          <input
            type="search"
            data-autofocus
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filtrar por caminho, texto ou palavra-chave"
            aria-label="Filtrar resumos"
            className={inputClass}
          />
        )}
        {shown && shown.length === 0 && (
          <EmptyState
            icon={FileSearch}
            title="Nenhum resumo encontrado"
            text={items?.length ? "Nenhum resumo combina com o filtro." : "Este repo ainda não tem arquivos resumidos."}
          />
        )}
        {shown && shown.length > 0 && (
          <ul className="max-h-[55dvh] divide-y divide-stone-100 overflow-y-auto dark:divide-stone-800">
            {shown.map((s) => (
              <li key={s.path} className="space-y-1.5 py-3">
                <p className="truncate font-mono text-xs text-stone-500 dark:text-stone-400" title={s.path}>
                  {s.path}
                </p>
                <p className="text-sm leading-relaxed text-stone-800 dark:text-stone-100">{s.summary}</p>
                <KeywordChips keywords={s.keywords} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </Dialog>
  );
}
