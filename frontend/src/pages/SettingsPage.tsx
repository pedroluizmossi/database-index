import { useEffect, useState } from "react";
import { BarChart3, RotateCcw, ShieldOff } from "lucide-react";
import { describeError } from "../api/client";
import { api } from "../api/endpoints";
import type { PrefSchema, PrefValue, UsageReport } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useToast } from "../components/Toaster";
import { Alert, Badge, Button, EmptyState, Panel, Segmented, Select, Skeleton, Switch, inputClass } from "../components/ui";
import { ActionTable, Section } from "../components/usage";
import { PERIODS, UsageKpis, type Period } from "./UsagePage";
import { usePrefs } from "../data/PrefsProvider";
import { canWrite } from "../lib/capabilities";
import { formatDateTime } from "../lib/utils";

/** Configurações da própria pessoa. O formulário vem do registro do servidor: config nova aparece aqui sozinha. */
export function SettingsPage() {
  const { session } = useAuth();
  const { info, update } = usePrefs();
  const { push } = useToast();
  const [busy, setBusy] = useState<string | null>(null);

  if (!canWrite(session)) {
    return (
      <EmptyState
        icon={ShieldOff}
        title="Configurações são alteradas com login no navegador"
        text="Entre com a sua conta Google ou Microsoft. Uma API key colada na tela só lê as configurações do dono."
      />
    );
  }
  if (!info) return <Alert>Não consegui carregar as suas configurações. Recarregue a página.</Alert>;

  async function save(pref: PrefSchema, value: PrefValue | null) {
    setBusy(pref.key);
    try {
      await update({ [pref.key]: value });
    } catch (err) {
      push({ tone: "error", title: `Não consegui salvar “${pref.label}”`, description: describeError(err) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Configurações</h1>
        <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
          {info.display_name ? `${info.display_name} · ` : ""}
          {info.email} · último login {formatDateTime(info.last_login_at)}
        </p>
      </div>

      <Panel className="divide-y divide-zinc-100 dark:divide-zinc-800">
        {info.schema.map((pref) => {
          const changed = info.changed.includes(pref.key);
          return (
            <div key={pref.key} className="flex flex-wrap items-center gap-x-6 gap-y-3 px-5 py-4">
              <div className="min-w-0 flex-1 basis-56">
                <p className="flex items-center gap-2 text-sm font-medium">
                  {pref.label}
                  {!changed && <Badge tone="neutral">padrão</Badge>}
                </p>
                <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">{hint(pref)}</p>
              </div>
              <div className="flex items-center gap-2">
                <PrefControl
                  pref={pref}
                  value={info.settings[pref.key]}
                  disabled={busy === pref.key}
                  onChange={(value) => void save(pref, value)}
                />
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!changed || busy === pref.key}
                  onClick={() => void save(pref, null)}
                  aria-label={`Voltar “${pref.label}” ao padrão`}
                  title="Voltar ao padrão"
                >
                  <RotateCcw className="size-4" aria-hidden="true" />
                </Button>
              </div>
            </div>
          );
        })}
      </Panel>

      <MyUsage />
    </div>
  );
}

/** Resumo do uso do próprio usuário: todas as credenciais dele (navegador, tokens e clientes MCP). */
function MyUsage() {
  const [days, setDays] = useState<Period>("30");
  const [report, setReport] = useState<UsageReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.myUsage(Number(days)).then(
      (r) => {
        if (!alive) return;
        setReport(r);
        setError(null);
      },
      (err: unknown) => alive && setError(describeError(err)),
    );
    return () => {
      alive = false;
    };
  }, [days]);

  return (
    <Section
      title="Meu uso"
      subtitle="Somando esta interface, os seus tokens e os seus clientes MCP. Custo estimado na Voyage."
      actions={<Segmented label="Período" options={[...PERIODS]} value={days} onChange={setDays} />}
    >
      {error && <Alert>{error}</Alert>}
      {!report && !error && <Skeleton className="h-48 rounded-xl" />}
      {report && report.totals.calls === 0 && (
        <Panel>
          <EmptyState icon={BarChart3} title="Nenhum uso no período" text="Use a busca ou conecte um cliente MCP e os números aparecem aqui." />
        </Panel>
      )}
      {report && report.totals.calls > 0 && (
        <div className="space-y-4">
          <UsageKpis report={report} showUsers={false} />
          <ActionTable rows={report.by_action} compact />
        </div>
      )}
    </Section>
  );
}

function hint(pref: PrefSchema): string {
  const scope = pref.applies_to === "search" ? "Vale também para os seus tokens e clientes MCP." : "Vale só nesta interface.";
  const range = pref.type === "int" && pref.minimum !== undefined && pref.maximum !== undefined ? ` De ${pref.minimum} a ${pref.maximum}.` : "";
  return scope + range;
}

function PrefControl({
  pref,
  value,
  disabled,
  onChange,
}: {
  pref: PrefSchema;
  value: PrefValue;
  disabled: boolean;
  onChange: (value: PrefValue) => void;
}) {
  if (pref.type === "choice" && pref.choices) {
    return pref.choices.length <= 4 ? (
      <Segmented label={pref.label} options={pref.choices} value={String(value)} onChange={onChange} />
    ) : (
      <Select label={pref.label} value={String(value)} onChange={onChange}>
        {pref.choices.map((c) => (
          <option key={c.value} value={c.value}>
            {c.label}
          </option>
        ))}
      </Select>
    );
  }
  if (pref.type === "bool") return <Switch label={pref.label} checked={value === true} onChange={onChange} />;
  return <DraftInput pref={pref} value={value} disabled={disabled} onChange={onChange} />;
}

/** Número ou texto: grava ao sair do campo ou com Enter, não a cada tecla. */
function DraftInput({
  pref,
  value,
  disabled,
  onChange,
}: {
  pref: PrefSchema;
  value: PrefValue;
  disabled: boolean;
  onChange: (value: PrefValue) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const numeric = pref.type === "int";

  function commit() {
    const text = draft.trim();
    if (text === String(value)) return;
    if (numeric) {
      const n = Number(text);
      const ok = text !== "" && Number.isInteger(n) && n >= (pref.minimum ?? -Infinity) && n <= (pref.maximum ?? Infinity);
      if (!ok) {
        setDraft(String(value)); // fora da faixa: volta ao valor salvo
        return;
      }
      onChange(n);
    } else {
      onChange(text);
    }
  }

  return (
    <input
      type={numeric ? "number" : "text"}
      inputMode={numeric ? "numeric" : undefined}
      min={pref.minimum}
      max={pref.maximum}
      value={draft}
      disabled={disabled}
      aria-label={pref.label}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
      }}
      className={`${inputClass} ${numeric ? "w-24 tabular-nums" : "w-56"}`}
    />
  );
}
