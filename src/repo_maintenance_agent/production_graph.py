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
from repo_maintenance_agent.sandbox.remote import RemoteSandbox
from repo_maintenance_agent.sandbox.verifier import SandboxVerifier
from repo_maintenance_agent.search.adapters.opensearch import (
    OpenSearchClientImpl,
    OpenSearchHybridAdapter,
)
from repo_maintenance_agent.search.adapters.ripgrep import default_lexical_search
from repo_maintenance_agent.search.embeddings import OpenAIEmbeddingClient
from repo_maintenance_agent.search.history import GitHistorySearch
from repo_maintenance_agent.search.production import WorkspaceIndex
from repo_maintenance_agent.search.reranker import LLMReranker
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

    def _build_index(
        self, workspace: Path, *, model: OpenAIModelGateway | None = None
    ) -> WorkspaceIndex:
        if self.settings.openai_embedding_api_key is None and self.settings.openai_api_key is None:
            raise RuntimeError(
                "OPENAI_API_KEY or OPENAI_EMBEDDING_API_KEY is required "
                "to build the hybrid index (Vector channel). Set at least one key."
            )
        embeddings = OpenAIEmbeddingClient.from_settings(self.settings)
        # ── OpenSearch 接入 ──
        opensearch: OpenSearchHybridAdapter | None = None
        if self.settings.opensearch_hosts:
            client = OpenSearchClientImpl(
                list(self.settings.opensearch_hosts),
                port=self.settings.opensearch_port,
                http_auth=(
                    (self.settings.opensearch_user, self.settings.opensearch_password.get_secret_value())  # noqa: E501
                    if self.settings.opensearch_user and self.settings.opensearch_password
                    else None
                ),
                use_ssl=self.settings.opensearch_use_ssl,
                verify_certs=self.settings.opensearch_verify_certs,
            )
            if client.ping():
                opensearch = OpenSearchHybridAdapter(
                    client,
                    index_alias=self.settings.opensearch_index_alias,
                )
        return WorkspaceIndex(
            workspace,
            embeddings=embeddings,
            lexical=default_lexical_search(workspace),
            history=GitHistorySearch(workspace, ProcessRunner(allowed_executables={"git"})),
            opensearch=opensearch,
            reranker=LLMReranker(model=model, candidate_pool=20, final_k=10),
        )

    def build_adapters(self, workspace: Path) -> dict[str, ToolAdapter]:
        model = OpenAIModelGateway.from_settings(self.settings)
        patch_runner = ProcessRunner(allowed_executables={"git"})
        sandbox = (
            RemoteSandbox(
                base_url=self.settings.sandbox_runner_url,
                token=self.settings.sandbox_runner_token,
                workspace_root=Path(self.settings.workspace_root),
            )
            if self.settings.sandbox_runner_url is not None
            and self.settings.sandbox_runner_token is not None
            else DockerSandbox(
                ProcessRunner(allowed_executables={"docker"}),
                seccomp_profile=self.settings.sandbox_seccomp_profile,
            )
        )
        verifier = SandboxVerifier(
            workspace=workspace,
            profiler=EnvironmentProfiler(),
            sandbox=sandbox,
            image_digests=self.settings.sandbox_image_digests,
            summary_limit=self.settings.verification_summary_limit,
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
            "search_code": SearchAdapter(self._build_index(workspace, model=model)),
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
