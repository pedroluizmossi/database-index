"""Modelo de relações: fonte nova só com plugin, recálculo, entidade genérica e migração dos dados antigos."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from code_index.domain.documents import WIKI_PAGE, DocumentMeta
from code_index.domain.doc_links import parse_front_matter
from code_index.domain.models import DocSource
from code_index.domain.relations import DOCUMENTS, Relation, doc_key, repo_key
from code_index.settings import Settings

pytestmark = pytest.mark.integration


class FolderLinker:
    """Linker da fonte: página que declara `repo:` no front-matter documenta esse repo (origem 'source')."""

    name = "pasta-declara-repo"
    origins = frozenset({"source"})

    async def run(self, conn: asyncpg.Connection, settings: Settings) -> list[Relation]:
        rows = await conn.fetch(
            f"""SELECT d.key, d.attrs->>'repo' AS repo FROM "{settings.pg_schema}".documents d
                WHERE d.present AND d.doc_repo = 'docs__notas' AND d.attrs ? 'repo'""")
        return [Relation(r["key"], repo_key(r["repo"]), DOCUMENTS, "source") for r in rows]


class FolderPlugin:
    """Fonte de teste: copia os .md de uma pasta local (DocSource.url = caminho)."""

    type = "pasta"
    label = "Pasta local"

    async def fetch(self, source: DocSource, dest: Path, settings: Settings, *, only: Any = None) -> str:
        files = sorted(Path(source.url).glob("*.md"))
        for f in files:
            shutil.copy(f, dest / f.name)
        return "pasta:" + ",".join(f"{f.name}:{f.stat().st_mtime_ns}" for f in files)

    def documents(self, source: DocSource, dest: Path) -> list[DocumentMeta]:
        out = []
        for f in sorted(dest.glob("*.md")):
            meta = parse_front_matter(f.read_text(encoding="utf-8"))[0]
            attrs = {"repo": meta["repo"]} if meta.get("repo") else {}
            out.append(DocumentMeta(path=f.name, type=WIKI_PAGE, title=str(meta.get("title") or f.stem), attrs=attrs))
        return out

    async def preview(self, source: DocSource, settings: Settings, *, limit: int) -> dict[str, Any]:
        return {"name": source.repo_name, "total": len(list(Path(source.url).glob("*.md")))}

    def linkers(self) -> list[Any]:
        return [FolderLinker()]


async def test_fonte_nova_so_com_plugin(int_settings: Settings, tmp_path: Path, fake_llm: Any,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from code_index.services import open_service
    from code_index.sources import registry

    monkeypatch.setitem(registry._PLUGINS, "pasta", FolderPlugin())
    notas = tmp_path / "notas"
    notas.mkdir()
    (notas / "deploy.md").write_text('---\ntitle: "Deploy"\nrepo: "email-front"\n---\n\nComo publicar.\n')
    (notas / "geral.md").write_text('---\ntitle: "Geral"\n---\n\nTexto sem repo.\n')
    front = tmp_path / "front"
    front.mkdir()
    (front / "app.ts").write_text("export const x = 1;\n")

    async with open_service(int_settings) as svc:
        await svc.add_repo(str(front), name="email-front")
        await svc._config.put(DocSource(name="notas", url=str(notas), type="pasta"))
        assert (await svc.docs_preview("notas"))["total"] == 2
        res = await svc.sync(None, batch=None, do_discover=False, progress=False)
        assert res.errors == {} and "docs__notas" in res.indexed

        # catálogo, vínculo do linker da fonte, X-Ray e relação genérica: nada no núcleo mudou para isso
        linked = await svc.linked_docs("email-front")
        assert [(d["path"], d["origins"]) for d in linked] == [("deploy.md", ["source"])]
        xray = await svc.xray()
        assert {(p["repo"], p["path"], p["type"]) for p in xray["pages"]} == {
            ("docs__notas", "deploy.md", "wiki_page"), ("docs__notas", "geral.md", "wiki_page")}
        rel = await svc.related("email-front")
        assert [(r["entity"], r["type"], r["direction"]) for r in rel["relations"]] == [
            (doc_key("docs__notas", "deploy.md"), "documents", "in")]
        hits = await svc.search("Como publicar", top_k=5, kind="doc", rerank=False, mode="keyword")
        assert any(h.title == "Deploy" and h.linked_repos == ["email-front"] for h in hits)

        # a página deixa de declarar o repo → o vínculo some no próximo sync (linker recalculado)
        (notas / "deploy.md").write_text('---\ntitle: "Deploy"\n---\n\nComo publicar.\n')
        await svc.sync(["docs__notas"], batch=None, do_discover=False, progress=False)
        assert await svc.linked_docs("email-front") == []

        # página ignorada: some da busca e a marca sobrevive ao recálculo do catálogo
        await svc._documents.set_ignore("docs__notas", "geral.md", "teste")
        assert (await svc.rebuild_relations())["documents"] == {"docs__notas": 2}
        hits = await svc.search("Texto sem repo", top_k=5, kind="doc", rerank=False, mode="keyword")
        assert all(h.path != "geral.md" for h in hits)
        assert await svc._documents.remove_ignore("docs__notas", "geral.md")
        hits = await svc.search("Texto sem repo", top_k=5, kind="doc", rerank=False, mode="keyword")
        assert any(h.path == "geral.md" for h in hits)


async def test_migracao_010_copia_dados_e_apaga_tabelas(int_settings: Settings, pg_url: str) -> None:
    """Aplica as migrations até a 009, grava dados no formato antigo e confere a cópia feita pela 010."""
    from code_index.infra.db import SQL_DIR

    schema = "code_index_mig_test"
    conn = await asyncpg.connect(pg_url)
    try:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        for path in sorted(SQL_DIR.glob("[0-9][0-9][0-9]_*.sql")):
            if path.stem.startswith("010"):
                break
            await conn.execute(path.read_text(encoding="utf-8").replace("{schema}", schema))
        q = f'"{schema}"'
        await conn.execute(f"""INSERT INTO {q}.doc_links (doc_repo, doc_path, repo, origin, reason, status, action,
                                                        basis, confidence, agent) VALUES
            ('docs__wiki', 'pt-br/A.md', 'o__x', 'manual', 'é o serviço', 'active', 'add', NULL, NULL, ''),
            ('docs__wiki', 'pt-br/B.md', 'o__y', 'llm', 'citação solta', 'suggested', 'remove', NULL, 'alta', 'haiku')""")
        await conn.execute(f"INSERT INTO {q}.doc_ignores (doc_repo, doc_path, reason) VALUES ('docs__wiki', 'pt-br/C.md', 'teste')")
        await conn.execute(f"""INSERT INTO {q}.api_impls (domain, service, operation, kind, repo, symbol, path, line)
                               VALUES ('platform', 'email', 'sendEmail', 'action', 'o__x', 'SendEmailHandler', 'S.java', 9)""")
        await conn.execute(f"""INSERT INTO {q}.doc_link_reviews (doc_repo, doc_path, input_hash, agent, result)
                               VALUES ('docs__wiki', 'pt-br/A.md', 'h1', 'haiku', '{{}}'::jsonb)""")

        await conn.execute((SQL_DIR / "010_relations_documents.sql").read_text(encoding="utf-8").replace("{schema}", schema))

        rels = {(r["src"], r["dst"], r["type"], r["origin"], r["status"], r["action"]): json.loads(r["evidence"])
                for r in await conn.fetch(f"SELECT * FROM {q}.relations")}
        assert rels == {
            ("doc:docs__wiki/pt-br/A.md", "repo:o__x", "documents", "manual", "active", "add"): {"reason": "é o serviço"},
            ("doc:docs__wiki/pt-br/B.md", "repo:o__y", "documents", "llm", "suggested", "remove"): {"reason": "citação solta"},
            ("op:platform/email/sendEmail", "code:o__x/S.java:9", "implemented_by", "handler", "active", "add"): {
                "domain": "platform", "service": "email", "operation": "sendEmail", "kind": "action", "repo": "o__x",
                "symbol": "SendEmailHandler", "path": "S.java", "line": 9},
        }
        ign = await conn.fetchrow(f"SELECT * FROM {q}.documents WHERE path = 'pt-br/C.md'")
        assert ign["ignored"] and ign["ignore_reason"] == "teste" and not ign["present"]
        assert await conn.fetchval(f"SELECT entity FROM {q}.enrich_reviews") == "doc:docs__wiki/pt-br/A.md"
        tables = {r[0] for r in await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = $1", schema)}
        assert not tables & {"doc_links", "doc_ignores", "api_impls", "doc_link_reviews"}
        assert {"relations", "documents", "enrich_reviews", "effective_relations"} <= tables
    finally:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await conn.close()
