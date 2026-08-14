"""Parse Inspect evaluation logs into AegisEvo-consumable ``EvalResult`` records.

Inspect writes two on-disk log flavours:

1. **Legacy / streamed JSONL** (one JSON *event* per line): each event carries a
   ``type`` field such as ``"sample"`` / ``"score"``; a ``"sample"`` event embeds
   the ``sample`` (with ``scores``, ``usage``, ``messages``), ``run_id`` and
   ``eval`` header information.
2. **Zip archive** (``.eval``, starts with the ``PK`` magic bytes; the default
   since inspect_ai 0.3.x): parsed via ``inspect_ai.log.read_eval_log`` when
   available.

``parse_inspect_log`` auto-detects the flavour. The JSONL path is deliberately
tolerant: malformed lines and unexpected event types are skipped, and missing
fields fall back to defaults, so schema drift never crashes the consumer.
Schema note: **the exact event schema is tied to the installed ``inspect_ai``
version; the parser must not crash on parsing failures** — unknown keys are
ignored and absent fields default.

The output ``EvalResult`` dataclass is the canonical per-sample row consumed by
AegisEvo statistical gating.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Score names preferred when choosing the "primary" per-sample score.
_PREFERRED_SCORE_KEYS: tuple[str, ...] = ("progress", "swe", "swebench", "accuracy")

#: Letter grades produced by Inspect scorers mapped to numbers for gating.
_LETTER_GRADES: dict[str, float] = {"C": 1.0, "P": 0.5, "I": 0.0, "N": 0.0}


@dataclass(frozen=True, slots=True)
class EvalResult:
    """One evaluated sample in a canonical, AegisEvo-consumable shape.

    Attributes:
        run_id: Inspect evaluation run id (may be empty if absent from the log).
        model: Model spec used for the run (may be empty).
        sample_id: Sample id as recorded by Inspect (str or int, or ``None``).
        score: Primary score value (prefers progress/swe scores, then the first
            score found); numbers are floats, letter grades are mapped to
            floats, otherwise the raw value is kept.
        extra_scores: All other per-sample scores by name.
        tokens: Token usage snapshot (``input_tokens`` / ``output_tokens`` /
            ``total_tokens``); missing usage yields an empty dict.
        tool_calls: Number of tool calls made by the agent for this sample
            (counted from assistant ``tool_calls`` entries, falling back to
            ``tool``-role messages).
        status: ``"completed"`` / ``"error"`` / ``"unknown"`` per sample.
        source: Always ``"inspect"``; distinguishes these rows in AegisEvo
            pipelines that also ingest the self-hosted harness.
    """

    run_id: str
    model: str
    sample_id: str | int | None
    score: float | str | None
    extra_scores: dict[str, float | str | None] = field(default_factory=dict)
    tokens: dict[str, int | None] = field(default_factory=dict)
    tool_calls: int = 0
    status: str = "unknown"
    source: str = "inspect"


def _dig(obj: Any, *keys: str, default: Any = None) -> Any:
    """Multi-level dict lookup that tolerates missing keys and non-dict nodes."""
    current = obj
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def _to_float(value: Any, default: float | None = None) -> float | None:
    """Coerce a value to ``float``, returning ``default`` on failure."""
    if value is None or isinstance(value, bool):
        return 1.0 if isinstance(value, bool) else default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _to_int(value: Any, default: int | None = None) -> int | None:
    """Coerce a value to ``int``, returning ``default`` on failure."""
    if value is None or isinstance(value, bool):
        return int(value) if isinstance(value, bool) else default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return default
    return default


def _normalize_score_value(raw: Any) -> float | str | None:
    """Extract a comparable value from an Inspect score object.

    Handles nested ``{"value": ...}`` dicts, pydantic Score-like objects,
    numbers, booleans and strings (including letter grades such as ``C`` / ``I``
    that the official SWE-bench scorer emits).
    """
    value = raw
    if isinstance(value, dict):
        value = value.get("value")
        if isinstance(value, dict):
            value = value.get("value")
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    # Pydantic Score-like objects expose a ``value`` attribute.
    if hasattr(value, "value") and not isinstance(value, (str, bytes)):
        return _normalize_score_value(value.value)
    if isinstance(value, str):
        if value.upper() in _LETTER_GRADES:
            return _LETTER_GRADES[value.upper()]
        number = _to_float(value)
        return number if number is not None else value
    return str(value)


def _extract_scores(
    scores: Any,
) -> tuple[float | str | None, dict[str, float | str | None]]:
    """Return ``(primary_score, extra_scores)`` from an Inspect scores mapping.

    ``scores`` may be a dict (name -> score object) or a list of score objects.
    """
    if scores is None:
        return None, {}
    if isinstance(scores, dict):
        items = [(str(key), value) for key, value in scores.items()]
    elif isinstance(scores, list):
        items = []
        for item in scores:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("scorer") or "score")
            items.append((name, item))
    else:
        return _normalize_score_value(scores), {}

    if not items:
        return None, {}

    by_lower = {key.lower(): (key, value) for key, value in items}
    primary_key: str | None = None
    for preferred in _PREFERRED_SCORE_KEYS:
        if preferred in by_lower:
            primary_key = preferred
            break
    if primary_key is None:
        primary_key = items[0][0]

    primary = by_lower.get(primary_key.lower(), items[0])
    extras = {key: _normalize_score_value(value) for key, value in items if key != primary[0]}
    return _normalize_score_value(primary[1]), extras


def _usage_to_tokens(usage: Any) -> dict[str, int | None]:
    """Extract token counts from a usage dict or pydantic model."""
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):  # pydantic ModelUsage
        usage = usage.model_dump()
    if not isinstance(usage, dict):
        return {}
    return {
        "input_tokens": _to_int(usage.get("input_tokens")),
        "output_tokens": _to_int(usage.get("output_tokens")),
        "total_tokens": _to_int(usage.get("total_tokens")),
    }


def _extract_tokens(sample: Any) -> dict[str, int | None]:
    """Find usage across the common schema locations."""
    usage = _dig(sample, "usage")
    if usage is None:
        usage = _dig(sample, "model_usage")
    if usage is None:
        usage = _dig(sample, "output", "usage")
    return _usage_to_tokens(usage)


def _count_tool_calls(sample: Any) -> int:
    """Count agent tool calls in a sample.

    Prefers assistant ``tool_calls`` entries (each entry is one call); falls
    back to ``tool``-role / ``tool``-type messages so logs without explicit
    tool_calls arrays still produce a sensible count.
    """
    messages = _dig(sample, "messages", default=[]) or []
    if not isinstance(messages, list):
        messages = []
    calls_from_assistant = 0
    tool_messages = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool" or message.get("type") == "tool":
            tool_messages += 1
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            calls_from_assistant += len(tool_calls)
    return calls_from_assistant if calls_from_assistant else tool_messages


def _sample_status(sample: Any, has_scores: bool) -> str:
    """Derive a coarse per-sample status from the log payload."""
    status = _dig(sample, "status")
    if status:
        return str(status)
    if _dig(sample, "error") or _dig(sample, "output", "error"):
        return "error"
    return "completed" if has_scores else "unknown"


def _sample_event_to_result(event: dict[str, Any], header: dict[str, Any]) -> EvalResult:
    """Convert a ``type == "sample"`` event into an ``EvalResult``."""
    sample = event.get("sample")
    if not isinstance(sample, dict):
        sample = {}

    run_id = _dig(event, "run_id", default="") or header.get("run_id", "") or ""
    model = _dig(sample, "output", "model") or _dig(event, "model") or header.get("model") or ""
    sample_id = _dig(sample, "id")
    if sample_id is None:
        sample_id = _dig(event, "sample_id")
    if isinstance(sample_id, bool):
        sample_id = int(sample_id)

    scores_obj = sample.get("scores")
    if scores_obj is None:
        scores_obj = _dig(event, "scores")
    primary, extras = _extract_scores(scores_obj)
    has_scores = primary is not None or bool(extras)

    tokens = _extract_tokens(sample) or _extract_tokens(event)
    tool_calls = _count_tool_calls(sample)
    status = _sample_status(sample, has_scores)

    return EvalResult(
        run_id=str(run_id),
        model=str(model),
        sample_id=sample_id,
        score=primary,
        extra_scores=extras,
        tokens=tokens,
        tool_calls=tool_calls,
        status=status,
    )


def _score_event_to_result(event: dict[str, Any], header: dict[str, Any]) -> EvalResult:
    """Convert a standalone ``type == "score"`` event into an ``EvalResult``."""
    primary, extras = _extract_scores(_dig(event, "scores"))
    sample_id = _dig(event, "sample_id")
    if sample_id is None:
        sample_id = _dig(event, "sample", "id")
    return EvalResult(
        run_id=_dig(event, "run_id", default="") or header.get("run_id", "") or "",
        model=_dig(event, "model") or header.get("model") or "",
        sample_id=sample_id,
        score=primary,
        extra_scores=extras,
        tokens=_extract_tokens(event),
        tool_calls=_count_tool_calls(event),
        status="completed" if primary is not None else "unknown",
    )


def _parse_jsonl_events(path: Path) -> list[EvalResult]:
    """Parse a JSONL event log, skipping malformed lines and other events."""
    results: list[EvalResult] = []
    header: dict[str, Any] = {}
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerant: skip malformed lines
            if not isinstance(event, dict):
                continue
            if event.get("run_id"):
                header.setdefault("run_id", event["run_id"])
            eval_spec = event.get("eval")
            if isinstance(eval_spec, dict) and eval_spec.get("model"):
                header.setdefault("model", eval_spec["model"])
            event_type = event.get("type")
            if event_type == "sample":
                results.append(_sample_event_to_result(event, header))
            elif event_type == "score":
                results.append(_score_event_to_result(event, header))
            # Other event types (model, solver, logging, ...) are skipped.
    return results


def _parse_zip_eval_log(path: Path) -> list[EvalResult]:
    """Parse a zip-archive ``.eval`` log via ``inspect_ai.log.read_eval_log``."""
    try:
        from inspect_ai.log import read_eval_log
    except Exception as exc:  # pragma: no cover - import fallback
        raise ValueError(
            "file is a zip-based .eval log but inspect_ai.log.read_eval_log "
            "is unavailable in this environment"
        ) from exc

    try:
        log = read_eval_log(path)
    except Exception as exc:
        raise ValueError(f"failed to read zip-based .eval log {path}: {exc}") from exc

    if not log.samples:
        return []

    run_id = getattr(log.eval, "run_id", "") or ""
    model = getattr(log.eval, "model", "") or ""
    results: list[EvalResult] = []
    for sample in log.samples:
        primary, extras = _extract_scores(getattr(sample, "scores", None))
        has_scores = primary is not None or bool(extras)
        tokens = _usage_to_tokens(getattr(sample, "model_usage", None))
        tool_calls = sum(
            1
            for message in getattr(sample, "messages", []) or []
            if getattr(message, "role", None) == "tool"
        )
        status = (
            "error"
            if getattr(sample, "error", None)
            else ("completed" if has_scores else "unknown")
        )
        results.append(
            EvalResult(
                run_id=str(run_id),
                model=str(model),
                sample_id=sample.id,
                score=primary,
                extra_scores=extras,
                tokens=tokens,
                tool_calls=tool_calls,
                status=status,
            )
        )
    return results


def parse_inspect_log(log_path: Path) -> list[EvalResult]:
    """Parse an Inspect evaluation log into a list of ``EvalResult`` rows.

    Auto-detects the on-disk flavour:

    * Files starting with the zip magic bytes (``PK``) are treated as the
      current zip-based ``.eval`` format and delegated to
      ``inspect_ai.log.read_eval_log`` (falling back to a clear error if the
      reader is unavailable).
    * Everything else is treated as JSONL event logs: ``"sample"`` and
      ``"score"`` events become rows, all other event types and malformed lines
      are skipped, and missing fields default to safe values.

    Args:
        log_path: Path to an Inspect log (``.eval`` JSONL or zip archive).

    Returns:
        List of :class:`EvalResult`, one per evaluated sample, in log order.

    Raises:
        FileNotFoundError: If ``log_path`` does not exist.
        ValueError: If the file is a zip-based log that cannot be read.
    """
    path = Path(log_path)
    if not path.exists():
        raise FileNotFoundError(f"inspect log not found: {path}")
    with path.open("rb") as handle:
        magic = handle.read(4)
    if magic.startswith(b"PK\x03\x04"):
        return _parse_zip_eval_log(path)
    return _parse_jsonl_events(path)
