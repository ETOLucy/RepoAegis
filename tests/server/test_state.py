from itertools import pairwise

import pytest

from repoaegis.server.models import TaskStatus as S
from repoaegis.server.state import TERMINAL, TRANSITIONS, IllegalTransition, assert_transition


def test_every_status_has_a_row() -> None:
    assert set(TRANSITIONS) == set(S)


def test_terminal_states_have_no_exits() -> None:
    for status in TERMINAL:
        assert TRANSITIONS[status] == frozenset()


def test_every_non_terminal_state_can_reach_a_terminal_state() -> None:
    for start in set(S) - TERMINAL:
        seen, frontier = set(), [start]
        while frontier:
            cur = frontier.pop()
            if cur in TERMINAL:
                break
            seen.add(cur)
            frontier.extend(n for n in TRANSITIONS[cur] if n not in seen)
        else:
            pytest.fail(f"{start} cannot reach a terminal state")


def test_happy_path_is_legal() -> None:
    path = [S.QUEUED, S.PLANNING, S.AWAITING_APPROVAL, S.SOLVING, S.VERIFYING, S.DELIVERING, S.DONE]
    for frm, to in pairwise(path):
        assert_transition(frm, to)


def test_skipping_the_approval_gate_is_illegal() -> None:
    with pytest.raises(IllegalTransition):
        assert_transition(S.PLANNING, S.SOLVING)
