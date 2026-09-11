from pathlib import Path

from repo_maintenance_agent.search.codegraph import build_repo_graph


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_build_repo_graph_finds_function_and_method_definitions(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/service.py",
        "class Service:\n"
        "    def handle(self, request):\n"
        "        return validate(request)\n"
        "\n"
        "def validate(request):\n"
        "    return request is not None\n",
    )
    graph = build_repo_graph(tmp_path)

    method = graph.definitions_for("Service.handle")
    assert len(method) == 1
    assert method[0].path == "pkg/service.py"
    assert method[0].line_start == 2
    assert method[0].kind == "method"

    function = graph.definitions_for("validate")
    assert len(function) == 1
    assert function[0].line_start == 5
    assert function[0].kind == "function"

    cls = graph.definitions_for("Service")
    assert len(cls) == 1
    assert cls[0].kind == "class"


def test_definitions_for_falls_back_to_leaf_name(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "class Foo:\n    def bar(self):\n        pass\n")
    graph = build_repo_graph(tmp_path)

    assert graph.definitions_for("Foo.bar")[0].symbol == "Foo.bar"
    # A caller that only knows the unqualified method name should still find it.
    assert graph.definitions_for("bar")[0].symbol == "Foo.bar"


def test_definitions_for_unknown_symbol_returns_empty(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "def known():\n    pass\n")
    graph = build_repo_graph(tmp_path)
    assert graph.definitions_for("nope") == ()


def test_references_for_finds_direct_and_attribute_calls(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "a.py",
        "def helper():\n"
        "    pass\n"
        "\n"
        "class Client:\n"
        "    def call_it(self):\n"
        "        helper()\n"
        "\n"
        "def use_client(client):\n"
        "    return client.call_it()\n",
    )
    graph = build_repo_graph(tmp_path)

    helper_refs = graph.references_for("helper")
    assert len(helper_refs) == 1
    assert helper_refs[0].path == "a.py"
    assert helper_refs[0].line == 6

    method_refs = graph.references_for("call_it")
    assert len(method_refs) == 1
    assert method_refs[0].line == 9


def test_import_graph_records_module_names_and_local_bindings(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "consumer.py",
        "import pkg.sub as ps\n"
        "from pkg.mod import helper, other as alias\n"
        "from . import sibling\n"
        "from mod2 import *\n",
    )
    graph = build_repo_graph(tmp_path)

    plain = next(edge for edge in graph.imports if edge.module == "pkg.sub")
    assert plain.names == ("ps",)

    from_import = next(edge for edge in graph.imports if edge.module == "pkg.mod")
    assert set(from_import.names) == {"helper", "alias"}

    wildcard = next(edge for edge in graph.imports if edge.module == "mod2")
    assert wildcard.names == ("*",)

    importers = graph.importers_of("pkg.mod")
    assert len(importers) == 1
    assert importers[0].path == "consumer.py"

    importers_by_name = graph.importers_of("helper")
    assert len(importers_by_name) == 1


def test_ignores_files_under_ignored_directories(tmp_path: Path) -> None:
    _write(tmp_path, ".venv/site-packages/mod.py", "def should_not_appear():\n    pass\n")
    _write(tmp_path, "real.py", "def real_function():\n    pass\n")
    graph = build_repo_graph(tmp_path)

    assert graph.definitions_for("should_not_appear") == ()
    assert graph.definitions_for("real_function")[0].path == "real.py"
