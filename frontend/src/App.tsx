import { useState } from "react";
import type { XrayTarget } from "./lib/xray";
import { useAuth } from "./auth/AuthProvider";
import { AppShell, tabsFor, type Tab } from "./components/AppShell";
import { Spinner } from "./components/ui";
import { IndexProvider } from "./data/IndexProvider";
import { PrefsProvider } from "./data/PrefsProvider";
import { canWrite } from "./lib/capabilities";
import { SettingsPage } from "./pages/SettingsPage";
import { LoginPage } from "./pages/LoginPage";
import { AuditPage } from "./pages/AuditPage";
import { DocsPage } from "./pages/DocsPage";
import { ApisPage } from "./pages/ApisPage";
import { ReposPage } from "./pages/ReposPage";
import { SearchPage } from "./pages/SearchPage";
import { TokensPage } from "./pages/TokensPage";
import { UsersPage } from "./pages/UsersPage";
import { UsagePage } from "./pages/UsagePage";
import { XrayPage } from "./pages/XrayPage";

export default function App() {
  const { session, booting } = useAuth();

  if (booting) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <Spinner className="size-6" />
      </div>
    );
  }
  if (!session) return <LoginPage />;

  return (
    <PrefsProvider>
      <IndexProvider>
        <Workspace />
      </IndexProvider>
    </PrefsProvider>
  );
}

const TAB_IDS: Tab[] = ["search", "repos", "docs", "apis", "xray", "tokens", "usage", "users", "audit", "settings"];

function Workspace() {
  const { session } = useAuth();
  const tabs = tabsFor(session);
  // "#repos?status=pending" abre a aba repos: o que vem depois de "?" são filtros da própria aba.
  const fromHash = window.location.hash.slice(1).split("?")[0] as Tab;
  const [tab, setTab] = useState<Tab>(() => (TAB_IDS.includes(fromHash) ? fromHash : "search"));
  const [xrayTarget, setXrayTarget] = useState<XrayTarget | null>(null);
  // "#usage?email=fulano@x.com" abre o painel de uso filtrado por um usuário.
  const [usageEmail, setUsageEmail] = useState(() =>
    fromHash === "usage" ? (new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("email") ?? "") : "",
  );
  // Uma aba só aparece se a sessão tem permissão; senão volta para a busca.
  // As configurações não são uma aba da barra: abrem pelo ícone ao lado do nome, só com login no navegador.
  const allowed = tab === "settings" ? canWrite(session) : tabs.some((t) => t.id === tab);
  const current = allowed ? tab : "search";

  function changeTab(next: Tab) {
    if (next === "usage") return filterUsage(usageEmail);
    setTab(next);
    window.history.replaceState(null, "", `#${next}`);
  }

  function filterUsage(email: string) {
    setUsageEmail(email);
    setTab("usage");
    window.history.replaceState(null, "", email ? `#usage?email=${encodeURIComponent(email)}` : "#usage");
  }

  function openXray(target: XrayTarget) {
    setXrayTarget(target);
    changeTab("xray");
  }

  const page = {
    search: <SearchPage />,
    repos: <ReposPage onXray={openXray} />,
    docs: <DocsPage onXray={openXray} />,
    apis: <ApisPage onXray={openXray} />,
    xray: <XrayPage initial={xrayTarget} />,
    tokens: <TokensPage />,
    usage: <UsagePage email={usageEmail} onEmail={filterUsage} />,
    users: <UsersPage onOpenUsage={filterUsage} />,
    audit: <AuditPage />,
    settings: <SettingsPage />,
  }[current];

  return (
    <AppShell tab={current} onTab={changeTab} tabs={tabs} wide={current === "repos" || current === "usage" || current === "users"}>
      {/* A key remonta a página a cada troca de aba: ela entra com `animate-page-in` (só a nova sobe; nada de
          retrato da página antiga, que distorcia telas longas). */}
      <div key={current} className="animate-page-in">
        {page}
      </div>
    </AppShell>
  );
}
