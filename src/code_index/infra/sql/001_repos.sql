-- Tabela de estado dos repositórios. Idêntica ao _DDL antigo (state.py) + coluna language.
-- {schema} é substituído pelo pg_schema do Settings (ver infra/db.py).
-- "Indexado" = revision não nula: é a chave de memo que o pipeline usa para pular repos sem mudanças.

CREATE TABLE IF NOT EXISTS "{schema}".repos (
    name              text PRIMARY KEY,
    org               text,             -- NULL = repo avulso do repos.toml
    url               text,
    path              text,
    default_branch    text,
    pushed_at         timestamptz,      -- último push segundo a API do GitHub
    size_kb           bigint,
    ignored           boolean NOT NULL DEFAULT false,
    indexed_commit    text,
    revision          text,             -- chave de memo do pipeline (commit ou commit#nonce)
    indexed_pushed_at timestamptz,
    indexed_at        timestamptz,
    error             text,
    discovered_at     timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS repos_org_idx ON "{schema}".repos (org);
ALTER TABLE "{schema}".repos ADD COLUMN IF NOT EXISTS language text;
