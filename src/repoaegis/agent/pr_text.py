"""What the pull request says.

Pure template over facts the pipeline already holds: which files changed, what
the approved plan claimed, how the change should be checked, what the run cost.
No model is involved, on purpose. A reviewer opening this wants a precise list,
and handing the same facts to a model to restate in prose adds a chance of
getting them wrong, a call to pay for, and a failure that could block a pull
request someone is waiting on. The description is generated the way a build
report is generated.

It is also worth being plain about what this text is for: it tells a reviewer
where the change came from, that two approval gates passed, and what it cost.
A pull request that hides its machine origin is a pull request that wastes
someone's afternoon.
"""

from __future__ import annotations

from collections.abc import Sequence

from repoaegis.agent.plan import Plan

MAX_TITLE = 70


def title_for(plan: Plan, issue_title: str) -> str:
    """One line a reviewer can scan in a list of twenty."""
    diagnosis = plan.diagnosis.strip().split("\n", 1)[0]
    if len(diagnosis) <= MAX_TITLE:
        return f"fix: {diagnosis}"
    fallback = issue_title.strip() or diagnosis
    return f"fix: {fallback[:MAX_TITLE]}"


def render(
    *,
    plan: Plan,
    summary: str,
    changed: Sequence[str],
    issue_url: str,
    steps: int,
    cost_usd: float,
    model: str,
) -> str:
    """The body. Every line is a fact the pipeline recorded."""
    files = "\n".join(f"- `{name}`" for name in changed) or "- （无）"
    cited = (
        "\n".join(
            f"- `{loc.file}:{loc.line_start}-{loc.line_end}` — {loc.why}" for loc in plan.locations
        )
        or "- （计划未给出具体位置）"
    )
    sections = [
        f"关联 issue：{issue_url}" if issue_url else "",
        "",
        "## 根因",
        "",
        plan.diagnosis.strip(),
        "",
        "## 改动",
        "",
        summary.strip() or "（求解器未给出总结）",
        "",
        files,
        "",
        "## 计划中定位到的位置",
        "",
        cited,
        "",
        "## 如何验证",
        "",
        plan.verification.strip() or "（计划未给出验证方式）",
        "",
        "---",
        "",
        f"由 RepoAegis 生成：{model} · {steps} 步 · ${cost_usd:.4f} · "
        f"计划与补丁均经人工审批门放行。",
    ]
    return "\n".join(sections).strip() + "\n"
