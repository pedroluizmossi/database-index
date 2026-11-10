import type { KeyboardEvent } from "react";
import type { OriginKind } from "../api/types";
import type { XrayKind, XrayTarget } from "../lib/xray";
import { cx } from "../lib/utils";
import { useOrigins } from "./docs";
import { KIND_META } from "./kind";

/** Vizinho do item no centro do mapa. `inferred`: só ligado por repositórios em comum (sem vínculo direto). */
export interface MapNeighbor {
  key: string;
  target: XrayTarget;
  title: string;
  subtitle: string;
  origins: OriginKind[];
  inferred: boolean;
}

const W = 680;
const H = 640;
const CX = W / 2;
const CY = 316;
const RIM = 292;          // aro graduado
const R_DIRECT = 150;     // vínculos diretos (com rótulo radial)
const R_INFERRED = 100;   // inferidos (rótulo só ao passar o mouse)
const R_INNER = 50;
const SECTOR_GAP = 14;    // graus entre os setores de cada tipo
const MAX_NODES = 40;
const LABEL_CHARS = 20;
const KIND_ORDER: XrayKind[] = ["repo", "doc", "api"];

interface Placed {
  item: MapNeighbor;
  x: number;
  y: number;
  angle: number;
}

const rad = (deg: number) => (deg * Math.PI) / 180;
const at = (r: number, deg: number) => ({ x: CX + r * Math.cos(rad(deg)), y: CY + r * Math.sin(rad(deg)) });
const short = (text: string) => (text.length > LABEL_CHARS ? `${text.slice(0, LABEL_CHARS - 1)}…` : text);

/**
 * Setores fixos por tipo (repo, doc, API), a partir do topo, com largura proporcional à quantidade (mínimo de 3
 * posições, para um tipo pequeno não sumir). Posições determinísticas: o mesmo item cai sempre no mesmo lugar.
 * Acima de MAX_NODES, cada tipo mostra a sua parte e o resto vira "+N" (as listas abaixo do mapa têm todos).
 */
function layout(neighbors: MapNeighbor[]): { placed: Placed[]; overflow: { kind: XrayKind; count: number; x: number; y: number }[] } {
  // Diretos antes dos inferidos: com o limite de nós, os vínculos que comprovam algo são os que ficam no mapa.
  const byKind = KIND_ORDER.map((kind) => ({
    kind,
    items: neighbors.filter((n) => n.target.kind === kind).sort((a, b) => Number(a.inferred) - Number(b.inferred)),
  })).filter((g) => g.items.length > 0);
  const total = neighbors.length;
  const groups = byKind.map((g) => {
    const cap = total <= MAX_NODES ? g.items.length : Math.max(1, Math.floor((MAX_NODES * g.items.length) / total));
    return { ...g, shown: g.items.slice(0, cap), hidden: g.items.length - Math.min(cap, g.items.length) };
  });
  const weights = groups.map((g) => Math.max(3, g.shown.length));
  const sum = weights.reduce((a, b) => a + b, 0);
  const free = 360 - (groups.length > 1 ? SECTOR_GAP * groups.length : 0);
  const placed: Placed[] = [];
  const overflow: { kind: XrayKind; count: number; x: number; y: number }[] = [];
  let start = -90 + (groups.length > 1 ? SECTOR_GAP / 2 : 0);
  groups.forEach((g, i) => {
    const span = (free * weights[i]) / sum;
    for (const inferred of [false, true]) {
      const ring = g.shown.filter((n) => n.inferred === inferred);
      ring.forEach((item, j) => {
        const angle = start + (span * (j + 0.5)) / ring.length;
        placed.push({ item, angle, ...at(inferred ? R_INFERRED : R_DIRECT, angle) });
      });
    }
    if (g.hidden > 0) overflow.push({ kind: g.kind, count: g.hidden, ...at(R_INNER + 14, start + span / 2) });
    start += span + (groups.length > 1 ? SECTOR_GAP : 0);
  });
  return { placed, overflow };
}

function onActivate(fn: () => void) {
  return (e: KeyboardEvent) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    fn();
  };
}

