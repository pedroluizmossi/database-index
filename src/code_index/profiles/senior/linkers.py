"""Linkers do perfil `senior` (ver indexing/linkers.py e sources/base.Linker) e as origens deles.

- `handler`: operação do `.sdl` → classe `<operação>Handler` do mesmo repo (convenção do SDK, `api_impl.py`);
- `openapi-sdl`: páginas de um serviço do catálogo OpenAPI → repos cujo `.sdl` declara o mesmo domain/service
  (lê o catálogo `documents`, tipos de API da fonte `openapi_s3`: sem a fonte cadastrada não produz nada).

O `openapi-service-name` (nome do serviço ≈ nome do repo) é genérico e fica no plugin `openapi_s3`; do perfil ele
só recebe o prefixo dos repos (`NameRules.repo_prefixes`, `snr-`). Sem LLM e sem Voyage.
"""

from __future__ import annotations

import json

import asyncpg

from code_index.profiles.api import (
    API_TYPES, CONTRACTS_SECTION, DOCUMENTS, IMPLEMENTED_BY, ImplNames, Origin, OriginPlacement, Relation, Settings,
    code_key, match_handlers, op_key, repo_key,
)

from .api_impl import handler_names

SDL, HANDLER = "sdl", "handler"

# Na ordem global (domain/relations), como sempre foram: `sdl` logo antes das origens do dono (`declared`...) e
# do `usage` do núcleo; `handler` logo antes de `service` (do núcleo).
SDL_ORIGINS = OriginPlacement(before="usage", origins=(
    Origin(SDL, "SDL", "domain/service declarados no .sdl do repo"),
))
LINK_ORIGINS = OriginPlacement(before="service", origins=(
    Origin(HANDLER, "handler", "classe <operação>Handler (convenção do SDK)"),
))


async def _exists(conn: asyncpg.Connection, *tables: str) -> bool:
    return all([await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", t) for t in tables])


class HandlerLinker:
    """Operação do `.sdl` → classe `<operação>Handler` do mesmo repo (convenção do SDK, sem LLM)."""

    name = "handler"
    origins = frozenset({HANDLER})

    def __init__(self, impl_names: ImplNames = handler_names) -> None:
        self._impl_names = impl_names

    async def run(self, conn: asyncpg.Connection, settings: Settings) -> list[Relation]:
        s = f'"{settings.pg_schema}"'
        cards = await conn.fetch(
            f"""SELECT c.repo, c.material->'facts'->'{CONTRACTS_SECTION}' AS sdl FROM {s}.repo_cards c
                JOIN {s}.repos r ON r.name = c.repo AND r.kind = 'code' AND NOT r.ignored
                WHERE jsonb_array_length(coalesce(c.material->'facts'->'{CONTRACTS_SECTION}', '[]'::jsonb)) > 0"""
        )
        symbols = settings.symbols_table_qualified
        if not cards or not await _exists(conn, symbols):
            return []
        out: list[Relation] = []
        for card in cards:
            classes = [(r["name"], r["path"], int(r["line"])) for r in await conn.fetch(
                f"SELECT name, path, line FROM {symbols} WHERE repo = $1 AND kind = 'container'", card["repo"])]
            for impl in match_handlers(json.loads(card["sdl"]), classes, self._impl_names):
                out.append(Relation(
                    op_key(impl["domain"], impl["service"], impl["operation"]),
                    code_key(card["repo"], impl["path"], impl["line"]), IMPLEMENTED_BY, HANDLER,
                    evidence={**impl, "repo": card["repo"]},
                ))
        return out


class SdlLinker:
    """Páginas de um serviço de API → repos cujo `.sdl` declara o mesmo domain/service."""

    name = "openapi-sdl"
    origins = frozenset({SDL})

    async def run(self, conn: asyncpg.Connection, settings: Settings) -> list[Relation]:
        s = f'"{settings.pg_schema}"'
        rows = await conn.fetch(
            f"""SELECT DISTINCT d.key, c.repo, sdl->>'path' AS sdl_path
                FROM {s}.repo_cards c
                CROSS JOIN LATERAL jsonb_array_elements(c.material->'facts'->'{CONTRACTS_SECTION}') sdl
                JOIN {s}.documents d ON d.present AND d.type = ANY($1::text[])
                     AND lower(d.attrs->>'domain') = lower(sdl->>'domain')
                     AND lower(d.attrs->>'service') = lower(sdl->>'service')
                JOIN {s}.repos r ON r.name = c.repo AND r.kind = 'code' AND NOT r.ignored""",
            sorted(API_TYPES),
        )
        return [Relation(r["key"], repo_key(r["repo"]), DOCUMENTS, SDL, evidence={"sdl": r["sdl_path"]})
                for r in rows]
