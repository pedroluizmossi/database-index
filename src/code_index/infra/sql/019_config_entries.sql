-- Configuração das fontes no banco: orgs do GitHub, repos avulsos e fontes de documentação.
-- `spec` guarda só os campos diferentes do padrão (mudar um padrão no código vale para quem não o alterou).
-- `seq` preserva a ordem de cadastro (a ordem das orgs pesa na seleção do lote).

CREATE TABLE IF NOT EXISTS "{schema}".config_entries (
    kind       text NOT NULL CHECK (kind IN ('org', 'repo', 'docs')),
    name       text NOT NULL,
    spec       jsonb NOT NULL DEFAULT '{}'::jsonb,
    seq        bigint GENERATED ALWAYS AS IDENTITY,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, name)
);
