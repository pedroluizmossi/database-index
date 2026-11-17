"""Grafo de chamadas (puro, sem I/O): fatos de cada arquivo e a resolução "quem chama quem" dentro de um repo.

O extrator (`indexing/call_scan.py`, tree-sitter) anota o que está escrito em cada arquivo: definições com as
linhas que ocupam, imports, tipos de campos e chamadas (nome + o que está à esquerda do ponto). Aqui cada
chamada vira, quando dá para saber, uma aresta definição → definição. A resolução é por nome e por tipo
declarado, sem compilar: o que não dá para provar (nome ambíguo, biblioteca de fora, tipo desconhecido) fica
sem aresta. Uma aresta nunca é inventada por semelhança.

`via` diz como o alvo foi achado, da mais para a menos confiável (só `nome` é inferida):
- `arquivo`: definido no mesmo arquivo;
- `pacote`: mesmo pacote Java ou mesma pasta Go;
- `import`: pelo import do arquivo;
- `tipo`: pelo tipo declarado de quem recebe a chamada (variável, parâmetro ou campo);
- `herança`: num tipo base da classe;
- `nome`: única definição com esse nome no repo (inferida).
"""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

EXTRACTOR_VERSION = 1   # mudou a extração ou a resolução? suba: os repos são relidos no próximo sync

# Linguagem (como `file_language` devolve) → família do extrator.
FAMILIES = {
    "java": "java",
    "typescript": "ts", "tsx": "ts", "javascript": "ts", "jsx": "ts",
    "python": "python",
    "go": "go",
}

VIAS = ("arquivo", "pacote", "import", "tipo", "herança", "nome")
INFERRED_VIAS = frozenset({"nome"})

# Nomes comuns demais para ligar só pelo nome (métodos de coleções, strings, logs, promessas, ciclo de vida).
COMMON_NAMES = frozenset("""
get set put add remove delete clear contains has size length count keys values items entries map filter
reduce find some every sort push pop shift unshift slice splice concat join split trim replace format parse
stringify then catch finally resolve reject emit on off once subscribe pipe next complete error warn info
debug log trace print println printf append extend update insert upsert save load read write open close
flush start stop run init setup dispose destroy apply call bind toString equals hashCode compareTo clone
valueOf getClass iterator stream collect forEach of from build builder execute handle process create
validate test check render main new copy merge equal len str int list dict encode decode lower upper strip
lstrip rstrip startswith endswith includes indexOf substring charAt match exec assign isEmpty toLowerCase
toUpperCase action callback handler onSuccess onError done reset refresh
""".split())

MAX_BASE_DEPTH = 5
_ACCESSOR = re.compile(r"^(?:get|set|is|has)[A-Z_]")   # getters/setters existem em quase toda classe


@dataclass(frozen=True)
class Definition:
    name: str
    line: int
    end_line: int
    kind: str              # class | function | method
    container: str = ""    # classe (ou tipo do receptor, em Go) que contém o método
    params: int = -1       # quantidade de parâmetros (-1 = desconhecida ou variável)

    @property
    def qualified(self) -> str:
        return f"{self.container}.{self.name}" if self.container else self.name


@dataclass(frozen=True)
class Import:
    local: str             # nome usado no arquivo ("" num import com curinga)
    module: str            # como está escrito: "com.x.Foo", "./a", ".m", "a.b", "github.com/o/r/pkg"
    symbol: str = ""       # nome importado do módulo ("" = o módulo inteiro; "*" = tudo)


@dataclass(frozen=True)
class Call:
    line: int
    caller: int            # índice da definição que contém a chamada (-1 = nível do arquivo)
    name: str
    recv_kind: str = ""    # "" sem receptor | self | super | type | field | ident | other
    recv: str = ""         # type: nome do tipo; field: "Classe.campo"; ident: nome (ou "a.b")
    args: int = -1
    new: bool = False      # construção (new Foo(...)): o alvo é a classe
    cls: str = ""          # classe em que a chamada está escrita (inclusive dentro de closures dos métodos)


@dataclass
class FileFacts:
    path: str
    language: str
    package: str = ""                                                 # pacote Java
    definitions: list[Definition] = field(default_factory=list)
    imports: list[Import] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    bases: dict[str, tuple[str, ...]] = field(default_factory=dict)   # classe → tipos base
    fields: dict[str, dict[str, str]] = field(default_factory=dict)   # classe → campo → tipo

    @property
    def family(self) -> str:
        return FAMILIES.get(self.language, "")


