import type { ContractGroup, ContractService, RepoCardInfo } from "../api/types";

/** Grupos de operação do núcleo (domain/contracts), na ordem de exibição. Entidades ficam de fora das operações. */
const GROUP_LABEL: Record<string, string> = {
  query: "Consultas",
  action: "Ações",
  signal: "Sinais",
  event: "Eventos",
};
const NOT_OPERATIONS = new Set<ContractGroup>(["entity"]);

export interface OperationGroup {
  group: ContractGroup;
  label: string;
  names: string[];
}

/** Grupos de operações de um serviço: os do núcleo na ordem, depois os desconhecidos (rótulo = o próprio nome). */
export function operationGroups(service: ContractService): OperationGroup[] {
  const known = Object.keys(GROUP_LABEL);
  const extra = Object.keys(service.groups).filter((g) => !(g in GROUP_LABEL) && !NOT_OPERATIONS.has(g));
  return [...known, ...extra]
    .map((group) => ({ group, label: GROUP_LABEL[group] ?? group, names: service.groups[group] ?? [] }))
    .filter((g) => g.names.length > 0);
}

export function operationCount(service: ContractService): number {
  return operationGroups(service).reduce((n, g) => n + g.names.length, 0);
}

/** Formatos de contrato que os perfis ativos leem (ex.: [".sdl"] no perfil senior); vazio = nenhum perfil lê contratos. */
export function contractFormats(info: RepoCardInfo | null): string[] {
  return [...new Set((info?.profiles ?? []).flatMap((p) => p.contract_formats))];
}

/** "arquivo .sdl", "arquivo .sdl ou .idl" (formatos dos perfis); sem formato conhecido, "contrato de API". */
export function formatsText(formats: string[]): string {
  if (formats.length === 0) return "contrato de API";
  return `arquivo ${formats.length === 1 ? formats[0] : `${formats.slice(0, -1).join(", ")} ou ${formats[formats.length - 1]}`}`;
}
