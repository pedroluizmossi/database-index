-- Autenticação e auditoria da API HTTP / MCP.
-- Tokens nunca são guardados em claro: só o HMAC-SHA256(token, CODE_INDEX_AUTH_SECRET).

CREATE TABLE IF NOT EXISTS "{schema}".api_tokens (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind          text NOT NULL CHECK (kind IN ('agent', 'session')),
    name          text NOT NULL,                 -- rótulo legível (ex.: "claude-code pedro")
    owner_email   text NOT NULL,                 -- pessoa responsável (quem emitiu / quem logou)
    token_hash    bytea NOT NULL UNIQUE,
    token_prefix  text NOT NULL,                 -- primeiros caracteres, só para identificação
    scopes        text[] NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz,                   -- NULL = sem expiração (desencorajado)
    last_used_at  timestamptz,
    revoked_at    timestamptz
);
CREATE INDEX IF NOT EXISTS api_tokens_owner_idx ON "{schema}".api_tokens (owner_email);

CREATE TABLE IF NOT EXISTS "{schema}".audit_log (
    id         bigserial PRIMARY KEY,
    at         timestamptz NOT NULL DEFAULT now(),
    actor      text NOT NULL,                    -- e-mail (+ nome do token, se agent) ou "anonymous"
    token_id   uuid,
    action     text NOT NULL,                    -- ex.: "POST /api/v1/sync", "mcp:search_code"
    params     jsonb,                            -- parâmetros (nunca tokens nem conteúdo retornado)
    status     integer,                          -- HTTP status / 0 = ok em tool MCP
    client_ip  text
);
CREATE INDEX IF NOT EXISTS audit_log_at_idx ON "{schema}".audit_log (at DESC);
