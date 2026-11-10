import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { OwnerOption } from "../lib/ownership";
import { cx } from "../lib/utils";
import { inputClass } from "./ui";

/**
 * Campo de domínio com sugestões (combobox ARIA). O texto é livre: o valor digitado vale mesmo sem escolher
 * uma opção; a validação e a confirmação de "criar novo" ficam no OwnerDialog.
 */
export function OwnerCombobox({
  id,
  value,
  onChange,
  options,
  placeholder,
  "aria-label": label,
  autoFocus = false,
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  options: OwnerOption[];
  placeholder?: string;
  /** Rótulo acessível quando não há um <label htmlFor> ligado ao campo. */
  "aria-label"?: string;
  /** Marca o campo com `data-autofocus`: o Dialog foca ele ao abrir. */
  autoFocus?: boolean;
}) {
  const reactId = useId();
  const inputId = id ?? reactId;
  const listId = `${reactId}-list`;
  const optionId = (index: number) => `${reactId}-opt-${index}`;
  const listRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);

  const filtered = useMemo(() => {
    const q = value.trim().toLowerCase();
    if (!q) return options;
    return options.filter((o) => o.value.toLowerCase().includes(q) || o.label.toLowerCase().includes(q));
  }, [options, value]);

  const showList = open && filtered.length > 0;

  // Lista presa ao corpo rolável do dialog: traz a lista para a área visível quando ela abre.
  useEffect(() => {
    if (showList) listRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [showList]);

  // Mantém a opção ativa visível quando as setas a movem.
  useEffect(() => {
    if (active >= 0) document.getElementById(optionId(active))?.scrollIntoView?.({ block: "nearest" });
  }, [active]);

  function pick(option: OwnerOption) {
    onChange(option.value);
    setOpen(false);
    setActive(-1);
  }

  function close() {
    setOpen(false);
    setActive(-1);
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    switch (e.key) {
      case "ArrowDown":
      case "ArrowUp": {
        e.preventDefault();
        if (!showList) {
          setOpen(true);
          setActive(e.key === "ArrowDown" ? 0 : filtered.length - 1);
          return;
        }
        const n = filtered.length;
        setActive((cur) => (e.key === "ArrowDown" ? (cur + 1) % n : cur <= 0 ? n - 1 : cur - 1));
        return;
      }
      case "Enter":
        if (showList && active >= 0) {
          e.preventDefault(); // não envia o formulário: só escolhe a opção
          pick(filtered[active]);
        }
        return;
      case "Escape":
        if (showList) {
          // Fecha só a lista: sem isso o Esc chegaria ao <dialog> e fecharia o modal inteiro.
          e.preventDefault();
          e.stopPropagation();
          close();
        }
        return;
      case "Tab":
        close();
        return;
    }
  }

  return (
    <div className="relative">
      <input
        id={inputId}
        value={value}
        role="combobox"
        aria-label={label}
        aria-autocomplete="list"
        aria-expanded={showList}
        aria-controls={showList ? listId : undefined}
        aria-activedescendant={showList && active >= 0 ? optionId(active) : undefined}
        data-autofocus={autoFocus || undefined}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
          setActive(-1);
        }}
        onClick={() => setOpen(true)}
        onKeyDown={onKeyDown}
        onBlur={close}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
        className={inputClass}
      />
      {showList && (
        <div
          ref={listRef}
          id={listId}
          role="listbox"
          aria-label={label ? `Sugestões para ${label}` : "Sugestões"}
          className="absolute top-full right-0 left-0 z-20 mt-1 max-h-64 overflow-y-auto rounded-lg border border-stone-200 bg-sheet py-1 shadow-[0_12px_24px_-8px_rgb(24_24_27/0.2)] dark:border-stone-700 dark:bg-stone-800 dark:shadow-[0_12px_24px_-8px_rgb(0_0_0/0.6)]"
        >
          {filtered.map((option, index) => {
            const selected = option.value === value;
            return (
              <div
                key={option.value}
                id={optionId(index)}
                role="option"
                aria-selected={selected}
                // mousedown sem padrão mantém o foco no campo (senão o blur fecharia a lista antes do clique).
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(option)}
                onMouseEnter={() => setActive(index)}
                className={cx(
                  "flex min-w-0 cursor-pointer items-baseline gap-2 px-3 py-1.5 text-sm",
                  index === active
                    ? "bg-accent-50 text-stone-900 dark:bg-accent-500/20 dark:text-white"
                    : "text-stone-800 dark:text-stone-200",
                  selected && "font-medium",
                )}
              >
                <span className="shrink-0 font-mono text-[13px] text-stone-900 dark:text-stone-100">{option.value}</span>
                <span className="min-w-0 truncate text-xs text-stone-600 dark:text-stone-400">— {option.label}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
