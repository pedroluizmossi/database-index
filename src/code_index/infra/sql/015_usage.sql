-- Pessoas como entidade central e eventos de uso (MCP, API e interface).
--
-- users: ganha estado (desativada), último acesso por qualquer credencial e `attrs` (jsonb livre) para
-- atributos futuros sem migration. Todo token passa a apontar para a pessoa (user_id); o owner_email
-- continua lá por compatibilidade e auditoria.
--
-- usage_events: um evento por chamada (tool MCP ou rota da API), para medir uso, consumo e qualidade.
-- Nunca guarda tokens nem o conteúdo devolvido; `params` tem só os parâmetros (consulta, filtros).

ALTER TABLE "{schema}".users ADD COLUMN IF NOT EXISTS last_seen_at timestamptz;
ALTER TABLE "{schema}".users ADD COLUMN IF NOT EXISTS disabled_at timestamptz;
ALTER TABLE "{schema}".users ADD COLUMN IF NOT EXISTS attrs jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Donos de tokens que nunca fizeram login (tokens emitidos pela CLI) também são pessoas.
INSERT INTO "{schema}".users (email)
SELECT DISTINCT lower(owner_email) FROM "{schema}".api_tokens
ON CONFLICT (email) DO NOTHING;

ALTER TABLE "{schema}".api_tokens
    ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES "{schema}".users (id) ON DELETE CASCADE;
UPDATE "{schema}".api_tokens t SET user_id = u.id
FROM "{schema}".users u
WHERE t.user_id IS NULL AND u.email = lower(t.owner_email);
CREATE INDEX IF NOT EXISTS api_tokens_user_idx ON "{schema}".api_tokens (user_id);

CREATE TABLE IF NOT EXISTS "{schema}".usage_events (
    id              bigserial PRIMARY KEY,
    at              timestamptz NOT NULL DEFAULT now(),
    user_id         uuid REFERENCES "{schema}".users (id) ON DELETE SET NULL,
    token_id        uuid,
    token_kind      text,                        -- session | agent | oauth
    channel         text NOT NULL,               -- mcp | api | ui
    action          text NOT NULL,               -- tool MCP ("search_code") ou rota ("POST /api/v1/search")
    client          text,                        -- cliente MCP (clientInfo/User-Agent), ex.: "claude-code"
    client_version  text,
    status          integer NOT NULL,            -- 200 ok; 4xx/5xx erro
    duration_ms     integer NOT NULL,
    result_count    integer,                     -- itens devolvidos (NULL = não se aplica)
    response_bytes  integer,                     -- tamanho da resposta: consumo de contexto do agent
    embed_tokens    integer NOT NULL DEFAULT 0,  -- Voyage: embedding da consulta
    rerank_tokens   integer NOT NULL DEFAULT 0,  -- Voyage: rerank
    cost_usd        double precision NOT NULL DEFAULT 0,  -- custo estimado pelo LiteLLM
    params          jsonb,                       -- parâmetros da chamada (sem segredos nem conteúdo)
    attrs           jsonb                        -- extensão livre
);
CREATE INDEX IF NOT EXISTS usage_events_at_idx ON "{schema}".usage_events (at DESC);
CREATE INDEX IF NOT EXISTS usage_events_user_at_idx ON "{schema}".usage_events (user_id, at DESC);
CREATE INDEX IF NOT EXISTS usage_events_action_at_idx ON "{schema}".usage_events (action, at DESC);
