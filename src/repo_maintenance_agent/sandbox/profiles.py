from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Language(StrEnum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    JAVA = "java"
    GO = "go"
    RUST = "rust"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EnvironmentProfile:
    language: Language
    image_key: str
    dependency_fingerprint_files: tuple[str, ...]
    setup_commands: tuple[tuple[str, ...], ...]
    test_commands: tuple[tuple[str, ...], ...]
    lint_commands: tuple[tuple[str, ...], ...] = ()


class EnvironmentProfiler:
    def inspect(self, workspace: Path) -> EnvironmentProfile:
        root = workspace.resolve()
        if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists():
            fingerprints = tuple(
                name
                for name in ("pyproject.toml", "uv.lock", "poetry.lock", "requirements.txt")
                if (root / name).exists()
            )
            return EnvironmentProfile(
                language=Language.PYTHON,
                image_key="python-3.12",
                dependency_fingerprint_files=fingerprints,
                setup_commands=(
                    ("python", "-m", "venv", ".repo-agent/venv"),
                    (
                        ".repo-agent/venv/bin/python",
                        "-m",
                        "pip",
                        "install",
                        "--disable-pip-version-check",
                        "-e",
                        ".",
                        "pytest",
                        "pytest-asyncio",
                        "pytest-cov",
                        "ruff",
                        "mypy",
                    ),
                ),
                test_commands=(("python", "-m", "pytest"),),
                lint_commands=(("ruff", "check", "."), ("mypy", "src")),
            )
        if (root / "package.json").exists():
            package = self._read_package(root / "package.json")
            language = (
                Language.TYPESCRIPT if (root / "tsconfig.json").exists() else Language.JAVASCRIPT
            )
            scripts = package.get("scripts")
            has_test = isinstance(scripts, dict) and "test" in scripts
            test_command = (
                ("npm", "test", "--", "--run")
                if has_test
                else ("npm", "run", "test", "--", "--run")
            )
            fingerprints = tuple(
                name
                for name in ("package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock")
                if (root / name).exists()
            )
            return EnvironmentProfile(
                language=language,
                image_key="node-22",
                dependency_fingerprint_files=fingerprints,
                setup_commands=(("npm", "ci", "--ignore-scripts"),),
                test_commands=(test_command,),
                lint_commands=(("npm", "run", "lint"),),
            )
        if (root / "pom.xml").exists() or (root / "build.gradle").exists():
            command = ("mvn", "test") if (root / "pom.xml").exists() else ("gradle", "test")
            setup = (
                ("mvn", "-B", "dependency:go-offline")
                if (root / "pom.xml").exists()
                else ("gradle", "dependencies")
            )
            return EnvironmentProfile(Language.JAVA, "jdk-21", (), (setup,), (command,))
        if (root / "go.mod").exists():
            return EnvironmentProfile(
                Language.GO,
                "go-1.24",
                ("go.mod", "go.sum"),
                (("go", "mod", "download"),),
                (("go", "test", "./..."),),
            )
        if (root / "Cargo.toml").exists():
            return EnvironmentProfile(
                Language.RUST,
                "rust-stable",
                ("Cargo.toml", "Cargo.lock"),
                (("cargo", "fetch", "--locked"),),
                (("cargo", "test"),),
                (("cargo", "clippy", "--", "-D", "warnings"),),
            )
        return EnvironmentProfile(Language.UNKNOWN, "generic", (), (), ())

    @staticmethod
    def _read_package(path: Path) -> dict[str, object]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}