@dataclass(frozen=True)
class Edge:
    src_path: str
    src_line: int          # 0 = código no nível do arquivo
    src_name: str
    dst_path: str
    dst_line: int
    dst_name: str
    via: str
    line: int              # primeira linha em que a chamada aparece
    calls: int = 1         # quantas chamadas do mesmo trecho para o mesmo alvo


@dataclass
class GraphResult:
    edges: list[Edge]
    files: int = 0
    definitions: int = 0
    calls: int = 0
    by_via: dict[str, int] = field(default_factory=dict)   # chamadas resolvidas por via

    @property
    def resolved(self) -> int:
        return sum(self.by_via.values())

    @property
    def inferred(self) -> int:
        return sum(n for v, n in self.by_via.items() if v in INFERRED_VIAS)


Target = tuple[str, Definition, str]     # (caminho, definição, via)


@dataclass(frozen=True)
class TsConfig:
    """`compilerOptions` de um tsconfig/jsconfig, com caminhos já relativos à raiz do repo."""

    dir: str                                          # pasta do arquivo de configuração
    base: str | None                                  # baseUrl (None se não declarado)
    paths: tuple[tuple[str, tuple[str, ...]], ...] = ()   # "~shared/*" → ("src/app/shared/*",)


# ------------------------------------------------------------------ módulos → arquivos (por linguagem)

_JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")


def _norm(path: str) -> str:
    p = posixpath.normpath(path)
    return "" if p == "." else p


def _js_candidates(base: str, paths: frozenset[str] | set[str]) -> list[str]:
    stem = base
    for ext in _JS_EXTS:
        if stem.endswith(ext):   # import "./a.js" com o fonte em a.ts (ESM com TypeScript)
            stem = stem[: -len(ext)]
            break
    for c in (base, *(stem + e for e in _JS_EXTS), *(f"{base}/index{e}" for e in _JS_EXTS)):
        if c in paths:
            return [c]
    return []


def nearest_ts_config(from_path: str, configs: Sequence[TsConfig]) -> TsConfig | None:
    """O tsconfig com `paths`/`baseUrl` mais próximo do arquivo (pasta dele ou acima)."""
    best: TsConfig | None = None
    for c in configs:
        if (c.dir == "" or from_path.startswith(c.dir + "/")) and (best is None or len(c.dir) > len(best.dir)):
            best = c
    return best


def js_module_paths(from_path: str, spec: str, paths: frozenset[str] | set[str],
                    configs: Sequence[TsConfig] = ()) -> list[str]:
    """Arquivos que um import JS/TS pode abrir: relativo ao arquivo, pelos `paths`/`baseUrl` do tsconfig mais
    próximo, ou `@/`/`~/` (apelido comum de `src/`). Pacote do npm (`react`, `@angular/core`): lista vazia."""
    if not spec.startswith("."):
        config = nearest_ts_config(from_path, configs)
        if config is not None:
            for key, targets in config.paths:
                if key.endswith("*") and spec.startswith(key[:-1]):
                    rest = spec[len(key) - 1:]
                elif key == spec:
                    rest = ""
                else:
                    continue
                for t in targets:
                    found = _js_candidates(_norm(t.replace("*", rest)), paths)
                    if found:
                        return found
            if config.base is not None:
                found = _js_candidates(_norm(posixpath.join(config.base, spec)), paths)
                if found:
                    return found
    if spec.startswith("."):
        base = _norm(posixpath.join(posixpath.dirname(from_path), spec))
    elif spec.startswith(("@/", "~/")):   # apelido de src/: a pasta src/ mais próxima do arquivo (monorepo)
        head, sep, _ = from_path.rpartition("/src/")
        src_dir = f"{head}/src" if sep else "src"
        base = _norm(f"{src_dir}/{spec[2:]}")
    else:
        return []
    return _js_candidates(base, paths)


