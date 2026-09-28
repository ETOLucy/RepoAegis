"""Which tests to run after an edit, and running them in the sandbox.

The whole suite is the safe answer and the wrong one: on the repositories this
project targets it takes minutes to tens of minutes, and a round that spends
that long on tests has spent its budget on the wrong thing. The selection here
is the static, deterministic kind: the command the approved plan named, plus
the test files that correspond to the changed modules by name or by import.
TDAD (2603.17973) reports that a static test map of this shape cut the
regression rate by about 70% at a cost of two points of resolve rate;
TestPrune (2510.18270) goes further with coverage-guided minimisation and is
the obvious next step, behind the same ``select_tests`` seam.

Two things are deliberately not done. Nothing is chosen by a model call --
selection is a function of the plan and the diff, so it can be tested and its
misses can be read off a log. And when nothing matches, the fallback is the
repository's ``tests`` directory rather than nothing: a round that ran zero
tests would be reported green by accident.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shlex
from collections.abc import Iterator, Sequence
from pathlib import Path

import structlog

from repoaegis.agent.plan import Plan
from repoaegis.agent.rounds import Failure, TestReport, failing_ids, parse_junitxml
from repoaegis.agent.sandbox import (
    EXCLUDED_DIRS,
    DockerSandbox,
    EnvironmentImages,
    SandboxTimeout,
    archive_tree,
)
from repoaegis.agent.tools import Workspace
from repoaegis.agent.workspace import GitError, git

log = structlog.get_logger(__name__)

MAX_SELECTED = 25
RUNS_DIR = ".repoaegis/runs"
BASELINE_DIR = ".repoaegis/baseline"
REPORT_IN_CONTAINER = "/tmp/repoaegis-report.xml"

_PYTEST_COMMAND = re.compile(r"^\s*(?:python\d?(?:\.\d+)?\s+-m\s+)?(?:py\.test|pytest)\b(.*)$")
_TEST_FILE = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.py)$")


def is_test_file(path: str) -> bool:
    return bool(_TEST_FILE.search(path))


def _test_files(ws: Workspace) -> Iterator[Path]:
    for folder, _subdirs, files in _walk(ws.root):
        for name in files:
            if _TEST_FILE.search(name):
                yield Path(folder) / name


def _walk(root: Path) -> Iterator[tuple[str, list[str], list[str]]]:
    import os

    for folder, subdirs, files in os.walk(root):
        subdirs[:] = sorted(d for d in subdirs if d not in EXCLUDED_DIRS)
        yield folder, subdirs, sorted(files)


def _module_names(changed: str) -> tuple[str, str]:
    """The dotted module a changed file defines, and its bare stem.

    ``src/requests/models.py`` -> (``requests.models``, ``models``). The
    ``src/`` prefix is the one layout convention worth special-casing.
    """
    parts = Path(changed).with_suffix("").as_posix().split("/")
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts), (parts[-1] if parts else "")


def from_plan(plan: Plan, ws: Workspace) -> list[str]:
    """pytest arguments the plan's ``verification`` line asked for, if it is a pytest line."""
    match = _PYTEST_COMMAND.match(
        plan.verification.strip().splitlines()[0] if plan.verification else ""
    )
    if not match:
        return []
    kept: list[str] = []
    tokens = shlex.split(match.group(1), posix=True)
    skip_next = False
    for token in tokens:
        if skip_next:
            kept.append(token)
            skip_next = False
            continue
        if token in {"-k", "-m", "-p", "-o", "-W", "--deselect"}:
            kept.append(token)
            skip_next = True
            continue
        if token.startswith("-"):
            if token not in {"-x", "--exitfirst", "-q", "--quiet", "-v", "--verbose"}:
                kept.append(token)
            continue
        target = token.split("::", 1)[0]
        if (ws.root / target).exists():
            kept.append(token)
    return kept


