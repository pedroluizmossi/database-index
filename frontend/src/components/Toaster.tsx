import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { CircleAlert, CircleCheck, Info, X, type LucideIcon } from "lucide-react";
import { cx } from "../lib/utils";

type Tone = "success" | "error" | "info";

interface ToastItem {
  id: number;
  tone: Tone;
  title: string;
  description?: string;
}

interface ToastApi {
  push: (toast: { tone: Tone; title: string; description?: string }) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const TONE_STYLES: Record<Tone, string> = {
  success: "border-emerald-200 text-emerald-700 dark:border-emerald-900/60 dark:text-emerald-300",
  error: "border-rose-200 text-rose-700 dark:border-rose-900/60 dark:text-rose-300",
  info: "border-zinc-200 text-zinc-700 dark:border-zinc-700 dark:text-zinc-200",
};

const TONE_ICON: Record<Tone, LucideIcon> = {
  success: CircleCheck,
  error: CircleAlert,
  info: Info,
};

const TOAST_TTL_MS = 5000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((list) => list.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (toast: { tone: Tone; title: string; description?: string }) => {
      const id = nextId.current++;
      setToasts((list) => [...list.slice(-3), { ...toast, id }]);
      window.setTimeout(() => dismiss(id), TOAST_TTL_MS);
    },
    [dismiss],
  );

  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed inset-x-0 bottom-4 z-60 flex flex-col items-center gap-2 px-4 sm:inset-x-auto sm:right-4 sm:top-4 sm:bottom-auto sm:items-end"
      >
        {toasts.map((t) => (
          <ToastCard key={t.id} item={t} onClose={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastCard({ item, onClose }: { item: ToastItem; onClose: () => void }) {
  const Icon = TONE_ICON[item.tone];
  return (
    <div
      role={item.tone === "error" ? "alert" : "status"}
      className={cx(
        "pointer-events-auto flex w-full max-w-sm animate-pop items-start gap-3 rounded-xl border bg-white/95 p-3.5 shadow-xl shadow-zinc-900/10 backdrop-blur dark:bg-zinc-900/95",
        TONE_STYLES[item.tone],
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{item.title}</p>
        {item.description && (
          <p className="mt-0.5 wrap-break-word text-xs text-zinc-600 dark:text-zinc-400">{item.description}</p>
        )}
      </div>
      <button
        type="button"
        onClick={onClose}
        aria-label="Fechar aviso"
        className="-m-1 rounded-md p-1 text-zinc-400 transition hover:text-zinc-700 dark:hover:text-zinc-200"
      >
        <X className="size-4" />
      </button>
    </div>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast precisa estar dentro de ToastProvider");
  return ctx;
}
