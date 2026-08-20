from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from repo_maintenance_agent.domain.models import SearchHit, SearchQuery

_IGNORED_PARTS = frozenset({".git", ".venv", "node_modules", "dist", "build"})
_MAX_FILE_BYTES = 2_000_000


class LocalLexicalSearch:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        roots = self._resolve_roots(query.allowed_paths)
        return await asyncio.to_thread(self._search_sync, roots, query)

    def _resolve_roots(self, allowed_paths: tuple[str, ...]) -> list[Path]:
        candidates = allowed_paths or (".",)
        roots: list[Path] = []
        for raw_path in candidates:
            resolved = (self._workspace / raw_path).resolve()
            if not resolved.is_relative_to(self._workspace):
                raise ValueError("allowed path resolves outside workspace")
            roots.append(resolved)
        return roots

    def _search_sync(self, roots: list[Path], query: SearchQuery) -> list[SearchHit]:
        needle = query.text.casefold()
        results: list[SearchHit] = []
        seen: set[Path] = set()
        for root in roots:
            paths = root.rglob("*") if root.is_dir() else [root]
            for path in paths:
                if path in seen or not path.is_file() or _IGNORED_PARTS.intersection(path.parts):
                    continue
                seen.add(path)
                try:
                    if path.stat().st_size > _MAX_FILE_BYTES:
                        continue
                    lines = path.read_text(encoding="utf-8").splitlines()
                except (OSError, UnicodeError):
                    continue
                for line_number, line in enumerate(lines, start=1):
                    if needle not in line.casefold():
                        continue
                    relative = path.relative_to(self._workspace).as_posix()
                    hit_id = hashlib.sha256(
                        f"{query.commit_sha}:{relative}:{line_number}".encode()
                    ).hexdigest()
                    results.append(
                        SearchHit(
                            hit_id=hit_id,
                            path=relative,
                            content=line.strip(),
                            score=1.0 / line_number,
                            source="lexical",
                            line_start=line_number,
                            line_end=line_number,
                        )
                    )
                    if len(results) >= query.top_k:
                        return results
        return results
