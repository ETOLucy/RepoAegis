import React, { useState } from "react";
import { api } from "./api.js";

export default function ChatView() {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  async function ask(event) {
    event.preventDefault();
    if (!query.trim() || loading) return;
    setLoading(true);
    setError("");
    try {
      const data = await api("/chat", { method: "POST", body: JSON.stringify({ query: query.trim(), top_k: 5 }) });
      setResult(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="chat">
      <h2>代码问答（检索增强生成 RAG）</h2>
      <p className="hint">对 RepoAegis 代码库提问，回答带文件路径与行号引用。</p>
      <form className="chat-form" onSubmit={ask}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="例如：ToolGateway 如何做工具调用授权？"
          disabled={loading}
        />
        <button type="submit" disabled={loading || !query.trim()}>
          {loading ? "检索中…" : "提问"}
        </button>
      </form>
      {error && <div className="error">{error}</div>}
      {result && (
        <div className="chat-result">
          <div className="answer">{result.answer}</div>
          <div className="hits">
            <h3>引用（{result.hits.length}）</h3>
            {result.hits.map((hit, i) => (
              <div className="hit" key={i}>
                <div className="hit-path">
                  {hit.path}:{hit.line_start}-{hit.line_end}{" "}
                  {hit.symbol && <span className="symbol">{hit.symbol}</span>}
                </div>
                <pre>{hit.content.slice(0, 300)}</pre>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}