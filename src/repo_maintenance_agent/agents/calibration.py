"""CalibrationJudge: standalone judge that recalibrates intake standards.

Each stage (research, planning, coding) can call calibrate() to check whether
the existing task_spec fields (task_type, acceptance_criteria, constraints,
unknowns) are still valid given newly gathered evidence.  Calibration results
are written into task_spec.calibration so downstream stages see the adjusted
standards.

Design principle:
- Independent from intake: does NOT modify intake output directly
- All stages have equal authority to request calibration
- Calibration decisions are based on explicit evidence, not LLM guesswork
- Results are stored as a diff/overlay, not a full replacement
"""

from __future__ import annotations

from typing import Any

from repo_maintenance_agent.domain.models import Evidence


class CalibrationJudge:
    """Standalone judge that recalibrates intake standards.

    Args:
        model: An LLM port with a ``structured()`` method, or None to use
               rule-based heuristics only.
    """

    def __init__(self, model: Any | None = None) -> None:
        self._model = model

    async def calibrate(
        self,
        task_spec: dict[str, Any],
        evidence: list[Evidence],
        stage: str,  # "research" | "planning" | "coding"
    ) -> dict[str, Any]:
        """Check and potentially adjust intake standards.

        Returns a calibration diff dict with keys:
        - calibrated_task_type: str | None  — adjusted task type, or None if unchanged
        - calibrated_ac: list[str] | None   — adjusted acceptance criteria
        - calibrated_constraints: list[str] | None
        - calibrated_unknowns: list[str] | None
        - calibration_reason: str           — why the calibration was (or was not) made
        - calibrated_by: str                — which stage triggered the calibration
        """
        if self._model is not None:
            return await self._llm_calibrate(task_spec, evidence, stage)
        return self._rule_calibrate(task_spec, evidence, stage)

    async def _llm_calibrate(
        self,
        task_spec: dict[str, Any],
        evidence: list[Evidence],
        stage: str,
    ) -> dict[str, Any]:
        """LLM-based calibration: ask the model to check consistency."""
        try:
            from repo_maintenance_agent.agents.schemas import CalibrationOutput

            output = await self._model.structured(
                system=(
                    "You are a calibration judge. Given the existing task specification "
                    "and newly gathered evidence, determine whether the task_type, "
                    "acceptance_criteria, constraints, or unknowns need adjustment. "
                    "Only suggest changes backed by concrete evidence. "
                    "Return a CalibrationOutput JSON object."
                ),
                input_text=_build_calibration_prompt(task_spec, evidence, stage),
                schema=CalibrationOutput,
                max_attempts=2,
            )
            return {
                "calibrated_task_type": output.calibrated_task_type,
                "calibrated_ac": output.calibrated_ac,
                "calibrated_constraints": output.calibrated_constraints,
                "calibrated_unknowns": output.calibrated_unknowns,
                "calibration_reason": output.calibration_reason,
                "calibrated_by": stage,
            }
        except Exception:
            # Fall back to rule-based
            return self._rule_calibrate(task_spec, evidence, stage)

    def _rule_calibrate(
        self,
        task_spec: dict[str, Any],
        evidence: list[Evidence],
        stage: str,
    ) -> dict[str, Any]:
        """Rule-based calibration: simple consistency checks.

        Rules:
        1. If evidence contains test files but task_type is not "test", flag mismatch
        2. If evidence contains error messages but task_type is not "bugfix", flag mismatch
        3. If evidence is empty, mark as "insufficient evidence"
        """
        result: dict[str, Any] = {
            "calibrated_task_type": None,
            "calibrated_ac": None,
            "calibrated_constraints": None,
            "calibrated_unknowns": None,
            "calibration_reason": "no adjustment needed",
            "calibrated_by": stage,
        }

        if not evidence:
            result["calibration_reason"] = (
                "insufficient evidence to verify intake standards"
            )
            return result

        current_type = task_spec.get("task_type", "")
        has_test_evidence = any(
            "test_" in e.source or "/test/" in e.source or "/tests/" in e.source
            for e in evidence
        )
        has_error_evidence = any(
            "error" in e.source.lower() or "exception" in e.summary[:200].lower()
            for e in evidence
        )

        if has_test_evidence and current_type != "test":
            result["calibrated_task_type"] = "test"
            result["calibration_reason"] = (
                f"evidence contains test files but task_type is '{current_type}'; "
                "suggesting 'test'"
            )
        elif has_error_evidence and current_type != "bugfix":
            result["calibrated_task_type"] = "bugfix"
            result["calibration_reason"] = (
                f"evidence contains error messages but task_type is '{current_type}'; "
                "suggesting 'bugfix'"
            )

        return result


def _build_calibration_prompt(
    task_spec: dict[str, Any],
    evidence: list[Evidence],
    stage: str,
) -> str:
    """Build the calibration prompt for the LLM."""
    import json

    lines = [
        "## Current Task Specification",
        json.dumps(task_spec, indent=2, sort_keys=True),
        "",
        f"## Stage Triggering Calibration: {stage}",
        "",
        "## Evidence Summary",
    ]
    for i, e in enumerate(evidence[:10], 1):
        lines.append(f"{i}. [{e.source}] {e.locator}")
        lines.append(f"   {e.summary[:300]}")
    if len(evidence) > 10:
        lines.append(f"   ... and {len(evidence) - 10} more items")
    return "\n".join(lines)
