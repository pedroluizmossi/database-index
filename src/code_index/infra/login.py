"""Registro dos provedores de login de pessoas (Google, Microsoft). Vários podem estar ativos ao mesmo tempo.

Um provedor está "configurado" quando as credenciais dele estão no Settings; erros de configuração
(tenant inválido, ninguém autorizado) aparecem ao abrir o login, com a mensagem do provedor.
"""

from __future__ import annotations

from ..settings import Settings
from .entra_oidc import EntraOIDC
from .google_oidc import GoogleOIDC
from .oidc import OIDCProvider

PROVIDERS: dict[str, type[OIDCProvider]] = {GoogleOIDC.name: GoogleOIDC, EntraOIDC.name: EntraOIDC}


def configured_providers(settings: Settings) -> list[type[OIDCProvider]]:
    """Provedores com credenciais no Settings, na ordem de exibição."""
    out: list[type[OIDCProvider]] = []
    if settings.google_client_id and settings.google_client_secret:
        out.append(GoogleOIDC)
    if settings.azure_client_id and settings.azure_client_secret:
        out.append(EntraOIDC)
    return out


def open_provider(settings: Settings, name: str) -> OIDCProvider:
    """Instancia o provedor `name`. RuntimeError com mensagem segura se desconhecido ou mal configurado."""
    cls = PROVIDERS.get(name)
    if cls is None or cls not in configured_providers(settings):
        raise RuntimeError(f"login '{name}' não está configurado neste servidor")
    return cls(settings)  # type: ignore[call-arg]
