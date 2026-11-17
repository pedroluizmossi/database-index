"""Casos de teste (puro): o que é arquivo de teste, os casos de cada um e o código de produção que testam.

Testes ficam fora do índice de trechos e dos embeddings (custam tokens e competiriam com o código na busca). Mesmo
assim, dizem o comportamento esperado do código. Por isso o sync lê os arquivos de teste dos clones (sem Voyage),
extrai os casos (nome, `describe`/classe que os envolve, linha) e liga cada arquivo ao código testado:
  1. pelo nome do arquivo: `AuthServiceTest.java`, `auth.spec.ts`, `test_auth.py`, `auth_test.go` → sujeito
     `AuthService`/`auth`, procurado entre as declarações (classe) ou nomes de arquivo do repo; sem nome exato, uma
     classe que começa pelo sujeito (`RefreshTokenTest` → `RefreshTokenHandler`), citada pelos casos ou única no
     mesmo pacote;
  2. sem sujeito no nome: a classe do repo citada em mais casos do arquivo (só se for a única nessa posição).
Cada caso guarda os membros do arquivo testado que ele chama (`target_symbols`), para achar o teste de um método.

A extração é por expressões regulares, por família de linguagem: cobre os frameworks comuns (JUnit, Kotest,
Spock, xUnit/NUnit/MSTest, Jest/Mocha/Vitest/Jasmine, RSpec, pytest/unittest, testing do Go, PHPUnit).
Mudou a regra? Suba `EXTRACTOR_VERSION`: o próximo sync relê os testes de todos os repos.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from .file_patterns import TEST_EXCLUDE

EXTRACTOR_VERSION = 2   # 2: ligação por prefixo do nome da classe
MAX_FILE_BYTES = 512 * 1024
MAX_CASES_PER_FILE = 500
MAX_TARGET_SYMBOLS = 10
_MAX_NAME = 300

_TEST_PARTS = tuple(p.removeprefix("**/") for p in TEST_EXCLUDE)
# Arquivos de configuração de teste casam com TEST_EXCLUDE, mas não têm casos
TEST_EXTENSIONS = (".java", ".kt", ".kts", ".groovy", ".scala", ".cs", ".js", ".jsx", ".ts", ".tsx", ".mjs",
                   ".cjs", ".mts", ".cts", ".py", ".go", ".rb", ".php")


def is_test_path(path: str) -> bool:
    """O caminho cai nos padrões de teste (TEST_EXCLUDE): pasta de teste ou nome de arquivo de teste."""
    parts = PurePosixPath(path).parts
    return any(fnmatchcase(part, pattern) for part in parts for pattern in _TEST_PARTS)


def without_test_excludes(excludes: Iterable[str]) -> list[str]:
    """Os excludes de um repo sem os padrões de teste (o resto, como node_modules e os do usuário, continua)."""
    tests = set(TEST_EXCLUDE)
    return [e for e in excludes if e not in tests]


def family(path: str) -> str | None:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in (".java", ".kt", ".kts", ".groovy", ".scala", ".cs"):
        return "jvm"
    if suffix in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"):
        return "js"
    return {".py": "python", ".go": "go", ".rb": "ruby", ".php": "php"}.get(suffix)


_SUBJECT = re.compile(
    r"^(?:test_(?P<a>.+)|(?P<b>.+?)(?:_test|_spec|\.test|\.spec|\.cy|Tests?|IT|TestCase|Spec))$"
)


def subject_of(path: str) -> str | None:
    """O que o arquivo testa, pelo nome: AuthServiceTest.java → AuthService; auth.service.spec.ts → auth.service;
    test_auth.py → auth. Nome sem marca de teste (TestHelpers.java, conftest.py) → None."""
    name = PurePosixPath(path).name
    if family(path) == "js":
        for marker in (".test.", ".spec.", ".cy."):
            if marker in name:
                return name.split(marker)[0] or None
        return None
    m = _SUBJECT.match(PurePosixPath(path).stem)
    if not m:
        return None
    return (m.group("a") or m.group("b") or "").strip("_") or None


@dataclass(frozen=True)
class TestCase:
    __test__ = False   # o pytest não deve coletar esta classe

    line: int
    name: str
    context: tuple[str, ...] = ()        # describe/classe/função que envolve o caso, de fora para dentro
    idents: frozenset[str] = frozenset()  # identificadores usados no corpo (para ligar a métodos)

    @property
    def display(self) -> str:
        return " > ".join([*self.context, self.name])


# ------------------------------------------------------------------ extração por família

_IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
_JVM_MARK = re.compile(
    r"@(?:Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate|Property)\b"
    r"|\[(?:Fact|Theory|Test|TestMethod|TestCase|DataTestMethod)\b"
)
_JVM_DISPLAY = re.compile(r"@DisplayName\(\s*\"((?:[^\"\\]|\\.)*)\"")
_JVM_METHOD = re.compile(
    r"(?:\bfun\s+(?:`(?P<k>[^`]+)`|(?P<kf>\w+))\s*\("                   # Kotlin
    r"|\bdef\s+(?:\"(?P<g>[^\"]+)\"|'(?P<g2>[^']+)'|(?P<gf>\w+))\s*\("  # Groovy/Spock
    r"|\b(?:void|Task|async\s+Task|Unit|def)\s+(?P<j>\w+)\s*\("          # Java/C#/Scala
    r"|\b(?P<jt>test\w*)\s*\(\s*\)\s*(?:throws\b[^{]*)?\{)"             # JUnit 3 sem modificador de retorno
)
_JVM_CLASS = re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s*)*(?:public\s+|internal\s+|abstract\s+|open\s+|final\s+)*"
                        r"(?:class|object)\s+(\w+)")
_JUNIT3 = re.compile(r"\bpublic\s+void\s+(test\w*)\s*\(\s*\)")
_SPOCK = re.compile(r"^\s*def\s+\"([^\"]+)\"\s*\(\s*\)")
_KOTEST = re.compile(r"^\s*\"([^\"]+)\"\s*(?:\{|-\s*>|\.config)")

_JS_CALL = re.compile(
    r"^(?P<indent>\s*)(?P<fn>describe|context|suite|fdescribe|xdescribe|it|test|specify|fit|xit)"
    r"(?:\.(?:only|skip|concurrent|todo|each\s*(?:\([^)]*\)|`[^`]*`)))*\s*\(\s*"
    r"(?:(?P<q>['\"`])(?P<name>(?:(?!(?P=q)).)*)(?P=q))"
)
_RUBY_CALL = re.compile(
    r"^(?P<indent>\s*)(?:RSpec\.)?(?P<fn>describe|context|feature|scenario|it|specify)\s*\(?\s*"
    r"(?:(?P<q>['\"])(?P<name>(?:(?!(?P=q)).)*)(?P=q)|(?P<const>[A-Z][\w:]*))"
)
_PY_DEF = re.compile(r"^(?P<indent>\s*)(?:async\s+)?def\s+(?P<name>test\w*)\s*\(")
_PY_CLASS = re.compile(r"^(?P<indent>\s*)class\s+(?P<name>\w+)")
_GO_FUNC = re.compile(r"^func\s+(?P<name>Test\w+)\s*\(\s*\w+\s+\*testing\.T\s*\)")
_GO_RUN = re.compile(r"\b\w+\.Run\(\s*\"(?P<name>(?:[^\"\\]|\\.)*)\"")
_PHP_FUNC = re.compile(r"\bfunction\s+(?P<name>test\w*)\s*\(")
_PHP_ANNOT = re.compile(r"@test\b|#\[Test\]")
_PHP_ANY_FUNC = re.compile(r"\bfunction\s+(?P<name>\w+)\s*\(")
_JS_SUITES = {"describe", "context", "suite", "fdescribe", "xdescribe", "feature"}


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", name.replace("\\'", "'").replace('\\"', '"')).strip()[:_MAX_NAME]


def _jvm(lines: list[str]) -> list[tuple[int, str, tuple[str, ...]]]:
    out: list[tuple[int, str, tuple[str, ...]]] = []
    classes: list[tuple[int, str]] = []    # (indentação, nome)
    pending_mark: int | None = None
    display: str | None = None
    for i, line in enumerate(lines, start=1):
        indent = len(line) - len(line.lstrip())
        if line.strip():   # linha no mesmo nível (ou fora) de uma classe aninhada: saiu dela
            while classes and classes[-1][0] >= indent:
                classes.pop()
        m_class = _JVM_CLASS.match(line)
        if m_class:
            classes.append((indent, m_class.group(1)))
        ctx = tuple(name for _, name in classes)
        if d := _JVM_DISPLAY.search(line):
            display = _clean(d.group(1))
        if _JVM_MARK.search(line):
            pending_mark = i
        spock = _SPOCK.match(line)
        if spock:
            out.append((i, _clean(spock.group(1)), ctx))
            pending_mark, display = None, None
            continue
        kotest = _KOTEST.match(line)
        if kotest and pending_mark is None:
            out.append((i, _clean(kotest.group(1)), ctx))
            continue
        if pending_mark is not None:
            m = _JVM_METHOD.search(line)
            if m:
                raw = next(g for g in m.groups() if g)
                out.append((i, display or _clean(raw), ctx))
                pending_mark, display = None, None
            elif i - pending_mark > 6:   # anotação sem método perto: desiste
                pending_mark, display = None, None
            continue
        junit3 = _JUNIT3.search(line)
        if junit3:
            out.append((i, junit3.group(1), ctx))
    return out


def _js_like(lines: list[str], call: re.Pattern[str]) -> list[tuple[int, str, tuple[str, ...]]]:
    out: list[tuple[int, str, tuple[str, ...]]] = []
    suites: list[tuple[int, str]] = []
    for i, line in enumerate(lines, start=1):
        m = call.match(line)
        if not m:
            continue
        indent = len(m.group("indent").expandtabs(4))
        while suites and suites[-1][0] >= indent:
            suites.pop()
        name = _clean(m.group("name") or (m.groupdict().get("const") or ""))
        if not name:
            continue
        if m.group("fn") in _JS_SUITES or m.group("fn") == "describe":
            suites.append((indent, name))
        else:
            out.append((i, name, tuple(n for _, n in suites)))
    return out


def _python(lines: list[str]) -> list[tuple[int, str, tuple[str, ...]]]:
    out: list[tuple[int, str, tuple[str, ...]]] = []
    classes: list[tuple[int, str]] = []
    for i, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        while classes and classes[-1][0] >= indent:
            classes.pop()
        if c := _PY_CLASS.match(line):
            classes.append((indent, c.group("name")))
        elif d := _PY_DEF.match(line):
            out.append((i, d.group("name"), tuple(n for _, n in classes)))
    return out


def _go(lines: list[str]) -> list[tuple[int, str, tuple[str, ...]]]:
    out: list[tuple[int, str, tuple[str, ...]]] = []
    current: str | None = None
    for i, line in enumerate(lines, start=1):
        if f := _GO_FUNC.match(line):
            current = f.group("name")
            out.append((i, current, ()))
        elif line.startswith("func "):
            current = None
        elif current and (r := _GO_RUN.search(line)):
            out.append((i, _clean(r.group("name")), (current,)))
    return out


def _php(lines: list[str]) -> list[tuple[int, str, tuple[str, ...]]]:
    out: list[tuple[int, str, tuple[str, ...]]] = []
    marked = False
    for i, line in enumerate(lines, start=1):
        if _PHP_ANNOT.search(line):
            marked = True
        if m := _PHP_FUNC.search(line):
            out.append((i, m.group("name"), ()))
            marked = False
        elif marked and (m := _PHP_ANY_FUNC.search(line)):
            out.append((i, m.group("name"), ()))
            marked = False
    return out


def extract_test_cases(text: str, path: str) -> list[TestCase]:
    """Casos de teste de um arquivo, na ordem, com os identificadores usados no corpo de cada um
    (do caso até o próximo). Linguagem sem extrator → []."""
    fam = family(path)
    lines = text.split("\n")
    if fam == "jvm":
        found = _jvm(lines)
    elif fam == "js":
        found = _js_like(lines, _JS_CALL)
    elif fam == "ruby":
        found = _js_like(lines, _RUBY_CALL)
    elif fam == "python":
        found = _python(lines)
    elif fam == "go":
        found = _go(lines)
    elif fam == "php":
        found = _php(lines)
    else:
        return []
    found = found[:MAX_CASES_PER_FILE]
    out: list[TestCase] = []
    seen: set[tuple[int, str]] = set()
    for n, (line, name, ctx) in enumerate(found):
        if (line, name) in seen:
            continue
        seen.add((line, name))
        end = found[n + 1][0] - 1 if n + 1 < len(found) else len(lines)
        body = "\n".join(lines[line - 1:end])
        out.append(TestCase(line, name, ctx, frozenset(_IDENT.findall(body))))
    return out


# ------------------------------------------------------------------ ligação ao código de produção

@dataclass(frozen=True)
class Declaration:
    """Declaração de produção (code_symbols): nome, arquivo, tipo (container | member) e classe envolvente."""

    name: str
    path: str
    kind: str
    container: str = ""


@dataclass
class LinkedFile:
    path: str
    subject: str | None
    target_path: str | None
    how: str                                   # "nome" | "citações" | ""
    cases: list[tuple[TestCase, list[str]]] = field(default_factory=list)   # (caso, membros do alvo que chama)


def _shared_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(PurePosixPath(a).parts, PurePosixPath(b).parts):
        if x != y:
            break
        n += 1
    return n


def _shared_package(a: str, b: str) -> int:
    """Pastas finais iguais (o pacote): src/test/java/x/handler/T.java ↔ src/main/java/x/handler/H.java → 2."""
    n = 0
    for x, y in zip(reversed(PurePosixPath(a).parent.parts), reversed(PurePosixPath(b).parent.parts)):
        if x != y:
            break
        n += 1
    return n


def _closest(test_path: str, candidates: Iterable[str]) -> str | None:
    """O candidato mais perto do teste: mais pastas iniciais em comum (módulo) e, no empate, mais pastas finais em
    comum (pacote: src/test/java/x ↔ src/main/java/x). Empate nos dois → None."""
    def score(c: str) -> tuple[int, int]:
        return _shared_prefix(test_path, c), _shared_package(test_path, c)

    ranked = sorted(set(candidates), key=lambda c: (tuple(-v for v in score(c)), c))
    if not ranked:
        return None
    if len(ranked) > 1 and score(ranked[0]) == score(ranked[1]):
        return None
    return ranked[0]


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


MIN_PREFIX = 6   # sujeito curto ("User", "Token") casaria com classes demais


def _by_prefix(test_path: str, key: str, cases: Sequence[TestCase], declarations: Sequence[Declaration]) -> str | None:
    """Classe cujo nome começa pelo sujeito (RefreshTokenTest → RefreshTokenHandler), quando não há classe com o
    nome exato. Para reduzir ligação errada: vale a citada pelos casos (a mais perto do teste); sem citação, só a
    única do mesmo pacote (mesma pasta final que o teste)."""
    if len(key) < MIN_PREFIX:
        return None
    found = [d for d in declarations if d.kind == "container" and _norm(d.name).startswith(key) and _norm(d.name) != key]
    if not found:
        return None
    cited = {n for c in cases for n in c.idents}
    by_citation = [d.path for d in found if d.name in cited]
    if by_citation:
        return _closest(test_path, by_citation)
    package = PurePosixPath(test_path).parent.name
    same_package = {d.path for d in found if PurePosixPath(d.path).parent.name == package}
    return next(iter(same_package)) if len(same_package) == 1 else None


def find_target(test_path: str, cases: Sequence[TestCase], declarations: Sequence[Declaration],
                production_paths: Sequence[str]) -> tuple[str | None, str | None, str]:
    """(sujeito, arquivo testado, como foi achado) de um arquivo de teste."""
    subject = subject_of(test_path)
    if subject:
        key = _norm(subject)
        by_class = [d.path for d in declarations if d.kind == "container" and _norm(d.name) == key]
        target = _closest(test_path, by_class) if by_class else None
        if target is None:
            by_file = [p for p in production_paths if _norm(PurePosixPath(p).name.split(".")[0]) == key
                       or _norm(PurePosixPath(p).stem) == key]
            target = _closest(test_path, by_file)
        if target:
            return subject, target, "nome"
        target = _by_prefix(test_path, key, cases, declarations)
        if target:
            return subject, target, "prefixo"
    # sem sujeito pelo nome: a classe de produção citada em mais casos, se for a única nessa posição
    containers: dict[str, list[str]] = {}
    for d in declarations:
        if d.kind == "container":
            containers.setdefault(d.name, []).append(d.path)
    counts = Counter(name for c in cases for name in c.idents if name in containers)
    if not counts:
        return subject, None, ""
    top = counts.most_common(2)
    if len(top) > 1 and top[0][1] == top[1][1]:
        return subject, None, ""
    target = _closest(test_path, containers[top[0][0]])
    return subject, target, "citações" if target else ""


def link_file(test_path: str, cases: Sequence[TestCase], declarations: Sequence[Declaration],
              production_paths: Sequence[str]) -> LinkedFile:
    """Arquivo testado e, para cada caso, os membros dele que o caso chama."""
    subject, target, how = find_target(test_path, cases, declarations, production_paths)
    members: set[str] = set()
    if target:
        members = {d.name for d in declarations if d.path == target and d.kind == "member"}
    out = LinkedFile(test_path, subject, target, how)
    for case in cases:
        called = sorted(n for n in case.idents if n in members)[:MAX_TARGET_SYMBOLS]
        out.cases.append((case, called))
    return out


def group_declarations(rows: Iterable[tuple[str, str, str, str]]) -> list[Declaration]:
    """(name, path, kind, container) → Declaration, sem as de arquivos de teste."""
    return [Declaration(name, path, kind, container) for name, path, kind, container in rows if not is_test_path(path)]


def target_symbol_index(cases: Iterable[tuple[str, Sequence[str]]]) -> Mapping[str, int]:
    """Quantos casos chamam cada membro (para ordenar o resultado de find_tests)."""
    return Counter(s for _, symbols in cases for s in symbols)
