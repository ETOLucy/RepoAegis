<p align="center">
  <img src="assets/repo-aegis-mark-256.png" width="96" alt="RepoAegis">
</p>

<h1 align="center">RepoAegis</h1>

<p align="center">受策略约束的 issue → PR 编码 agent。</p>

给它一个 GitHub issue，它在沙箱里定位、修改、验证，然后开一个 PR。每一步写操作都要先经过人类批准，批准绑定到具体的工具调用和参数，事后无法调包。

## 仓库结构

```
src/repoaegis/agent/    能力线 1：ReAct 循环、工具、上下文压缩
src/repoaegis/server/   能力线 2：状态机、审批、持久化、事件、HTTP API
src/repoaegis/eval/     评测 harness，项目的每个断言都从这里复现
web/                    能力线 3：Vue 3 控制台
docs/industry-survey.md 设计依据：业界系统与论文调研
```

## 开发

```bash
uv sync                 # Python 依赖
uv run pytest           # 测试
uv run ruff check .     # lint
uv run mypy             # 类型检查
uv run uvicorn repoaegis.server.api:app --reload   # 后端，http://127.0.0.1:8000

cd web && npm install   # 前端依赖
npm run dev             # 控制台，http://127.0.0.1:5173，/api 代理到后端
npm run gen:api         # 后端 schema 变了之后重新生成 TS 类型
```

后端 schema 改动后同步类型：

```bash
uv run python -m repoaegis.server.openapi > web/openapi.json && (cd web && npm run gen:api)
```

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。
