export function cx(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(" ");
}

const relative = new Intl.RelativeTimeFormat("pt-BR", { numeric: "auto" });
const UNITS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ["year", 60 * 60 * 24 * 365],
  ["month", 60 * 60 * 24 * 30],
  ["week", 60 * 60 * 24 * 7],
  ["day", 60 * 60 * 24],
  ["hour", 60 * 60],
  ["minute", 60],
];

/** "há 2 horas", "ontem"... Usa o horário atual como referência. */
export function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const time = new Date(iso).getTime();
  if (Number.isNaN(time)) return iso;
  const seconds = Math.round((time - Date.now()) / 1000);
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit);
  }
  return relative.format(seconds, "second");
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleString("pt-BR", { dateStyle: "medium", timeStyle: "short" });
}

/** Nome do repo sem o prefixo da org ("org__repo" -> "repo"). */
export function repoShortName(name: string, org: string | null): string {
  return org && name.startsWith(`${org}__`) ? name.slice(org.length + 2) : name;
}

export interface NumberedLine {
  n: number;
  text: string;
}

const NUMBERED = /^\s*(\d+)  (.*)$/;

/** O endpoint de arquivos devolve cada linha como `{numero:>5}  conteúdo`. Separa os dois. */
export function parseNumberedLines(text: string): NumberedLine[] {
  return text.split("\n").map((line, i) => {
    const match = NUMBERED.exec(line);
    return match ? { n: Number(match[1]), text: match[2] } : { n: i + 1, text: line };
  });
}

/** Duração legível: "45 s", "3 min 20 s", "1 h 5 min". */
export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h} h${m ? ` ${m} min` : ""}`;
  if (m > 0) return `${m} min${s ? ` ${s} s` : ""}`;
  return `${s} s`;
}
