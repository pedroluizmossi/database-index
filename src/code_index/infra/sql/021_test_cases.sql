-- Casos de teste lidos dos clones no sync (domain/test_cases.py), sem embedding: os testes continuam fora do
-- índice de trechos. Servem à tool find_tests ("quais testes cobrem isto?") e à contagem de testes nos
-- resultados da busca. `target_path`: arquivo de produção testado (pelo nome do teste ou pelas citações);
-- `target_symbols`: membros desse arquivo que o caso chama. Um repo é relido quando a revisão muda.
CREATE TABLE IF NOT EXISTS "{schema}".test_cases (
    repo           text NOT NULL,
    path           text NOT NULL,
    line           integer NOT NULL,
    name           text NOT NULL,
    context        text NOT NULL DEFAULT '',
    language       text NOT NULL DEFAULT '',
    subject        text,
    target_path    text,
    link           text NOT NULL DEFAULT '',
    target_symbols text[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (repo, path, line, name)
);
CREATE INDEX IF NOT EXISTS test_cases_target_idx ON "{schema}".test_cases (repo, target_path);
CREATE INDEX IF NOT EXISTS test_cases_symbols_idx ON "{schema}".test_cases USING gin (target_symbols);

-- Última leitura dos testes de cada repo (revisão do índice + versão do extrator).
CREATE TABLE IF NOT EXISTS "{schema}".test_scans (
    repo       text PRIMARY KEY,
    revision   text NOT NULL,
    files      integer NOT NULL DEFAULT 0,
    cases      integer NOT NULL DEFAULT 0,
    linked     integer NOT NULL DEFAULT 0,
    scanned_at timestamptz NOT NULL DEFAULT now()
);
