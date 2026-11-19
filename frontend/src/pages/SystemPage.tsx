import { useCallback, useEffect, useMemo, useState } from "react";
import { Cpu, Database, GitBranch, HardDrive, Layers, RefreshCw, Search } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { SystemInfo, SystemModelHealth, SystemSetting } from "../api/types";
import { Alert, Badge, Button, Panel, Skeleton, inputClass, tableHeadClass, type BadgeTone } from "../components/ui";
import { cx, formatDateTime } from "../lib/utils";

const numberFormat = new Intl.NumberFormat("pt-BR");

function formatBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toLocaleString("pt-BR", { maximumFractionDigits: i >= 3 ? 1 : 0 })} ${units[i]}`;
}

const MODEL_STATUS: Record<SystemModelHealth["status"], { label: string; tone: BadgeTone }> = {
  ok: { label: "Respondendo", tone: "indexed" },
  warning: { label: "Atenção", tone: "warning" },
  error: { label: "Sem resposta", tone: "error" },
  hosted: { label: "Hospedado", tone: "neutral" },
  off: { label: "Desligado", tone: "ignored" },
};

function originBadge(origin: string): { label: string; tone: BadgeTone; title: string } {
  if (origin === "env") return { label: "ambiente", tone: "accent", title: "Definido por variável de ambiente ou .env" };
  if (origin.startsWith("profile:")) {
    const name = origin.slice("profile:".length);
    return { label: `perfil ${name}`, tone: "pending", title: `Padrão do perfil de organização ${name}` };
  }
  return { label: "padrão", tone: "neutral", title: "Padrão do núcleo (nada definido)" };
}

function formatValue(s: SystemSetting): string {
  if (s.secret) return s.defined ? "definido" : "não definido";
  const v = s.value;
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "—";
  if (typeof v === "boolean") return v ? "sim" : "não";
  if (typeof v === "number") return numberFormat.format(v);
  return String(v).replace(/\n/g, "\\n");
}

/**
 * Sistema (admin, só leitura): saúde das dependências (servidor de modelos, GitHub, banco, disco), perfis de
 * organização ativos e a configuração efetiva com a origem de cada valor. Segredos aparecem só como
 * definido/não definido. Nada aqui gera custo: o servidor de modelos é sondado sem embedding.
 */
export function SystemPage() {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState("");
  const [onlyDefined, setOnlyDefined] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setInfo(await api.system());
      setError(null);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const groups = useMemo(() => {
    if (!info) return [];
    const q = filter.trim().toLowerCase();
    return info.settings
      .map((g) => ({
        ...g,
        settings: g.settings.filter(
          (s) =>
            (!onlyDefined || s.origin !== "default") &&
            (!q || s.name.includes(q) || s.env.toLowerCase().includes(q) || (s.hint ?? "").toLowerCase().includes(q)),
        ),
      }))
      .filter((g) => g.settings.length > 0);
  }, [info, filter, onlyDefined]);

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Sistema</h1>
          <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">
            Saúde das dependências, perfis ativos e a configuração efetiva do servidor. Só leitura; segredos nunca aparecem.
          </p>
        </div>
        <Button variant="secondary" size="sm" loading={busy} onClick={() => void load()}>
          <RefreshCw className="size-3.5" aria-hidden="true" />
          Atualizar
        </Button>
      </div>

      {error && <Alert>{error}</Alert>}

      {!info && !error ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-28 rounded-xl" />
          ))}
        </div>
      ) : info ? (
        <>
          <Health info={info} />
          <Profiles info={info} />
          <section aria-labelledby="system-settings" className="space-y-3">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <h2 id="system-settings" className="font-mono text-[10px] tracking-[0.16em] text-stone-500 uppercase dark:text-stone-400">
                Configuração efetiva
              </h2>
              <div className="flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-2 text-sm text-stone-600 dark:text-stone-300">
                  <input type="checkbox" checked={onlyDefined} onChange={(e) => setOnlyDefined(e.target.checked)} />
                  só o que não é padrão
                </label>
                <div className="relative w-64">
                  <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-stone-400" aria-hidden="true" />
                  <input
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                    className={cx(inputClass, "pl-8")}
                    placeholder="Filtrar (nome, variável, descrição)"
                    aria-label="Filtrar configurações"
                  />
                </div>
              </div>
            </div>
            {groups.length === 0 ? (
              <Panel className="px-5 py-4 text-sm text-stone-500 dark:text-stone-400">Nenhuma configuração casa com o filtro.</Panel>
            ) : (
              groups.map((g) => <SettingsGroup key={g.id} label={g.label} settings={g.settings} />)
            )}
          </section>
          <p className="text-xs text-stone-500 dark:text-stone-400">
            {info.app.version ? `code-index ${info.app.version} · ` : ""}Python {info.app.python} · servido por {info.app.host}
            {" · "}worker {info.app.worker_embedded ? "embutido na API" : "separado"}
          </p>
        </>
      ) : null}
    </div>
  );
}

function Card({ icon: Icon, title, badge, children }: { icon: typeof Cpu; title: string; badge?: { label: string; tone: BadgeTone }; children: React.ReactNode }) {
  return (
    <Panel className="space-y-2 px-4 py-3.5">
      <div className="flex items-center gap-2">
        <Icon className="size-4 shrink-0 text-stone-500" aria-hidden="true" />
        <h3 className="min-w-0 flex-1 truncate font-medium">{title}</h3>
        {badge && (
          <Badge tone={badge.tone} dot>
            {badge.label}
          </Badge>
        )}
      </div>
      <div className="space-y-1 text-sm text-stone-600 dark:text-stone-300">{children}</div>
    </Panel>
  );
}

function Health({ info }: { info: SystemInfo }) {
  const { models, github, database, disks } = info.health;
  const running = database?.queue.running ?? 0;
  const queued = database?.queue.queued ?? 0;
  return (
    <section aria-labelledby="system-health" className="space-y-3">
      <h2 id="system-health" className="font-mono text-[10px] tracking-[0.16em] text-stone-500 uppercase dark:text-stone-400">
        Saúde
      </h2>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {models.map((m) => (
          <Card key={m.role} icon={Cpu} title={m.role === "embedding" ? "Embedding" : "Rerank"} badge={MODEL_STATUS[m.status]}>
            <p className="truncate font-mono text-xs" title={m.model}>{m.model || "—"}</p>
            {m.api_base && <p className="truncate font-mono text-xs text-stone-500" title={m.api_base}>{m.api_base}</p>}
            <p className="text-xs text-stone-500 dark:text-stone-400">{m.detail}</p>
          </Card>
        ))}
        <Card
          icon={GitBranch}
          title="GitHub"
          badge={!github.token ? { label: "Sem token", tone: "warning" } : github.rate_remaining == null ? { label: "Sem resposta", tone: "error" } : github.rate_remaining <= github.reserve ? { label: "Na reserva", tone: "warning" } : { label: "OK", tone: "indexed" }}
        >
          <p>
            Cota restante: <span className="font-medium tabular-nums">{github.rate_remaining == null ? "—" : numberFormat.format(github.rate_remaining)}</span>
            <span className="text-stone-500"> (reserva {numberFormat.format(github.reserve)})</span>
          </p>
          <p className="text-xs text-stone-500 dark:text-stone-400">
            Estimativa de arquivos: até {numberFormat.format(github.estimate_max)} chamadas por pedido
          </p>
        </Card>
        {database && (
          <Card icon={Database} title="Banco" badge={{ label: `Postgres ${database.version.split(" ")[0]}`, tone: "neutral" }}>
            <p>
              <span className="font-medium tabular-nums">{formatBytes(database.bytes)}</span>
              <span className="text-stone-500"> · schema </span>
              <span className="font-mono text-xs">{database.schema}</span>
            </p>
            <p className="text-xs text-stone-500 dark:text-stone-400">
              {database.migrations.count} migrations; última {database.migrations.last ?? "—"}
              {database.migrations.last_applied_at ? ` (${formatDateTime(database.migrations.last_applied_at)})` : ""}
            </p>
            <p className="text-xs text-stone-500 dark:text-stone-400">
              {Object.entries(database.extensions).map(([k, v]) => `${k} ${v}`).join(" · ")}
            </p>
            <p className="text-xs text-stone-500 dark:text-stone-400">
              Fila de sync: {running} rodando · {queued} na fila
            </p>
          </Card>
        )}
        {disks.map((d) => {
          const pct = d.total ? Math.round((d.used / d.total) * 100) : 0;
          return (
            <Card key={d.path} icon={HardDrive} title={d.label} badge={pct >= 90 ? { label: `${pct}%`, tone: "error" } : pct >= 80 ? { label: `${pct}%`, tone: "warning" } : { label: `${pct}%`, tone: "indexed" }}>
              <div className="h-1.5 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-800" aria-hidden="true">
                <div className={cx("h-full rounded-full", pct >= 90 ? "bg-rose-500" : pct >= 80 ? "bg-amber-500" : "bg-emerald-500")} style={{ width: `${pct}%` }} />
              </div>
              <p>
                <span className="font-medium tabular-nums">{formatBytes(d.free)}</span> livres de {formatBytes(d.total)}
              </p>
              <p className="truncate font-mono text-xs text-stone-500" title={d.path}>{d.path}</p>
            </Card>
          );
        })}
      </div>
      {database && database.tables.length > 0 && (
        <details className="group">
          <summary className="cursor-pointer text-sm text-stone-600 select-none hover:text-stone-900 dark:text-stone-300 dark:hover:text-white">
            Maiores tabelas do schema
          </summary>
          <Panel className="mt-2 overflow-hidden">
            <table className="w-full text-sm">
              <thead className={tableHeadClass}>
                <tr>
                  <th className="px-4 py-2 font-medium">Tabela</th>
                  <th className="px-4 py-2 text-right font-medium">Linhas (aprox.)</th>
                  <th className="px-4 py-2 text-right font-medium">Tamanho</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-200 dark:divide-stone-800">
                {database.tables.map((t) => (
                  <tr key={t.name}>
                    <td className="px-4 py-2 font-mono text-xs">{t.name}</td>
                    <td className="px-4 py-2 text-right tabular-nums">{numberFormat.format(t.rows)}</td>
                    <td className="px-4 py-2 text-right tabular-nums">{formatBytes(t.bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        </details>
      )}
    </section>
  );
}

function Profiles({ info }: { info: SystemInfo }) {
  return (
    <section aria-labelledby="system-profiles" className="space-y-3">
      <h2 id="system-profiles" className="font-mono text-[10px] tracking-[0.16em] text-stone-500 uppercase dark:text-stone-400">
        Perfis de organização ativos
      </h2>
      {info.profiles.length === 0 ? (
        <Panel className="px-5 py-4 text-sm text-stone-500 dark:text-stone-400">
          Nenhum perfil ativo: comportamento genérico do núcleo (CODE_INDEX_PROFILES vazio ou "default").
        </Panel>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {info.profiles.map((p) => (
            <Card key={p.name} icon={Layers} title={p.label} badge={{ label: p.name, tone: "accent" }}>
              <p className="text-xs text-stone-500 dark:text-stone-400">
                Origens de vínculo: {p.origins.length ? p.origins.join(", ") : "nenhuma"}
              </p>
              <p className="text-xs text-stone-500 dark:text-stone-400">
                Contratos lidos: {p.contract_formats.length ? p.contract_formats.join(", ") : "nenhum"}
              </p>
            </Card>
          ))}
        </div>
      )}
    </section>
  );
}

function SettingsGroup({ label, settings }: { label: string; settings: SystemSetting[] }) {
  return (
    <Panel className="overflow-hidden">
      <div className="border-b border-stone-200 px-4 py-2.5 dark:border-stone-800">
        <h3 className="font-semibold">{label}</h3>
      </div>
      <ul className="divide-y divide-stone-200 dark:divide-stone-800">
        {settings.map((s) => {
          const origin = originBadge(s.origin);
          return (
            <li key={s.name} className="grid gap-x-4 gap-y-1 px-4 py-2.5 text-sm md:grid-cols-[minmax(14rem,18rem)_1fr_auto]">
              <div className="min-w-0">
                <p className="truncate font-mono text-xs font-medium text-stone-900 dark:text-stone-100" title={s.env}>{s.env}</p>
                {s.hint && <p className="mt-0.5 text-xs text-stone-500 dark:text-stone-400">{s.hint}</p>}
              </div>
              <p
                className={cx(
                  "min-w-0 font-mono text-xs break-all",
                  s.secret ? "text-stone-500 italic" : s.defined ? "text-stone-800 dark:text-stone-200" : "text-stone-400",
                )}
              >
                {formatValue(s)}
              </p>
              <span title={origin.title} className="justify-self-start md:justify-self-end">
                <Badge tone={origin.tone}>{origin.label}</Badge>
              </span>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
