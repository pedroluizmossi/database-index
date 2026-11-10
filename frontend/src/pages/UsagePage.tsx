import { useCallback, useEffect, useState } from "react";
import { Activity, BarChart3, CircleDollarSign, Gauge, RefreshCw, SearchX, TriangleAlert, Users, Layers } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { UsageReport, UserUsage } from "../api/types";
import { Alert, Badge, Button, EmptyState, Field, Panel, Segmented, Select, Skeleton } from "../components/ui";
import {
  ActionTable,
  ChannelBadge,
  DailyChart,
  QueryList,
  Section,
  StatTile,
  fmtBytes,
  fmtCompact,
  fmtInt,
  fmtMs,
  fmtPct,
  fmtUsd,
  numClass,
  rowClass,
  tbodyClass,
  thClass,
  theadClass,
} from "../components/usage";
import { cx, formatDateTime, timeAgo } from "../lib/utils";

export const PERIODS = [
  { value: "7", label: "7 dias" },
  { value: "30", label: "30 dias" },
  { value: "90", label: "90 dias" },
] as const;
export type Period = (typeof PERIODS)[number]["value"];

/** Painel de uso (admin): quem usa, por qual cliente, quais tools, quanto custa e o que volta vazio. */
export function UsagePage({ email, onEmail }: { email: string; onEmail: (email: string) => void }) {
  const [days, setDays] = useState<Period>("30");
  const [report, setReport] = useState<UsageReport | null>(null);
  const [users, setUsers] = useState<UserUsage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setReport(await api.usage(Number(days), email || undefined));
      setError(null);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }, [days, email]);

  useEffect(() => {
    void load();
  }, [load]);

  // Lista de usuários só alimenta o filtro: falha aqui não impede o painel.
  useEffect(() => {
    api.users(Number(days)).then(setUsers, () => setUsers([]));
  }, [days]);

  const options = users.map((u) => u.email);
  if (email && !options.includes(email)) options.unshift(email);

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Uso</h1>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
            Chamadas do MCP, da API e desta interface: quem usa, quanto custa e o que a busca não encontra.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Usuário" className="w-64">
            <Select value={email} onChange={onEmail}>
              <option value="">Todos os usuários</option>
              {options.map((e) => (
                <option key={e} value={e}>
                  {e}
                </option>
              ))}
            </Select>
          </Field>
          <Segmented label="Período" options={[...PERIODS]} value={days} onChange={setDays} />
          <Button variant="secondary" onClick={() => void load()} loading={busy} aria-label="Atualizar">
            {!busy && <RefreshCw className="size-4" aria-hidden="true" />}
            Atualizar
          </Button>
        </div>
      </div>

      {error && <Alert>{error}</Alert>}
      {!report && !error && <UsageSkeleton />}
      {report && <UsageReportView report={report} filtered={Boolean(email)} />}
    </div>
  );
}

function UsageSkeleton() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {Array.from({ length: 8 }, (_, i) => (
          <Skeleton key={i} className="h-24 rounded-xl" />
        ))}
      </div>
      <Skeleton className="h-64 rounded-xl" />
    </div>
  );
}

/** KPIs do relatório. Também usado em "Meu uso" (sem a contagem de usuários). */
export function UsageKpis({ report, showUsers = true }: { report: UsageReport; showUsers?: boolean }) {
  const t = report.totals;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <StatTile icon={Activity} label="Chamadas" value={fmtInt(t.calls)} hint={`${fmtPct(t.mcp_calls, t.calls)} pelo MCP`} />
      {showUsers ? (
        <StatTile icon={Users} label="Usuários ativos" value={fmtInt(t.users)} hint={`${fmtInt(t.clients)} ${t.clients === 1 ? "cliente" : "clientes"}`} />
      ) : (
        <StatTile icon={Users} label="Clientes" value={fmtInt(t.clients)} hint="claude-code, cursor, navegador…" />
      )}
      <StatTile
        icon={TriangleAlert}
        label="Taxa de erro"
        value={fmtPct(t.errors, t.calls)}
        hint={`${fmtInt(t.errors)} ${t.errors === 1 ? "erro" : "erros"}`}
        tone={t.errors > 0 ? "warning" : "neutral"}
      />
      <StatTile icon={Gauge} label="Latência p95" value={fmtMs(t.p95_ms)} hint={`média ${fmtMs(t.avg_ms)}`} />
      <StatTile
        icon={CircleDollarSign}
        label="Custo Voyage (estimado)"
        value={fmtUsd(t.cost_usd)}
        hint={`${fmtCompact(t.embed_tokens)} embedding · ${fmtCompact(t.rerank_tokens)} rerank`}
      />
      <StatTile
        icon={Layers}
        label="Contexto dos agents (MCP)"
        value={`~${fmtCompact(t.context_tokens)}`}
        hint={t.mcp_calls > 0 ? `tokens · ${fmtBytes(t.context_bytes)} em ${fmtInt(t.mcp_calls)} chamadas` : "nenhuma chamada MCP"}
      />
      <StatTile
        icon={SearchX}
        label="Consultas sem resultado"
        value={fmtInt(t.empty_results)}
        hint={`${fmtPct(t.empty_results, t.calls)} das chamadas`}
        tone={t.empty_results > 0 ? "warning" : "neutral"}
      />
      <StatTile icon={BarChart3} label="Média por dia" value={fmtInt(t.calls / Math.max(1, report.days))} hint={`em ${report.days} dias`} />
    </div>
  );
}