def by_name_and_import(ws: Workspace, changed: Sequence[str]) -> list[str]:
    """Test files that name a changed module in their filename or import it."""
    stems: dict[str, str] = {}  # stem -> dotted
    picked: set[str] = set()
    for path in changed:
        if not path.endswith(".py"):
            continue
        if is_test_file(path):
            if (ws.root / path).is_file():
                picked.add(Path(path).as_posix())
            continue
        dotted, stem = _module_names(path)
        if stem:
            stems[stem] = dotted

    if not stems:
        return sorted(picked)

    name_patterns = {f"test_{s}.py" for s in stems} | {f"{s}_test.py" for s in stems}
    import_pattern = re.compile(
        r"^\s*(?:from\s+(?P<mod>[\w.]+)\s+import\b|import\s+(?P<imp>[\w.]+))", re.MULTILINE
    )
    wanted = set(stems.values()) | set(stems)

    for test in _test_files(ws):
        rel = test.relative_to(ws.root).as_posix()
        if test.name in name_patterns or any(test.name.startswith(f"test_{s}_") for s in stems):
            picked.add(rel)
            continue
        try:
            text = test.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in import_pattern.finditer(text):
            module = m.group("mod") or m.group("imp") or ""
            head = module.split(".")
            # Either the exact dotted module, or a module whose tail is the stem
            # (``from .models import`` inside the package).
            if module in wanted or (head and head[-1] in stems):
                picked.add(rel)
                break
    return sorted(picked)


def select_tests(ws: Workspace, plan: Plan, changed: Sequence[str]) -> list[str]:
    """pytest arguments for this round. Empty means "the whole suite"."""
    args = from_plan(plan, ws)
    files = by_name_and_import(ws, changed)
    present = {a for a in args if not a.startswith("-")}
    for f in files:
        if f not in present:
            args.append(f)
    paths = [a for a in args if not a.startswith("-")]
    if len(paths) > MAX_SELECTED:
        options = [a for a in args if a.startswith("-")]
        args = options + paths[:MAX_SELECTED]
        log.info("tests.capped", selected=len(paths), kept=MAX_SELECTED)
    if not paths:
        if (ws.root / "tests").is_dir():
            args.append("tests")
        elif (ws.root / "test").is_dir():
            args.append("test")
    return args


