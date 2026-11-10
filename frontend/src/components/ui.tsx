import { useRef, type ButtonHTMLAttributes, type CSSProperties, type KeyboardEvent, type ReactNode } from "react";
import { ChevronDown, CircleAlert, Info, LoaderCircle, type LucideIcon } from "lucide-react";
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
    "bg-accent-600 text-white shadow-sm shadow-accent-950/20 hover:bg-accent-700 dark:hover:bg-accent-500",
  secondary:
    "border border-stone-200 bg-sheet text-stone-800 shadow-sm hover:bg-stone-50 dark:border-stone-700 dark:bg-stone-800 dark:text-stone-100 dark:hover:bg-stone-700",
  danger: "bg-rose-600 text-white shadow-sm shadow-rose-950/20 hover:bg-rose-700 dark:hover:bg-rose-500",
  ghost: "text-stone-600 hover:bg-stone-900/5 hover:text-stone-900 dark:text-stone-300 dark:hover:bg-white/10 dark:hover:text-white",
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
        "inline-flex select-none items-center justify-center rounded-xl font-medium outline-none transition duration-150 focus-visible:ring-4 focus-visible:ring-accent-500/30 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50",
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
  disabled = false,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
      className={cx(
        "inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-stone-500 transition outline-none focus-visible:ring-2 focus-visible:ring-accent-500 dark:text-stone-400",
        disabled
          ? "cursor-not-allowed opacity-40"
          : "hover:bg-stone-900/5 hover:text-stone-900 dark:hover:bg-white/10 dark:hover:text-white",
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
  pending: "bg-stone-500/5 text-stone-700 ring-stone-400/40 dark:text-stone-300 dark:ring-stone-600",
  error: "bg-rose-500/10 text-rose-700 ring-rose-600/20 dark:text-rose-300",
  ignored: "bg-transparent text-stone-500 ring-stone-300 dark:text-stone-400 dark:ring-stone-700",
  warning: "bg-amber-500/10 text-amber-700 ring-amber-600/20 dark:text-amber-300",
  neutral: "bg-stone-500/10 text-stone-700 ring-stone-500/20 dark:text-stone-300",
  accent: "bg-accent-500/10 text-accent-700 ring-accent-600/20 dark:text-accent-300",
};

const DOT_TONE: Partial<Record<BadgeTone, string>> = {
  indexed: "bg-emerald-500",
  // Pendente é ausência de índice, não um estado com cor: anel vazio.
  pending: "border-[1.5px] border-stone-500 dark:border-stone-500",
  error: "bg-rose-500",
  ignored: "bg-stone-300 dark:bg-stone-600",
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
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ring-1 ring-inset",
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
        "inline-flex h-6 min-w-6 items-center justify-center rounded-md border border-stone-200 bg-stone-50 px-1.5 font-mono text-[11px] text-stone-500 shadow-sm dark:border-stone-700 dark:bg-stone-800 dark:text-stone-400",
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
        "rounded-xl border border-stone-200 bg-sheet/85 backdrop-blur-md dark:border-stone-800 dark:bg-stone-900/80",
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
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  // Roving tabindex: só a opção marcada entra na ordem de Tab; as setas e Home/End andam pelo grupo.
  const checked = Math.max(0, options.findIndex((o) => o.value === value));

  function move(from: number, key: string) {
    const n = options.length;
    let next: number;
    switch (key) {
      case "ArrowRight":
      case "ArrowDown":
        next = (from + 1) % n;
        break;
      case "ArrowLeft":
      case "ArrowUp":
        next = (from - 1 + n) % n;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = n - 1;
        break;
      default:
        return;
    }
    onChange(options[next].value);
    buttons.current[next]?.focus();
  }

  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex max-w-full flex-wrap gap-1 rounded-xl bg-stone-900/5 p-1 dark:bg-white/5"
    >
      {options.map((option, index) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            ref={(el) => {
              buttons.current[index] = el;
            }}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={index === checked ? 0 : -1}
            onClick={() => onChange(option.value)}
            onKeyDown={(e: KeyboardEvent<HTMLButtonElement>) => {
              if (!["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp", "Home", "End"].includes(e.key)) return;
              e.preventDefault();
              move(index, e.key);
            }}
            className={cx(
              "h-8 rounded-lg px-3 text-xs font-medium whitespace-nowrap transition outline-none focus-visible:ring-2 focus-visible:ring-accent-500",
              active
                ? "bg-sheet text-stone-900 shadow-sm dark:bg-stone-800 dark:text-white"
                : "text-stone-600 hover:text-stone-900 dark:text-stone-300 dark:hover:text-white",
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
        checked ? "bg-accent-600" : "bg-stone-300 dark:bg-stone-700",
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

/** Cabeçalho de tabela no estilo de etiqueta de espécime: mono pequeno, maiúsculas e espaçado (também nos botões de
 *  ordenação, que o preflight do Tailwind deixa sem `text-transform`). */
export const tableHeadClass =
  "border-b border-stone-200 bg-stone-50 text-left font-mono text-[10px] tracking-[0.14em] text-stone-500 uppercase dark:border-stone-800 dark:bg-stone-900/60 dark:text-stone-400 [&_button]:uppercase";

/** Classes dos campos de texto e seleção. */
export const inputClass =
  "h-10 w-full rounded-xl border border-stone-200 bg-sheet px-3 text-sm text-stone-900 shadow-sm outline-none transition placeholder:text-stone-500 dark:placeholder:text-stone-400 hover:border-stone-300 focus:border-accent-500 focus:ring-4 focus:ring-accent-500/15 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-100 dark:hover:border-stone-600";

export function Field({ label, className, children }: { label: string; className?: string; children: ReactNode }) {
  return (
    <label className={cx("flex min-w-0 flex-col gap-1.5", className)}>
      <span className="text-xs font-medium text-stone-500 dark:text-stone-400">{label}</span>
      {children}
    </label>
  );
}

export function Select({
  value,
  onChange,
  children,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
  /** Rótulo acessível quando o select não está dentro de um <Field>. */
  label?: string;
}) {
  return (
    <div className="relative">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label={label}
        className={cx(inputClass, "appearance-none pr-9")}
      >
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-stone-500 dark:text-stone-400" aria-hidden="true" />
    </div>
  );
}

const ALERT_TONE = {
  error: "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300",
  info: "border-stone-200 bg-stone-50 text-stone-700 dark:border-stone-800 dark:bg-stone-900 dark:text-stone-300",
} as const;

/** Aviso em linha. `error` é falha; `info` é um estado normal que pede atenção (ex.: nada indexado ainda). */
export function Alert({ tone = "error", children }: { tone?: keyof typeof ALERT_TONE; children: ReactNode }) {
  const Icon = tone === "error" ? CircleAlert : Info;
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cx("flex items-start gap-2.5 rounded-lg border px-3.5 py-2.5 text-sm", ALERT_TONE[tone])}
    >
      <Icon className={cx("mt-0.5 size-4 shrink-0", tone === "info" && "text-accent-600 dark:text-accent-400")} aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

/** Barra de progresso determinada (role=progressbar). Sem total, mostra só o trilho. */
export function ProgressBar({
  value,
  max,
  label,
  tone = "accent",
  className,
}: {
  value: number;
  max: number | null;
  label: string;
  tone?: "accent" | "indexed" | "error";
  className?: string;
}) {
  const percent = max && max > 0 ? Math.min(100, (value / max) * 100) : 0;
  const fill = { accent: "bg-accent-500", indexed: "bg-emerald-500", error: "bg-rose-500" }[tone];
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max ?? undefined}
      aria-valuenow={max ? value : undefined}
      className={cx("h-1.5 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-800", className)}
    >
      <div className={cx("h-full rounded-full transition-[width] duration-700 ease-out", fill)} style={{ width: `${percent}%` }} />
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <LoaderCircle className={cx("animate-spin text-accent-500", className)} role="status" aria-label="Carregando" />;
}

export function Skeleton({ className, style }: { className?: string; style?: CSSProperties }) {
  return (
    <div
      aria-hidden="true"
      style={style}
      className={cx(
        "rounded-md bg-[length:200%_100%] bg-linear-to-r from-stone-200 via-stone-100 to-stone-200 animate-shimmer dark:from-stone-800 dark:via-stone-700 dark:to-stone-800",
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
    <div className="flex flex-col items-center justify-center gap-4 px-6 py-14 text-center">
      <Constellation icon={Icon} />
      <div className="max-w-md">
        <p className="font-serif text-xl italic">{title}</p>
        {text && <p className="mt-1.5 text-sm text-stone-500 dark:text-stone-400">{text}</p>}
      </div>
      {action}
    </div>
  );
}

/** Pontos e fios ao redor de um anel vazio, no traço do mapa do Raio X: o lugar onde algo estaria. */
const STARS: [number, number, number][] = [
  [16, 22, 1.6],
  [32, 60, 1.4],
  [112, 12, 1.8],
  [128, 48, 1.4],
  [98, 64, 1.6],
  [52, 8, 1.1],
];

function Constellation({ icon: Icon }: { icon: LucideIcon }) {
  return (
    <div className="relative h-[72px] w-[144px] animate-fade-in" aria-hidden="true">
      <svg viewBox="0 0 144 72" className="absolute inset-0 size-full overflow-visible">
        <g className="stroke-stone-300 dark:stroke-stone-700" fill="none" strokeWidth={0.6}>
          <circle cx={72} cy={36} r={29} strokeDasharray="1 3" />
          {STARS.map(([x, y]) => (
            <line key={`${x}-${y}`} x1={72} y1={36} x2={x} y2={y} strokeDasharray={x < 40 ? "3 3" : undefined} />
          ))}
          <line x1={112} y1={12} x2={128} y2={48} />
          <line x1={16} y1={22} x2={32} y2={60} strokeDasharray="1 3" />
        </g>
        <g className="fill-stone-400 dark:fill-stone-500">
          {STARS.map(([x, y, r]) => (
            <circle key={`${x}-${y}`} cx={x} cy={y} r={r} />
          ))}
        </g>
        <circle cx={72} cy={36} r={17} className="fill-sheet stroke-stone-400 dark:fill-stone-900 dark:stroke-stone-500" strokeWidth={0.8} />
      </svg>
      <Icon className="absolute top-1/2 left-1/2 size-4.5 -translate-x-1/2 -translate-y-1/2 text-stone-500 dark:text-stone-400" />
    </div>
  );
}

