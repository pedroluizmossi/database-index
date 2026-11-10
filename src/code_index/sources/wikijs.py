"""Fonte de documentação: Wiki.js 2.x via API GraphQL.

Cada página publicada vira um arquivo `<locale>/<caminho>.md` na pasta da fonte, com front-matter
(título, URL original, tags, data). O pipeline de indexação lê essa pasta como um repo kind="docs".

Incremental (obrigatório): um manifesto guarda o `updatedAt` de cada página. A cada sync só a
lista de páginas é consultada; o conteúdo só é baixado para páginas novas ou alteradas, e os
arquivos de páginas removidas (ou que saíram dos filtros) são apagados.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import logging
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from markdownify import markdownify

from ..domain.doc_content import strip_embedded_blobs
from ..domain.doc_links import parse_front_matter
from ..domain.documents import WIKI_PAGE, DocumentMeta
from ..domain.models import DocSource
from ..settings import Settings
from .base import OWNER_MARKER, Linker  # noqa: F401  (OWNER_MARKER: compatibilidade de import)

log = logging.getLogger(__name__)

MANIFEST = ".manifest.json"   # começa com ponto: fica fora da indexação (padrão **/.*)
_CONCURRENCY = 4

_LIST_QUERY = """query ($locale: String) {
  pages { list(limit: 100000, orderBy: PATH, locale: $locale) {
    id path locale title updatedAt tags isPublished isPrivate
  } }
}"""
_PAGE_QUERY = """query ($id: Int!) {
  pages { single(id: $id) {
    id path locale title description content contentType updatedAt tags { tag }
  } }
}"""


@dataclass(frozen=True)
class PageMeta:
    id: int
    path: str
    locale: str
    title: str
    updated_at: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class DocSyncResult:
    total: int        # páginas que passaram nos filtros
    changed: int      # baixadas agora (novas ou alteradas)
    deleted: int      # arquivos apagados (página removida ou fora dos filtros)
    revision: str     # muda quando qualquer página muda: chave de memo do pipeline


class WikiJsError(RuntimeError):
    pass


class WikiJsClient:
    def __init__(self, base_url: str, token: str | None, *, http: httpx.AsyncClient | None = None) -> None:
        if not token:
            raise WikiJsError("defina WIKIJS_TOKEN (API key de leitura do Wiki.js) no .env")
        self._endpoint = base_url.rstrip("/") + "/graphql"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._http = http

    async def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        client = self._http or httpx.AsyncClient(timeout=60)
        try:
            r = await client.post(self._endpoint, json={"query": query, "variables": variables}, headers=self._headers)
        except httpx.HTTPError as e:
            raise WikiJsError(f"falha ao falar com o Wiki.js: {e}") from None
        finally:
            if self._http is None:
                await client.aclose()
        if r.status_code in (401, 403):
            raise WikiJsError("o Wiki.js recusou o token (WIKIJS_TOKEN inválido ou sem permissão de leitura)")
        if r.status_code != 200:
            raise WikiJsError(f"Wiki.js respondeu HTTP {r.status_code}")
        body = r.json()
        if body.get("errors"):
            msg = body["errors"][0].get("message", "erro desconhecido")
            if msg == "Forbidden":
                raise WikiJsError("o Wiki.js recusou o token (sem permissão para listar/ler páginas)")
            raise WikiJsError(f"erro do Wiki.js: {msg}")
        return body["data"]

    async def list_pages(self, locale: str) -> list[PageMeta]:
        data = await self._query(_LIST_QUERY, {"locale": locale})
        pages = []
        for p in data["pages"]["list"]:
            if not p.get("isPublished", True) or p.get("isPrivate"):
                continue
            pages.append(PageMeta(
                id=int(p["id"]), path=str(p["path"]), locale=str(p["locale"]), title=str(p.get("title") or p["path"]),
                updated_at=str(p["updatedAt"]), tags=tuple(str(t) for t in (p.get("tags") or [])),
            ))
        return pages

    async def get_page(self, page_id: int) -> dict[str, Any]:
        try:
            data = await self._query(_PAGE_QUERY, {"id": page_id})
        except WikiJsError as e:
            raise WikiJsError(f"não foi possível ler o conteúdo da página {page_id}; "
                              f"confira a permissão read:source: {e}") from e
        page = data["pages"]["single"]
        if page is None:
            raise WikiJsError(f"página {page_id} não encontrada")
        return dict(page)


def page_matches(path: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, g.replace("**/", "*")) or fnmatch.fnmatch(path, g) for g in globs)


def selected_pages(source: DocSource, pages: list[PageMeta]) -> list[PageMeta]:
    """Páginas que passam nos filtros include/exclude da fonte (glob sobre o caminho da página)."""
    return [p for p in pages if (not source.include or page_matches(p.path, source.include))
            and not page_matches(p.path, source.exclude)]


_GLOB_CHARS = re.compile(r"([*?\[])")


def _literal(path: str) -> str:
    """Caminho como glob que só casa ele mesmo (`*`, `?` e `[` do nome não viram curinga)."""
    return _GLOB_CHARS.sub(r"[\1]", path)


@dataclass
class _Node:
    path: str
    page: bool = False       # o caminho do nó é uma página (pasta também pode ser página)
    chosen: bool = False
    children: dict[str, _Node] = field(default_factory=dict)
    total: int = 0           # páginas na subárvore (inclusive o próprio nó)
    selected: int = 0


def _tree(paths: Iterable[str], chosen: Collection[str]) -> _Node:
    root = _Node("")
    for path in paths:
        node = root
        for part in path.split("/"):
            node = node.children.setdefault(part, _Node(f"{node.path}/{part}" if node.path else part))
        node.page, node.chosen = True, path in chosen

    def count(node: _Node) -> None:
        for child in node.children.values():
            count(child)
        node.total = int(node.page) + sum(c.total for c in node.children.values())
        node.selected = int(node.page and node.chosen) + sum(c.selected for c in node.children.values())

    count(root)
    return root


def _whole(node: _Node) -> list[str]:
    """Globs do nó inteiro: a página dele (se existe) e tudo abaixo, inclusive páginas futuras."""
    return ([_literal(node.path)] if node.page else []) + ([_literal(node.path) + "/**"] if node.children else [])


def _excludes(node: _Node) -> list[str]:
    """Nó já coberto por um include: exclui o que não foi escolhido (subpasta sem nada escolhido vira `Pasta/**`,
    que mantém de fora também as páginas futuras dela)."""
    if node.selected == 0:
        return _whole(node)
    out = [_literal(node.path)] if node.page and not node.chosen else []
    if node.children and node.selected == int(node.page and node.chosen):
        return out + [_literal(node.path) + "/**"]
    for child in node.children.values():
        out += _excludes(child)
    return out


def _rules(node: _Node) -> tuple[list[str], list[str]]:
    """Nó fora de qualquer include: a forma com menos regras entre incluir página a página (descendo) e incluir
    a pasta inteira excluindo o que sobra."""
    if node.selected == 0:
        return [], []
    if node.selected == node.total:
        return _whole(node), []
    own = [_literal(node.path)] if node.page and node.chosen else []
    inc, exc = list(own), []
    for child in node.children.values():
        ci, ce = _rules(child)
        inc += ci
        exc += ce
    # "Pasta/**" não casa a própria "Pasta": a página da pasta, se não foi escolhida, já fica de fora.
    folder_exc = [e for child in node.children.values() for e in _excludes(child)]
    folder = (own + [_literal(node.path) + "/**"], folder_exc)
    folder_n, pages_n = len(folder[0]) + len(folder[1]), len(inc) + len(exc)
    # Empate sem excludes: tudo abaixo da pasta foi escolhido, e a pasta inteira leva também as páginas futuras.
    return folder if folder_n < pages_n or (folder_n == pages_n and not folder_exc) else (inc, exc)


def selection_rules(paths: Iterable[str], chosen: Collection[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Regras include/exclude (globs de `selected_pages`) que escolhem exatamente `chosen` entre `paths`.

    Pasta escolhida inteira vira `Pasta` + `Pasta/**`: páginas novas dentro dela entram no próximo sync. Seções
    novas na raiz da wiki nunca entram sozinhas (o include nunca fica vazio, que significaria a wiki toda).
    """
    known = set(paths)
    unknown = set(chosen) - known
    if unknown:
        raise ValueError(f"páginas fora da lista: {', '.join(sorted(unknown)[:5])}")
    root = _tree(sorted(known), chosen)
    if root.selected == 0:
        raise ValueError("escolha ao menos uma página")
    include: list[str] = []
    exclude: list[str] = []
    for child in root.children.values():
        ci, ce = _rules(child)
        include += ci
        exclude += ce
    return tuple(include), tuple(exclude)


