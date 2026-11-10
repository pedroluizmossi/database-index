import { useEffect, useState, type ReactNode } from "react";
import { IdCard } from "lucide-react";
import { api } from "../api/endpoints";
import { ApiError, describeError } from "../api/client";
import type { RepoCardInfo } from "../api/types";
import { Dialog } from "./Dialog";
import { Alert, Badge, Button, EmptyState, Skeleton } from "./ui";
import { formatDateTime } from "../lib/utils";

const shortName = (repo: string) => repo.split("__").pop() ?? repo;

const KIND_LABEL: Record<string, string> = {
  service: "serviço",
  library: "biblioteca",
  frontend: "frontend",
  infra: "infra",
  docs: "documentação",
  tool: "ferramenta",
  other: "outro",
};

/** Cartão do repo: resumo escrito por agent + fatos extraídos sem LLM. Só leitura. */
export function RepoCardDialog({ repo, open, onClose }: { repo: string; open: boolean; onClose: () => void }) {
  const [info, setInfo] = useState<RepoCardInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    if (!open) return;
    let active = true;
    setInfo(null);
    setError(null);
    setMissing(false);
    api
      .repoCard(repo)
      .then((data) => active && setInfo(data))
      .catch((err) => {
        if (!active) return;
        if (err instanceof ApiError && err.status === 404) setMissing(true);
        else setError(describeError(err));
      });
    return () => {
      active = false;
    };
  }, [open, repo]);

  const card = info?.card ?? null;
  const implByOp = new Map((info?.impls ?? []).map((i) => [`${i.service}|${i.operation}`, i]));
  const facts = info?.facts;
  const catalog = facts?.catalog ?? {};
  const catalogBits = ["owner", "lifecycle", "type", "system"]
    .filter((k) => typeof catalog[k] === "string")
    .map((k) => `${k}: ${String(catalog[k])}`);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Cartão do repo"
      description={repo}
      size="xl"
      footer={
        <Button variant="ghost" onClick={onClose}>
          Fechar
        </Button>
      }
    >
      {error && <Alert>{error}</Alert>}
      {!info && !error && !missing && <Skeleton className="h-32 w-full" />}
      {missing && (
        <EmptyState
          icon={IdCard}
          title="Sem cartão ainda"
          text="Os fatos saem de `code-index enrich facts` e o resumo é escrito por um agent com `code-index enrich next`."
        />
      )}
      {info && (
        <div className="space-y-5 text-[0.95rem]">
          {card ? (
            <section className="space-y-2">
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge tone="accent">{KIND_LABEL[card.kind] ?? card.kind}</Badge>
                {info.stale && <Badge tone="warning">desatualizado</Badge>}
                {card.domain_tags.map((t) => (
                  <Badge key={t}>{t}</Badge>
                ))}
              </div>
              <p className="text-base leading-relaxed text-stone-800 dark:text-stone-100">{card.summary}</p>
              <p className="text-xs text-stone-500 dark:text-stone-400">
                Escrito por {info.card_agent ?? "agent"} em {formatDateTime(info.card_at)}. Gerado a partir do código: confirme
                antes de afirmar.
              </p>
            </section>
          ) : (
            <Alert>Só há fatos para este repo: o resumo ainda não foi escrito por um agent.</Alert>
          )}

          <div className="grid gap-x-8 gap-y-5 md:grid-cols-2">
          {card && card.capabilities.length > 0 && (
            <Block title="O que faz">
              <ul className="list-disc space-y-0.5 pl-5">
                {card.capabilities.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            </Block>
          )}

          {card?.related_repos && card.related_repos.length > 0 && (
            <Block title="Repos relacionados">
              <ul className="space-y-1">
                {card.related_repos.map((r) => (
                  <li key={r.repo} className="flex flex-wrap items-baseline gap-2">
                    <span className="font-medium" title={r.repo}>{shortName(r.repo)}</span>
                    <Badge tone={r.basis === "codigo" ? "indexed" : "neutral"}>
                      {r.basis === "codigo" ? "visto no código" : "pelo nome"}
                    </Badge>
                    <span className="text-sm text-stone-500 dark:text-stone-400">{r.why}</span>
                  </li>
                ))}
              </ul>
            </Block>
          )}

          {card?.entrypoints && card.entrypoints.length > 0 && (
            <Block title="Por onde começar">
              <ul className="space-y-1 font-mono text-xs">
                {card.entrypoints.map((p) => (
                  <li key={p} className="truncate" title={p}>
                    {p}
                  </li>
                ))}
              </ul>
            </Block>
          )}

          {card?.glossary && card.glossary.length > 0 && (
            <Block title="Glossário">
              <p className="text-stone-600 dark:text-stone-300">{card.glossary.join(" · ")}</p>
            </Block>
          )}

          </div>

          {facts && (facts.sdl.length > 0 || catalogBits.length > 0 || facts.properties.length > 0) && (
            <Block title="Fatos (extraídos sem LLM)">
              <div className="space-y-2">
                {catalogBits.length > 0 && <p className="text-sm text-stone-600 dark:text-stone-300">catalog-info · {catalogBits.join(" · ")}</p>}
                {facts.sdl.map((s) => (
                  <div key={`${s.path}|${s.service}`} className="text-sm">
                    <p className="font-medium">
                      {s.domain}/{s.service} <span className="font-mono font-normal text-stone-500">({s.path})</span>
                    </p>
                    {[...s.queries, ...s.actions].length > 0 && (
                      <p className="font-mono text-xs leading-relaxed text-stone-600 dark:text-stone-300">
                        {[...s.queries, ...s.actions].map((op, i) => {
                          const impl = implByOp.get(`${s.service}|${op}`);
                          return (
                            <span key={op}>
                              {i > 0 && ", "}
                              <span
                                title={impl ? `${impl.symbol} — ${impl.path}:${impl.line}` : "handler não encontrado pelo nome"}
                                className={impl ? "text-emerald-700 dark:text-emerald-400" : undefined}
                              >
                                {op}
                              </span>
                            </span>
                          );
                        })}
                      </p>
                    )}
                    {s.events.length > 0 && <p className="text-stone-500">eventos: {s.events.join(", ")}</p>}
                  </div>
                ))}
                {facts.sdl.length > 0 && (
                  <p className="text-xs text-stone-500 dark:text-stone-400">
                    Em verde: operação com handler encontrado ({info.impls.length}); passe o mouse para ver o arquivo.
                  </p>
                )}
                {facts.properties.map((p) => (
                  <p key={p.path} className="text-sm text-stone-600 dark:text-stone-300">
                    {p.properties.length} propriedade(s) configurável(is) em <span className="font-mono">{p.path}</span>
                  </p>
                ))}
              </div>
            </Block>
          )}
        </div>
      )}
    </Dialog>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-1.5">
      <h3 className="text-sm font-semibold text-stone-700 dark:text-stone-200">{title}</h3>
      {children}
    </section>
  );
}
