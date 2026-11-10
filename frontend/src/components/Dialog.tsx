import { useEffect, useId, useRef, type ReactNode } from "react";
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

/**
 * Foco inicial: o campo marcado com `data-autofocus`; senão o primeiro campo do corpo; senão o próprio dialog.
 * (Sem isso o showModal focaria o botão Fechar, que é o primeiro focável do cabeçalho.)
 */
function focusInitial(dialog: HTMLDialogElement, body: HTMLElement | null) {
  const target =
    body?.querySelector<HTMLElement>("[data-autofocus]") ??
    body?.querySelector<HTMLElement>("input:not([type=hidden]):not(:disabled), select:not(:disabled), textarea:not(:disabled)") ??
    dialog;
  target.focus();
}

/** Modal com o <dialog> nativo: foco preso, Esc fecha e o fundo fica bloqueado. Nome e descrição vêm do título. */
export function Dialog({ open, onClose, title, description, children, footer, size = "md" }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      focusInitial(dialog, bodyRef.current);
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      tabIndex={-1}
      aria-labelledby={titleId}
      aria-describedby={description ? descriptionId : undefined}
      className={cx(
        "m-auto w-[calc(100%-2rem)] animate-pop rounded-xl border border-stone-200 bg-sheet p-0 text-stone-900 outline-none shadow-[0_24px_48px_-12px_rgb(0_0_0/0.25)] backdrop:animate-fade-in backdrop:bg-ink-950/40 backdrop:backdrop-blur-[3px] dark:border-stone-800 dark:bg-stone-900 dark:text-stone-100 dark:backdrop:bg-black/55",
        size === "md" ? "max-w-md" : size === "lg" ? "max-w-2xl" : "max-w-5xl",
      )}
    >
      {open && (
        <div className="flex max-h-[85dvh] flex-col">
          <header className="flex items-start gap-3 border-b border-stone-200 px-5 py-4 dark:border-stone-800">
            <div className="min-w-0 flex-1">
              <h2 id={titleId} className="text-base font-semibold">
                {title}
              </h2>
              {description && (
                <p id={descriptionId} className="mt-1 text-sm text-stone-500 dark:text-stone-400">
                  {description}
                </p>
              )}
            </div>
            <IconButton label="Fechar" onClick={() => ref.current?.close()}>
              <X className="size-4" />
            </IconButton>
          </header>
          <div ref={bodyRef} className="overflow-y-auto px-5 py-4">
            {children}
          </div>
          {footer && (
            <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-stone-200 px-5 py-3 dark:border-stone-800">
              {footer}
            </footer>
          )}
        </div>
      )}
    </dialog>
  );
}