def _safe_relpath(locale: str, path: str) -> str | None:
    """`<locale>/<path>.md`, recusando caminhos que escapariam da pasta."""
    rel = PurePosixPath(locale) / f"{path.strip('/')}.md"
    if rel.is_absolute() or ".." in rel.parts or not path.strip("/"):
        return None
    return rel.as_posix()


def _yaml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # JSON é YAML válido e escapa aspas/quebras


def render_page(source: DocSource, page: dict[str, Any]) -> str:
    """Página → Markdown com front-matter. HTML (editor visual) é convertido para Markdown."""
    content = page.get("content") or ""
    if (page.get("contentType") or "markdown") == "html":
        content = markdownify(content, heading_style="ATX")
    content = strip_embedded_blobs(content)  # diagramas em base64 não vão para o disco nem para a Voyage
    tags = [t["tag"] for t in (page.get("tags") or []) if t.get("tag")]
    front = [
        "---",
        f"title: {_yaml_str(page.get('title') or page['path'])}",
        f"url: {_yaml_str(source.page_url(page['locale'], page['path']))}",
        f"page_id: {int(page['id'])}",
        f"updated_at: {_yaml_str(str(page.get('updatedAt') or ''))}",
        f"tags: {json.dumps(tags, ensure_ascii=False)}",
    ]
    if page.get("description"):
        front.append(f"description: {_yaml_str(page['description'])}")
    return "\n".join(front + ["---", "", content.strip(), ""])


