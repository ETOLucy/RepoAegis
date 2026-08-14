"""Inspect dataset loader for RepoAegis frozen holdout data.

Converts RepoAegis's frozen holdout records (``data/holdout.jsonl`` style) into
an Inspect :class:`~inspect_ai.dataset.MemoryDataset` of
:class:`~inspect_ai.dataset.Sample` objects so they can be consumed by an
Inspect ``Task`` (see :mod:`repo_maintenance_agent.inspect`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from inspect_ai.dataset import Dataset, MemoryDataset, Sample

#: Holdout fields copied verbatim into ``Sample.metadata``.
_METADATA_FIELDS: tuple[str, ...] = (
    "instance_id",
    "repo",
    "base_commit",
    "test_patch",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "difficulty",
    "gold_patch",
)


def _as_str_list(value: Any) -> list[str]:
    """Normalize a ``FAIL_TO_PASS`` / ``PASS_TO_PASS`` cell to a list of strings.

    Datasets sometimes store the test lists as JSON-encoded strings (e.g.
    ``'["test_a", "test_b"]'``) or comma-separated text; this helper accepts
    lists, JSON strings and plain strings so the loader never chokes on a
    record variant.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            return [item.strip() for item in stripped.split(",") if item.strip()]
        if isinstance(decoded, list):
            return [str(item) for item in decoded]
        return [str(decoded)]
    return [str(value)]


def _record_to_sample(record: dict[str, Any], line_no: int) -> Sample:
    """Convert one holdout JSON object into an Inspect ``Sample``."""
    instance_id = record.get("instance_id")
    problem_statement = record.get("problem_statement")
    if not instance_id or not problem_statement:
        raise ValueError(
            f"holdout line {line_no}: 'instance_id' and 'problem_statement' are required"
        )

    metadata: dict[str, Any] = {
        field: record[field] for field in _METADATA_FIELDS if field in record
    }
    # Normalize test lists so downstream consumers always see a list.
    metadata["FAIL_TO_PASS"] = _as_str_list(metadata.get("FAIL_TO_PASS"))
    metadata["PASS_TO_PASS"] = _as_str_list(metadata.get("PASS_TO_PASS"))

    return Sample(
        id=str(instance_id),
        input=problem_statement,
        target=metadata.get("test_patch") or "",
        metadata=metadata,
    )


def load_repoaegis_holdout(path: Path) -> Dataset:
    """Load a RepoAegis frozen holdout file into an Inspect dataset.

    Args:
        path: Path to a JSONL file (``data/holdout.jsonl`` style). Each line is
            a JSON object with at least ``instance_id`` and
            ``problem_statement``. Recognized optional fields: ``repo``,
            ``base_commit``, ``test_patch``, ``FAIL_TO_PASS``, ``PASS_TO_PASS``,
            ``difficulty`` and ``gold_patch`` (the last one optional).

    Returns:
        A :class:`~inspect_ai.dataset.MemoryDataset` with one
        :class:`~inspect_ai.dataset.Sample` per holdout record. Every source
        field is preserved in ``Sample.metadata`` (``FAIL_TO_PASS`` /
        ``PASS_TO_PASS`` normalized to lists), ``Sample.id`` is the
        ``instance_id``, ``Sample.input`` is the ``problem_statement`` and
        ``Sample.target`` is the ``test_patch``.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If a line is not valid JSON, is not a JSON object, or is
            missing the required ``instance_id`` / ``problem_statement`` fields.
    """
    path = Path(path)
    samples: list[Sample] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"holdout line {line_no} is not valid JSON: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"holdout line {line_no} must be a JSON object")
            samples.append(_record_to_sample(record, line_no))
    return MemoryDataset(samples=samples, name=path.stem, location=str(path))
