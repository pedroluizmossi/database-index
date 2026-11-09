import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api/endpoints";
import { ApiError, describeError, onUnauthorized, setSessionToken } from "../api/client";
import type { Me } from "../api/types";

const STORAGE_KEY = "cix_token";

/**
 * Sessão ativa. `token` é null quando a pessoa entrou pelo navegador (Google ou Microsoft): o servidor guarda
 * a sessão num cookie HttpOnly, que o JavaScript desta página não consegue ler.
 * Quando há `token`, é uma API key colada na tela de login (uso de agents/CLI).
 */
export interface Session {
  token: string | null;
  me: Me;
}

interface AuthValue {
  session: Session | null;
  /** true enquanto a sessão é verificada ao abrir a página. */
  booting: boolean;
  /** Motivo de estar deslogada (sessão expirou, chave salva inválida...). */
  notice: string | null;
  /** Vai para o login do provedor (`google`, `microsoft`), com redirecionamento. */
  signInWith: (provider: string) => void;
  /** Entra com uma API key colada. `remember` guarda a chave neste navegador. */
  signInWithToken: (token: string, remember: boolean) => Promise<void>;
  signOut: (reason?: string) => void;
}

const AuthContext = createContext<AuthValue | null>(null);

function readStored(): { token: string; remember: boolean } | null {
  const local = localStorage.getItem(STORAGE_KEY);
  if (local) return { token: local, remember: true };
  const tab = sessionStorage.getItem(STORAGE_KEY);
  return tab ? { token: tab, remember: false } : null;
}

function clearStored(): void {
  localStorage.removeItem(STORAGE_KEY);
  sessionStorage.removeItem(STORAGE_KEY);
}

function writeStored(token: string, remember: boolean): void {
  clearStored();
  (remember ? localStorage : sessionStorage).setItem(STORAGE_KEY, token);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  // Sempre verifica ao abrir: pode haver cookie de sessão, chave salva, ou nada.
  const [booting, setBooting] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  // Ref para o handler de 401 saber, sem depender de re-render, se havia sessão.
  const sessionRef = useRef<Session | null>(null);

  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  const signOut = useCallback((reason?: string) => {
    const wasBrowser = sessionRef.current !== null && sessionRef.current.token === null;
    clearStored();
    setSessionToken(null);
    sessionRef.current = null;
    setSession(null);
    setNotice(reason ?? null);
    if (wasBrowser) void api.logout().catch(() => undefined); // encerra a sessão no servidor
  }, []);

  const signInWith = useCallback((provider: string) => {
    window.location.assign(`/auth/login?provider=${encodeURIComponent(provider)}`);
  }, []);

  const signInWithToken = useCallback(async (token: string, remember: boolean) => {
    const me = await api.me(token); // lança ApiError se a chave não serve
    writeStored(token, remember);
    setSessionToken(token);
    setSession({ token, me });
    setNotice(null);
  }, []);

  // Qualquer 401 numa sessão ativa derruba a sessão (ver api/client.ts).
  useEffect(() => {
    onUnauthorized(() => {
      if (sessionRef.current) signOut("Sua sessão expirou. Entre de novo.");
    });
    return () => onUnauthorized(null);
  }, [signOut]);

  // Ao abrir: chave salva (se houver) ou cookie de sessão.
  useEffect(() => {
    const stored = readStored();
    const check = stored ? api.me(stored.token) : api.me();
    check
      .then((me) => {
        const token = stored?.token ?? null;
        setSessionToken(token);
        setSession({ token, me });
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) {
          // Sem cookie válido: a pessoa só precisa escolher como entrar. Chave salva inválida avisa.
          if (stored) {
            clearStored();
            setNotice("Sua API key salva não é mais válida. Entre de novo.");
          }
        } else {
          // Servidor fora do ar: mantém a chave salva para a próxima tentativa.
          setNotice(describeError(err));
        }
      })
      .finally(() => setBooting(false));
  }, []);

  const value = useMemo(
    () => ({ session, booting, notice, signInWith, signInWithToken, signOut }),
    [session, booting, notice, signInWith, signInWithToken, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth precisa estar dentro de AuthProvider");
  return ctx;
}
