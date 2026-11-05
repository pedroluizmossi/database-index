-- Dono do código no DDD (domain/ownership.py): relação 'belongs_to' de um repo ('repo:x') ou de uma pasta
-- dele ('dir:x/caminho', monorepo) para um serviço ('svc:dominio/servico') ou domínio ('dom:dominio').
-- Origens derivadas (declared, sibling, api_calls, usage) são recalculadas pelo linker 'ownership';
-- a atribuição manual (origin = 'manual') é gravada pela interface/API/CLI e vence as demais.
ALTER TABLE "{schema}".relations DROP CONSTRAINT IF EXISTS relations_type_check;
ALTER TABLE "{schema}".relations ADD CONSTRAINT relations_type_check
    CHECK (type IN ('documents', 'describes', 'implemented_by', 'related_to', 'belongs_to'));

CREATE INDEX IF NOT EXISTS relations_belongs_idx ON "{schema}".relations (src) WHERE type = 'belongs_to';

-- Mesma view da 010, mais: pasta de repo ('dir:') só vale enquanto o repo existir (e não estiver ignorado).
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
  AND (left(r.src, 4) <> 'dir:' OR EXISTS (
        SELECT 1 FROM "{schema}".repos p
        WHERE p.name = split_part(substr(r.src, 5), '/', 1) AND p.kind = 'code' AND NOT p.ignored))
  AND (left(r.src, 4) <> 'doc:' OR EXISTS (
        SELECT 1 FROM "{schema}".documents d WHERE d.key = r.src AND d.present));
