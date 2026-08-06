from __future__ import annotations

import difflib
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import pairwise

from repo_maintenance_agent.agents.schemas import PatchEdit, PatchProposal


@dataclass(frozen=True)
class RenderedPatch:
    data: bytes
    changed_files: tuple[str, ...]


def render_patch(
    proposal: PatchProposal,
    *,
    current_files: Mapping[str, object],
    declared_files: tuple[str, ...],
) -> RenderedPatch:
    declared = {path.replace("\\", "/") for path in declared_files}
    edits_by_path: dict[str, list[PatchEdit]] = defaultdict(list)
    for edit in proposal.edits:
        if edit.path not in declared:
            raise ValueError(f"patch path is not declared in the approved plan: {edit.path}")
        edits_by_path[edit.path].append(edit)

    sections: list[str] = []
    for path in sorted(edits_by_path):
        edits = edits_by_path[path]
        current = current_files.get(path, {"error": "not_found"})
        creation_edits = [edit for edit in edits if edit.old_text is None]
        if len(creation_edits) > 1:
            raise ValueError(f"patch path is created more than once: {path}")
        if creation_edits:
            if len(edits) != 1:
                raise ValueError(f"file creation cannot be combined with replacement edits: {path}")
            if isinstance(current, str):
                raise ValueError(f"patch creation target already exists: {path}")
            updated = creation_edits[0].new_text
            sections.append(_unified_section(path, "", updated, is_new=True))
            continue

        if not isinstance(current, str):
            raise ValueError(f"patch replacement target was not found: {path}")
        replacements = _locate_replacements(path, current, edits)
        updated = current
        for start, end, replacement in reversed(replacements):
            updated = updated[:start] + replacement + updated[end:]
        sections.append(_unified_section(path, current, updated, is_new=False))

    return RenderedPatch(
        data="".join(sections).encode("utf-8"),
        changed_files=tuple(sorted(edits_by_path)),
    )


def _locate_replacements(
    path: str,
    current: str,
    edits: list[PatchEdit],
) -> list[tuple[int, int, str]]:
    replacements: list[tuple[int, int, str]] = []
    newline = _source_newline(current)
    for edit in edits:
        assert edit.old_text is not None
        old_text = _with_newline(edit.old_text, newline)
        new_text = _with_newline(edit.new_text, newline)
        positions = _all_positions(current, old_text)
        if not positions:
            raise ValueError(f"patch old_text was not found in {path}")
        if len(positions) > 1:
            raise ValueError(f"patch old_text occurs more than once in {path}")
        start = positions[0]
        replacements.append((start, start + len(old_text), new_text))

    replacements.sort(key=lambda item: item[0])
    for previous, current_edit in pairwise(replacements):
        if current_edit[0] < previous[1]:
            raise ValueError(f"patch edits overlap in {path}")
    return replacements


def _source_newline(content: str) -> str:
    if "\r\n" in content:
        return "\r\n"
    if "\r" in content:
        return "\r"
    return "\n"


def _with_newline(value: str, newline: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)


def _all_positions(content: str, needle: str) -> list[int]:
    positions: list[int] = []
    start = 0
    while True:
        position = content.find(needle, start)
        if position < 0:
            return positions
        positions.append(position)
        start = position + 1


def _unified_section(path: str, before: str, after: str, *, is_new: bool) -> str:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="/dev/null" if is_new else f"a/{path}",
        tofile=f"b/{path}",
        lineterm="\n",
    )
    body = "".join(_with_no_newline_markers(lines))
    mode = "new file mode 100644\n" if is_new else ""
    return f"diff --git a/{path} b/{path}\n{mode}{body}"


def _with_no_newline_markers(lines: Iterable[str]) -> list[str]:
    rendered: list[str] = []
    for line in lines:
        if line.startswith((" ", "+", "-")) and not line.endswith(("\n", "\r")):
            rendered.extend((line + "\n", "\\ No newline at end of file\n"))
        else:
            rendered.append(line)
    return rendered
