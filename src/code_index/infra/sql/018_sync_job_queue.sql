-- Fila durável de sync: a API enfileira (`queued`) e um worker reserva o job (`running`, com lease renovado
-- por heartbeat). Lease vencido (worker caiu, servidor reiniciou) devolve o job à fila até o limite de
-- tentativas; só então ele vira `failed`. Um job ativo (na fila ou rodando) por vez.
-- `allow_register` e `token_id` guardam o necessário para o worker aplicar a regra de admin e auditar sem
-- o Principal em memória (token_id é o id do token, nunca o valor).

ALTER TABLE "{schema}".sync_jobs DROP CONSTRAINT IF EXISTS sync_jobs_status_check;
ALTER TABLE "{schema}".sync_jobs ADD CONSTRAINT sync_jobs_status_check
    CHECK (status IN ('queued', 'running', 'done', 'failed'));

ALTER TABLE "{schema}".sync_jobs ADD COLUMN IF NOT EXISTS attempts       int NOT NULL DEFAULT 0;
ALTER TABLE "{schema}".sync_jobs ADD COLUMN IF NOT EXISTS lease_until    timestamptz;
ALTER TABLE "{schema}".sync_jobs ADD COLUMN IF NOT EXISTS worker         text;
ALTER TABLE "{schema}".sync_jobs ADD COLUMN IF NOT EXISTS run_started_at timestamptz;
ALTER TABLE "{schema}".sync_jobs ADD COLUMN IF NOT EXISTS allow_register boolean NOT NULL DEFAULT false;
ALTER TABLE "{schema}".sync_jobs ADD COLUMN IF NOT EXISTS token_id       uuid;

-- Jobs `running` de antes da fila (sem lease): só o mais recente segue ativo (o lease nulo conta como vencido,
-- então ele é retomado); os demais não cabem na regra "um ativo por vez".
UPDATE "{schema}".sync_jobs SET status = 'failed', finished_at = now(),
       error = coalesce(error, 'interrompido: substituído pela fila durável de sync')
WHERE status = 'running'
  AND id <> (SELECT id FROM "{schema}".sync_jobs WHERE status = 'running' ORDER BY started_at DESC LIMIT 1);

CREATE UNIQUE INDEX IF NOT EXISTS sync_jobs_one_active_idx ON "{schema}".sync_jobs ((true))
    WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS sync_jobs_queued_idx ON "{schema}".sync_jobs (started_at) WHERE status = 'queued';
