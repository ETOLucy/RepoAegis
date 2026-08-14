import React, { useState } from "react";
import ChatView from "./ChatView.jsx";
import TasksView from "./TasksView.jsx";
import EvalView from "./EvalView.jsx";

const VIEWS = [
  { key: "chat", label: "代码问答 (RAG)", component: ChatView },
  { key: "tasks", label: "任务控制台", component: TasksView },
  { key: "eval", label: "评测看板", component: EvalView },
];

export default function App() {
  const [active, setActive] = useState("chat");
  const Active = VIEWS.find((v) => v.key === active).component;
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">RepoAegis <span>工作台</span></div>
        <nav>
          {VIEWS.map((v) => (
            <button key={v.key} className={active === v.key ? "active" : ""} onClick={() => setActive(v.key)}>
              {v.label}
            </button>
          ))}
        </nav>
      </header>
      <main>
        <Active />
      </main>
    </div>
  );
}