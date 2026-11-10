import { useEffect, useState } from "react";
import { BookOpen } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { RepoDocLink } from "../api/types";
import { Dialog } from "./Dialog";
import { OriginBadges } from "./docs";
import { Alert, Button, EmptyState, Skeleton } from "./ui";

/** Documentação ligada a um repo, com a origem de cada vínculo. Só leitura: a gestão fica na aba Documentação. */
export function RepoDocsDialog({ repo, open, onClose }: { repo: string; open: boolean; onClose: () => void }) {
  const [items, setItems] = useState<RepoDocLink[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let active = true;
    setItems(null);
    setError(null);
    api
      .repoDocs(repo)
      .then((data) => active && setItems(data))
      .catch((err) => active && setError(describeError(err)));
    return () => {
      active = false;
    };
  }, [open, repo]);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Documentação ligada"
      description={repo}
      footer={
        <Button variant="ghost" onClick={onClose}>
          Fechar
        </Button>
      }
    >
      {error && <Alert>{error}</Alert>}
      {!items && !error && <Skeleton className="h-24 w-full" />}
      {items && items.length === 0 && (
        <EmptyState
          icon={BookOpen}
          title="Nenhuma página ligada"
          text="Os vínculos aparecem quando a documentação e este repo estão indexados, ou quando alguém grava um vínculo na aba Documentação."
        />
      )}
      {items && items.length > 0 && (
        <ul className="max-h-[50dvh] divide-y divide-stone-100 overflow-y-auto dark:divide-stone-800">
          {items.map((d) => (
            <li key={`${d.doc_repo}|${d.path}`} className="space-y-1 py-2.5">
              {d.url ? (
                <a
                  href={d.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm font-medium text-accent-600 hover:underline dark:text-accent-400"
                >
                  {d.title ?? d.path}
                </a>
              ) : (
                <p className="text-sm font-medium">{d.title ?? d.path}</p>
              )}
              <p className="truncate font-mono text-xs text-stone-500 dark:text-stone-400">{d.path}</p>
              <div className="flex flex-wrap gap-1.5">
                <OriginBadges origins={d.origins} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}
