-- Vetores extras da busca (ver domain/search_vectors.py): perguntas dos resumos de arquivos (`question`, um por
-- pergunta) e cartões dos repos (`repo_card`, um por repo). Não passam pelo CocoIndex: são gerados sob pedido
-- (`code-index vectors embed`). `text_hash` e `model` dizem se o vetor ainda corresponde ao texto de origem e ao
-- modelo atual; o que diverge é pendente. A coluna não fixa dimensões: a busca filtra por `model`.
CREATE TABLE IF NOT EXISTS "{schema}".search_vectors (
    kind        text NOT NULL CHECK (kind IN ('question', 'repo_card')),
    repo        text NOT NULL,
    path        text NOT NULL DEFAULT '',
    ord         integer NOT NULL DEFAULT 0,
    text        text NOT NULL,
    text_hash   text NOT NULL,
    model       text NOT NULL,
    embedding   vector NOT NULL,
    embedded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, repo, path, ord)
);
