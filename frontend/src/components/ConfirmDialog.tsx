import { useState, type ReactNode } from "react";
import { describeError } from "../api/client";
import { Alert, Button } from "./ui";
import { Dialog } from "./Dialog";

interface ConfirmDialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  /** Executa a ação. Se lançar erro, a mensagem aparece no diálogo e ele continua aberto. */
  onConfirm: () => Promise<void>;
}

export function ConfirmDialog({ open, onClose, title, children, confirmLabel, danger, onConfirm }: ConfirmDialogProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      onClose();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancelar
          </Button>
          <Button onClick={() => void run()} loading={busy} variant={danger ? "danger" : "primary"}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-sm text-stone-600 dark:text-stone-300">
        {children}
        {error && <Alert>{error}</Alert>}
      </div>
    </Dialog>
  );
}
