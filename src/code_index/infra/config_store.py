"""Configuração das fontes no banco (tabela "<schema>".config_entries): a fonte da verdade.

Três tipos de entrada: `org` (org do GitHub descoberta via API), `repo` (repo avulso: url git ou path local) e
`docs` (fonte de documentação). O `spec` (jsonb) guarda só os campos diferentes do padrão.

Escrita por entrada (`add`/`put`/`delete`), nunca "regrava tudo": dois processos (serve e worker) mexendo
em entradas diferentes não se atropelam.
"""

from __future__ import annotations

import json
from dataclasses import fields
from typing import Any, Literal

import asyncpg

from code_index.domain.models import DocLinkRule, DocSource, Org, Repo
from code_index.settings import Settings

Kind = Literal["org", "repo", "docs"]
Entry = Repo | Org | DocSource

_SKIP = ("name", "kind", "doc_source")  # `kind`/`doc_source` são derivados (DocSource.to_repo)
_ALWAYS = ("url", "path")               # origem: sempre gravada quando existe


def kind_of(obj: Entry) -> Kind:
    if isinstance(obj, Org):
        return "org"
    if isinstance(obj, DocSource):
        return "docs"
    return "repo"


def _defaults(obj: Entry) -> Entry:
    if isinstance(obj, Org):
        return Org(name=obj.name)
    if isinstance(obj, DocSource):
        return DocSource(name=obj.name, url=obj.url)
    return Repo(name=obj.name, path="x")


def _plain(v: Any) -> Any:
    if isinstance(v, DocLinkRule):
        return {"path": v.path, "repos": list(v.repos)}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def to_spec(obj: Entry) -> dict[str, Any]:
    """Campos diferentes do padrão (sem o nome), com listas no lugar de tuplas. Mudar um padrão no código
    vale para todas as entradas que não o alteraram."""
    defaults = _defaults(obj)
    out: dict[str, Any] = {}
    for f in fields(obj):
        if f.name in _SKIP:
            continue
        v = getattr(obj, f.name)
        if v is None:
            continue
        if f.name in _ALWAYS or v != getattr(defaults, f.name):
            out[f.name] = _plain(v)
    return out


def from_spec(kind: Kind, name: str, spec: dict[str, Any]) -> Entry:
    """Inverso de `to_spec`. Valida como os modelos (ValueError/TypeError para entrada inválida)."""
    values: dict[str, Any] = {}
    for key, v in spec.items():
        if key == "links":
            values[key] = tuple(DocLinkRule(path=str(r["path"]), repos=tuple(r["repos"])) for r in v)
        elif isinstance(v, list):
            values[key] = tuple(v)
        else:
            values[key] = v
    cls: type[Entry] = {"org": Org, "repo": Repo, "docs": DocSource}[kind]
    return cls(name=name, **values)


class ConfigStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._t = f'"{settings.pg_schema}".config_entries'

    async def _load(self, kind: Kind) -> list[Any]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(f"SELECT name, spec FROM {self._t} WHERE kind = $1 ORDER BY seq", kind)
        return [from_spec(kind, r["name"], json.loads(r["spec"])) for r in rows]

    async def orgs(self) -> list[Org]:
        return await self._load("org")

    async def repos(self) -> list[Repo]:
        return await self._load("repo")

    async def docs(self) -> list[DocSource]:
        return await self._load("docs")

    async def add(self, obj: Entry) -> bool:
        """Cadastra uma entrada nova. False se já existe uma com o mesmo tipo e nome (nada muda)."""
        async with self._pool.acquire() as conn:
            res = await conn.execute(
                f"INSERT INTO {self._t} (kind, name, spec) VALUES ($1, $2, $3::jsonb) ON CONFLICT DO NOTHING",
                kind_of(obj), obj.name, json.dumps(to_spec(obj)),
            )
        return res.endswith(" 1")

    async def put(self, obj: Entry) -> None:
        """Grava a entrada (cria ou substitui), mantendo a posição de uma que já existia."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {self._t} (kind, name, spec) VALUES ($1, $2, $3::jsonb)
                    ON CONFLICT (kind, name) DO UPDATE SET spec = EXCLUDED.spec, updated_at = now()""",
                kind_of(obj), obj.name, json.dumps(to_spec(obj)),
            )

    async def delete(self, kind: Kind, name: str) -> bool:
        async with self._pool.acquire() as conn:
            res = await conn.execute(f"DELETE FROM {self._t} WHERE kind = $1 AND name = $2", kind, name)
        return res.endswith(" 1")
