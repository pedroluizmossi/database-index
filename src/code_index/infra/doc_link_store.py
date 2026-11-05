"""Vínculos documentação → repo, calculados na consulta (nunca ficam desatualizados).

Cruza as menções de cada página (doc_mentions) com os apelidos dos repos (repo_aliases), as duas
tabelas mantidas pelo CocoIndex na indexação:
  explicit → regra do repos.toml (padrão LIKE sobre o nome do repo, com ou sem a org)
  tag      → tag da página igual a um apelido (qualquer apelido)
  url      → link github.com/<org>/<repo> na página
  mention  → termo do texto igual a um apelido FORTE (nomes genéricos não ligam por menção).
             Página que menciona mais de DOC_MENTION_MAX_REPOS repos (índices, listas de times)
             não liga por menção: ligaria a quase tudo e encheria a busca filtrada por repo.
  manual/agent → linha gravada em doc_links (tabela nossa, não do CocoIndex): decisão de uma pessoa
                 ou de um agent. Só vale enquanto a página e o repo existirem.
"""

from __future__ import annotations

from collections.abc import Iterable

import asyncpg

from ..settings import Settings

ORIGIN_ORDER = ("manual", "agent", "explicit", "service", "tag", "url", "mention")  # da mais confiável para a menos


def _doc_links(settings: Settings) -> str:
    return f'"{settings.pg_schema}".doc_links'


def links_sql(settings: Settings) -> str:
    """SELECT doc_repo, doc_path, repo, origin — usado como subconsulta."""
    m = settings.qualified(settings.mentions_table)
    a = settings.qualified(settings.aliases_table)
    pages = settings.qualified(settings.doc_pages_table)
    r = f'"{settings.pg_schema}".repos'
    max_repos = int(settings.doc_mention_max_repos)  # inteiro de configuração, não entrada do usuário
    return f"""
        SELECT m.doc_repo, m.doc_path, a.repo, m.origin
        FROM {m} m JOIN {a} a ON a.alias = m.term AND a.repo <> m.doc_repo
        WHERE left(m.doc_repo, 14) <> 'docs__openapi-' AND (m.origin IN ('tag', 'url') OR (m.origin = 'mention' AND a.strong AND (m.doc_repo, m.doc_path) NOT IN (
            SELECT m2.doc_repo, m2.doc_path FROM {m} m2
            JOIN {a} a2 ON a2.alias = m2.term AND a2.repo <> m2.doc_repo AND a2.strong
            WHERE m2.origin = 'mention' GROUP BY 1, 2 HAVING count(DISTINCT a2.repo) > {max_repos})))
        UNION
        SELECT p.repo, p.path, matched.repo, 'service'
        FROM {pages} p JOIN (
          SELECT services.service, match.repo FROM (
            SELECT DISTINCT split_part(path, '/', 2) AS service FROM {pages}
            WHERE left(repo, 14) = 'docs__openapi-'
          ) services CROSS JOIN LATERAL (
            SELECT min(name) AS repo FROM (
                SELECT name, rank, min(rank) OVER () AS best_rank FROM (
                    SELECT name, CASE WHEN repo_name = service_name THEN 0 ELSE 1 END AS rank
                    FROM (
                        SELECT r.name,
                          regexp_replace(regexp_replace(lower(regexp_replace(r.name, '^.*__', '')),
                                         '^snr-', ''), '[^a-z0-9]', '', 'g') AS repo_name,
                          regexp_replace(lower(services.service), '[^a-z0-9]', '', 'g') AS service_name
                        FROM {r} r WHERE r.kind = 'code' AND NOT r.ignored
                    ) names
                    WHERE length(service_name) >= 4 AND (
                        repo_name = service_name OR (
                            length(regexp_replace(service_name, '(service|backend)$', '')) >= 4 AND
                            regexp_replace(repo_name, '(service|backend)$', '') =
                            regexp_replace(service_name, '(service|backend)$', '')
                        )
                    )
                ) candidates
            ) ranked WHERE rank = best_rank HAVING count(*) = 1
          ) match WHERE match.repo IS NOT NULL
        ) matched ON matched.service = split_part(p.path, '/', 2)
        WHERE left(p.repo, 14) = 'docs__openapi-'
        UNION
        SELECT m.doc_repo, m.doc_path, r.name, 'explicit'
        FROM {m} m JOIN {r} r ON m.origin = 'explicit' AND r.kind = 'code' AND NOT r.ignored AND (r.name LIKE m.term OR r.name LIKE '%\\_\\_' || m.term)
        UNION
        SELECT l.doc_repo, l.doc_path, l.repo, l.origin
        FROM {_doc_links(settings)} l
        JOIN {pages} p ON p.repo = l.doc_repo AND p.path = l.doc_path
        JOIN {r} r ON r.name = l.repo AND r.kind = 'code' AND NOT r.ignored"""


