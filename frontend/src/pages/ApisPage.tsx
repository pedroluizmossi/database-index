import { useCallback, useEffect, useMemo, useState } from "react";
import { Database, ExternalLink, Network, RefreshCw, Search } from "lucide-react";
import { apiId, type XrayTarget } from "../lib/xray";
import { api } from "../api/endpoints";
import { ApiError, describeError } from "../api/client";
import type { SyncJob } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useIndex } from "../data/IndexProvider";
import { hasScope } from "../lib/capabilities";
import { formatDateTime } from "../lib/utils";
import { Alert, Badge, Button, EmptyState, Field, Panel, Select, Skeleton, inputClass } from "../components/ui";
import { Dialog } from "../components/Dialog";
import { KindIcon } from "../components/kind";
import { JobDialog, JobsPanel, RunningJobBanner, useJobs } from "../components/JobsPanel";

type Item = Awaited<ReturnType<typeof api.openapiCatalog>>[number];
type Source = Awaited<ReturnType<typeof api.openapiStatus>>[number];
type Preview = Awaited<ReturnType<typeof api.openapiPreview>>;

export function ApisPage({ onXray }: { onXray: (target: XrayTarget) => void }) {
  const { session } = useAuth();
  const { reload } = useIndex();
  const [items, setItems] = useState<Item[] | null>(null);
  const [sources, setSources] = useState<Source[] | null>(null);
  const [domain, setDomain] = useState("");
  const [query, setQuery] = useState("");
  const [all, setAll] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(0);
  const [jobDetail, setJobDetail] = useState<SyncJob | null>(null);
  const admin = !!session && hasScope(session.me, "admin");
  const canSeeJobs = !!session && hasScope(session.me, "index:write");

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [catalog, status] = await Promise.allSettled([api.openapiCatalog(), api.openapiStatus()]);
      if (catalog.status === "rejected") throw catalog.reason;
      setItems(catalog.value);
      if (status.status === "fulfilled") {
        setSources(status.value);
        setDomain((current) => current || status.value[0]?.domain || "");
      } else {
        setSources(null);
        setError(status.reason instanceof ApiError && status.reason.status === 404
          ? "Reinicie o servidor para carregar o estado das APIs. O catálogo continua disponível."
          : describeError(status.reason));
      }
    } catch (e) { setError(describeError(e)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const onFinished = useCallback(() => { void load(); void reload(); }, [load, reload]);
  const jobs = useJobs(canSeeJobs, onFinished);
  const apiRepos = new Set(sources?.map((s) => s.repo) ?? []);
  const apiJobs = { ...jobs, jobs: jobs.jobs?.filter((j) => j.params?.openapi_domain != null || j.params?.repos?.some((r) => apiRepos.has(r))) ?? null };
  const domains = useMemo(() => [...new Set((items ?? []).map((i) => i.domain))].sort(), [items]);
  const services = useMemo(() => (items ?? []).filter((i) => i.domain === domain), [items, domain]);
  const source = sources?.find((s) => s.domain === domain);
  const indexed = new Map(source?.services.map((s) => [s.service, s.operations]) ?? []);
  const visibleServices = services.filter((s) => s.service.toLowerCase().includes(query.trim().toLowerCase()));
  const pageSize = 12;
  const pageCount = Math.max(1, Math.ceil(visibleServices.length / pageSize));
  const currentPage = Math.min(page, pageCount - 1);
  const visible = visibleServices.slice(currentPage * pageSize, (currentPage + 1) * pageSize);
  const totalOperations = (sources ?? []).flatMap((s) => s.services).reduce((sum, s) => sum + s.operations, 0);
  const indexedServices = (sources ?? []).reduce((sum, s) => sum + s.services.length, 0);

  function changeDomain(value: string) { setDomain(value); setSelected([]); setAll(false); setQuery(""); setPage(0); setPreview(null); }
  async function showPreview() {
    setBusy(true); setError("");
    try { setPreview(await api.openapiPreview(domain, all ? null : selected)); }
    catch (e) { setError(describeError(e)); }
    finally { setBusy(false); }
  }
  async function start() {
    setBusy(true); setError("");
    try { await api.openapiIndex(domain, all ? null : selected); setPreview(null); setSelected([]); setAll(false); await jobs.refresh(); await load(); }
    catch (e) { setError(describeError(e)); }
    finally { setBusy(false); }
  }

  return <div className="space-y-8">
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div><h1 className="text-2xl font-semibold tracking-tight">APIs / Swagger</h1>
        <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">Catálogo de serviços e operações indexadas do ecossistema.</p></div>
      <Button variant="secondary" size="sm" loading={loading} onClick={() => void load()}><RefreshCw className="size-3.5" /> Atualizar</Button>
    </div>
    {error && <Alert>{error}</Alert>}
    {jobs.running && <RunningJobBanner job={jobs.running} onOpen={setJobDetail} />}
    <Panel>
      <dl className="grid divide-stone-200 sm:grid-cols-3 sm:divide-x dark:divide-stone-800">
        <Summary label="domínios cadastrados" value={sources?.length} hint="seleções persistidas" />
        <Summary label="serviços com operações" value={sources ? indexedServices : undefined} hint="com conteúdo no índice" />
        <Summary label="operações indexadas" value={sources ? totalOperations : undefined} hint="documentos de operações" />
      </dl>
    </Panel>
    <Panel className="overflow-hidden">
      <div className="flex flex-wrap items-end gap-4 border-b border-stone-200 p-5 dark:border-stone-800">
        <Field label="Domínio" className="min-w-48 flex-1">
          <Select value={domain} onChange={changeDomain}><option value="">Selecione um domínio</option>{domains.map((d) => <option key={d} value={d}>{d}</option>)}</Select>
        </Field>
        <div className="relative min-w-48 flex-1"><label htmlFor="api-search" className="mb-1.5 block text-sm font-medium">Buscar serviço</label>
          <Search className="pointer-events-none absolute bottom-3 left-3 size-4 text-stone-400" aria-hidden="true" />
          <input id="api-search" className={`${inputClass} pl-9`} value={query} onChange={(e) => { setQuery(e.target.value); setPage(0); }} placeholder="Nome do serviço" disabled={!domain} />
        </div>
      </div>
      {items === null && !error && <div className="space-y-2 p-5"><Skeleton className="h-12 w-full" /><Skeleton className="h-12 w-full" /></div>}
      {items !== null && !domain && <EmptyState icon={Database} title="Selecione um domínio" text="Escolha um domínio para consultar os serviços disponíveis." />}
      {domain && <>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-stone-200 px-5 py-4 dark:border-stone-800">
          <div><h2 className="font-semibold">{domain} <span className="font-normal text-stone-500">· {services.length} serviços</span></h2>
            <p className="mt-1 text-xs text-stone-500 dark:text-stone-400">{source ? `${source.all_services ? "Domínio inteiro cadastrado" : `${source.selected_services.length} serviço(s) cadastrados`} · última indexação: ${formatDateTime(source.indexed_at)}` : sources === null ? "Estado da indexação indisponível." : "Este domínio ainda não foi cadastrado."}</p>
          </div>
          {source && <Badge tone={source.status === "error" ? "error" : source.status === "indexed" ? "indexed" : "pending"} dot>{source.status === "indexed" ? "Fonte indexada" : source.status === "error" ? "Fonte com erro" : "Fonte pendente"}</Badge>}
        </div>
        {source?.error && <div className="px-5 pt-4"><Alert>{source.error}</Alert></div>}
        <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={all} onChange={(e) => { setAll(e.target.checked); setPreview(null); }} className="size-4 accent-accent-600" /> Domínio inteiro, incluindo serviços futuros</label>
          <span className="text-xs text-stone-500 dark:text-stone-400">{all ? `${services.length} serviços no domínio` : `${selected.length} selecionado(s)`}</span>
        </div>
        {visible.length === 0 ? <EmptyState icon={Search} title="Nenhum serviço encontrado" text="Tente outro nome de serviço." /> :
          <ul className="stagger divide-y divide-stone-100 border-y border-stone-200 dark:divide-stone-800 dark:border-stone-800">{visible.map((s) => {
            const count = indexed.get(s.service);
            return <li key={s.key} className="flex items-center gap-3 px-5 py-3">
              <input type="checkbox" aria-label={`Selecionar ${s.service}`} className="size-4 shrink-0 accent-accent-600" disabled={all} checked={all || selected.includes(s.service)} onChange={(e) => { setSelected(e.target.checked ? [...selected, s.service] : selected.filter((v) => v !== s.service)); setPreview(null); }} />
              <KindIcon kind="api" size="sm" />
              <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{s.service}</p><p className="truncate text-xs text-stone-500 dark:text-stone-400">{s.key}</p></div>
              {count !== undefined ? <Badge tone="indexed" dot>{count} {count === 1 ? "operação" : "operações"}</Badge> : <span className="text-xs text-stone-500 dark:text-stone-400">{sources === null ? "Estado indisponível" : "Sem operações no índice"}</span>}
              {count !== undefined && source && <button type="button" aria-label={`Raio X de ${domain}/${s.service}`} onClick={() => onXray({kind: "api", id: apiId(source.repo, domain, s.service)})} className="inline-flex items-center gap-1 text-xs font-medium text-accent-600 hover:underline dark:text-accent-400"><Network className="size-3.5" aria-hidden="true" /> Raio X</button>}
              <a href={s.url} target="_blank" rel="noreferrer" aria-label={`Abrir Swagger de ${s.service}`} className="text-stone-400 hover:text-accent-500"><ExternalLink className="size-4" /></a>
            </li>;
          })}</ul>}
        <div className="flex flex-wrap items-center justify-between gap-3 p-5">
          <div className="flex items-center gap-2 text-sm text-stone-500"><Button variant="secondary" size="sm" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Anterior</Button><span>Página {currentPage + 1} de {pageCount}</span><Button variant="secondary" size="sm" disabled={currentPage + 1 >= pageCount} onClick={() => setPage(currentPage + 1)}>Próxima</Button></div>
          <Button loading={busy} disabled={!all && selected.length === 0} onClick={() => void showPreview()}>Ver prévia</Button>
        </div>
      </>}
    </Panel>
    {canSeeJobs && <JobsPanel state={apiJobs} onOpen={setJobDetail} />}
    <JobDialog job={jobDetail} onClose={() => setJobDetail(null)} />
    <Dialog open={preview !== null} onClose={() => setPreview(null)} title="Prévia da indexação" description="Confira os serviços antes de iniciar." size="lg" footer={<><Button variant="secondary" onClick={() => setPreview(null)}>Cancelar</Button>{admin && <Button loading={busy} disabled={!!jobs.running} onClick={() => void start()}>Indexar seleção</Button>}</>}>
      {preview && <div className="space-y-4 text-sm"><p><strong>Domínio:</strong> {preview.domain}</p><p><strong>Serviços:</strong> {preview.services.length} · {(preview.bytes / 1024 / 1024).toFixed(2)} MiB em arquivos de origem</p><div className="max-h-48 overflow-y-auto rounded-xl border border-stone-200 p-3 dark:border-stone-800">{preview.services.join(", ")}</div><p className="text-stone-500 dark:text-stone-400">A indexação gera custo de embeddings na Voyage. O tamanho dos arquivos não é uma estimativa de custo.</p>{!admin && <p>A indexação exige permissão de administrador.</p>}{jobs.running && <p>Já existe uma indexação em andamento.</p>}</div>}
    </Dialog>
  </div>;
}

function Summary({ label, value, hint }: { label: string; value?: number; hint: string }) {
  return (
    <div className="flex flex-col-reverse gap-0.5 border-stone-200 px-5 py-4 not-first:border-t sm:not-first:border-t-0 dark:border-stone-800">
      <dt className="text-sm text-stone-600 dark:text-stone-300">
        {label} <span className="text-stone-400 dark:text-stone-500">· {hint}</span>
      </dt>
      <dd className="text-2xl font-semibold tracking-tight text-stone-900 tabular-nums dark:text-white">
        {value === undefined ? "—" : value.toLocaleString("pt-BR")}
      </dd>
    </div>
  );
}