def _load_manifest(dest: Path) -> dict[str, dict[str, str]]:
    try:
        return dict(json.loads((dest / MANIFEST).read_text(encoding="utf-8")).get("pages", {}))
    except (OSError, ValueError):
        return {}


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


async def sync_docs(
    source: DocSource, client: WikiJsClient, dest: Path, *, only: frozenset[str] | None = None,
) -> DocSyncResult:
    """Sincroniza a pasta `dest` com a wiki, baixando só o que mudou.

    `only`: caminhos de página (sem idioma) a sincronizar, dentro da seleção da fonte. Com ele, as outras
    páginas já baixadas ficam como estão: nada é apagado e o manifesto só muda nas páginas pedidas.
    """
    dest.mkdir(parents=True, exist_ok=True)
    old = _load_manifest(dest)
    pages = [
        p for p in await client.list_pages(source.locale)
        if (not source.include or page_matches(p.path, source.include)) and not page_matches(p.path, source.exclude)
    ]

    wanted: dict[str, tuple[PageMeta, str]] = {}
    for p in pages:
        rel = _safe_relpath(p.locale, p.path)
        if rel is None:
            log.warning("página %s com caminho inválido ignorada: %r", p.id, p.path)
            continue
        wanted[str(p.id)] = (p, rel)
    scope = wanted if only is None else {pid: v for pid, v in wanted.items() if v[0].path in only}

    to_fetch = [(p, rel) for pid, (p, rel) in scope.items()
                if old.get(pid, {}).get("updated_at") != p.updated_at
                or old.get(pid, {}).get("file") != rel or not (dest / rel).exists()]

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def fetch(p: PageMeta, rel: str) -> None:
        async with sem:
            page = await client.get_page(p.id)
        _write_atomic(dest / rel, render_page(source, page))

    await asyncio.gather(*(fetch(p, rel) for p, rel in to_fetch))

    deleted = 0
    if only is None:
        # Remove o que saiu: páginas apagadas, fora dos filtros, ou que mudaram de caminho.
        keep = {rel for _p, rel in wanted.values()}
        for pid, entry in old.items():
            file = entry.get("file", "")
            if file and file not in keep and (dest / file).is_file():
                (dest / file).unlink()
                deleted += 1
        manifest = {pid: {"updated_at": p.updated_at, "file": rel} for pid, (p, rel) in wanted.items()}
    else:
        manifest = dict(old)
        manifest.update({pid: {"updated_at": p.updated_at, "file": rel} for pid, (p, rel) in scope.items()})
    _write_atomic(dest / MANIFEST, json.dumps({"pages": manifest}, ensure_ascii=False, indent=1))
    digest = hashlib.sha256(json.dumps(sorted(manifest.items())).encode()).hexdigest()[:16]
    return DocSyncResult(total=len(scope), changed=len(to_fetch), deleted=deleted, revision=f"docs:{digest}")


