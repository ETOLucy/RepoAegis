"""SWE-bench instances, loaded from the published parquet.

One instance carries everything an evaluation needs and nothing it should see
too early: the issue text the agent is allowed to read, the commit the agent
must work from, and -- kept strictly on the scoring side -- the gold patch, the
test patch, and the two test lists that decide pass or fail.

The split matters. ``problem_statement`` and ``base_commit`` go to the agent;
``patch``, ``test_patch``, ``fail_to_pass`` and ``pass_to_pass`` never do. A
harness that leaks the answer into the prompt measures nothing, and the leak is
easy to introduce by accident, so the two sides are separated by type here
rather than by discipline later.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx
import structlog

log = structlog.get_logger(__name__)

DATASET = "princeton-nlp/SWE-bench_Verified"
TREE_API = f"https://huggingface.co/api/datasets/{DATASET}/tree/main/data"
DOWNLOAD = f"https://huggingface.co/datasets/{DATASET}/resolve/main/"
DEFAULT_PATH = Path("data/eval/swebench_verified.parquet")


@dataclass(frozen=True, slots=True)
class Instance:
    """One benchmark task. The agent may see only the first four fields."""

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str

    # Scoring side. Never put these in a prompt.
    gold_patch: str
    test_patch: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]

    version: str = ""
    difficulty: str = ""

    @property
    def image(self) -> str:
        """The official prebuilt image: the repository at ``base_commit`` with
        its dependencies already installed. Docker Hub forbids ``__`` in a
        name, which is why upstream substitutes ``_1776_``."""
        return f"swebench/sweb.eval.x86_64.{self.instance_id.replace('__', '_1776_')}:latest"

    @property
    def issue_title(self) -> str:
        first = self.problem_statement.strip().splitlines()[0] if self.problem_statement else ""
        return first[:200] or self.instance_id

    @property
    def gold_files(self) -> tuple[str, ...]:
        """Which files the reference fix touches, for localisation scoring."""
        return changed_files(self.gold_patch)


def changed_files(patch: str) -> tuple[str, ...]:
    """Paths named by a unified diff, normalised away from git's a/ and b/."""
    files: list[str] = []
    for line in patch.splitlines():
        if not line.startswith("--- ") and not line.startswith("+++ "):
            continue
        path = line[4:].strip()
        if path in {"/dev/null", ""}:
            continue
        if path.startswith(("a/", "b/")):
            path = path[2:]
        if path not in files:
            files.append(path)
    return tuple(files)


def ensure_dataset(path: Path = DEFAULT_PATH, *, timeout: float = 180.0) -> Path:
    """Download the parquet once. It is 2 MB and not worth vendoring."""
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    listing = httpx.get(TREE_API, timeout=30).json()
    names = [entry["path"] for entry in listing if entry["path"].endswith(".parquet")]
    if not names:
        raise RuntimeError(f"no parquet file published under {DATASET}")
    log.info("dataset.downloading", dataset=DATASET, file=names[0])
    with httpx.stream("GET", DOWNLOAD + names[0], timeout=timeout, follow_redirects=True) as reply:
        reply.raise_for_status()
        path.write_bytes(reply.read())
    return path


def _tests(raw: object) -> tuple[str, ...]:
    """The test lists are JSON arrays stored as strings."""
    if isinstance(raw, list):
        return tuple(str(item) for item in raw)
    if isinstance(raw, str) and raw.strip():
        try:
            return tuple(str(item) for item in json.loads(raw))
        except json.JSONDecodeError:
            return ()
    return ()


def load(
    path: Path = DEFAULT_PATH,
    *,
    repos: Sequence[str] | None = None,
    instance_ids: Sequence[str] | None = None,
    difficulties: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[Instance]:
    """Read instances, filtered. Order follows the dataset so runs repeat."""
    import pyarrow.parquet as pq

    table = pq.read_table(ensure_dataset(path)).to_pylist()
    chosen = list(_filter(table, repos, instance_ids, difficulties))
    return chosen[:limit] if limit else chosen


def _filter(
    rows: list[dict[str, object]],
    repos: Sequence[str] | None,
    instance_ids: Sequence[str] | None,
    difficulties: Sequence[str] | None,
) -> Iterator[Instance]:
    wanted_repos = set(repos) if repos else None
    wanted_ids = set(instance_ids) if instance_ids else None
    wanted_difficulty = set(difficulties) if difficulties else None
    for row in rows:
        if wanted_repos is not None and row.get("repo") not in wanted_repos:
            continue
        if wanted_ids is not None and row.get("instance_id") not in wanted_ids:
            continue
        if wanted_difficulty is not None and row.get("difficulty") not in wanted_difficulty:
            continue
        yield Instance(
            instance_id=str(row["instance_id"]),
            repo=str(row["repo"]),
            base_commit=str(row["base_commit"]),
            problem_statement=str(row.get("problem_statement") or ""),
            gold_patch=str(row.get("patch") or ""),
            test_patch=str(row.get("test_patch") or ""),
            fail_to_pass=_tests(row.get("FAIL_TO_PASS")),
            pass_to_pass=_tests(row.get("PASS_TO_PASS")),
            version=str(row.get("version") or ""),
            difficulty=str(row.get("difficulty") or ""),
        )
