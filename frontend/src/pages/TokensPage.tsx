import { useCallback, useEffect, useState } from "react";
import { Check, Copy, KeyRound, Plus, ShieldOff, TriangleAlert } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { NewToken, Scope, TokenInfo } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Dialog } from "../components/Dialog";
import { useToast } from "../components/Toaster";
import { Alert, Badge, Button, EmptyState, Field, Panel, Skeleton, inputClass } from "../components/ui";
import { canWrite } from "../lib/capabilities";
import { formatDateTime, timeAgo } from "../lib/utils";

const SCOPES: { value: Scope; label: string; hint: string }[] = [
  { value: "search:read", label: "search:read", hint: "buscar e ler arquivos (agents)" },
  { value: "index:write", label: "index:write", hint: "indexar: gera custo na Voyage" },
  { value: "admin", label: "admin", hint: "gerenciar repos, orgs e auditoria" },
];

/** Tokens de agent. Emitir exige login com Google (o servidor também recusa token para emitir token). */
export function TokensPage() {
  const { session } = useAuth();
  const { push } = useToast();
  const [tokens, setTokens] = useState<TokenInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<TokenInfo | null>(null);
  const [created, setCreated] = useState<NewToken | null>(null);
  const writable = canWrite(session);

  const load = useCallback(async () => {
    try {
      setTokens(await api.tokens(false));
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }, []);

  useEffect(() => {
    if (writable) void load();
  }, [writable, load]);

  if (!writable) {
    return (
      <EmptyState
        icon={ShieldOff}
        title="Tokens são gerenciados com login Google"
        text="Entre com a sua conta Google para criar e revogar tokens. Uma API key colada na tela não pode emitir tokens."
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Tokens</h1>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
            Credenciais para agents e MCP. O valor aparece uma única vez, na criação.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="size-4" aria-hidden="true" />
          Novo token
        </Button>
      </div>

      {error && <Alert>{error}</Alert>}

      <Panel className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead className="bg-zinc-50/80 text-left text-xs tracking-wide text-zinc-500 uppercase dark:bg-zinc-900 dark:text-zinc-400">
              <tr>
                <th className="px-4 py-3 font-medium">Nome</th>
                <th className="px-4 py-3 font-medium">Scopes</th>
                <th className="px-4 py-3 font-medium">Dono</th>
                <th className="px-4 py-3 font-medium">Expira</th>
                <th className="px-4 py-3 font-medium">Último uso</th>
                <th className="px-4 py-3 text-right font-medium">Ações</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {tokens === null && !error && (
                <tr>
                  <td className="px-4 py-4" colSpan={6}>
                    <Skeleton className="h-5 w-full" />
                  </td>
                </tr>
              )}
              {tokens !== null && tokens.length === 0 && (
                <tr>
                  <td colSpan={6}>
                    <EmptyState icon={KeyRound} title="Nenhum token ativo" />
                  </td>
                </tr>
              )}
              {tokens?.map((t) => (
                <tr key={t.id} className="hover:bg-zinc-50/80 dark:hover:bg-white/[0.02]">
                  <td className="px-4 py-3">
                    <p className="font-medium">{t.name}</p>
                    <p className="font-mono text-xs text-zinc-500">{t.prefix}…</p>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {t.scopes.map((s) => (
                        <Badge key={s} tone="accent">{s}</Badge>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-300">{t.owner_email}</td>
                  <td className="px-4 py-3" title={formatDateTime(t.expires_at)}>
                    {t.expires_at ? timeAgo(t.expires_at) : "sem prazo"}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-300">{timeAgo(t.last_used_at)}</td>
                  <td className="px-4 py-3 text-right">
                    {t.active ? (
                      <Button variant="ghost" size="sm" onClick={() => setRevokeTarget(t)} aria-label={`Revogar ${t.name}`}>
                        Revogar
                      </Button>
                    ) : (
                      <Badge tone="ignored">{t.revoked_at ? "revogado" : "expirado"}</Badge>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <CreateTokenDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(token) => {
          setCreated(token);
          void load();
        }}
      />
      <ShowOnceDialog token={created} onClose={() => setCreated(null)} />
      <ConfirmDialog
        open={revokeTarget !== null}
        onClose={() => setRevokeTarget(null)}
        title="Revogar token"
        confirmLabel="Revogar"
        danger
        onConfirm={async () => {
          if (!revokeTarget) return;
          await api.revokeToken(revokeTarget.id);
          push({ tone: "success", title: "Token revogado", description: revokeTarget.name });
          await load();
        }}
      >
        <p>
          <span className="font-medium">{revokeTarget?.name}</span> deixa de funcionar imediatamente. Quem usa esse
          token precisará de um novo.
        </p>
      </ConfirmDialog>
    </div>
  );
}

function CreateTokenDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (token: NewToken) => void;
}) {
  const { session } = useAuth();
  const mine = session?.me.scopes ?? [];
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<Scope[]>(["search:read"]);
  const [days, setDays] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggle(scope: Scope) {
    setScopes((cur) => (cur.includes(scope) ? cur.filter((s) => s !== scope) : [...cur, scope]));
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const token = await api.createToken({
        name: name.trim(),
        scopes,
        ttl_days: days ? Number(days) : null,
      });
      setName("");
      setScopes(["search:read"]);
      setDays("");
      onCreated(token);
      onClose();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Novo token"
      description="Só dá para emitir scopes que você já tem."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancelar
          </Button>
          <Button onClick={() => void submit()} loading={busy} disabled={!name.trim() || scopes.length === 0}>
            Criar token
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        <Field label="Nome">
          <input value={name} onChange={(e) => setName(e.target.value)} className={inputClass} placeholder="ex.: claude-code notebook" maxLength={100} />
        </Field>

        <fieldset className="space-y-2">
          <legend className="mb-2 text-xs font-medium text-zinc-500 dark:text-zinc-400">Scopes</legend>
          {SCOPES.map((s) => {
            const allowed = mine.includes(s.value);
            return (
              <label key={s.value} className={`flex items-start gap-3 rounded-xl border border-zinc-200 p-3 dark:border-zinc-800 ${allowed ? "cursor-pointer" : "opacity-50"}`}>
                <input
                  type="checkbox"
                  className="mt-0.5 size-4 accent-indigo-600"
                  checked={scopes.includes(s.value)}
                  disabled={!allowed}
                  onChange={() => toggle(s.value)}
                />
                <span className="text-sm">
                  <span className="font-mono font-medium">{s.label}</span>
                  <span className="block text-xs text-zinc-500 dark:text-zinc-400">
                    {allowed ? s.hint : "você não tem este scope"}
                  </span>
                </span>
              </label>
            );
          })}
        </fieldset>

        <Field label="Validade em dias (vazio = padrão do servidor, 90)">
          <input
            type="number"
            min={1}
            max={365}
            value={days}
            onChange={(e) => setDays(e.target.value)}
            className={inputClass}
          />
        </Field>

        {error && <Alert>{error}</Alert>}
      </div>
    </Dialog>
  );
}

function ShowOnceDialog({ token, onClose }: { token: NewToken | null; onClose: () => void }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    if (!token) return;
    try {
      await navigator.clipboard.writeText(token.token);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard indisponível: a pessoa copia manualmente
    }
  }

  return (
    <Dialog
      open={token !== null}
      onClose={onClose}
      title="Token criado"
      footer={<Button onClick={onClose}>Já guardei</Button>}
    >
      {token && (
        <div className="space-y-4">
          <div className="flex items-start gap-2.5 rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-300">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span>Copie agora. Este valor não será mostrado de novo.</span>
          </div>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 overflow-x-auto rounded-xl bg-zinc-100 px-3 py-2.5 font-mono text-xs dark:bg-zinc-800">
              {token.token}
            </code>
            <Button variant="secondary" size="sm" onClick={() => void copy()} aria-label="Copiar token">
              {copied ? <Check className="size-4 text-emerald-500" /> : <Copy className="size-4" />}
              {copied ? "Copiado" : "Copiar"}
            </Button>
          </div>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            Scopes: {token.scopes.join(", ")} · expira em {formatDateTime(token.expires_at)}
          </p>
        </div>
      )}
    </Dialog>
  );
}
