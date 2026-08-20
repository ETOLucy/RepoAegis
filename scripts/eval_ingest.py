#!/usr/bin/env python3
"""Normalize Inspect ``.eval`` logs into EvalResult rows + bootstrap summary.

??? v1 ?6 ??????
--------------------------
?6 ???????**Inspect ??????**??? SWE-bench scorer / ?? scorer ?
sandbox ?????? ``.eval`` ????**????? AegisEvo ??**?paired bootstrap
? delta + CI + direction ???? promotion???????????????

1. ? Inspect ? ``.eval`` ?????? ``log.json`` + ``score.json``?? PK ???
   ``.eval`` zip ?????????? :class:`EvalResult` ??``source="inspect"``??
2. ? :func:`repo_maintenance_agent.evaluation.significance.paired_bootstrap_delta`
   ?? ``mean_delta [CI] (direction)`` ???champion vs challenger??

???????/???????????????? schema ????????????
???????? ``inspect_ai``????????? import significance ????
"""

from __future__ import annotations

import argparse
import json
import zipfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from repo_maintenance_agent.evaluation.significance import (
        minimum_effect_tier as _minimum_effect_tier,
    )
    from repo_maintenance_agent.evaluation.significance import paired_bootstrap_delta
except ImportError:  # significance.minimum_effect_tier is not shipped yet
    from repo_maintenance_agent.evaluation.significance import paired_bootstrap_delta

    def _minimum_effect_tier(n: int) -> float:
        """Tiered minimum effect: small samples need larger effects to claim wins."""
        if n < 10:
            return 0.10
        if n < 30:
            return 0.05
        return 0.02


#: Score names preferred when choosing the "primary" per-sample score.
_PREFERRED_SCORE_KEYS: tuple[str, ...] = ("progress", "swe", "swebench", "accuracy")

#: Letter grades produced by Inspect scorers mapped to numbers for gating.
_LETTER_GRADES: dict[str, float] = {"C": 1.0, "P": 0.5, "I": 0.0, "N": 0.0}

#: Simple per-token CNY estimates (placeholder, used only when the log carries no cost).
_INPUT_CNY_PER_TOKEN = 20e-6
_OUTPUT_CNY_PER_TOKEN = 60e-6


@dataclass(frozen=True, slots=True)
class EvalResult:
    """One normalized sample row consumed by AegisEvo statistical gating.

    Attributes:
        case_id: Sample / instance id as recorded by Inspect.
        score: Primary score (prefers progress/swe/swebench/accuracy; letter
            grades ``C``/``I`` map to 1/0); ``None`` when no score was found.
        safety_violations: Count of policy/security violations for the sample
            (from ``metadata.safety_violations`` or a safety scorer), default 0.
        cost_cny: Estimated cost in CNY (from usage ``estimated_cost_cny`` or a
            simple per-token estimate), default 0.
        latency_ms: Wall-clock latency in milliseconds, default 0.
        model: Model spec used for the run, may be empty.
        seed: Evaluation seed, default 0.
        source: Always ``"inspect"``; distinguishes these rows in AegisEvo
            pipelines that also ingest the self-hosted harness.
    """

    case_id: str
    score: float | None
    safety_violations: int = 0
    cost_cny: float = 0.0
    latency_ms: int = 0
    model: str = ""
    seed: int = 0
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


def _to_int(value: Any, default: int = 0) -> int:
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


def _score_value(raw: Any) -> float | None:
    """Extract a comparable value from an Inspect score object."""
    value = raw
    if isinstance(value, dict):
        value = value.get("value")
        if isinstance(value, dict):
            value = value.get("value")
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        if value in _LETTER_GRADES:
            return _LETTER_GRADES[value]
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _pick_primary_score(scores: Any) -> float | None:
    """Pick the primary score from a scores mapping (name -> score object)."""
    if not isinstance(scores, dict):
        return None
    for key in _PREFERRED_SCORE_KEYS:
        if key in scores:
            value = _score_value(scores[key])
            if value is not None:
                return value
    for value in scores.values():
        score = _score_value(value)
        if score is not None:
            return score
    return None


def _score_float(value: float | None, default: float = 0.0) -> float:
    """Coerce a normalized score to a float for bootstrap (``None`` -> 0)."""
    return default if value is None else float(value)


