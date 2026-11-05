"""Arquivo repos.toml: [[repo]] avulsos (url git ou path local), [[org]] do GitHub e [[docs]] (wikis).

Formato igual ao do config.py antigo. Ao gravar, só saem os campos diferentes do padrão.
"""

from __future__ import annotations

import tomllib
from dataclasses import fields
from pathlib import Path
from typing import Any

from code_index.domain.models import DocLinkRule, DocSource, Org, Repo

_REPO_TUPLES = ("include", "exclude", "extra_exclude")
_ORG_TUPLES = ("match", "skip", "languages", "include", "exclude", "extra_exclude")
_DOC_TUPLES = ("include", "exclude")


def _tuples(entry: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Listas do TOML viram tuplas (os modelos são imutáveis e hasheáveis)."""
    entry = dict(entry)
    for key in keys:
        if key in entry:
            entry[key] = tuple(entry[key])
    return entry


def _toml_value(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(type(v))


def _toml_table(header: str, obj: Repo | Org) -> list[str]:
    defaults: Repo | Org = Repo(name=obj.name, path="x") if isinstance(obj, Repo) else Org(name=obj.name)
    lines = [f"[[{header}]]"]
    for f in fields(obj):
        if f.name in ("kind", "doc_source"):
            continue
        v = getattr(obj, f.name)
        if f.name == "name" or (v is not None and v != getattr(defaults, f.name)) or (
            f.name in ("url", "path") and v is not None
        ):
            lines.append(f"{f.name} = {_toml_value(v)}")
    return lines + [""]


def _doc_source(entry: dict[str, Any]) -> DocSource:
    entry = _tuples(entry, _DOC_TUPLES)
    entry["links"] = tuple(
        DocLinkRule(path=str(rule["path"]), repos=tuple(rule["repos"])) for rule in entry.get("links", [])
    )
    return DocSource(**entry)


def _docs_table(doc: DocSource) -> list[str]:
    defaults = DocSource(name=doc.name, url=doc.url)
    lines = ["[[docs]]", f"name = {_toml_value(doc.name)}", f"url = {_toml_value(doc.url)}"]
    for key in ("type", "locale", "include", "exclude"):
        value = getattr(doc, key)
        if value != getattr(defaults, key):
            lines.append(f"{key} = {_toml_value(value)}")
    if doc.links:
        rules = ", ".join(
            "{ path = " + _toml_value(r.path) + ", repos = " + _toml_value(r.repos) + " }" for r in doc.links
        )
        lines.append(f"links = [{rules}]")
    return lines + [""]


class ConfigFile:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return tomllib.loads(self.path.read_text(encoding="utf-8"))

    def load_repos(self) -> list[Repo]:
        repos = [Repo(**_tuples(e, _REPO_TUPLES)) for e in self._load().get("repo", [])]
        names = [r.name for r in repos]
        if len(names) != len(set(names)):
            raise ValueError(f"nomes duplicados em {self.path}")
        return repos

    def load_orgs(self) -> list[Org]:
        return [Org(**_tuples(e, _ORG_TUPLES)) for e in self._load().get("org", [])]

    def load_docs(self) -> list[DocSource]:
        docs = [_doc_source(e) for e in self._load().get("docs", [])]
        names = [d.name for d in docs]
        if len(names) != len(set(names)):
            raise ValueError(f"fontes de documentação com nomes duplicados em {self.path}")
        return docs

    def save(
        self,
        repos: list[Repo] | None = None,
        orgs: list[Org] | None = None,
        docs: list[DocSource] | None = None,
    ) -> None:
        """Grava o arquivo. `None` mantém o conteúdo atual daquela seção."""
        repos = self.load_repos() if repos is None else repos
        orgs = self.load_orgs() if orgs is None else orgs
        docs = self.load_docs() if docs is None else docs
        lines = [
            "# Gerenciado por `code-index repo ...` / `code-index org ...`, mas pode editar à mão.",
            "# [[repo]]: repositório avulso (url git ou path local). [[org]]: org do GitHub descoberta via API.",
            "",
        ]
        for o in orgs:
            lines += _toml_table("org", o)
        for r in repos:
            lines += _toml_table("repo", r)
        for d in docs:
            lines += _docs_table(d)
        self.path.write_text("\n".join(lines), encoding="utf-8")
