import { useEffect, useMemo, useState } from "react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { DocPageRow } from "../api/types";
import { useIndex } from "../data/IndexProvider";
import { Dialog } from "./Dialog";
import { useToast } from "./Toaster";
import { Alert, Button, Field, Select, inputClass } from "./ui";

/** Grava um vínculo manual entre uma página e um repo de código. */
export function AddDocLinkDialog({
  page,
  onClose,
  onDone,
}: {
  page: DocPageRow | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const { repos } = useIndex();
  const { push } = useToast();
  const [repo, setRepo] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const targets = useMemo(
    () =>
      (repos ?? [])
        .filter((r) => r.kind !== "docs" && r.status !== "ignored")
        .sort((a, b) => a.name.localeCompare(b.name)),
    [repos],
  );

  useEffect(() => {
    if (page) {
      setRepo("");
      setReason("");
      setError(null);
    }
  }, [page]);

  async function submit() {
    if (!page || !repo) return;
    setBusy(true);
    setError(null);
    try {
      await api.addDocLink({ doc_repo: page.doc_repo, doc_path: page.doc_path, repo, reason: reason.trim() });
      push({ tone: "success", title: "Vínculo gravado", description: `${page.title ?? page.page} ↔ ${repo}` });
      onDone();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={page !== null}
      onClose={onClose}
      title="Vincular página a um repo"
      description={page ? page.title ?? page.page : undefined}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancelar
          </Button>
          <Button onClick={() => void submit()} loading={busy} disabled={!repo}>
            Gravar vínculo
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Repositório">
          <Select value={repo} onChange={setRepo}>
            <option value="">Escolha um repo</option>
            {targets.map((r) => (
              <option key={r.name} value={r.name}>
                {r.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Motivo (por que esta página fala deste repo)">
          <textarea
            className={`${inputClass} h-24 py-2`}
            value={reason}
            maxLength={500}
            onChange={(e) => setReason(e.target.value)}
          />
        </Field>
        {error && <Alert>{error}</Alert>}
      </div>
    </Dialog>
  );
}
