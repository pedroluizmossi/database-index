-- OAuth do MCP: o code-index emite tokens a clientes MCP após login (Google/Microsoft) e consentimento.
-- Nada em claro: códigos e refresh tokens só como HMAC-SHA256 (CODE_INDEX_AUTH_SECRET); o client_secret
-- dos clientes confidenciais fica cifrado com chave derivada do mesmo segredo.

ALTER TABLE "{schema}".api_tokens DROP CONSTRAINT IF EXISTS api_tokens_kind_check;
ALTER TABLE "{schema}".api_tokens
    ADD CONSTRAINT api_tokens_kind_check CHECK (kind IN ('agent', 'session', 'oauth'));
ALTER TABLE "{schema}".api_tokens ADD COLUMN IF NOT EXISTS oauth_client_id text;

-- Clientes registrados (RFC 7591, registro dinâmico)
CREATE TABLE IF NOT EXISTS "{schema}".oauth_clients (
    client_id     text PRIMARY KEY,
    client_name   text,
    metadata      jsonb NOT NULL,                -- registro do cliente, sem o segredo
    secret_enc    text,                          -- client_secret cifrado; NULL = cliente público (PKCE)
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- Códigos de autorização: uso único, vida curta
CREATE TABLE IF NOT EXISTS "{schema}".oauth_codes (
    code_hash              bytea PRIMARY KEY,
    client_id              text NOT NULL REFERENCES "{schema}".oauth_clients (client_id) ON DELETE CASCADE,
    owner_email            text NOT NULL,
    scopes                 text[] NOT NULL,
    code_challenge         text NOT NULL,
    redirect_uri           text NOT NULL,
    redirect_uri_explicit  boolean NOT NULL,
    resource               text,
    expires_at             timestamptz NOT NULL
);

-- Refresh tokens: rotacionados a cada uso. Reuso de um já usado revoga a família inteira.
CREATE TABLE IF NOT EXISTS "{schema}".oauth_refresh_tokens (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash       bytea NOT NULL UNIQUE,
    family_id        uuid NOT NULL,              -- mesma autorização ao longo das rotações
    client_id        text NOT NULL REFERENCES "{schema}".oauth_clients (client_id) ON DELETE CASCADE,
    owner_email      text NOT NULL,
    scopes           text[] NOT NULL,
    resource         text,
    access_token_id  uuid,                       -- api_tokens.id emitido junto
    created_at       timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz NOT NULL,
    revoked_at       timestamptz
);
CREATE INDEX IF NOT EXISTS oauth_refresh_family_idx ON "{schema}".oauth_refresh_tokens (family_id);
CREATE INDEX IF NOT EXISTS oauth_refresh_access_idx ON "{schema}".oauth_refresh_tokens (access_token_id);
