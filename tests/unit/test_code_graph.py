"""Grafo de chamadas: extração com tree-sitter (indexing/call_scan) e resolução pura (domain/code_graph)."""

from __future__ import annotations

from pathlib import Path

import pytest

from code_index.domain.code_graph import (
    Call, Definition, FileFacts, Import, TsConfig, go_package_dir, js_module_paths, python_module_index,
    python_module_paths, resolve_repo,
)
from code_index.indexing.call_scan import _ts_config, extract, scan_calls, simple_type


def _edges(files: dict[str, tuple[str, str]], **kw: object) -> set[tuple[str, str, str]]:
    facts = [extract(src, path, lang) for path, (lang, src) in files.items()]
    res = resolve_repo([f for f in facts if f is not None], **kw)  # type: ignore[arg-type]
    return {(e.src_name or "(arquivo)", e.dst_name, e.via) for e in res.edges}


JAVA = {
    "src/main/java/com/x/billing/Billing.java": ("java", """package com.x.billing;

import com.x.mail.Sender;
import static com.x.util.Num.round2;

public class Billing extends BaseService {
    private final Sender sender;

    Billing(Sender sender) {
        this.sender = sender;
    }

    public void charge(Order o) {
        Tax t = new Tax();
        double v = t.compute(o);
        sender.send(o, v);
        this.sender.send(o);
        audit("x");
        round2(v);
        helper();
        new Tax().compute(o);
        list.add(o);
    }

    private void helper() {
    }
}
"""),
    "src/main/java/com/x/billing/Tax.java": ("java", """package com.x.billing;

public class Tax {
    double compute(Order o) {
        return 1;
    }
}
"""),
    "src/main/java/com/x/billing/BaseService.java": ("java", """package com.x.billing;

public abstract class BaseService {
    protected void audit(String s) {
    }
}
"""),
    "src/main/java/com/x/mail/Sender.java": ("java", """package com.x.mail;

public class Sender {
    public void send(Object o, double v) {
    }

    public void send(Object o) {
    }
}
"""),
    "src/main/java/com/x/util/Num.java": ("java", """package com.x.util;

public class Num {
    public static double round2(double v) {
        return v;
    }
}
"""),
}


def test_java_resolve_por_tipo_pacote_import_estatico_e_heranca() -> None:
    edges = _edges(JAVA)
    assert ("Billing.charge", "Tax", "pacote") in edges                 # new Tax()
    assert ("Billing.charge", "Tax.compute", "tipo") in edges           # variável local e new Tax().compute()
    assert ("Billing.charge", "Sender.send", "tipo") in edges           # campo, com e sem this.
    assert ("Billing.charge", "BaseService.audit", "herança") in edges
    assert ("Billing.charge", "Num.round2", "import") in edges          # import static
    assert ("Billing.charge", "Billing.helper", "arquivo") in edges
    assert not any(dst.endswith(".add") for _, dst, _ in edges)         # list.add: de fora, sem chute


def test_java_sobrecarga_pela_quantidade_de_argumentos() -> None:
    facts = [extract(src, p, lang) for p, (lang, src) in JAVA.items()]
    res = resolve_repo([f for f in facts if f is not None])
    lines = {e.dst_line for e in res.edges if e.dst_name == "Sender.send"}
    assert lines == {4, 7}   # send(o, v) e send(o): uma aresta para cada sobrecarga


def test_typescript_import_relativo_tsconfig_campo_do_construtor_e_inject() -> None:
    files = {
        "web/src/app/svc.ts": ("typescript", """import { Http } from '~core/http';
import * as fmt from '@/utils/fmt';
import { inject } from '@angular/core';
import { Store } from './store';

export class Svc {
  private store = inject(Store);
  constructor(private http: Http) {}
  load(id: string) {
    this.http.get2(id);
    fmt.money(1);
    this.local();
    const h = new Http();
    h.post2();
    this.store.save2();
  }
  local() { helperFn(); }
}
function helperFn() {}
"""),
        "web/src/app/core/http.ts": ("typescript", "export class Http {\n  get2(x: string) {}\n  post2() {}\n}\n"),
        "web/src/app/store.ts": ("typescript", "export class Store {\n  save2() {}\n}\n"),
        "web/src/utils/fmt.ts": ("typescript", "export function money(v: number) {\n  return v;\n}\n"),
    }
    cfg = TsConfig(dir="web", base="web", paths=(("~core/*", ("web/src/app/core/*",)),))
    edges = _edges(files, ts_configs=[cfg])
    assert ("Svc.load", "Http.get2", "tipo") in edges            # constructor(private http: Http)
    assert ("Svc.load", "Http.post2", "tipo") in edges           # const h = new Http()
    assert ("Svc.load", "Store.save2", "tipo") in edges          # inject(Store)
    assert ("Svc.load", "money", "import") in edges              # @/ → src/ mais próximo
    assert ("Svc.load", "Svc.local", "arquivo") in edges
    assert ("Svc.local", "helperFn", "arquivo") in edges


