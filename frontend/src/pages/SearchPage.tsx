import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Search, SearchX, Sparkles } from "lucide-react";
import { ApiError, describeError } from "../api/client";
import { api } from "../api/endpoints";
import type { Hit, SearchMode } from "../api/types";
import { useToast } from "../components/Toaster";
import { ResultCard } from "../components/ResultCard";
import { FileDrawer, type FileTarget } from "../components/FileDrawer";
import { Button, EmptyState, Field, Kbd, Panel, Segmented, Select, Skeleton, Switch, inputClass } from "../components/ui";
import { useIndex } from "../data/IndexProvider";

const MODES: { value: SearchMode; label: string; help: string }[] = [
  { value: "hybrid", label: "Híbrida", help: "Combina significado com identificadores exatos. Recomendada." },
  { value: "semantic", label: "Semântica", help: "Procura pelo significado. Descreva o comportamento que você quer achar." },
  { value: "keyword", label: "Palavras-chave", help: "Procura o texto literal. Bom para nomes de classes, funções e constantes." },
];

const TOP_K = [8, 16, 30];

interface Results {
  query: string;
  hits: Hit[];
}

export function SearchPage() {
  const { repos } = useIndex();
  const { push } = useToast();
  const inputRef = useRef<HTMLInputElement>(null);

  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [repo, setRepo] = useState("");
  const [language, setLanguage] = useState("");
  const [pathGlob, setPathGlob] = useState("");
  const [topK, setTopK] = useState(8);
  const [rerank, setRerank] = useState(true);
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<Results | null>(null);
  const [target, setTarget] = useState<FileTarget | null>(null);

  const indexed = useMemo(() => (repos ?? []).filter((r) => r.status === "indexed"), [repos]);
  const maxScore = useMemo(() => Math.max(0, ...(results?.hits ?? []).map((h) => h.score)), [results]);
  const modeHelp = MODES.find((m) => m.value === mode)?.help ?? "";

  // "/" foca a busca, como em outros buscadores de código.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "/" || isTyping(event.target)) return;
      event.preventDefault();
      inputRef.current?.focus();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  async function runSearch(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const text = query.trim();
    if (!text || loading) return;
    setLoading(true);
    try {
      const hits = await api.search({
        query: text,
        mode,
        top_k: topK,
        rerank,
        repos: repo ? [repo] : null,
        language: language.trim() || null,
        path_glob: pathGlob.trim() || null,
      });
      setResults({ query: text, hits });
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 401)) {
        push({ tone: "error", title: "A busca falhou", description: describeError(err) });
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-10">
      <section className="mx-auto max-w-3xl text-center">
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Pergunte ao código</h1>
        <p className="mt-3 text-zinc-500 dark:text-zinc-400">
          {indexed.length > 0
            ? `Busca em ${indexed.length} repositório(s) indexado(s).`
            : "Nenhum repositório indexado ainda."}{" "}
          Descreva o que procura ou digite um identificador.
        </p>

        <form
          onSubmit={runSearch}
          className="group mx-auto mt-8 flex items-center gap-2 rounded-2xl border border-zinc-200 bg-white p-2 shadow-xl shadow-zinc-900/5 transition focus-within:border-indigo-500 focus-within:ring-4 focus-within:ring-indigo-500/15 dark:border-zinc-800 dark:bg-zinc-900"
        >
          <Search className="ml-3 size-5 shrink-0 text-zinc-400 transition group-focus-within:text-indigo-500" aria-hidden="true" />
          <input
            ref={inputRef}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="ex.: onde o token expirado é rejeitado"
            aria-label="Texto da busca"
            className="h-11 min-w-0 flex-1 bg-transparent text-base text-zinc-900 outline-none placeholder:text-zinc-400 dark:text-zinc-100"
          />
          <Kbd className="hidden sm:inline-flex">/</Kbd>
          <Button type="submit" loading={loading} disabled={!query.trim()} className="h-11 px-5">
            Buscar
          </Button>
        </form>
      </section>

      <section className="mx-auto max-w-4xl">
        <Panel className="space-y-5 p-5">
          <div className="flex flex-col gap-2">
            <Segmented label="Modo de busca" value={mode} onChange={setMode} options={MODES} />
            <p className="text-xs text-zinc-500 dark:text-zinc-400">{modeHelp}</p>
          </div>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Repositório">
              <Select value={repo} onChange={setRepo}>
                <option value="">Todos</option>
                {indexed.map((r) => (
                  <option key={r.name} value={r.name}>
                    {r.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Linguagem">
              <input
                className={inputClass}
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                placeholder="java, python…"
              />
            </Field>
            <Field label="Caminho (glob)">
              <input
                className={inputClass}
                value={pathGlob}
                onChange={(e) => setPathGlob(e.target.value)}
                placeholder="src/**/*.ts"
              />
            </Field>
            <Field label="Resultados">
              <Select value={String(topK)} onChange={(v) => setTopK(Number(v))}>
                {TOP_K.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </Select>
            </Field>
          </div>

          <div className="flex items-center justify-between gap-4 border-t border-zinc-200/80 pt-4 dark:border-zinc-800">
            <div>
              <p className="text-sm font-medium">Reordenar com rerank</p>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                Um modelo da Voyage reavalia os melhores trechos. Mais preciso, um pouco mais lento.
              </p>
            </div>
            <Switch checked={rerank} onChange={setRerank} label="Reordenar com rerank" />
          </div>
        </Panel>
      </section>

      <section aria-live="polite" className="mx-auto max-w-4xl">
        {loading && <ResultsSkeleton />}

        {!loading && results && (
          <>
            <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2 text-sm text-zinc-500 dark:text-zinc-400">
              <p>
                <span className="font-semibold text-zinc-900 dark:text-zinc-100">{results.hits.length}</span>{" "}
                trecho(s) para <span className="text-zinc-900 dark:text-zinc-100">“{results.query}”</span>
              </p>
              {results.hits.length > 0 && <p className="text-xs">ordenados por relevância</p>}
            </div>

            {results.hits.length === 0 ? (
              <Panel>
                <EmptyState
                  icon={SearchX}
                  title="Nada encontrado"
                  text="Tente outra descrição, use o modo palavras-chave ou remova filtros."
                />
              </Panel>
            ) : (
              <div className="grid gap-4">
                {results.hits.map((hit, i) => (
                  <ResultCard
                    key={`${i}-${hit.repo}-${hit.path}-${hit.start_line}`}
                    hit={hit}
                    maxScore={maxScore}
                    onOpen={() =>
                      setTarget({
                        repo: hit.repo,
                        path: hit.path,
                        highlight: { start: hit.start_line, end: hit.end_line },
                      })
                    }
                  />
                ))}
              </div>
            )}
          </>
        )}

        {!loading && !results && (
          <Panel>
            <EmptyState
              icon={Sparkles}
              title="Comece pela descrição"
              text="Pergunte pelo significado (ex.: “onde validamos o JWT”) ou digite um identificador (ex.: UserRepository)."
            />
          </Panel>
        )}
      </section>

      <FileDrawer target={target} onClose={() => setTarget(null)} />
    </div>
  );
}

function ResultsSkeleton() {
  return (
    <div className="grid gap-4">
      {[0, 1, 2].map((i) => (
        <Panel key={i} className="space-y-3 p-4">
          <div className="flex gap-3">
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-5 w-64" />
          </div>
          <Skeleton className="h-24 w-full" />
        </Panel>
      ))}
    </div>
  );
}

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}
