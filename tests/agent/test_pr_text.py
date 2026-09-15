"""What the pull request says. Pure template, so the tests are about facts."""

from typing import Any

from repoaegis.agent.plan import Location, Plan
from repoaegis.agent.pr_text import MAX_TITLE, render, title_for

PLAN = Plan(
    diagnosis="Content-Length 被无条件设置",
    locations=[Location(file="requests/models.py", line_start=386, line_end=392, why="这里写入")],
    approach="只在有 body 时设置",
    verification="pytest test_requests.py -k content_length",
)


def body(**overrides: Any) -> str:
    arguments: dict[str, Any] = {
        "plan": PLAN,
        "summary": "删掉了那一行",
        "changed": ["requests/models.py"],
        "issue_url": "https://github.com/psf/requests/issues/1142",
        "steps": 17,
        "cost_usd": 0.006,
        "model": "deepseek-flash",
    }
    return render(**{**arguments, **overrides})


def test_the_title_leads_with_the_diagnosis() -> None:
    assert title_for(PLAN, "any issue title") == "fix: Content-Length 被无条件设置"


def test_a_rambling_diagnosis_falls_back_to_the_issue_title() -> None:
    """A title nobody can scan is worse than a plain restatement of the issue."""
    verbose = Plan(diagnosis="因为 " * 60, approach="x")
    title = title_for(verbose, "redirects drop the fragment")
    assert title == "fix: redirects drop the fragment"
    assert len(title) <= MAX_TITLE + len("fix: ")


def test_a_rambling_diagnosis_with_no_issue_title_is_simply_cut() -> None:
    verbose = Plan(diagnosis="x" * 300, approach="a")
    assert title_for(verbose, "") == "fix: " + "x" * MAX_TITLE


def test_the_body_carries_every_fact_the_pipeline_recorded() -> None:
    text = body()
    assert "https://github.com/psf/requests/issues/1142" in text
    assert "`requests/models.py`" in text
    assert "requests/models.py:386-392" in text
    assert "pytest test_requests.py -k content_length" in text
    assert "deepseek-flash · 17 步 · $0.0060" in text


def test_the_body_says_it_was_machine_generated_and_gated() -> None:
    """Hiding that would waste a reviewer's afternoon."""
    text = body()
    assert "RepoAegis" in text and "审批门" in text


def test_a_plan_without_locations_says_so_rather_than_leaving_a_hole() -> None:
    text = body(plan=Plan(diagnosis="d", approach="a"), changed=[])
    assert "计划未给出具体位置" in text
    assert "计划未给出验证方式" in text
    assert "- （无）" in text


def test_a_run_without_an_issue_url_omits_the_line_rather_than_linking_nowhere() -> None:
    text = body(issue_url="")
    assert "关联 issue" not in text
    assert text.startswith("## 根因")


def test_no_model_is_consulted() -> None:
    """render is a pure function; nothing here can fail or cost money."""
    import inspect

    from repoaegis.agent import pr_text

    assert "llm" not in inspect.getsource(pr_text).lower()
