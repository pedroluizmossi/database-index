"""Orquestra descoberta, seleção de lote, git sync e o pipeline CocoIndex.

Fluxo de um `sync`:
  1. espelha os [[repo]] do repos.toml na tabela de estado e (opcional) redescobre
     os repos das [[org]] pela API do GitHub;
  2. escolhe o que processar: os nomes pedidos, ou um lote de até N repos
     (alterados upstream > pendentes > com erro);
  3. clona/atualiza só esses;
  4. roda o pipeline com TODOS os repos indexados — os não selecionados passam com
     a revisão já gravada, então o `index_repo` memoizado os pula sem percorrer nada;
  5. grava commit/revisão/erro de cada selecionado.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

import cocoindex as coco

from . import state
from .config import HOME, Org, Repo, load_orgs, load_repos, name_from_url, normalize_source, save_config
from .git_sync import remote_head, sync_repo
from .github import list_org_repos

log = logging.getLogger(__name__)

DEFAULT_BATCH = 10


@dataclass
class SyncResult:
    selected: list[str] = field(default_factory=list)
    indexed: dict[str, str | None] = field(default_factory=dict)   # nome -> commit
    errors: dict[str, str] = field(default_factory=dict)
    discovered: dict[str, dict] = field(default_factory=dict)
    remaining: int = 0       # candidatos que ficaram para os próximos lotes

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@contextlib.contextmanager
def _lock() -> Iterator[None]:
    path = HOME / ".code-index.lock"
    with open(path, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("já existe um sync em andamento (watch, MCP ou outro terminal)") from None
        yield


def _org_cfg(orgs: list[Org], name: str | None) -> Org | None:
    return next((o for o in orgs if o.name == name), None)


def _to_repo(row: state.RepoState, orgs: list[Org], manual: dict[str, Repo]) -> Repo:
    if row.org is None and row.name in manual:
        return manual[row.name]
    org = _org_cfg(orgs, row.org) or Org(name=row.org or "")
    return Repo(name=row.name, url=row.url, include=org.include, exclude=org.exclude,
                extra_exclude=org.extra_exclude)


def _resolve(name: str, rows: dict[str, state.RepoState]) -> str | None:
    """Aceita 'org__repo', 'org/repo' ou só 'repo' (se não for ambíguo)."""
    if name in rows:
        return name
    if "/" in name and not name.startswith(("http", "git@", "/", ".", "~")):
        cand = name.replace("/", "__", 1)
        if cand in rows:
            return cand
    suffix = [n for n in rows if n.endswith("__" + name)]
    if len(suffix) > 1:
        raise ValueError(f"nome ambíguo {name!r}: {', '.join(sorted(suffix))}")
    return suffix[0] if suffix else None


def _register_manual(source: str) -> str:
    """Nome não encontrado mas é 'org/repo', URL git ou diretório: adiciona como [[repo]]."""
    url, path = normalize_source(source)
    if url is None and path is None:
        raise ValueError(f"repositório desconhecido: {source}")
    name = name_from_url(url) if url else re.sub(r"[^A-Za-z0-9._-]", "_", path.rstrip("/").rsplit("/", 1)[-1])
    repos = load_repos()
    if not any(r.name == name for r in repos):
        repos.append(Repo(name=name, url=url, path=path))
        save_config(repos=repos)
        log.info("registrado novo repo avulso %s", name)
    return name


def _pick_batch(rows: dict[str, state.RepoState], batch: int | None,
                orgs: list[Org] | None = None) -> tuple[list[str], int]:
    changed, pending, failed = [], [], []
    for r in rows.values():
        if r.ignored:
            continue
        if r.error:
            failed.append(r)
        elif not r.indexed:
            pending.append(r)
        elif r.path is not None:
            changed.append(r)  # diretório local: sempre revarre (barato, memo por arquivo)
        elif r.org is not None:
            if r.changed_upstream:
                changed.append(r)
        else:
            try:  # repo avulso: compara o HEAD remoto com o commit indexado
                if remote_head(r.url, r.default_branch) != r.indexed_commit:
                    changed.append(r)
            except RuntimeError as e:
                log.warning("ls-remote falhou para %s: %s", r.name, e)
    org_by_name = {o.name: o for o in orgs or []}

    def priority(r: state.RepoState) -> tuple[int, float]:
        org = org_by_name.get(r.org or "")
        rank = org.language_rank(r.language) if org else 0
        return rank, -(r.pushed_at.timestamp() if r.pushed_at else 0)

    order = (sorted(changed, key=priority) + sorted(pending, key=priority)
             + sorted(failed, key=lambda r: r.updated_at))
    names = [r.name for r in order]
    if batch is None:
        return names, 0
    return names[:batch], max(0, len(names) - batch)


def discover(orgs: list[Org] | None = None) -> dict[str, dict]:
    """Redescobre os repos das orgs (sem clonar nem indexar)."""
    orgs = load_orgs() if orgs is None else orgs

    async def run() -> dict[str, dict]:
        conn = await state.connect()
        try:
            out = {}
            for org in orgs:
                found = await asyncio.to_thread(list_org_repos, org)
                out[org.name] = await state.reconcile_org(conn, org.name, found)
            return out
        finally:
            await conn.close()

    return asyncio.run(run())


def sync(
    names: list[str] | None = None,
    *,
    batch: int | None = DEFAULT_BATCH,
    do_discover: bool = True,
    pull: bool = True,
    full: bool = False,
    progress: bool = True,
    dry_run: bool = False,
) -> SyncResult:
    """Sincroniza e indexa.

    names: indexa exatamente estes (revarrendo mesmo sem mudança de commit);
           aceita nomes desconhecidos no formato 'org/repo', URL ou diretório.
    batch: sem `names`, processa no máximo N repos (None = todos os candidatos).
    dry_run: só mostra a seleção (não clona, não indexa, não gasta tokens).
    """
    from .pipeline import build_app  # import tardio: só carrega o CocoIndex quando indexar

    res = SyncResult()
    with _lock():
        orgs = load_orgs()
        if do_discover and orgs:
            for org in orgs:
                try:
                    res.discovered[org.name] = discover([org])[org.name]
                except Exception as e:  # noqa: BLE001
                    log.error("descoberta da org %s falhou: %s", org.name, e)
                    res.errors[f"org:{org.name}"] = str(e)

        async def load() -> dict[str, state.RepoState]:
            conn = await state.connect()
            try:
                await state.reconcile_manual(conn, load_repos())
                await state.prune_orgs(conn, [o.name for o in orgs])
                return await state.all_rows(conn)
            finally:
                await conn.close()

        rows = asyncio.run(load())

        forced = bool(names)
        if names:
            selected = []
            for n in names:
                resolved = _resolve(n, rows)
                if resolved is None:
                    resolved = _register_manual(n)
                    rows = asyncio.run(load())
                selected.append(resolved)
            selected = list(dict.fromkeys(selected))
        else:
            selected, res.remaining = _pick_batch(rows, batch, orgs)
        res.selected = selected
        if dry_run:
            return res

        manual = {r.name: r for r in load_repos()}

        # git sync dos selecionados
        revisions: dict[str, tuple[str | None, str]] = {}
        for name in selected:
            row = rows[name]
            repo = _to_repo(row, orgs, manual)
            try:
                if pull or not repo.local_path.is_dir():
                    log.info("git sync %s", name)
                    commit = sync_repo(repo)
                else:
                    commit = row.indexed_commit
                revision = commit or "local"
                if forced or repo.path is not None:
                    revision += f"#{time.time_ns()}"  # força revarrer o repo (arquivos seguem memoizados)
                revisions[name] = (commit, revision)
            except Exception as e:  # noqa: BLE001
                log.error("git sync falhou para %s: %s", name, e)
                res.errors[name] = str(e)

        # pipeline: todos os indexados + selecionados com sucesso
        plan: list[tuple[Repo, str]] = []
        for row in rows.values():
            if row.ignored and row.name not in revisions:
                continue
            if row.name in revisions:
                plan.append((_to_repo(row, orgs, manual), revisions[row.name][1]))
            elif row.indexed:
                # mesma revisão já gravada => memo hit, o repo não é percorrido
                plan.append((_to_repo(row, orgs, manual), row.revision))

        if not selected:
            log.info("nenhum repo para sincronizar; aplicando apenas remoções pendentes")
        pipeline_error: str | None = None
        try:
            build_app(plan).update_blocking(report_to_stdout=progress, full_reprocess=full)
        except Exception as e:  # noqa: BLE001
            log.exception("pipeline falhou")
            pipeline_error = f"pipeline: {e}"
        finally:
            # Finaliza o lifespan (fecha pool do Postgres e sessões HTTP do LiteLLM);
            # o ambiente é recriado sob demanda no próximo sync (watch/MCP).
            coco.stop_blocking()

        async def record() -> None:
            conn = await state.connect()
            try:
                for name, msg in res.errors.items():
                    if name in rows:
                        await state.mark_error(conn, name, msg)
                for name, (commit, revision) in revisions.items():
                    if pipeline_error:
                        await state.mark_error(conn, name, pipeline_error)
                        res.errors[name] = pipeline_error
                    else:
                        if rows[name].ignored:
                            await state.set_ignored(conn, name, False)
                        await state.mark_indexed(conn, name, commit, revision, rows[name].pushed_at)
                        res.indexed[name] = commit
            finally:
                await conn.close()

        asyncio.run(record())
    return res


async def repo_rows() -> dict[str, state.RepoState]:
    conn = await state.connect()
    try:
        return await state.all_rows(conn)
    finally:
        await conn.close()
