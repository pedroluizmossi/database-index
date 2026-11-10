"""Páginas HTML do próprio servidor (login, escolha do provedor, consentimento do OAuth) e o build da interface web.

Todo texto vindo de fora passa por `escape`: nada externo vira HTML.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi.responses import HTMLResponse

from ...infra.oidc import OIDCProvider

UI_DIR = Path(__file__).resolve().parents[4] / "frontend" / "dist"  # build de `pnpm build` em frontend/

_PAGE_CSS = (
    "body{margin:0;min-height:100dvh;display:grid;place-items:center;padding:24px;"
    "font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;background:#fafafa;color:#18181b}"
    "main{max-width:440px;background:#fff;border:1px solid #e4e4e7;border-radius:16px;padding:28px}"
    "h1{font-size:20px;margin:0 0 8px}p{margin:8px 0;color:#52525b}"
    "a{color:#4f46e5;font-weight:500}code{font-size:14px}"
    ".btn{display:block;box-sizing:border-box;width:100%;margin:10px 0;padding:11px 16px;border-radius:10px;"
    "border:1px solid #d4d4d8;background:#fff;color:#18181b;font:inherit;font-weight:500;text-align:center;"
    "text-decoration:none;cursor:pointer}.btn.primary{background:#4f46e5;border-color:#4f46e5;color:#fff}"
    "@media (prefers-color-scheme:dark){body{background:#09090b;color:#fafafa}"
    "main{background:#18181b;border-color:#27272a}p{color:#a1a1aa}"
    ".btn{background:#27272a;border-color:#3f3f46;color:#fafafa}.btn.primary{background:#4f46e5;color:#fff}}"
)


def html_page(status_code: int, title: str, inner_html: str) -> HTMLResponse:
    """Página simples do servidor (login, consentimento). `inner_html` já deve vir com o texto escapado.

    Sem `form-action` na CSP: depois do consentimento o navegador segue o redirect para o cliente MCP
    (ex.: http://localhost:PORTA), e o Chrome aplica `form-action` também ao redirect."""
    body = (
        '<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_PAGE_CSS}</style></head><body><main>"
        f"<h1>{escape(title)}</h1>{inner_html}</main></body></html>"
    )
    return HTMLResponse(
        body,
        status_code=status_code,
        headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
                                       "frame-ancestors 'none'",
            "X-Frame-Options": "DENY",
            "Cache-Control": "no-store",
        },
    )


def login_page(status_code: int, title: str, message: str, *, retry: bool = True) -> HTMLResponse:
    """Página de erro do login. Todo texto passa por escape: nada vindo de fora vira HTML."""
    retry_link = '<a href="/auth/login">Tentar de novo</a> · ' if retry else ""
    return html_page(
        status_code, title,
        f"<p>{escape(message)}</p><p>{retry_link}<a href=\"/ui/\">Voltar para a interface</a></p>",
    )


def chooser_page(providers: list[type[OIDCProvider]], next_path: str | None) -> HTMLResponse:
    """Escolha do provedor quando há mais de um (Google e Microsoft)."""
    links = []
    for p in providers:
        query = urlencode({"provider": p.name, **({"next": next_path} if next_path else {})})
        links.append(f'<a class="btn" href="/auth/login?{escape(query)}">Entrar com {escape(p.label)}</a>')
    return html_page(200, "Entrar no code-index", "<p>Escolha a sua conta:</p>" + "".join(links))


def consent_page(*, client_name: str, redirect_uri: str, email: str, req: str) -> HTMLResponse:
    """Consentimento do OAuth do MCP. O nome do cliente vem do próprio cliente (não confiável): só escapado."""
    target = urlsplit(redirect_uri)
    switch = urlencode({"next": f"/auth/authorize?req={req}"})
    return html_page(
        200, "Autorizar acesso",
        f"<p><strong>{escape(client_name)}</strong> quer acesso de <strong>leitura</strong> ao code-index "
        f"(buscar e ler o código e a documentação indexados) em nome de <strong>{escape(email)}</strong>.</p>"
        f"<p>Depois de aprovar, você volta para <code>{escape(target.scheme)}://{escape(target.netloc)}</code>. "
        "Se não foi você que iniciou esta conexão, negue.</p>"
        '<form method="post" action="/auth/authorize">'
        f'<input type="hidden" name="req" value="{escape(req)}">'
        '<button class="btn primary" name="decision" value="allow">Permitir</button>'
        '<button class="btn" name="decision" value="deny">Negar</button></form>'
        f'<p><a href="/auth/login?{escape(switch)}">Usar outra conta</a></p>',
    )


def safe_next(value: str | None) -> str | None:
    """Destino depois do login: só a página de consentimento do OAuth (evita open redirect)."""
    if value and value.startswith("/auth/authorize?req=") and "//" not in value and "\\" not in value:
        return value
    return None
