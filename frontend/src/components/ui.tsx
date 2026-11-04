import type { ButtonHTMLAttributes, CSSProperties, ReactNode } from "react";
import { ChevronDown, CircleAlert, LoaderCircle, type LucideIcon } from "lucide-react";
import { cx } from "../lib/utils";

/* Peças visuais compartilhadas. Cores em pares claro/escuro (prefixo `dark:`). */

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "sm" | "md";
  loading?: boolean;
}

const BUTTON_VARIANT: Record<ButtonVariant, string> = {
  primary:
    "bg-linear-to-r from-indigo-600 to-violet-600 text-white shadow-md shadow-indigo-600/25 hover:brightness-110",
  secondary:
    "border border-zinc-200 bg-white text-zinc-800 shadow-sm hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100 dark:hover:bg-zinc-700",
  danger: "bg-rose-600 text-white shadow-md shadow-rose-600/25 hover:bg-rose-500",
  ghost: "text-zinc-600 hover:bg-zinc-900/5 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-white/10 dark:hover:text-white",
};

const BUTTON_SIZE = {
  sm: "h-8 gap-1.5 px-3 text-xs",
  md: "h-10 gap-2 px-4 text-sm",
} as const;

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  type = "button",
  disabled,
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      disabled={disabled || loading}
      className={cx(
        "inline-flex select-none items-center justify-center rounded-xl font-medium outline-none transition duration-150 focus-visible:ring-4 focus-visible:ring-indigo-500/30 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50",
        BUTTON_VARIANT[variant],
        BUTTON_SIZE[size],
        className,
      )}
    >
      {loading && <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />}
      {children}
    </button>
  );
}

export function IconButton({
  label,
  onClick,
  children,
  className,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
  className?: string;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className={cx(
        "inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-zinc-500 transition hover:bg-zinc-900/5 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-white/10 dark:hover:text-white",
        className,
      )}
    >
      {children}
    </button>
  );
}

export type BadgeTone = "indexed" | "pending" | "error" | "ignored" | "warning" | "neutral" | "accent";

const BADGE_TONE: Record<BadgeTone, string> = {
  indexed: "bg-emerald-500/10 text-emerald-700 ring-emerald-600/20 dark:text-emerald-400",
  pending: "bg-sky-500/10 text-sky-700 ring-sky-600/20 dark:text-sky-300",
  error: "bg-rose-500/10 text-rose-700 ring-rose-600/20 dark:text-rose-300",
  ignored: "bg-zinc-500/10 text-zinc-600 ring-zinc-500/20 dark:text-zinc-400",
  warning: "bg-amber-500/10 text-amber-700 ring-amber-600/20 dark:text-amber-300",
  neutral: "bg-zinc-500/10 text-zinc-700 ring-zinc-500/20 dark:text-zinc-300",
  accent: "bg-indigo-500/10 text-indigo-700 ring-indigo-600/20 dark:text-indigo-300",
};

const DOT_TONE: Partial<Record<BadgeTone, string>> = {
  indexed: "bg-emerald-500",
  pending: "bg-sky-500",
  error: "bg-rose-500",
  ignored: "bg-zinc-400",
};

export function Badge({
  tone = "neutral",
  dot = false,
  className,
  children,
}: {
  tone?: BadgeTone;
  dot?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const dotClass = DOT_TONE[tone];
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
        BADGE_TONE[tone],
        className,
      )}
    >
      {dot && dotClass && <span className={cx("size-1.5 rounded-full", dotClass)} aria-hidden="true" />}
      {children}
    </span>
  );
}

export function Kbd({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <kbd
      aria-hidden="true"
      className={cx(
        "inline-flex h-6 min-w-6 items-center justify-center rounded-md border border-zinc-200 bg-zinc-50 px-1.5 font-mono text-[11px] text-zinc-500 shadow-sm dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-400",
        className,
      )}
    >
      {children}
    </kbd>
  );
}

export function Panel({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div
      className={cx(
        "rounded-2xl border border-zinc-200/80 bg-white/80 shadow-sm shadow-zinc-900/3 backdrop-blur-sm dark:border-zinc-800 dark:bg-zinc-900/70",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex max-w-full flex-wrap gap-1 rounded-xl bg-zinc-900/5 p-1 dark:bg-white/5"
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(option.value)}
            className={cx(
              "h-8 rounded-lg px-3 text-xs font-medium whitespace-nowrap transition",
              active
                ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-white"
                : "text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export function Switch({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={cx(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition duration-200",
        checked ? "bg-indigo-600" : "bg-zinc-300 dark:bg-zinc-700",
      )}
    >
      <span
        className={cx(
          "inline-block size-4 rounded-full bg-white shadow transition duration-200",
          checked ? "translate-x-[18px]" : "translate-x-0.5",
        )}
      />
    </button>
  );
}

/** Classes dos campos de texto e seleção. */
export const inputClass =
  "h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm text-zinc-900 shadow-sm outline-none transition placeholder:text-zinc-400 hover:border-zinc-300 focus:border-indigo-500 focus:ring-4 focus:ring-indigo-500/15 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:border-zinc-600";

export function Field({ label, className, children }: { label: string; className?: string; children: ReactNode }) {
  return (
    <label className={cx("flex min-w-0 flex-col gap-1.5", className)}>
      <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">{label}</span>
      {children}
    </label>
  );
}

export function Select({
  value,
  onChange,
  children,
}: {
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
}) {
  return (
    <div className="relative">
      <select value={value} onChange={(e) => onChange(e.target.value)} className={cx(inputClass, "appearance-none pr-9")}>
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-zinc-400" aria-hidden="true" />
    </div>
  );
}

export function Alert({ children }: { children: ReactNode }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-2.5 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-sm text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300"
    >
      <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <LoaderCircle className={cx("animate-spin text-indigo-500", className)} role="status" aria-label="Carregando" />;
}

export function Skeleton({ className, style }: { className?: string; style?: CSSProperties }) {
  return (
    <div
      aria-hidden="true"
      style={style}
      className={cx(
        "rounded-md bg-[length:200%_100%] bg-linear-to-r from-zinc-200 via-zinc-100 to-zinc-200 animate-shimmer dark:from-zinc-800 dark:via-zinc-700 dark:to-zinc-800",
        className,
      )}
    />
  );
}

export function EmptyState({
  icon: Icon,
  title,
  text,
  action,
}: {
  icon: LucideIcon;
  title: string;
  text?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
      <div className="flex size-12 items-center justify-center rounded-2xl bg-zinc-900/5 text-zinc-500 dark:bg-white/5 dark:text-zinc-400">
        <Icon className="size-6" aria-hidden="true" />
      </div>
      <div className="max-w-md">
        <p className="font-medium">{title}</p>
        {text && <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">{text}</p>}
      </div>
      {action}
    </div>
  );
}

/** Fundo com brilho suave, usado na tela de login e no app. */
export function Backdrop() {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 -z-10 bg-[radial-gradient(45rem_30rem_at_12%_-8%,rgba(99,102,241,0.13),transparent),radial-gradient(38rem_26rem_at_96%_0%,rgba(139,92,246,0.11),transparent)] dark:bg-[radial-gradient(45rem_30rem_at_12%_-8%,rgba(99,102,241,0.22),transparent),radial-gradient(38rem_26rem_at_96%_0%,rgba(139,92,246,0.15),transparent)]"
    />
  );
}
