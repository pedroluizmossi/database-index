"""Persistência dos jobs de sync (tabela sync_jobs)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from ..settings import Settings

_COLS = "id::text, requested_by, params, status, started_at, finished_at, result, error, progress"


@dataclass(frozen=True)
class JobRecord:
    id: str
    requested_by: str
    params: dict[str, Any]
    status: str                      # running | done | failed
    started_at: datetime
    finished_at: datetime | None
    result: dict[str, Any] | None
    error: str | None
    progress: dict[str, Any] | None = None   # etapa e situação de cada repo (ver indexing/progress.py)

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
    )


class JobStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._t = f'"{settings.pg_schema}".sync_jobs'

    async def create(self, requested_by: str, params: dict[str, Any]) -> str:
        row = await self._pool.fetchrow(
            f"INSERT INTO {self._t} (requested_by, params, status) VALUES ($1, $2::jsonb, 'running') RETURNING id::text",
            requested_by, json.dumps(params, default=str),
        )
        return str(row["id"])

    async def finish(self, job_id: str, *, status: str, result: dict[str, Any] | None, error: str | None) -> None:
        await self._pool.execute(
            f"""UPDATE {self._t} SET status = $2, finished_at = now(), result = $3::jsonb, error = $4
                WHERE id = $1::uuid""",
            job_id, status, json.dumps(result, default=str) if result is not None else None, error,
        )

    async def update_progress(self, job_id: str, progress: dict[str, Any]) -> None:
        """Retrato do andamento; só enquanto o job roda (não sobrescreve um job já encerrado)."""
        await self._pool.execute(
            f"UPDATE {self._t} SET progress = $2::jsonb WHERE id = $1::uuid AND status = 'running'",
            job_id, json.dumps(progress, default=str),
        )

    async def get(self, job_id: str) -> JobRecord | None:
        try:
            row = await self._pool.fetchrow(f"SELECT {_COLS} FROM {self._t} WHERE id = $1::uuid", job_id)
        except asyncpg.DataError:  # id não é uuid
            return None
        return _record(row) if row else None

    async def running(self) -> JobRecord | None:
        row = await self._pool.fetchrow(
            f"SELECT {_COLS} FROM {self._t} WHERE status = 'running' ORDER BY started_at LIMIT 1"
        )
        return _record(row) if row else None

    async def recent(self, limit: int = 50) -> list[JobRecord]:
        rows = await self._pool.fetch(
            f"SELECT {_COLS} FROM {self._t} ORDER BY started_at DESC LIMIT $1", min(max(limit, 1), 200)
        )
        return [_record(r) for r in rows]

    async def fail_interrupted(self, reason: str) -> int:
        """Jobs que ainda constam como running (o processo que os executava morreu)."""
        res = await self._pool.execute(
            f"""UPDATE {self._t} SET status = 'failed', finished_at = now(), error = $1
                WHERE status = 'running'""",
            reason,
        )
        return int(res.split()[-1])
