"""Mensagens claras para os erros de um sync (puro, sem I/O).

O erro técnico (exceção do LiteLLM, do git, do Wiki.js) continua no log; o que vai para o job, o repo e a tela é uma
frase que diz o que aconteceu e o que fazer. Erro que não casa com nenhum caso volta como veio (primeira linha).
"""

from __future__ import annotations

import re

# (padrão no texto do erro, mensagem). A ordem importa: o primeiro que casar vence.
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"exceeds the available context size|ContextWindowExceeded|context length|maximum context", re.I),
     "texto maior que o contexto do modelo de embedding (aumente o contexto do servidor ou o arquivo é gerado)"),
    (re.compile(r"(APIConnectionError|Connection ?refused|ConnectError|Name or service not known|"
                r"client has been closed)", re.I),
     "servidor de modelos fora do ar ou inacessível (confira o llama-swap / EMBED_API_BASE)"),
    (re.compile(r"\b429\b|rate.?limit|RateLimitError", re.I),
     "o provedor de embedding limitou as requisições (rate limit); o job tenta de novo"),
    (re.compile(r"\b401\b.*(voyage|openai|embedding)|AuthenticationError|invalid api key", re.I),
     "chave do provedor de embedding recusada (confira VOYAGE_API_KEY / a chave do modelo)"),
    (re.compile(r"Authentication failed|could not read Username|terminal prompts disabled|"
                r"Repository not found|remote: Not Found", re.I),
     "sem acesso ao repositório no GitHub (token sem permissão, repo privado ou apagado)"),
    (re.compile(r"Could not resolve host|unable to access 'https?://", re.I),
     "sem rede até o GitHub (DNS ou conexão)"),
    (re.compile(r"AdvisoryLockLost|lost the advisory lock", re.I),
     "a conexão com o banco caiu durante o sync (o job volta para a fila)"),
    (re.compile(r"No space left on device|disk full", re.I),
     "disco cheio no servidor"),
)


def explain_error(message: str | None) -> str:
    """Frase clara para o erro. Mensagens já escritas em português pelo sistema (Wiki.js, permissões) passam como
    estão; o resto casa com as regras acima ou volta na primeira linha."""
    text = (message or "").strip()
    if not text:
        return "erro sem mensagem (veja o log do worker)"
    for pattern, friendly in _RULES:
        if pattern.search(text):
            return friendly
    return text.splitlines()[0][:500]
