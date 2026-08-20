from __future__ import annotations

import difflib
import re  # 新增
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
            positions = _fuzzy_positions(current, old_text)
        if not positions:
            # Fallback 1: try with trimmed whitespace
            trimmed_old = _trim_blank_lines(old_text)
            if trimmed_old != old_text:
                positions = _all_positions(current, trimmed_old)
                if not positions:
                    positions = _fuzzy_positions(current, trimmed_old)
        if not positions:
            # Fallback 2: try matching just the first significant lines
            positions = _approx_positions(current, old_text)
        if not positions:
            # Fallback 3: whitespace-normalized matching
            norm_old = _collapse_whitespace(old_text)
            norm_cur = _collapse_whitespace(current)
            if norm_old != old_text or norm_cur != current:
                positions = _all_positions(norm_cur, norm_old)
                if not positions:
                    positions = _fuzzy_positions(norm_cur, norm_old)
        if not positions:
            # Fallback 4: whole-file replacement — only if old_text is a reasonable
            # approximation of the file content (within 50% size similarity)
            # Whole-file fallback: only if old_text is a close match to the file content
            match_ratio = difflib.SequenceMatcher(None, old_text, current).ratio()
            if match_ratio >= 0.5:
                positions = [0]
                old_text = current
                new_text = _with_newline(edit.new_text, newline)
        if not positions:
            raise ValueError(f"patch old_text was not found in {path}")
        if len(positions) > 1:
            # Disambiguate: pick the position whose context window best matches
            best = _disambiguate_positions(current, old_text, positions)
            if best is not None:
                positions = [best]
        if len(positions) > 1:
            raise ValueError(f"patch old_text occurs more than once in {path}")
        start = positions[0]
        replacements.append((start, start + len(old_text), new_text))

    replacements.sort(key=lambda item: item[0])
    for previous, current_edit in pairwise(replacements):
        if current_edit[0] < previous[1]:
            raise ValueError(f"patch edits overlap in {path}")
    return replacements


def _trim_blank_lines(text: str) -> str:
    """Strip leading/trailing blank lines from old_text for lenient matching."""
    lines = text.splitlines(keepends=True)
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    end = len(lines)
    while end > start and not lines[end - 1].strip():
        end -= 1
    if start >= end:
        return text
    return "".join(lines[start:end])


def _approx_positions(content: str, needle: str) -> list[int]:
    """Approximate match using first and last non-blank lines as anchors."""
    n_lines = needle.splitlines(keepends=True)
    first_sig = next((i, line) for i, line in enumerate(n_lines) if line.strip())
    last_sig = next((i, line) for i, line in reversed(list(enumerate(n_lines))) if line.strip())
    if first_sig is None or last_sig is None:
        return []
    c_lines = content.splitlines(keepends=True)
    # Find first significant line of needle in content
    candidates = []
    for i, line in enumerate(c_lines):
        ratio = difflib.SequenceMatcher(None, first_sig[1].strip(), line.strip()).ratio()
        if ratio >= 0.6:
            candidates.append(i)
    if not candidates:
        return []
    # For each candidate, check if the last significant line matches nearby
    for cand_start in candidates:
        end_idx = cand_start + (last_sig[0] - first_sig[0])
        if end_idx < len(c_lines):
            end_ratio = difflib.SequenceMatcher(
                None, last_sig[1].strip(), c_lines[end_idx].strip()
            ).ratio()
            if end_ratio >= 0.6:
                # Build approximate window
                window = "".join(c_lines[cand_start : cand_start + len(n_lines)])
                ratio = difflib.SequenceMatcher(None, needle, window).ratio()
                if ratio >= 0.6:
                    start_char = sum(len(line) for line in c_lines[:cand_start])
                    return [start_char]
    return []


def _disambiguate_positions(content: str, needle: str, positions: list[int]) -> int | None:
    """Pick the best match from multiple positions using context window scoring."""
    best_score = 0.0
    best_pos = None
    for pos in positions:
        window_start = max(0, pos - 100)
        window_end = min(len(content), pos + len(needle) + 100)
        window = content[window_start:window_end]
        ratio = difflib.SequenceMatcher(None, needle, window).ratio()
        if ratio > best_score:
            best_score = ratio
            best_pos = pos
    return best_pos


def _collapse_whitespace(text: str) -> str:
    """Collapse runs of whitespace to a single space for lenient matching.
    Preserves newlines as they are semantically significant."""
    lines = text.split("\n")
    collapsed = [re.sub(r"[ \t]+", " ", line).strip() for line in lines]
    return "\n".join(collapsed)


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


def _fuzzy_positions(content: str, needle: str) -> list[int]:
    """Best-effort whitespace/typo-tolerant match; [] when no confident match."""
    if not needle or not content:
        return []
    content_lines = content.splitlines(keepends=True)
    needle_lines = needle.splitlines(keepends=True)
    if not needle_lines or len(needle_lines) > len(content_lines):
        return []
    needle_first = next((line for line in needle_lines if line.strip()), "")
    if not needle_first:
        return []
    scored = [
        (i, difflib.SequenceMatcher(None, needle_first.strip(), line.strip()).ratio())
        for i, line in enumerate(content_lines)
        if line.strip()
    ]
    if not scored:
        return []
    scored.sort(key=lambda item: item[1], reverse=True)
    best_line, best_ratio = scored[0]
    if best_ratio < 0.6:
        return []
    window_len = len(needle_lines)
    results: list[tuple[int, float]] = []
    lo = max(0, best_line - 1)
    hi = min(len(content_lines) - window_len, best_line + 1)
    for start in range(lo, hi + 1):
        window = "".join(content_lines[start : start + window_len])
        ratio = difflib.SequenceMatcher(None, needle, window).ratio()
        if ratio >= 0.7:
            start_char = sum(len(line) for line in content_lines[:start])
            results.append((start_char, ratio))
    if not results:
        return []
    results.sort(key=lambda item: item[1], reverse=True)
    best = results[0]
    close = [item for item in results if item[1] >= best[1] - 0.02]
    if len(close) > 1:
        # Don't return empty — disambiguate by context window
        return [best[0]]
    return [best[0]]


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
