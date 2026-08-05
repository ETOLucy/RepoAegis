from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PrivacyFinding:
    rule_id: str
    path: str
    line: int
    preview: str


_RULES = (
    ("credential.openai", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("credential.github", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    (
        "credential.private-key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    (
        "privacy.windows-user-path",
        re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s]+\\"),
    ),
    ("privacy.local-proxy", re.compile(r"\b127\.0\.0\.1:7897\b")),
)
_MAX_BYTES = 2_000_000
_MAX_HISTORY_BYTES = 50_000_000

# Deterministic test fixture marker used only to verify that credential
# filtering excludes OpenAI-shaped values from generated artifacts. It is
# exempted so the privacy scanner (which also scans reachable git history)
# does not report a known non-credential test value.
_KNOWN_TEST_MARKERS = frozenset({"sk-secret-target-pack-test"})


def scan_paths(paths: list[Path], *, root: Path) -> list[PrivacyFinding]:
    findings: list[PrivacyFinding] = []
    resolved_root = root.resolve()
    for path in paths:
        resolved = path.resolve()
        if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
            continue
        try:
            if resolved.stat().st_size > _MAX_BYTES:
                continue
            text = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        findings.extend(
            _scan_text(
                text,
                display_path=resolved.relative_to(resolved_root).as_posix(),
            )
        )
    return findings


def repository_files(root: Path) -> list[Path]:
    git = _git_executable()
    result = subprocess.run(  # noqa: S603 - resolved executable and fixed arguments
        [
            git,
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [root / raw.decode() for raw in result.stdout.split(b"\0") if raw]


def scan_history(root: Path) -> list[PrivacyFinding]:
    git = _git_executable()
    result = subprocess.run(  # noqa: S603 - resolved executable and fixed arguments
        [
            git,
            "log",
            "-p",
            "--all",
            "--no-ext-diff",
            "--no-color",
            "--format=",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    if len(result.stdout) > _MAX_HISTORY_BYTES:
        raise RuntimeError("git history exceeds privacy scan byte limit")
    return _scan_text(
        result.stdout.decode("utf-8", errors="replace"),
        display_path="<git-history>",
    )


def _scan_text(text: str, *, display_path: str) -> list[PrivacyFinding]:
    findings: list[PrivacyFinding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if any(marker in line for marker in _KNOWN_TEST_MARKERS):
            continue
        for rule_id, pattern in _RULES:
            if pattern.search(line):
                findings.append(
                    PrivacyFinding(
                        rule_id=rule_id,
                        path=display_path,
                        line=line_number,
                        preview=f"[REDACTED:{rule_id}]",
                    )
                )
    return findings


def _git_executable() -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is required for repository privacy scanning")
    return git


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan tracked files for credentials and privacy data."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    findings = scan_paths(repository_files(args.root), root=args.root)
    findings.extend(scan_history(args.root))
    for finding in findings:
        print(f"{finding.path}:{finding.line}: {finding.rule_id} {finding.preview}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
