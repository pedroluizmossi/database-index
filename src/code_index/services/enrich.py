"""Enriquecimento offline: fatos e cartão do repo, operação → handler e fila de agents."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any


from ..domain.models import RepoState
from ..domain.api_impl import parse_operation_ref
from ..domain.doc_task import DOC_BATCH, page_excerpt, page_folder, render_doc_task, search_text, validate_doc_links
from ..domain.repo_card import material_hash, parse_payload, related_candidates, render_card_task, validate_card
from ..indexing import enrich as enrich_collect
from ..indexing.orchestrator import configured_repos, repo_for_row
from ..retrieval.redaction import mask_output

from .base import ServiceBase, _facts_key, _iso_local

log = logging.getLogger(__name__)


class EnrichMixin(ServiceBase):
    def _card_targets(self, rows: dict[str, RepoState]) -> dict[str, tuple[RepoState, Path]]:
        """Repos de código indexados, não ignorados, com clone local."""
        orgs = self._config.load_orgs()
        manual = configured_repos(self._config)
        out: dict[str, tuple[RepoState, Path]] = {}
        for row in rows.values():
            if row.kind != "code" or row.ignored or not row.indexed:
                continue
            root = repo_for_row(row, orgs, manual).local_path(self.settings.repos_dir)
            if root.is_dir():
                out[row.name] = (row, root)
        return out

    async def _collect_card_material(self, row: RepoState, root: Path) -> None:
        symbols = await self._symbols.repo_overview(row.name)
        material = await asyncio.to_thread(enrich_collect.collect_material, root, language=row.language, symbols=symbols)
        await self._enrich.save_material(row.name, revision=_facts_key(row), commit=row.indexed_commit,
                                         input_hash=material_hash(material), material=material)

    async def enrich_facts(self, repos: Sequence[str] | None = None, *, force: bool = False,
                           rebuild: bool = True) -> dict[str, int]:
        """Coleta os fatos/material (sem LLM, sem custo) dos repos cuja revisão mudou desde a última coleta.
        Com `rebuild`, recalcula as relações que dependem dos fatos (SDL → API, operação → handler)."""
        wanted = await self._resolve_repos(repos)
        targets = self._card_targets(await self._repos.all_rows())
        done = await self._enrich.revisions()
        updated = 0
        for name, (row, root) in sorted(targets.items()):
            if wanted is not None and name not in wanted:
                continue
            if not force and name in done and done[name] == _facts_key(row):
                continue
            await self._collect_card_material(row, root)
            updated += 1
        if updated and rebuild:
            await self.rebuild_relations(only=("handler", "openapi-sdl"))
        return {"repos": len(targets) if wanted is None else len(wanted), "updated": updated}

    async def find_api_impl(self, operation: str, *, service: str | None = None,
                            domain: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Onde uma operação da API (SDL) é implementada: repo, classe handler, arquivo e linha.
        Aceita 'op', 'servico.op' ou o título da página da API ('POST /email/queries/op')."""
        parsed_service, op = parse_operation_ref(operation)
        return await self._relations.find_impls(op, service=service or parsed_service, domain=domain,
                                                limit=max(1, min(limit, 100)))

    async def enrich_next(self, *, agent: str = "", repo: str | None = None,
                          kind: str = "card", scope: str = "todas") -> dict[str, Any] | None:
        """Reserva a próxima tarefa e devolve o texto para o agent. None = nada pendente.
        kind "card": cartão de um repo; "doc_links": lote de páginas da wiki → repos (escopo: todas,
        sem-vinculo ou varios), com veredito sobre os vínculos automáticos atuais."""
        if kind == "doc_links":
            await self._enrich.expire_leases()
            return await self._next_doc_links(agent=agent.strip()[:100], doc_repo=repo, scope=scope)
        if kind != "card":
            raise ValueError("kind inválido: use card ou doc_links")
        await self._enrich.expire_leases()
        await self.enrich_facts()
        wanted = (await self._resolve_repos([repo]) or [repo])[0] if repo else None
        task = await self._enrich.lease_next(agent=agent.strip()[:100], lease_minutes=self.settings.enrich_lease_minutes,
                                             repo=wanted)
        if task is None:
            return None
        card = await self._enrich.get(task.repo)
        assert card is not None
        targets = self._card_targets(await self._repos.all_rows())
        candidates = related_candidates(task.repo, targets.keys(), await self._enrich.all_facts())
        await self._enrich.set_candidates(task.id, candidates)
        remaining = await self._enrich.pending_count()
        text = render_card_task(task_id=task.id, repo=task.repo, commit=card["commit"], remaining=remaining,
                                material=card["material"], candidates=candidates)
        return {"id": task.id, "kind": task.kind, "repo": task.repo, "commit": card["commit"],
                "lease_until": _iso_local(task.lease_until), "remaining": remaining,
                "candidates": candidates, "material": card["material"], "text": text}

    def _read_page_excerpt(self, doc_repo: str, path: str) -> str:
        root = self._local_path(doc_repo)
        if not enrich_collect.path_exists(root, path):
            return ""
        try:
            text = (root / path).read_text(encoding="utf-8", errors="replace")[:200_000]
        except OSError:
            return ""
        return page_excerpt(mask_output(text, "markdown"))

    async def _next_doc_links(self, *, agent: str, doc_repo: str | None, scope: str) -> dict[str, Any] | None:
        pending = await self._enrich.pending_pages(doc_repo, scope=scope)
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for p in pending:
            groups.setdefault((p["repo"], page_folder(p["path"])), []).append(p)
        for (repo, folder), pages in groups.items():
            batch = pages[:DOC_BATCH]
            current = await self._relations.links_for_docs([(repo, p["path"]) for p in batch])
            for p in batch:
                p["links"] = {r: sorted(o) for r, o in current.get((repo, p["path"]), {}).items()}
            candidates: dict[str, dict[str, Any]] = {}
            for p in batch:
                for hit in await self._enrich.find(search_text(p["title"], p["path"]), limit=6):
                    candidates.setdefault(hit["repo"], {"repo": hit["repo"], "summary": hit["summary"], "tags": hit["tags"]})
            cands = list(candidates.values())[:15]
            task = await self._enrich.lease_doc_batch(agent=agent, lease_minutes=self.settings.enrich_lease_minutes,
                                                      doc_repo=repo, folder=folder, pages=batch, candidates=cands)
            if task is None:  # outra reserva ativa nesta pasta
                continue
            items = [{"path": p["path"], "title": p["title"], "tags": " ".join(p["tags"]), "url": p["url"],
                      "links": p["links"],
                      "excerpt": await asyncio.to_thread(self._read_page_excerpt, repo, p["path"])} for p in batch]
            remaining = len(pending) - len(batch)
            text = render_doc_task(task_id=task.id, doc_repo=repo, folder=folder, remaining_pages=remaining,
                                   pages=items, candidates=[{**c, "summary": mask_output(c["summary"] or "", "markdown")}
                                                            for c in cands])
            return {"id": task.id, "kind": task.kind, "repo": repo, "folder": folder,
                    "lease_until": _iso_local(task.lease_until), "remaining": remaining,
                    "pages": [p["path"] for p in batch], "candidates": [c["repo"] for c in cands], "text": text}
        return None

    async def _submit_doc_links(self, task: Any, payload: str, agent: str) -> dict[str, Any]:
        task_pages = (task.payload or {}).get("pages", [])
        pages = [p["path"] for p in task_pages]
        existing = {p["path"]: p.get("links", {}) for p in task_pages}
        try:
            raw = parse_payload(payload)
            known = self._code_repo_names(await self._repos.all_rows())
            results, errors = validate_doc_links(raw, pages=pages, known_repos=known, existing=existing)
        except ValueError as e:
            results, errors = None, [str(e)]
        if results is None:
            msg = "entrega recusada:\n" + "\n".join(f"- {e}" for e in errors)
            await self._enrich.reject(task.id, msg)
            raise ValueError(msg)
        agent = agent or task.agent
        created = 0
        async with self._chunks.pool.acquire() as conn:
            async with conn.transaction():
                for page in results:
                    for v in page["verdicts"]:
                        if v["verdict"] == "remover":
                            created += await self._relations.suggest(
                                conn, doc_repo=task.repo, path=page["path"], repo=v["repo"], action="remove",
                                reason=v["why"], confidence=v["confidence"], basis=None, agent=agent)
                    for link in page["repos"]:
                        created += await self._relations.suggest(
                            conn, doc_repo=task.repo, path=page["path"], repo=link["repo"], action="add",
                            reason=link["why"], confidence=link["confidence"], basis=link["basis"], agent=agent)
                await self._enrich.complete_doc_task(conn, task, results, agent)
        remaining = len(await self._enrich.pending_pages())
        removals = sum(v["verdict"] == "remover" for r in results for v in r["verdicts"])
        return {"kind": "doc_links", "repo": task.repo, "pages": len(results), "suggested": created,
                "removals": removals, "remaining": remaining}

    async def enrich_submit(self, task_id: str, payload: str, *, agent: str = "") -> dict[str, Any]:
        """Valida e grava o cartão entregue. Entrega inválida → ValueError com os erros (a tarefa continua)."""
        task = await self._enrich.task(task_id.strip())
        if task is None:
            raise ValueError(f"tarefa não encontrada: {task_id}")
        if task.status == "done":
            raise ValueError("tarefa já entregue")
        if task.status == "released":
            raise ValueError("tarefa devolvida à fila; pegue outra com `enrich next`")
        if task.kind == "doc_links":
            return await self._submit_doc_links(task, payload, agent.strip()[:100])
        root = self._local_path(task.repo)
        try:
            raw = parse_payload(payload)
            known = self._code_repo_names(await self._repos.all_rows())
            card, errors = validate_card(raw, candidates=task.candidates, known_repos=known,
                                         path_exists=lambda p: enrich_collect.path_exists(root, p))
        except ValueError as e:
            card, errors = None, [str(e)]
        if card is None:
            msg = "entrega recusada:\n" + "\n".join(f"- {e}" for e in errors)
            await self._enrich.reject(task.id, msg)
            raise ValueError(msg)
        if not await self._enrich.complete_card(task, card, agent.strip()[:100] or task.agent):
            raise ValueError("outro agent reservou este repo depois que sua reserva expirou; pegue outra tarefa")
        await self.rebuild_relations(only=("card",))  # repos relacionados do cartão
        return {"kind": "card", "repo": task.repo, "card": card, "remaining": await self._enrich.pending_count()}

    async def enrich_release(self, task_id: str) -> bool:
        return await self._enrich.release(task_id.strip())

    async def enrich_status(self) -> dict[str, Any]:
        await self._enrich.expire_leases()
        st = await self._enrich.status()
        st["pending"] = await self._enrich.pending_count()
        st["impls"] = {"operations": await self._enrich.sdl_operation_count(),
                       "matched": await self._relations.count("implemented_by")}
        st["docs"] = {**await self._enrich.doc_queue_stats(), **await self._relations.suggestion_stats()}
        st["leased"] = [x | {"lease_until": _iso_local(x["lease_until"])} for x in st["leased"]]
        return st

    async def doc_suggestions(self, *, status: str = "suggested", source: str | None = None,
                              confidence: str | None = None, action: str | None = None,
                              limit: int = 200) -> list[dict[str, Any]]:
        """Vínculos página → repo sugeridos por LLM (`enrich next --kind doc_links`), para revisão."""
        if status not in ("suggested", "active", "rejected"):
            raise ValueError("status inválido: suggested, active ou rejected")
        doc_repo = self._doc_source(source).repo_name if source and not source.startswith("docs__") else source
        if action not in (None, "add", "remove"):
            raise ValueError("ação inválida: add ou remove")
        return await self._relations.suggestions(status=status, doc_repo=doc_repo, confidence=confidence,
                                                 action=action, limit=limit)

    async def review_doc_suggestion(self, doc_repo: str, doc_path: str, repo: str, *, approve: bool) -> bool:
        """Aprova ou rejeita uma sugestão. add aprovado: o vínculo passa a valer. remove aprovado: o vínculo
        automático deixa de valer. Rejeitada não volta a ser sugerida (e rejeitar um remove aprovado o desfaz)."""
        return await self._relations.set_suggestion_status(doc_repo, doc_path, repo,
                                                           "active" if approve else "rejected")

    async def approve_doc_suggestions(self, *, confidence: str, source: str | None = None,
                                      action: str | None = None) -> int:
        """Aprova em lote as sugestões pendentes de uma confiança (ex.: todas as 'alta'), opcionalmente
        só de uma ação (add = novos vínculos, remove = remoções de vínculos automáticos)."""
        n = 0
        for sug in await self.doc_suggestions(confidence=confidence, source=source, action=action, limit=2000):
            n += await self.review_doc_suggestion(sug["doc_repo"], sug["doc_path"], sug["repo"], approve=True)
        return n

    async def repo_card(self, repo: str) -> dict[str, Any] | None:
        """Fatos e cartão de um repo (None se ainda não coletado)."""
        name = (await self._resolve_repos([repo]) or [repo])[0]
        row = await self._enrich.get(name)
        if row is None:
            return None
        card = row["card"]
        if card and card.get("summary"):
            card = {**card, "summary": mask_output(card["summary"], "markdown")}
        return {"repo": name, "commit": row["commit"], "facts": row["material"].get("facts", {}),
                "impls": await self._relations.impls_for_repo(name),
                "related": await self._relations.related_repos(name),
                "card": card, "stale": row["card"] is not None and row["card_input_hash"] != row["input_hash"],
                "card_agent": row["card_agent"] or None, "card_at": _iso_local(row["card_at"]),
                "facts_at": _iso_local(row["facts_at"])}

    # ------------------------------------------------------------ jobs de sync

