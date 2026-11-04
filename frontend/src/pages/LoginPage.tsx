import { useState, type FormEvent } from "react";
import { ChevronDown, Eye, EyeOff, KeyRound, SearchCode } from "lucide-react";
import { describeError } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { Backdrop, Alert, Button, Panel } from "../components/ui";

export function LoginPage() {
  const { signInWithGoogle, signInWithToken, notice } = useAuth();
  const [token, setToken] = useState("");
  const [remember, setRemember] = useState(false);
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = token.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    try {
      await signInWithToken(value, remember);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  const message = error ?? notice;

  return (
    <div className="relative flex min-h-dvh items-center justify-center px-4 py-12">
      <Backdrop />
      <div className="w-full max-w-md animate-fade-up">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="mb-5 flex size-14 items-center justify-center rounded-2xl bg-linear-to-br from-indigo-500 to-violet-600 text-white shadow-xl shadow-indigo-500/30">
            <SearchCode className="size-7" aria-hidden="true" />
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">Entrar no code-index</h1>
          <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">
            Busque no código dos repositórios indexados.
          </p>
        </div>

        <Panel className="space-y-6 p-6 sm:p-8">
          {message && <Alert>{message}</Alert>}

          <Button type="button" onClick={signInWithGoogle} className="h-12 w-full gap-3 text-[15px]" variant="secondary">
            <GoogleMark />
            Entrar com Google
          </Button>

          <details className="group rounded-xl border border-zinc-200 dark:border-zinc-800">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-4 py-3 text-sm font-medium text-zinc-600 dark:text-zinc-300">
              <span className="inline-flex items-center gap-2">
                <KeyRound className="size-4" aria-hidden="true" />
                Usar API key (agents e scripts)
              </span>
              <ChevronDown className="size-4 transition group-open:rotate-180" aria-hidden="true" />
            </summary>

            <form onSubmit={submit} className="space-y-5 border-t border-zinc-200 p-4 dark:border-zinc-800">
              <div className="space-y-2">
                <label htmlFor="api-key" className="text-sm font-medium">
                  API key
                </label>
                <div className="relative">
                  <input
                    id="api-key"
                    type={reveal ? "text" : "password"}
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="cix_…"
                    className="h-11 w-full rounded-xl border border-zinc-200 bg-white pr-11 pl-4 font-mono text-sm text-zinc-900 shadow-sm outline-none transition placeholder:text-zinc-400 hover:border-zinc-300 focus:border-indigo-500 focus:ring-4 focus:ring-indigo-500/15 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:border-zinc-600"
                  />
                  <button
                    type="button"
                    onClick={() => setReveal((v) => !v)}
                    aria-label={reveal ? "Ocultar chave" : "Mostrar chave"}
                    className="absolute top-1/2 right-2 inline-flex size-8 -translate-y-1/2 items-center justify-center rounded-lg text-zinc-400 transition hover:bg-zinc-900/5 hover:text-zinc-700 dark:hover:bg-white/10 dark:hover:text-zinc-200"
                  >
                    {reveal ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                  </button>
                </div>
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  Gere a sua com <code className="font-mono">code-index token create</code>.
                </p>
              </div>

              <label className="flex cursor-pointer items-center gap-2.5 text-sm text-zinc-600 dark:text-zinc-300">
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={(e) => setRemember(e.target.checked)}
                  className="size-4 cursor-pointer accent-indigo-600"
                />
                Lembrar neste navegador
              </label>

              <Button type="submit" loading={busy} disabled={!token.trim()} className="w-full">
                Entrar com a API key
              </Button>
            </form>
          </details>
        </Panel>
      </div>
    </div>
  );
}

function GoogleMark() {
  return (
    <svg viewBox="0 0 24 24" className="size-5" aria-hidden="true">
      <path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.5c-.3 1.5-1.1 2.7-2.4 3.6v3h3.9c2.3-2.1 3.5-5.2 3.5-8.8z" />
      <path fill="#34A853" d="M12 24c3.2 0 6-1.1 7.9-2.9l-3.9-3c-1.1.7-2.5 1.2-4 1.2-3.1 0-5.7-2.1-6.6-4.9H1.4v3.1C3.4 21.3 7.4 24 12 24z" />
      <path fill="#FBBC05" d="M5.4 14.4c-.2-.7-.4-1.5-.4-2.4s.1-1.6.4-2.4V6.5H1.4C.5 8.2 0 10 0 12s.5 3.8 1.4 5.5l4-3.1z" />
      <path fill="#EA4335" d="M12 4.8c1.7 0 3.3.6 4.5 1.8l3.4-3.4C17.9 1.2 15.2 0 12 0 7.4 0 3.4 2.7 1.4 6.5l4 3.1C6.3 6.9 8.9 4.8 12 4.8z" />
    </svg>
  );
}
