-- Rate limit compartilhado entre réplicas da API (antes era em memória, por processo). Janela fixa: uma linha
-- por (balde, chave, início da janela), incrementada com INSERT ... ON CONFLICT DO UPDATE (uma ida ao banco por
-- requisição; ver infra/rate_limit_store.py). `key` é o id do token, o subject da sessão ou o IP: nunca o
-- valor de um token. `expires_at` = fim da janela; as vencidas são apagadas de vez em quando pela própria API.
-- UNLOGGED: contagem efêmera, não vale o WAL; num crash ou failover a tabela volta vazia (os limites recomeçam).
CREATE UNLOGGED TABLE IF NOT EXISTS "{schema}".rate_limits (
    bucket       text        NOT NULL,
    key          text        NOT NULL,
    window_start timestamptz NOT NULL,
    expires_at   timestamptz NOT NULL,
    hits         integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (bucket, key, window_start)
);
CREATE INDEX IF NOT EXISTS rate_limits_expires_idx ON "{schema}".rate_limits (expires_at);
