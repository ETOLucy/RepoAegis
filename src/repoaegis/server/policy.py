"""Who decides whether a step needs a human.

A policy is an ordered rule list; the first rule that matches wins and yields
one of three outcomes: allow (proceed silently), deny (refuse outright), ask
(stop and wait for a person). Anything unmatched falls through to the policy's
fallback, which is ``ASK`` everywhere except the benchmark policy -- an unknown
action is exactly the kind that deserves a human.

Keeping this declarative is what makes the evaluation honest: the gated and
ungated runs are the same binary with a different policy name, not two
codebases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Final


class Outcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@lru_cache(maxsize=256)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


@dataclass(frozen=True, slots=True)
class Rule:
    outcome: Outcome
    kind: str = "*"
    pattern: str | None = None
    reason: str = ""

    def matches(self, kind: str, subject: str) -> bool:
        if self.kind != "*" and self.kind != kind:
            return False
        return self.pattern is None or _compiled(self.pattern).search(subject) is not None


@dataclass(frozen=True, slots=True)
class Verdict:
    outcome: Outcome
    reason: str
    policy: str


@dataclass(frozen=True, slots=True)
class Policy:
    name: str
    rules: tuple[Rule, ...]
    fallback: Outcome = Outcome.ASK

    def decide(self, kind: str, subject: str) -> Verdict:
        for rule in self.rules:
            if rule.matches(kind, subject):
                return Verdict(rule.outcome, rule.reason or f"rule:{rule.kind}", self.name)
        return Verdict(self.fallback, "fallback", self.name)


_DESTRUCTIVE: Final = r"rm\s+-[rf]|mkfs|dd\s+if=|:\(\)\{|>\s*/dev/sd|chmod\s+-R\s+777"
_READ_ONLY: Final = r"^\s*(ls|cat|head|tail|grep|rg|find|git (status|diff|log|show)|pytest)\b"

DEFAULT: Final = Policy(
    name="default",
    rules=(
        Rule(Outcome.DENY, kind="shell", pattern=_DESTRUCTIVE, reason="destructive command"),
        Rule(Outcome.ALLOW, kind="shell", pattern=_READ_ONLY, reason="read-only command"),
        Rule(Outcome.ASK, kind="plan", reason="plans are reviewed before execution"),
        Rule(Outcome.ASK, kind="push", reason="pushing is visible to other people"),
    ),
)

# Benchmarks run unattended: everything a human would be asked about is granted,
# but the destructive rules stay, so a run cannot wreck the harness.
AUTO_APPROVE: Final = Policy(
    name="auto_approve",
    rules=(Rule(Outcome.DENY, kind="shell", pattern=_DESTRUCTIVE, reason="destructive command"),),
    fallback=Outcome.ALLOW,
)

# The other end of the ablation: every gate asks, nothing is pre-approved.
ALWAYS_ASK: Final = Policy(name="always_ask", rules=())

POLICIES: Final = {p.name: p for p in (DEFAULT, AUTO_APPROVE, ALWAYS_ASK)}


class UnknownPolicy(KeyError):
    pass


def get_policy(name: str) -> Policy:
    try:
        return POLICIES[name]
    except KeyError:
        raise UnknownPolicy(f"unknown approval policy {name!r}") from None
