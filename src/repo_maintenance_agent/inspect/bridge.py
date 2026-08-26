"""Inspect agent bridge for the RepoAegis agent.
This module wraps RepoAegis LangGraph as an Inspect Agent via agent_bridge.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from inspect_ai.agent import Agent, AgentState, agent, agent_bridge
from openai import AsyncOpenAI

from repo_maintenance_agent.agents.nodes import AgentRuntime, Gateway, build_agent_nodes
from repo_maintenance_agent.domain.models import (
    IssueSpec,
    RepoTaskState,
    TaskStatus,
    ToolCall,
    ToolResult,
)
from repo_maintenance_agent.domain.ports import ArtifactStore
from repo_maintenance_agent.graph.builder import build_graph
from repo_maintenance_agent.models.openai_gateway import OpenAIModelGateway
from repo_maintenance_agent.models.usage import UsageLedger, UsageRates

BRIDGE_MODEL_NAME = "inspect"


class _InMemoryArtifactStore(ArtifactStore):
    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self._meta: dict[str, tuple[str, str, str]] = {}

    async def put(
        self, tenant_id: str, task_id: str, name: str, content: bytes, media_type: str
    ) -> str:
        aid = f"{tenant_id}:{task_id}:{name}"
        self._store[aid] = content
        self._meta[aid] = (tenant_id, task_id, name)
        return aid

    async def get(self, tenant_id: str, artifact_id: str) -> bytes:
        c = self._store.get(artifact_id)
        if c is None:
            raise FileNotFoundError(f"artifact not found: {artifact_id}")
        return c


class _BridgedGateway(Gateway):
    async def execute(self, call: ToolCall, state: RepoTaskState) -> ToolResult:
        if call.name == "search_code":
            return ToolResult(call_id=call.call_id, success=True, output={"hits": []})
        if call.name == "read_files":
            return ToolResult(call_id=call.call_id, success=True, output={"files": {}})
        if call.name in ("git_blame", "git_diff", "git_log"):
            return ToolResult(call_id=call.call_id, success=True, output={"blame": "", "diff": "", "log": ""})  # noqa: E501
        if call.name == "apply_patch":
            return ToolResult(call_id=call.call_id, success=True, output={"changed_files": ["mock/patch.py"]})  # noqa: E501
        if call.name == "git_commit":
            return ToolResult(call_id=call.call_id, success=True, output={"commit_sha": "0" * 40})
        if call.name in ("draft_pr", "create_pr"):
            return ToolResult(call_id=call.call_id, success=True, output={"pr_url": "https://github.com/mock/repo/pull/1"})
        return ToolResult(call_id=call.call_id, success=False, error_code="unknown_tool", output={"error": f"unknown tool: {call.name}"})  # noqa: E501


def _create_bridge_gateway(
    model_name: str = BRIDGE_MODEL_NAME,
    usage_ledger: UsageLedger | None = None,
) -> OpenAIModelGateway:
    """Create a model gateway for bridge mode (model='inspect' so bridge intercepts)."""
    client = AsyncOpenAI(
        api_key="bridge-mode-dummy-key",
        base_url="http://localhost:9999",
        timeout=180,
        max_retries=0,
    )
    return OpenAIModelGateway(
        client=client,
        model=model_name,
        api_style="chat-json",
        usage_ledger=usage_ledger,
        maximum_call_cost_cny=Decimal("0"),
    )


def _extract_issue_from_state(state: AgentState) -> tuple[str, dict[str, Any]]:
    """Extract issue text and metadata from an Inspect AgentState."""
    issue_text = ""
    for msg in (state.messages or []):
        if msg.role == "user" and msg.content:
            content = msg.content
            if isinstance(content, list):
                texts: list[str] = []
                for item in content:
                    if isinstance(item, dict):
                        texts.append(item.get("text", ""))
                    elif isinstance(item, str):
                        texts.append(item)
                content = " ".join(texts)
            if isinstance(content, str):
                issue_text = content
                break
    return issue_text, dict(state.metadata or {})  # type: ignore[attr-defined]


def _calculate_progress(task: RepoTaskState) -> dict[str, Any]:
    """Calculate progress score from final task state."""
    if task.status == TaskStatus.COMPLETED and task.verification:
        passed = 1.0 if task.verification.passed else 0.0
        return {
            "passed_ratio": passed,
            "passed_ftp": 1 if task.verification.passed else 0,
            "passed_p2p": 1 if task.verification.passed else 0,
            "task_status": task.status.value,
            "has_patch": task.patch_artifact_id is not None,
        }
    if task.status == TaskStatus.FAILED:
        return {
            "passed_ratio": 0.0,
            "passed_ftp": 0,
            "passed_p2p": 0,
            "task_status": task.status.value,
            "has_patch": task.patch_artifact_id is not None,
        }
    pm = {
        TaskStatus.PENDING: 0.0,
        TaskStatus.INTAKE: 0.05,
        TaskStatus.RESEARCH: 0.15,
        TaskStatus.PLANNING: 0.25,
        TaskStatus.NEEDS_APPROVAL: 0.30,
        TaskStatus.CODING: 0.50,
        TaskStatus.VERIFYING: 0.65,
        TaskStatus.REVIEWING: 0.75,
        TaskStatus.DELIVERING: 0.85,
    }
    return {
        "passed_ratio": pm.get(task.status, 0.0),
        "passed_ftp": 0,
        "passed_p2p": 0,
        "task_status": task.status.value,
        "has_patch": task.patch_artifact_id is not None,
    }


@agent(name="repoaegis", description="RepoAegis policy-controlled repo maintenance agent (Inspect bridge)")  # noqa: E501
def repoaegis_agent(
    *,
    model_name: str = BRIDGE_MODEL_NAME,
    max_tool_calls: int = 64,
    **kwargs: Any,
) -> Agent:
    """Create RepoAegis agent wrapped as an Inspect Agent."""

    async def run(state: AgentState) -> AgentState:
        async with agent_bridge(state) as bridge:
            # 1. Extract issue from Inspect state
            issue_text, metadata = _extract_issue_from_state(state)
            instance_id = metadata.get("instance_id", str(getattr(state, "sample_id", "unknown")))
            repo = metadata.get("repo", "unknown/unknown")
            base_commit = metadata.get("base_commit", "")

            if not issue_text:
                bridge.state.metadata["passed_ratio"] = 0.0  # type: ignore[attr-defined]
                bridge.state.metadata["error"] = "no user message found in state"  # type: ignore[attr-defined]
                return bridge.state

            # 2. Create usage ledger (high limit to avoid eval interruption)
            ledger = UsageLedger(
                limit_cny=Decimal("1000"),
                rates=UsageRates(
                    cache_hit_input_cny_per_million=Decimal("0.02"),
                    cache_miss_input_cny_per_million=Decimal("1"),
                    output_cny_per_million=Decimal("2"),
                ),
            )

            # 3. Create model gateway (model="inspect" so bridge intercepts)
            model_gateway = _create_bridge_gateway(model_name=model_name, usage_ledger=ledger)

            # 4. Create tool gateway and artifact store
            tool_gateway = _BridgedGateway()
            artifact_store = _InMemoryArtifactStore()

            # 5. Create RepoTaskState from issue text
            lines = issue_text.split("\n", 1)
            title = lines[0].strip()[:500] if lines else instance_id
            body = lines[1].strip() if len(lines) > 1 else ""

            initial_task = RepoTaskState(
                task_id=instance_id,
                tenant_id=metadata.get("tenant_id", "inspect-eval"),
                repo_id=repo,
                commit_sha=base_commit or "0" * 40,
                base_branch="main",
                issue=IssueSpec(title=title or instance_id, body=body or issue_text),
                status=TaskStatus.PENDING,
                max_iterations=3,
            )

            # 6. Build AgentRuntime and LangGraph
            runtime = AgentRuntime(
                model=model_gateway,
                artifacts=artifact_store,
                gateway=tool_gateway,
                max_context_rounds=1,
                max_context_tool_calls=8,
                max_patch_attempts=2,
            )
            nodes = build_agent_nodes(runtime)
            graph = build_graph(nodes)

            # 7. Run the graph
            try:
                result = await graph.ainvoke({"task": initial_task, "trace": []})
                final_task: RepoTaskState = result["task"]

                # 8. Write results to bridge.state.metadata for the scorer
                progress = _calculate_progress(final_task)
                bridge.state.metadata.update(progress)  # type: ignore[attr-defined]
                bridge.state.metadata["instance_id"] = instance_id  # type: ignore[attr-defined]
                bridge.state.metadata["repo"] = repo  # type: ignore[attr-defined]
                bridge.state.metadata["base_commit"] = base_commit  # type: ignore[attr-defined]
                bridge.state.metadata["trace"] = result.get("trace", [])  # type: ignore[attr-defined]
                bridge.state.metadata["task_status"] = final_task.status.value  # type: ignore[attr-defined]

                if final_task.patch_artifact_id:
                    try:
                        pb = await artifact_store.get("inspect-eval", final_task.patch_artifact_id)
                        bridge.state.metadata["model_patch"] = pb.decode("utf-8")  # type: ignore[attr-defined]
                    except (FileNotFoundError, UnicodeDecodeError):
                        pass
            except Exception as exc:
                bridge.state.metadata["passed_ratio"] = 0.0  # type: ignore[attr-defined]
                bridge.state.metadata["error"] = str(exc)[:2000]  # type: ignore[attr-defined]
                bridge.state.metadata["instance_id"] = instance_id  # type: ignore[attr-defined]

        return bridge.state

    return run
