import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api/endpoints";
import type { PrefValue, UserSettings } from "../api/types";
import { Spinner } from "../components/ui";
import { readTheme, saveTheme, type Theme } from "../lib/theme";

interface PrefsValue {
  /** null se o servidor não respondeu: as telas seguem com os padrões locais. */
  info: UserSettings | null;
  /** Grava no servidor (`null` volta ao padrão) e aplica o resultado. Erros sobem para quem chamou. */
  update: (changes: Record<string, PrefValue | null>) => Promise<void>;
}

const PrefsContext = createContext<PrefsValue | null>(null);

/** O tema escolhido no servidor acompanha a pessoa entre navegadores; sem escolha, vale o deste navegador. */
function syncTheme(info: UserSettings): void {
  if (!info.changed.includes("ui.theme")) return;
  const theme = info.settings["ui.theme"] as Theme;
  if (theme !== readTheme()) saveTheme(theme);
}

/** Carrega as configurações da pessoa uma vez por sessão, antes das páginas (elas leem os valores iniciais daqui). */
export function PrefsProvider({ children }: { children: ReactNode }) {
  const [info, setInfo] = useState<UserSettings | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .mySettings()
      .then((next) => {
        if (!alive) return;
        syncTheme(next);
        setInfo(next);
      })
      .catch(() => undefined) // sem as configs, a interface funciona com os padrões
      .finally(() => {
        if (alive) setReady(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  const update = useCallback(async (changes: Record<string, PrefValue | null>) => {
    const next = await api.updateMySettings(changes);
    if ("ui.theme" in changes) saveTheme(next.settings["ui.theme"] as Theme);
    setInfo(next);
  }, []);

  const value = useMemo(() => ({ info, update }), [info, update]);

  if (!ready) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <Spinner className="size-6" />
      </div>
    );
  }
  return <PrefsContext.Provider value={value}>{children}</PrefsContext.Provider>;
}

export function usePrefs(): PrefsValue {
  const value = useContext(PrefsContext);
  if (!value) throw new Error("usePrefs fora do PrefsProvider");
  return value;
}

/** Valor de uma config, ou `fallback` se o servidor não a conhece. */
export function usePref<T extends PrefValue>(key: string, fallback: T): T {
  const { info } = usePrefs();
  const value = info?.settings[key];
  return typeof value === typeof fallback ? (value as T) : fallback;
}