class DockerVerifier:
    """Runs the selected tests in a fresh container and reports back."""

    def __init__(
        self,
        sandbox: DockerSandbox,
        images: EnvironmentImages,
        *,
        timeout_seconds: float = 600.0,
    ) -> None:
        self._sandbox = sandbox
        self._images = images
        self._timeout = timeout_seconds

    async def run(self, ws: Workspace, plan: Plan, changed: Sequence[str]) -> TestReport:
        run_dir = await _next_run_dir(ws.root)
        selected = select_tests(ws, plan, changed)
        command = (
            "python -m pytest -p no:cacheprovider -q -rN "
            f"--junitxml={REPORT_IN_CONTAINER} " + " ".join(shlex.quote(a) for a in selected)
        )
        (run_dir / "command.txt").write_text(command + "\n", encoding="utf-8")
        log_path = (run_dir / "pytest.log").relative_to(ws.root).as_posix()

        image = await self._images.ensure(ws)
        try:
            code, output, xml = await self._execute(image, await archive_tree(ws.root), command)
        except SandboxTimeout as exc:
            (run_dir / "pytest.log").write_text(f"{exc}\n", encoding="utf-8")
            return _timed_out(str(exc), log_path)
        (run_dir / "pytest.log").write_text(output, encoding="utf-8")

        if xml is None:
            # pytest never got as far as writing a report: a collection error,
            # a missing dependency, a crash. The log has the reason.
            return TestReport(
                errors=1,
                failures=[
                    Failure(
                        test="(pytest)",
                        kind="NoReport",
                        message=f"pytest exited {code} without writing a report; see the log",
                        excerpt="\n".join(output.strip().splitlines()[-8:]),
                    )
                ],
                log_path=log_path,
            )
        (run_dir / "report.xml").write_bytes(xml)
        text = xml.decode("utf-8", "replace")
        report = parse_junitxml(text, log_path=log_path)
        if not report.ok:
            # Red: find out how much of it was red before the change. Only
            # now, so a green run never pays for a second one.
            known = await self._baseline(ws, image, command, run_dir)
            if known:
                report = parse_junitxml(text, log_path=log_path, known_failing=known)
        log.info(
            "tests.finished",
            passed=report.passed,
            failed=report.failed,
            errors=report.errors,
            preexisting=report.preexisting,
            selected=len(selected),
        )
        return report

    async def _execute(
        self, image: str, archive: bytes, command: str
    ) -> tuple[int, str, bytes | None]:
        """One container: unpack the tree, run the command, bring the report back."""
        container = await self._sandbox.start(image)
        try:
            await self._sandbox.put_tree(container, archive)
            code, output = await self._sandbox.exec(container, command, timeout=self._timeout)
            xml = await self._sandbox.read_file(container, REPORT_IN_CONTAINER)
        finally:
            await self._sandbox.stop(container)
        return code, output, xml

    async def _baseline(self, ws: Workspace, image: str, command: str, run_dir: Path) -> set[str]:
        """Test ids that fail on the committed tree with the same command.

        Cached per command under ``.repoaegis/baseline`` so later rounds of the
        same task do not rerun it. An unavailable baseline (not a git checkout,
        a timeout) yields the empty set: nothing gets excused by guesswork.
        """
        cache_dir = ws.root / BASELINE_DIR
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / f"{hashlib.sha256(command.encode()).hexdigest()[:12]}.xml"
        if cached.is_file():
            return failing_ids(cached.read_text(encoding="utf-8", errors="replace"))

        base = await _committed_tree(ws.root)
        if base is None:
            return set()
        try:
            _, output, xml = await self._execute(image, base, command)
        except SandboxTimeout as exc:
            log.warning("baseline.timeout", detail=str(exc))
            return set()
        (run_dir / "baseline.log").write_text(output, encoding="utf-8")
        if xml is None:
            return set()
        cached.write_bytes(xml)
        ids = failing_ids(xml.decode("utf-8", "replace"))
        log.info("baseline.finished", failing=len(ids))
        return ids


def _timed_out(detail: str, log_path: str) -> TestReport:
    return TestReport(
        errors=1,
        failures=[Failure(test="(pytest)", kind="Timeout", message=detail)],
        log_path=log_path,
    )


async def _committed_tree(root: Path) -> bytes | None:
    """``git archive HEAD``: the tree as it was before any edit, as a tar."""
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "archive",
            "--format=tar",
            "HEAD",
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(process.communicate(), 120)
    except (OSError, TimeoutError):
        return None
    return out if process.returncode == 0 and out else None


async def _next_run_dir(root: Path) -> Path:
    """``.repoaegis/runs/<n>`` inside the checkout, kept out of git's sight."""
    runs = root / RUNS_DIR
    runs.mkdir(parents=True, exist_ok=True)
    await _exclude_from_git(root)
    existing = [int(p.name) for p in runs.iterdir() if p.is_dir() and p.name.isdigit()]
    run_dir = runs / str(max(existing, default=0) + 1)
    run_dir.mkdir()
    return run_dir


async def _exclude_from_git(root: Path) -> None:
    """Add ``.repoaegis/`` to the checkout's private exclude list.

    The diff the reviewer sees is ``git add -A`` then ``git diff --cached``, so
    anything untracked would appear in it. ``info/exclude`` is the per-clone
    ignore file that never touches the repository's own ``.gitignore``.
    """
    try:
        exclude = (
            await git("rev-parse", "--git-path", "info/exclude", cwd=root, timeout=30)
        ).strip()
    except GitError:
        return  # not a git checkout (tests); nothing to protect
    path = Path(exclude) if Path(exclude).is_absolute() else root / exclude
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    if ".repoaegis/" not in current.splitlines():
        with path.open("a", encoding="utf-8") as f:
            f.write(("" if current.endswith("\n") or not current else "\n") + ".repoaegis/\n")
