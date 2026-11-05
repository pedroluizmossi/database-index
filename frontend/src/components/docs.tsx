import { useEffect, useState } from "react";
import { api } from "../api/endpoints";
import type { OriginKind, RelationOrigin } from "../api/types";
import { Badge } from "./ui";

// Origens vêm da API (domain/relations.py); carregadas uma vez e compartilhadas pelos componentes.
let cache: Map<string, RelationOrigin> | null = null;
let pending: Promise<Map<string, RelationOrigin>> | null = null;

function loadOrigins(): Promise<Map<string, RelationOrigin>> {
  if (cache) return Promise.resolve(cache);
  pending ??= api
    .relationOrigins()
    .then((list) => (cache = new Map(list.map((o) => [o.name, o]))))
    .catch(() => new Map<string, RelationOrigin>());
  return pending;
}

export function useOrigins(): Map<string, RelationOrigin> {
  const [origins, setOrigins] = useState(cache ?? new Map<string, RelationOrigin>());
  useEffect(() => {
    let active = true;
    void loadOrigins().then((m) => active && setOrigins(m));
    return () => {
      active = false;
    };
  }, []);
  return origins;
}

/** Origens de um vínculo: gravadas (manual/agent/LLM aprovado) em destaque; automáticas em cinza. */
export function OriginBadges({ origins }: { origins: OriginKind[] }) {
  const meta = useOrigins();
  const sorted = [...origins].sort((a, b) => (meta.get(a)?.order ?? 99) - (meta.get(b)?.order ?? 99));
  return (
    <>
      {sorted.map((origin) => {
        const o = meta.get(origin);
        return (
          <Badge key={origin} tone={o?.stored ? "accent" : "neutral"}>
            <span title={o?.description}>{o?.label ?? origin}</span>
          </Badge>
        );
      })}
    </>
  );
}
