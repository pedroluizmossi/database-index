"""Busca exata/regex nos clones com ripgrep: o que a busca semântica não cobre bem
(mensagens de erro, chaves de configuração, strings, chamadas exatas).

Segurança e custo:
  - sem shell: argumentos em lista; o padrão vai depois de `-e` e os caminhos depois de `--`;
  - só arquivos que o índice consideraria (mesmos include/exclude), respeitando .gitignore,
    sem arquivos ocultos (`.env` etc.) e sem binários;
  - cada linha devolvida passa por `mask(arquivo, número da linha, linha)`, injetada pelo serviço
    (a máscara de segredos de indexing/content; infra não depende de indexing). A máscara vê o
    arquivo inteiro: segredo de várias linhas (chave PEM) não se esconde linha a linha;
  - limites: total de resultados, resultados por arquivo, tamanho de arquivo e tempo.
O regex do ripgrep (Rust) não tem backtracking exponencial.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from ..domain.models import GrepMatch
from ..domain.path_glob import glob_matcher

# (arquivo absoluto, número da linha a partir de 1, linha) -> linha mascarada
LineMask = Callable[[Path, int, str], Awaitable[str]]

MAX_PATTERN = 500
_PER_FILE = 20
_MAX_LINE = 300
# Maior evento JSON lido do rg: linha enorme (ou com milhares de ocorrências) passa disso e é pulada.
_MAX_EVENT = 8 * 1024 * 1024


async def _no_mask(_path: Path, _line: int, line: str) -> str:
    return line


@dataclass(frozen=True)
class GrepTarget:
    repo: str
    root: Path
    include: tuple[str, ...]
    exclude: tuple[str, ...]


@dataclass(frozen=True)
class GrepResult:
    matches: list[GrepMatch]
    truncated: bool          # parou no limite de resultados ou de tempo


class Grep:
    def __init__(self, rg_path: str | None = None) -> None:
        self._rg = rg_path or shutil.which("rg")

    @property
    def available(self) -> bool:
        return self._rg is not None

    async def search(
        self,
        targets: list[GrepTarget],
        pattern: str,
        *,
        regex: bool = False,
        case_sensitive: bool = False,
        path_glob: str | None = None,
        max_results: int = 50,
        timeout: float = 20.0,
        mask: LineMask = _no_mask,
    ) -> GrepResult:
        if not self._rg:
            raise RuntimeError("ripgrep (rg) não está instalado no servidor")
        if not pattern or len(pattern) > MAX_PATTERN:
            raise ValueError(f"padrão vazio ou maior que {MAX_PATTERN} caracteres")
        max_results = max(1, min(max_results, 500))
        keep = glob_matcher(path_glob)

        # Repos com os mesmos filtros de arquivo vão numa só execução do rg.
        groups: dict[tuple[tuple[str, ...], tuple[str, ...]], list[GrepTarget]] = {}
        for t in targets:
            groups.setdefault((t.include, t.exclude), []).append(t)

        matches: list[GrepMatch] = []
        truncated = False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        for (include, exclude), group in groups.items():
            remaining = deadline - loop.time()
            if remaining <= 0 or len(matches) >= max_results:
                truncated = True
                break
            hit_limit = await self._run_group(
                group, include, exclude, pattern, regex, case_sensitive, keep, mask, matches, max_results, remaining
            )
            truncated = truncated or hit_limit
        return GrepResult(matches=matches[:max_results], truncated=truncated)

    async def _run_group(
        self,
        group: list[GrepTarget],
        include: tuple[str, ...],
        exclude: tuple[str, ...],
        pattern: str,
        regex: bool,
        case_sensitive: bool,
        keep,  # type: ignore[no-untyped-def]
        mask: LineMask,
        out: list[GrepMatch],
        max_results: int,
        timeout: float,
    ) -> bool:
        assert self._rg is not None
        args = [
            self._rg, "--json", "--no-config", "--max-filesize", "512K",
            "--max-count", str(_PER_FILE), "--max-columns", "2000",
            "--case-sensitive" if case_sensitive else "--ignore-case",
        ]
        if not regex:
            args.append("--fixed-strings")
        for g in include:
            args += ["-g", g]
        for g in exclude:
            args += ["-g", f"!{g}"]
        roots = sorted(group, key=lambda t: len(str(t.root)), reverse=True)
        args += ["-e", pattern, "--", *[str(t.root) for t in group]]

        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=_MAX_EVENT
        )
        hit_limit = False
        try:
            async with asyncio.timeout(timeout):
                assert proc.stdout is not None
                while True:
                    try:
                        raw = await proc.stdout.readline()
                    except ValueError:  # evento maior que o limite: descartado pelo leitor
                        continue
                    if not raw:
                        break
                    try:
                        event = json.loads(raw)
                    except ValueError:  # resto de um evento grande demais
                        continue
                    if event.get("type") != "match":
                        continue
                    data = event["data"]
                    abs_path = data["path"].get("text")
                    if abs_path is None:  # nome de arquivo não UTF-8
                        continue
                    target = next((t for t in roots if abs_path.startswith(str(t.root) + "/")), None)
                    if target is None:
                        continue
                    rel = abs_path[len(str(target.root)) + 1:]
                    if not keep(rel):
                        continue
                    raw_line = (data["lines"].get("text") or "").rstrip("\n")
                    text = await mask(Path(abs_path), data["line_number"], raw_line)
                    out.append(GrepMatch(repo=target.repo, path=rel, line=data["line_number"],
                                         text=text.strip()[:_MAX_LINE]))
                    if len(out) >= max_results:
                        hit_limit = True
                        break
        except TimeoutError:
            hit_limit = True
        finally:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
        if not hit_limit and proc.returncode == 2 and proc.stderr is not None:
            err = (await proc.stderr.read()).decode(errors="replace").strip()
            if "regex parse error" in err or "error parsing" in err:
                raise ValueError(f"padrão inválido: {err.splitlines()[-1][:200] if err else ''}")
        return hit_limit
