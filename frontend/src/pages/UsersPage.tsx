import { useCallback, useEffect, useState } from "react";
import { BarChart3, RefreshCw, UserCheck, UserX, Users } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { UserUsage } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useToast } from "../components/Toaster";
import { Alert, Badge, Button, EmptyState, IconButton, Panel, Segmented, Skeleton, tableHeadClass } from "../components/ui";
import { CHANNELS, fmtCompact, fmtInt, fmtUsd, numClass, rowClass, tbodyClass, thClass } from "../components/usage";
import { cx, formatDateTime, timeAgo } from "../lib/utils";
import { PERIODS, type Period } from "./UsagePage";

/** Usuários (admin): papel, estado, uso no período; desativar corta o acesso (login, tokens e MCP). */
export function UsersPage({ onOpenUsage }: { onOpenUsage: (email: string) => void }) {
  const { session } = useAuth();
  const { push } = useToast();
  const [days, setDays] = useState<Period>("30");
  const [users, setUsers] = useState<UserUsage[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [disableTarget, setDisableTarget] = useState<UserUsage | null>(null);
  const [enabling, setEnabling] = useState<string | null>(null);
  const self = session?.me.subject ?? "";

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setUsers(await api.users(Number(days)));
      setError(null);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }, [days]);

  useEffect(() => {
    void load();
  }, [load]);

  async function enable(user: UserUsage) {
    setEnabling(user.email);
    try {
      await api.enableUser(user.email);
      push({ tone: "success", title: "Acesso reativado", description: user.email });
      await load();
    } catch (err) {
      push({ tone: "error", title: "Não consegui reativar", description: describeError(err) });
    } finally {
      setEnabling(null);
    }
  }

  const sorted = users ? [...users].sort((a, b) => Number(b.active) - Number(a.active) || b.calls - a.calls || a.email.localeCompare(b.email)) : null;
  const maxCalls = Math.max(1, ...(users ?? []).map((u) => u.calls));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Usuários</h1>
          <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">
            Quem já entrou, o papel de cada um e o uso no período. Clique num usuário para ver o uso dele em detalhe.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <Segmented label="Período" options={[...PERIODS]} value={days} onChange={setDays} />
          <Button variant="secondary" onClick={() => void load()} loading={busy} aria-label="Atualizar">
            {!busy && <RefreshCw className="size-4" aria-hidden="true" />}
            Atualizar
          </Button>
        </div>
      </div>

      {error && <Alert>{error}</Alert>}

      <Panel className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1080px] text-sm">
            <thead className={tableHeadClass}>
              <tr>
                <th className={thClass}>Usuário</th>
                <th className={thClass}>Papel</th>
                <th className={thClass}>Estado</th>
                <th className={cx(thClass, "text-right")}>Tokens ativos</th>
                <th className={cx(thClass, "text-right")}>Chamadas</th>
                <th className={cx(thClass, "text-right")} title="Consumo de contexto (~tokens)">Contexto</th>
                <th className={cx(thClass, "text-right")}>Custo</th>
                <th className={thClass}>Clientes</th>
                <th className={thClass}>Última atividade</th>
                <th className={thClass}>
                  <span className="sr-only">Ações</span>
                </th>
              </tr>
            </thead>
            <tbody className={tbodyClass}>
              {sorted === null && !error && (
                <tr>
                  <td className="px-4 py-4" colSpan={10}>
                    <Skeleton className="h-5 w-full" />
                  </td>
                </tr>
              )}
              {sorted !== null && sorted.length === 0 && (
                <tr>
                  <td colSpan={10}>
                    <EmptyState icon={Users} title="Ninguém entrou ainda" text="Os usuários aparecem aqui depois do primeiro login." />
                  </td>
                </tr>
              )}
              {sorted?.map((u) => (
                <tr key={u.id} className={cx(rowClass, !u.active && "text-stone-500 dark:text-stone-500")}>
                  <td className="max-w-64 px-3 py-2.5">
                    <button
                      type="button"
                      onClick={() => onOpenUsage(u.email)}
                      className="block max-w-full truncate rounded text-left outline-none hover:text-accent-700 focus-visible:ring-2 focus-visible:ring-accent-500 dark:hover:text-accent-300"
                      title={`Ver o uso de ${u.email}`}
                    >
                      <span className="block truncate font-medium">{u.display_name ?? u.email.split("@")[0]}</span>
                      <span className="block truncate text-xs text-stone-500 dark:text-stone-400">{u.email}</span>
                    </button>
                  </td>
                  <td className="px-3 py-2.5">
                    <Badge tone={u.role === "admin" ? "accent" : "neutral"}>{u.role}</Badge>
                  </td>
                  <td className="px-3 py-2.5">
                    {u.active ? (
                      <Badge tone="indexed" dot>
                        ativo
                      </Badge>
                    ) : (
                      <span title={`Desativado ${formatDateTime(u.disabled_at)}`}>
                        <Badge tone="error" dot>
                          desativado
                        </Badge>
                      </span>
                    )}
                  </td>
                  <td className={numClass}>{fmtInt(u.active_tokens)}</td>
                  <td className={numClass}>
                    <CallsCell user={u} max={maxCalls} />
                  </td>
                  <td className={numClass}>~{fmtCompact(u.context_tokens)}</td>
                  <td className={numClass}>{fmtUsd(u.cost_usd)}</td>
                  <td className="max-w-44 px-3 py-2.5">
                    <span className="block truncate text-xs text-stone-600 dark:text-stone-300" title={u.clients.join(", ")}>
                      {u.clients.length ? u.clients.join(", ") : "—"}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 whitespace-nowrap">
                    <span title={formatDateTime(u.last_at ?? u.last_seen_at)}>{timeAgo(u.last_at ?? u.last_seen_at)}</span>
                    {u.top_action && (
                      <span className="block max-w-40 truncate font-mono text-[11px] text-stone-500 dark:text-stone-400" title={`Mais usado: ${u.top_action}`}>
                        {u.top_action}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center justify-end gap-1">
                      <IconButton label={`Ver o uso de ${u.email}`} onClick={() => onOpenUsage(u.email)}>
                        <BarChart3 className="size-4" aria-hidden="true" />
                      </IconButton>
                      {u.active ? (
                        <IconButton
                          label={u.email === self ? "Você não pode desativar o próprio acesso" : `Desativar ${u.email}`}
                          onClick={() => setDisableTarget(u)}
                          disabled={u.email === self}
                          className="hover:text-rose-600 dark:hover:text-rose-400"
                        >
                          <UserX className="size-4" aria-hidden="true" />
                        </IconButton>
                      ) : (
                        <Button variant="secondary" size="sm" loading={enabling === u.email} onClick={() => void enable(u)}>
                          {enabling !== u.email && <UserCheck className="size-3.5" aria-hidden="true" />}
                          Reativar
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <ConfirmDialog
        open={disableTarget !== null}
        onClose={() => setDisableTarget(null)}
        title="Desativar acesso"
        confirmLabel="Desativar"
        danger
        onConfirm={async () => {
          if (!disableTarget) return;
          await api.disableUser(disableTarget.email);
          push({ tone: "success", title: "Acesso desativado", description: disableTarget.email });
          await load();
        }}
      >
        <p>
          <span className="font-medium">{disableTarget?.email}</span> deixa de entrar na interface, e os tokens e clientes
          MCP desse usuário param de funcionar. O histórico de uso continua aqui, e dá para reativar depois.
        </p>
      </ConfirmDialog>
    </div>
  );
}

/** Total de chamadas com a divisão por canal numa barrinha (mesmas cores do gráfico de uso). */
function CallsCell({ user, max }: { user: UserUsage; max: number }) {
  const parts = [
    { key: "mcp" as const, value: user.mcp_calls },
    { key: "api" as const, value: user.api_calls },
    { key: "ui" as const, value: user.ui_calls },
  ];
  const title = parts.map((p) => `${CHANNELS[p.key].label} ${fmtInt(p.value)}`).join(" · ") + (user.errors ? ` · ${fmtInt(user.errors)} erros` : "");
  return (
    <div className="flex items-center justify-end gap-2" title={title}>
      <span className="hidden h-1.5 w-20 gap-px overflow-hidden rounded-full bg-stone-100 sm:flex dark:bg-stone-800" aria-hidden="true">
        {parts.map((p) =>
          p.value > 0 ? <span key={p.key} className={cx("h-full", CHANNELS[p.key].swatch)} style={{ width: `${(p.value / max) * 100}%` }} /> : null,
        )}
      </span>
      {fmtInt(user.calls)}
    </div>
  );
}
