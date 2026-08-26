import React, { useEffect, useState } from "react";
import { api } from "./api.js";

function EvalCard({ run }) {
  const created = run.created_at ? new Date(run.created_at).toLocaleString("zh-CN") : "-";
  const completed = run.completed_at ? new Date(run.completed_at).toLocaleString("zh-CN") : "-";
  const passed = run.results ? run.results.filter(r => r.passed).length : 0;
  const total = run.results ? run.results.length : 0;
  const rate = total > 0 ? ((passed / total) * 100).toFixed(1) : "0";
  return (
    <div className="eval-card">
      <div className="eval-card-header">
        <span className="eval-id" title={run.run_id}>{run.run_id.slice(0, 8)}...</span>
        <span className="eval-label">{run.candidate_label || "-"}</span>
        <span className={"badge " + (run.status || "unknown")}>{run.status}</span>
        {run.gate_decision && <span className={"badge " + run.gate_decision.decision}>{run.gate_decision.decision}</span>}
      </div>
      <div className="eval-stats">
        <div className="eval-stat">
          <div className="eval-stat-value" style={{ color: "#49c99b" }}>{rate}%</div>
          <div className="eval-stat-label">通过率</div>
        </div>
        <div className="eval-stat">
          <div className="eval-stat-value">{passed}/{total}</div>
          <div className="eval-stat-label">通过/总数</div>
        </div>
        <div className="eval-stat">
          <div className="eval-stat-value">{run.suite?.case_ids?.length || 0}</div>
          <div className="eval-stat-label">评测用例</div>
        </div>
      </div>
      {run.aggregate && (
        <div className="eval-progress">
          <div className="eval-bar">
            <div className="eval-bar-fill" style={{ width: rate + "%" }} />
          </div>
        </div>
      )}
      <div className="eval-meta">
        <span>创建: {created}</span>
        {run.completed_at && <span>完成: {completed}</span>}
        {run.baseline_run_id && <span>基线: {run.baseline_run_id.slice(0, 8)}...</span>}
      </div>
      {run.results && run.results.length > 0 && (
        <details className="task-details">
          <summary>结果详情 ({run.results.length})</summary>
          <div className="task-evidence">
            {run.results.slice(0, 10).map((r, i) => (
              <div key={i} className="evidence-item">
                <span className="evidence-source" style={{ color: r.passed ? "#49c99b" : "#ff7b72" }}>
                  {r.passed ? "PASS" : "FAIL"}
                </span>
                <span className="evidence-locator">{r.case_id}</span>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

export default function EvalView() {
  const [runs, setRuns] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api("/evaluations/runs?limit=20")
      .then((data) => setRuns(data.items || []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <h2>评测看板</h2>
      {error && <div className="error">{error}</div>}
      {loading && <div className="loading">加载中...</div>}
      {runs.length === 0 && !loading && <div className="empty-state">暂无评测运行</div>}
      {runs.map((r) => <EvalCard key={r.run_id} run={r} />)}
    </div>
  );
}