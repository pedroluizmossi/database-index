-- Grafo de chamadas (domain/code_graph.py): quem chama quem dentro de cada repo, lido dos clones no sync com
-- tree-sitter (indexing/call_scan.py), sem embedding. Uma linha por (trecho que chama, alvo); `calls` conta as
-- repetições e `line` é a primeira. `src_line = 0` é código no nível do arquivo. `via` diz como o alvo foi
-- achado (arquivo, pacote, import, tipo, herança, nome); só `nome` é inferida. Um repo é relido quando a
-- revisão muda. Fica fora de `relations` pelo volume (dezenas de milhares de arestas por repo).
CREATE TABLE IF NOT EXISTS "{schema}".code_edges (
    repo      text NOT NULL,
    src_path  text NOT NULL,
    src_line  integer NOT NULL,
    src_name  text NOT NULL DEFAULT '',
    dst_path  text NOT NULL,
    dst_line  integer NOT NULL,
    dst_name  text NOT NULL,
    via       text NOT NULL,
    line      integer NOT NULL,
    calls     integer NOT NULL DEFAULT 1,
    PRIMARY KEY (repo, src_path, src_line, dst_path, dst_line, dst_name)
);
CREATE INDEX IF NOT EXISTS code_edges_dst_idx ON "{schema}".code_edges (repo, dst_path, dst_line);
CREATE INDEX IF NOT EXISTS code_edges_dst_name_idx ON "{schema}".code_edges (lower(dst_name) text_pattern_ops);
CREATE INDEX IF NOT EXISTS code_edges_src_name_idx ON "{schema}".code_edges (lower(src_name) text_pattern_ops);

-- Última leitura de cada repo (revisão do índice + versão do extrator) e os números dela.
CREATE TABLE IF NOT EXISTS "{schema}".code_graph_scans (
    repo        text PRIMARY KEY,
    revision    text NOT NULL,
    files       integer NOT NULL DEFAULT 0,
    definitions integer NOT NULL DEFAULT 0,
    calls       integer NOT NULL DEFAULT 0,
    resolved    integer NOT NULL DEFAULT 0,
    inferred    integer NOT NULL DEFAULT 0,
    edges       integer NOT NULL DEFAULT 0,
    scanned_at  timestamptz NOT NULL DEFAULT now()
);
