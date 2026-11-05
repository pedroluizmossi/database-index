import { useEffect, useState, type ReactNode } from "react";
import { BookOpen, Braces, Database, KeyRound, LogOut, Moon, Network, ScrollText, Search, SearchCode, Sun, type LucideIcon } from "lucide-react";
import { useAuth } from "../auth/AuthProvider";
import { canWrite, hasScope } from "../lib/capabilities";
import type { Session } from "../auth/AuthProvider";
import type { Me } from "../api/types";
import { applyTheme, readTheme, saveTheme, systemPrefersDark, watchSystemTheme, type Theme } from "../lib/theme";
import { cx } from "../lib/utils";

export type Tab = "search" | "repos" | "docs" | "apis" | "xray" | "tokens" | "audit";

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
    { id: "apis", label: "APIs", icon: Braces },
    { id: "xray", label: "Raio X", icon: Network },
  ];
  if (session && canWrite(session)) tabs.push({ id: "tokens", label: "Tokens", icon: KeyRound });
  if (session && hasScope(session.me, "admin")) tabs.push({ id: "audit", label: "Auditoria", icon: ScrollText });
  return tabs;
}

/** Botão de ícone da barra escura (o IconButton comum é feito para fundo claro). */
const barIconButton =
  "inline-flex size-8 shrink-0 items-center justify-center rounded-md text-ink-300 outline-none transition hover:bg-white/10 hover:text-white focus-visible:ring-2 focus-visible:ring-accent-400";

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
      <header className="sticky top-0 z-40 bg-ink-950 text-white shadow-[0_1px_0_0_rgb(255_255_255/0.06),0_4px_12px_-6px_rgb(0_0_0/0.35)]">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-4 sm:px-6 xl:gap-6">
          <Brand />
          <TabList tabs={tabs} tab={tab} onTab={onTab} className="hidden self-stretch lg:flex" />
          <div className="ml-auto flex shrink-0 items-center gap-1">
            <ThemeToggle />
            <UserMenu />
          </div>
        </div>
        {/* Telas estreitas: as abas descem para uma segunda linha rolável, sempre com rótulo. */}
        <div className="border-t border-white/[0.06] lg:hidden">
          <TabList
            tabs={tabs}
            tab={tab}
            onTab={onTab}
            className="mx-auto flex h-11 max-w-6xl overflow-x-auto px-1 [scrollbar-width:none] sm:px-3"
          />
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 pt-10 pb-24 sm:px-6">{children}</main>
    </div>
  );
}

function TabList({
  tabs,
  tab,
  onTab,
  className,
}: {
  tabs: TabDef[];
  tab: Tab;
  onTab: (tab: Tab) => void;
  className?: string;
}) {
  return (
    <nav aria-label="Principal" className={cx("items-stretch gap-0.5", className)}>
      {tabs.map(({ id, label, icon: Icon }) => {
        const active = id === tab;
        return (
          <button
            key={id}
            type="button"
            aria-current={active ? "page" : undefined}
            onClick={() => onTab(id)}
            className={cx(
              "group relative inline-flex shrink-0 items-center gap-2 px-3 text-sm lg:px-2.5 xl:px-3 font-medium whitespace-nowrap outline-none transition-colors",
              "focus-visible:after:absolute focus-visible:after:inset-x-1 focus-visible:after:inset-y-2 focus-visible:after:rounded-md focus-visible:after:ring-2 focus-visible:after:ring-accent-400",
              active ? "text-white" : "text-ink-300 hover:text-white",
            )}
          >
            <Icon
              className={cx("size-4 shrink-0 transition-colors", active ? "text-accent-300" : "text-ink-400 group-hover:text-ink-100")}
              aria-hidden="true"
            />
            {label}
            {/* Marca da aba ativa: encosta na borda de baixo da barra. */}
            <span
              aria-hidden="true"
              className={cx(
                "absolute inset-x-3 bottom-0 h-0.5 rounded-t-full transition-colors",
                active ? "bg-accent-400" : "bg-transparent group-hover:bg-white/15",
              )}
            />
          </button>
        );
      })}
    </nav>
  );
}

function Brand() {
  return (
    <div className="flex shrink-0 items-center gap-2.5">
      <div className="flex size-7 items-center justify-center rounded-md bg-accent-500 text-white">
        <SearchCode className="size-4" aria-hidden="true" />
      </div>
      <span className="font-semibold tracking-tight whitespace-nowrap">code-index</span>
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

  const label = dark ? "Usar tema claro" : "Usar tema escuro";
  return (
    <button type="button" aria-label={label} title={label} onClick={toggle} className={barIconButton}>
      {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </button>
  );
}

/** Nível de acesso mais alto, para mostrar ao lado do nome. */
function accessLabel(me: Me): string {
  if (me.scopes.includes("admin")) return "admin";
  if (me.scopes.includes("index:write")) return "indexação";
  return "leitura";
}

function UserMenu() {
  const { session, signOut } = useAuth();
  if (!session) return null;
  const { me } = session;
  const name = me.token_name ?? me.subject.split("@")[0];

  return (
    <div className="ml-2 flex items-center gap-2 border-l border-white/10 pl-3">
      <div className="hidden min-w-0 items-center gap-2 md:flex lg:hidden xl:flex" title={`${me.subject} · ${me.scopes.join(", ")}`}>
        <span className="max-w-40 truncate text-sm text-ink-100">{name}</span>
        <span className="rounded px-1.5 py-px text-[11px] font-medium text-ink-300 ring-1 ring-white/15 ring-inset">
          {accessLabel(me)}
        </span>
      </div>
      <button type="button" aria-label="Sair" title="Sair" onClick={() => signOut()} className={barIconButton}>
        <LogOut className="size-4" />
      </button>
    </div>
  );
}
