"""Vetores extras da busca: resumos e perguntas dos arquivos e cartões dos repos (domain/search_vectors).

Nada é embedado quando um resumo ou cartão muda: o estado (`vector_status`) mostra o que está pendente e quanto
custa, e a geração (`embed_vectors`) roda sob pedido, pela CLI ou pela API (em segundo plano no servidor). A
geração é idempotente: se parar no meio, o que faltou continua pendente e entra na próxima.

No fim de cada sync (`maintain_vectors`, sem custo): apaga os vetores sem origem e, só se
VECTORS_AUTO_EMBED_MAX_CHARS > 0, gera os pendentes até esse total de caracteres.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from ..domain.auth import Principal
from ..domain.search_vectors import KIND_LABELS, KINDS, VectorText, estimate_tokens, plan
from ..infra import llm
from ..infra.summary_store import SummaryStore
from ..infra.vector_store import VectorStore
from .base import ServiceBase

log = logging.getLogger(__name__)

_CHUNK = 512   # textos por gravação (cada um vira um lote de chamadas à Voyage e um INSERT)


class VectorsBusy(RuntimeError):
    """Outra geração de vetores está rodando (neste ou em outro processo)."""

    def __init__(self) -> None:
        super().__init__("já existe uma geração de vetores em andamento")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class VectorsMixin(ServiceBase):
    _vector_task: asyncio.Task[Any] | None = None
    _vector_run: dict[str, Any] | None = None   # última geração pedida pela API neste processo

    @property
    def _vectors(self) -> VectorStore:
        return VectorStore(self._chunks.pool, self.settings)

    def _estimate(self, items: list[VectorText]) -> dict[str, Any]:
        """Caracteres, tokens (≈ caracteres/4) e US$ estimados para embedar `items` (a cobrança real vem depois)."""
        chars = sum(len(i.text) for i in items)
        tokens = estimate_tokens(chars)
        price = llm.input_price(self.settings.embed_model)
        return {"chars": chars, "tokens": tokens, "cost_usd": round(tokens * price, 4) if price is not None else None}

    async def vector_status(self) -> dict[str, Any]:
        """Por tipo: textos de origem, vetores em dia, pendentes (custam ao gerar) e obsoletos (saem sem custo).
        Mais: custo estimado dos pendentes (total e por tipo), resumos desatualizados, vetores de outro modelo
        (a busca ignora até gerar de novo) e a cobertura dos resumos."""
        store = self._vectors
        model = self.settings.embed_model
        stored = await store.stored()
        p = plan(await store.sources(), stored, model)
        last = await store.last_embedded_at()
        task = self._vector_task
        labels = KIND_LABELS
        estimate = self._estimate(p.embed)
        by_kind: dict[str, dict[str, Any]] = {k: self._estimate([i for i in p.embed if i.key.kind == k]) for k in KINDS}
        coverage = await SummaryStore(self._chunks.pool, self.settings).stats()
        return {
            "model": model,
            "kinds": {k: {**c, "label": labels.get(k, k), "pending_estimate": by_kind.get(k)}
                      for k, c in p.counts_dict().items()},
            "pending": p.pending,
            "pending_chars": estimate["chars"],
            "pending_estimate": estimate,
            "obsolete": len(p.delete),
            "other_model": sum(1 for s in stored.values() if s.model != model),
            "stale_summaries": await store.stale_summaries(),
            "summary_coverage": coverage,
            "last_embedded_at": last.astimezone().isoformat(timespec="seconds") if last else None,
            "running": task is not None and not task.done(),
            "last_run": self._vector_run,
        }

    async def embed_vectors(self, *, dry_run: bool = False, limit: int | None = None,
                            kinds: list[str] | None = None, max_chars: int | None = None) -> dict[str, Any]:
        """Gera os vetores pendentes e apaga os obsoletos. `dry_run`: só conta (sem custo).
        `limit`: no máximo N textos nesta rodada; `kinds`: só estes tipos (question, summary, repo_card);
        `max_chars`: para antes de passar desse total de caracteres. O resto continua pendente.
        Custo: um embedding por texto."""
        unknown = sorted(set(kinds or ()) - set(KINDS))
        if unknown:
            raise ValueError(f"tipo de vetor desconhecido: {', '.join(unknown)} (use {', '.join(KINDS)})")
        store = self._vectors
        model = self.settings.embed_model
        p = plan(await store.sources(), await store.stored(), model)
        todo = [i for i in p.embed if not kinds or i.key.kind in kinds]
        if limit is not None:
            todo = todo[:max(limit, 0)]
        if max_chars is not None:
            within, total = [], 0
            for i in todo:
                if total + len(i.text) > max_chars:
                    break
                within.append(i)
                total += len(i.text)
            todo = within
        result: dict[str, Any] = {"pending": p.pending, "to_embed": len(todo),
                                  "chars": sum(len(i.text) for i in todo), "obsolete": len(p.delete),
                                  "estimate": self._estimate(todo), "dry_run": dry_run}
        if dry_run:
            return result
        conn = await store.try_lock()
        if conn is None:
            raise VectorsBusy()
        started = time.monotonic()
        try:
            with llm.metered() as meter:
                deleted = await store.delete(p.delete)
                done = 0
                for start in range(0, len(todo), _CHUNK):
                    part = todo[start:start + _CHUNK]
                    vectors = await llm.embed_documents(self.settings, [i.text for i in part])
                    await store.upsert(part, vectors, model)
                    done += len(part)
                usage = meter.snapshot()
            if done:
                await store.ensure_index(model)   # HNSW parcial do modelo (idempotente)
        finally:
            await store.unlock(conn)
        result.update(embedded=done, deleted=deleted, seconds=round(time.monotonic() - started, 1),
                      embed_tokens=usage.embed_tokens, cost_usd=usage.cost_usd)
        log.info("vetores extras: %d gerados, %d apagados (%d tokens)", done, deleted, usage.embed_tokens)
        return result

    async def maintain_vectors(self) -> dict[str, Any]:
        """Fim do sync: apaga os vetores sem origem (sem custo) e, com VECTORS_AUTO_EMBED_MAX_CHARS > 0, gera os
        pendentes até esse total. Outra geração rodando: não espera nem falha (fica para o próximo sync)."""
        store = self._vectors
        out: dict[str, Any] = {"deleted": 0, "embedded": 0}
        budget = self.settings.vectors_auto_embed_max_chars
        if budget > 0:
            try:
                res = await self.embed_vectors(max_chars=budget)
            except VectorsBusy:
                return {**out, "busy": True}
            return {"deleted": res["deleted"], "embedded": res["embedded"], "cost_usd": res["cost_usd"]}
        p = plan(await store.sources(), await store.stored(), self.settings.embed_model)
        if not p.delete:
            return out
        conn = await store.try_lock()
        if conn is None:
            return {**out, "busy": True}
        try:
            out["deleted"] = await store.delete(p.delete)
        finally:
            await store.unlock(conn)
        return out

    async def start_vector_embedding(self, principal: Principal | None, *, kinds: list[str] | None = None,
                                     limit: int | None = None) -> dict[str, Any]:
        """Dispara a geração em segundo plano neste processo e devolve o estado. Uma por vez: VectorsBusy se
        já houver uma rodando (aqui ou em outro processo, pela trava no banco)."""
        if self._vector_task is not None and not self._vector_task.done():
            raise VectorsBusy()
        unknown = sorted(set(kinds or ()) - set(KINDS))
        if unknown:
            raise ValueError(f"tipo de vetor desconhecido: {', '.join(unknown)} (use {', '.join(KINDS)})")
        actor = principal.actor if principal else "operador local"
        run: dict[str, Any] = {"requested_by": actor, "started_at": _now(), "finished_at": None,
                               "status": "running", "result": None, "error": None}
        self._vector_run = run

        async def go() -> None:
            try:
                run["result"] = await self.embed_vectors(kinds=kinds, limit=limit)
                run["status"] = "done"
            except Exception as e:  # noqa: BLE001 - o erro vai para o estado, não derruba o servidor
                log.exception("geração de vetores falhou")
                run["status"], run["error"] = "failed", str(e)[:500]
            finally:
                run["finished_at"] = _now()
            await self._audit.record(actor=actor, token_id=principal.token_id if principal else None,
                                     action="vectors:embed",
                                     params={"status": run["status"], "kinds": kinds, "limit": limit})

        task = asyncio.create_task(go())
        self._vector_task = task
        self._job_tasks.add(task)
        task.add_done_callback(self._job_tasks.discard)
        return await self.vector_status()
