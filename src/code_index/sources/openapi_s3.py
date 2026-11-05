"""Descoberta de especificações Swagger públicas e extração de operações."""

from __future__ import annotations

import json
import hashlib
import logging
import posixpath
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urldefrag, urljoin, urlsplit

import httpx
import yaml

from ..domain.models import DocSource
from ..settings import Settings

log = logging.getLogger(__name__)
_RENDER_VERSION = 3

_NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
_KEY = re.compile(r"^([a-zA-Z0-9-]+)_([a-zA-Z0-9_-]+)\.swagger\.(yaml|yml|json)$")
_METHODS = frozenset(("get", "post", "put", "patch", "delete", "head", "options", "trace"))


def _load_yaml(content: str | bytes, name: str) -> object:
    """Corrige apenas tabulações cuja posição é inequívoca nos Swagger publicados."""
    current = content
    repaired = False
    while True:
        try:
            result = yaml.safe_load(current)
            if repaired:
                log.warning("tabulações YAML normalizadas em %s", name)
            return result
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            problem = getattr(exc, "problem", "") or ""
            if isinstance(exc, yaml.scanner.ScannerError) and mark and "character '\\t'" in problem:
                try:
                    text = current.decode("utf-8-sig") if isinstance(current, bytes) else current
                except UnicodeDecodeError:
                    text = ""
                lines = text.splitlines(keepends=True)
                if mark.line < len(lines):
                    line = lines[mark.line]
                    before = line[:mark.column]
                    after = line[mark.column:]
                    if (re.fullmatch(r" *[^#\r\n]+", before)
                            and after.startswith("\t") and not after.strip()):
                        lines[mark.line] = before.rstrip(" ") + ("\n" if line.endswith("\n") else "")
                        current = "".join(lines)
                        repaired = True
                        continue
                    # Alguns arquivos alinham respostas 401/default com tabs. A resposta
                    # anterior determina a coluna, preservando o recuo relativo do bloco.
                    status = re.match(r"^( *)(\t+)( *)(?:[1-5][0-9]{2}|default):[ \t]*(?:\r?\n)?$", line)
                    if status:
                        parent = None
                        response_line = 0
                        for index in range(mark.line - 1, max(-1, mark.line - 101), -1):
                            prior = lines[index]
                            found = re.fullmatch(r"( *)responses:[ \t]*(?:\r?\n)?", prior)
                            if found:
                                parent = len(found[1])
                                response_line = index
                                break
                        expected = parent + 2 if parent is not None else 0
                        prefix_spaces = len(status[1]) + len(status[3])
                        replacement = expected - prefix_spaces
                        peers = lines[response_line + 1:mark.line]
                        has_peer = any(re.match(rf"^ {{{expected}}}(?:[1-5][0-9]{{2}}|default):", p) for p in peers)
                        if parent is not None and replacement > 0 and has_peer:
                            prefix = status[1] + status[2]
                            for index in range(mark.line, len(lines)):
                                if not lines[index].startswith(prefix):
                                    break
                                lines[index] = status[1] + " " * replacement + lines[index][len(prefix):]
                            current = "".join(lines)
                            repaired = True
                            continue
            location = f" (linha {mark.line + 1}, coluna {mark.column + 1})" if mark else ""
            raise ValueError(f"YAML inválido em {name}{location}: {problem or 'erro de sintaxe'}") from exc


@dataclass(frozen=True)
class SwaggerObject:
    key: str
    domain: str
    service: str
    etag: str
    modified: str
    size: int
    bucket_url: str = "https://s3.amazonaws.com/br-com-senior-ecossistema-api"

    @property
    def url(self) -> str:
        return f"{self.bucket_url.rstrip('/')}/{quote(self.key, safe='')}"


async def list_objects(client: httpx.AsyncClient, bucket_url: str = "https://s3.amazonaws.com/br-com-senior-ecossistema-api") -> list[SwaggerObject]:
    """Lista todas as páginas do bucket, sem baixar ou indexar os arquivos."""
    items: list[SwaggerObject] = []
    token: str | None = None
    seen: set[str] = set()
    while True:
        params = {"list-type": "2", "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        response = await client.get(bucket_url, params=params)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        for entry in root.findall("s:Contents", _NS):
            key = entry.findtext("s:Key", default="", namespaces=_NS)
            match = _KEY.fullmatch(key)
            if match:
                items.append(SwaggerObject(key, match[1], match[2],
                    entry.findtext("s:ETag", default="", namespaces=_NS).strip('"'),
                    entry.findtext("s:LastModified", default="", namespaces=_NS),
                    int(entry.findtext("s:Size", default="0", namespaces=_NS)), bucket_url))
        if root.findtext("s:IsTruncated", default="false", namespaces=_NS) != "true":
            return items
        token = root.findtext("s:NextContinuationToken", namespaces=_NS)
        if not token or token in seen:
            raise ValueError("paginação inválida na listagem do bucket")
        seen.add(token)


def select_objects(items: list[SwaggerObject], domain: str, services: list[str] | None = None) -> list[SwaggerObject]:
    """Seleciona um domínio ou serviços exatos, recusando nomes desconhecidos."""
    matching = [item for item in items if item.domain == domain]
    if not matching:
        raise ValueError(f"domínio não encontrado: {domain}")
    if services is not None:
        if not services:
            raise ValueError("selecione ao menos um serviço ou o domínio inteiro")
        missing = set(services) - {item.service for item in matching}
        if missing:
            raise ValueError(f"serviços não encontrados em {domain}: {', '.join(sorted(missing))}")
        matching = [item for item in matching if item.service in services]
    return matching


def _references(value: object) -> list[str]:
    """Encontra objetos $ref, inclusive em estruturas com aliases YAML cíclicos."""
    result: list[str] = []
    seen: set[int] = set()
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, (dict, list)):
            if id(node) in seen:
                continue
            seen.add(id(node))
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                result.append(ref)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return result


