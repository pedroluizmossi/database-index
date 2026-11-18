"""CLI remota: cliente HTTP de /api/v1 com os mesmos métodos que os comandos usam do `CodeIndexService`.

Com uma URL configurada (CODE_INDEX_URL + CODE_INDEX_TOKEN, ou `code-index login`), `open_backend` (common.py)
entrega um `RemoteService` no lugar do serviço local: os comandos não mudam e a saída é a mesma. Nada aqui é regra
de negócio: cada método é uma rota que chama o mesmo método do serviço no servidor (scope e auditoria lá).

Segurança: o token só vai no header Authorization (nunca na URL, em log ou na saída) e fica em
~/.config/code-index/credentials.toml com permissão 0600.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import sys
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from ...domain.models import DocLinkRule, DocSource, GrepMatch, Hit, RepoStatus, SymbolHit, SyncResult
from ...settings import Settings

API = "/api/v1"
_LOOPBACK_NAMES = {"localhost", "localhost.localdomain"}
_ACTIVE_JOB = ("queued", "running")


class RemoteError(RuntimeError):
    """Falha falando com o servidor (HTTP ou rede), já com a mensagem para a pessoa. `status` None = rede."""

    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def warn(message: str) -> None:
    print(f"aviso: {message}", file=sys.stderr)


# ---------------------------------------------------------------- URL e credenciais

def normalize_url(raw: str) -> str:
    """Valida a URL do servidor e devolve a base (sem barra final nem /api/v1). Recusa credencial na URL."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("informe a URL do servidor, ex.: --url https://code-index.exemplo.com")
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"URL inválida: {raw!r} (use http(s)://host[:porta])")
    if parts.username or parts.password or parts.query or parts.fragment or "cix_" in raw:
        raise ValueError("a URL não pode levar token, usuário, ?parâmetros nem #fragmento: o token vai só no header "
                         "(--token, entrada padrão ou CODE_INDEX_TOKEN)")
    path = parts.path.rstrip("/")
    if path.endswith(API):
        path = path[: -len(API)]
    return f"{parts.scheme}://{parts.netloc}{path}"


def is_loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def insecure(url: str) -> bool:
    """http:// fora da própria máquina: o token trafega sem TLS."""
    return url.startswith("http://") and not is_loopback(url)


def warn_insecure(url: str) -> None:
    """Avisa (stderr) quando o token vai trafegar sem TLS."""
    if insecure(url):
        warn(f"{url} usa http:// fora desta máquina: o token trafega sem TLS (use https://)")


@dataclass(frozen=True)
class Credentials:
    url: str
    token: str = field(repr=False)


def load_credentials(path: Path) -> Credentials | None:
    """Lê o arquivo do `code-index login` (None se não existe). Avisa se outros usuários podem lê-lo."""
    path = path.expanduser()
    if not path.is_file():
        return None
    if os.name == "posix" and path.stat().st_mode & 0o077:
        warn(f"{path} pode ser lido por outros usuários: rode `chmod 600 {path}`")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ValueError(f"arquivo de credenciais ilegível ({path}): {e}; rode `code-index login` de novo") from None
    url, token = data.get("url"), data.get("token")
    if not isinstance(url, str) or not isinstance(token, str) or not url or not token:
        raise ValueError(f"arquivo de credenciais incompleto ({path}): rode `code-index login` de novo")
    return Credentials(url=url, token=token)


def _toml_str(value: str) -> str:
    # JSON com ensure_ascii é uma string básica válida em TOML (\", \\, \n, \uXXXX)
    return json.dumps(value, ensure_ascii=True)


