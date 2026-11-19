-- Estimativa de arquivos indexáveis de um repo ainda não indexado, contada na árvore do GitHub (sem clonar) com os
-- filtros do sync (`code-index repo estimate`). `files_estimate_pushed_at` guarda o push que foi contado: com o
-- mesmo push, a contagem não se repete (não gasta cota do GitHub). `truncated`: o GitHub cortou a árvore (repo
-- enorme), então a contagem é um mínimo.
ALTER TABLE "{schema}".repos ADD COLUMN IF NOT EXISTS files_estimate integer;
ALTER TABLE "{schema}".repos ADD COLUMN IF NOT EXISTS files_estimate_truncated boolean NOT NULL DEFAULT false;
ALTER TABLE "{schema}".repos ADD COLUMN IF NOT EXISTS files_estimate_at timestamptz;
ALTER TABLE "{schema}".repos ADD COLUMN IF NOT EXISTS files_estimate_pushed_at timestamptz;
