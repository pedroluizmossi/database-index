import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  BarChart3,
  BookOpen,
  Braces,
  Database,
  KeyRound,
  LogOut,
  Menu,
  Moon,
  Network,
  PanelLeftClose,
  PanelLeftOpen,
  ScrollText,
  Search,
  SearchCode,
  Settings,
  Sun,
  Users,
  X,
  type LucideIcon,
} from "lucide-react";
import { useAuth } from "../auth/AuthProvider";
import { canWrite, hasScope } from "../lib/capabilities";
import type { Session } from "../auth/AuthProvider";
import type { Me } from "../api/types";
import { applyTheme, readTheme, saveTheme, systemPrefersDark, watchSystemTheme, type Theme } from "../lib/theme";
import { usePrefs } from "../data/PrefsProvider";
import { cx } from "../lib/utils";

export type Tab = "search" | "repos" | "docs" | "apis" | "xray" | "tokens" | "usage" | "users" | "audit" | "settings";

type Group = "explore" | "account" | "admin";

interface TabDef {
  id: Tab;
  label: string;
  icon: LucideIcon;
  group: Group;
}

const GROUP_LABELS: Record<Group, string> = { explore: "Explorar", account: "Conta", admin: "Administração" };
const GROUP_ORDER: Group[] = ["explore", "account", "admin"];

/** Itens do menu visíveis para a sessão: tokens só com login no navegador; uso, usuários e auditoria só para admin. */
export function tabsFor(session: Session | null): TabDef[] {
  const tabs: TabDef[] = [
    { id: "search", label: "Busca", icon: Search, group: "explore" },
    { id: "repos", label: "Repositórios", icon: Database, group: "explore" },
    { id: "docs", label: "Documentação", icon: BookOpen, group: "explore" },
    { id: "apis", label: "APIs", icon: Braces, group: "explore" },
    { id: "xray", label: "Raio X", icon: Network, group: "explore" },
  ];
  if (session && canWrite(session)) tabs.push({ id: "tokens", label: "Tokens", icon: KeyRound, group: "account" });
  if (session && hasScope(session.me, "admin")) {
    tabs.push(
      { id: "usage", label: "Uso", icon: BarChart3, group: "admin" },
      { id: "users", label: "Usuários", icon: Users, group: "admin" },
      { id: "audit", label: "Auditoria", icon: ScrollText, group: "admin" },
    );
  }
  return tabs;
}

/** Botão de ícone do fundo escuro (o IconButton comum é feito para fundo claro). */
const barIconButton =
  "inline-flex size-8 shrink-0 items-center justify-center rounded-md text-ink-300 outline-none transition hover:bg-white/10 hover:text-white focus-visible:ring-2 focus-visible:ring-accent-400";

const COLLAPSED_KEY = "code-index.sidebar-collapsed";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

function saveCollapsed(value: boolean) {
  try {
    window.localStorage.setItem(COLLAPSED_KEY, value ? "1" : "0");
  } catch {
    // sem armazenamento (janela privada): vale só nesta visita
  }
}

/**
 * Casca da aplicação: menu lateral à esquerda (agrupado) e o conteúdo à direita.
 * Telas largas: menu fixo, que pode ser recolhido para só ícones. Telas estreitas: barra no topo com botão
 * que abre o menu numa gaveta. A largura do menu fica em `--sidebar-w` (usada por barras fixas das páginas).
 */
