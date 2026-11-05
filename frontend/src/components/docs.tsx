import type { OriginKind } from "../api/types";
import { Badge } from "./ui";

const ORIGIN_LABEL: Record<OriginKind, string> = {
  manual: "manual",
  agent: "agent",
  explicit: "regra",
  service: "nome do serviço",
  tag: "tag",
  url: "link",
  mention: "menção",
};

/** Origens de um vínculo: gravadas (manual/agent) em destaque; automáticas em cinza. */
export function OriginBadges({ origins }: { origins: OriginKind[] }) {
  return (
    <>
      {origins.map((origin) => (
        <Badge key={origin} tone={origin === "manual" || origin === "agent" ? "accent" : "neutral"}>
          {ORIGIN_LABEL[origin]}
        </Badge>
      ))}
    </>
  );
}
