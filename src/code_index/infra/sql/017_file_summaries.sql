-- Resumos de arquivos de código escritos por agents (fila `enrich next --kind file_summaries`).
-- O resumo e as palavras-chave entram nas colunas de busca por termos dos trechos do arquivo
-- (ver domain/file_summary.py); `content_hash` diz se o arquivo mudou desde o resumo.
CREATE TABLE IF NOT EXISTS "{schema}".file_summaries (
    repo         text NOT NULL,
    path         text NOT NULL,
    content_hash text NOT NULL,
    summary      text NOT NULL,
    questions    jsonb NOT NULL DEFAULT '[]'::jsonb,
    keywords     jsonb NOT NULL DEFAULT '[]'::jsonb,
    symbol_terms text NOT NULL,
    text_terms   text NOT NULL,
    agent        text NOT NULL DEFAULT '',
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (repo, path)
);

-- Fila: novo tipo de tarefa (lote de arquivos de um repo; subject = primeiro arquivo do lote).
ALTER TABLE "{schema}".enrich_tasks DROP CONSTRAINT IF EXISTS enrich_tasks_kind_check;
ALTER TABLE "{schema}".enrich_tasks ADD CONSTRAINT enrich_tasks_kind_check
    CHECK (kind IN ('card', 'doc_links', 'file_summaries'));