def _usage_cost_cny(usage: Any) -> float:
    """Estimate sample cost in CNY from usage (prefers recorded cost)."""
    if not isinstance(usage, dict):
        return 0.0
    recorded = _to_float(_dig(usage, "estimated_cost_cny"))
    if recorded is not None:
        return recorded
    input_tokens = _to_float(_dig(usage, "input_tokens"), 0.0) or 0.0
    output_tokens = _to_float(_dig(usage, "output_tokens"), 0.0) or 0.0
    return input_tokens * _INPUT_CNY_PER_TOKEN + output_tokens * _OUTPUT_CNY_PER_TOKEN


def _elapsed_ms(sample: Any) -> int:
    """Sample latency in ms from elapsed / metadata / time window fields."""
    if not isinstance(sample, dict):
        return 0
    elapsed = _to_float(sample.get("elapsed"))
    if elapsed is not None:
        return max(0, int(elapsed * 1000))
    metadata_ms = _to_int(_dig(sample, "metadata", "elapsed_ms"), -1)
    if metadata_ms >= 0:
        return metadata_ms
    time = sample.get("time")
    if isinstance(time, dict) and time.get("completed") and time.get("started"):
        try:
            start = datetime.fromisoformat(str(time["started"]).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(time["completed"]).replace("Z", "+00:00"))
            return max(0, int((end - start).total_seconds() * 1000))
        except ValueError:
            return 0
    return 0


def _safety_violations(sample: Any, scores: Any) -> int:
    """Read safety violations from metadata or an explicit safety scorer."""
    metadata = _dig(sample, "metadata")
    if isinstance(metadata, dict):
        recorded = _to_int(metadata.get("safety_violations"), -1)
        if recorded >= 0:
            return recorded
    if isinstance(scores, dict):
        for key in ("safety", "safety_violations", "policy", "policy_violations"):
            if key in scores:
                value = _score_value(scores[key])
                if value is not None:
                    return max(0, round(value))
    return 0


def _case_id(sample: Any) -> str:
    """Sample id with tolerant fallbacks to input/metadata id fields."""
    if not isinstance(sample, dict):
        return "unknown"
    candidate = sample.get("id")
    if candidate is None:
        candidate = _dig(sample, "input", "instance_id")
    if candidate is None:
        candidate = _dig(sample, "metadata", "instance_id")
    if candidate is None:
        candidate = _dig(sample, "metadata", "case_id")
    return "unknown" if candidate is None else str(candidate)


def _sample_to_result(
    sample: Any,
    *,
    header: dict[str, Any],
) -> EvalResult | None:
    """Convert one Inspect sample dict into an :class:`EvalResult` (or None)."""
    if not isinstance(sample, dict):
        return None
    scores = sample.get("scores")
    usage = sample.get("model_usage")
    if usage is None:
        usage = sample.get("usage")
    return EvalResult(
        case_id=_case_id(sample),
        score=_pick_primary_score(scores),
        safety_violations=_safety_violations(sample, scores),
        cost_cny=_usage_cost_cny(usage),
        latency_ms=_elapsed_ms(sample),
        model=str(header.get("model") or _dig(sample, "metadata", "model") or ""),
        seed=_to_int(header.get("seed"), 0),
    )


def _merge_score_overrides(
    results: list[EvalResult],
    overrides: dict[str, Any],
) -> list[EvalResult]:
    """Fill missing scores from a separate ``score.json`` payload."""
    if not overrides:
        return results
    by_case = {result.case_id: index for index, result in enumerate(results)}
    merged = list(results)
    for case_id, raw_score in overrides.items():
        index = by_case.get(case_id)
        if index is None or merged[index].score is not None:
            continue
        value = _score_value(raw_score)
        if value is None:
            continue
        result = merged[index]
        merged[index] = EvalResult(
            case_id=result.case_id,
            score=value,
            safety_violations=result.safety_violations,
            cost_cny=result.cost_cny,
            latency_ms=result.latency_ms,
            model=result.model,
            seed=result.seed,
        )
    return merged


