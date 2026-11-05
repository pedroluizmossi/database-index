-- Operação da API (SDL) → handler no código. Preenchida sem LLM em `enrich facts` (origin 'nome':
-- classe <operacao>Handler); 'llm' fica para os casos que um agent resolver.
CREATE TABLE IF NOT EXISTS "{schema}".api_impls (
    domain    text NOT NULL,
    service   text NOT NULL,
    operation text NOT NULL,
    kind      text NOT NULL,
    repo      text NOT NULL,
    symbol    text NOT NULL,
    path      text NOT NULL,
    line      integer NOT NULL,
    origin    text NOT NULL DEFAULT 'nome' CHECK (origin IN ('nome', 'llm')),
    PRIMARY KEY (domain, service, operation, repo)
);
CREATE INDEX IF NOT EXISTS api_impls_op_idx ON "{schema}".api_impls (lower(operation));
CREATE INDEX IF NOT EXISTS api_impls_repo_idx ON "{schema}".api_impls (repo);

-- Vínculos página → repo sugeridos por LLM: entram como 'suggested' e só valem depois de aprovados.
ALTER TABLE "{schema}".doc_links DROP CONSTRAINT IF EXISTS doc_links_origin_check;
ALTER TABLE "{schema}".doc_links ADD CONSTRAINT doc_links_origin_check CHECK (origin IN ('manual', 'agent', 'llm'));
ALTER TABLE "{schema}".doc_links ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'active';
ALTER TABLE "{schema}".doc_links DROP CONSTRAINT IF EXISTS doc_links_status_check;
ALTER TABLE "{schema}".doc_links ADD CONSTRAINT doc_links_status_check CHECK (status IN ('active', 'suggested', 'rejected'));
ALTER TABLE "{schema}".doc_links ADD COLUMN IF NOT EXISTS basis text;
ALTER TABLE "{schema}".doc_links ADD COLUMN IF NOT EXISTS confidence text;
ALTER TABLE "{schema}".doc_links ADD COLUMN IF NOT EXISTS agent text NOT NULL DEFAULT '';

-- Páginas já analisadas por agent (inclusive "nenhum repo"): não voltam para a fila enquanto não mudarem.
CREATE TABLE IF NOT EXISTS "{schema}".doc_link_reviews (
    doc_repo   text NOT NULL,
    doc_path   text NOT NULL,
    input_hash text NOT NULL,
    agent      text NOT NULL DEFAULT '',
    result     jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (doc_repo, doc_path)
);

-- Fila: novo tipo de tarefa (lote de páginas da wiki) identificado por (repo, subject = pasta).
ALTER TABLE "{schema}".enrich_tasks DROP CONSTRAINT IF EXISTS enrich_tasks_kind_check;
ALTER TABLE "{schema}".enrich_tasks ADD CONSTRAINT enrich_tasks_kind_check CHECK (kind IN ('card', 'doc_links'));
ALTER TABLE "{schema}".enrich_tasks ADD COLUMN IF NOT EXISTS subject text NOT NULL DEFAULT '';
ALTER TABLE "{schema}".enrich_tasks ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
DROP INDEX IF EXISTS "{schema}".enrich_tasks_one_lease;
CREATE UNIQUE INDEX IF NOT EXISTS enrich_tasks_one_lease ON "{schema}".enrich_tasks (kind, repo, subject) WHERE status = 'leased';
