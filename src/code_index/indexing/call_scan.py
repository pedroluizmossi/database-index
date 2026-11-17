"""Extração das chamadas com tree-sitter, lendo o clone (sem CocoIndex e sem embedding).

Para cada arquivo Java, TypeScript/TSX, JavaScript, Python ou Go: definições (com as linhas que ocupam),
imports, tipos dos campos e das variáveis e as chamadas. A resolução (quem chama quem) é pura e fica em
`domain/code_graph.py`. Usa os mesmos filtros do repo que a indexação (include/exclude), sem links simbólicos.
Só nomes e números de linha saem daqui: nada do conteúdo dos arquivos é gravado.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
from collections.abc import Callable, Sequence
from functools import cache
from pathlib import Path

import tree_sitter as ts

from ..domain.code_graph import FAMILIES, Call, Definition, FileFacts, GraphResult, Import, TsConfig, resolve_repo
from ..domain.path_glob import glob_regex
from .content import file_language, looks_minified

MAX_DEPTH = 400        # árvores mais fundas que isso (código gerado) param de descer
_IDENT = re.compile(r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$")
_GENERIC = re.compile(r"<.*>|\[.*\]")
_GO_VERSION = re.compile(r"^v\d+$")


@cache
def _parser(language: str) -> ts.Parser:
    if language == "java":
        import tree_sitter_java as mod
        return ts.Parser(ts.Language(mod.language()))
    if language in ("typescript", "tsx"):
        import tree_sitter_typescript as tsmod
        lang = tsmod.language_tsx() if language == "tsx" else tsmod.language_typescript()
        return ts.Parser(ts.Language(lang))
    if language in ("javascript", "jsx"):
        import tree_sitter_javascript as jsmod
        return ts.Parser(ts.Language(jsmod.language()))
    if language == "python":
        import tree_sitter_python as pymod
        return ts.Parser(ts.Language(pymod.language()))
    if language == "go":
        import tree_sitter_go as gomod
        return ts.Parser(ts.Language(gomod.language()))
    raise ValueError(f"linguagem sem extrator: {language}")


def simple_type(text: str) -> str:
    """Nome do tipo sem genéricos, ponteiros, arrays nem opcionais ("" se não for um nome simples)."""
    t = text.strip().lstrip("*&").removeprefix(":").strip()
    t = _GENERIC.sub("", t).rstrip("?").strip()
    return t if _IDENT.match(t) else ""


class _Extractor:
    """Percorre a árvore uma vez. Pilhas: definições (quem chama), classes (contêiner) e escopos (tipos das
    variáveis locais e parâmetros)."""

    def __init__(self, src: bytes, path: str, language: str) -> None:
        self.src = src
        self.facts = FileFacts(path=path, language=language)
        self.defs: list[int] = []
        self.classes: list[str] = []
        self.scopes: list[dict[str, str]] = [{}]

    # -- utilitários

    def text(self, node: ts.Node | None) -> str:
        if node is None:
            return ""
        return self.src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    @staticmethod
    def line(node: ts.Node) -> int:
        return node.start_point[0] + 1

    @property
    def cls(self) -> str:
        return self.classes[-1] if self.classes else ""

    def lookup(self, name: str) -> str:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return ""

    def declare(self, name: str, type_name: str) -> None:
        t = simple_type(type_name)
        if name and t:
            self.scopes[-1][name] = t

    def add_field(self, name: str, type_name: str) -> None:
        t = simple_type(type_name)
        if self.cls and name and t:
            self.facts.fields.setdefault(self.cls, {})[name] = t

    def add_def(self, node: ts.Node, name: str, kind: str, params: int = -1) -> int:
        container = "" if kind == "function" else self.cls   # função solta (ou aninhada) não tem contêiner
        self.facts.definitions.append(Definition(
            name=name, line=self.line(node), end_line=node.end_point[0] + 1, kind=kind,
            container=container, params=params))
        return len(self.facts.definitions) - 1

    def add_call(self, node: ts.Node, name: str, recv_kind: str = "", recv: str = "", args: int = -1,
                 new: bool = False) -> None:
        if not name:
            return
        self.facts.calls.append(Call(
            line=self.line(node), caller=self.defs[-1] if self.defs else -1, name=name,
            recv_kind=recv_kind, recv=recv, args=args, new=new, cls=self.cls))

    def run(self, root: ts.Node) -> FileFacts:
        self.walk(root, 0)
        return self.facts

    def walk(self, node: ts.Node, depth: int) -> None:
        if depth > MAX_DEPTH:
            return
        handler: Callable[[ts.Node, int], bool] | None = getattr(self, "on_" + node.type, None)
        if handler is not None and handler(node, depth):
            return   # o handler já desceu nos filhos
        for child in node.children:
            self.walk(child, depth + 1)

    def walk_children(self, node: ts.Node | None, depth: int) -> None:
        if node is not None:
            for child in node.children:
                self.walk(child, depth + 1)

    def enter_def(self, node: ts.Node, name: str, kind: str, body: ts.Node | None, depth: int,
                  params: int = -1, scope: dict[str, str] | None = None, as_class: bool = False) -> None:
        idx = self.add_def(node, name, kind, params)
        self.defs.append(idx)
        if as_class:
            self.classes.append(name)
        self.scopes.append(dict(scope or {}))
        try:
            self.walk_children(body if body is not None else node, depth)
        finally:
            self.scopes.pop()
            if as_class:
                self.classes.pop()
            self.defs.pop()


def _args(node: ts.Node | None) -> int:
    return -1 if node is None else len(node.named_children)


# ------------------------------------------------------------------ Java


class _Java(_Extractor):
    _CLASSES = ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration",
                "annotation_type_declaration")

    def on_package_declaration(self, node: ts.Node, depth: int) -> bool:
        for c in node.named_children:
            if c.type in ("scoped_identifier", "identifier"):
                self.facts.package = self.text(c)
        return True

    def on_import_declaration(self, node: ts.Node, depth: int) -> bool:
        static = any(c.type == "static" for c in node.children)
        star = any(c.type == "asterisk" for c in node.children)
        name = next((self.text(c) for c in node.named_children if c.type in ("scoped_identifier", "identifier")), "")
        if not name:
            return True
        if star:
            self.facts.imports.append(Import("", name, "*"))
        elif static:
            module, _, symbol = name.rpartition(".")
            self.facts.imports.append(Import(symbol, module, symbol))
        else:
            self.facts.imports.append(Import(name.rsplit(".", 1)[-1], name, ""))
        return True

    def _class(self, node: ts.Node, depth: int) -> bool:
        name = self.text(node.child_by_field_name("name"))
        bases: list[str] = []
        for c in node.children:
            if c.type in ("superclass", "super_interfaces", "extends_interfaces"):
                bases += [simple_type(self.text(t)) for t in _type_nodes(c)]
        if name:
            self.facts.bases[name] = tuple(b for b in bases if b)
        self.enter_def(node, name, "class", node.child_by_field_name("body"), depth, as_class=True)
        return True

    on_class_declaration = on_interface_declaration = on_enum_declaration = _class
    on_record_declaration = on_annotation_type_declaration = _class

    def on_field_declaration(self, node: ts.Node, depth: int) -> bool:
        type_name = self.text(node.child_by_field_name("type"))
        for d in node.children_by_field_name("declarator"):
            self.add_field(self.text(d.child_by_field_name("name")), type_name)
        return False   # inicializadores podem ter chamadas

    def _method(self, node: ts.Node, depth: int) -> bool:
        name = self.text(node.child_by_field_name("name"))
        params = node.child_by_field_name("parameters")
        scope: dict[str, str] = {}
        count = 0
        variadic = False
        for p in params.named_children if params is not None else []:
            if p.type == "formal_parameter":
                count += 1
                t = simple_type(self.text(p.child_by_field_name("type")))
                n = self.text(p.child_by_field_name("name"))
                if t and n:
                    scope[n] = t
            elif p.type == "spread_parameter":
                variadic = True
        self.enter_def(node, name, "method", node.child_by_field_name("body"), depth,
                       params=-1 if variadic else count, scope=scope)
        return True

    on_method_declaration = on_constructor_declaration = _method

    def on_local_variable_declaration(self, node: ts.Node, depth: int) -> bool:
        type_name = self.text(node.child_by_field_name("type"))
        for d in node.children_by_field_name("declarator"):
            n = self.text(d.child_by_field_name("name"))
            value = d.child_by_field_name("value")
            if type_name == "var" and value is not None and value.type == "object_creation_expression":
                self.declare(n, self.text(value.child_by_field_name("type")))
            elif type_name != "var":
                self.declare(n, type_name)
        return False

    def on_enhanced_for_statement(self, node: ts.Node, depth: int) -> bool:
        self.declare(self.text(node.child_by_field_name("name")), self.text(node.child_by_field_name("type")))
        return False

    def on_method_invocation(self, node: ts.Node, depth: int) -> bool:
        name = self.text(node.child_by_field_name("name"))
        obj = node.child_by_field_name("object")
        args = _args(node.child_by_field_name("arguments"))
        kind, recv = self._receiver(obj)
        self.add_call(node, name, kind, recv, args)
        return False

    def _receiver(self, obj: ts.Node | None) -> tuple[str, str]:
        if obj is None:
            return "", ""
        if obj.type == "this":
            return "self", ""
        if obj.type == "super":
            return "super", ""
        if obj.type == "identifier":
            n = self.text(obj)
            t = self.lookup(n)
            if t:
                return "type", t
            if n[:1].isupper():
                return "ident", n
            return ("field", f"{self.cls}.{n}") if self.cls else ("ident", n)
        if obj.type == "field_access":
            o = obj.child_by_field_name("object")
            f = self.text(obj.child_by_field_name("field"))
            if o is not None and o.type == "this" and self.cls:
                return "field", f"{self.cls}.{f}"
            if o is not None and _IDENT.match(self.text(obj)):
                return "ident", self.text(obj)   # com.x.Classe.metodo() ou Classe.CONSTANTE
        if obj.type == "object_creation_expression":   # new Foo().bar()
            t = simple_type(self.text(obj.child_by_field_name("type")))
            if t:
                return "type", t
        return "other", ""

    def on_object_creation_expression(self, node: ts.Node, depth: int) -> bool:
        t = simple_type(self.text(node.child_by_field_name("type")))
        if t:
            self.add_call(node, t, args=_args(node.child_by_field_name("arguments")), new=True)
        return False


def _type_nodes(node: ts.Node) -> list[ts.Node]:
    """Tipos citados num `extends`/`implements` (Java)."""
    out = []
    for c in node.named_children:
        if c.type == "type_list":
            out += list(c.named_children)
        else:
            out.append(c)
    return out


# ------------------------------------------------------------------ TypeScript / JavaScript


class _TS(_Extractor):
    def on_import_statement(self, node: ts.Node, depth: int) -> bool:
        source = node.child_by_field_name("source")
        module = self.text(source).strip("'\"`")
        if not module:
            return True
        for clause in node.named_children:
            if clause.type != "import_clause":
                continue
            for c in clause.named_children:
                if c.type == "identifier":
                    self.facts.imports.append(Import(self.text(c), module, "default"))
                elif c.type == "namespace_import":
                    ident = next((x for x in c.named_children if x.type == "identifier"), None)
                    if ident is not None:
                        self.facts.imports.append(Import(self.text(ident), module, "*"))
                elif c.type == "named_imports":
                    for spec in c.named_children:
                        if spec.type != "import_specifier":
                            continue
                        name = self.text(spec.child_by_field_name("name"))
                        alias = self.text(spec.child_by_field_name("alias")) or name
                        self.facts.imports.append(Import(alias, module, name))
        return True

    def on_variable_declarator(self, node: ts.Node, depth: int) -> bool:
        name_node = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        name = self.text(name_node) if name_node is not None and name_node.type == "identifier" else ""
        if value is not None and value.type == "call_expression" and self.text(value.child_by_field_name("function")) == "require":
            module = self._string_arg(value)
            if module and name:
                self.facts.imports.append(Import(name, module, ""))
                return True
            if module and name_node is not None and name_node.type == "object_pattern":
                for p in name_node.named_children:
                    if p.type == "shorthand_property_identifier_pattern":
                        n = self.text(p)
                        self.facts.imports.append(Import(n, module, n))
                    elif p.type == "pair_pattern":
                        key = self.text(p.child_by_field_name("key"))
                        val = self.text(p.child_by_field_name("value"))
                        self.facts.imports.append(Import(val, module, key))
                return True
        if value is not None and value.type in ("arrow_function", "function_expression", "function") and name:
            kind = "method" if self.cls and not self.defs_is_function() else "function"
            self._function(value, name, kind, depth)
            return True
        type_ann = node.child_by_field_name("type")
        if name and type_ann is not None:
            self.declare(name, self.text(type_ann))
        elif name and value is not None:
            self.declare(name, self._value_type(value))
        return False

    def _value_type(self, value: ts.Node) -> str:
        """Tipo de `new Foo()` ou de `inject(Foo)` (injeção do Angular sem construtor)."""
        if value.type == "new_expression":
            return self.text(value.child_by_field_name("constructor"))
        if value.type == "call_expression" and self.text(value.child_by_field_name("function")) == "inject":
            args = value.child_by_field_name("arguments")
            if args is not None and args.named_children:
                return self.text(args.named_children[0])
        return ""

    def defs_is_function(self) -> bool:
        return bool(self.defs) and self.facts.definitions[self.defs[-1]].kind != "class"

    def _string_arg(self, call: ts.Node) -> str:
        args = call.child_by_field_name("arguments")
        if args is None or not args.named_children or args.named_children[0].type != "string":
            return ""
        return self.text(args.named_children[0]).strip("'\"`")

    def _function(self, node: ts.Node, name: str, kind: str, depth: int) -> None:
        params = node.child_by_field_name("parameters")
        scope, count, _ = self._params(params)
        self.enter_def(node, name, kind, node.child_by_field_name("body"), depth, params=count, scope=scope)

    def _params(self, params: ts.Node | None) -> tuple[dict[str, str], int, list[tuple[str, str]]]:
        """(tipos dos parâmetros, quantidade, propriedades de parâmetro do construtor TS)."""
        scope: dict[str, str] = {}
        props: list[tuple[str, str]] = []
        count = 0
        for p in params.named_children if params is not None else []:
            if p.type in ("required_parameter", "optional_parameter"):
                count += 1
                n = self.text(p.child_by_field_name("pattern"))
                t = self.text(p.child_by_field_name("type"))
                if n and t:
                    scope[n] = simple_type(t)
                if any(c.type in ("accessibility_modifier", "readonly") for c in p.children):
                    props.append((n, t))
            elif p.type in ("identifier", "assignment_pattern", "object_pattern", "array_pattern"):
                count += 1
            elif p.type == "rest_pattern":
                count = -1_000   # variádico: fica negativo
        return {k: v for k, v in scope.items() if v}, max(count, -1), props

    def _function_decl(self, node: ts.Node, depth: int) -> bool:
        name = self.text(node.child_by_field_name("name"))
        if not name:
            return False
        self._function(node, name, "function", depth)
        return True

    on_function_declaration = on_generator_function_declaration = _function_decl

    def _class(self, node: ts.Node, depth: int) -> bool:
        name = self.text(node.child_by_field_name("name"))
        if not name:
            return False
        bases: list[str] = []
        for c in node.named_children:
            if c.type == "class_heritage":
                for clause in c.named_children:
                    if clause.type in ("extends_clause", "implements_clause"):
                        bases += [simple_type(self.text(t)) for t in clause.named_children if t.type != "type_arguments"]
                    else:   # JavaScript: class A extends B
                        bases.append(simple_type(self.text(clause)))
        self.facts.bases[name] = tuple(b for b in bases if b)
        self.enter_def(node, name, "class", node.child_by_field_name("body"), depth, as_class=True)
        return True

    on_class_declaration = on_abstract_class_declaration = on_class = _class

    def on_method_definition(self, node: ts.Node, depth: int) -> bool:
        name = self.text(node.child_by_field_name("name"))
        params = node.child_by_field_name("parameters")
        scope, count, props = self._params(params)
        for n, t in props:   # constructor(private svc: Svc): campo da classe
            self.add_field(n, t)
        self.enter_def(node, name, "method", node.child_by_field_name("body"), depth, params=count, scope=scope)
        return True

    def _field(self, node: ts.Node, depth: int) -> bool:
        name_node = node.child_by_field_name("name") or node.child_by_field_name("property")
        name = self.text(name_node)
        value = node.child_by_field_name("value")
        type_ann = node.child_by_field_name("type")
        if type_ann is not None:
            self.add_field(name, self.text(type_ann))
        elif value is not None:
            self.add_field(name, self._value_type(value))
        if value is not None and value.type in ("arrow_function", "function_expression", "function") and name:
            self._function(value, name, "method", depth)   # handle = () => {...}
            return True
        return False

    on_public_field_definition = on_field_definition = _field

    def on_call_expression(self, node: ts.Node, depth: int) -> bool:
        fn = node.child_by_field_name("function")
        args = _args(node.child_by_field_name("arguments"))
        if fn is None:
            return False
        if fn.type == "identifier":
            name = self.text(fn)
            if name != "require":
                self.add_call(node, name, args=args)
        elif fn.type == "member_expression":
            name = self.text(fn.child_by_field_name("property"))
            kind, recv = self._receiver(fn.child_by_field_name("object"))
            self.add_call(node, name, kind, recv, args)
        return False

    def _receiver(self, obj: ts.Node | None) -> tuple[str, str]:
        if obj is None:
            return "other", ""
        if obj.type == "this":
            return "self", ""
        if obj.type == "super":
            return "super", ""
        if obj.type == "identifier":
            n = self.text(obj)
            t = self.lookup(n)
            return ("type", t) if t else ("ident", n)
        if obj.type == "member_expression":
            o = obj.child_by_field_name("object")
            prop = self.text(obj.child_by_field_name("property"))
            if o is not None and o.type == "this" and self.cls:
                return "field", f"{self.cls}.{prop}"
        if obj.type in ("new_expression", "parenthesized_expression"):   # new Foo().bar() / (new Foo()).bar()
            inner = obj if obj.type == "new_expression" else next(
                (c for c in obj.named_children if c.type == "new_expression"), None)
            t = simple_type(self._value_type(inner)) if inner is not None else ""
            if t:
                return "type", t
        return "other", ""

    def on_new_expression(self, node: ts.Node, depth: int) -> bool:
        ctor = node.child_by_field_name("constructor")
        args = _args(node.child_by_field_name("arguments"))
        if ctor is not None and ctor.type == "identifier":
            self.add_call(node, self.text(ctor), args=args, new=True)
        elif ctor is not None and ctor.type == "member_expression":
            o = ctor.child_by_field_name("object")
            if o is not None and o.type == "identifier":
                self.add_call(node, self.text(ctor.child_by_field_name("property")), recv=self.text(o), args=args, new=True)
        return False


# ------------------------------------------------------------------ Python


class _Python(_Extractor):
    def on_import_statement(self, node: ts.Node, depth: int) -> bool:
        for c in node.named_children:
            if c.type == "dotted_name":
                name = self.text(c)
                self.facts.imports.append(Import(name, name, ""))
            elif c.type == "aliased_import":
                module = self.text(c.child_by_field_name("name"))
                self.facts.imports.append(Import(self.text(c.child_by_field_name("alias")), module, ""))
        return True

    def on_import_from_statement(self, node: ts.Node, depth: int) -> bool:
        module = self.text(node.child_by_field_name("module_name"))
        if not module:
            return True
        for c in node.children_by_field_name("name"):
            if c.type == "dotted_name":
                n = self.text(c)
                self.facts.imports.append(Import(n, module, n))
            elif c.type == "aliased_import":
                n = self.text(c.child_by_field_name("name"))
                self.facts.imports.append(Import(self.text(c.child_by_field_name("alias")), module, n))
        if any(c.type == "wildcard_import" for c in node.named_children):
            self.facts.imports.append(Import("", module, "*"))
        return True

    def on_class_definition(self, node: ts.Node, depth: int) -> bool:
        name = self.text(node.child_by_field_name("name"))
        supers = node.child_by_field_name("superclasses")
        bases = [simple_type(self.text(c)) for c in (supers.named_children if supers is not None else [])
                 if c.type in ("identifier", "attribute")]
        self.facts.bases[name] = tuple(b for b in bases if b)
        self.enter_def(node, name, "class", node.child_by_field_name("body"), depth, as_class=True)
        return True

    def on_function_definition(self, node: ts.Node, depth: int) -> bool:
        name = self.text(node.child_by_field_name("name"))
        in_class = bool(self.defs) and self.facts.definitions[self.defs[-1]].kind == "class"
        params = node.child_by_field_name("parameters")
        scope: dict[str, str] = {}
        count = 0
        variadic = False
        for i, p in enumerate(params.named_children if params is not None else []):
            if in_class and i == 0 and p.type == "identifier":
                continue   # self/cls
            if p.type in ("list_splat_pattern", "dictionary_splat_pattern"):
                variadic = True
                continue
            if p.type in ("keyword_separator", "positional_separator"):
                continue
            count += 1
            if p.type in ("typed_parameter", "typed_default_parameter"):
                n = next((self.text(c) for c in p.named_children if c.type == "identifier"), "")
                if p.type == "typed_default_parameter":
                    n = self.text(p.child_by_field_name("name")) or n
                t = simple_type(self.text(p.child_by_field_name("type")))
                if n and t:
                    scope[n] = t
        self.enter_def(node, name, "method" if in_class else "function", node.child_by_field_name("body"), depth,
                       params=-1 if variadic else count, scope=scope)
        return True

    def on_assignment(self, node: ts.Node, depth: int) -> bool:
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        ann = node.child_by_field_name("type")
        t = ""
        if ann is not None:
            t = self.text(ann)
        elif right is not None and right.type == "call":
            fn = right.child_by_field_name("function")
            t = self.text(fn) if fn is not None and fn.type in ("identifier", "attribute") else ""
        elif right is not None and right.type == "identifier":
            t = self.lookup(self.text(right))
        if left is not None and left.type == "identifier" and t:
            if self.defs and self.facts.definitions[self.defs[-1]].kind == "class":
                self.add_field(self.text(left), t)   # atributo de classe
            else:
                self.declare(self.text(left), t)
        elif left is not None and left.type == "attribute" and t:
            o = left.child_by_field_name("object")
            if o is not None and self.text(o) == "self":
                self.add_field(self.text(left.child_by_field_name("attribute")), t)
        return False

    def on_call(self, node: ts.Node, depth: int) -> bool:
        fn = node.child_by_field_name("function")
        args = _args(node.child_by_field_name("arguments"))
        if fn is None:
            return False
        if fn.type == "identifier":
            self.add_call(node, self.text(fn), args=args)
        elif fn.type == "attribute":
            name = self.text(fn.child_by_field_name("attribute"))
            kind, recv = self._receiver(fn.child_by_field_name("object"))
            self.add_call(node, name, kind, recv, args)
        return False

    def _receiver(self, obj: ts.Node | None) -> tuple[str, str]:
        if obj is None:
            return "other", ""
        if obj.type == "identifier":
            n = self.text(obj)
            if n in ("self", "cls") and self.cls:
                return "self", ""
            t = self.lookup(n)
            return ("type", t) if t else ("ident", n)
        if obj.type == "call":
            fn = obj.child_by_field_name("function")
            if fn is not None and self.text(fn) == "super":
                return "super", ""
            if fn is not None and fn.type == "identifier" and self.text(fn)[:1].isupper():
                return "type", self.text(fn)   # Foo().bar(): convenção de classe com inicial maiúscula
        if obj.type == "attribute":
            o = obj.child_by_field_name("object")
            if o is not None and self.text(o) == "self" and self.cls:
                return "field", f"{self.cls}.{self.text(obj.child_by_field_name('attribute'))}"
            dotted = self.text(obj)
            if _IDENT.match(dotted):
                return "ident", dotted   # pacote.modulo.funcao()
        return "other", ""


# ------------------------------------------------------------------ Go


class _Go(_Extractor):
    def on_import_spec(self, node: ts.Node, depth: int) -> bool:
        path = self.text(node.child_by_field_name("path")).strip('"`')
        alias = self.text(node.child_by_field_name("name"))
        if not path or alias in ("_", "."):
            return True
        if not alias:
            parts = path.split("/")
            alias = parts[-2] if len(parts) > 1 and _GO_VERSION.match(parts[-1]) else parts[-1]
            alias = alias.replace("-", "_").removeprefix("go_").removeprefix("go.")
        self.facts.imports.append(Import(alias, path, ""))
        return True

    def on_type_spec(self, node: ts.Node, depth: int) -> bool:
        name = self.text(node.child_by_field_name("name"))
        body = node.child_by_field_name("type")
        if body is None or body.type not in ("struct_type", "interface_type") or not name:
            return False
        if body.type == "struct_type":
            self.classes.append(name)
            try:
                for fl in body.named_children:
                    for f in fl.named_children if fl.type == "field_declaration_list" else []:
                        t = self.text(f.child_by_field_name("type"))
                        for n in f.children_by_field_name("name"):
                            self.add_field(self.text(n), t)
            finally:
                self.classes.pop()
        self.facts.definitions.append(Definition(name, self.line(node), node.end_point[0] + 1, "class"))
        return True

    def _params(self, params: ts.Node | None, scope: dict[str, str]) -> int:
        count = 0
        for p in params.named_children if params is not None else []:
            if p.type == "variadic_parameter_declaration":
                return -1
            if p.type != "parameter_declaration":
                continue
            t = simple_type(self.text(p.child_by_field_name("type")))
            names = p.children_by_field_name("name")
            count += max(1, len(names))
            for n in names:
                if t:
                    scope[self.text(n)] = t
        return count

    def on_function_declaration(self, node: ts.Node, depth: int) -> bool:
        scope: dict[str, str] = {}
        count = self._params(node.child_by_field_name("parameters"), scope)
        self.enter_def(node, self.text(node.child_by_field_name("name")), "function",
                       node.child_by_field_name("body"), depth, params=count, scope=scope)
        return True

    def on_method_declaration(self, node: ts.Node, depth: int) -> bool:
        scope: dict[str, str] = {}
        self._params(node.child_by_field_name("receiver"), scope)
        recv_type = next(iter(scope.values()), "")
        count = self._params(node.child_by_field_name("parameters"), scope)
        self.classes.append(recv_type)
        try:
            self.enter_def(node, self.text(node.child_by_field_name("name")), "method",
                           node.child_by_field_name("body"), depth, params=count, scope=scope)
        finally:
            self.classes.pop()
        return True

    def on_var_spec(self, node: ts.Node, depth: int) -> bool:
        t = self.text(node.child_by_field_name("type"))
        for n in node.children_by_field_name("name"):
            self.declare(self.text(n), t)
        return False

    def on_short_var_declaration(self, node: ts.Node, depth: int) -> bool:
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or right is None:
            return False
        for n, v in zip(left.named_children, right.named_children):
            if v.type == "unary_expression":
                v = v.child_by_field_name("operand") or v
            if v.type == "composite_literal":
                self.declare(self.text(n), self.text(v.child_by_field_name("type")))
        return False

    def on_call_expression(self, node: ts.Node, depth: int) -> bool:
        fn = node.child_by_field_name("function")
        args = _args(node.child_by_field_name("arguments"))
        if fn is None:
            return False
        if fn.type == "identifier":
            self.add_call(node, self.text(fn), args=args)
        elif fn.type == "selector_expression":
            name = self.text(fn.child_by_field_name("field"))
            operand = fn.child_by_field_name("operand")
            kind, recv = "other", ""
            if operand is not None and operand.type == "identifier":
                n = self.text(operand)
                t = self.lookup(n)
                kind, recv = ("type", t) if t else ("ident", n)
            elif operand is not None and operand.type == "selector_expression":
                o = operand.child_by_field_name("operand")
                f = self.text(operand.child_by_field_name("field"))
                t = self.lookup(self.text(o)) if o is not None and o.type == "identifier" else ""
                if t:
                    kind, recv = "field", f"{t}.{f}"
            self.add_call(node, name, kind, recv, args)
        return False


_EXTRACTORS: dict[str, type[_Extractor]] = {"java": _Java, "ts": _TS, "python": _Python, "go": _Go}


def extract(text: str, path: str, language: str) -> FileFacts | None:
    """Fatos de um arquivo (None se a linguagem não tem extrator)."""
    family = FAMILIES.get(language)
    if family is None:
        return None
    src = text.encode("utf-8", errors="replace")
    tree = _parser(language).parse(src)
    return _EXTRACTORS[family](src, path, language).run(tree.root_node)


# ------------------------------------------------------------------ clone


def _compile(globs: Sequence[str]) -> list[re.Pattern[str]]:
    out = []
    for g in globs:
        try:
            out.append(re.compile(glob_regex(g)))
        except ValueError:
            continue
    return out


def _go_modules(root: Path, go_mods: list[str]) -> list[tuple[str, str]]:
    out = []
    for rel in go_mods:
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r"(?m)^module\s+(\S+)", text)
        if m:
            d = os.path.dirname(rel)
            out.append((m.group(1).strip('"'), d))
    return out


_TS_CONFIGS = re.compile(r"^(?:tsconfig|jsconfig)(?:\.[\w-]+)?\.json$")
_JSON_NOISE = re.compile(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', re.DOTALL)


def _ts_config(root: Path, rel: str) -> TsConfig | None:
    """`baseUrl` e `paths` de um tsconfig (JSON com comentários e vírgulas sobrando). `extends` não é seguido:
    cada arquivo do repo é lido e vale o mais próximo que declara alguma coisa."""
    try:
        raw = (root / rel).read_text(encoding="utf-8", errors="replace")
        text = _JSON_NOISE.sub(lambda m: m.group(1) or "", raw)
        data = json.loads(re.sub(r",(\s*[}\]])", r"\1", text))
    except (OSError, ValueError):
        return None
    opts = data.get("compilerOptions") if isinstance(data, dict) else None
    if not isinstance(opts, dict):
        return None
    cfg_dir = posixpath.dirname(rel)
    base_url = opts.get("baseUrl")
    base = posixpath.normpath(posixpath.join(cfg_dir, base_url)) if isinstance(base_url, str) else None
    base = "" if base == "." else base
    origin = base if base is not None else cfg_dir
    raw_paths = opts.get("paths")
    paths = []
    for key, targets in (raw_paths.items() if isinstance(raw_paths, dict) else ()):
        if isinstance(key, str) and isinstance(targets, list):
            norm = tuple(posixpath.normpath(posixpath.join(origin, t)).removeprefix("./")
                         for t in targets if isinstance(t, str))
            paths.append((key, tuple("" if t == "." else t for t in norm)))
    if base is None and not paths:
        return None
    return TsConfig(dir=cfg_dir, base=base, paths=tuple(paths))


def scan_calls(root: Path, include: Sequence[str], exclude: Sequence[str], max_bytes: int) -> GraphResult:
    """Lê o clone e resolve as chamadas. Bloqueante (tree-sitter e disco): chame via asyncio.to_thread."""
    inc = _compile(include)
    exc = _compile(exclude)
    root = root.resolve()
    files: list[FileFacts] = []
    go_mods: list[str] = []
    ts_configs: list[TsConfig] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = [d for d in sorted(dirnames)
                       if not os.path.islink(os.path.join(dirpath, d))
                       and not any(rx.match(f"{rel_dir}/{d}" if rel_dir else d) for rx in exc)]
        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            full = os.path.join(dirpath, name)
            if name == "go.mod" and not os.path.islink(full):
                go_mods.append(rel)
            elif _TS_CONFIGS.match(name) and not os.path.islink(full):
                cfg = _ts_config(root, rel)
                if cfg is not None:
                    ts_configs.append(cfg)
            language = file_language(name)
            if language not in FAMILIES or os.path.islink(full):
                continue
            if not any(rx.match(rel) for rx in inc) or any(rx.match(rel) for rx in exc):
                continue
            facts = _read(full, rel, language, max_bytes)
            if facts is not None:
                files.append(facts)
    return resolve_repo(files, _go_modules(root, go_mods), ts_configs)


def _read(full: str, rel: str, language: str, max_bytes: int) -> FileFacts | None:
    try:
        if os.path.getsize(full) > max_bytes:
            return None
        raw = Path(full).read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
    if looks_minified(text):
        return None
    try:
        return extract(text, rel, language)
    except Exception:  # noqa: BLE001 - um arquivo que o parser não aguenta não derruba o repo
        return None

