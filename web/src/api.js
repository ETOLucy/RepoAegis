const TOKEN = "__REPO_AGENT_TOKEN__";
const BASE = "/v1";

export async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (TOKEN && !TOKEN.startsWith("__REPO")) headers.Authorization = `Bearer ${TOKEN}`;
  const response = await fetch(BASE + path, { ...options, headers });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`${response.status}: ${text.slice(0, 200)}`);
  }
  return response.json();
}

export function apiUrl(path) {
  return BASE + path;
}