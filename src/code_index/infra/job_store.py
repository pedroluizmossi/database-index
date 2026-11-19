"""Persistência dos jobs de sync (tabela sync_jobs): fila durável com reserva por lease.

Ciclo de um job: `queued` → `running` (reservado por um worker, com `lease_until` renovado por heartbeat)
→ `done`/`failed`; um job da fila pode ser `canceled` antes de começar. Lease vencido devolve o job à fila
(`expire_leases`) até o limite de tentativas. Vários na fila (até o teto do `create`), um rodando por vez: índice
único parcial `sync_jobs_one_running_idx`; o worker leva o mais antigo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from ..settings import Settings

_COLS = ("id::text, requested_by, params, status, started_at, finished_at, result, error, progress, "
         "attempts, lease_until, worker, run_started_at, allow_register, token_id::text, stop_requested_at, "
         "stop_requested_by, heartbeat_at")

ACTIVE = ("queued", "running")


class QueueFull(RuntimeError):
    """A fila de sync chegou ao teto de jobs esperando."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"a fila de sync está cheia ({limit} job(s) esperando): espere um terminar ou cancele um")


class AlreadyQueued(RuntimeError):
    """Repos pedidos já estão num job da fila."""

    def __init__(self, names: list[str], job_id: str) -> None:
        self.names = names
        self.job_id = job_id
        shown = ", ".join(names[:5]) + (f" e mais {len(names) - 5}" if len(names) > 5 else "")
        super().__init__(f"já na fila (job {job_id}): {shown}")


@dataclass(frozen=True)
class JobRecord:
    id: str
    requested_by: str
    params: dict[str, Any]
    status: str                      # queued | running | done | failed | canceled | stopped
    started_at: datetime             # quando foi pedido (entrou na fila)
    finished_at: datetime | None
    result: dict[str, Any] | None
    error: str | None
    progress: dict[str, Any] | None = None   # etapa e situação de cada repo (ver indexing/progress.py)
    attempts: int = 0                # reservas feitas por workers (a atual inclusive)
    lease_until: datetime | None = None
    worker: str | None = None        # quem detém o lease (host:pid:id)
    run_started_at: datetime | None = None   # início da tentativa atual (ou da última)
    allow_register: bool = False     # quem pediu podia registrar repos desconhecidos (admin)
    token_id: str | None = None      # id do token de quem pediu (para a auditoria; nunca o valor)
    stop_requested_at: datetime | None = None  # alguém pediu para parar (o worker confere a cada poucos segundos)
    stop_requested_by: str | None = None
    heartbeat_at: datetime | None = None       # última renovação do lease: o worker está vivo

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    def to_dict(self) -> dict[str, Any]:
        def iso(d: datetime | None) -> str | None:
            return d.isoformat(timespec="seconds") if d else None

        return {
            "id": self.id,
            "requested_by": self.requested_by,
            "params": self.params,
            "status": self.status,
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "result": self.result,
            "error": self.error,
            "progress": self.progress,
            "attempts": self.attempts,
            "lease_until": iso(self.lease_until),
            "worker": self.worker,
            "run_started_at": iso(self.run_started_at),
            "stop_requested_at": iso(self.stop_requested_at),
            "stop_requested_by": self.stop_requested_by,
            "heartbeat_at": iso(self.heartbeat_at),
        }


