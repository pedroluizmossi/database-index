import { createContext, useContext } from "react";
import type { Tab } from "../components/AppShell";

/**
 * Navegação entre abas a partir de dentro das páginas (ex.: Indexação abre Repositórios já filtrado; um sync
 * disparado em Repositórios aponta para Indexação). Quem fornece é o `Workspace` (App.tsx).
 */
export interface Navigation {
  /** Aba atual. */
  current: Tab;
  /** A sessão vê esta aba? (mesma regra do menu). */
  can: (tab: Tab) => boolean;
  /** Abre a aba. `hash` (ex.: `#repos?status=error`) leva filtros que a página lê ao montar. */
  open: (tab: Tab, hash?: string) => void;
}

export const NavigationContext = createContext<Navigation | null>(null);

/** Null fora do Workspace (ex.: testes isolados): quem usa esconde o atalho. */
export function useNavigation(): Navigation | null {
  return useContext(NavigationContext);
}
