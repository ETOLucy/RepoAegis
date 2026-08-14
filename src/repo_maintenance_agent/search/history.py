from __future__ import annotations

import re
from pathlib import Path

from repo_maintenance_agent.domain.models import SearchHit, SearchQuery
from repo_maintenance_agent.tools.process import ProcessRunner

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_QUERY_NOISE = frozenset({"why", "history", "commit", "changed", "introduced", "blame"})


class GitHistorySearch:
    def __init__(self, workspace: Path, runner: ProcessRunner) -> None:
        self._workspace = workspace.resolve()
        self._runner = runner

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        arguments = [
            "git",
            "log",
            "--max-count=100",
            "--format=%x1e%H%x1f%s%x1f%b%x1d",
            "--name-only",
            query.commit_sha,
            "--",
            *query.allowed_paths,
        ]
        result = await self._runner.run(arguments, cwd=self._workspace)
        terms = set(_tokens(query.text)) - _QUERY_NOISE
        hits: list[SearchHit] = []
        for raw_record in result.stdout.split("\x1e"):
            record = raw_record.strip()
            if not record or "\x1d" not in record:
                continue
            metadata, raw_paths = record.split("\x1d", maxsplit=1)
            parts = metadata.split("\x1f", maxsplit=2)
            if len(parts) != 3:
                continue
            commit_sha, subject, body = parts
            paths = [line.strip() for line in raw_paths.splitlines() if line.strip()]
            haystack = " ".join((subject, body, *paths)).casefold()
            score = float(sum(haystack.count(term) for term in terms))
            if score <= 0:
                continue
            hits.append(
                SearchHit(
                    hit_id=commit_sha,
                    path=paths[0] if paths else ".git/history",
                    content="\n".join(part for part in (subject, body, *paths) if part),
                    score=score,
                    source="history",
                )
            )
        hits.sort(key=lambda hit: (-hit.score, hit.hit_id))
        return hits[: query.top_k]


def _tokens(value: str) -> list[str]:
    return [token.casefold() for token in _TOKEN.findall(value)]
