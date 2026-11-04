import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { OrgSummary, Repo } from "../api/types";
import { useToast } from "../components/Toaster";

interface IndexValue {
  repos: Repo[] | null;
  orgs: OrgSummary[] | null;
  loading: boolean;
  reload: () => Promise<void>;
}

const IndexContext = createContext<IndexValue | null>(null);

/** Carrega repositórios e orgs uma vez por sessão. As páginas leem daqui. */
export function IndexProvider({ children }: { children: ReactNode }) {
  const [repos, setRepos] = useState<Repo[] | null>(null);
  const [orgs, setOrgs] = useState<OrgSummary[] | null>(null);
  const [loading, setLoading] = useState(false);
  const { push } = useToast();

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [nextRepos, nextOrgs] = await Promise.all([api.repos(), api.orgs()]);
      setRepos(nextRepos);
      setOrgs(nextOrgs);
    } catch (err) {
      push({ tone: "error", title: "Não consegui atualizar o índice", description: describeError(err) });
    } finally {
      setLoading(false);
    }
  }, [push]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const value = useMemo(() => ({ repos, orgs, loading, reload }), [repos, orgs, loading, reload]);
  return <IndexContext.Provider value={value}>{children}</IndexContext.Provider>;
}

export function useIndex(): IndexValue {
  const ctx = useContext(IndexContext);
  if (!ctx) throw new Error("useIndex precisa estar dentro de IndexProvider");
  return ctx;
}
