import React, { useEffect, useState } from "react";
import { api } from "./api.js";

export default function TasksView() {
  const [tasks, setTasks] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/tasks?limit=20")
      .then((data) => setTasks(data.items || []))
      .catch((e) => setError(e.message));
  }, []);

  return (
    <div>
      <h2>任务控制台</h2>
      {error && <div className="error">{error}</div>}
      <table className="table">
        <thead>
          <tr><th>任务</th><th>仓库</th><th>状态</th><th>风险</th><th>迭代</th></tr>
        </thead>
        <tbody>
          {tasks.map((t) => (
            <tr key={t.task_id}>
              <td title={t.task_id}>{t.task_id.slice(0, 8)}…</td>
              <td>{t.repo_id}</td>
              <td><span className={`badge ${t.status}`}>{t.status}</span></td>
              <td>{t.risk}</td>
              <td>{t.iteration}</td>
            </tr>
          ))}
          {tasks.length === 0 && <tr><td colSpan="5" className="empty">暂无任务</td></tr>}
        </tbody>
      </table>
    </div>
  );
}