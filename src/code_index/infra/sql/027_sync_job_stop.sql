-- Parar um job rodando sem perder o que foi feito (botão "Parar" e atualização do sistema) e sinal de vida do worker.
-- `stop_requested_*`: pedido de parada; o worker confere a cada poucos segundos. `stopped`: parado a pedido (os repos
-- que terminaram ficam indexados). `heartbeat_at`: última renovação do lease (o worker está vivo).
ALTER TABLE "{schema}".sync_jobs ADD COLUMN IF NOT EXISTS stop_requested_at timestamptz;
ALTER TABLE "{schema}".sync_jobs ADD COLUMN IF NOT EXISTS stop_requested_by text;
ALTER TABLE "{schema}".sync_jobs ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz;

ALTER TABLE "{schema}".sync_jobs DROP CONSTRAINT IF EXISTS sync_jobs_status_check;
ALTER TABLE "{schema}".sync_jobs ADD CONSTRAINT sync_jobs_status_check
    CHECK (status IN ('queued', 'running', 'done', 'failed', 'canceled', 'stopped'));