function UsageReportView({ report, filtered }: { report: UsageReport; filtered: boolean }) {
  if (report.totals.calls === 0) {
    return (
      <Panel>
        <EmptyState
          icon={BarChart3}
          title="Nenhum uso registrado no período"
          text={filtered ? "Esse usuário não usou o MCP, a API nem a interface nesses dias." : "Assim que alguém usar o MCP, a API ou esta interface, os números aparecem aqui."}
        />
      </Panel>
    );
  }

  return (
    <div className="space-y-8">
      <UsageKpis report={report} />

      <Section title="Chamadas por dia" subtitle={`Por canal. Gerado ${timeAgo(report.generated_at)}.`}>
        <Panel className="p-4">
          <DailyChart daily={report.daily} />
        </Panel>
      </Section>

      <Section
        title="Consumo por tool"
        subtitle={`Contexto (só MCP) ≈ bytes da resposta ÷ ${report.bytes_per_token}: quanto da janela do agent cada tool ocupa. API e interface não ocupam contexto. Custo estimado na Voyage.`}
      >
        <ActionTable rows={report.by_action} />
      </Section>

      <div className="grid gap-4 lg:grid-cols-2">
        <QueryList
          title="Consultas mais frequentes"
          subtitle="O que os usuários e os agents mais procuram."
          rows={report.top_queries}
          empty="Nenhuma consulta no período."
        />
        <QueryList
          title="Consultas sem resultado"
          subtitle="Sinal para melhorar o índice: repo faltando, termo diferente, filtro estreito demais."
          rows={report.empty_queries}
          empty="Toda consulta encontrou algo."
          tone="warning"
        />
      </div>

      <Section title="Clientes" subtitle="Quem chama: agent (MCP/API) ou navegador.">
        <Panel className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead className={theadClass}>
                <tr>
                  <th className={thClass}>Cliente</th>
                  <th className={thClass}>Canal</th>
                  <th className={thClass}>Versão</th>
                  <th className={cx(thClass, "text-right")}>Chamadas</th>
                  <th className={cx(thClass, "text-right")}>Usuários</th>
                  <th className={cx(thClass, "text-right")}>Último uso</th>
                </tr>
              </thead>
              <tbody className={tbodyClass}>
                {report.by_client.map((c) => (
                  <tr key={`${c.client}:${c.channel}`} className={rowClass}>
                    <td className="px-3 py-2.5 font-medium">{c.client}</td>
                    <td className="px-3 py-2.5">
                      <ChannelBadge channel={c.channel} />
                    </td>
                    <td className="px-3 py-2.5 font-mono text-xs text-zinc-500 dark:text-zinc-400">{c.last_version ?? "—"}</td>
                    <td className={numClass}>{fmtInt(c.calls)}</td>
                    <td className={numClass}>{fmtInt(c.users)}</td>
                    <td className={cx(numClass, "text-zinc-500 dark:text-zinc-400")} title={formatDateTime(c.last_at)}>
                      {timeAgo(c.last_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </Section>

      <Section title="Eventos recentes" subtitle="As últimas chamadas registradas. Parâmetros sem segredos.">
        <RecentTable report={report} />
      </Section>
    </div>
  );
}

function RecentTable({ report }: { report: UsageReport }) {
  return (
    <Panel className="overflow-hidden">
      <div className="max-h-[32rem] overflow-auto">
        <table className="w-full min-w-[1000px] text-xs">
          <thead className={cx(theadClass, "sticky top-0 z-10")}>
            <tr>
              <th className={thClass}>Quando</th>
              <th className={thClass}>Quem</th>
              <th className={thClass}>Canal</th>
              <th className={thClass}>Ação</th>
              <th className={thClass}>Cliente</th>
              <th className={thClass}>Status</th>
              <th className={cx(thClass, "text-right")}>Duração</th>
              <th className={cx(thClass, "text-right")}>Result.</th>
              <th className={cx(thClass, "text-right")}>Resposta</th>
              <th className={cx(thClass, "text-right")}>Custo</th>
              <th className={thClass}>Parâmetros</th>
            </tr>
          </thead>
          <tbody className={tbodyClass}>
            {report.recent.map((e) => (
              <tr key={e.id} className={rowClass}>
                <td className="px-3 py-2 whitespace-nowrap" title={formatDateTime(e.at)}>
                  {timeAgo(e.at)}
                </td>
                <td className="max-w-48 truncate px-3 py-2 text-zinc-700 dark:text-zinc-300" title={e.email ?? undefined}>
                  {e.email ?? "—"}
                </td>
                <td className="px-3 py-2">
                  <ChannelBadge channel={e.channel} />
                </td>
                <td className="max-w-56 truncate px-3 py-2 font-mono" title={e.action}>
                  {e.action}
                </td>
                <td className="px-3 py-2 whitespace-nowrap text-zinc-500 dark:text-zinc-400">
                  {e.client ?? "—"}
                  {e.client_version && <span className="ml-1 font-mono">{e.client_version}</span>}
                </td>
                <td className="px-3 py-2">
                  <Badge tone={e.status >= 400 ? "error" : "indexed"}>{e.status}</Badge>
                </td>
                <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap">{fmtMs(e.duration_ms)}</td>
                <td className={cx("px-3 py-2 text-right tabular-nums", e.result_count === 0 && "text-amber-700 dark:text-amber-300")}>
                  {e.result_count ?? "—"}
                </td>
                <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap">{e.response_bytes === null ? "—" : fmtBytes(e.response_bytes)}</td>
                <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap">{e.cost_usd > 0 ? fmtUsd(e.cost_usd) : "—"}</td>
                <td className="max-w-xs px-3 py-2">
                  <code className="block truncate font-mono text-zinc-500" title={e.params ? JSON.stringify(e.params) : undefined}>
                    {e.params ? JSON.stringify(e.params) : "—"}
                  </code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
