"""SearchPort adapter over a pre-built RepoGraph (search/codegraph.py).

Answers "where is X defined" / "who calls or imports X" from the call graph
and import graph instead of text/BM25 matching — see 改造计划.md 三、2.

The hit-building helpers here are also reused by ``tools/agent_actions.py``'s
``GraphToolAdapter`` (the Localizer's goto_definition/find_references
actions), so there is one place that turns a graph node into a SearchHit.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from repo_maintenance_agent.domain.models import SearchHit, SearchQuery
from repo_maintenance_agent.search.codegraph import RepoGraph

# Dotted identifiers so "Foo.bar" and "bar" both work as lookup tokens.
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_SNIPPET_CONTEXT = 2  # lines of context to include on each side of a hit
_DEFINITION_SCORE = 3.0  # ranked above references/imports: usually the most useful evidence
_REFERENCE_SCORE = 2.0
_IMPORT_SCORE = 1.0


class GraphSearch:
    def __init__(self, workspace: Path, graph: RepoGraph) -> None:
        self._workspace = workspace.resolve()
        self._graph = graph

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        hits: list[SearchHit] = []
        seen: set[tuple[str, int | None]] = set()
        for token in _identifier_tokens(query.text):
            for definition in self._graph.definitions_for(token):
                self._add(
                    hits,
                    seen,
                    path=definition.path,
                    line=definition.line_start,
                    end_line=definition.line_end,
                    symbol=definition.symbol,
                    score=_DEFINITION_SCORE,
                    source=f"graph-definition:{definition.kind}",
                    commit_sha=query.commit_sha,
                )
            for reference in self._graph.references_for(token):
                self._add(
                    hits,
                    seen,
                    path=reference.path,
                    line=reference.line,
                    end_line=reference.line,
                    symbol=reference.symbol,
                    score=_REFERENCE_SCORE,
                    source="graph-reference",
                    commit_sha=query.commit_sha,
                )
            for edge in self._graph.importers_of(token):
                self._add(
                    hits,
                    seen,
                    path=edge.path,
                    line=1,
                    end_line=1,
                    symbol=edge.module,
                    score=_IMPORT_SCORE,
                    source="graph-import",
                    commit_sha=query.commit_sha,
                )
        # Highest-signal hits first, then trim to what the caller asked for.
        hits.sort(key=lambda hit: -hit.score)
        return hits[: query.top_k]

    def _add(
        self,
        hits: list[SearchHit],
        seen: set[tuple[str, int | None]],
        *,
        path: str,
        line: int,
        end_line: int,
        symbol: str,
        score: float,
        source: str,
        commit_sha: str,
    ) -> None:
        key = (path, line)
        if key in seen:
            return
        seen.add(key)
        hits.append(
            make_hit(
                workspace=self._workspace,
                commit_sha=commit_sha,
                path=path,
                line=line,
                end_line=end_line,
                symbol=symbol,
                score=score,
                source=source,
            )
        )


def make_hit(
    *,
    workspace: Path,
    commit_sha: str,
    path: str,
    line: int,
    end_line: int,
    symbol: str,
    score: float,
    source: str,
) -> SearchHit:
    """Build a SearchHit for a graph node, reading a small snippet around it
    from the checked-out file. Shared by GraphSearch and GraphToolAdapter so
    goto_definition/find_references and the GRAPH QueryKind agree on shape."""
    hit_id = hashlib.sha256(f"{commit_sha}:{path}:{line}:{source}".encode()).hexdigest()
    return SearchHit(
        hit_id=hit_id,
        path=path,
        content=read_snippet(workspace, path, line, end_line),
        score=score,
        source=source,
        symbol=symbol,
        line_start=line,
        line_end=end_line,
    )


def read_snippet(workspace: Path, path: str, line: int, end_line: int) -> str:
    try:
        text = (workspace / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    start = max(1, line - _SNIPPET_CONTEXT)
    stop = min(len(lines), end_line + _SNIPPET_CONTEXT)
    return "\n".join(lines[start - 1 : stop])


def _identifier_tokens(text: str) -> list[str]:
    # Dedupe while keeping first-seen order so repeated words don't waste lookups.
    seen: set[str] = set()
    tokens: list[str] = []
    for match in _IDENTIFIER.finditer(text):
        token = match.group(0)
        if token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens
