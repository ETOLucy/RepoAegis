"""Call graph + import graph over a Python workspace (tree-sitter based).

Scope is deliberately narrow (see 改造计划.md 三、2): a call graph and an
import graph, built with tree-sitter so the same walker generalizes to other
languages later. No data-flow analysis, no full Code Property Graph — this
answers exactly two navigation questions the Localizer's four existing
actions (search/read/blame/finish) could not: "where is this symbol defined"
and "who calls/imports this symbol".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import tree_sitter_python as _tspython
from tree_sitter import Language, Node, Parser

from repo_maintenance_agent.search.index import _IGNORED_PARTS

_LANGUAGE = Language(_tspython.language())

_DEFINITION_TYPES = frozenset({"function_definition", "class_definition"})


@dataclass(frozen=True, slots=True)
class Definition:
    symbol: str  # dotted qualified name, e.g. "Foo.bar" for a method
    path: str
    line_start: int
    line_end: int
    kind: str  # "function" | "method" | "class"


@dataclass(frozen=True, slots=True)
class Reference:
    symbol: str  # the called name as written: "helper" or "bar" for obj.bar(...)
    path: str
    line: int


@dataclass(frozen=True, slots=True)
class ImportEdge:
    path: str
    module: str  # dotted module, relative imports keep their leading dots
    names: tuple[str, ...]  # locally-bound names ("*" for a wildcard import)


@dataclass(frozen=True, slots=True)
class RepoGraph:
    definitions: tuple[Definition, ...]
    references: tuple[Reference, ...]
    imports: tuple[ImportEdge, ...]

    def definitions_for(self, symbol: str) -> tuple[Definition, ...]:
        """Exact qualified-name match first, else fall back to the leaf name
        (the part after the last dot) so "bar" finds "Foo.bar" too."""
        symbol = symbol.strip()
        exact = tuple(d for d in self.definitions if d.symbol == symbol)
        if exact or not symbol:
            return exact
        leaf = symbol.rsplit(".", maxsplit=1)[-1]
        return tuple(d for d in self.definitions if d.symbol.rsplit(".", maxsplit=1)[-1] == leaf)

    def references_for(self, symbol: str) -> tuple[Reference, ...]:
        symbol = symbol.strip()
        exact = tuple(r for r in self.references if r.symbol == symbol)
        if exact or not symbol:
            return exact
        leaf = symbol.rsplit(".", maxsplit=1)[-1]
        return tuple(r for r in self.references if r.symbol.rsplit(".", maxsplit=1)[-1] == leaf)

    def importers_of(self, module_or_name: str) -> tuple[ImportEdge, ...]:
        """Files that import a module (by dotted name or its last segment) or
        that import a specific name from anywhere."""
        target = module_or_name.strip()
        return tuple(
            edge
            for edge in self.imports
            if edge.module == target
            or edge.module.rsplit(".", maxsplit=1)[-1] == target
            or target in edge.names
        )


def build_repo_graph(root: Path) -> RepoGraph:
    # One parser reused for every file; parsing is the only per-file work.
    root = root.resolve()
    parser = Parser(_LANGUAGE)
    definitions: list[Definition] = []
    references: list[Reference] = []
    imports: list[ImportEdge] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        # Skip venvs/build output/etc, same ignore list the BM25/symbol indexer uses.
        if _IGNORED_PARTS.intersection(relative.parts):
            continue
        try:
            source = path.read_bytes()
        except OSError:
            continue
        tree = parser.parse(source)
        rel_posix = relative.as_posix()
        _walk(tree.root_node, source, rel_posix, (), definitions, references, imports)
    return RepoGraph(tuple(definitions), tuple(references), tuple(imports))


def _walk(
    node: Node,
    source: bytes,
    path: str,
    scope: tuple[str, ...],
    definitions: list[Definition],
    references: list[Reference],
    imports: list[ImportEdge],
) -> None:
    """Depth-first walk that tracks enclosing class/function names in ``scope``
    so nested definitions get a dotted qualified name (e.g. "Foo.bar")."""
    for child in node.children:
        target = child
        if target.type == "decorated_definition":
            # Unwrap "@decorator\ndef f(): ..." to the def/class node itself.
            inner = target.child_by_field_name("definition")
            if inner is None:
                continue
            target = inner
        if target.type in _DEFINITION_TYPES:
            _record_definition(target, source, path, scope, definitions)
            # Recurse into the body with the new name pushed onto scope, then
            # skip the generic recursion below (would double-visit the body).
            definitions_scope = (*scope, _text(target.child_by_field_name("name"), source))
            body = target.child_by_field_name("body")
            if body is not None:
                _walk(body, source, path, definitions_scope, definitions, references, imports)
            continue
        if target.type == "call":
            _record_call(target, source, path, references)
        if target.type in ("import_statement", "import_from_statement"):
            _record_import(target, source, path, imports)
        _walk(target, source, path, scope, definitions, references, imports)


def _record_definition(
    node: Node,
    source: bytes,
    path: str,
    scope: tuple[str, ...],
    definitions: list[Definition],
) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = _text(name_node, source)
    qualified = ".".join((*scope, name))
    if node.type == "class_definition":
        kind = "class"
    elif scope:
        kind = "method"
    else:
        kind = "function"
    definitions.append(
        Definition(
            symbol=qualified,
            path=path,
            line_start=node.start_point.row + 1,
            line_end=node.end_point.row + 1,
            kind=kind,
        )
    )


def _record_call(node: Node, source: bytes, path: str, references: list[Reference]) -> None:
    # "helper(x)" -> function is an identifier; "obj.bar(x)" -> function is an
    # attribute node and the method name is its "attribute" field.
    function = node.child_by_field_name("function")
    if function is None:
        return
    if function.type == "attribute":
        attribute = function.child_by_field_name("attribute")
        name = _text(attribute, source) if attribute is not None else None
    elif function.type == "identifier":
        name = _text(function, source)
    else:
        name = None
    if name:
        references.append(Reference(symbol=name, path=path, line=node.start_point.row + 1))


def _record_import(node: Node, source: bytes, path: str, imports: list[ImportEdge]) -> None:
    if node.type == "import_statement":
        # "import pkg.sub" and "import pkg.sub as ps" are separate grammar shapes.
        for child in node.children:
            if child.type == "dotted_name":
                imports.append(ImportEdge(path=path, module=_text(child, source), names=()))
            elif child.type == "aliased_import":
                module_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if module_node is not None:
                    local = _text(alias_node, source) if alias_node is not None else None
                    imports.append(
                        ImportEdge(
                            path=path,
                            module=_text(module_node, source),
                            names=(local,) if local else (),
                        )
                    )
        return
    # import_from_statement: one module, one or more locally-bound names.
    module_node = node.child_by_field_name("module_name")
    if module_node is None:
        return
    module = _text(module_node, source)
    names: list[str] = []
    for child in node.children:
        if child.type == "wildcard_import":
            names.append("*")
        elif child.type == "dotted_name" and child.start_byte != module_node.start_byte:
            # Skip the module_name node itself, which is also a bare "dotted_name" child.
            names.append(_text(child, source))
        elif child.type == "aliased_import":
            # "other as alias" binds the local name "alias", not "other".
            alias_node = child.child_by_field_name("alias")
            name_node = child.child_by_field_name("name")
            bound = alias_node if alias_node is not None else name_node
            if bound is not None:
                names.append(_text(bound, source))
    imports.append(ImportEdge(path=path, module=module, names=tuple(names)))


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
