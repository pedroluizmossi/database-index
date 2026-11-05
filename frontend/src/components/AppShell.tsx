import { useEffect, useState, type ReactNode } from "react";
import { BookOpen, Database, KeyRound, LogOut, Moon, ScrollText, Search, SearchCode, Sun, type LucideIcon } from "lucide-react";
import { useAuth } from "../auth/AuthProvider";
import { canWrite, hasScope } from "../lib/capabilities";
import type { Session } from "../auth/AuthProvider";
import { applyTheme, readTheme, saveTheme, systemPrefersDark, watchSystemTheme, type Theme } from "../lib/theme";
import { cx } from "../lib/utils";
import { Backdrop, Badge, IconButton } from "./ui";

export type Tab = "search" | "repos" | "docs" | "tokens" | "audit";

interface TabDef {
  id: Tab;
  label: string;
  icon: LucideIcon;
}

/** Abas visíveis para a sessão: tokens só com login Google; auditoria só para admin. */
export function tabsFor(session: Session | null): TabDef[] {
  const tabs: TabDef[] = [
    { id: "search", label: "Busca", icon: Search },
    { id: "repos", label: "Repositórios", icon: Database },
    { id: "docs", label: "Documentação", icon: BookOpen },
  ];
  if (session && canWrite(session)) tabs.push({ id: "tokens", label: "Tokens", icon: KeyRound });
  if (session && hasScope(session.me, "admin")) tabs.push({ id: "audit", label: "Auditoria", icon: ScrollText });
  return tabs;
}

export function AppShell({
  tab,
  onTab,
  tabs,
  children,
}: {
  tab: Tab;
  onTab: (tab: Tab) => void;
  tabs: TabDef[];
  children: ReactNode;
}) {
  return (
    <div className="min-h-dvh">
      <Backdrop />
      <header className="sticky top-0 z-40 border-b border-zinc-200/70 bg-white/70 backdrop-blur-xl dark:border-zinc-800/70 dark:bg-zinc-950/70">
        <div className="mx-auto flex h-16 max-w-6xl items-center gap-4 px-4 sm:px-6">
          <Brand />
          <nav aria-label="Principal" className="flex items-center gap-1 rounded-xl bg-zinc-900/5 p-1 dark:bg-white/5">
            {tabs.map(({ id, label, icon: Icon }) => {
              const active = id === tab;
              return (
                <button
                  key={id}
                  type="button"
                  aria-label={label}
                  aria-current={active ? "page" : undefined}
                  onClick={() => onTab(id)}
                  className={cx(
                    "inline-flex h-8 items-center gap-2 rounded-lg px-3 text-sm font-medium transition",
                    active
                      ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-white"
                      : "text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200",
                  )}
                >
                  <Icon className="size-4" aria-hidden="true" />
                  <span className="hidden lg:inline">{label}</span>
                </button>
              );
            })}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <ThemeToggle />
            <UserMenu />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 pt-10 pb-24 sm:px-6">{children}</main>
    </div>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <div className="flex size-8 items-center justify-center rounded-lg bg-linear-to-br from-indigo-500 to-violet-600 text-white shadow-md shadow-indigo-500/30">
        <SearchCode className="size-4" aria-hidden="true" />
      </div>
      <span className="hidden font-semibold whitespace-nowrap tracking-tight lg:inline">code-index</span>
    </div>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => readTheme());
  const [systemDark, setSystemDark] = useState(() => systemPrefersDark());

  useEffect(
    () =>
      watchSystemTheme(() => {
        setSystemDark(systemPrefersDark());
        applyTheme(readTheme());
      }),
    [],
  );

  const dark = theme === "dark" || (theme === "system" && systemDark);

  function toggle() {
    const next: Theme = dark ? "light" : "dark";
    saveTheme(next);
    setTheme(next);
  }

  return (
    <IconButton label={dark ? "Usar tema claro" : "Usar tema escuro"} onClick={toggle}>
      {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </IconButton>
  );
}

function UserMenu() {
  const { session, signOut } = useAuth();
  if (!session) return null;
  const { me } = session;
  const name = me.token_name ?? me.subject;

  return (
    <div className="flex items-center gap-3 border-l border-zinc-200 pl-3 dark:border-zinc-800">
      <div className="hidden items-center gap-1.5 xl:flex">
        {me.scopes.map((scope) => (
          <Badge key={scope} tone="accent">
            {scope}
          </Badge>
        ))}
      </div>
      <div className="hidden text-right lg:block">
        <p className="text-sm leading-tight font-medium">{name}</p>
        <p className="text-xs leading-tight text-zinc-500 dark:text-zinc-400">{me.subject}</p>
      </div>
      <span
        aria-hidden="true"
        className="flex size-8 items-center justify-center rounded-full bg-linear-to-br from-zinc-700 to-zinc-900 text-xs font-semibold text-white uppercase dark:from-zinc-200 dark:to-zinc-400 dark:text-zinc-900"
      >
        {me.subject.charAt(0)}
      </span>
      <IconButton label="Sair" onClick={() => signOut()}>
        <LogOut className="size-4" />
      </IconButton>
    </div>
  );
}
