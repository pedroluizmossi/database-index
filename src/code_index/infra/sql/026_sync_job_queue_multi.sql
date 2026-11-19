-- Fila de sync com vários pedidos: vários jobs `queued` (o worker leva o mais antigo), no máximo um `running`
-- (a trava do sync no Postgres já serializa a execução). O teto da fila (CODE_INDEX_SYNC_QUEUE_MAX) e a recusa de
-- repo já pedido num job da fila ficam no JobStore.create (com trava de transação). `canceled`: tirado da fila antes
-- de começar.
ALTER TABLE "{schema}".sync_jobs DROP CONSTRAINT IF EXISTS sync_jobs_status_check;
ALTER TABLE "{schema}".sync_jobs ADD CONSTRAINT sync_jobs_status_check
    CHECK (status IN ('queued', 'running', 'done', 'failed', 'canceled'));

DROP INDEX IF EXISTS "{schema}".sync_jobs_one_active_idx;
CREATE UNIQUE INDEX IF NOT EXISTS sync_jobs_one_running_idx ON "{schema}".sync_jobs ((true)) WHERE status = 'running';