/**
 * Mapa radial do Raio X, no traço da prancha de fundo: o item estudado no centro (vermelhão: "encontrado"), os
 * vínculos diretos no anel de fora e os inferidos no de dentro. O traço diz a origem: cheio = gravado (pessoa,
 * agent ou LLM aprovado), tracejado = automático, pontilhado = inferido. Clicar num vizinho o traz para o centro:
 * os nós que continuam deslizam até a nova posição, e linhas e rótulos novos aparecem depois.
 */
export function XrayMap({ center, neighbors, onSelect }: { center: { key: string; title: string }; neighbors: MapNeighbor[]; onSelect: (target: XrayTarget) => void }) {
  const meta = useOrigins();
  const { placed, overflow } = layout(neighbors);
  const stored = (origins: OriginKind[]) => origins.some((o) => meta.get(o)?.stored);
  const ticks = Array.from({ length: 72 }, (_, i) => i * 5);

  return (
    <figure className="space-y-3">
      <figcaption className="flex items-baseline justify-between gap-4 border-b border-stone-300/70 pb-2 font-mono text-[10px] tracking-[0.18em] text-stone-500 uppercase dark:border-stone-700 dark:text-stone-400">
        <span className="truncate">Fig. 1 — vínculos de {center.title}</span>
        <span className="shrink-0 tabular-nums">n = {neighbors.length}</span>
      </figcaption>

      <svg viewBox={`0 0 ${W} ${H}`} className="mx-auto block w-full max-w-[640px] select-none" role="group" aria-label={`Mapa de vínculos de ${center.title}`}>
        {/* instrumentos: aro graduado e isolinhas */}
        <g fill="none" className="stroke-stone-400/60 dark:stroke-stone-600" aria-hidden="true">
          <circle cx={CX} cy={CY} r={RIM} strokeWidth={0.8} />
          <circle cx={CX} cy={CY} r={RIM - 5} strokeWidth={0.4} />
          {[R_INNER, R_INFERRED, R_DIRECT].map((r) => (
            <circle key={r} cx={CX} cy={CY} r={r} strokeWidth={0.5} strokeDasharray="1 3" />
          ))}
          {ticks.map((deg) => {
            const long = deg % 30 === 0;
            const a = at(RIM, deg - 90);
            const b = at(RIM - (long ? 9 : 4), deg - 90);
            return <line key={deg} x1={a.x} y1={a.y} x2={b.x} y2={b.y} strokeWidth={long ? 0.8 : 0.5} />;
          })}
        </g>
        <g className="fill-stone-400 font-mono dark:fill-stone-500" fontSize={9} letterSpacing={1.5} textAnchor="middle" aria-hidden="true">
          {[0, 90, 180, 270].map((deg) => {
            const p = at(RIM + 12, deg - 90);
            return (
              <text key={deg} x={p.x} y={p.y + 3}>
                {String(deg).padStart(3, "0")}
              </text>
            );
          })}
        </g>

        {/* vínculos: aparecem depois que os nós chegam ao lugar */}
        <g aria-hidden="true">
          {placed.map(({ item, x, y }) => {
            const solid = !item.inferred && stored(item.origins);
            return (
              <line
                key={`${center.key}|${item.key}`}
                x1={CX}
                y1={CY}
                x2={x}
                y2={y}
                stroke={item.inferred ? "currentColor" : KIND_META[item.target.kind].color}
                strokeWidth={solid ? 1.3 : 0.9}
                strokeDasharray={item.inferred ? "1 4" : solid ? undefined : "4 3"}
                strokeLinecap="round"
                className={cx("animate-fade-in [animation-delay:300ms]", item.inferred && "text-stone-400 dark:text-stone-600")}
              />
            );
          })}
        </g>

        {overflow.map((o) => (
          <text key={`${center.key}|+${o.kind}`} x={o.x} y={o.y + 3} textAnchor="middle" fontSize={10} className="animate-fade-in fill-stone-500 font-mono">
            +{o.count}
          </text>
        ))}

        {/* Centro e vizinhos numa só lista com key: o vizinho clicado é o mesmo elemento que vira centro, então
            ele desliza até lá (transição do transform); o antigo centro desliza para a sua posição de vizinho. */}
        {[{ center: true as const, key: center.key }, ...placed.map((p) => ({ center: false as const, ...p }))].map((node) => {
          const motion = "transition-transform duration-500 ease-[cubic-bezier(0.16,1,0.3,1)]";
          if (node.center) {
            return (
              <g key={node.key} style={{ transform: `translate(${CX}px, ${CY}px)` }} className={motion}>
                <g className="animate-pop">
                  <circle r={22} fill="none" stroke="var(--color-signal)" strokeWidth={0.6} strokeDasharray="2 3" opacity={0.6} />
                  <circle r={13} fill="none" stroke="var(--color-signal)" strokeWidth={1} />
                  <circle r={6.5} fill="var(--color-signal)" />
                </g>
                <title>{center.title}</title>
              </g>
            );
          }
          const { item, x, y, angle } = node;
          const left = Math.cos(rad(angle)) < 0;
          const color = KIND_META[item.target.kind].color;
          const select = () => onSelect(item.target);
          return (
            <g
              key={item.key}
              role="button"
              tabIndex={0}
              aria-label={`${KIND_META[item.target.kind].label}: ${item.title}${item.inferred ? " (inferido)" : ""}`}
              onClick={select}
              onKeyDown={onActivate(select)}
              style={{ transform: `translate(${x}px, ${y}px)` }}
              className={cx("group cursor-pointer outline-none", motion)}
            >
              <g className="animate-fade-in [animation-delay:150ms]">
                <circle r={12} fill="transparent" />
                <circle r={11} fill="none" stroke="var(--color-accent-500)" strokeWidth={1.5} className="opacity-0 group-focus-visible:opacity-100" />
                <circle
                  r={item.inferred ? 4 : 5.5}
                  fill={color}
                  stroke={color}
                  strokeWidth={item.inferred ? 1.2 : 1.5}
                  className={cx(
                    "transition-[r] duration-150",
                    item.inferred ? "fill-sheet group-hover:[r:6px] dark:fill-stone-900" : "stroke-sheet group-hover:[r:7.5px] dark:stroke-stone-900",
                  )}
                />
              </g>
              <text
                key={`${center.key}|label`}
                transform={`rotate(${left ? angle + 180 : angle})`}
                x={left ? -11 : 11}
                y={3.5}
                textAnchor={left ? "end" : "start"}
                fontSize={11}
                className={cx(
                  "fill-stone-700 transition-opacity dark:fill-stone-300",
                  item.inferred
                    ? "opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100"
                    : "animate-fade-in [animation-delay:450ms] group-hover:fill-stone-950 dark:group-hover:fill-white",
                )}
              >
                {short(item.title)}
              </text>
              <title>{`${item.title}\n${item.subtitle}`}</title>
            </g>
          );
        })}
      </svg>

      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-t border-stone-300/70 pt-2 font-mono text-[10px] tracking-[0.14em] text-stone-500 uppercase dark:border-stone-700 dark:text-stone-400">
        <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
          {KIND_ORDER.map((k) => (
            <span key={k} className="inline-flex items-center gap-1.5">
              <svg viewBox="0 0 10 10" className="size-2" aria-hidden="true">
                <circle cx={5} cy={5} r={4} fill={KIND_META[k].color} />
              </svg>
              {k === "api" ? "API" : KIND_META[k].label}
            </span>
          ))}
        </span>
        <span className="flex flex-wrap items-center gap-x-4 gap-y-1" aria-label="Legenda do traço">
          <Stroke dash={undefined} label="gravado" />
          <Stroke dash="4 3" label="automático" />
          <Stroke dash="1 3" label="inferido" />
        </span>
      </div>
    </figure>
  );
}

function Stroke({ dash, label }: { dash: string | undefined; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <svg viewBox="0 0 22 4" className="h-1 w-5.5" aria-hidden="true">
        <line x1={1} y1={2} x2={21} y2={2} stroke="currentColor" strokeWidth={1.2} strokeDasharray={dash} strokeLinecap="round" />
      </svg>
      {label}
    </span>
  );
}
