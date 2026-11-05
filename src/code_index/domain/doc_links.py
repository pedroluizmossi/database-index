"""Vínculos entre documentação e repos (puro, determinístico, sem I/O).

Lado do repo — apelidos (aliases), de fontes que existem em qualquer stack:
  nome do repo (org__repo, repo, repo sem prefixo curto), package.json, pom.xml, pyproject.toml,
  setup.cfg, go.mod, Cargo.toml, settings.gradle e catalog-info.yaml (Backstage).
Lado da página — menções:
  regra explícita do repos.toml (caminho → repos), tags da página, links para o GitHub e termos do texto.
O vínculo é o cruzamento (linker `alias` em indexing/linkers.py, gravado em `relations`). Termos do texto só casam com
apelidos FORTES: nomes curtos/genéricos ("email") ligariam páginas demais; esses só valem por tag ou regra.
"""

from __future__ import annotations

import configparser
import json
import re
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from typing import Any

import yaml

MANIFESTS = {
    "package.json", "pom.xml", "pyproject.toml", "setup.cfg", "go.mod", "Cargo.toml",
    "settings.gradle", "settings.gradle.kts", "catalog-info.yaml", "catalog-info.yml",
}
_TERM = re.compile(r"[A-Za-z0-9@][A-Za-z0-9._/@-]{2,}")
_GITHUB = re.compile(r"github\.com[/:]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?=[/\s)\]\"'#?]|$)")
_MAX_TERMS = 3000


def normalize(alias: str) -> str:
    return alias.strip().strip("\"'").lower()


def alias_is_strong(alias: str) -> bool:
    """Forte = específico o bastante para casar por menção no texto (ex.: email-backend, @org/ui-kit).
    Fraco = curto ou palavra comum (email, functions, core)."""
    a = normalize(alias)
    return (len(a) >= 8 and bool(re.search(r"[-_./@]", a))) or len(a) >= 14


def repo_name_aliases(repo: str) -> list[tuple[str, str]]:
    """org__snr-email-backend -> org__snr-email-backend, org/snr-email-backend, snr-email-backend, email-backend."""
    out = [(repo.lower(), "name")]
    org, sep, short = repo.partition("__")
    if sep:
        out += [(f"{org}/{short}".lower(), "name"), (short.lower(), "name")]
        parts = short.split("-", 1)
        if len(parts) == 2 and len(parts[0]) <= 4 and parts[1]:  # prefixo curto: snr-, ms-, lib-...
            out.append((parts[1].lower(), "name"))
    return list(dict.fromkeys(out))


def manifest_aliases(filename: str, text: str) -> list[tuple[str, str]]:
    """Nomes de pacote/artefato/componente declarados num manifesto. Arquivo malformado → []."""
    try:
        names = _manifest_names(filename, text)
    except Exception:  # noqa: BLE001 - manifesto malformado não pode quebrar a indexação
        return []
    out: list[tuple[str, str]] = []
    for name, origin in names:
        n = normalize(name)
        if len(n) >= 3 and "${" not in n:
            out.append((n, origin))
            if n.startswith("@") and "/" in n:  # @escopo/pacote -> também pacote
                out.append((n.split("/", 1)[1], origin))
    return list(dict.fromkeys(out))


