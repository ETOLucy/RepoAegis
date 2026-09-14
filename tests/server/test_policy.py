"""The policy table is pure logic, so it is tested as a truth table."""

import pytest

from repoaegis.server.policy import Outcome, UnknownPolicy, get_policy


@pytest.mark.parametrize(
    ("kind", "subject", "expected"),
    [
        ("shell", "rm -rf build", Outcome.DENY),
        ("shell", "dd if=/dev/zero of=/dev/sda", Outcome.DENY),
        ("shell", "ls -la src", Outcome.ALLOW),
        ("shell", "pytest tests/", Outcome.ALLOW),
        ("shell", "pip install requests", Outcome.ASK),
        ("plan", "fix the redirect bug", Outcome.ASK),
        ("push", "origin feat/x", Outcome.ASK),
    ],
)
def test_default_policy(kind: str, subject: str, expected: Outcome) -> None:
    assert get_policy("default").decide(kind, subject).outcome is expected


def test_auto_approve_still_refuses_destruction() -> None:
    policy = get_policy("auto_approve")
    assert policy.decide("plan", "anything").outcome is Outcome.ALLOW
    assert policy.decide("push", "origin main").outcome is Outcome.ALLOW
    assert policy.decide("shell", "rm -rf /").outcome is Outcome.DENY


def test_always_ask_is_the_other_end_of_the_ablation() -> None:
    policy = get_policy("always_ask")
    assert policy.decide("shell", "ls").outcome is Outcome.ASK
    assert policy.decide("plan", "x").outcome is Outcome.ASK


def test_first_matching_rule_wins() -> None:
    # "rm -rf" would also match nothing else, but order is what makes deny beat allow.
    rules = get_policy("default").rules
    assert rules[0].outcome is Outcome.DENY


def test_unknown_policy_fails_loudly() -> None:
    with pytest.raises(UnknownPolicy):
        get_policy("permissive-please")
