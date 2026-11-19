"""Progresso de um sync enquanto ele roda: etapa atual e situação de cada repo selecionado.

O pipeline do CocoIndex roda numa thread própria (`update_blocking`). Para não mexer nas funções
memoizadas (o que forçaria reprocessar), cada repo selecionado é montado dentro de um
`coco.stats_group`; o orquestrador lê esses contadores periodicamente e publica um retrato
(`snapshot`) que o serviço grava no job.

O grupo só conta componentes montados (`index_repo`, `process_file`): arquivos, sim; trechos, não
(`process_chunk` roda via `coco.map`, que não é componente). O total de arquivos vem de uma contagem
prévia feita pelo orquestrador (`files_total`), com os mesmos filtros do `index_repo`.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Phase = Literal["discover", "select", "fetch", "index", "finish"]
RepoStatus = Literal["queued", "fetching", "indexing", "checking", "done", "error", "skipped"]

PHASE_LABEL: dict[str, str] = {
    "discover": "descobrindo repos",
    "select": "escolhendo o lote",
    "fetch": "baixando",
    "index": "indexando",
    "finish": "finalizando",
}
# Etapa "index" com todos os selecionados prontos: o CocoIndex ainda percorre os demais repos indexados
# (memo hit, em geral rápido; demora quando uma dependência de lógica, como `content.py`, mudou).
CHECKING_OTHERS_LABEL = "verificando os demais repos"
# ... e algum deles está sendo reprocessado de verdade (arquivos mudaram de regra: versão do sistema, filtros, divisão).
REPROCESSING_OTHERS_LABEL = "reprocessando outros repos já indexados"
OTHERS_REASON = ("não foram pedidos neste job, mas as regras de indexação mudaram (versão do sistema, filtros ou "
                 "divisão dos trechos) e os arquivos deles estão sendo refeitos; acontece uma vez por mudança")
_MAX_OTHERS = 20  # quantos repos "extras" aparecem no retrato (o total vem à parte)


class _StatsSource(Protocol):
    """`coco.StatsGroupHandle`: `stats()` devolve `UpdateStats` (contadores por componente) ou None."""

    def stats(self) -> Any: ...


@dataclass
class RepoProgress:
    name: str
    status: RepoStatus = "queued"
    files_total: int | None = None    # contagem prévia dos arquivos que entram na indexação
    files_done: int = 0
    files_unchanged: int = 0          # memoizados: não foram reprocessados
    errors: int = 0
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    requested: bool = True            # False: repo já indexado que não foi pedido, mas que o CocoIndex percorre

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "files_total": self.files_total,
            "files_done": self.files_done,
            "files_unchanged": self.files_unchanged,
            "errors": self.errors,
            "error": self.error,
            "seconds": round((self.finished_at or time.time()) - self.started_at, 1) if self.started_at else None,
        }


def _component(stats: Any, suffix: str) -> Any | None:
    """Contadores de um componente pelo nome da função (o CocoIndex pode qualificar o nome)."""
    for name, counts in stats.by_component.items():
        if name == suffix or name.endswith(f".{suffix}") or name.endswith(f":{suffix}"):
            return counts
    return None


@dataclass
class SyncProgress:
    """Estado compartilhado entre a thread do pipeline (registra os grupos) e o orquestrador (lê)."""

    phase: Phase = "discover"
    repos: dict[str, RepoProgress] = field(default_factory=dict)
    others: dict[str, RepoProgress] = field(default_factory=dict)  # não pedidos (só aparecem se reprocessarem)
    started_at: float = field(default_factory=time.time)
    activity_at: float = field(default_factory=time.time)  # última mudança de verdade (etapa, status, contadores)
    step: str | None = None                                # detalhe da etapa ("lendo os testes", "parando...")
    stopping: str | None = None                            # motivo de uma parada pedida
    _sources: dict[str, _StatsSource] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # --- escrita (orquestrador) ---------------------------------------------------

    def set_phase(self, phase: Phase) -> None:
        self.phase = phase
        self.step = None
        self.activity_at = time.time()

    def set_step(self, step: str | None) -> None:
        self.step = step
        self.activity_at = time.time()

    def set_stopping(self, reason: str) -> None:
        self.stopping = reason
        self.activity_at = time.time()

    def select(self, names: list[str]) -> None:
        with self._lock:
            self.repos = {n: RepoProgress(n) for n in names}

    def update(self, name: str, **changes: Any) -> None:
        with self._lock:
            repo = self.repos.get(name)
            if repo is None:
                return
            for key, value in changes.items():
                setattr(repo, key, value)
            self.activity_at = time.time()
            if changes.get("status") in ("fetching", "indexing") and repo.started_at is None:
                repo.started_at = time.time()
            if changes.get("status") in ("done", "error", "skipped"):
                repo.finished_at = time.time()

    # --- escrita (thread do pipeline) ----------------------------------------------

    def tracks(self, name: str) -> bool:
        """Todo repo do plano é acompanhado: os pedidos e os já indexados que o CocoIndex percorre (se uma regra
        mudou, estes são reprocessados, e o job precisa mostrar isso em vez de parecer parado)."""
        return True

    def attach(self, name: str, source: _StatsSource) -> None:
        with self._lock:
            self._sources[name] = source
            if name not in self.repos and name not in self.others:
                self.others[name] = RepoProgress(name, status="checking", requested=False, started_at=time.time())

    # --- leitura ---------------------------------------------------------------------

    def refresh(self) -> None:
        """Copia os contadores do CocoIndex para os repos em indexação."""
        with self._lock:
            sources = dict(self._sources)
        for name, source in sources.items():
            stats = source.stats()
            if stats is None:
                continue
            files = _component(stats, "process_file")
            with self._lock:
                repo = self.repos.get(name) or self.others.get(name)
                if repo is None or repo.status not in ("indexing", "checking"):
                    continue
                if files is not None:
                    before = (repo.files_done, repo.errors)
                    repo.files_done = files.num_finished
                    repo.files_unchanged = files.num_unchanged
                    repo.errors = files.num_errors
                    if (repo.files_done, repo.errors) != before:
                        self.activity_at = time.time()
                if not repo.requested:
                    continue
                # Um arquivo só termina depois dos seus trechos (process_file espera o map dos trechos):
                # todos os arquivos prontos = repo pronto, mesmo com outros repos ainda rodando.
                if repo.files_total is not None and repo.files_done >= repo.files_total:
                    repo.status = "done"
                    repo.finished_at = time.time()

    def finish_indexing(self, error: str | None) -> None:
        """Fim do pipeline: quem estava indexando termina (com o erro do pipeline, se houver)."""
        self.refresh()
        with self._lock:
            self.activity_at = time.time()
            for other in self.others.values():
                if other.status == "checking":
                    other.status, other.finished_at = ("error", time.time()) if error else ("done", time.time())
            for repo in self.repos.values():
                if repo.status != "indexing":
                    continue
                if error:
                    repo.status, repo.error = "error", error
                else:
                    repo.status = "done"
                    if repo.files_total is not None:
                        repo.files_done = max(repo.files_done, repo.files_total)
                repo.finished_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            repos = [r.to_dict() for r in self.repos.values()]
            # Extras só aparecem quando refazem arquivos (memo hit puro é rápido e não interessa à tela).
            busy = [r for r in self.others.values() if r.files_done - r.files_unchanged > 0 or r.errors]
            others = [{**r.to_dict(), "files_redone": r.files_done - r.files_unchanged}
                      for r in sorted(busy, key=lambda r: -(r.files_done - r.files_unchanged))[:_MAX_OTHERS]]
            others_files = sum(r.files_done - r.files_unchanged for r in busy)
        done = sum(1 for r in repos if r["status"] in ("done", "error", "skipped"))
        label = PHASE_LABEL[self.phase]
        if self.phase == "index" and repos and done == len(repos):
            label = REPROCESSING_OTHERS_LABEL if busy else CHECKING_OTHERS_LABEL
        if self.stopping:
            label = f"parando: {self.stopping}"
        return {
            "phase": self.phase,
            "phase_label": label,
            "step": self.step,
            "repos_total": len(repos),
            "repos_done": done,
            "files_total": sum(r["files_total"] or 0 for r in repos),
            "files_done": sum(r["files_done"] for r in repos),
            "seconds": round(time.time() - self.started_at, 1),
            "activity_at": _iso(self.activity_at),
            "repos": repos,
            "others": {"repos": len(busy), "files": others_files, "reason": OTHERS_REASON if busy else None,
                       "list": others},
        }

    def done_names(self) -> set[str]:
        """Repos pedidos que terminaram de indexar (usado ao parar no meio: só estes são marcados como indexados)."""
        with self._lock:
            return {r.name for r in self.repos.values() if r.status == "done"}


def _iso(ts: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds")


# Sync em andamento neste processo (só um por vez: o orquestrador segura um advisory lock). O pipeline do
# CocoIndex lê daqui para registrar os grupos de estatística dos repos acompanhados.
_active: SyncProgress | None = None


def set_active(progress: SyncProgress | None) -> None:
    global _active
    _active = progress


def active() -> SyncProgress | None:
    return _active
