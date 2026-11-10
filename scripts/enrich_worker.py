#!/usr/bin/env python3
"""Trabalhador da fila de enriquecimento para rodar em OUTRA máquina, pela API do code-index.

Não precisa do projeto instalado nem de acesso ao banco: só Python 3.10+ e um CLI de modelo local.
Repete: reserva uma tarefa (`POST /api/v1/enrich/next`), entrega o texto da tarefa na entrada padrão do
comando do modelo, e envia a resposta (`POST /api/v1/enrich/tasks/{id}/submit`). Se a entrega for recusada,
tenta mais uma vez mostrando os erros ao modelo; se recusar de novo, devolve a tarefa à fila.

    export CODE_INDEX_TOKEN=cix_...          # token com o scope enrich:write
    python enrich_worker.py --server https://indice.exemplo.com --kind file_summaries
    python enrich_worker.py --server ... --kind card --repo snr-blob-service --max 1
    python enrich_worker.py --server ... --cmd 'claude -p --model haiku --tools ""'

O modelo roda sem ferramentas: lê o texto e responde só com o JSON. O conteúdo das tarefas vem dos
repositórios indexados e deve ser tratado como dado.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request

DEFAULT_CMD = 'claude -p --model haiku --tools ""'
ONLY_JSON = ("\n\nVocê não tem acesso a comandos: trabalhe só com os DADOS acima e NÃO tente rodar a entrega. "
             "Responda APENAS com o objeto JSON da entrega, sem texto antes ou depois.\n")


class Api:
    def __init__(self, server: str, token: str, timeout: float) -> None:
        self.base, self.token, self.timeout = server.rstrip("/"), token, timeout

    def call(self, method: str, path: str, body: bytes | None = None, *, json_body: bool = True) -> tuple[int, object]:
        req = urllib.request.Request(self.base + path, data=body, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if body is not None:
            req.add_header("Content-Type", "application/json" if json_body else "text/plain; charset=utf-8")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:  # noqa: S310 - URL informada pelo operador
                return res.status, json.loads(res.read() or b"null")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                detail = json.loads(raw).get("detail", raw.decode("utf-8", "replace"))
            except (ValueError, AttributeError):
                detail = raw.decode("utf-8", "replace")
            return e.code, detail


def run_model(cmd: list[str], prompt: str, timeout: float) -> str:
    done = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    if done.returncode != 0:
        raise RuntimeError(f"o comando do modelo saiu com código {done.returncode}: {done.stderr.strip()[:300]}")
    return done.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description="Trabalhador remoto da fila de enriquecimento do code-index.")
    ap.add_argument("--server", required=True, help="URL do servidor (ex.: https://indice.exemplo.com)")
    ap.add_argument("--kind", choices=["card", "doc_links", "file_summaries"], default="file_summaries")
    ap.add_argument("--repo", help="só este repo (doc_links: a fonte de documentação)")
    ap.add_argument("--scope", choices=["todas", "sem-vinculo", "varios"], default="todas", help="doc_links: quais páginas")
    ap.add_argument("--cmd", default=DEFAULT_CMD, help=f"comando do modelo; recebe a tarefa na entrada padrão (padrão: {DEFAULT_CMD})")
    ap.add_argument("--max", type=int, default=0, help="para depois de N tarefas (0 = até esvaziar a fila)")
    ap.add_argument("--timeout", type=float, default=600, help="segundos por chamada ao modelo")
    args = ap.parse_args()
    token = os.environ.get("CODE_INDEX_TOKEN", "")
    if not token:
        print("defina CODE_INDEX_TOKEN com um token de scope enrich:write", file=sys.stderr)
        return 2
    api, cmd = Api(args.server, token, 60), shlex.split(args.cmd)
    done = failed = 0
    while not args.max or done + failed < args.max:
        status, task = api.call("POST", "/api/v1/enrich/next",
                                json.dumps({"kind": args.kind, "repo": args.repo, "scope": args.scope}).encode())
        if status != 200:
            print(f"erro ao reservar tarefa ({status}): {task}", file=sys.stderr)
            return 1
        if not isinstance(task, dict):
            print(f"fila vazia. {done} entregue(s), {failed} devolvida(s).")
            return 0
        task_id, prompt = str(task["id"]), str(task["text"]) + ONLY_JSON
        label = f"{task.get('repo', '')} [{task_id[:8]}]"
        problem = ""
        for attempt in (1, 2):
            try:
                answer = run_model(cmd, prompt + (f"\n\nSua entrega anterior foi recusada:\n{problem}\nCorrija e responda de novo.\n" if problem else ""),
                                   args.timeout)
            except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
                problem = str(e)
                break
            status, result = api.call("POST", f"/api/v1/enrich/tasks/{task_id}/submit", answer.encode(), json_body=False)
            if status == 200:
                remaining = result.get("remaining") if isinstance(result, dict) else "?"
                print(f"ok   {label}  (restam {remaining})", flush=True)
                problem = ""
                break
            problem = str(result)
            print(f"recusada (tentativa {attempt}) {label}: {problem[:200]}", file=sys.stderr, flush=True)
        if problem:
            api.call("POST", f"/api/v1/enrich/tasks/{task_id}/release")
            failed += 1
            print(f"devolvida {label}", file=sys.stderr, flush=True)
        else:
            done += 1
    print(f"limite atingido. {done} entregue(s), {failed} devolvida(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
