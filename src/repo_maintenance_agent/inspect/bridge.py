# ruff: noqa: RUF002, RUF003
"""Inspect agent bridge for the RepoAegis agent.

本模块把 RepoAegis 的 LangGraph agent 以 Inspect
:class:`~inspect_ai.agent.Agent` 的形式暴露出来，供 Inspect ``Task`` 的
``solver`` 使用（经 ``inspect_ai.agent.as_solver`` 转换），从而让 RepoAegis
跑在 UK AISI Inspect 评测框架里，面向官方 SWE-bench 评测。

桥接原理（inspect_ai 0.3.255 的 ``agent_bridge``）：

* 在 ``async with agent_bridge(state) as bridge`` 期间，Inspect 会 patch
  OpenAI / Anthropic 兼容的 LLM client 库；
* 只要 RepoAegis 内部 LLM 客户端发起请求时把 ``model_name`` 注入为
  ``"inspect"``（或 ``"inspect/<model>"``），该请求就会被转接到 Inspect 的
  模型 API，消息流、工具调用、token 用量都会自动回写到 ``bridge.state``；
* ``agent_bridge`` 上下文退出后，``bridge.state`` 即为本次评测的最终对话
  状态，返回它即可。

当前为**结构正确的骨架**：工厂函数、``@agent`` 注册、桥接上下文与返回
``bridge.state`` 的 Agent 协议都已就位，但 RepoAegis 内部真实调用链尚未
接线（见 TODO）。真正联调前调用会抛出 ``NotImplementedError``。
"""

from __future__ import annotations

from typing import Any

from inspect_ai.agent import Agent, AgentState, agent, agent_bridge

#: RepoAegis 内部 LLM 客户端在桥接模式下使用的模型名；bridge 会拦截该名字。
BRIDGE_MODEL_NAME = "inspect"


@agent(
    name="repoaegis",
    description="RepoAegis policy-controlled repo maintenance agent (Inspect bridge)",
)
def repoaegis_agent(
    *,
    model_name: str = BRIDGE_MODEL_NAME,
    max_tool_calls: int = 64,
    **kwargs: Any,
) -> Agent:
    """创建被包装为 Inspect Agent 的 RepoAegis agent。

    在 task 文件里这样使用::

        from inspect_ai import Task
        from inspect_ai.agent import as_solver
        from repo_maintenance_agent.inspect.bridge import repoaegis_agent
        from repo_maintenance_agent.inspect.dataset import load_repoaegis_holdout
        from repo_maintenance_agent.inspect.scorers import repoaegis_swe_progress_scorer

        @task
        def repoaegis_swe() -> Task:
            return Task(
                dataset=load_repoaegis_holdout("data/holdout.jsonl"),
                solver=as_solver(repoaegis_agent()),
                scorer=repoaegis_swe_progress_scorer(),
            )

    Args:
        model_name: RepoAegis 内部 LLM 客户端应注入的模型名；默认 ``"inspect"``
            使其请求进入 bridge。若想指定具体模型可传 ``"inspect/<model>"``。
        max_tool_calls: 单样本最大工具调用次数上限（超限即结束样本进入评分）。
        **kwargs: 透传给 RepoAegis 运行时的扩展参数（预留）。

    Returns:
        符合 :class:`~inspect_ai.agent.Agent` 协议的可调用对象。

    .. todo::
        1. **LLM 客户端注入**：RepoAegis 内部 LLM 客户端
           （``models/openai_gateway.py`` 一带）需支持注入
           ``model_name="inspect"``，使每次请求经 bridge 转发到 Inspect；
           同时把 ``max_tool_calls`` 等上限接入客户端参数。
        2. **工具网关对接**：RepoAegis 的 tool gateway（``tools/`` 与
           ``domain.ports``）需把工具定义与执行结果映射到 Inspect 的
           tool 协议。注意：inspect_ai 0.3.255 的 ``AgentBridge`` 通过客户端
           声明（client-declared tools / ``bridge_generate``）转发工具，没有
           名为 ``bridge.tools`` 的属性；工具接入点以
           ``inspect_ai.agent.AgentBridge`` 的实际方法为准。
        3. **消息转换**：把 Inspect 的 ``ChatMessage`` 序列与 RepoAegis 的
           ``ToolCall`` / ``ToolResult``（``domain.models``）互相转换，保证
           评分阶段的 ``state.metadata`` 里能写入 ``passed_ratio`` /
           ``passed_ftp`` / ``passed_p2p`` 供 progress scorer 消费。
        4. **评测产物落盘**：让 RepoAegis 产出的 model patch 按 SWE-bench
           predictions JSONL 格式写出，供官方 harness 与 AegisEvo 门控复用。
    """

    async def run(state: AgentState) -> AgentState:
        """在 Inspect bridge 上下文内驱动 RepoAegis agent 运行一次。"""
        async with agent_bridge(state) as bridge:
            # TODO(integration): 在此处驱动 RepoAegis 的 LangGraph 图。
            # 1) 用注入 model_name 的 LLM 客户端构建 RepoAegis runtime；
            # 2) 把任务输入（bridge.state 中的 user 消息）传给 RepoAegis；
            # 3) 把 RepoAegis 工具调用经 bridge 转发，结果回灌；
            # 4) 结束后把测试结果写入 state.metadata（passed_ratio 等）。
            raise NotImplementedError(
                "repoaegis_agent is a scaffold: wire the RepoAegis runtime to "
                "the Inspect agent bridge before running an eval (see module "
                "docstring TODO list)."
            )
        return bridge.state

    return run