export function AppShell({
  tab,
  onTab,
  tabs,
  wide = false,
  children,
}: {
  tab: Tab;
  onTab: (tab: Tab) => void;
  tabs: TabDef[];
  /** Páginas com tabelas largas usam uma faixa de conteúdo mais larga. */
  wide?: boolean;
  children: ReactNode;
}) {
  const width = wide ? "max-w-7xl" : "max-w-6xl";
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    document.documentElement.style.setProperty("--sidebar-w", collapsed ? "4rem" : "15rem");
  }, [collapsed]);

  function toggleCollapsed() {
    setCollapsed((cur) => {
      saveCollapsed(!cur);
      return !cur;
    });
  }

  function go(next: Tab) {
    onTab(next);
    setDrawerOpen(false);
  }

  return (
    <div className="min-h-dvh">
      {/* Telas largas: menu fixo à esquerda */}
      <aside
        className={cx(
          "fixed inset-y-0 left-0 z-40 hidden flex-col bg-ink-950 text-white shadow-[1px_0_0_0_rgb(255_255_255/0.06)] transition-[width] duration-200 lg:flex",
          collapsed ? "w-16" : "w-60",
        )}
      >
        <SidebarContent
          tabs={tabs}
          tab={tab}
          onTab={go}
          collapsed={collapsed}
          headerAction={
            <button
              type="button"
              onClick={toggleCollapsed}
              aria-label={collapsed ? "Expandir menu" : "Recolher menu"}
              title={collapsed ? "Expandir menu" : "Recolher menu"}
              className={barIconButton}
            >
              {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
            </button>
          }
        />
      </aside>

      {/* Telas estreitas: barra no topo + gaveta */}
      <header className="sticky top-0 z-40 flex h-14 items-center gap-3 bg-ink-950 px-4 text-white shadow-[0_1px_0_0_rgb(255_255_255/0.06)] lg:hidden">
        <button
          type="button"
          onClick={() => setDrawerOpen(true)}
          aria-label="Abrir menu"
          aria-expanded={drawerOpen}
          className={barIconButton}
        >
          <Menu className="size-5" />
        </button>
        <Brand />
        <span className="ml-auto truncate text-sm text-ink-300">{tabs.find((t) => t.id === tab)?.label}</span>
      </header>
      <MobileDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)}>
        <SidebarContent
          tabs={tabs}
          tab={tab}
          onTab={go}
          collapsed={false}
          headerAction={
            <button type="button" onClick={() => setDrawerOpen(false)} aria-label="Fechar menu" className={barIconButton}>
              <X className="size-4" />
            </button>
          }
        />
      </MobileDrawer>

      <div className={cx("transition-[padding] duration-200", collapsed ? "lg:pl-16" : "lg:pl-60")}>
        <main className={cx("mx-auto px-4 pt-8 pb-24 sm:px-6 lg:pt-10", width)}>{children}</main>
      </div>
    </div>
  );
}

function MobileDrawer({ open, onClose, children }: { open: boolean; onClose: () => void; children: ReactNode }) {
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    panel.current?.querySelector<HTMLElement>("[aria-current=page], button")?.focus();
    const { overflow } = document.body.style;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="Menu">
      <div className="absolute inset-0 bg-ink-950/60 animate-fade-in" onClick={onClose} aria-hidden="true" />
      <div ref={panel} className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col bg-ink-950 text-white shadow-xl animate-slide-in-left">
        {children}
      </div>
    </div>
  );
}

