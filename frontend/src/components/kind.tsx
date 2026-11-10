import { BookOpen, Braces, GitBranch, type LucideIcon } from "lucide-react";
import { cx } from "../lib/utils";

/** Tipos de entidade do índice. Cada um tem cor e ícone próprios, os mesmos em todas as telas. */
export type EntityKind = "repo" | "doc" | "api";

export const KIND_META: Record<EntityKind, { label: string; icon: LucideIcon; tile: string; text: string; color: string }> = {
  repo: { label: "Repositório", icon: GitBranch, tile: "bg-kind-repo/12 dark:bg-kind-repo/18", text: "text-kind-repo", color: "var(--kind-repo)" },
  doc: { label: "Documento", icon: BookOpen, tile: "bg-kind-doc/12 dark:bg-kind-doc/18", text: "text-kind-doc", color: "var(--kind-doc)" },
  api: { label: "API", icon: Braces, tile: "bg-kind-api/12 dark:bg-kind-api/18", text: "text-kind-api", color: "var(--kind-api)" },
};

/** Ícone do tipo num quadrado tingido: reconhece-se o tipo antes de ler o texto. */
export function KindIcon({ kind, size = "md", label, className }: { kind: EntityKind; size?: "sm" | "md" | "lg"; label?: string; className?: string }) {
  const meta = KIND_META[kind];
  const Icon = meta.icon;
  return (
    <span
      title={label ?? meta.label}
      className={cx(
        "inline-flex shrink-0 items-center justify-center",
        size === "sm" ? "size-5 rounded" : size === "md" ? "size-7 rounded-md" : "size-10 rounded-lg",
        meta.tile,
        meta.text,
        className,
      )}
    >
      <Icon className={size === "sm" ? "size-3" : size === "md" ? "size-3.5" : "size-5"} aria-hidden="true" />
    </span>
  );
}
