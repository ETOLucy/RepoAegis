from __future__ import annotations

import asyncio
import hashlib
import shutil
from pathlib import Path

from repo_maintenance_agent.domain.models import SearchHit, SearchQuery

_IGNORED_PARTS = frozenset({".git", ".venv", "node_modules", "dist", "build"})


class RipgrepSearch:
    """Exact-substring channel backed by `rg` (ripgrep).
    Registered in the hybrid retriever map under QueryKind.LEXICAL so that
    error-string / quoted-identifier queries get a fast, precise match.
    Ripgrep is a hard requirement: constructing without the binary raises so
    the misconfiguration surfaces at startup, not as silently empty results.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()
        self._binary = shutil.which("rg")
        if self._binary is None:
            raise RuntimeError(
                "ripgrep (rg) is required for the LEXICAL search channel; "
                "install it (e.g. `apt-get install ripgrep` / `brew install ripgrep`) "
                "or configure the pure-Python LocalLexicalSearch explicitly."
            )

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        command = [
            self._binary,
            "--line-number",
            "--no-heading",
            "--color",
            "never",
            "--glob",
            "!.git/**",
            "--glob",
            "!.venv/**",
            "--glob",
            "!node_modules/**",
            "--glob",
            "!dist/**",
            "--glob",
            "!build/**",
            "--",
            query.text,
        ]
        roots = self._resolve_roots(query.allowed_paths)
        command.extend(str(root) for root in roots)
        result = await asyncio.to_thread(
            self._run,
            command,
        )
        return self._parse(result, query)

    def _resolve_roots(self, allowed_paths: tuple[str, ...]) -> list[Path]:
        candidates = allowed_paths or (".",)
        roots: list[Path] = []
        for raw_path in candidates:
            resolved = (self._workspace / raw_path).resolve()
            if not resolved.is_relative_to(self._workspace):
                raise ValueError("allowed path resolves outside workspace")
            roots.append(resolved)
        return roots

    def _run(self, command: list[str]) -> tuple[int, str]:
        import subprocess

        process = subprocess.run(  # noqa: S603 - resolved executable and fixed args
            command,
            cwd=self._workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        return process.returncode, process.stdout

    def _parse(self, result: tuple[int, str], query: SearchQuery) -> list[SearchHit]:
        returncode, stdout = result
        if returncode not in (0, 1):  # 1 = no matches
            return []
        hits: list[SearchHit] = []
        for raw_line in stdout.splitlines():
            # rg --no-heading output: "path:line:content"
            parts = raw_line.split(":", 2)
            if len(parts) != 3:
                continue
            path, raw_line_number, content = parts
            try:
                line_number = int(raw_line_number)
            except ValueError:
                continue
            relative = self._relativize(path)
            hit_id = hashlib.sha256(
                f"{query.commit_sha}:{relative}:{line_number}".encode()
            ).hexdigest()
            hits.append(
                SearchHit(
                    hit_id=hit_id,
                    path=relative,
                    content=content.strip(),
                    score=1.0 / line_number,
                    source="lexical",
                    line_start=line_number,
                    line_end=line_number,
                )
            )
            if len(hits) >= query.top_k:
                break
        return hits

    def _relativize(self, path: str) -> str:
        try:
            return Path(path).resolve().relative_to(self._workspace).as_posix()
        except ValueError:
            return Path(path).as_posix()


def default_lexical_search(workspace: Path) -> RipgrepSearch:
    """Factory for the exact-substring channel; fails fast when rg is absent.
    Teams that cannot guarantee ripgrep on the runtime image should construct
    LocalLexicalSearch explicitly instead of silently degrading quality.
    """
    return RipgrepSearch(workspace)