def test_javascript_require_e_jsx() -> None:
    files = {
        "app/main.js": ("javascript", "const { sum } = require('./lib/math');\nconst m = require('./lib/math');\n"
                                      "function go() {\n  sum(1, 2);\n  m.mul(2, 3);\n  return <App/>;\n}\n"),
        "app/lib/math.js": ("javascript", "function sum(a, b) { return a + b; }\nfunction mul(a, b) { return a * b; }\n"
                                          "module.exports = { sum, mul };\n"),
    }
    edges = _edges(files)
    assert ("go", "sum", "import") in edges
    assert ("go", "mul", "import") in edges


def test_python_imports_relativos_self_campos_e_heranca() -> None:
    files = {
        "app/service.py": ("python", """from .repo import Repo
from app import util
import app.util as u2


class Service(BaseSvc):
    def __init__(self, repo: Repo):
        self.repo = repo

    def run(self, x):
        self.repo.fetch(x)
        util.fmt(x)
        u2.fmt(x)
        self.inherited()

        def inner():
            self.run(1)
        top_level()


def top_level():
    r = Repo()
    r.fetch(1)
    Repo().fetch(2)


class BaseSvc:
    def inherited(self):
        pass
"""),
        "app/repo.py": ("python", "class Repo:\n    def fetch(self, x):\n        return x\n"),
        "app/util.py": ("python", "def fmt(x):\n    return x\n"),
    }
    edges = _edges(files)
    assert ("Service.run", "Repo.fetch", "tipo") in edges        # self.repo = repo (repo: Repo)
    assert ("Service.run", "fmt", "import") in edges              # from app import util; import app.util as u2
    assert ("Service.run", "BaseSvc.inherited", "herança") in edges
    assert ("inner", "Service.run", "arquivo") in edges           # self dentro de closure do método
    assert ("top_level", "Repo", "import") in edges
    assert ("top_level", "Repo.fetch", "tipo") in edges


def test_go_pacote_receptor_campos_de_struct_e_import_pelo_go_mod() -> None:
    files = {
        "internal/server/server.go": ("go", """package server

import (
\t"fmt"
\t"github.com/o/r/internal/db"
)

type Server struct {
\tstore *db.Store
\tc     *Client
}

func (s *Server) Run() {
\ts.store.Get()
\ts.c.Do()
\ts.helper()
\tdb.Open()
\tfmt.Println()
\tNew()
}

func (s *Server) helper() {}

func New() *Server { return &Server{} }
"""),
        "internal/server/client.go": ("go", "package server\n\ntype Client struct{}\n\nfunc (c *Client) Do() {}\n"),
        "internal/db/db.go": ("go", "package db\n\ntype Store struct{}\n\nfunc (s *Store) Get() {}\n\nfunc Open() {}\n"),
    }
    edges = _edges(files, go_modules=[("github.com/o/r", "")])
    assert ("Server.Run", "Store.Get", "tipo") in edges            # campo de struct com tipo de outro pacote
    assert ("Server.Run", "Client.Do", "tipo") in edges            # método noutro arquivo do pacote
    assert ("Server.Run", "Server.helper", "tipo") in edges
    assert ("Server.Run", "Open", "import") in edges
    assert ("Server.Run", "New", "arquivo") in edges
    assert not any(dst == "Println" for _, dst, _ in edges)


