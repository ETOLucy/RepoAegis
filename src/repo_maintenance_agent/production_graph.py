from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repo_maintenance_agent.agents.nodes import AgentRuntime, build_agent_nodes
from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.domain.ports import ArtifactStore, ToolAdapter
from repo_maintenance_agent.graph.builder import build_graph
from repo_maintenance_agent.models.openai_gateway import OpenAIModelGateway
from repo_maintenance_agent.policies.permissions import PermissionPolicy
from repo_maintenance_agent.sandbox.docker import DockerSandbox
from repo_maintenance_agent.sandbox.profiles import EnvironmentProfiler
from repo_maintenance_agent.sandbox.verifier import SandboxVerifier
from repo_maintenance_agent.search.adapters.local import LocalLexicalSearch
from repo_maintenance_agent.tools.agent_actions import (
    PatchArtifactAdapter,
    SearchAdapter,
    VerificationAdapter,
    WorkspaceReadAdapter,
)
from repo_maintenance_agent.tools.gateway import OperationLog, ToolGateway
from repo_maintenance_agent.tools.git import GitToolAdapter
from repo_maintenance_agent.tools.github import GitHubCliAdapter, LocalDraftRecordAdapter
from repo_maintenance_agent.tools.patch import GitPatchApplier
from repo_maintenance_agent.tools.process import ProcessRunner


@dataclass(frozen=True, slots=True)
class ProductionGraphFactory:
    settings: Settings
    artifacts: ArtifactStore
    operations: OperationLog

    def __call__(self, workspace: Path) -> Any:
        model = OpenAIModelGateway.from_settings(self.settings)
        gateway = ToolGateway(
            policy=PermissionPolicy(),
            adapters=self.build_adapters(workspace),
            operation_log=self.operations,
            workspace_root=workspace,
        )
        return build_graph(
            build_agent_nodes(
                AgentRuntime(
                    model=model,
                    artifacts=self.artifacts,
                    gateway=gateway,
                )
            )
        )

    def build_adapters(self, workspace: Path) -> dict[str, ToolAdapter]:
        patch_runner = ProcessRunner(allowed_executables={"git"})
        sandbox = DockerSandbox(
            ProcessRunner(allowed_executables={"docker"}),
            seccomp_profile=self.settings.sandbox_seccomp_profile,
        )
        verifier = SandboxVerifier(
            workspace=workspace,
            profiler=EnvironmentProfiler(),
            sandbox=sandbox,
            image_digests=self.settings.sandbox_image_digests,
        )
        git_adapter = GitToolAdapter(patch_runner)
        draft_adapter: ToolAdapter = (
            GitHubCliAdapter(
                ProcessRunner(allowed_executables={"gh"}),
                token=self.settings.github_token,
            )
            if self.settings.github_token is not None
            else LocalDraftRecordAdapter(self.artifacts)
        )
        return {
            "search_code": SearchAdapter(LocalLexicalSearch(workspace)),
            "apply_patch": PatchArtifactAdapter(
                artifacts=self.artifacts,
                applier=GitPatchApplier(patch_runner),
            ),
            "run_verification": VerificationAdapter(verifier),
            "read_files": WorkspaceReadAdapter(),
            "git_diff": git_adapter,
            "git_commit": git_adapter,
            "git_push": git_adapter,
            "create_draft_pr": draft_adapter,
        }
