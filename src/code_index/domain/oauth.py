"""Regras (puras) do OAuth do MCP: o code-index como servidor de autorização dos clientes MCP.

Fluxo: o cliente (Claude Code, Codex, Kiro...) se registra (RFC 7591), abre o navegador em /authorize,
a pessoa entra com Google ou Microsoft e aprova o acesso; o cliente troca o código (PKCE) por um token
`cix_` do tipo "oauth" e um refresh token, rotacionado a cada uso.

Proteções:
  - redirect_uri: só loopback (http://localhost, 127.0.0.1, [::1]) ou prefixos configurados. Um cliente
    registrado com redirect para um site qualquer serviria para phishing do código de autorização;
  - consentimento explícito a cada autorização (o registro de clientes é aberto);
  - o token OAuth só lê (search:read), mesmo para admins: indexar continua na API/interface.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from urllib.parse import SplitResult, urlsplit

from .auth import SEARCH_READ

OAUTH_SCOPES: tuple[str, ...] = (SEARCH_READ,)
CODE_TTL_SECONDS = 300
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _matches_prefix(uri: str, p: SplitResult, prefix: str) -> bool:
    """Mesmo esquema e mesmo host:porta (sem usuário no meio: `https://site@evil.com`), e o caminho do prefixo
    termina numa fronteira de segmento (`/cb` aceita `/cb` e `/cb/x`, não `/cbx`)."""
    try:
        q = urlsplit(prefix)
    except ValueError:
        return False
    if not q.scheme:
        return False
    if not q.netloc:  # só o esquema de um app (`kiro://`): quem configurou aceita o esquema inteiro
        return p.scheme.lower() == q.scheme.lower() and uri.startswith(prefix)
    if p.scheme.lower() != q.scheme.lower() or p.netloc.lower() != q.netloc.lower():
        return False
    if q.query:
        return uri.startswith(prefix)
    base = q.path
    if not base or base.endswith("/"):
        return (p.path or "/").startswith(base or "/")
    return p.path == base or p.path.startswith(base + "/")


def redirect_uri_allowed(uri: str, allowed_prefixes: Iterable[str] = ()) -> bool:
    """Loopback http(s) em qualquer porta/caminho, ou dentro de um dos prefixos configurados."""
    try:
        p = urlsplit(uri)
    except ValueError:
        return False
    if p.fragment or not p.scheme:
        return False
    if p.scheme in ("http", "https") and (p.hostname or "") in LOOPBACK_HOSTS:
        return True
    return any(prefix and _matches_prefix(uri, p, prefix) for prefix in allowed_prefixes)


def check_redirect_uris(uris: Sequence[str], allowed_prefixes: Iterable[str] = ()) -> None:
    """ValueError com mensagem clara se algum redirect_uri não é aceito."""
    prefixes = tuple(allowed_prefixes)
    if not uris:
        raise ValueError("informe ao menos um redirect_uri")
    bad = [u for u in uris if not redirect_uri_allowed(u, prefixes)]
    if bad:
        raise ValueError(
            f"redirect_uri não permitido: {', '.join(bad)}. Aceitos: http://localhost / 127.0.0.1 e os "
            "prefixos de CODE_INDEX_OAUTH_REDIRECT_URIS"
        )


def grant_scopes(requested: Iterable[str] | None, user_scopes: Iterable[str]) -> tuple[str, ...]:
    """Scopes de um token OAuth: o pedido (ou todos os do OAuth) ∩ os da pessoa ∩ os do OAuth."""
    user = set(user_scopes)
    wanted = list(requested) if requested else list(OAUTH_SCOPES)
    unknown = [s for s in wanted if s not in OAUTH_SCOPES]
    if unknown:
        raise ValueError(f"scope não disponível por OAuth: {', '.join(unknown)}")
    out = tuple(s for s in dict.fromkeys(wanted) if s in user)
    if not out:
        raise PermissionError("você não tem acesso de leitura a este servidor")
    return out
