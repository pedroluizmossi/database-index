-- Modelo de relações (ver ARQUITETURA.md): documentos tipados + uma tabela única de relações.
-- Substitui doc_links, doc_ignores, api_impls e doc_link_reviews (os dados são copiados e as tabelas,
-- apagadas). As relações derivadas (menção, tag, SDL, handler...) são recalculadas pelos linkers no
-- primeiro uso depois desta migração (services.open_service) e no fim de cada sync.

-- Chave de entidade do repo (domain.relations.repo_key), para juntar com relations por índice.
ALTER TABLE "{schema}".repos ADD COLUMN IF NOT EXISTS key text GENERATED ALWAYS AS ('repo:' || name) STORED;
CREATE UNIQUE INDEX IF NOT EXISTS repos_key_idx ON "{schema}".repos (key);

-- Catálogo das fontes de documentação: o que cada fonte materializou, com tipo e atributos.
-- present = existe na fonte hoje; ignored = fora dos resultados da busca (docs ignore).
CREATE TABLE IF NOT EXISTS "{schema}".documents (
    doc_repo      text NOT NULL,
    path          text NOT NULL,
    key           text GENERATED ALWAYS AS ('doc:' || doc_repo || '/' || path) STORED,
    type          text NOT NULL DEFAULT 'wiki_page',
    title         text NOT NULL DEFAULT '',
    url           text NOT NULL DEFAULT '',
    tags          jsonb NOT NULL DEFAULT '[]'::jsonb,
    source_version text NOT NULL DEFAULT '',
    attrs         jsonb NOT NULL DEFAULT '{}'::jsonb,
    present       boolean NOT NULL DEFAULT true,
    ignored       boolean NOT NULL DEFAULT false,
    ignore_reason text NOT NULL DEFAULT '',
    refreshed_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (doc_repo, path)
);
CREATE UNIQUE INDEX IF NOT EXISTS documents_key_idx ON "{schema}".documents (key);
CREATE INDEX IF NOT EXISTS documents_type_idx ON "{schema}".documents (doc_repo, type);

-- Relações entre entidades (chaves 'repo:', 'doc:', 'op:', 'code:').
CREATE TABLE IF NOT EXISTS "{schema}".relations (
    src         text NOT NULL,
    dst         text NOT NULL,
    type        text NOT NULL CHECK (type IN ('documents', 'describes', 'implemented_by', 'related_to')),
    origin      text NOT NULL,
    status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suggested', 'rejected')),
    action      text NOT NULL DEFAULT 'add' CHECK (action IN ('add', 'remove')),
    contestable boolean NOT NULL DEFAULT false,
    confidence  text,
    evidence    jsonb NOT NULL DEFAULT '{}'::jsonb,
    agent       text NOT NULL DEFAULT '',
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (src, dst, type, origin)
);
CREATE INDEX IF NOT EXISTS relations_dst_idx ON "{schema}".relations (dst, type);
CREATE INDEX IF NOT EXISTS relations_origin_idx ON "{schema}".relations (origin);
CREATE INDEX IF NOT EXISTS relations_review_idx ON "{schema}".relations (status, action) WHERE status <> 'active' OR action <> 'add';

-- Relação efetiva: a única consulta de vínculo do sistema.
CREATE OR REPLACE VIEW "{schema}".effective_relations AS
SELECT r.src, r.dst, r.type, r.origin, r.confidence, r.evidence, r.agent, r.updated_at
FROM "{schema}".relations r
WHERE r.status = 'active' AND r.action = 'add'
  AND NOT (r.contestable AND EXISTS (
        SELECT 1 FROM "{schema}".relations x
        WHERE x.src = r.src AND x.dst = r.dst AND x.type = r.type AND x.action = 'remove' AND x.status = 'active'))
  AND (left(r.dst, 5) <> 'repo:' OR EXISTS (
        SELECT 1 FROM "{schema}".repos p WHERE p.key = r.dst AND p.kind = 'code' AND NOT p.ignored))
  AND (left(r.src, 5) <> 'repo:' OR EXISTS (
        SELECT 1 FROM "{schema}".repos p WHERE p.key = r.src AND p.kind = 'code' AND NOT p.ignored))
  AND (left(r.src, 4) <> 'doc:' OR EXISTS (
        SELECT 1 FROM "{schema}".documents d WHERE d.key = r.src AND d.present));

-- Análises feitas por agents (ex.: página já revisada), genérico por tipo de tarefa e entidade.
CREATE TABLE IF NOT EXISTS "{schema}".enrich_reviews (
    kind       text NOT NULL,
    entity     text NOT NULL,
    input_hash text NOT NULL,
    agent      text NOT NULL DEFAULT '',
    result     jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, entity)
);

-- ------------------------------------------------------------ cópia dos dados das tabelas antigas

-- páginas ignoradas → documents.ignored (present = false até o catálogo ser recalculado)
INSERT INTO "{schema}".documents (doc_repo, path, present, ignored, ignore_reason)
SELECT doc_repo, doc_path, false, true, reason FROM "{schema}".doc_ignores
ON CONFLICT (doc_repo, path) DO UPDATE SET ignored = true, ignore_reason = EXCLUDED.ignore_reason;

-- vínculos gravados (manual/agent) e sugestões de LLM (adicionar/remover) → relations
INSERT INTO "{schema}".relations (src, dst, type, origin, status, action, contestable, confidence, evidence,
                                  agent, updated_at)
SELECT 'doc:' || doc_repo || '/' || doc_path, 'repo:' || repo, 'documents', origin, status, action, false,
       confidence, jsonb_strip_nulls(jsonb_build_object('reason', NULLIF(reason, ''), 'basis', basis)),
       agent, created_at
FROM "{schema}".doc_links
ON CONFLICT DO NOTHING;

-- operação → handler (recalculado pelo linker 'handler'; copiado para não haver janela vazia)
INSERT INTO "{schema}".relations (src, dst, type, origin, evidence)
SELECT 'op:' || lower(domain) || '/' || lower(service) || '/' || operation,
       'code:' || repo || '/' || path || ':' || line, 'implemented_by', 'handler',
       jsonb_build_object('domain', domain, 'service', service, 'operation', operation, 'kind', kind,
                          'repo', repo, 'symbol', symbol, 'path', path, 'line', line)
FROM "{schema}".api_impls
ON CONFLICT DO NOTHING;

-- páginas já analisadas por agent → enrich_reviews
INSERT INTO "{schema}".enrich_reviews (kind, entity, input_hash, agent, result, created_at)
SELECT 'doc_links', 'doc:' || doc_repo || '/' || doc_path, input_hash, agent, result, created_at
FROM "{schema}".doc_link_reviews
ON CONFLICT DO NOTHING;

DROP TABLE IF EXISTS "{schema}".doc_links;
DROP TABLE IF EXISTS "{schema}".doc_ignores;
DROP TABLE IF EXISTS "{schema}".api_impls;
DROP TABLE IF EXISTS "{schema}".doc_link_reviews;