def ignores_table(settings: Settings) -> str:
    return f'"{settings.pg_schema}".doc_ignores'


class DocLinkStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._settings = settings

    async def available(self) -> bool:
        """As tabelas de documentação existem (já houve um sync com a versão que as cria)."""
        s = self._settings
        return bool(await self._pool.fetchval(
            "SELECT to_regclass($1) IS NOT NULL AND to_regclass($2) IS NOT NULL AND to_regclass($3) IS NOT NULL"
            " AND to_regclass($4) IS NOT NULL",
            s.qualified(s.mentions_table), s.qualified(s.aliases_table), s.qualified(s.doc_pages_table),
            _doc_links(s),
        ))

    async def openapi_counts(self, repos: list[str]) -> list[dict]:
        """Conta documentos de operações por domínio e serviço já gravados no índice."""
        if not repos or not await self.available():
            return []
        pages = self._settings.qualified(self._settings.doc_pages_table)
        rows = await self._pool.fetch(
            f"SELECT repo, split_part(path, '/', 1) AS domain, split_part(path, '/', 2) AS service, "
            f"count(*) AS operations FROM {pages} WHERE repo = ANY($1::text[]) "
            "AND split_part(path, '/', 3) <> '__overview.md' "
            "GROUP BY repo, 2, 3", repos,
        )
        return [dict(row) for row in rows]

    async def xray(self) -> dict:
        """Inventário e vínculos existentes, sem truncar a lista de páginas."""
        if not await self.available():
            return {"available": False, "pages": [], "links": []}
        pages = self._settings.qualified(self._settings.doc_pages_table)
        rows = await self._pool.fetch(f"SELECT repo, path, title, url FROM {pages} ORDER BY repo, path")
        links = await self._pool.fetch(
            f"SELECT DISTINCT l.doc_repo, l.doc_path, l.repo, l.origin FROM ({links_sql(self._settings)}) l "
            f"JOIN {pages} p ON p.repo = l.doc_repo AND p.path = l.doc_path "
            "ORDER BY l.doc_repo, l.doc_path, l.repo, l.origin"
        )
        return {"available": True, "pages": [dict(r) for r in rows], "links": [dict(r) for r in links]}

    # ------------------------------------------------------------------ vínculos gravados (manual/agent)

    async def set_link(self, doc_repo: str, doc_path: str, repo: str, *, origin: str, reason: str) -> None:
        """Grava (ou atualiza) um vínculo. Não reindexa nada: a consulta lê a linha na hora."""
        await self._pool.execute(
            f"""INSERT INTO {_doc_links(self._settings)} (doc_repo, doc_path, repo, origin, reason)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (doc_repo, doc_path, repo)
                DO UPDATE SET origin = EXCLUDED.origin, reason = EXCLUDED.reason, created_at = now()""",
            doc_repo, doc_path, repo, origin, reason,
        )

    async def remove_link(self, doc_repo: str, doc_path: str, repo: str) -> bool:
        status = await self._pool.execute(
            f"DELETE FROM {_doc_links(self._settings)} WHERE doc_repo = $1 AND doc_path = $2 AND repo = $3",
            doc_repo, doc_path, repo,
        )
        return status.endswith(" 1")

    async def list_links(self, *, doc_repo: str | None = None, repo: str | None = None) -> list[dict]:
        rows = await self._pool.fetch(
            f"""SELECT doc_repo, doc_path, repo, origin, reason, created_at FROM {_doc_links(self._settings)}
                WHERE ($1::text IS NULL OR doc_repo = $1) AND ($2::text IS NULL OR repo = $2)
                ORDER BY created_at DESC""",
            doc_repo, repo,
        )
        return [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]

    async def overview(self, *, limit: int, offset: int = 0, docs_only: bool = False) -> list[dict]:
        """Páginas indexadas, cada uma com os repos ligados: origens automáticas e gravadas (manual/agent)."""
        if not await self.available():
            return []
        s = self._settings
        pages = await self._pool.fetch(
            f"SELECT repo, path, url, title FROM {s.qualified(s.doc_pages_table)} "
            "WHERE (NOT $3::boolean OR left(repo, 14) <> 'docs__openapi-') "
            "ORDER BY repo, path LIMIT $1 OFFSET $2",
            max(1, min(limit, 2000)), max(0, offset), docs_only,
        )
        links = await self._pool.fetch(f"SELECT doc_repo, doc_path, repo, origin FROM ({links_sql(s)}) l")
        manual = await self._pool.fetch(f"SELECT doc_repo, doc_path, repo, reason FROM {_doc_links(s)}")
        reasons = {(m["doc_repo"], m["doc_path"], m["repo"]): m["reason"] for m in manual}
        by_page: dict[tuple[str, str], dict[str, dict]] = {}
        for link in links:
            entry = by_page.setdefault((link["doc_repo"], link["doc_path"]), {}).setdefault(
                link["repo"], {"repo": link["repo"], "origins": set(), "reason": None, "removable": False})
            entry["origins"].add(link["origin"])
        for (doc_repo, doc_path, repo), reason in reasons.items():
            entry = by_page.setdefault((doc_repo, doc_path), {}).setdefault(
                repo, {"repo": repo, "origins": set(), "reason": None, "removable": False})
            entry.update(removable=True, reason=reason)
        out = []
        for p in pages:
            links_of = by_page.get((p["repo"], p["path"]), {})
            out.append({
                "repo": p["repo"], "path": p["path"], "url": p["url"], "title": p["title"],
                "links": [
                    {"repo": v["repo"], "origins": sorted(v["origins"], key=ORIGIN_ORDER.index),
                     "reason": v["reason"], "removable": v["removable"]}
                    for v in sorted(links_of.values(), key=lambda v: v["repo"])
                ],
            })
        return out

    async def page_indexed(self, doc_repo: str, doc_path: str) -> bool:
        s = self._settings
        return bool(await self._pool.fetchval(
            f"SELECT EXISTS (SELECT 1 FROM {s.qualified(s.doc_pages_table)} WHERE repo = $1 AND path = $2)",
            doc_repo, doc_path,
        ))

    async def annotate(self, pairs: Iterable[tuple[str, str]]) -> dict[tuple[str, str], dict]:
        """Para cada (repo, path) de documentação externa: url, título e repos ligados (com a origem)."""
        keys = list(dict.fromkeys(pairs))
        if not keys or not await self.available():
            return {}
        s = self._settings
        repos, paths = [k[0] for k in keys], [k[1] for k in keys]
        rows = await self._pool.fetch(
            f"""SELECT p.repo, p.path, p.url, p.title FROM {s.qualified(s.doc_pages_table)} p
                JOIN unnest($1::text[], $2::text[]) AS k(repo, path) USING (repo, path)""",
            repos, paths,
        )
        out: dict[tuple[str, str], dict] = {(r["repo"], r["path"]): {"url": r["url"], "title": r["title"],
                                                                       "linked": {}} for r in rows}
        links = await self._pool.fetch(
            f"""SELECT l.doc_repo, l.doc_path, l.repo, l.origin FROM ({links_sql(s)}) l
                JOIN unnest($1::text[], $2::text[]) AS k(repo, path) ON l.doc_repo = k.repo AND l.doc_path = k.path""",
            repos, paths,
        )
        for link in links:
            entry = out.setdefault((link["doc_repo"], link["doc_path"]), {"url": None, "title": None, "linked": {}})
            entry["linked"].setdefault(link["repo"], set()).add(link["origin"])
        return out

    async def docs_for_repo(self, repo: str, *, limit: int = 100) -> list[dict]:
        """Páginas ligadas a um repo, da origem mais confiável para a menos."""
        if not await self.available():
            return []
        s = self._settings
        rows = await self._pool.fetch(
            f"""SELECT l.doc_repo, l.doc_path, array_agg(DISTINCT l.origin) AS origins, p.url, p.title
                FROM ({links_sql(s)}) l
                LEFT JOIN {s.qualified(s.doc_pages_table)} p ON p.repo = l.doc_repo AND p.path = l.doc_path
                WHERE l.repo = $1
                GROUP BY l.doc_repo, l.doc_path, p.url, p.title
                LIMIT $2""",
            repo, max(1, min(limit, 500)),
        )

        def rank(origins: list[str]) -> int:
            return min(ORIGIN_ORDER.index(o) for o in origins if o in ORIGIN_ORDER)

        out = [{"doc_repo": r["doc_repo"], "path": r["doc_path"], "url": r["url"], "title": r["title"],
                "origins": sorted(r["origins"], key=ORIGIN_ORDER.index)} for r in rows]
        return sorted(out, key=lambda d: (rank(d["origins"]), d["title"] or d["path"]))

    async def set_ignore(self, doc_repo: str, doc_path: str, reason: str) -> None:
        await self._pool.execute(
            f"""INSERT INTO {ignores_table(self._settings)} (doc_repo, doc_path, reason) VALUES ($1, $2, $3)
                ON CONFLICT (doc_repo, doc_path) DO UPDATE SET reason = EXCLUDED.reason""",
            doc_repo, doc_path, reason,
        )

    async def remove_ignore(self, doc_repo: str, doc_path: str) -> bool:
        status = await self._pool.execute(
            f"DELETE FROM {ignores_table(self._settings)} WHERE doc_repo = $1 AND doc_path = $2", doc_repo, doc_path,
        )
        return status.endswith(" 1")
