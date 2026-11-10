import { useState, type ReactNode } from "react";
import { TriangleAlert, type LucideIcon } from "lucide-react";
import type { UsageAction, UsageChannel, UsageDay, UsageQuery } from "../api/types";
import { cx, formatDateTime, timeAgo } from "../lib/utils";
import { Panel, Segmented, tableHeadClass } from "./ui";

/* Peças do painel de uso (aba "Uso", aba "Usuários" e "Meu uso" nas configurações).
   Gráficos desenhados com divs, sem biblioteca. Cores dos canais: três primeiras posições da paleta
   categórica (azul, laranja, verde-água), validadas para daltonismo em claro e escuro; cada uma tem o
   seu tom no tema escuro. Erros usam a cor de status (rosa) e sempre vêm com ícone e rótulo. */

// ---------- formatação (pt-BR) ----------

const INT = new Intl.NumberFormat("pt-BR");
const DEC1 = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
const COMPACT = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 });
const PCT = new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: 1 });

export const fmtInt = (n: number) => INT.format(Math.round(n));
export const fmtDec = (n: number | null) => (n === null ? "—" : DEC1.format(n));
/** Números grandes: "12,3 mil", "4,5 mi". Abaixo de 10 mil, o número inteiro. */
export const fmtCompact = (n: number) => (Math.abs(n) < 10_000 ? fmtInt(n) : COMPACT.format(n));
export const fmtPct = (part: number, total: number) => (total > 0 ? PCT.format(part / total) : "—");

/** US$ estimado: 4 casas quando o valor é pequeno (custo por consulta fica na casa dos centésimos de centavo). */
export function fmtUsd(n: number): string {
  if (n === 0) return "US$ 0,00";
  const digits = Math.abs(n) < 1 ? 4 : 2;
  return `US$ ${n.toLocaleString("pt-BR", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export function fmtMs(ms: number): string {
  if (ms < 1000) return `${fmtInt(ms)} ms`;
  return `${DEC1.format(ms / 1000)} s`;
}

export function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${fmtInt(bytes)} B`;
  if (bytes < 1024 * 1024) return `${DEC1.format(bytes / 1024)} KB`;
  return `${DEC1.format(bytes / 1024 / 1024)} MB`;
}

/** "2026-11-10" -> "10/11". */
export function fmtDay(day: string, withWeekday = false): string {
  const [y, m, d] = day.split("-").map(Number);
  if (!y || !m || !d) return day;
  const date = new Date(y, m - 1, d);
  return date.toLocaleDateString("pt-BR", withWeekday ? { weekday: "short", day: "2-digit", month: "2-digit" } : { day: "2-digit", month: "2-digit" });
}

// ---------- canais ----------

export const CHANNELS: Record<UsageChannel, { label: string; hint: string; swatch: string }> = {
  mcp: { label: "MCP", hint: "tools chamadas por agents via MCP", swatch: "bg-[#2a78d6] dark:bg-[#3987e5]" },
  api: { label: "API", hint: "REST com token de agent", swatch: "bg-[#eb6834] dark:bg-[#d95926]" },
  ui: { label: "Interface", hint: "esta interface, no navegador", swatch: "bg-[#1baf7a] dark:bg-[#199e70]" },
};
export const CHANNEL_ORDER: UsageChannel[] = ["mcp", "api", "ui"];

function channelMeta(channel: string) {
  return CHANNELS[channel as UsageChannel] ?? { label: channel, hint: channel, swatch: "bg-stone-400 dark:bg-stone-500" };
}

/** Canal com a mesma marca de cor do gráfico: a cor identifica, o texto diz qual é. */
export function ChannelBadge({ channel }: { channel: string }) {
  const meta = channelMeta(channel);
  return (
    <span
      title={meta.hint}
      className="inline-flex items-center gap-1.5 rounded-full bg-stone-500/10 px-2 py-0.5 text-xs font-medium whitespace-nowrap text-stone-700 ring-1 ring-stone-500/20 ring-inset dark:text-stone-300"
    >
      <span className={cx("size-2 rounded-sm", meta.swatch)} aria-hidden="true" />
      {meta.label}
    </span>
  );
}