# ------------------------------------------------------------------ plugin


def _read_front_matter(path: Path) -> dict[str, Any]:
    """Só o cabeçalho `---` ... `---` (sem ler a página inteira)."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            head = fh.read(8192)
    except OSError:
        return {}
    return parse_front_matter(head)[0]


class WikiJsPlugin:
    """Wiki.js 2.x via GraphQL: páginas publicadas → `<locale>/<caminho>.md` com front-matter."""

    type = "wikijs"
    label = "Wiki.js"

    async def fetch(self, source: DocSource, dest: Path, settings: Settings, *,
                    only: frozenset[str] | None = None) -> str:
        token = settings.wikijs_token.get_secret_value() if settings.wikijs_token else None
        log.info("sync de documentação %s (%s)", source.repo_name, source.url)
        result = await sync_docs(source, WikiJsClient(source.url, token), dest, only=only)
        log.info("%s: %d páginas, %d baixadas, %d removidas", source.repo_name, result.total, result.changed,
                 result.deleted)
        return result.revision

    async def preview(self, source: DocSource, settings: Settings, *, limit: int) -> dict[str, Any]:
        """Páginas que entrariam, agrupadas pela raiz (lista a wiki; não baixa conteúdo)."""
        token = settings.wikijs_token.get_secret_value() if settings.wikijs_token else None
        pages = await WikiJsClient(source.url, token).list_pages(source.locale)
        selected = sorted(selected_pages(source, pages), key=lambda p: p.path.lower())
        roots: dict[str, int] = {}
        for p in selected:
            root = p.path.split("/", 1)[0]
            roots[root] = roots.get(root, 0) + 1
        cap = max(1, min(limit, 1000))
        return {
            "name": source.repo_name, "url": source.url, "locale": source.locale, "total": len(selected),
            "pages_in_locale": len(pages),
            "roots": [{"root": r, "pages": n} for r, n in sorted(roots.items(), key=lambda kv: kv[0].lower())],
            "pages": [{"path": p.path, "title": p.title, "url": source.page_url(p.locale, p.path),
                       "tags": list(p.tags), "updated_at": p.updated_at} for p in selected[:cap]],
            "truncated": len(selected) > cap,
        }

    def documents(self, source: DocSource, dest: Path) -> list[DocumentMeta]:
        out: list[DocumentMeta] = []
        for entry in _load_manifest(dest).values():
            rel = entry.get("file", "")
            if not rel or not (dest / rel).is_file():
                continue
            meta = _read_front_matter(dest / rel)
            folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
            out.append(DocumentMeta(
                path=rel, type=WIKI_PAGE, title=str(meta.get("title") or rel), url=str(meta.get("url") or ""),
                tags=tuple(str(t) for t in meta.get("tags") or []), updated_at=str(meta.get("updated_at") or ""),
                attrs={"page_id": meta.get("page_id"), "folder": folder},
            ))
        return out

    def linkers(self) -> list[Linker]:
        return []  # menções, tags e regras valem para qualquer fonte de texto (linkers do núcleo)
