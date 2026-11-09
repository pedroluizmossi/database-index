import type { Scope, Me } from "../api/types";

/** Ações de escrita exigem login no navegador (Google ou Microsoft, cookie HttpOnly). API key colada na tela só lê. */
export function canWrite(session: { token: string | null } | null): boolean {
  return session !== null && session.token === null;
}

export function hasScope(me: Me, scope: Scope): boolean {
  return me.scopes.includes(scope);
}
