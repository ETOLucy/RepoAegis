"""Logging must never be able to take down the thing it is logging."""

import io
import sys

import pytest
import structlog

from repoaegis.server.config import configure_logging


def test_a_log_line_with_any_character_survives_a_gbk_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What a Windows console does by default: refuse anything outside GBK."""
    console = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
    monkeypatch.setattr(sys, "stdout", console)
    with pytest.raises(UnicodeEncodeError):
        console.write("\U0001f4a9")  # the character that wedged a real task

    configure_logging(json=False)
    structlog.get_logger("t").info("task.status_changed", body="issue text \U0001f4a9 here")

    sys.stdout.flush()
    written = console.buffer.getvalue().decode("utf-8", "replace")  # type: ignore[attr-defined]
    assert "task.status_changed" in written and "issue text" in written
