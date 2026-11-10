"""Persistência dos jobs de sync (tabela sync_jobs): fila durável com reserva por lease.

Ciclo de um job: `queued` → `running` (reservado por um worker, com `lease_until` renovado por heartbeat)
→ `done`/`failed`. Lease vencido devolve o job à fila (`expire_leases`) até o limite de tentativas.
Um job ativo (`queued` ou `running`) por vez: garantido pelo índice único parcial `sync_jobs_one_active_idx`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from ..settings import Settings

_COLS = ("id::text, requested_by, params, status, started_at, finished_at, result, error, progress, "
         "attempts, lease_until, worker, run_started_at, allow_register, token_id::text")

ACTIVE = ("queued", "running")


class ActiveJobExists(RuntimeError):
    """Já existe um job na fila ou rodando (um sync por vez)."""

    def __init__(self, job_id: str | None, status: str | None) -> None:
        self.job_id = job_id
        self.status = status
        where = "na fila" if status == "queued" else "em andamento"
        super().__init__(f"já existe um sync {where}: job {job_id}")


@dataclass(frozen=True)
class JobRecord:
    id: str
    requested_by: str
    params: dict[str, Any]
    status: str                      # queued | running | done | failed
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
    )


def _json(value: Any) -> str | None:
    return json.dumps(value, default=str) if value is not None else None


class JobStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._t = f'"{settings.pg_schema}".sync_jobs'

    # ------------------------------------------------------------ pedido

    async def create(self, requested_by: str, params: dict[str, Any], *, allow_register: bool = False,
                     token_id: str | None = None) -> str:
        """Enfileira um job. ActiveJobExists se já houver um na fila ou rodando (atômico, entre processos)."""
        try:
            row = await self._pool.fetchrow(
                f"""INSERT INTO {self._t} (requested_by, params, status, allow_register, token_id)
                    VALUES ($1, $2::jsonb, 'queued', $3, $4::uuid) RETURNING id::text""",
                requested_by, json.dumps(params, default=str), allow_register, token_id,
            )
        except asyncpg.UniqueViolationError:
            active = await self.active()
            raise ActiveJobExists(active.id if active else None, active.status if active else None) from None
        return str(row["id"])

    async def active(self) -> JobRecord | None:
        """O job na fila ou rodando, se houver."""
        row = await self._pool.fetchrow(
            f"SELECT {_COLS} FROM {self._t} WHERE status IN ('queued', 'running') ORDER BY started_at LIMIT 1"
        )
        return _record(row) if row else None

    # ------------------------------------------------------------ worker

    async def lease_next(self, *, worker: str, lease_seconds: float) -> JobRecord | None:
        """Reserva o próximo job da fila (o mais antigo). Seguro com vários workers: só um leva."""
        row = await self._pool.fetchrow(
            f"""UPDATE {self._t} SET status = 'running', attempts = attempts + 1, worker = $1,
                       lease_until = now() + make_interval(secs => $2), run_started_at = now(), error = NULL
                WHERE id = (SELECT id FROM {self._t} WHERE status = 'queued'
                            ORDER BY started_at LIMIT 1 FOR UPDATE SKIP LOCKED)
                RETURNING {_COLS}""",
            worker, float(lease_seconds),
        )
        return _record(row) if row else None

    async def renew(self, job_id: str, *, worker: str, lease_seconds: float) -> bool:
        """Heartbeat: estende o lease. False se o worker não detém mais o job (lease vencido e retomado)."""
        row = await self._pool.fetchval(
            f"""UPDATE {self._t} SET lease_until = now() + make_interval(secs => $3)
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
