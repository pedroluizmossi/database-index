-- Pessoas (quem já fez login) e suas preferências.
-- `users` nasce/atualiza no login; as tabelas de token continuam ligadas pelo e-mail (owner_email).
-- `user_settings` guarda só o que a pessoa mudou: o registro das configs e os padrões ficam no código
-- (domain/preferences.py). Apagar a linha = voltar ao padrão.

CREATE TABLE IF NOT EXISTS "{schema}".users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         text NOT NULL UNIQUE,          -- sempre em minúsculas
    display_name  text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz
);

CREATE TABLE IF NOT EXISTS "{schema}".user_settings (
    user_id    uuid NOT NULL REFERENCES "{schema}".users (id) ON DELETE CASCADE,
    key        text NOT NULL,
    value      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, key)
);
