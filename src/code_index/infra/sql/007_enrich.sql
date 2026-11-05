-- Enriquecimento offline (ver ENRIQUECIMENTO.md).
-- repo_cards: fatos determinísticos (parsers) + cartão escrito por um agent. Não passa pelo CocoIndex.
--   material      = o que o agent recebe (fatos, README, símbolos, arquivos sugeridos)
--   input_hash    = hash do material atual; card_input_hash = hash do material em que o cartão se baseou.
--   Cartão desatualizado = card_input_hash <> input_hash.
CREATE TABLE IF NOT EXISTS "{schema}".repo_cards (
    repo            text PRIMARY KEY,
    revision        text,
    commit          text,
    input_hash      text NOT NULL,
    material        jsonb NOT NULL,
    facts_at        timestamptz NOT NULL DEFAULT now(),
    card            jsonb,
    card_input_hash text,
    card_agent      text NOT NULL DEFAULT '',
    card_at         timestamptz
);

-- Tarefas entregues a agents (`code-index enrich next`). Uma reserva ativa por (kind, repo).
CREATE TABLE IF NOT EXISTS "{schema}".enrich_tasks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind        text NOT NULL CHECK (kind IN ('card')),
    repo        text NOT NULL,
    input_hash  text NOT NULL,
    candidates  jsonb NOT NULL DEFAULT '[]'::jsonb,
    agent       text NOT NULL DEFAULT '',
    status      text NOT NULL CHECK (status IN ('leased', 'done', 'released', 'expired')),
    lease_until timestamptz NOT NULL,
    rejections  integer NOT NULL DEFAULT 0,
    last_error  text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS enrich_tasks_one_lease ON "{schema}".enrich_tasks (kind, repo) WHERE status = 'leased';
CREATE INDEX IF NOT EXISTS enrich_tasks_created_idx ON "{schema}".enrich_tasks (created_at DESC);
