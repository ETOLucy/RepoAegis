from pathlib import Path

from repo_maintenance_agent.domain.models import SearchQuery
from repo_maintenance_agent.search.adapters.graph import GraphSearch
from repo_maintenance_agent.search.codegraph import build_repo_graph

_COMMIT = "a" * 40


def _query(text: str, *, top_k: int = 10) -> SearchQuery:
    return SearchQuery(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha=_COMMIT,
        text=text,
        top_k=top_k,
    )


async def test_graph_search_finds_definition_by_symbol_name(tmp_path: Path) -> None:
    (tmp_path / "svc.py").write_text(
        "class Service:\n    def handle(self, request):\n        return True\n",
        encoding="utf-8",
    )
    search = GraphSearch(tmp_path, build_repo_graph(tmp_path))

    hits = await search.search(_query("where is Service.handle defined"))

    assert any(h.path == "svc.py" and h.line_start == 2 for h in hits)
    definition_hits = [h for h in hits if h.source.startswith("graph-definition")]
    assert definition_hits[0].content  # snippet was read from the real file


async def test_graph_search_finds_call_sites(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "def helper():\n    pass\n\ndef caller():\n    helper()\n",
        encoding="utf-8",
    )
    search = GraphSearch(tmp_path, build_repo_graph(tmp_path))

    hits = await search.search(_query("who calls helper"))

    reference_hits = [h for h in hits if h.source == "graph-reference"]
    assert any(h.line_start == 5 for h in reference_hits)


async def test_graph_search_ranks_definitions_above_references_and_imports(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.py").write_text(
        "def widget():\n    pass\n\ndef use():\n    widget()\n",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text("from a import widget\n", encoding="utf-8")
    search = GraphSearch(tmp_path, build_repo_graph(tmp_path))

    hits = await search.search(_query("widget"))

    sources = [h.source for h in hits]
    assert sources.index("graph-definition:function") < sources.index("graph-reference")
    assert sources.index("graph-reference") < sources.index("graph-import")


async def test_graph_search_respects_top_k(tmp_path: Path) -> None:
    lines = [f"def fn_{i}():\n    pass\n" for i in range(5)]
    (tmp_path / "many.py").write_text("".join(lines), encoding="utf-8")
    search = GraphSearch(tmp_path, build_repo_graph(tmp_path))

    hits = await search.search(_query("fn_0 fn_1 fn_2 fn_3 fn_4", top_k=2))

    assert len(hits) == 2


async def test_graph_search_returns_empty_for_unknown_symbol(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def known():\n    pass\n", encoding="utf-8")
    search = GraphSearch(tmp_path, build_repo_graph(tmp_path))

    hits = await search.search(_query("totally_unknown_symbol"))

    assert hits == []
