-- Páginas de documentação ignoradas: somem dos resultados da busca (sem reindexar; reversível com `docs unignore`).
CREATE TABLE IF NOT EXISTS "{schema}".doc_ignores (
    doc_repo   text NOT NULL,
    doc_path   text NOT NULL,
    reason     text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (doc_repo, doc_path)
);