def save_credentials(path: Path, creds: Credentials) -> Path:
    """Grava url e token com permissão 0600 (pasta 0700), por troca atômica: nunca fica um arquivo legível."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    text = ("# code-index: credenciais da CLI remota (code-index login). Não compartilhe este arquivo.\n"
            f"url = {_toml_str(creds.url)}\n"
            f"token = {_toml_str(creds.token)}\n")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def delete_credentials(path: Path) -> bool:
    path = path.expanduser()
    if not path.exists():
        return False
    path.unlink()
    return True


@dataclass(frozen=True)
class RemoteTarget:
    url: str
    token: str = field(repr=False)
    origin: str = "ambiente"   # de onde veio a configuração: ambiente | arquivo


def resolve_target(settings: Settings) -> RemoteTarget | None:
    """Modo da CLI: None = local (abre o banco). URL e token do ambiente vencem o arquivo do `code-index login`;
    o token do arquivo só vale para a URL gravada com ele."""
    if settings.cli_mode == "local":
        return None
    creds = load_credentials(settings.credentials_file)
    raw_url = settings.remote_url or (creds.url if creds else None)
    if not raw_url:
        if settings.cli_mode == "remote":
            raise ValueError("CODE_INDEX_CLI_MODE=remote sem servidor: rode `code-index login --url URL` "
                             "ou defina CODE_INDEX_URL")
        return None
    url = normalize_url(raw_url)
    if settings.remote_token is not None:
        token = settings.remote_token.get_secret_value()
    elif creds is not None and normalize_url(creds.url) == url:
        token = creds.token
    else:
        raise ValueError(f"sem token para {url}: rode `code-index login --url {url}` ou defina CODE_INDEX_TOKEN")
    origin = "ambiente" if settings.remote_url else "arquivo"
    return RemoteTarget(url=url, token=token.strip(), origin=origin)


# ---------------------------------------------------------------- conversões (JSON -> objetos que a CLI formata)

def _build(cls: Any, data: dict[str, Any]) -> Any:
    """Monta a dataclass com as chaves conhecidas (servidor mais novo pode mandar campos a mais)."""
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


def _dt(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) and value else None


@dataclass
class RemoteToken:
    """Metadados de token como a CLI os mostra (a API devolve `prefix` e datas ISO)."""

    id: str
    name: str
    owner_email: str
    token_prefix: str
    scopes: tuple[str, ...]
    active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RemoteToken:
        return cls(id=d["id"], name=d["name"], owner_email=d.get("owner_email") or "", token_prefix=d.get("prefix", ""),
                   scopes=tuple(d.get("scopes") or ()), active=bool(d.get("active")),
                   expires_at=_dt(d.get("expires_at")), last_used_at=_dt(d.get("last_used_at")),
                   revoked_at=_dt(d.get("revoked_at")))


@dataclass
class RemoteGrep:
    matches: list[GrepMatch]
    truncated: bool


def _doc_source(d: dict[str, Any]) -> DocSource:
    return DocSource(name=d["name"], url=d.get("url") or "", type=d.get("type") or "wikijs",
                     locale=d.get("locale") or "pt-br", include=tuple(d.get("include") or ()),
                     exclude=tuple(d.get("exclude") or ()),
                     links=tuple(DocLinkRule(path=r["path"], repos=tuple(r["repos"])) for r in d.get("links") or ()))


def _params(**kw: Any) -> dict[str, Any]:
    """Query string sem os None (o httpx mandaria vazio)."""
    return {k: v for k, v in kw.items() if v is not None}


def _seg(value: str) -> str:
    return quote(value, safe="")


# ---------------------------------------------------------------- progresso de job (stderr)

class JobProgress:
    """Mostra na saída de erro o andamento de um job de sync (a saída padrão fica com o resultado)."""

    def __init__(self, stream: Any = None) -> None:
        self._stream = stream or sys.stderr
        self._tty = bool(getattr(self._stream, "isatty", lambda: False)())
        self._last = ""
        self._last_key = ""
        self._last_at = 0.0

    def __call__(self, job: dict[str, Any]) -> None:
        line = self.describe(job)
        now = time.monotonic()
        if not line or line == self._last:
            return
        # Fora de terminal (log, CI): linha nova quando muda a etapa ou o nº de repos; só arquivos, a cada 10 s.
        key = line.split(", ")[0]
        if not self._tty and key == self._last_key and now - self._last_at < 10:
            return
        self._last, self._last_key, self._last_at = line, key, now
        if self._tty:
            print(f"\r\033[K{line}", end="", file=self._stream, flush=True)
        else:
            print(line, file=self._stream, flush=True)

    def done(self) -> None:
        if self._tty and self._last:
            print(file=self._stream, flush=True)

    @staticmethod
    def describe(job: dict[str, Any]) -> str:
        if job["status"] == "queued":
            return f"job {job['id']}: na fila do worker..."
        if job["status"] != "running":
            return ""
        p = job.get("progress") or {}
        if not p:
            return f"job {job['id']}: começando..."
        files = f", {p.get('files_done', 0)}/{p['files_total']} arquivos" if p.get("files_total") else ""
        return (f"job {job['id']}: {p.get('phase_label') or p.get('phase', '')} "
                f"({p.get('repos_done', 0)}/{p.get('repos_total', 0)} repos{files})")


# ---------------------------------------------------------------- cliente

class RemoteService:
    """Mesmos métodos (nomes e argumentos) que a CLI usa do `CodeIndexService`, via /api/v1."""

    transport: httpx.AsyncBaseTransport | None = None   # testes: httpx.MockTransport ou ASGITransport
    poll_seconds: float = 2.0                           # intervalo do acompanhamento de jobs

    def __init__(self, target: RemoteTarget, *, timeout: float = 60.0,
                 progress: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.target = target
        self._timeout = timeout
        self._progress = progress
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> RemoteService:
        self._http = httpx.AsyncClient(
            base_url=self.target.url, timeout=self._timeout, transport=self.transport, follow_redirects=False,
            headers={"Authorization": f"Bearer {self.target.token}", "Accept": "application/json",
                     "User-Agent": "code-index-cli"},
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------ HTTP

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None,
                      content: bytes | None = None, headers: dict[str, str] | None = None,
                      timeout: float | None = None) -> httpx.Response:
        assert self._http is not None, "use `async with RemoteService(...)`"
        url = self.target.url
        try:
            r = await self._http.request(method, f"{API}{path}", params=params, json=json, content=content,
                                         headers=headers, timeout=timeout if timeout is not None else self._timeout)
        except httpx.TimeoutException:
            raise RemoteError(f"o servidor {url} não respondeu a tempo (CODE_INDEX_REMOTE_TIMEOUT)") from None
        except httpx.TransportError as e:
            raise RemoteError(f"não foi possível conectar a {url}: {e.__class__.__name__}: {e}") from None
        if r.status_code >= 300:
            raise self._error(r)
        return r

    def _error(self, r: httpx.Response) -> RemoteError:
        code, url = r.status_code, self.target.url
        detail = _detail(r)
        if 300 <= code < 400:
            loc = r.headers.get("location", "")
            return RemoteError(f"o servidor redirecionou para {loc or '?'}: confira a URL (https:// e o endereço "
                               f"da API) com `code-index login --url`", status=code)
        if code == 401:
            return RemoteError(f"não autenticado em {url} ({detail or 'token recusado'}): rode "
                               f"`code-index login --url {url}` com um token válido", status=code)
        if code == 403:
            return RemoteError(f"sem permissão: {detail or 'o token não tem o scope necessário'}", status=code)
        if code == 429:
            retry = _retry_after(r)
            wait = f" Tente de novo em {retry:.0f} s." if retry is not None else " Tente de novo em instantes."
            return RemoteError(f"limite de requisições atingido ({detail or 'rate limit'}).{wait}",
                               status=code, retry_after=retry)
        if code >= 500:
            what = "servidor indisponível" if code in (502, 503, 504) else "erro no servidor"
            return RemoteError(f"{what} (HTTP {code}{': ' + detail if detail else ''}). Tente de novo; se "
                               "persistir, veja os logs do servidor.", status=code)
        if code == 404 and not detail:
            return RemoteError(f"rota não encontrada em {url} (HTTP 404): o servidor é de outra versão?", status=code)
        return RemoteError(detail or f"pedido recusado (HTTP {code})", status=code)

    async def call(self, method: str, path: str, **kw: Any) -> Any:
        r = await self.request(method, path, **kw)
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            raise RemoteError(f"resposta inesperada de {self.target.url}{API}{path} (não é JSON): confira a URL") from None

    async def _get(self, path: str, **params: Any) -> Any:
        return await self.call("GET", path, params=_params(**params))

    async def _or_none_on_404(self, method: str, path: str, **kw: Any) -> Any:
        try:
            return await self.call(method, path, **kw)
        except RemoteError as e:
            if e.status == 404:
                return None
            raise

    # ------------------------------------------------ quem sou eu / auditoria (servidor audita)

    async def me(self) -> dict[str, Any]:
        result: dict[str, Any] = await self.call("GET", "/me")
        return result

    async def audit(self, *_: Any, **__: Any) -> None:
        """No modo remoto a rota já audita com o principal do token."""

    # ------------------------------------------------ orgs e repos

    async def add_org(self, name: str, *, match: Any = (), skip: Any = (), languages: Any = (),
                      include_archived: bool = False, include_forks: bool = False, max_size_mb: int | None = None,
                      extra_exclude: Any = ()) -> dict[str, Any]:
        return await self.call("POST", "/orgs", json={
            "name": name, "match": list(match), "skip": list(skip), "languages": list(languages),
            "include_archived": include_archived, "include_forks": include_forks, "max_size_mb": max_size_mb,
            "extra_exclude": list(extra_exclude)})

    async def set_org(self, name: str, *, languages: list[str] | None = None, match: list[str] | None = None,
                      skip: list[str] | None = None, max_size_mb: int | None = None) -> dict[str, Any]:
        body = _params(languages=languages, match=match, skip=skip, max_size_mb=max_size_mb)
        return await self.call("PATCH", f"/orgs/{_seg(name)}", json=body)

    async def remove_org(self, name: str, *, keep_clones: bool = False) -> int:
        res = await self.call("DELETE", f"/orgs/{_seg(name)}", params={"keep_clones": keep_clones})
        return int(res["removed_repos"])

    async def refresh_orgs(self, name: str | None = None) -> dict[str, Any]:
        return await self.call("POST", "/orgs/refresh", params=_params(name=name))

    async def org_summary(self) -> list[dict[str, Any]]:
        return await self._get("/orgs")

    async def add_repo(self, source: str, *, name: str | None = None, branch: str | None = None,
                       include: list[str] | None = None, extra_exclude: list[str] | None = None) -> SimpleNamespace:
        res = await self.call("POST", "/repos", json={"source": source, "name": name, "branch": branch,
                                                       "include": include, "extra_exclude": extra_exclude})
        return SimpleNamespace(**res)

    async def remove_repo(self, name: str, *, keep_clone: bool = False) -> str:
        res = await self.call("DELETE", f"/repos/{_seg(name)}", params={"keep_clone": keep_clone})
        return str(res["result"])

    async def unignore_repo(self, name: str) -> None:
        await self.call("POST", f"/repos/{_seg(name)}/unignore")

    async def list_repos(self, *, status: str | None = None, org: str | None = None,
                         name: str | None = None) -> list[RepoStatus]:
        rows = await self._get("/repos", status=status, org=org, name=name)
        return [_build(RepoStatus, r) for r in rows]

    async def find_repos(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        return await self._get("/repos/find", q=query, limit=limit)

    async def skipped_files(self, repo: str | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
        return await self._get("/skipped", repo=repo, limit=limit)

    async def repo_card(self, name: str) -> dict[str, Any] | None:
        try:
            return await self.call("GET", f"/repos/{_seg(name)}/card")
        except RemoteError as e:
            if e.status == 404 and str(e).startswith("sem cartão"):
                return None
            raise

    # ------------------------------------------------ sync (fila do worker)

    async def start_sync(self, repos: list[str] | None = None, *, batch: int | None | str = "default",
                         do_discover: bool = True, pull: bool = True, full: bool = False,
                         pages: list[str] | None = None, dry_run: bool = False,
                         only_changed: bool = False) -> dict[str, Any]:
        """POST /sync. `batch`: None = sem limite (--all), "default" = padrão do servidor."""
        body: dict[str, Any] = {"repos": repos or None, "discover": do_discover, "pull": pull, "full": full,
                                "dry_run": dry_run, "all": batch is None, "only_changed": only_changed}
        if isinstance(batch, int):
            body["batch"] = batch
        if pages:
            body["pages"] = list(pages)
        return await self.call("POST", "/sync", json=body)

    async def wait_job(self, job_id: str) -> dict[str, Any]:
        """Acompanha GET /sync/jobs/{id} até o fim. Rate limit e falhas passageiras (servidor reiniciando) esperam."""
        failures = 0
        try:
            while True:
                try:
                    job: dict[str, Any] = await self.call("GET", f"/sync/jobs/{_seg(job_id)}")
                except RemoteError as e:
                    if e.status == 429:
                        await asyncio.sleep(e.retry_after or self.poll_seconds)
                        continue
                    failures += 1
                    if (e.status is not None and e.status < 500) or failures > 5:
                        raise
                    await asyncio.sleep(self.poll_seconds * failures)
                    continue
                failures = 0
                if self._progress is not None:
                    self._progress(job)
                if job["status"] not in _ACTIVE_JOB:
                    return job
                await asyncio.sleep(self.poll_seconds)
        except asyncio.CancelledError:
            print(f"\no job {job_id} continua no servidor; acompanhe com `code-index sync --job {job_id}`.",
                  file=sys.stderr)
            raise
        finally:
            if isinstance(self._progress, JobProgress):
                self._progress.done()

    async def job_result(self, job_id: str) -> SyncResult:
        job = await self.wait_job(job_id)
        if job.get("result"):
            return _build(SyncResult, job["result"])
        if job["status"] == "failed":
            raise RemoteError(f"o job {job_id} falhou: {job.get('error') or 'sem detalhes'}")
        return SyncResult()

    async def sync(self, repos: list[str] | None = None, *, batch: int | None | str = "default",
                   do_discover: bool = True, pull: bool = True, full: bool = False, dry_run: bool = False,
                   only_changed: bool = False, **_: Any) -> SyncResult:
        res = await self.start_sync(repos, batch=batch, do_discover=do_discover, pull=pull, full=full,
                                    dry_run=dry_run, only_changed=only_changed)
        if dry_run:
            return _build(SyncResult, res)
        return await self.job_result(res["job_id"])

    async def index_doc_pages(self, repo: str, pages: list[str]) -> SyncResult:
        res = await self.start_sync([repo], batch="default", do_discover=False, pages=pages)
        return await self.job_result(res["job_id"])

    # ------------------------------------------------ vetores

    async def vector_status(self) -> dict[str, Any]:
        return await self._get("/vectors/status")

    async def embed_vectors(self, *, dry_run: bool = False, limit: int | None = None,
                            kinds: list[str] | None = None) -> dict[str, Any]:
        """dry_run: a conta na hora. Senão inicia no servidor e espera o fim da rodada (o estado é do processo que
        recebeu o pedido: com várias réplicas, pode não dar para acompanhar; aí devolve {"detached": ...})."""
        body = _params(kinds=kinds, limit=limit)
        if dry_run:
            return await self.call("POST", "/vectors/embed", json={**body, "dry_run": True})
        state = await self.call("POST", "/vectors/embed", json=body)
        mine = (state.get("last_run") or {}).get("started_at")
        misses = 0
        while True:
            run = state.get("last_run") or {}
            if mine and run.get("started_at") == mine:
                misses = 0
                if run.get("status") == "done":
                    return {**run["result"], "dry_run": False}
                if run.get("status") == "failed":
                    raise RemoteError(f"a geração de vetores falhou no servidor: {run.get('error') or 'sem detalhes'}")
            else:
                misses += 1
                if misses > 10:
                    return {"detached": True, "pending": state.get("pending")}
            await asyncio.sleep(self.poll_seconds)
            try:
                state = await self.vector_status()
            except RemoteError as e:
                if e.status != 429 and (e.status is None or e.status < 500):
                    raise

    # ------------------------------------------------ busca e leitura

    async def search(self, query: str, *, top_k: int = 8, repos: list[str] | None = None, language: str | None = None,
                     path_glob: str | None = None, mode: str = "hybrid", rerank: bool = True,
                     candidates: int | None = None, kind: str | None = None, include_linked_docs: bool = True,
                     exclude_repos: list[str] | None = None, exclude_path_glob: list[str] | None = None,
                     max_docs: int | None = None, domain: str | None = None, service: str | None = None) -> list[Hit]:
        body = {"query": query, "top_k": top_k, "mode": mode, "rerank": rerank,
                "include_linked_docs": include_linked_docs,
                **_params(repos=repos, language=language, path_glob=path_glob, candidates=candidates, kind=kind,
                          exclude_repos=exclude_repos, exclude_path_glob=exclude_path_glob, max_docs=max_docs,
                          domain=domain, service=service)}
        return [_build(Hit, h) for h in await self.call("POST", "/search", json=body)]

    async def find_symbol(self, name: str, *, kind: str | None = None, repos: list[str] | None = None,
                          language: str | None = None, limit: int = 20) -> list[SymbolHit]:
        rows = await self._get("/symbols", name=name, kind=kind, repo=repos, language=language, limit=limit)
        return [_build(SymbolHit, r) for r in rows]

    async def grep(self, pattern: str, *, repos: list[str] | None = None, path_glob: str | None = None,
                   regex: bool = False, case_sensitive: bool = False, max_results: int = 50,
                   include_tests: bool = False) -> RemoteGrep:
        res = await self.call("POST", "/grep", json={
            "pattern": pattern, "repos": repos, "path_glob": path_glob, "regex": regex,
            "case_sensitive": case_sensitive, "max_results": max_results, "include_tests": include_tests})
        return RemoteGrep(matches=[_build(GrepMatch, m) for m in res["matches"]], truncated=bool(res["truncated"]))

    async def read_file(self, repo: str, path: str, start: int | None = None, end: int | None = None) -> str:
        r = await self.request("GET", f"/files/{_seg(repo)}/{quote(path.lstrip('/'), safe='/')}",
                               params=_params(start_line=start, end_line=end), headers={"Accept": "text/plain"})
        return r.text

    async def find_tests(self, target: str, *, repos: list[str] | None = None, limit: int = 30) -> dict[str, Any]:
        return await self._get("/tests", target=target, repo=repos, limit=limit)

    async def call_graph(self, target: str, *, direction: str = "callers", repos: list[str] | None = None,
                         limit: int = 30) -> dict[str, Any]:
        return await self._get("/calls", target=target, direction=direction, repo=repos,
                               limit=limit)

    # ------------------------------------------------ leituras dos clones (grafo e testes)

    async def _scan(self, kind: str, repos: list[str] | None, force: bool) -> dict[str, Any]:
        res = await self.call("POST", f"/scans/{kind}", params=_params(repo=repos or None, force=force, wait=True),
                              timeout=max(self._timeout, 1800.0))
        return res["result"]

    async def _scan_status(self, kind: str) -> dict[str, Any]:
        st = (await self._get("/scans/status"))[kind]
        return {k: v for k, v in st.items() if k not in ("running", "last_run")}

    async def scan_tests(self, repos: list[str] | None = None, *, force: bool = False) -> dict[str, Any]:
        return await self._scan("tests", repos, force)

    async def tests_status(self) -> dict[str, Any]:
        return await self._scan_status("tests")

    async def scan_calls(self, repos: list[str] | None = None, *, force: bool = False) -> dict[str, Any]:
        return await self._scan("graph", repos, force)

    async def graph_status(self) -> dict[str, Any]:
        return await self._scan_status("graph")

    # ------------------------------------------------ APIs (OpenAPI e handlers)

    async def openapi_catalog(self) -> list[dict[str, Any]]:
        return await self._get("/openapi/catalog")

    async def openapi_preview(self, domain: str, services: list[str] | None = None) -> dict[str, Any]:
        return await self.call("POST", "/openapi/preview",
                               json={"domain": domain, "services": services})

    async def openapi_index(self, domain: str, services: list[str] | None = None) -> dict[str, Any]:
        """Enfileira a indexação (admin); acompanhe com `job_result`."""
        return await self.call("POST", "/openapi/index",
                               json={"domain": domain, "services": services})

    async def find_api_impl(self, operation: str, *, service: str | None = None, domain: str | None = None,
                            limit: int = 20) -> list[dict[str, Any]]:
        return await self._get("/api-impls", operation=operation, service=service,
                               domain=domain, limit=limit)

    # ------------------------------------------------ documentação

    async def add_docs(self, name: str, *, url: str, locale: str = "pt-br", include: Any = (), exclude: Any = (),
                       links: Any = ()) -> dict[str, Any]:
        return await self.call("POST", "/docs/sources", json={
            "name": name, "url": url, "locale": locale, "include": list(include), "exclude": list(exclude),
            "links": [{"path": path, "repos": list(repos)} for path, repos in links]})

    async def list_docs(self) -> list[DocSource]:
        return [_doc_source(d) for d in await self._get("/docs/sources")]

    async def remove_docs(self, name: str, *, keep_files: bool = False) -> None:
        await self.call("DELETE", f"/docs/sources/{_seg(name)}", params={"keep_files": keep_files})

    async def docs_preview(self, name: str, *, limit: int = 200) -> dict[str, Any]:
        return await self._get(f"/docs/{_seg(name)}/preview", limit=limit)

    async def link_doc(self, source: str, page: str, repo: str, *, reason: str = "",
                       origin: str = "manual") -> dict[str, Any]:
        return await self.call("POST", "/docs/links", json={
            "source": source, "page": page, "repo": repo, "reason": reason, "origin": origin})

    async def unlink_doc(self, source: str, page: str, repo: str) -> bool:
        res = await self._or_none_on_404("DELETE", "/docs/links",
                                         params={"source": source, "page": page, "repo": repo})
        return res is not None

    async def ignore_doc(self, source: str, page: str, *, reason: str = "") -> dict[str, Any]:
        return await self.call("POST", "/docs/ignored",
                               json={"source": source, "page": page, "reason": reason})

    async def unignore_doc(self, source: str, page: str) -> bool:
        res = await self._or_none_on_404("DELETE", "/docs/ignored", params={"source": source, "page": page})
        return res is not None

    async def doc_links(self, *, source: str | None = None, repo: str | None = None) -> list[dict[str, Any]]:
        return await self._get("/docs/links", source=source, repo=repo)

    async def linked_docs(self, repo: str, *, limit: int = 100) -> list[dict[str, Any]]:
        return await self._get(f"/repos/{_seg(repo)}/docs", limit=limit)

    async def doc_suggestions(self, *, status: str = "suggested", source: str | None = None,
                              confidence: str | None = None, action: str | None = None,
                              limit: int = 200) -> list[dict[str, Any]]:
        return await self._get("/docs/suggestions", status=status, source=source,
                               confidence=confidence, action=action, limit=limit)

    async def approve_doc_suggestions(self, *, confidence: str, source: str | None = None,
                                      action: str | None = None) -> int:
        res = await self.call("POST", "/docs/suggestions/approve",
                              json={"confidence": confidence, "source": source, "action": action})
        return int(res["approved"])

    async def review_doc_suggestion(self, doc_repo: str, doc_path: str, repo: str, *, approve: bool) -> bool:
        res = await self._or_none_on_404("POST", "/docs/suggestions/review", json={
            "doc_repo": doc_repo, "doc_path": doc_path, "repo": repo, "approve": approve})
        return res is not None

    # ------------------------------------------------ enriquecimento

    async def enrich_facts(self, repos: list[str] | None = None, *, force: bool = False) -> dict[str, Any]:
        return await self.call("POST", "/enrich/facts", json={"repos": repos, "force": force},
                               timeout=max(self._timeout, 1800.0))

    async def enrich_next(self, *, agent: str = "", repo: str | None = None, kind: str = "card",
                          scope: str = "todas") -> dict[str, Any] | None:
        # `agent` não vai: o servidor registra quem pediu pelo token
        return await self.call("POST", "/enrich/next",
                               json={"kind": kind, "repo": repo, "scope": scope})

    async def enrich_submit(self, task_id: str, payload: str, *, agent: str = "") -> dict[str, Any]:
        return await self.call("POST", f"/enrich/tasks/{_seg(task_id)}/submit",
                               content=payload.encode("utf-8"), headers={"Content-Type": "application/json"})

    async def enrich_release(self, task_id: str) -> bool:
        res = await self.call("POST", f"/enrich/tasks/{_seg(task_id)}/release")
        return bool(res["released"])

    async def apply_file_summaries(self) -> dict[str, Any]:
        return await self.call("POST", "/enrich/apply-summaries",
                               timeout=max(self._timeout, 1800.0))

    async def enrich_status(self) -> dict[str, Any]:
        return await self._get("/enrich/status")

    # ------------------------------------------------ relações e dono

    async def rebuild_relations(self) -> dict[str, Any]:
        return await self.call("POST", "/relations/rebuild",
                               timeout=max(self._timeout, 1800.0))

    async def relation_origins(self) -> list[dict[str, Any]]:
        return await self._get("/relations/origins")

    async def relation_stats(self) -> dict[str, Any]:
        return await self._get("/relations/stats")

    async def related(self, entity: str, *, type_: str | None = None, limit: int = 200) -> dict[str, Any]:
        return await self._get("/relations", entity=entity, type=type_, limit=limit)

    async def ownership_catalog(self) -> dict[str, Any]:
        return await self._get("/ownership/catalog")

    async def ownership_list(self, *, domain: str | None = None, service: str | None = None, origin: str | None = None,
                             unowned: bool = False, repo: str | None = None) -> list[dict[str, Any]]:
        return await self._get("/ownership", domain=domain, service=service,
                               origin=origin, unowned=unowned, repo=repo)

    async def set_owner(self, _principal: Any, repos: list[str], owner: str, *, path: str | None = None,
                        create: bool = False, reason: str = "") -> dict[str, Any]:
        return await self.call("PUT", "/ownership", json={
            "repos": list(repos), "owner": owner, "path": path, "create": create, "reason": reason})

    async def clear_owner(self, _principal: Any, repos: list[str], *, path: str | None = None) -> dict[str, Any]:
        return await self.call("POST", "/ownership/clear",
                               json={"repos": list(repos), "path": path})

    # ------------------------------------------------ tokens, usuários e uso

    async def list_tokens(self, _principal: Any, *, include_inactive: bool = False) -> list[RemoteToken]:
        rows = await self._get("/tokens", include_inactive=include_inactive)
        return [RemoteToken.from_dict(r) for r in rows]

    async def revoke_token(self, _principal: Any, token_id: str) -> None:
        await self.call("DELETE", f"/tokens/{_seg(token_id)}")

    async def users_overview(self, days: int = 30) -> list[dict[str, Any]]:
        return await self._get("/users", days=days)

    async def usage_report(self, days: int = 30, *, email: str | None = None) -> dict[str, Any]:
        return await self._get("/usage", days=days, email=email)


def _detail(r: httpx.Response) -> str:
    """Mensagem de erro da API (FastAPI: {"detail": texto | [erros de validação]}); nunca despeja HTML."""
    try:
        data = r.json()
    except ValueError:
        return ""
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):  # 422: [{"loc": [...], "msg": ...}]
        parts = []
        for err in detail[:5]:
            loc = ".".join(str(x) for x in (err.get("loc") or [])[1:]) if isinstance(err, dict) else ""
            msg = err.get("msg", "") if isinstance(err, dict) else str(err)
            parts.append(f"{loc}: {msg}" if loc else msg)
        return "pedido inválido: " + "; ".join(parts)
    return ""


def _retry_after(r: httpx.Response) -> float | None:
    raw = r.headers.get("retry-after", "")
    try:
        return max(float(raw), 0.0)
    except ValueError:
        return None