def _parse_score_payload(payload: Any) -> dict[str, Any]:
    """Normalize a score payload (list or dict) into ``{sample_id: score}``."""
    overrides: dict[str, Any] = {}
    entries: Any = payload
    if isinstance(payload, dict):
        entries = payload.get("scores")
    if isinstance(entries, dict):
        overrides.update({str(key): value for key, value in entries.items()})
    elif isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and entry.get("sample_id") is not None:
                overrides[str(entry["sample_id"])] = entry.get("score")
    return overrides


def _parse_log_payload(payload: Any) -> list[EvalResult]:
    """Convert a parsed ``log.json`` document into ``EvalResult`` rows."""
    if not isinstance(payload, dict):
        return []
    header = payload.get("eval")
    if not isinstance(header, dict):
        header = {}
    samples = payload.get("samples")
    if not isinstance(samples, list):
        return []
    results: list[EvalResult] = []
    for sample in samples:
        result = _sample_to_result(sample, header=header)
        if result is not None:
            results.append(result)
    return results


def _load_json_bytes(path: Path) -> Any:
    """Load a JSON file, raising ``ValueError`` on malformed content."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"malformed JSON in {path}: {exc}") from exc


def _zip_member(zf: zipfile.ZipFile, name: str) -> Any:
    """Read one JSON member from a zip archive, or ``None`` on failure."""
    try:
        return json.loads(zf.read(name).decode("utf-8"))
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _zip_entry_name(names: Sequence[str], stem: str) -> str | None:
    """Find a ``stem.json`` member at the archive root or under any subdir."""
    return next(
        (name for name in names if name == f"{stem}.json" or name.endswith(f"/{stem}.json")),
        None,
    )


def _parse_zip_archive(path: Path) -> list[EvalResult]:
    """Parse an Inspect ``.eval`` zip archive (``log.json`` + ``score.json``)."""
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"failed to read zip-based .eval log {path}: {exc}") from exc
    with zf:
        names = zf.namelist()
        log_name = _zip_entry_name(names, "log")
        if log_name is None:
            raise ValueError(f"zip archive {path} contains no log.json")
        payload = _zip_member(zf, log_name)
        if payload is None:
            return []
        score_name = _zip_entry_name(names, "score")
        overrides = _parse_score_payload(_zip_member(zf, score_name)) if score_name else {}
    return _merge_score_overrides(_parse_log_payload(payload), overrides)


def ingest_inspect_log(log_path: Path) -> list[EvalResult]:
    """Ingest an Inspect ``.eval`` log into normalized ``EvalResult`` rows.

    Supports two on-disk flavours (plus a convenient bare ``log.json`` file):

    * A directory containing ``log.json`` (+ optional ``score.json``).
    * A ``.eval`` zip archive (``PK`` magic) containing ``log.json`` /
      ``score.json`` members.
    * A plain JSON file that is itself a ``log.json`` document.

    Parsing is tolerant: malformed members yield defaults, non-dict samples are
    skipped, and missing fields fall back to safe values.

    Args:
        log_path: Path to an Inspect log directory, zip archive or ``log.json``.

    Returns:
        List of :class:`EvalResult`, one per evaluated sample, in log order.

    Raises:
        FileNotFoundError: If ``log_path`` does not exist.
        ValueError: If the input cannot be read as an Inspect log.
    """
    path = Path(log_path)
    if not path.exists():
        raise FileNotFoundError(f"inspect log not found: {path}")

    if path.is_dir():
        log_file = path / "log.json"
        if not log_file.exists():
            raise ValueError(f"inspect log directory {path} contains no log.json")
        results = _parse_log_payload(_load_json_bytes(log_file))
        score_file = path / "score.json"
        if score_file.exists():
            overrides = _parse_score_payload(_load_json_bytes(score_file))
            results = _merge_score_overrides(results, overrides)
        return results

    with path.open("rb") as handle:
        magic = handle.read(4)
    if magic.startswith(b"PK\x03\x04"):
        return _parse_zip_archive(path)
    if path.suffix.lower() == ".json":
        return _parse_log_payload(_load_json_bytes(path))
    raise ValueError(f"unrecognized inspect log format: {path}")


def _default_minimum_effect(n: int) -> float:
    """Minimum effect for ``n`` paired samples (tiered, see module docs)."""
    return float(_minimum_effect_tier(n))


def bootstrap_summary(
    results_a: Sequence[EvalResult],
    results_b: Sequence[EvalResult],
    *,
    seed: int = 42,
    minimum_effect: float | None = None,
) -> str:
    """Paired bootstrap summary: ``mean_delta [CI] (direction)``.

    Delegates to :func:`repo_maintenance_agent.evaluation.significance.paired_bootstrap_delta`
    (baseline = ``results_a``, candidate = ``results_b``). Scores are coerced to
    floats (missing scores count as 0). When the bootstrap reports
    ``improvement`` but the effect size is below ``minimum_effect``, the
    direction is downgraded to ``inconclusive``. ``minimum_effect`` defaults to
    :func:`significance.minimum_effect_tier` when available, otherwise a local
    tiered fallback.

    Args:
        results_a: Baseline (champion) results; must be same length as B.
        results_b: Candidate (challenger) results; must be same length as A.
        seed: RNG seed for the bootstrap.
        minimum_effect: Smallest absolute mean delta still called an improvement.

    Returns:
        Formatted summary, e.g. ``0.0500 [-0.1200, 0.0100] (inconclusive)``.
    """
    baseline_scores = [_score_float(result.score) for result in results_a]
    candidate_scores = [_score_float(result.score) for result in results_b]
    decision = paired_bootstrap_delta(
        baseline_scores,
        candidate_scores,
        seed=seed,
    )
    threshold = (
        _default_minimum_effect(len(results_a)) if minimum_effect is None else float(minimum_effect)
    )
    direction = decision.direction
    if direction == "improvement" and abs(decision.mean_delta) < threshold:
        direction = "inconclusive"
    return (
        f"{decision.mean_delta:.4f} "
        f"[{decision.ci_lower:.4f}, {decision.ci_upper:.4f}] "
        f"({direction})"
    )


def _collect_logs(root: Path) -> list[Path]:
    """Collect eval log sources (zip files / log.json dirs) under ``root``."""
    if root.is_file():
        return [root]
    found: list[Path] = []
    for path in sorted(root.iterdir()):
        if (path.is_file() and (path.suffix.lower() == ".eval" or path.name == "log.json")) or (
            path.is_dir() and (path / "log.json").exists()
        ):
            found.append(path)
    return sorted(found, key=str)


def _ingest_arm(logs: Sequence[Path]) -> list[EvalResult]:
    """Ingest every log of one arm and flatten the rows."""
    results: list[EvalResult] = []
    for log in logs:
        results.extend(ingest_inspect_log(log))
    return results


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument(
        "--dir",
        type=Path,
        required=True,
        help="Inspect log root: champion/challenger subdirs, or two eval logs",
    )
    argument_parser.add_argument(
        "--arms",
        default="champion,challenger",
        help="comma-separated arm names (default: champion,challenger)",
    )
    argument_parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="bootstrap RNG seed (default: 42)",
    )
    argument_parser.add_argument(
        "--min-effect",
        type=float,
        default=None,
        help="minimum absolute mean delta for an improvement claim",
    )
    return argument_parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    arm_names = [name.strip() for name in args.arms.split(",") if name.strip()]
    if not arm_names:
        raise ValueError("--arms must name at least one arm")

    arm_logs: dict[str, list[Path]] = {}
    for name in arm_names:
        candidate = args.dir / name
        if candidate.exists():
            arm_logs[name] = _collect_logs(candidate)
    if not arm_logs:
        logs = _collect_logs(args.dir)
        if len(logs) != len(arm_names):
            raise ValueError(
                f"--dir {args.dir} has {len(logs)} eval logs; expected {len(arm_names)} "
                f"for arms {arm_names}"
            )
        arm_logs = {name: [log] for name, log in zip(arm_names, logs, strict=True)}

    ingested: dict[str, list[EvalResult]] = {
        name: _ingest_arm(logs) for name, logs in arm_logs.items()
    }
    for name, results in ingested.items():
        for result in sorted(results, key=lambda item: item.case_id):
            row = asdict(result)
            row["arm"] = name
            print(json.dumps(row, ensure_ascii=False, sort_keys=True))
    if len(ingested) == 2:
        champion, challenger = (ingested[name] for name in arm_names[:2])
        summary = bootstrap_summary(
            champion,
            challenger,
            seed=args.seed,
            minimum_effect=args.min_effect,
        )
        print(f"bootstrap: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
