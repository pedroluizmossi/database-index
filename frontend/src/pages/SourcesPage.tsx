import { useCallback, useEffect, useMemo, useState } from "react";
import { BookOpen, Building2, ExternalLink, GitBranch, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { DocSourceInfo, OrgFilters, OrgSummary, Repo, RepoStatusValue } from "../api/types";
import { useIndex } from "../data/IndexProvider";
import { useToast } from "../components/Toaster";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { AddOrgDialog, AddRepoDialog, EditOrgDialog } from "../components/SourceDialogs";
import { Alert, Badge, Button, EmptyState, IconButton, Panel, Skeleton, tableHeadClass } from "../components/ui";
import { useNavigation } from "../lib/navigation";
import { reposHash } from "../lib/repoFilters";

const STATUS_LABEL: Record<RepoStatusValue, string> = {
  indexed: "indexado",
  pending: "pendente",
  error: "erro",
  ignored: "ignorado",
};

/** Filtros de uma org como chips curtos; vazio = descobre tudo com os padrões. */
function filterChips(f: OrgFilters): string[] {
  const out: string[] = [];
  if (f.match?.length) out.push(`só ${f.match.join(", ")}`);
  if (f.skip?.length) out.push(`ignora ${f.skip.join(", ")}`);
  if (f.languages?.length) out.push(f.languages.join(" › "));
  if (f.max_size_mb) out.push(`até ${f.max_size_mb} MB`);
  if (f.include_forks) out.push("com forks");
  if (f.include_archived) out.push("com arquivados");
  if (f.extra_exclude?.length) out.push(`exclui ${f.extra_exclude.join(", ")}`);
  return out;
}

/**
 * Fontes cadastradas (admin): orgs/contas do GitHub, repositórios avulsos e fontes de documentação.
 * É o cadastro; a operação da indexação (lote, fila, cobertura) fica na aba Indexação.
 */
export function SourcesPage() {
  const { repos, orgs, loading, reload } = useIndex();
  const { push } = useToast();
  const nav = useNavigation();
  const [docs, setDocs] = useState<DocSourceInfo[] | null>(null);
  const [docsError, setDocsError] = useState<string | null>(null);
  const [addRepoOpen, setAddRepoOpen] = useState(false);
  const [addOrgOpen, setAddOrgOpen] = useState(false);
  const [editOrg, setEditOrg] = useState<OrgSummary | null>(null);
  const [removeOrg, setRemoveOrg] = useState<OrgSummary | null>(null);
  const [keepClones, setKeepClones] = useState(false);
  const [removeRepo, setRemoveRepo] = useState<Repo | null>(null);
  const [refreshing, setRefreshing] = useState<string | null>(null);

  const loadDocs = useCallback(async () => {
    try {
      setDocs(await api.docSources());
      setDocsError(null);
    } catch (err) {
      setDocsError(describeError(err));
    }
  }, []);

  useEffect(() => {
    void loadDocs();
  }, [loadDocs]);

  const standalone = useMemo(() => (repos ?? []).filter((r) => r.org === null && r.kind !== "docs"), [repos]);
  const openRepos = nav?.can("repos") ? (search: string) => nav.open("repos", reposHash({ search })) : undefined;

  async function refresh(org: OrgSummary) {
    setRefreshing(org.name);
    try {
      const d = (await api.refreshOrg(org.name))[org.name];
      push({
        tone: "success",
        title: `Descoberta de ${org.name} atualizada`,
        description: d ? `${d.total} repositório(s), ${d.new} novo(s), ${d.removed} fora.` : undefined,
      });
      await reload();
    } catch (err) {
      push({ tone: "error", title: `Não consegui atualizar ${org.name}`, description: describeError(err) });
    } finally {
      setRefreshing(null);
    }
  }

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Fontes</h1>
          <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">
            O que está cadastrado para indexar. Cadastrar só descobre; a indexação é feita na aba Indexação.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" size="sm" loading={loading} onClick={() => void Promise.all([reload(), loadDocs()])}>
            Atualizar
          </Button>
          <Button variant="secondary" size="sm" onClick={() => setAddRepoOpen(true)}>
            <Plus className="size-3.5" aria-hidden="true" />
            Adicionar repositório
          </Button>
          <Button size="sm" onClick={() => setAddOrgOpen(true)}>
            <Plus className="size-3.5" aria-hidden="true" />
            Adicionar org/conta
          </Button>
        </div>
      </div>

      {/* Orgs e contas */}
      <section className="space-y-3" aria-labelledby="sources-orgs">
        <h2 id="sources-orgs" className="text-sm font-semibold uppercase tracking-wide text-stone-500 dark:text-stone-400">
          Orgs e contas do GitHub
        </h2>
        <Panel className="overflow-hidden">
          {orgs === null ? (
            <div className="space-y-2 p-4">
              <Skeleton className="h-8" />
              <Skeleton className="h-8" />
            </div>
          ) : orgs.length === 0 ? (
            <EmptyState
              icon={Building2}
              title="Nenhuma org cadastrada"
              text="Uma org ou conta do GitHub descobre os repositórios dela automaticamente."
              action={
                <Button size="sm" onClick={() => setAddOrgOpen(true)}>
                  Adicionar org/conta
                </Button>
              }
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-sm">
                <thead className={tableHeadClass}>
                  <tr>
                    <th className="px-4 py-3 font-medium">Nome</th>
                    <th className="px-4 py-3 font-medium">Filtros</th>
                    <th className="px-4 py-3 font-medium">Repositórios</th>
                    <th className="px-4 py-3 text-right font-medium">Ações</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-stone-100 dark:divide-stone-800">
                  {orgs.map((org) => {
                    const chips = filterChips(org.filters);
                    return (
                      <tr key={org.name}>
                        <td className="px-4 py-3 font-medium">
                          <a
                            href={`https://github.com/${encodeURIComponent(org.name)}`}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1.5 hover:text-accent-700 dark:hover:text-accent-300"
                          >
                            {org.name}
                            <ExternalLink className="size-3 opacity-60" aria-hidden="true" />
                          </a>
                        </td>
                        <td className="px-4 py-3">
                          {chips.length === 0 ? (
                            <span className="text-stone-500 dark:text-stone-400">padrão</span>
                          ) : (
                            <div className="flex flex-wrap gap-1.5">
                              {chips.map((c) => (
                                <Badge key={c}>{c}</Badge>
                              ))}
                            </div>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          {openRepos ? (
                            <button
                              type="button"
                              onClick={() => openRepos(`${org.name}__`)}
                              className="font-medium tabular-nums hover:text-accent-700 hover:underline dark:hover:text-accent-300"
                            >
                              {org.status.indexed} de {org.total} indexados
                            </button>
                          ) : (
                            <span className="font-medium tabular-nums">
                              {org.status.indexed} de {org.total} indexados
                            </span>
                          )}
                          <p className="mt-0.5 text-xs text-stone-500 tabular-nums dark:text-stone-400">
                            {[
                              org.status.pending && `${org.status.pending} pendente(s)`,
                              org.status.error && `${org.status.error} com erro`,
                              org.status.ignored && `${org.status.ignored} ignorado(s)`,
                              org.outdated && `${org.outdated} com push novo`,
                            ]
                              .filter(Boolean)
                              .join(" · ") || "tudo em dia"}
                          </p>
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex justify-end gap-1">
                            <IconButton
                              label={`Atualizar descoberta de ${org.name}`}
                              onClick={() => void refresh(org)}
                              disabled={refreshing !== null}
                            >
                              <RefreshCw className={refreshing === org.name ? "size-4 animate-spin" : "size-4"} aria-hidden="true" />
                            </IconButton>
                            <IconButton label={`Editar filtros de ${org.name}`} onClick={() => setEditOrg(org)}>
                              <Pencil className="size-4" aria-hidden="true" />
                            </IconButton>
                            <IconButton
                              label={`Remover ${org.name}`}
                              onClick={() => {
                                setKeepClones(false);
                                setRemoveOrg(org);
                              }}
                              className="hover:text-rose-600 dark:hover:text-rose-400"
                            >
                              <Trash2 className="size-4" aria-hidden="true" />
                            </IconButton>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </section>

      {/* Repos avulsos */}
      <section className="space-y-3" aria-labelledby="sources-repos">
        <h2 id="sources-repos" className="text-sm font-semibold uppercase tracking-wide text-stone-500 dark:text-stone-400">
          Repositórios avulsos
        </h2>
        <Panel className="overflow-hidden">
          {repos === null ? (
            <div className="p-4">
              <Skeleton className="h-8" />
            </div>
          ) : standalone.length === 0 ? (
            <EmptyState
              icon={GitBranch}
              title="Nenhum repositório avulso"
              text="URL git, org/repo do GitHub ou diretório do servidor, fora de uma org cadastrada."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-sm">
                <thead className={tableHeadClass}>
                  <tr>
                    <th className="px-4 py-3 font-medium">Nome</th>
                    <th className="px-4 py-3 font-medium">Origem</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 text-right font-medium">Ações</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-stone-100 dark:divide-stone-800">
                  {standalone.map((r) => (
                    <tr key={r.name}>
                      <td className="px-4 py-3 font-medium">
                        {openRepos ? (
                          <button type="button" onClick={() => openRepos(r.name)} className="hover:text-accent-700 hover:underline dark:hover:text-accent-300">
                            {r.name}
                          </button>
                        ) : (
                          r.name
                        )}
                      </td>
                      <td className="max-w-[28rem] truncate px-4 py-3 font-mono text-xs text-stone-600 dark:text-stone-300" title={r.source ?? ""}>
                        {r.source ?? "—"}
                      </td>
                      <td className="px-4 py-3">
                        <Badge tone={r.status} dot>
                          {STATUS_LABEL[r.status]}
                        </Badge>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex justify-end">
                          <IconButton label={`Remover ${r.name}`} onClick={() => setRemoveRepo(r)} className="hover:text-rose-600 dark:hover:text-rose-400">
                            <Trash2 className="size-4" aria-hidden="true" />
                          </IconButton>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </section>

      {/* Fontes de documentação */}
      <section className="space-y-3" aria-labelledby="sources-docs">
        <div className="flex items-end justify-between gap-4">
          <h2 id="sources-docs" className="text-sm font-semibold uppercase tracking-wide text-stone-500 dark:text-stone-400">
            Fontes de documentação
          </h2>
          {nav?.can("docs") && (
            <Button variant="ghost" size="sm" onClick={() => nav.open("docs")}>
              Abrir Documentação
            </Button>
          )}
        </div>
        {docsError && <Alert>{docsError}</Alert>}
        <Panel className="overflow-hidden">
          {docs === null ? (
            <div className="p-4">
              <Skeleton className="h-8" />
            </div>
          ) : docs.length === 0 ? (
            <EmptyState
              icon={BookOpen}
              title="Nenhuma fonte de documentação"
              text="Wiki.js ou OpenAPI: cadastre pela CLI (`code-index docs add`)."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-sm">
                <thead className={tableHeadClass}>
                  <tr>
                    <th className="px-4 py-3 font-medium">Nome</th>
                    <th className="px-4 py-3 font-medium">Tipo</th>
                    <th className="px-4 py-3 font-medium">Endereço</th>
                    <th className="px-4 py-3 font-medium">Escopo</th>
                    <th className="px-4 py-3 text-right font-medium">Documentos</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-stone-100 dark:divide-stone-800">
                  {docs.map((d) => (
                    <tr key={d.name}>
                      <td className="px-4 py-3 font-medium">{d.label || d.name}</td>
                      <td className="px-4 py-3">
                        <Badge>{d.type}</Badge>
                      </td>
                      <td className="max-w-[20rem] truncate px-4 py-3 font-mono text-xs text-stone-600 dark:text-stone-300" title={d.url}>
                        {d.url}
                      </td>
                      <td className="px-4 py-3 text-xs text-stone-600 dark:text-stone-300">
                        {d.include.length ? d.include.filter((g) => !g.endsWith("/**")).join(", ") : "tudo"}
                        {d.exclude.length > 0 && <span className="text-stone-500"> · exclui {d.exclude.join(", ")}</span>}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">
                        {d.documents}
                        {d.ignored > 0 && (
                          <span className="block text-xs text-stone-500 dark:text-stone-400">
                            {d.ignored} fora da busca
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </section>

      <AddRepoDialog open={addRepoOpen} onClose={() => setAddRepoOpen(false)} onDone={() => void reload()} />
      <AddOrgDialog open={addOrgOpen} onClose={() => setAddOrgOpen(false)} onDone={() => void reload()} />
      <EditOrgDialog org={editOrg} onClose={() => setEditOrg(null)} onDone={() => void reload()} />
      <ConfirmDialog
        open={removeOrg !== null}
        onClose={() => setRemoveOrg(null)}
        title="Remover org/conta"
        confirmLabel="Remover"
        danger
        onConfirm={async () => {
          if (!removeOrg) return;
          const res = await api.removeOrg(removeOrg.name, keepClones);
          push({
            tone: "success",
            title: `${removeOrg.name} removida`,
            description: `${res.removed_repos} repositório(s) saíram do catálogo. Os trechos somem no próximo sync.`,
          });
          await reload();
        }}
      >
        <p>
          Remover <span className="font-mono">{removeOrg?.name}</span> e os {removeOrg?.total ?? 0} repositório(s) dela do
          catálogo. Os trechos indexados somem no próximo sync; indexar de novo custa tokens.
        </p>
        <label className="mt-3 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={keepClones} onChange={(e) => setKeepClones(e.target.checked)} className="size-4 accent-accent-600" />
          Manter os clones locais
        </label>
      </ConfirmDialog>
      <ConfirmDialog
        open={removeRepo !== null}
        onClose={() => setRemoveRepo(null)}
        title="Remover repositório"
        confirmLabel="Remover"
        danger
        onConfirm={async () => {
          if (!removeRepo) return;
          await api.removeRepo(removeRepo.name);
          push({ tone: "success", title: "Repositório removido", description: "Os trechos somem no próximo sync." });
          await reload();
        }}
      >
        <p>
          Remover <span className="font-mono">{removeRepo?.name}</span> da configuração e apagar o clone local. Os trechos
          indexados somem no próximo sync.
        </p>
      </ConfirmDialog>
    </div>
  );
}
