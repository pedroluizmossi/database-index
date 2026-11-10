-- Leituras das telas de observação (dashboard de uso, pessoas, auditoria) não são uso do índice: deixaram de
-- ser registradas (interfaces/http/usage.py, NOT_USAGE_PREFIXES). Remove as que já tinham sido gravadas.
DELETE FROM "{schema}".usage_events
WHERE channel <> 'mcp' AND action LIKE 'GET %' AND (
       action LIKE 'GET /api/v1/usage%' OR action LIKE 'GET /api/v1/me/usage%'
    OR action LIKE 'GET /api/v1/users%' OR action LIKE 'GET /api/v1/audit%'
);