function SidebarContent({
  tabs,
  tab,
  onTab,
  collapsed,
  headerAction,
}: {
  tabs: TabDef[];
  tab: Tab;
  onTab: (tab: Tab) => void;
  collapsed: boolean;
  headerAction: ReactNode;
}) {
  return (
    <>
      <div className={cx("flex h-14 shrink-0 items-center gap-2 border-b border-white/[0.06]", collapsed ? "justify-center px-2" : "px-4")}>
        {!collapsed && <Brand />}
        <div className={cx(!collapsed && "ml-auto")}>{headerAction}</div>
      </div>
      <nav aria-label="Principal" className="flex-1 overflow-y-auto px-2 py-4 [scrollbar-width:thin]">
        {GROUP_ORDER.map((group) => {
          const items = tabs.filter((t) => t.group === group);
          if (items.length === 0) return null;
          return (
            <div key={group} className="mb-5 last:mb-0">
              {collapsed ? (
                <div className="mx-3 mb-2 h-px bg-white/[0.08] first:hidden" aria-hidden="true" />
              ) : (
                <div className="mb-1.5 px-3 text-[11px] font-semibold tracking-wider text-ink-400 uppercase">
                  {GROUP_LABELS[group]}
                </div>
              )}
              <ul className="space-y-0.5">
                {items.map((item) => (
                  <li key={item.id}>
                    <NavItem item={item} active={item.id === tab} collapsed={collapsed} onClick={() => onTab(item.id)} />
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </nav>
      <SidebarFooter collapsed={collapsed} settingsOpen={tab === "settings"} onSettings={() => onTab("settings")} />
    </>
  );
}

function NavItem({
  item,
  active,
  collapsed,
  onClick,
}: {
  item: TabDef;
  active: boolean;
  collapsed: boolean;
  onClick: () => void;
}) {
  const Icon = item.icon;
  return (
    <button
      type="button"
      aria-current={active ? "page" : undefined}
      aria-label={collapsed ? item.label : undefined}
      title={collapsed ? item.label : undefined}
      onClick={onClick}
      className={cx(
        "group relative flex w-full items-center gap-3 rounded-md py-2 text-sm font-medium whitespace-nowrap outline-none transition-colors",
        "focus-visible:ring-2 focus-visible:ring-accent-400",
        collapsed ? "justify-center px-2" : "px-3",
        active ? "bg-white/[0.08] text-white" : "text-ink-300 hover:bg-white/[0.05] hover:text-white",
      )}
    >
      {/* Marca do item ativo, encostada na borda esquerda */}
      <span
        aria-hidden="true"
        className={cx(
          "absolute top-1.5 bottom-1.5 left-0 w-0.5 rounded-r-full transition-colors",
          active ? "bg-accent-400" : "bg-transparent",
        )}
      />
      <Icon
        className={cx("size-4 shrink-0 transition-colors", active ? "text-accent-300" : "text-ink-400 group-hover:text-ink-100")}
        aria-hidden="true"
      />
      {!collapsed && <span className="truncate">{item.label}</span>}
    </button>
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
  const { session } = useAuth();
  const { info, update } = usePrefs();
  const [theme, setTheme] = useState<Theme>(() => readTheme());
  // A tela de configurações também muda o tema: acompanha o valor salvo.
  const saved = info?.settings["ui.theme"];
  useEffect(() => setTheme(readTheme()), [saved]);
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
    // Com login no navegador, a escolha fica na conta (vale em outros navegadores); senão, só neste.
    if (canWrite(session)) void update({ "ui.theme": next }).catch(() => undefined);
  }

  const label = dark ? "Usar tema claro" : "Usar tema escuro";
  return (
    <button type="button" aria-label={label} title={label} onClick={toggle} className={barIconButton}>
      {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </button>
  );
}

const PROVIDER_LABELS: Record<string, string> = { google: "Google", microsoft: "Microsoft" };

/** Como esta sessão entrou: provedor do login (Google, Microsoft), token de agent colado ou cliente OAuth. */
function sessionLabel(me: Me): string {
  if (me.kind === "session") return me.provider ? (PROVIDER_LABELS[me.provider] ?? me.provider) : "navegador";
  if (me.kind === "oauth") return "OAuth (MCP)";
  return me.token_name ? `token "${me.token_name}"` : "token de agent";
}

/** Nível de acesso mais alto, para mostrar ao lado do nome. */
function accessLabel(me: Me): string {
  if (me.scopes.includes("admin")) return "admin";
  if (me.scopes.includes("index:write")) return "indexação";
  return "leitura";
}

function SidebarFooter({
  collapsed,
  settingsOpen,
  onSettings,
}: {
  collapsed: boolean;
  settingsOpen: boolean;
  onSettings: () => void;
}) {
  const { session, signOut } = useAuth();
  if (!session) return null;
  const { me } = session;
  const kind = sessionLabel(me);
  const details = `${me.subject} · ${kind} · ${accessLabel(me)}`;

  return (
    <div className={cx("shrink-0 border-t border-white/[0.06] p-2", collapsed ? "space-y-1" : "")}>
      {collapsed ? (
        <div
          className="mx-auto mb-1 flex size-7 items-center justify-center rounded-full bg-white/10 text-xs font-semibold uppercase"
          title={details}
          aria-label={details}
        >
          {me.subject.slice(0, 1)}
        </div>
      ) : (
        <div className="min-w-0 px-2 pt-1 pb-2" title={`${details} · ${me.scopes.join(", ")}`}>
          <div className="truncate text-xs text-ink-100">{me.subject}</div>
          <div className="truncate text-[11px] text-ink-400">
            {kind} · {accessLabel(me)}
          </div>
        </div>
      )}
      <div className={cx("flex items-center gap-1", collapsed ? "flex-col" : "px-1")}>
        <ThemeToggle />
        {canWrite(session) && (
          <button
            type="button"
            aria-label="Configurações"
            title="Configurações"
            aria-current={settingsOpen ? "page" : undefined}
            onClick={onSettings}
            className={cx(barIconButton, settingsOpen && "bg-white/10 text-white")}
          >
            <Settings className="size-4" />
          </button>
        )}
        <button
          type="button"
          aria-label="Sair"
          title="Sair"
          onClick={() => signOut()}
          className={cx(barIconButton, !collapsed && "ml-auto")}
        >
          <LogOut className="size-4" />
        </button>
      </div>
    </div>
  );
}
