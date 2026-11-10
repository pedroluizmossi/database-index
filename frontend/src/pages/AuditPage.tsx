import { useCallback, useEffect, useState } from "react";
import { ScrollText, Search } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { AuditEntry } from "../api/types";
import { Alert, Badge, Button, EmptyState, Field, Panel, Skeleton, inputClass, tableHeadClass } from "../components/ui";
import { formatDateTime, timeAgo } from "../lib/utils";

const LIMITS = [50, 200, 500] as const;

/** Trilha de auditoria (admin). Mostra quem fez o quê; nunca tokens nem conteúdo retornado. */
export function AuditPage() {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actor, setActor] = useState("");
  const [limit, setLimit] = useState<(typeof LIMITS)[number]>(50);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setEntries(await api.audit(limit, actor.trim()));
      setError(null);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }, [limit, actor]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Auditoria</h1>
          <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">Ações recentes na API e no MCP.</p>
        </div>
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void load();
          }}
        >
          <Field label="Quem" className="w-56">
            <input value={actor} onChange={(e) => setActor(e.target.value)} className={inputClass} placeholder="e-mail ou nome do token" />
          </Field>
          <Field label="Mostrar" className="w-28">
            <select value={limit} onChange={(e) => setLimit(Number(e.target.value) as (typeof LIMITS)[number])} className={inputClass}>
              {LIMITS.map((n) => (
                <option key={n} value={n}>últimas {n}</option>
              ))}
            </select>
          </Field>
          <Button type="submit" variant="secondary" loading={busy} aria-label="Filtrar">
            <Search className="size-4" aria-hidden="true" />
            Filtrar
          </Button>
        </form>
      </div>

      {error && <Alert>{error}</Alert>}

      <Panel className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] text-sm">
            <thead className={tableHeadClass}>
              <tr>
                <th className="px-4 py-3 font-medium">Quando</th>
                <th className="px-4 py-3 font-medium">Quem</th>
                <th className="px-4 py-3 font-medium">Ação</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Parâmetros</th>
                <th className="px-4 py-3 font-medium">IP</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-stone-100 dark:divide-stone-800">
              {entries === null && !error && (
                <tr>
                  <td className="px-4 py-4" colSpan={6}>
                    <Skeleton className="h-5 w-full" />
                  </td>
                </tr>
              )}
              {entries !== null && entries.length === 0 && (
                <tr>
                  <td colSpan={6}>
                    <EmptyState icon={ScrollText} title="Nada registrado com esse filtro" />
                  </td>
                </tr>
              )}
              {entries?.map((e) => (
                <tr key={e.id} className="align-top transition-colors hover:bg-stone-50 dark:hover:bg-white/[0.02]">
                  <td className="px-4 py-3 whitespace-nowrap" title={formatDateTime(e.at)}>
                    {timeAgo(e.at)}
                  </td>
                  <td className="px-4 py-3 text-stone-700 dark:text-stone-300">{e.actor}</td>
                  <td className="px-4 py-3 font-mono text-xs">{e.action}</td>
                  <td className="px-4 py-3">
                    <Badge tone={e.status !== null && e.status >= 400 ? "error" : "indexed"}>{e.status ?? "—"}</Badge>
                  </td>
                  <td className="max-w-md px-4 py-3">
                    <code className="block truncate font-mono text-xs text-stone-500" title={JSON.stringify(e.params)}>
                      {e.params ? JSON.stringify(e.params) : "—"}
                    </code>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-stone-500">{e.client_ip ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
