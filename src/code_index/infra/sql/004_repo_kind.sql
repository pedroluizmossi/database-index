-- Tipo do repositório: "code" (git/pasta) ou "docs" (páginas de uma fonte de documentação).
ALTER TABLE "{schema}".repos ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'code';
