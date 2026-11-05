-- Progresso de um sync em andamento (etapa e situação de cada repo: arquivos e trechos processados).
-- Gravado pelo servidor durante a execução; no fim fica como o último retrato do job.
ALTER TABLE "{schema}".sync_jobs ADD COLUMN IF NOT EXISTS progress jsonb;
