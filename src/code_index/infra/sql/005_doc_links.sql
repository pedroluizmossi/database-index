-- Vínculos página → repo gravados por pessoas ou agents (origem manual/agent). Não passam pelo CocoIndex:
-- a consulta (infra/doc_link_store.links_sql) só usa a linha enquanto a página e o repo existirem.
CREATE TABLE IF NOT EXISTS "{schema}".doc_links (
    doc_repo   text NOT NULL,
    doc_path   text NOT NULL,
    repo       text NOT NULL,
    origin     text NOT NULL DEFAULT 'manual' CHECK (origin IN ('manual', 'agent')),
    reason     text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (doc_repo, doc_path, repo)
);
