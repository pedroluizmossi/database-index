import { useCallback, useEffect, useState } from "react";
import { Check, ExternalLink, Sparkles, X } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { DocSuggestion } from "../api/types";
import { useToast } from "./Toaster";
import { Alert, Badge, IconButton, Panel } from "./ui";

const CONFIDENCE_TONE = { alta: "indexed", media: "warning", baixa: "neutral" } as const;
const shortName = (repo: string) => repo.split("__").pop() ?? repo;

/** Vínculos página → repo sugeridos por LLM (`code-index enrich next --kind doc_links`). Admin aprova ou rejeita. */
export function DocSuggestionsPanel({ admin, onChanged }: { admin: boolean; onChanged: () => void }) {
  const { push } = useToast();
  const [items, setItems] = useState<DocSuggestion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await api.docSuggestions());
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function review(s: DocSuggestion, approve: boolean) {
    const key = `${s.doc_repo}|${s.doc_path}|${s.repo}`;
    setBusy(key);
    try {
      await api.reviewDocSuggestion({ doc_repo: s.doc_repo, doc_path: s.doc_path, repo: s.repo, approve });
      setItems((prev) => (prev ?? []).filter((x) => `${x.doc_repo}|${x.doc_path}|${x.repo}` !== key));
      if (approve) onChanged();
    } catch (err) {
      push({ tone: "error", title: "Não foi possível revisar", description: describeError(err) });
    } finally {
      setBusy(null);
    }
  }

  if (error) return <Alert>{error}</Alert>;
  if (!items || items.length === 0) return null;

  return (
    <Panel className="p-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="flex items-center gap-2 font-semibold">
          <Sparkles className="size-4 text-accent-500" aria-hidden="true" />
          Sugestões de vínculo ({items.length})
        </h2>
        <p className="text-xs text-stone-500 dark:text-stone-400">
          Feitas por agent a partir da página e do cartão do repo. Só valem depois de aprovadas: “remover” tira um vínculo automático (menção ou tag).
        </p>
      </div>
      <ul className="max-h-[28rem] divide-y divide-stone-100 overflow-y-auto dark:divide-stone-800">
        {items.map((s) => {
          const key = `${s.doc_repo}|${s.doc_path}|${s.repo}`;
          return (
            <li key={key} className="flex items-start gap-3 py-2.5">
              <div className="min-w-0 flex-1 space-y-1 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  {s.url ? (
                    <a href={s.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 font-medium text-accent-600 hover:underline dark:text-accent-400">
                      {s.title ?? s.doc_path}
                      <ExternalLink className="size-3" aria-hidden="true" />
                    </a>
                  ) : (
                    <span className="font-medium">{s.title ?? s.doc_path}</span>
                  )}
                  <Badge tone={s.action === "remove" ? "error" : "accent"}>{s.action === "remove" ? "remover" : "adicionar"}</Badge>
                  <span className="text-stone-400">→</span>
                  <span className={s.action === "remove" ? "font-medium line-through decoration-rose-400" : "font-medium"} title={s.repo}>
                    {shortName(s.repo)}
                  </span>
                  {s.confidence && <Badge tone={CONFIDENCE_TONE[s.confidence]}>{s.confidence}</Badge>}
                  {s.basis && <Badge>{s.basis === "pagina_cita" ? "a página cita" : "mesmo assunto"}</Badge>}
                </div>
                <p className="text-xs text-stone-600 dark:text-stone-300">{s.reason}</p>
                <p className="truncate font-mono text-xs text-stone-400" title={s.doc_path}>{s.doc_path}</p>
              </div>
              {admin && (
                <div className="flex shrink-0 gap-1">
                  <IconButton label={`Aprovar ${s.repo}`} onClick={() => busy !== key && void review(s, true)} className="hover:text-emerald-600">
                    <Check className="size-4" />
                  </IconButton>
                  <IconButton label={`Rejeitar ${s.repo}`} onClick={() => busy !== key && void review(s, false)} className="hover:text-rose-600">
                    <X className="size-4" />
                  </IconButton>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