def python_module_index(paths: Iterable[str]) -> dict[str, list[str]]:
    """Nome de módulo → arquivos. Cada .py entra por todos os sufixos do caminho ("src/pkg/a.py" vira "src.pkg.a",
    "pkg.a" e "a"), porque a raiz dos pacotes (src/, a pasta do serviço...) varia por repo."""
    index: dict[str, list[str]] = defaultdict(list)
    for p in paths:
        if not p.endswith(".py"):
            continue
        parts = p[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        for i in range(len(parts)):
            index[".".join(parts[i:])].append(p)
    return index


def python_module_paths(from_path: str, module: str, index: dict[str, list[str]],
                        paths: frozenset[str] | set[str]) -> list[str]:
    if module.startswith("."):
        dots = len(module) - len(module.lstrip("."))
        rest = module[dots:]
        base = posixpath.dirname(from_path)
        for _ in range(dots - 1):
            base = posixpath.dirname(base)
        target = posixpath.join(base, *rest.split(".")) if rest else base
        target = _norm(target)
        for c in (f"{target}.py", f"{target}/__init__.py"):
            if c in paths:
                return [c]
        return []
    found = index.get(module, [])
    if len(found) == 1:
        return found
    exact = [p for p in found if p in (module.replace(".", "/") + ".py", module.replace(".", "/") + "/__init__.py")]
    return exact[:1]   # mais de um arquivo com esse nome de módulo: ambíguo


def go_package_dir(import_path: str, modules: Sequence[tuple[str, str]]) -> str | None:
    """Pasta do repo de um import Go, pelos `go.mod` do repo: (caminho do módulo, pasta do go.mod)."""
    best: tuple[int, str] | None = None
    for mod, mod_dir in modules:
        if import_path == mod or import_path.startswith(mod + "/"):
            rest = import_path[len(mod):].lstrip("/")
            d = _norm(posixpath.join(mod_dir, rest)) if (mod_dir or rest) else ""
            if best is None or len(mod) > best[0]:
                best = (len(mod), d)
    return None if best is None else best[1]


# ------------------------------------------------------------------ resolução


class _Repo:
    """Índices do repo inteiro para resolver nomes."""

    def __init__(self, files: Sequence[FileFacts], go_modules: Sequence[tuple[str, str]] = (),
                 ts_configs: Sequence[TsConfig] = ()) -> None:
        self.files = {f.path: f for f in files}
        self.paths = frozenset(self.files)
        self.go_modules = list(go_modules)
        self.ts_configs = list(ts_configs)
        self.py_index = python_module_index(self.paths)
        self.dir_files: dict[str, list[str]] = defaultdict(list)
        self.classes: dict[str, list[tuple[str, Definition]]] = defaultdict(list)     # nome → classes
        self.callables: dict[str, list[tuple[str, Definition]]] = defaultdict(list)   # nome → funções/métodos
        self.fqcn: dict[str, tuple[str, Definition]] = {}                             # Java: pacote.Classe
        self.package_classes: dict[str, list[tuple[str, Definition]]] = defaultdict(list)
        self.members: dict[tuple[str, str, str], list[Definition]] = defaultdict(list)  # (arquivo, classe, nome)
        self.go_members: dict[tuple[str, str, str], list[tuple[str, Definition]]] = defaultdict(list)  # (pasta, tipo, nome)
        for f in files:
            d = posixpath.dirname(f.path)
            self.dir_files[d].append(f.path)
            for df in f.definitions:
                if df.kind == "class":
                    self.classes[df.name].append((f.path, df))
                    if f.family == "java":
                        qualified = df.qualified if df.container else df.name
                        self.fqcn[f"{f.package}.{qualified}" if f.package else qualified] = (f.path, df)
                        self.package_classes[f.package].append((f.path, df))
                else:
                    self.callables[df.name].append((f.path, df))
                    self.members[(f.path, df.container, df.name)].append(df)
                    if f.family == "go" and df.container:
                        self.go_members[(d, df.container, df.name)].append((f.path, df))

    # -- tipos

    def find_type(self, facts: FileFacts, name: str) -> list[Target]:
        """Classe/tipo `name` como visto de dentro de `facts` (sem genéricos; "pkg.T" em Go)."""
        name = name.strip()
        if not name:
            return []
        if "." in name and facts.family in ("go", "python", "ts"):
            alias, _, rest = name.partition(".")
            return [t for t in self._module_members(facts, alias, rest) if t[1].kind == "class"]
        if "." in name and facts.family == "java":
            hit = self.fqcn.get(name)
            if hit:
                return [(hit[0], hit[1], "import")]
            name = name.rsplit(".", 1)[-1]
        local = [(facts.path, d, "arquivo") for d in facts.definitions if d.kind == "class" and d.name == name]
        if local:
            return local[:1]
        for imp in facts.imports:
            if imp.local == name:
                found = [t for t in self._import_targets(facts, imp) if t[1].kind == "class"]
                if found:
                    return found[:1]
        fam = facts.family
        if fam == "java":
            same = [(p, d, "pacote") for p, d in self.package_classes.get(facts.package, []) if d.name == name]
            if same:
                return same[:1]
            for imp in facts.imports:
                if imp.symbol == "*":
                    hit = self.fqcn.get(f"{imp.module}.{name}")
                    if hit:
                        return [(hit[0], hit[1], "import")]
        if fam == "go":
            d0 = posixpath.dirname(facts.path)
            same = [(p, d, "pacote") for p, d in self.classes.get(name, []) if posixpath.dirname(p) == d0]
            if same:
                return same[:1]
        every = self.classes.get(name, [])
        if len(every) == 1 and fam not in ("go", "java"):   # Java/Go exigem import ou mesmo pacote: o resto é de fora
            return [(every[0][0], every[0][1], "nome")]
        return []

    def find_member(self, path: str, cls: Definition, name: str, args: int, depth: int = 0) -> list[Target]:
        """Método `name` da classe `cls` (definida em `path`) ou de um tipo base."""
        facts = self.files[path]
        if facts.family == "go":
            in_dir = self.go_members.get((posixpath.dirname(path), cls.name, name), [])
            return [(in_dir[0][0], in_dir[0][1], "tipo")] if in_dir else []
        candidates = self.members.get((path, cls.name, name), [])
        if candidates:
            return [(path, _by_arity(candidates, args), "tipo")]
        if depth >= MAX_BASE_DEPTH:
            return []
        for base in facts.bases.get(cls.name, ()):
            for bpath, bdef, _ in self.find_type(facts, base):
                found = self.find_member(bpath, bdef, name, args, depth + 1)
                if found:
                    return [(found[0][0], found[0][1], "herança")]
        return []

    def field_type(self, facts: FileFacts, owner: str, field_name: str) -> list[tuple[FileFacts, str]]:
        """Tipo declarado de `owner.field_name` (owner = classe) → (arquivo da classe, nome do tipo)."""
        out = []
        owners = [(facts.path, d) for d in facts.definitions if d.kind == "class" and d.name == owner] \
            or [(p, d) for p, d, _ in self.find_type(facts, owner)]
        for p, d in owners:
            f = self.files[p]
            t = f.fields.get(d.name, {}).get(field_name)
            if not t and f.family == "go":   # campos de struct ficam no arquivo do tipo; o método pode estar em outro
                for sibling in self.dir_files.get(posixpath.dirname(p), []):
                    t = self.files[sibling].fields.get(d.name, {}).get(field_name)
                    if t:
                        f = self.files[sibling]
                        break
            if t:
                out.append((f, t))
        return out

    # -- módulos

    def _module_paths(self, facts: FileFacts, imp: Import) -> list[str]:
        fam = facts.family
        if fam == "ts":
            return js_module_paths(facts.path, imp.module, self.paths, self.ts_configs)
        if fam == "python":
            found = python_module_paths(facts.path, imp.module, self.py_index, self.paths)
            if imp.symbol and imp.symbol != "*":   # from pkg import modulo
                sub = imp.module + ("" if imp.module.endswith(".") else ".") + imp.symbol
                sub_paths = python_module_paths(facts.path, sub, self.py_index, self.paths)
                if sub_paths and not any(d.name == imp.symbol for p in found for d in self.files[p].definitions):
                    return sub_paths
            return found
        if fam == "go":
            d = go_package_dir(imp.module, self.go_modules)
            return [] if d is None else list(self.dir_files.get(d, []))
        return []

    def _import_targets(self, facts: FileFacts, imp: Import) -> list[Target]:
        """Definições que um import traz com o nome local dele."""
        if facts.family == "java":
            hit = self.fqcn.get(imp.module)
            if hit and not imp.symbol:
                return [(hit[0], hit[1], "import")]
            if hit and imp.symbol and imp.symbol != "*":   # import static Classe.metodo
                return [(hit[0], d, "import") for d in self.members.get((hit[0], hit[1].name, imp.symbol), [])][:1]
            return []
        paths = self._module_paths(facts, imp)
        if not imp.symbol or imp.symbol == "*":
            return []   # módulo inteiro: os membros saem por _module_members
        if facts.family == "python" and paths and not any(d.name == imp.symbol for d in self.files[paths[0]].definitions):
            return []   # from pkg import modulo (é um módulo, não uma definição)
        symbol = imp.symbol
        out: list[Target] = []
        for p in paths:
            f = self.files[p]
            if symbol == "default":   # import X from './x': a classe/função exportada como padrão (aprox.: o nome X)
                hits = [d for d in f.definitions if d.name == imp.local and not d.container]
            else:
                hits = [d for d in f.definitions if d.name == symbol and not d.container]
            out += [(p, d, "import") for d in hits[:1]]
        return out[:1]

    def _module_members(self, facts: FileFacts, alias: str, name: str) -> list[Target]:
        """`alias.name` em que `alias` é um módulo importado (import * as ns, import pkg, pacote Go)."""
        for imp in facts.imports:
            if imp.local != alias:
                continue
            if facts.family == "python" and imp.symbol and imp.symbol != "*":
                sub = imp.module + ("" if imp.module.endswith(".") else ".") + imp.symbol
                paths = python_module_paths(facts.path, sub, self.py_index, self.paths)
            elif imp.symbol and imp.symbol != "*" and facts.family != "go":
                continue
            else:
                paths = self._module_paths(facts, imp)
            for p in paths:
                hits = [d for d in self.files[p].definitions if d.name == name and not d.container]
                if hits:
                    return [(p, hits[0], "import")]
        return []

    def has_import(self, facts: FileFacts, local: str) -> bool:
        return any(i.local == local for i in facts.imports)

    # -- chamadas

    def resolve(self, facts: FileFacts, call: Call) -> Target | None:
        cls = self._class_named(facts, call.cls, call.line)
        kind, name = call.recv_kind, call.name

        if call.new:
            found = self.find_type(facts, name if not call.recv else f"{call.recv}.{name}")
            return found[0] if found else None

        if kind == "":
            return self._unqualified(facts, cls, call)
        if kind == "self":
            if cls is not None:
                found = self.find_member(facts.path, cls, name, call.args)
                if found:
                    p, d, via = found[0]
                    return (p, d, "arquivo" if via == "tipo" and p == facts.path else via)
            return None
        if kind == "super":
            if cls is not None:
                for base in facts.bases.get(cls.name, ()):
                    for bpath, bdef, _ in self.find_type(facts, base):
                        found = self.find_member(bpath, bdef, name, call.args)
                        if found:
                            return (found[0][0], found[0][1], "herança")
            return None
        if kind == "type":
            return self._on_type(facts, call.recv, call)
        if kind == "field":
            owner, _, fname = call.recv.partition(".")
            for f2, t in self.field_type(facts, owner, fname):
                hit = self._on_type(f2, t, call)
                if hit:
                    return hit
            return None
        if kind == "ident":
            found = self._module_members(facts, call.recv, name)
            if found:
                return found[0]
            if self.has_import(facts, call.recv) and facts.family != "java":
                return None   # módulo/pacote de fora do repo
            types = self.find_type(facts, call.recv)
            if types:   # Classe.metodoEstatico()
                p, d, via = types[0]
                found = self.find_member(p, d, name, call.args)
                if found:
                    return (found[0][0], found[0][1], via if via != "tipo" else "tipo")
                return None
            return None   # receptor desconhecido (variável sem tipo, classe/pacote de fora): sem chute pelo nome
        return None   # other: cadeia a.b().c(), sem tipo conhecido

    def _on_type(self, facts: FileFacts, type_name: str, call: Call) -> Target | None:
        for p, d, _ in self.find_type(facts, type_name):
            found = self.find_member(p, d, call.name, call.args)
            if found:
                via = found[0][2]
                return (found[0][0], found[0][1], "herança" if via == "herança" else "tipo")
        return None   # tipo conhecido mas de fora do repo (String, List...): sem chute pelo nome

    def _unqualified(self, facts: FileFacts, cls: Definition | None, call: Call) -> Target | None:
        name = call.name
        fam = facts.family
        if cls is not None and fam == "java":   # método da própria classe (Java sem this.)
            found = self.find_member(facts.path, cls, name, call.args)
            if found:
                p, d, via = found[0]
                return (p, d, "arquivo" if via == "tipo" and p == facts.path else via)
        local = [d for d in facts.definitions if d.name == name and not d.container and d.kind != "class"]
        if local:
            return (facts.path, local[0], "arquivo")
        local_cls = [d for d in facts.definitions if d.name == name and d.kind == "class"]
        if local_cls and fam in ("python", "ts"):
            return (facts.path, local_cls[0], "arquivo")
        for imp in facts.imports:
            if imp.local == name:
                found = self._import_targets(facts, imp)
                if found:
                    return found[0]
                return None   # importado de fora do repo
        if fam == "java":
            for imp in facts.imports:   # import static Classe.*
                if imp.symbol == "*":
                    hit = self.fqcn.get(imp.module)
                    if hit:
                        members = self.members.get((hit[0], hit[1].name, name), [])
                        if members:
                            return (hit[0], _by_arity(members, call.args), "import")
            return None   # Java sem receptor: método da classe, de uma base ou de fora
        if fam == "go":
            d0 = posixpath.dirname(facts.path)
            for p, d in self.callables.get(name, []):
                if posixpath.dirname(p) == d0 and not d.container:
                    return (p, d, "pacote")
            return None
        return self._by_name(facts, name, functions_only=True)   # sem receptor: em TS/JS/Python não é método

    def _by_name(self, facts: FileFacts, name: str, *, functions_only: bool = False) -> Target | None:
        """Chamada solta `foo()` sem definição no arquivo nem import: a única função solta com esse nome no repo
        (inferida; global de script, módulo carregado de outro jeito). Só para chamadas sem receptor: `x.reset()` com
        `x` sem tipo é quase sempre de biblioteca (Angular, jQuery) e ligaria ao `reset` errado. Não vale em Java (o
        que não se resolveu é de biblioteca), nem para nomes comuns e getters/setters."""
        if facts.family == "java" or len(name) < 4 or name in COMMON_NAMES or _ACCESSOR.match(name):
            return None
        found = self.callables.get(name, [])   # único entre TODAS as definições, antes de olhar o tipo
        if functions_only and len(found) == 1 and found[0][1].container:
            return None
        if len(found) == 1:
            return (found[0][0], found[0][1], "nome")
        return None

    @staticmethod
    def _class_named(facts: FileFacts, name: str, line: int) -> Definition | None:
        """A classe `name` que contém a linha (a mais interna); em Go, o tipo do receptor, que pode estar noutro
        arquivo do pacote (os métodos são achados pela pasta)."""
        if not name:
            return None
        inside = [d for d in facts.definitions if d.kind == "class" and d.name == name and d.line <= line <= d.end_line]
        if inside:
            return max(inside, key=lambda d: d.line)
        same = [d for d in facts.definitions if d.kind == "class" and d.name == name]
        return same[0] if same else Definition(name, 0, 0, "class")


def _by_arity(candidates: Sequence[Definition], args: int) -> Definition:
    """Sobrecargas: a primeira com a mesma quantidade de parâmetros; senão, a primeira."""
    if args >= 0:
        for d in candidates:
            if d.params == args:
                return d
    return candidates[0]


def resolve_repo(files: Sequence[FileFacts], go_modules: Sequence[tuple[str, str]] = (),
                 ts_configs: Sequence[TsConfig] = ()) -> GraphResult:
    """Arestas de chamada de um repo. Agrupa por (trecho que chama, alvo): `calls` conta as repetições."""
    repo = _Repo(files, go_modules, ts_configs)
    edges: dict[tuple[str, int, str, int, str], Edge] = {}
    by_via: dict[str, int] = defaultdict(int)
    n_calls = 0
    for facts in files:
        for call in facts.calls:
            n_calls += 1
            hit = repo.resolve(facts, call)
            if hit is None:
                continue
            dst_path, dst, via = hit
            caller = facts.definitions[call.caller] if 0 <= call.caller < len(facts.definitions) else None
            src_line, src_name = (caller.line, caller.qualified) if caller else (0, "")
            if dst_path == facts.path and src_line == dst.line:
                continue   # recursão
            by_via[via] += 1
            key = (facts.path, src_line, dst_path, dst.line, dst.qualified)
            old = edges.get(key)
            if old is None:
                edges[key] = Edge(facts.path, src_line, src_name, dst_path, dst.line, dst.qualified, via, call.line)
            else:
                best = old.via if VIAS.index(old.via) <= VIAS.index(via) else via
                edges[key] = Edge(old.src_path, old.src_line, old.src_name, old.dst_path, old.dst_line, old.dst_name,
                                  best, min(old.line, call.line), old.calls + 1)
    return GraphResult(
        edges=list(edges.values()), files=len(files), definitions=sum(len(f.definitions) for f in files),
        calls=n_calls, by_via=dict(by_via),
    )