def test_nome_unico_e_inferido_e_nome_comum_ou_ambiguo_nao_liga() -> None:
    a = FileFacts("a.js", "javascript", definitions=[Definition("caller", 1, 9, "function")],
                  calls=[Call(2, 0, "formatMoney"),                          # função solta, única: liga (inferida)
                         Call(3, 0, "processInvoice", "ident", "obj"),       # método, receptor desconhecido: não
                         Call(4, 0, "formatMoney", "other"),                 # com receptor, nem função solta
                         Call(5, 0, "twice"),                                # dois "twice": ambíguo
                         Call(6, 0, "getTotal"), Call(7, 0, "save")])        # getter e nome comum: não
    b = FileFacts("b.js", "javascript", definitions=[
        Definition("Billing", 1, 20, "class"), Definition("processInvoice", 2, 3, "method", "Billing"),
        Definition("save", 4, 5, "function"), Definition("twice", 6, 7, "method", "Billing"),
        Definition("getTotal", 8, 9, "function"), Definition("formatMoney", 10, 11, "function")])
    c = FileFacts("c.js", "javascript", definitions=[Definition("twice", 1, 2, "function")])
    res = resolve_repo([a, b, c])
    assert {(e.dst_name, e.via, e.calls) for e in res.edges} == {("formatMoney", "nome", 1)}
    assert res.inferred == 1 and res.calls == 6


def test_nome_nao_vale_em_java() -> None:
    a = FileFacts("A.java", "java", definitions=[Definition("A", 1, 9, "class"), Definition("run", 2, 8, "method", "A")],
                  calls=[Call(3, 1, "processInvoice", "other", cls="A"), Call(4, 1, "Billing", new=True, cls="A")])
    b = FileFacts("x/B.java", "java", package="x", definitions=[
        Definition("Billing", 1, 9, "class"), Definition("processInvoice", 2, 3, "method", "Billing")])
    assert resolve_repo([a, b]).edges == []   # sem import nem mesmo pacote: é de fora


def test_recursao_nao_vira_aresta() -> None:
    f = FileFacts("a.py", "python", definitions=[Definition("loop", 1, 3, "function")],
                  calls=[Call(2, 0, "loop")])
    assert resolve_repo([f]).edges == []


def test_modulos() -> None:
    paths = {"web/src/a.ts", "web/src/b/index.tsx", "web/src/c.ts"}
    assert js_module_paths("web/src/x.ts", "./a", paths) == ["web/src/a.ts"]
    assert js_module_paths("web/src/x.ts", "./b", paths) == ["web/src/b/index.tsx"]
    assert js_module_paths("web/src/x.ts", "./c.js", paths) == ["web/src/c.ts"]   # ESM: .js aponta o .ts
    assert js_module_paths("web/src/x.ts", "react", paths) == []
    py = {"src/pkg/__init__.py", "src/pkg/a.py", "other/a.py"}
    idx = python_module_index(py)
    assert python_module_paths("src/pkg/b.py", ".a", idx, py) == ["src/pkg/a.py"]
    assert python_module_paths("x.py", "pkg.a", idx, py) == ["src/pkg/a.py"]
    assert python_module_paths("x.py", "a", idx, py) == []   # dois módulos "a": ambíguo
    assert go_package_dir("github.com/o/r/pkg/x", [("github.com/o/r", "svc")]) == "svc/pkg/x"
    assert go_package_dir("github.com/o/other", [("github.com/o/r", "")]) is None


@pytest.mark.parametrize(("text", "expected"), [
    ("List<Foo>", "List"), ("*Server", "Server"), (": Http", "Http"), ("Foo[]", "Foo"),
    ("db.Store", "db.Store"), ("string | null", ""), ("Foo?", "Foo"),
])
def test_simple_type(text: str, expected: str) -> None:
    assert simple_type(text) == expected


def test_tsconfig_com_comentarios_e_virgula_sobrando(tmp_path: Path) -> None:
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "tsconfig.json").write_text(
        '{\n  // comentário\n  "compilerOptions": {\n    "baseUrl": "./",\n    /* outro */\n'
        '    "paths": { "~app/*": ["src/app/*"], },\n  },\n}\n', encoding="utf-8")
    cfg = _ts_config(tmp_path, "web/tsconfig.json")
    assert cfg == TsConfig(dir="web", base="web", paths=(("~app/*", ("web/src/app/*",)),))


def test_scan_calls_usa_os_filtros_do_repo_e_ignora_links(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def a():\n    b()\n\ndef b():\n    pass\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("function x() { y(); }\nfunction y() {}\n", encoding="utf-8")
    (tmp_path / "link.py").symlink_to(tmp_path / "src" / "a.py")
    res = scan_calls(tmp_path, ["**"], ["node_modules"], 1_000_000)
    assert res.files == 1
    assert [(e.src_path, e.src_name, e.dst_name, e.via) for e in res.edges] == [("src/a.py", "a", "b", "arquivo")]


def test_extract_ignora_linguagem_sem_extrator() -> None:
    assert extract("fn main() {}", "main.rs", "rust") is None
    assert Import("x", "y").symbol == ""
