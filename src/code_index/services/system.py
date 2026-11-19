"""Tela Sistema (admin, só leitura): configuração efetiva, saúde das dependências e perfis ativos.

Nada aqui gera custo: o servidor de modelos próprio é sondado em GET /models (sem embedding), a cota do GitHub em
GET /rate_limit (não gasta cota) e provedor hospedado (Voyage) não é testado. Segredo nunca sai: só "definido".
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import platform
import re
import shutil
import sys
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any

import httpx
from pydantic import AliasChoices, SecretStr

from ..infra.system_store import SystemStore
from ..profiles.base import PROFILE_SETTINGS
from ..settings import Settings

# Campos só da CLI remota (não dizem nada sobre o servidor).
_CLI_ONLY = frozenset({"remote_url", "remote_token", "credentials_file", "cli_mode", "remote_timeout_seconds"})
_SECRET = re.compile(r"(token|secret|password|api_key|private_key)", re.I)

_GROUPS: tuple[tuple[str, str], ...] = (
    ("models", "Modelos"), ("search", "Busca"), ("indexing", "Indexação e fila"), ("github", "GitHub"),
    ("sources", "Fontes de documentação"), ("login", "Login e segurança"), ("limits", "Limites e uso"),
    ("database", "Banco"), ("general", "Geral"),
)


def _group(name: str) -> str:
    if name.startswith(("embed_", "rerank_")):
        return "models"
    if name.startswith(("search_", "summary_", "vectors_")):
        return "search"
    if name.startswith("github_"):
        return "github"
    if name.startswith(("wikijs_", "openapi_", "doc_")):
        return "sources"
    if name.startswith(("sync_", "enrich_")) or name in {"default_batch", "max_file_bytes", "repos_dir", "cocoindex_db"}:
        return "indexing"
    if name.startswith(("google_", "azure_", "oauth_", "session_", "token_", "auth_")) or name in {
            "admin_emails", "allowed_hosts", "public_url", "http_host", "http_port"}:
        return "login"
    if name.startswith(("rate_limit_", "usage_")):
        return "limits"
    if name in {"database_url", "pg_schema"} or name.endswith("_table"):
        return "database"
    return "general"


@lru_cache(maxsize=1)
def _hints() -> dict[str, str]:
    """Comentários do código de `Settings` (o bloco logo acima de cada campo e o comentário na mesma linha): a
    documentação de cada configuração, sem repetir texto. Vazio se o código-fonte não estiver disponível."""
    try:
        source = inspect.getsource(Settings)
    except (OSError, TypeError):
        return {}
    lines = source.splitlines()
    tree = ast.parse(source)
    cls = tree.body[0]
    out: dict[str, str] = {}
    assert isinstance(cls, ast.ClassDef)
    for node in cls.body:
        if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)):
            continue
        block: list[str] = []
        i = node.lineno - 2
        while i >= 0 and lines[i].strip().startswith("#"):
            block.insert(0, lines[i].strip().lstrip("#").strip())
            i -= 1
        # Linha solta antes de um grupo de campos ("# Postgres", "# GitHub") é título de seção, não dica.
        if len(block) == 1 and len(block[0]) < 70 and not block[0].rstrip().endswith((".", ":")) \
                and (i < 0 or not lines[i].strip()):
            block = []
        end = lines[(node.end_lineno or node.lineno) - 1]
        inline = end.split("#", 1)[1].strip() if "#" in end and not end.strip().startswith("#") else ""
        text = " ".join(block + ([inline] if inline else []))
        if text:
            out[node.target.id] = text
    return out


def _env_name(name: str, alias: Any) -> str:
    if isinstance(alias, str):
        return alias
    if isinstance(alias, AliasChoices):
        first = next((c for c in alias.choices if isinstance(c, str)), None)
        if first:
            return first
    return name.upper()


def _plain(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(v) for v in value]
    return value


def _mask_url(url: str) -> str:
    """Tira usuário e senha de uma URL de conexão (postgres://u:s@host/db → postgres://***@host/db)."""
    return re.sub(r"//[^/@]+@", "//***@", url)


def describe_settings(settings: Settings, profiles: Any) -> list[dict[str, Any]]:
    """Configuração efetiva por grupo: valor (segredo nunca: só se está definido), variável de ambiente, origem
    (`env`: ambiente ou .env; `profile`: padrão de um perfil ativo; `default`: padrão do núcleo) e a dica."""
    hints = _hints()
    explicit = settings.model_fields_set
    rows: dict[str, list[dict[str, Any]]] = {key: [] for key, _ in _GROUPS}
    for name, field in type(settings).model_fields.items():
        if name in _CLI_ONLY:
            continue
        value = getattr(settings, name)
        origin = "env" if name in explicit else "default"
        if name in PROFILE_SETTINGS and value is None:
            for p in profiles.profiles:
                default = p.settings_defaults().get(name)
                if default is not None:
                    value, origin = default, f"profile:{p.name}"
                    break
        secret = isinstance(value, SecretStr) or (field.annotation is not None and "SecretStr" in str(field.annotation)) \
            or bool(_SECRET.search(name))
        item: dict[str, Any] = {"name": name, "env": _env_name(name, field.validation_alias), "origin": origin,
                                "secret": secret, "hint": hints.get(name)}
        if secret:
            item["value"] = None
            item["defined"] = value is not None and value != ""
        else:
            plain = _plain(value)
            item["value"] = _mask_url(plain) if name == "database_url" and isinstance(plain, str) else plain
            item["defined"] = plain not in (None, "", [])
        rows[_group(name)].append(item)
    return [{"id": key, "label": label, "settings": rows[key]} for key, label in _GROUPS if rows[key]]


def _model_name(model: str) -> str:
    return model.split("/", 1)[1] if "/" in model else model


async def _probe_models(base: str | None, model: str, role: str) -> dict[str, Any]:
    """Servidor de modelos próprio: GET {base}/models (sem gerar embedding). Hospedado: não testa (custaria)."""
    out: dict[str, Any] = {"role": role, "model": model, "api_base": base}
    if not model:
        return {**out, "status": "off", "detail": "desligado"}
    if not base:
        return {**out, "status": "hosted", "detail": "provedor hospedado: não testado aqui (geraria custo)"}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(base.rstrip("/") + "/models")
        if r.status_code != 200:
            return {**out, "status": "error", "detail": f"HTTP {r.status_code} em /models"}
        ids = [m.get("id") for m in (r.json().get("data") or []) if isinstance(m, dict)]
        wanted = _model_name(model)
        if wanted in ids:
            return {**out, "status": "ok", "detail": f"respondendo; {wanted} disponível"}
        return {**out, "status": "warning", "detail": f"respondendo, mas sem {wanted} na lista ({len(ids)} modelos)"}
    except (httpx.HTTPError, ValueError) as e:
        return {**out, "status": "error", "detail": f"sem resposta: {type(e).__name__}"}


def _disk(label: str, path: Path) -> dict[str, Any] | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return {"label": label, "path": str(path), "total": usage.total, "used": usage.used, "free": usage.free}


class SystemMixin:
    settings: Settings
    profiles: Any
    _system: SystemStore | None

    async def system_info(self) -> dict[str, Any]:
        """Tudo da tela Sistema numa chamada (admin). Sem custo de API."""
        s = self.settings
        github = getattr(self, "_indexer").github

        async def github_status() -> dict[str, Any]:
            remaining = await asyncio.to_thread(github.rate_remaining)
            return {"token": s.github_token is not None, "rate_remaining": remaining,
                    "reserve": s.github_rate_reserve, "estimate_max": s.github_estimate_max}

        async def database() -> dict[str, Any] | None:
            return await self._system.database() if self._system is not None else None

        embed, rerank, gh, db = await asyncio.gather(
            _probe_models(s.embed_api_base, s.embed_model, "embedding"),
            _probe_models(s.rerank_api_base, s.rerank_model, "rerank"),
            github_status(), database(),
        )
        disks = [d for d in (_disk("Clones (repos_dir)", s.repos_dir), _disk("Estado do CocoIndex", s.cocoindex_db))
                 if d is not None]
        try:
            version = metadata.version("code-index")
        except metadata.PackageNotFoundError:
            version = None
        return {
            "app": {"version": version, "python": sys.version.split()[0], "host": platform.node(),
                    "worker_embedded": s.sync_worker_embedded},
            "health": {"models": [embed, rerank], "github": gh, "database": db, "disks": disks},
            "profiles": self.profiles.describe(),
            "settings": describe_settings(s, self.profiles),
        }
