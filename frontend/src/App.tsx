import { useState } from "react";
import type { XrayTarget } from "./lib/xray";
import { useAuth } from "./auth/AuthProvider";
import { AppShell, tabsFor, type Tab } from "./components/AppShell";
import { Spinner } from "./components/ui";
import { IndexProvider } from "./data/IndexProvider";
import { LoginPage } from "./pages/LoginPage";
import { AuditPage } from "./pages/AuditPage";
import { DocsPage } from "./pages/DocsPage";
import { ApisPage } from "./pages/ApisPage";
import { ReposPage } from "./pages/ReposPage";
import { SearchPage } from "./pages/SearchPage";
import { TokensPage } from "./pages/TokensPage";
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
    <IndexProvider>
      <Workspace />
    </IndexProvider>
  );
}

const TAB_IDS: Tab[] = ["search", "repos", "docs", "apis", "xray", "tokens", "audit"];

function Workspace() {
  const { session } = useAuth();
  const tabs = tabsFor(session);
  // "#repos?status=pending" abre a aba repos: o que vem depois de "?" são filtros da própria aba.
  const fromHash = window.location.hash.slice(1).split("?")[0] as Tab;
  const [tab, setTab] = useState<Tab>(() => (TAB_IDS.includes(fromHash) ? fromHash : "search"));
  const [xrayTarget, setXrayTarget] = useState<XrayTarget | null>(null);
  // Uma aba só aparece se a sessão tem permissão; senão volta para a busca.
  const current = tabs.some((t) => t.id === tab) ? tab : "search";

  function changeTab(next: Tab) {
    setTab(next);
    window.history.replaceState(null, "", `#${next}`);
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
    audit: <AuditPage />,
  }[current];

  return (
    <AppShell tab={current} onTab={changeTab} tabs={tabs} wide={current === "repos"}>
      {page}
    </AppShell>
  );
}
