import React, { useEffect, useState } from "react";
import { api } from "./api.js";

export default function EvalView() {
  const [runs, setRuns] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/evaluations/runs?limit=20")
      .then((data) => setRuns(data.items || []))
      .catch((e) => setError(e.message));
  }, []);

  return (
    <div>
      <h2>评测看板</h2>
      {error && <div className="error">{error}</div>}
      <table className="table">
        <thead>
          <tr><th>运行</th><th>候选</th><th>状态</th><th>门禁</th><th>创建时间</th></tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.run_id}>
              <td title={r.run_id}>{r.run_id.slice(0, 8)}…</td>
              <td>{r.candidate_label || "-"}</td>
              <td><span className="badge">{r.status}</span></td>
              <td>{r.gate ? r.gate.decision : "-"}</td>
              <td>{r.created_at ? r.created_at.slice(0, 19) : "-"}</td>
            </tr>
          ))}
          {runs.length === 0 && <tr><td colSpan="5" className="empty">暂无评测运行</td></tr>}
        </tbody>
      </table>
    </div>
  );
}