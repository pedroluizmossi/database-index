-- Revisão de todas as páginas da wiki: o agent também pode sugerir REMOVER um vínculo automático
-- (menção, tag, nome, SDL...). A linha em doc_links ganha `action`:
--   'add'    = vínculo gravado (manual/agent) ou sugerido pela LLM (vale quando status = 'active');
--   'remove' = contesta um vínculo automático; quando aprovado (status = 'active'), a consulta deixa de
--              ligar a página àquele repo (ver infra/doc_link_store.links_sql).
ALTER TABLE "{schema}".doc_links ADD COLUMN IF NOT EXISTS action text NOT NULL DEFAULT 'add';
ALTER TABLE "{schema}".doc_links DROP CONSTRAINT IF EXISTS doc_links_action_check;
ALTER TABLE "{schema}".doc_links ADD CONSTRAINT doc_links_action_check CHECK (action IN ('add', 'remove'));
