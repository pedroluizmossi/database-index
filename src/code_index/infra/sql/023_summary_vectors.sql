-- Vetor do texto do resumo de cada arquivo (`summary`, um por arquivo), ao lado das perguntas e dos cartões
-- (ver domain/search_vectors.py). Gerado sob pedido, como os outros.
ALTER TABLE "{schema}".search_vectors DROP CONSTRAINT IF EXISTS search_vectors_kind_check;
ALTER TABLE "{schema}".search_vectors ADD CONSTRAINT search_vectors_kind_check
    CHECK (kind IN ('question', 'summary', 'repo_card'));

-- Resumo desatualizado = o arquivo mudou no índice depois que o resumo foi escrito. Calculado no fim de cada
-- sync (SummaryStore.refresh_stale), para a busca não recalcular o hash dos arquivos a cada consulta.
-- `stale_since`: desde quando (SUMMARY_MAX_STALE_DAYS tira da busca o resumo velho demais).
ALTER TABLE "{schema}".file_summaries ADD COLUMN IF NOT EXISTS stale boolean NOT NULL DEFAULT false;
ALTER TABLE "{schema}".file_summaries ADD COLUMN IF NOT EXISTS stale_since timestamptz;
CREATE INDEX IF NOT EXISTS file_summaries_stale_idx ON "{schema}".file_summaries (repo, path) WHERE stale;
