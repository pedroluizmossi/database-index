import { useState } from "react";
import { useAuth } from "./auth/AuthProvider";
import { AppShell, tabsFor, type Tab } from "./components/AppShell";
import { Spinner } from "./components/ui";
import { IndexProvider } from "./data/IndexProvider";
import { LoginPage } from "./pages/LoginPage";
import { AuditPage } from "./pages/AuditPage";
import { DocsPage } from "./pages/DocsPage";
import { ReposPage } from "./pages/ReposPage";
import { SearchPage } from "./pages/SearchPage";
import { TokensPage } from "./pages/TokensPage";

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

const TAB_IDS: Tab[] = ["search", "repos", "docs", "tokens", "audit"];

function Workspace() {
  const { session } = useAuth();
  const tabs = tabsFor(session);
  const fromHash = window.location.hash.slice(1) as Tab;
  const [tab, setTab] = useState<Tab>(() => (TAB_IDS.includes(fromHash) ? fromHash : "search"));
  // Uma aba só aparece se a sessão tem permissão; senão volta para a busca.
  const current = tabs.some((t) => t.id === tab) ? tab : "search";

  function changeTab(next: Tab) {
    setTab(next);
    window.history.replaceState(null, "", `#${next}`);
  }

  const page = {
    search: <SearchPage />,
    repos: <ReposPage />,
    docs: <DocsPage />,
    tokens: <TokensPage />,
    audit: <AuditPage />,
  }[current];

  return (
    <AppShell tab={current} onTab={changeTab} tabs={tabs}>
      {page}
    </AppShell>
  );
}