def _pointer(doc: object, fragment: str) -> object:
    if not fragment:
        return doc
    pointer = unquote(fragment)
    if not pointer.startswith("/"):
        raise ValueError("fragmento não é JSON Pointer")
    current = doc
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list) and key.isdecimal() and int(key) < len(current):
            current = current[int(key)]
        else:
            raise ValueError("caminho não encontrado")
    return current


def _allowed_url(url: str, bucket_url: str) -> bool:
    target, bucket = urlsplit(url), urlsplit(bucket_url)
    path = unquote(target.path)
    normalized = posixpath.normpath(path)
    root = posixpath.normpath(unquote(bucket.path))
    return (target.scheme == "https" and target.scheme == bucket.scheme and target.netloc == bucket.netloc
            and not target.username and not target.password and not target.query
            and path == normalized and (normalized == root or normalized.startswith(root.rstrip("/") + "/")))


def _resolved(value: object, doc: object, item: SwaggerObject, external: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    docs = {item.url: doc, **external}
    pending = [(item.url, ref) for ref in _references(value)]
    seen: set[tuple[str, str]] = set()
    resolved: dict[str, object] = {}
    issues: list[str] = []
    while pending:
        base, ref = pending.pop()
        url, fragment = urldefrag(urljoin(base, ref))
        key = (url, fragment)
        if key in seen:
            continue
        seen.add(key)
        label = f"#{fragment}" if url == item.url else f"{url}#{fragment}"
        if url != item.url and not _allowed_url(url, item.bucket_url):
            issues.append(f"{label}: referência externa não permitida")
            continue
        if url not in docs:
            issues.append(f"{label}: documento externo indisponível ou limite excedido")
            continue
        try:
            target = _pointer(docs[url], fragment)
        except ValueError as exc:
            issues.append(f"{label}: {exc}")
            continue
        resolved[label] = target
        pending.extend((url, child) for child in _references(target))
    return resolved, sorted(issues)


def render_operations(content: str, item: SwaggerObject, external: dict[str, object] | None = None) -> list[tuple[str, str]]:
    """Converte operações completas e inclui os alvos de referências alcançáveis."""
    doc = _load_yaml(content, item.key)
    if not isinstance(doc, dict) or str(doc.get("swagger")) != "2.0":
        raise ValueError(f"Swagger 2.0 inválido: {item.key}")
    info = doc.get("info") or {}
    if not isinstance(info, dict) or not isinstance(doc.get("paths"), dict):
        raise ValueError(f"Swagger sem info ou paths válidos: {item.key}")
    result: list[tuple[str, str]] = []
    for path, methods in doc["paths"].items():
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            if method.lower() not in _METHODS or not isinstance(operation, dict):
                continue
            title = f"{method.upper()} {path}"
            front = ["---", f"title: {json.dumps(title, ensure_ascii=False)}",
                     f"url: {json.dumps(item.url)}", f"tags: {json.dumps([item.service])}", "---"]
            details = dict(operation)
            if "security" not in details and doc.get("security"):
                details["security"] = doc["security"]
            path_parameters = methods.get("parameters")
            references, issues = _resolved([details, path_parameters], doc, item, external or {})
            description = str(info.get("description") or "")
            summary = next((line.strip() for line in description.splitlines() if line.strip()), "")
            body = "\n".join(front + [f"# {title}", f"Domínio: {item.domain}",
                f"Serviço: {item.service}", f"Base: {doc.get('basePath', '')}",
                f"Descrição da API: {summary}", "", "```yaml",
                yaml.safe_dump(details, allow_unicode=True, sort_keys=False).rstrip(), "```", ""])
            if path_parameters:
                body += "\n## Parâmetros do caminho\n\n```yaml\n" + yaml.safe_dump(
                    {"path_parameters": path_parameters}, allow_unicode=True, sort_keys=False).rstrip() + "\n```\n"
            if references:
                body += "\n## Referências resolvidas\n\n```yaml\n" + yaml.safe_dump(
                    references, allow_unicode=True, sort_keys=True).rstrip() + "\n```\n"
            if issues:
                body += "\n## Referências não resolvidas\n\n" + "\n".join(f"- {issue}" for issue in issues) + "\n"
            result.append((title, body))
    return result


def render_overview(content: str, item: SwaggerObject) -> str | None:
    """A descrição integral aparece uma vez por serviço."""
    doc = _load_yaml(content, item.key)
    info = doc.get("info") if isinstance(doc, dict) else None
    description = info.get("description") if isinstance(info, dict) else None
    if not isinstance(description, str) or not description.strip():
        return None
    title = f"Visão geral: {item.domain}/{item.service}"
    return "\n".join(["---", f"title: {json.dumps(title, ensure_ascii=False)}",
                      f"url: {json.dumps(item.url)}", f"tags: {json.dumps([item.service])}", "---",
                      f"# {title}", f"Domínio: {item.domain}", f"Serviço: {item.service}",
                      "", description.strip(), ""])


async def _external_documents(client: httpx.AsyncClient, content: str, item: SwaggerObject,
                              settings: Settings) -> tuple[dict[str, object], bool]:
    """Busca apenas documentos HTTPS no bucket configurado, sem seguir redirecionamentos."""
    root = _load_yaml(content, item.key)
    docs: dict[str, object] = {}
    pending = [(item.url, ref) for ref in _references(root)]
    visited = {item.url}
    has_external = False
    while pending:
        base, ref = pending.pop()
        url, _ = urldefrag(urljoin(base, ref))
        if url == base:
            continue
        has_external = True
        if url in visited or not _allowed_url(url, item.bucket_url):
            continue
        visited.add(url)
        if len(visited) - 1 > settings.openapi_ref_max_documents:
            continue
        try:
            async with client.stream("GET", url, follow_redirects=False) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > settings.openapi_ref_max_bytes:
                        raise ValueError("documento externo excede o limite de tamanho")
            document = _load_yaml(bytes(body), url)
            docs[url] = document
            pending.extend((url, child) for child in _references(document))
        except (httpx.HTTPError, ValueError, yaml.YAMLError) as exc:
            log.warning("não foi possível carregar referência externa %s (%s)", url, exc)
    return docs, has_external


async def sync_source(source: DocSource, dest: Path, settings: Settings, only: frozenset[str] | None = None) -> str:
    """Baixa os selecionados e só altera arquivos depois de validar todos os documentos."""
    manifest_path = dest / ".openapi-manifest.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
        items = select_objects(await list_objects(client, source.url), source.locale,
                               list(only) if only is not None else list(source.include) or None)
        # Seleção parcial preserva os outros serviços já indexados.
        selected_keys = {item.key for item in items}
        new: dict[str, dict] = {key: value for key, value in old.items() if key not in selected_keys} if only else {}
        writes: dict[str, str] = {}
        for item in items:
            previous = old.get(item.key, {})
            if (item.etag and previous.get("etag") == item.etag and previous.get("render_version") == _RENDER_VERSION
                    and not previous.get("external_refs") and all((dest / p).is_file() for p in previous.get("files", []))):
                new[item.key] = previous
                continue
            response = await client.get(item.url)
            response.raise_for_status()
            external, has_external = await _external_documents(client, response.text, item, settings)
            rendered = render_operations(response.text, item, external)
            files = []
            content_hash = hashlib.sha256()
            for title, text in rendered:
                if len(text.encode()) > settings.max_file_bytes:
                    raise ValueError(f"operação {title} de {item.key} excede o limite de indexação; "
                                     "nenhuma alteração foi aplicada à fonte")
                digest = hashlib.sha256(title.encode()).hexdigest()[:20]
                path = f"{item.domain}/{item.service}/{digest}.md"
                writes[path] = text
                files.append(path)
                content_hash.update(path.encode())
                content_hash.update(b"\0")
                content_hash.update(text.encode())
                content_hash.update(b"\0")
            overview = render_overview(response.text, item)
            if overview is not None:
                if len(overview.encode()) > settings.max_file_bytes:
                    raise ValueError(f"visão geral de {item.key} excede o limite de indexação")
                path = f"{item.domain}/{item.service}/__overview.md"
                writes[path] = overview
                files.append(path)
                content_hash.update(path.encode() + b"\0" + overview.encode() + b"\0")
            new[item.key] = {"etag": item.etag, "files": files, "render_version": _RENDER_VERSION,
                             "external_refs": has_external, "content_hash": content_hash.hexdigest()}
    for path, content in writes.items():
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    old_files = {p for value in old.values() for p in value.get("files", [])}
    new_files = {p for value in new.values() for p in value.get("files", [])}
    for path in old_files - new_files:
        target = (dest / path).resolve()
        if target.is_relative_to(dest.resolve()):
            target.unlink(missing_ok=True)
    raw = json.dumps(new, ensure_ascii=False, sort_keys=True)
    manifest_path.write_text(raw, encoding="utf-8")
    return hashlib.sha256(raw.encode()).hexdigest()