def _record(row: asyncpg.Record) -> JobRecord:
    def load(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    return JobRecord(
        id=row["id"],
        requested_by=row["requested_by"],
        params=load(row["params"]) or {},
        status=row["status"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        result=load(row["result"]),
        error=row["error"],
        progress=load(row["progress"]),
        attempts=row["attempts"],
        lease_until=row["lease_until"],
        worker=row["worker"],
        run_started_at=row["run_started_at"],
        allow_register=row["allow_register"],
        token_id=row["token_id"],
        stop_requested_at=row["stop_requested_at"],
        stop_requested_by=row["stop_requested_by"],
        heartbeat_at=row["heartbeat_at"],
    )


def _json(value: Any) -> str | None:
    return json.dumps(value, default=str) if value is not None else None


class JobStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._t = f'"{settings.pg_schema}".sync_jobs'

    # ------------------------------------------------------------ pedido

    async def create(self, requested_by: str, params: dict[str, Any], *, max_queued: int,
                     allow_register: bool = False, token_id: str | None = None) -> str:
        """Enfileira um job. QueueFull com `max_queued` jobs já esperando; AlreadyQueued se algum repo pedido já
        está num job da fila. Atômico entre processos (trava de transação: a contagem e a inserção andam juntas)."""
        repos = set(params.get("repos") or [])
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"{self._t}:queue")
            queued = await conn.fetch(f"SELECT id::text, params FROM {self._t} WHERE status = 'queued' ORDER BY started_at")
            if len(queued) >= max_queued:
                raise QueueFull(max_queued)
            for row in queued:
                p = json.loads(row["params"]) if isinstance(row["params"], str) else (row["params"] or {})
                dup = sorted(repos & set(p.get("repos") or []))
                if dup and not p.get("doc_pages") and not params.get("doc_pages"):
                    raise AlreadyQueued(dup, row["id"])
            row = await conn.fetchrow(
                f"""INSERT INTO {self._t} (requested_by, params, status, allow_register, token_id)
                    VALUES ($1, $2::jsonb, 'queued', $3, $4::uuid) RETURNING id::text""",
                requested_by, json.dumps(params, default=str), allow_register, token_id,
            )
        return str(row["id"])

    async def request_stop(self, job_id: str, *, by: str) -> bool:
        """Marca o pedido de parada de um job rodando. False se ele não está rodando."""
        try:
            row = await self._pool.fetchval(
                f"""UPDATE {self._t} SET stop_requested_at = now(), stop_requested_by = $2
                    WHERE id = $1::uuid AND status = 'running' RETURNING 1""", job_id, by)
        except asyncpg.DataError:
            return False
        return row is not None

    async def stop_requested(self, job_id: str) -> str | None:
        """Quem pediu a parada do job (None: ninguém)."""
        row = await self._pool.fetchrow(
            f"SELECT stop_requested_at, stop_requested_by FROM {self._t} WHERE id = $1::uuid", job_id)
        if row is None or row["stop_requested_at"] is None:
            return None
        return str(row["stop_requested_by"] or "alguém")

    async def requeue(self, job_id: str, *, worker: str, params: dict[str, Any], note: str) -> bool:
        """Devolve à fila (com os parâmetros do que falta) um job que este worker parou para a atualização do
        sistema. Não conta tentativa: não foi falha."""
        row = await self._pool.fetchval(
            f"""UPDATE {self._t} SET status = 'queued', worker = NULL, lease_until = NULL, error = $3,
                       params = $4::jsonb, attempts = greatest(attempts - 1, 0)
                WHERE id = $1::uuid AND worker = $2 AND status = 'running' RETURNING 1""",
            job_id, worker, note, json.dumps(params, default=str))
        return row is not None

    async def queued_count(self) -> int:
        return int(await self._pool.fetchval(f"SELECT count(*) FROM {self._t} WHERE status = 'queued'"))

    async def active(self) -> JobRecord | None:
        """O job rodando; sem ele, o primeiro da fila."""
        row = await self._pool.fetchrow(
            f"""SELECT {_COLS} FROM {self._t} WHERE status IN ('queued', 'running')
                ORDER BY status = 'running' DESC, started_at LIMIT 1"""
        )
        return _record(row) if row else None

    async def cancel(self, job_id: str, *, by: str) -> bool:
        """Tira da fila um job que ainda não começou. False se ele não está na fila (já rodando ou encerrado)."""
        try:
            row = await self._pool.fetchval(
                f"""UPDATE {self._t} SET status = 'canceled', finished_at = now(), error = $2
                    WHERE id = $1::uuid AND status = 'queued' RETURNING 1""",
                job_id, f"cancelado por {by} antes de começar",
            )
        except asyncpg.DataError:  # id não é uuid
            return False
        return row is not None

    # ------------------------------------------------------------ worker

    async def lease_next(self, *, worker: str, lease_seconds: float) -> JobRecord | None:
        """Reserva o próximo job da fila (o mais antigo). Seguro com vários workers: só um leva, e só com nenhum
        outro rodando (índice único `sync_jobs_one_running_idx`)."""
        try:
            row = await self._pool.fetchrow(
                f"""UPDATE {self._t} SET status = 'running', attempts = attempts + 1, worker = $1,
                       lease_until = now() + make_interval(secs => $2), run_started_at = now(), error = NULL,
                       heartbeat_at = now(), stop_requested_at = NULL, stop_requested_by = NULL
                WHERE id = (SELECT id FROM {self._t} WHERE status = 'queued'
                            ORDER BY started_at LIMIT 1 FOR UPDATE SKIP LOCKED)
                RETURNING {_COLS}""",
                worker, float(lease_seconds),
            )
        except asyncpg.UniqueViolationError:  # outro worker já roda um job
            return None
        return _record(row) if row else None

    async def renew(self, job_id: str, *, worker: str, lease_seconds: float) -> bool:
        """Heartbeat: estende o lease. False se o worker não detém mais o job (lease vencido e retomado)."""
        row = await self._pool.fetchval(
            f"""UPDATE {self._t} SET lease_until = now() + make_interval(secs => $3), heartbeat_at = now()
                WHERE id = $1::uuid AND worker = $2 AND status = 'running' RETURNING 1""",
            job_id, worker, float(lease_seconds),
        )
        return row is not None

    async def release(self, job_id: str, *, worker: str, count_attempt: bool = False,
                      error: str | None = None) -> bool:
        """Devolve à fila um job reservado por este worker (encerramento limpo, trava ocupada ou nova tentativa).
        `count_attempt=False` desfaz a contagem da reserva (a tentativa não chegou a valer)."""
        row = await self._pool.fetchval(
            f"""UPDATE {self._t} SET status = 'queued', worker = NULL, lease_until = NULL, error = $3,
                       attempts = CASE WHEN $4 THEN attempts ELSE greatest(attempts - 1, 0) END
                WHERE id = $1::uuid AND worker = $2 AND status = 'running' RETURNING 1""",
            job_id, worker, error, count_attempt,
        )
        return row is not None

    async def expire_leases(self, *, max_attempts: int) -> list[tuple[str, str]]:
        """Jobs `running` com lease vencido (ou sem lease: de antes da fila) voltam à fila; os que já usaram
        todas as tentativas viram `failed`. Devolve (id, novo status)."""
        rows = await self._pool.fetch(
            f"""UPDATE {self._t} SET
                    status = CASE WHEN attempts >= $1 THEN 'failed' ELSE 'queued' END,
                    finished_at = CASE WHEN attempts >= $1 THEN now() END,
                    error = CASE WHEN attempts >= $1
                                 THEN 'falhou após ' || attempts || ' tentativa(s): o worker parou durante a '
                                      || 'indexação (lease vencido)'
                                 ELSE 'retomado: o worker parou durante a indexação (lease vencido)' END,
                    worker = NULL, lease_until = NULL
                WHERE status = 'running' AND (lease_until IS NULL OR lease_until < now())
                RETURNING id::text, status""",
            max_attempts,
        )
        return [(r["id"], r["status"]) for r in rows]

    async def finish(self, job_id: str, *, status: str, result: dict[str, Any] | None, error: str | None,
                     worker: str | None = None) -> bool:
        """Encerra o job. Com `worker`, só se ele ainda detém o lease (False: o job foi retomado por outro)."""
        guard = " AND worker = $5 AND status = 'running'" if worker is not None else ""
        args: list[Any] = [job_id, status, _json(result), error]
        if worker is not None:
            args.append(worker)
        row = await self._pool.fetchval(
            f"""UPDATE {self._t} SET status = $2, finished_at = now(), result = $3::jsonb, error = $4,
                       worker = NULL, lease_until = NULL
                WHERE id = $1::uuid{guard} RETURNING 1""",
            *args,
        )
        return row is not None

    async def update_progress(self, job_id: str, progress: dict[str, Any], *, worker: str | None = None) -> None:
        """Retrato do andamento; só enquanto o job roda (não sobrescreve um job já encerrado)."""
        guard = " AND worker = $3" if worker is not None else ""
        args: list[Any] = [job_id, json.dumps(progress, default=str)]
        if worker is not None:
            args.append(worker)
        await self._pool.execute(
            f"UPDATE {self._t} SET progress = $2::jsonb WHERE id = $1::uuid AND status = 'running'{guard}", *args,
        )

    # ------------------------------------------------------------ leitura

    async def get(self, job_id: str) -> JobRecord | None:
        try:
            row = await self._pool.fetchrow(f"SELECT {_COLS} FROM {self._t} WHERE id = $1::uuid", job_id)
        except asyncpg.DataError:  # id não é uuid
            return None
        return _record(row) if row else None

    async def recent(self, limit: int = 50) -> list[JobRecord]:
        rows = await self._pool.fetch(
            f"SELECT {_COLS} FROM {self._t} ORDER BY started_at DESC LIMIT $1", min(max(limit, 1), 200)
        )
        return [_record(r) for r in rows]
