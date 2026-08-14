"""RepoAegis SWE-bench progress scorer for Inspect.

Layers a continuous 0..1 "partial resolution" score on top of the official
SWE-bench pass/fail signal. The official SWE-bench scorer (or any solver that
runs the official test suite) records per-test outcomes; RepoAegis additionally
wants to know *how much* of the required test set was satisfied so partial
progress can feed statistical gating (AegisEvo) and failure triage.

Version note (inspect_ai 0.3.255): ``TaskState`` exposes sample metadata via
``state.metadata`` (there is no ``state.sample`` attribute in this release), so
this scorer reads ``FAIL_TO_PASS`` / ``PASS_TO_PASS`` from ``state.metadata``.
"""

from __future__ import annotations

from typing import Any

from inspect_ai.scorer import Score, Scorer, Target, mean, scorer
from inspect_ai.solver import TaskState

#: Metadata keys produced by the RepoAegis solver / bridge integration.
PASSED_RATIO_KEY = "passed_ratio"
PASSED_FTP_KEY = "passed_ftp"
PASSED_P2P_KEY = "passed_p2p"


def _as_int(value: Any, default: int = 0) -> int:
    """Coerce a value to ``int``, tolerating ``None`` / malformed strings."""
    if value is None or isinstance(value, bool):
        return int(value) if isinstance(value, bool) else default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _progress_ratio(
    passed_ftp: int,
    passed_p2p: int,
    total_ftp: int,
    total_p2p: int,
) -> float:
    """Fraction of required tests that passed, clamped to ``[0, 1]``.

    Args:
        passed_ftp: Number of ``FAIL_TO_PASS`` tests that passed.
        passed_p2p: Number of ``PASS_TO_PASS`` tests that passed.
        total_ftp: Total number of ``FAIL_TO_PASS`` tests.
        total_p2p: Total number of ``PASS_TO_PASS`` tests.

    Returns:
        A float in ``[0, 1]``; ``0.0`` when there are no required tests.
    """
    total = max(0, total_ftp) + max(0, total_p2p)
    if total <= 0:
        return 0.0
    passed = max(0, passed_ftp) + max(0, passed_p2p)
    return round(min(1.0, passed / total), 4)


@scorer(metrics=[mean()])
def repoaegis_swe_progress_scorer(pass_threshold: float = 0.5) -> Scorer:
    """Create the RepoAegis SWE-bench partial-progress scorer.

    Reads per-sample test metadata from ``state.metadata``:

    * ``passed_ratio`` (float): precomputed progress in ``[0, 1]`` written by a
      solver that ran the official tests (production path).
    * ``passed_ftp`` / ``passed_p2p`` (int): number of passed
      ``FAIL_TO_PASS`` / ``PASS_TO_PASS`` tests (fallback path).

    When ``passed_ratio`` is absent the ratio is derived from
    ``passed_ftp`` / ``passed_p2p`` over the total ``FAIL_TO_PASS`` /
    ``PASS_TO_PASS`` list lengths stored in metadata. If the solver recorded
    neither, the sample scores ``0.0`` (a conservative default: no official
    evidence, no credit).

    The returned ``Score.value`` is the continuous float in ``[0, 1]``; a
    boolean ``passed = value >= pass_threshold`` is attached to
    ``Score.metadata`` so the official binary view stays available. The metric
    ``mean()`` aggregates the continuous values across samples.

    Args:
        pass_threshold: Minimum progress treated as "resolved" for the binary
            flag in ``Score.metadata``. Defaults to ``0.5``.

    Returns:
        An Inspect :class:`~inspect_ai.scorer.Scorer`.

    .. note::
        Placeholder implementation: it does not run the official sandbox. The
        production implementation should resolve per-test outcomes from
        ``get_eval_report`` (or the SWE-bench runner's report) and write
        ``passed_ratio`` / ``passed_ftp`` / ``passed_p2p`` back into
        ``state.metadata`` before this scorer runs.
    """

    async def score(state: TaskState, target: Target) -> Score:
        del target
        metadata = dict(state.metadata or {})
        total_ftp = len(metadata.get("FAIL_TO_PASS") or [])
        total_p2p = len(metadata.get("PASS_TO_PASS") or [])
        passed_ftp = _as_int(metadata.get(PASSED_FTP_KEY))
        passed_p2p = _as_int(metadata.get(PASSED_P2P_KEY))

        raw_ratio = metadata.get(PASSED_RATIO_KEY)
        if raw_ratio is None:
            raw_ratio = _progress_ratio(passed_ftp, passed_p2p, total_ftp, total_p2p)
        try:
            progress = max(0.0, min(1.0, float(raw_ratio)))
        except (TypeError, ValueError):
            progress = 0.0

        return Score(
            value=progress,
            answer=f"{progress:.4f}",
            explanation=(
                f"progress={progress:.4f} (passed_ftp={passed_ftp}/{total_ftp}, "
                f"passed_p2p={passed_p2p}/{total_p2p}, threshold={pass_threshold})"
            ),
            metadata={
                "passed": progress >= pass_threshold,
                "pass_threshold": pass_threshold,
                "total_tests": total_ftp + total_p2p,
            },
        )

    return score
