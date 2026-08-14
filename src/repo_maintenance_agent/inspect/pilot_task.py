"""Inspect task for evaluating RepoAegis on SWE-bench Verified.

In-repo copy of ``.portfolio-eval/inspect_pilot/pilot_task.py`` (2026-08-11
pilot), integrated so the official Inspect grading track is runnable from the
RepoAegis repository itself.

Dataset is loaded from the local JSONL prepared by prepare_dataset.py (no
network). The solver is repoaegis_solver: in replay mode it applies a
previously generated official-format prediction; in generate mode it calls the
real RepoAegis pipeline. The scorer is Inspect's official swe_bench_scorer,
which runs the real SWE-bench tests inside the Docker sandbox.
"""
from __future__ import annotations

import platform
import re
from pathlib import Path
from textwrap import dedent
from typing import Any

from inspect_ai import Task, task
from inspect_ai.dataset import FieldSpec, Sample, json_dataset
from inspect_ai.scorer import Scorer
from inspect_ai.util import SandboxEnvironmentSpec
from inspect_evals.swe_bench.scorers import swe_bench_scorer

from .repoaegis_solver import repoaegis_solver

_INSTANCE_ID_RE = re.compile(r"^(?P<org>.+?)__(?P<repo>.+)-(?P<issue>\d+)$")

# DockerHub image template used by the official swebench 4.x harness
# (swebench/sweb.eval.{arch}.{org}_1776_{repo}-{issue}:latest).
DOCKERHUB_IMAGE_TEMPLATE = "swebench/sweb.eval.{arch}.{org}_1776_{repo}-{issue}:latest"


@task
def repoaegis_verified(
    dataset: str = "data/verified.jsonl",
    predictions_path: str | None = None,
    scorer: Scorer | list[Scorer] | None = None,
    sandbox_type: str = "docker",
    image_name_template: str = DOCKERHUB_IMAGE_TEMPLATE,
    arch: str | None = None,
    allow_internet: bool = False,
    compose_dir: str | None = None,
    **kwargs: Any,
) -> Task:
    """Evaluate RepoAegis on a local SWE-bench Verified subset.

    Args:
        dataset: Local JSONL prepared by prepare_dataset.py.
        predictions_path: Optional official-format prediction JSONL to replay
            (free pipeline validation). Omit to call the model.
        scorer: Override scorer (defaults to official swe_bench_scorer).
        sandbox_type: Sandbox provider (docker required for scoring).
        image_name_template: Image template with {org}/{repo}/{issue}/{arch}/{id}.
        arch: Architecture for the image. Auto-detected when None.
        allow_internet: Whether sandboxes may reach the internet.
        compose_dir: Directory for generated compose files (defaults to a
            temp directory under the pilot package).
        **kwargs: Forwarded to Task.
    """
    samples = json_dataset(
        dataset,
        FieldSpec(
            input="problem_statement",
            id="instance_id",
            metadata=[
                "base_commit",
                "patch",
                "PASS_TO_PASS",
                "FAIL_TO_PASS",
                "test_patch",
                "version",
                "repo",
                "environment_setup_commit",
                "hints_text",
                "created_at",
            ],
        ),
    )

    if arch is None and "{arch}" in image_name_template:
        arch = "arm64" if platform.machine() in {"aarch64", "arm64"} else "x86_64"

    def resolve_image_name(instance_id: str) -> str:
        match = _INSTANCE_ID_RE.match(instance_id)
        parts = match.groupdict() if match else {}
        return image_name_template.format(id=instance_id, arch=arch, **parts)

    compose_root = Path(compose_dir) if compose_dir else Path(__file__).parent / ".compose"

    def sandbox_config(sandbox_type: str, sample: Sample) -> SandboxEnvironmentSpec:
        assert sample.metadata is not None
        image_name = sample.metadata.get("image_name")
        if not isinstance(image_name, str):
            image_name = resolve_image_name(str(sample.id))
        network_mode = "" if allow_internet else "network_mode: none"
        config_file = compose_root / f"{sample.id}-compose.yaml"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(
            dedent(
                f"""\
                services:
                  default:
                    image: {image_name}
                    command: sleep infinity
                    working_dir: /testbed
                    {network_mode}
                """
            ),
            encoding="utf-8",
        )
        return SandboxEnvironmentSpec(type=sandbox_type, config=str(config_file))

    for sample in samples:
        sample.metadata = sample.metadata or {}
        sample.metadata["image_name"] = resolve_image_name(str(sample.id))
        sample.metadata["allow_internet"] = allow_internet
        sample.sandbox = sandbox_config(sandbox_type, sample)

    return Task(
        dataset=samples,
        solver=repoaegis_solver(predictions_path=predictions_path),
        scorer=scorer or swe_bench_scorer(),
        **kwargs,
    )