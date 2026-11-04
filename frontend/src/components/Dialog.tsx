import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import { IconButton } from "./ui";
import { cx } from "../lib/utils";

interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: "md" | "lg";
}

/** Modal com o <dialog> nativo: foco preso, Esc fecha e o fundo fica bloqueado. */
export function Dialog({ open, onClose, title, description, children, footer, size = "md" }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      className={cx(
        "m-auto w-[calc(100%-2rem)] rounded-2xl border border-zinc-200 bg-white p-0 text-zinc-900 shadow-2xl shadow-zinc-900/20 backdrop:bg-zinc-950/40 backdrop:backdrop-blur-sm dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100",
        size === "md" ? "max-w-md" : "max-w-2xl",
      )}
    >
      {open && (
        <div className="flex max-h-[85dvh] flex-col">
          <header className="flex items-start gap-3 border-b border-zinc-200 px-5 py-4 dark:border-zinc-800">
            <div className="min-w-0 flex-1">
              <h2 className="text-base font-semibold">{title}</h2>
              {description && <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">{description}</p>}
            </div>
            <IconButton label="Fechar" onClick={() => ref.current?.close()}>
              <X className="size-4" />
            </IconButton>
          </header>
          <div className="overflow-y-auto px-5 py-4">{children}</div>
          {footer && (
            <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-zinc-200 px-5 py-3 dark:border-zinc-800">
              {footer}
            </footer>
          )}
        </div>
      )}
    </dialog>
  );
}