def _manifest_names(filename: str, text: str) -> list[tuple[str, str]]:
    if filename == "package.json":
        name = json.loads(text).get("name")
        return [(name, "package.json")] if isinstance(name, str) else []
    if filename == "pom.xml":
        root = ET.fromstring(text)
        ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
        el = root.find(f"{ns}artifactId")  # o do próprio projeto, não o do <parent>
        return [(el.text, "pom.xml")] if el is not None and el.text else []
    if filename == "pyproject.toml":
        data = tomllib.loads(text)
        name = data.get("project", {}).get("name") or data.get("tool", {}).get("poetry", {}).get("name")
        return [(name, "pyproject.toml")] if isinstance(name, str) else []
    if filename == "setup.cfg":
        cp = configparser.ConfigParser()
        cp.read_string(text)
        name = cp.get("metadata", "name", fallback=None)
        return [(name, "setup.cfg")] if name else []
    if filename == "go.mod":
        m = re.search(r"^module\s+(\S+)", text, re.MULTILINE)
        if not m:
            return []
        module = m.group(1)
        return [(module, "go.mod"), (module.rsplit("/", 1)[-1], "go.mod")]
    if filename == "Cargo.toml":
        name = tomllib.loads(text).get("package", {}).get("name")
        return [(name, "Cargo.toml")] if isinstance(name, str) else []
    if filename in ("settings.gradle", "settings.gradle.kts"):
        m = re.search(r"rootProject\.name\s*=\s*['\"]([^'\"]+)['\"]", text)
        return [(m.group(1), "gradle")] if m else []
    if filename in ("catalog-info.yaml", "catalog-info.yml"):
        out: list[tuple[str, str]] = []
        for doc in yaml.safe_load_all(text):
            if not isinstance(doc, Mapping):
                continue
            meta = doc.get("metadata") or {}
            if isinstance(meta.get("name"), str):
                out.append((meta["name"], "catalog"))
            for key, value in (meta.get("annotations") or {}).items():
                if isinstance(value, str) and key in (
                    "sonarqube.org/project-key", "argocd/app-name", "github.com/project-slug",
                    "backstage.io/kubernetes-id",
                ):
                    out.append((value, "catalog"))
        return out
    return []


# ------------------------------------------------------------------ páginas


def parse_front_matter(text: str) -> tuple[dict[str, Any], int]:
    """Front-matter gravado pelo conector (`---` ... `---`). Devolve (campos, nº de linhas do bloco)."""
    if not text.startswith("---\n"):
        return {}, 0
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, 0
    meta: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        key, sep, raw = line.partition(":")
        if not sep:
            continue
        raw = raw.strip()
        try:
            meta[key.strip()] = json.loads(raw)
        except ValueError:
            meta[key.strip()] = raw
    return meta, text[: end + 5].count("\n")


def blank_lines(text: str, count: int) -> str:
    """Esvazia as primeiras `count` linhas (mantém a numeração das linhas do arquivo)."""
    if count <= 0:
        return text
    lines = text.split("\n")
    return "\n".join([""] * min(count, len(lines)) + lines[count:])


def page_path(file_path: str) -> str:
    """'pt-br/Plataforma/Email.md' -> 'Plataforma/Email' (caminho da página na wiki, sem idioma)."""
    p = file_path[:-3] if file_path.endswith(".md") else file_path
    return p.split("/", 1)[1] if "/" in p else p


def glob_to_like(glob: str) -> str:
    """Glob de nome de repo → padrão LIKE. A consulta casa o padrão com o nome inteiro ou com o nome
    sem a org (`org__`), então 'snr-x' liga tanto o repo avulso 'snr-x' quanto 'qualquer-org__snr-x'."""
    g = glob.strip().lower()
    escaped = g.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return escaped.replace("*", "%").replace("?", "_")


def page_mentions(
    path: str,
    tags: Iterable[str],
    body: str,
    rules: Iterable[tuple[str, tuple[str, ...]]],
    path_matches: Any,
) -> list[tuple[str, str]]:
    """Menções de uma página: (termo, origem). `path_matches(path, globs)` aplica as regras explícitas."""
    out: list[tuple[str, str]] = []
    for rule_path, repos in rules:
        if path_matches(path, (rule_path,)):
            out += [(glob_to_like(r), "explicit") for r in repos]
    out += [(normalize(t), "tag") for t in tags if normalize(t)]
    for org, repo in _GITHUB.findall(body):
        out.append((f"{org}__{repo}".lower(), "url"))
    seen: set[str] = set()
    for m in _TERM.finditer(body):
        term = normalize(m.group(0)).rstrip(".")
        if term not in seen:
            seen.add(term)
            out.append((term, "mention"))
            if len(seen) >= _MAX_TERMS:
                break
    return list(dict.fromkeys(out))
