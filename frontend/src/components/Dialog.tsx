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
  size?: "md" | "lg" | "xl";
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
        "m-auto w-[calc(100%-2rem)] rounded-xl border border-zinc-200 bg-white p-0 text-zinc-900 shadow-[0_24px_48px_-12px_rgb(0_0_0/0.25)] backdrop:bg-ink-950/50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100 dark:backdrop:bg-black/60",
        size === "md" ? "max-w-md" : size === "lg" ? "max-w-2xl" : "max-w-5xl",
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
