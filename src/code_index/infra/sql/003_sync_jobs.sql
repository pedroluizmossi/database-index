-- Histórico dos jobs de sync da API. Sobrevive a reinícios do servidor.
-- Jobs que estavam "running" quando o servidor caiu são marcados como "failed" na inicialização.

CREATE TABLE IF NOT EXISTS "{schema}".sync_jobs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    requested_by  text NOT NULL,
    params        jsonb NOT NULL DEFAULT '{}'::jsonb,
    status        text NOT NULL CHECK (status IN ('running', 'done', 'failed')),
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    result        jsonb,
    error         text
);
CREATE INDEX IF NOT EXISTS sync_jobs_started_idx ON "{schema}".sync_jobs (started_at DESC);
CREATE INDEX IF NOT EXISTS sync_jobs_running_idx ON "{schema}".sync_jobs (status) WHERE status = 'running';