export type ChannelFilter = "all" | UsageChannel;

export const CHANNEL_FILTERS: { value: ChannelFilter; label: string }[] = [
  { value: "all", label: "Todos" },
  { value: "mcp", label: "MCP" },
  { value: "api", label: "API" },
  { value: "ui", label: "Interface" },
];

// ---------- blocos ----------

/** Número de destaque com rótulo e uma linha de contexto. */
export function StatTile({
  icon: Icon,
  label,
  value,
  hint,
  tone = "neutral",
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  hint?: ReactNode;
  tone?: "neutral" | "warning";
}) {
  return (
    <Panel className="px-4 py-3.5">
      <p className="flex items-center gap-1.5 text-xs font-medium text-stone-500 dark:text-stone-400">
        <Icon className={cx("size-3.5", tone === "warning" ? "text-amber-600 dark:text-amber-400" : "text-stone-400 dark:text-stone-500")} aria-hidden="true" />
        {label}
      </p>
      <p className="mt-1.5 text-2xl font-semibold tracking-tight tabular-nums">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-stone-500 dark:text-stone-400">{hint}</p>}
    </Panel>
  );
}

export function Section({ title, subtitle, actions, children }: { title: string; subtitle?: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold tracking-tight">{title}</h2>
          {subtitle && <p className="mt-0.5 text-xs text-stone-500 dark:text-stone-400">{subtitle}</p>}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

export const thClass = "px-3 py-2.5 font-medium whitespace-nowrap";
export const tbodyClass = "divide-y divide-stone-100 dark:divide-stone-800";
export const rowClass = "transition-colors hover:bg-stone-50 dark:hover:bg-white/[0.02]";
export const numClass = "px-3 py-2.5 text-right tabular-nums whitespace-nowrap";
const numSmClass = "px-3 py-1.5 text-right tabular-nums whitespace-nowrap";

// ---------- gráfico diário ----------

const CHART_HEIGHT = 160;

/** Barras empilhadas por dia e canal. Passar o mouse (ou focar com Tab) mostra o detalhe do dia. */
export function DailyChart({ daily }: { daily: UsageDay[] }) {
  const [active, setActive] = useState<number | null>(null);
  const totals = daily.map((d) => d.mcp + d.api + d.ui);
  const max = Math.max(1, ...totals);
  const hasErrors = daily.some((d) => d.errors > 0);
  const focus = active !== null ? daily[active] : null;
  const ticks = daily.length > 1 ? [0, Math.floor((daily.length - 1) / 2), daily.length - 1] : [0];

  return (
    <div>
      {/* Legenda: sempre presente; a cor nunca é a única pista (o detalhe e a tabela dizem o canal). */}
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-stone-600 dark:text-stone-300">
        {CHANNEL_ORDER.map((c) => (
          <span key={c} className="inline-flex items-center gap-1.5">
            <span className={cx("size-2.5 rounded-sm", CHANNELS[c].swatch)} aria-hidden="true" />
            {CHANNELS[c].label}
          </span>
        ))}
        {hasErrors && (
          <span className="inline-flex items-center gap-1.5">
            <TriangleAlert className="size-3 text-rose-600 dark:text-rose-400" aria-hidden="true" />
            dia com erros
          </span>
        )}
      </div>

      <div className="relative pl-10">
        {/* Eixo e grade discretos: só o máximo e a metade. */}
        <div className="pointer-events-none absolute inset-y-0 left-0 w-full" style={{ height: CHART_HEIGHT }} aria-hidden="true">
          {[1, 0.5].map((f) => (
            <div key={f} className="absolute right-0 left-10 border-t border-dashed border-stone-200 dark:border-stone-800" style={{ top: CHART_HEIGHT * (1 - f) }}>
              <span className="absolute -top-2 -left-10 w-8 text-right text-[10px] text-stone-400 tabular-nums dark:text-stone-500">
                {fmtCompact(Math.round(max * f))}
              </span>
            </div>
          ))}
        </div>

        <div
          role="img"
          aria-label={`Chamadas por dia nos últimos ${daily.length} dias; máximo de ${fmtInt(max)} num dia.`}
          className="relative flex items-end gap-px border-b border-stone-300 dark:border-stone-700"
          style={{ height: CHART_HEIGHT }}
          onMouseLeave={() => setActive(null)}
        >
          {daily.map((d, i) => {
            const total = totals[i];
            return (
              <button
                key={d.day}
                type="button"
                aria-label={`${fmtDay(d.day, true)}: ${fmtInt(total)} chamadas, ${fmtInt(d.errors)} erros`}
                onMouseEnter={() => setActive(i)}
                onFocus={() => setActive(i)}
                onBlur={() => setActive(null)}
                className={cx(
                  "group flex h-full min-w-0 flex-1 flex-col-reverse gap-[2px] rounded-t outline-none focus-visible:bg-accent-500/10",
                  active === i && "bg-stone-900/[0.04] dark:bg-white/[0.04]",
                )}
              >
                {CHANNEL_ORDER.map((c, ci) => {
                  const value = d[c];
                  if (value <= 0) return null;
                  const top = CHANNEL_ORDER.slice(ci + 1).every((k) => d[k] <= 0);
                  return (
                    <span
                      key={c}
                      className={cx("block w-full shrink-0", CHANNELS[c].swatch, top && "rounded-t-[3px]")}
                      style={{ height: Math.max(2, (value / max) * (CHART_HEIGHT - 4)) }}
                    />
                  );
                })}
              </button>
            );
          })}
        </div>

        {/* Faixa de erros: um traço sob o dia que teve erro. */}
        {hasErrors && (
          <div className="mt-1 flex h-1.5 gap-px" aria-hidden="true">
            {daily.map((d) => (
              <span key={d.day} className={cx("flex-1 rounded-full", d.errors > 0 ? "bg-rose-500 dark:bg-rose-400" : "bg-transparent")} />
            ))}
          </div>
        )}

        <div className="relative mt-1.5 h-4 text-[10px] text-stone-400 tabular-nums dark:text-stone-500" aria-hidden="true">
          {ticks.map((t, k) => (
            <span
              key={t}
              className={cx("absolute", k === 0 ? "left-0" : k === ticks.length - 1 ? "right-0" : "-translate-x-1/2")}
              style={k > 0 && k < ticks.length - 1 ? { left: `${((t + 0.5) / daily.length) * 100}%` } : undefined}
            >
              {fmtDay(daily[t].day)}
            </span>
          ))}
        </div>

        {/* Detalhe do dia: faixa fixa abaixo do gráfico, sem cobrir as barras. */}
        <div className="mt-2 min-h-[2.75rem] rounded-lg bg-stone-50 px-3 py-2 text-xs dark:bg-white/[0.03]" aria-live="polite">
          {focus ? (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <span className="font-medium text-stone-900 dark:text-stone-100">{fmtDay(focus.day, true)}</span>
              {CHANNEL_ORDER.map((c) => (
                <span key={c} className="inline-flex items-center gap-1.5 text-stone-600 dark:text-stone-300">
                  <span className={cx("size-2 rounded-sm", CHANNELS[c].swatch)} aria-hidden="true" />
                  {CHANNELS[c].label} <span className="font-medium tabular-nums text-stone-900 dark:text-stone-100">{fmtInt(focus[c])}</span>
                </span>
              ))}
              <span className={cx("inline-flex items-center gap-1", focus.errors > 0 ? "text-rose-700 dark:text-rose-300" : "text-stone-500 dark:text-stone-400")}>
                {focus.errors > 0 && <TriangleAlert className="size-3" aria-hidden="true" />}
                {fmtInt(focus.errors)} {focus.errors === 1 ? "erro" : "erros"}
              </span>
              <span className="text-stone-500 dark:text-stone-400">{fmtInt(focus.users)} {focus.users === 1 ? "usuário" : "usuários"}</span>
              <span className="text-stone-500 dark:text-stone-400">{fmtUsd(focus.cost_usd)}</span>
            </div>
          ) : (
            <span className="text-stone-500 dark:text-stone-400">Passe o mouse sobre um dia para ver o detalhe.</span>
          )}
        </div>
      </div>

      {/* Visão em tabela, para leitor de tela e para conferir números. */}
      <details className="mt-3 text-xs">
        <summary className="cursor-pointer text-stone-500 select-none hover:text-stone-900 dark:text-stone-400 dark:hover:text-white">Ver como tabela</summary>
        <div className="mt-2 max-h-72 overflow-auto rounded-lg border border-stone-200 dark:border-stone-800">
          <table className="w-full text-xs">
            <thead className={tableHeadClass}>
              <tr>
                <th className={thClass}>Dia</th>
                <th className={cx(thClass, "text-right")}>MCP</th>
                <th className={cx(thClass, "text-right")}>API</th>
                <th className={cx(thClass, "text-right")}>Interface</th>
                <th className={cx(thClass, "text-right")}>Erros</th>
                <th className={cx(thClass, "text-right")}>Usuários</th>
                <th className={cx(thClass, "text-right")}>Custo</th>
              </tr>
            </thead>
            <tbody className={tbodyClass}>
              {[...daily].reverse().map((d) => (
                <tr key={d.day}>
                  <td className="px-3 py-1.5 whitespace-nowrap">{fmtDay(d.day, true)}</td>
                  <td className={numSmClass}>{fmtInt(d.mcp)}</td>
                  <td className={numSmClass}>{fmtInt(d.api)}</td>
                  <td className={numSmClass}>{fmtInt(d.ui)}</td>
                  <td className={numSmClass}>{fmtInt(d.errors)}</td>
                  <td className={numSmClass}>{fmtInt(d.users)}</td>
                  <td className={numSmClass}>{fmtUsd(d.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

// ---------- tabela por tool ----------

/** Consumo por tool/rota, ordenado por chamadas. `compact` esconde colunas secundárias (tela "Meu uso"). */
export function ActionTable({ rows, compact = false }: { rows: UsageAction[]; compact?: boolean }) {
  const [filter, setFilter] = useState<ChannelFilter>(() => (rows.some((r) => r.channel === "mcp") ? "mcp" : "all"));
  const shown = rows.filter((r) => filter === "all" || r.channel === filter).sort((a, b) => b.calls - a.calls);
  const maxCalls = Math.max(1, ...shown.map((r) => r.calls));

  return (
    <div className="space-y-3">
      <Segmented label="Canal" options={CHANNEL_FILTERS} value={filter} onChange={setFilter} />
      <Panel className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className={cx("w-full text-sm", compact ? "min-w-[640px]" : "min-w-[1100px]")}>
            <thead className={tableHeadClass}>
              <tr>
                <th className={thClass}>Canal</th>
                <th className={thClass}>Tool / rota</th>
                <th className={cx(thClass, "text-right")}>Chamadas</th>
                <th className={cx(thClass, "text-right")}>Erros</th>
                {!compact && <th className={cx(thClass, "text-right")}>Usuários</th>}
                <th className={cx(thClass, "text-right")} title="Mediana e percentil 95 da latência">p50 / p95</th>
                {!compact && <th className={cx(thClass, "text-right")} title="Média de resultados por chamada">Média result.</th>}
                <th className={cx(thClass, "text-right")} title="Chamadas que voltaram sem resultado">Sem result.</th>
                <th className={cx(thClass, "text-right")} title="Contexto do agent por chamada (~tokens; só MCP)">Contexto / chamada</th>
                <th className={cx(thClass, "text-right")} title="Contexto do agent no total (~tokens; só MCP)">Contexto total</th>
                {!compact && <th className={cx(thClass, "text-right")} title="Tokens enviados à Voyage: embedding + rerank">Tokens Voyage</th>}
                <th className={cx(thClass, "text-right")}>Custo</th>
              </tr>
            </thead>
            <tbody className={tbodyClass}>
              {shown.length === 0 && (
                <tr>
                  <td colSpan={12} className="px-4 py-8 text-center text-sm text-stone-500 dark:text-stone-400">
                    Nada nesse canal no período.
                  </td>
                </tr>
              )}
              {shown.map((r) => (
                <tr key={`${r.channel}:${r.action}`} className={rowClass}>
                  <td className="px-3 py-2.5">
                    <ChannelBadge channel={r.channel} />
                  </td>
                  <td className="max-w-72 px-3 py-2.5">
                    <span className="block truncate font-mono text-xs" title={`${r.action} · última ${formatDateTime(r.last_at)}`}>
                      {r.action}
                    </span>
                  </td>
                  <td className={numClass}>
                    <div className="flex items-center justify-end gap-2">
                      <span className="hidden h-1.5 w-16 overflow-hidden rounded-full bg-stone-100 sm:block dark:bg-stone-800" aria-hidden="true">
                        <span className={cx("block h-full rounded-full", channelMeta(r.channel).swatch)} style={{ width: `${(r.calls / maxCalls) * 100}%` }} />
                      </span>
                      {fmtInt(r.calls)}
                    </div>
                  </td>
                  <td className={cx(numClass, r.errors > 0 && "text-rose-700 dark:text-rose-300")}>
                    {r.errors > 0 ? `${fmtInt(r.errors)} (${fmtPct(r.errors, r.calls)})` : "0"}
                  </td>
                  {!compact && <td className={numClass}>{fmtInt(r.users)}</td>}
                  <td className={cx(numClass, "text-stone-600 dark:text-stone-300")}>
                    {fmtMs(r.p50_ms)} / {fmtMs(r.p95_ms)}
                  </td>
                  {!compact && <td className={numClass}>{fmtDec(r.avg_results)}</td>}
                  <td className={cx(numClass, r.empty_results > 0 && "text-amber-700 dark:text-amber-300")}>{fmtInt(r.empty_results)}</td>
                  <td className={numClass}>{r.avg_context_tokens === null ? "—" : `~${fmtCompact(r.avg_context_tokens)}`}</td>
                  <td className={numClass}>{r.context_tokens === null ? "—" : `~${fmtCompact(r.context_tokens)}`}</td>
                  {!compact && (
                    <td className={numClass} title={`embedding ${fmtInt(r.embed_tokens)} · rerank ${fmtInt(r.rerank_tokens)}`}>
                      {fmtCompact(r.embed_tokens + r.rerank_tokens)}
                    </td>
                  )}
                  <td className={numClass}>{fmtUsd(r.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

// ---------- consultas ----------

export function QueryList({ title, subtitle, rows, empty, tone = "neutral" }: { title: string; subtitle: string; rows: UsageQuery[]; empty: string; tone?: "neutral" | "warning" }) {
  return (
    <Panel className="flex flex-col overflow-hidden">
      <div className="border-b border-stone-100 px-4 py-3 dark:border-stone-800">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold">
          {tone === "warning" && <TriangleAlert className="size-3.5 text-amber-600 dark:text-amber-400" aria-hidden="true" />}
          {title}
        </h3>
        <p className="mt-0.5 text-xs text-stone-500 dark:text-stone-400">{subtitle}</p>
      </div>
      {rows.length === 0 ? (
        <p className="px-4 py-8 text-center text-sm text-stone-500 dark:text-stone-400">{empty}</p>
      ) : (
        <ol className="max-h-96 divide-y divide-stone-100 overflow-y-auto dark:divide-stone-800">
          {rows.map((q) => (
            <li key={`${q.action}:${q.query}`} className="flex items-start gap-3 px-4 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="truncate font-mono text-xs text-stone-900 dark:text-stone-100" title={q.query}>
                  {q.query}
                </p>
                <p className="mt-0.5 truncate text-[11px] text-stone-500 dark:text-stone-400">
                  {q.action} · {fmtInt(q.users)} {q.users === 1 ? "usuário" : "usuários"}
                  {q.avg_results !== null && ` · média ${fmtDec(q.avg_results)} result.`} · <span title={formatDateTime(q.last_at)}>{timeAgo(q.last_at)}</span>
                </p>
              </div>
              <span className="shrink-0 text-sm font-medium tabular-nums">{fmtInt(q.calls)}×</span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}
