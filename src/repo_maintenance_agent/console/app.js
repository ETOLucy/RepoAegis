(() => {
  "use strict";

  let apiToken = "";
  let runs = [];
  let tasks = [];
  let selectedRunId = null;
  let activeView = "runs";

  const byId = (id) => document.getElementById(id);
  const identityDialog = byId("identity-dialog");
  const tokenInput = byId("api-token");
  const runBody = byId("run-table-body");
  const taskBody = byId("task-table-body");

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function apiHeaders() {
    return {
      Accept: "application/json",
      Authorization: `Bearer ${apiToken}`,
    };
  }

  async function request(path, options = {}) {
    if (!apiToken) {
      throw new Error("Identity is required");
    }
    const response = await fetch(path, {
      ...options,
      headers: {
        ...apiHeaders(),
        ...(options.headers || {}),
      },
    });
    if (response.status === 401) {
      setConnected(false);
      openIdentity();
      throw new Error("Identity was rejected");
    }
    if (!response.ok) {
      throw new Error(`Request failed with HTTP ${response.status}`);
    }
    return response;
  }

  async function synchronize() {
    if (!apiToken) {
      openIdentity();
      return;
    }
    setBusy(true);
    try {
      const [runResponse, taskResponse] = await Promise.all([
        request("/v1/evaluations/runs?limit=100"),
        request("/v1/tasks?limit=100"),
      ]);
      runs = (await runResponse.json()).items;
      tasks = (await taskResponse.json()).items;
      setConnected(true);
      renderRuns();
      renderTasks();
      const selected = runs.find((run) => run.run_id === selectedRunId) || runs[0];
      selectRun(selected || null);
      byId("last-updated").textContent = `Updated ${formatTime(new Date().toISOString())}`;
    } catch (error) {
      showNotice(error instanceof Error ? error.message : "Synchronization failed");
    } finally {
      setBusy(false);
    }
  }

  function renderRuns() {
    const gateFilter = byId("gate-filter").value;
    const visible = runs.filter((run) => {
      const passed = run.gate_decision && run.gate_decision.passed;
      return gateFilter === "all"
        || (gateFilter === "pass" && passed)
        || (gateFilter === "fail" && !passed);
    });
    const rows = visible.map((run) => {
      const row = element("tr");
      row.tabIndex = 0;
      row.dataset.runId = run.run_id;
      if (run.run_id === selectedRunId) row.classList.add("selected");
      row.addEventListener("click", () => selectRun(run));
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") selectRun(run);
      });

      const candidate = element("td");
      candidate.append(
        element("span", "cell-primary", run.candidate_label),
        element("span", "cell-secondary", run.run_id.slice(0, 12)),
      );
      const status = element("td");
      status.append(statusBadge(run));
      const resolution = element(
        "td",
        "mono",
        formatPercent(run.aggregate && run.aggregate.resolution_rate),
      );
      const safety = element(
        "td",
        "mono",
        run.aggregate
          ? formatPercent(1 - run.aggregate.unauthorized_tool_call_rate)
          : "--",
      );
      const duration = element("td", "mono", durationLabel(run));
      row.append(candidate, status, resolution, safety, duration);
      return row;
    });
    runBody.replaceChildren(...rows);
    byId("runs-empty").hidden = visible.length !== 0;
    byId("run-count").textContent = `${visible.length} ${visible.length === 1 ? "run" : "runs"}`;
  }

  function renderTasks() {
    const rows = tasks.map((task) => {
      const row = element("tr");
      const repository = element("td");
      repository.append(
        element("span", "cell-primary", task.repo_id),
        element("span", "cell-secondary", task.commit_sha.slice(0, 12)),
      );
      row.append(
        repository,
        element("td", "mono", task.task_id.slice(0, 12)),
        element("td", "", task.status),
        element("td", "mono", String(task.iteration)),
        element("td", "", formatTime(task.updated_at)),
      );
      return row;
    });
    taskBody.replaceChildren(...rows);
    byId("tasks-empty").hidden = rows.length !== 0;
    byId("task-count").textContent = `${rows.length} ${rows.length === 1 ? "task" : "tasks"}`;
  }

  function selectRun(run) {
    selectedRunId = run ? run.run_id : null;
    renderRuns();
    byId("detail-empty").hidden = Boolean(run);
    byId("detail-content").hidden = !run;
    if (!run) return;

    byId("detail-title").textContent = run.candidate_label;
    byId("detail-run-id").textContent = run.run_id;
    const detailStatus = byId("detail-status");
    detailStatus.className = statusBadge(run).className;
    detailStatus.textContent = gateLabel(run);
    renderProvenance(run);
    renderMetrics(run);
    renderGates(run);
    renderCases(run);
    const failed = run.results.filter((result) => !casePassed(result));
    byId("replay-button").disabled = failed.length === 0;
    byId("replay-button").textContent = failed.length
      ? `Replay failed (${failed.length})`
      : "No failed cases";
  }

  function renderProvenance(run) {
    const values = [
      ["Suite", `${run.suite.suite_id}@${run.suite.version}`],
      ["Provider", run.provenance.provider],
      ["Model", run.provenance.model],
      ["Prompt", run.provenance.prompt_version],
      ["Policy", run.provenance.policy_version],
      ["Seed", String(run.provenance.seed)],
    ];
    const nodes = values.map(([label, value]) => {
      const item = element("span");
      item.append(element("span", "", label), element("strong", "", value));
      return item;
    });
    byId("provenance-strip").replaceChildren(...nodes);
  }

  function renderMetrics(run) {
    const aggregate = run.aggregate;
    const comparison = run.comparison;
    setMetric("metric-resolution", formatPercent(aggregate && aggregate.resolution_rate));
    setMetric(
      "metric-recall",
      formatPercent(aggregate && aggregate.relevant_file_recall_at_10),
    );
    setMetric("metric-mrr", formatDecimal(aggregate && aggregate.mrr));
    setMetric(
      "metric-latency",
      aggregate ? `${aggregate.latency_p95_ms} ms` : "--",
    );
    setDelta("delta-resolution", comparison && comparison.resolution_rate_delta, "pp");
    setDelta(
      "delta-recall",
      comparison && comparison.relevant_file_recall_at_10_delta,
      "pp",
    );
    setDelta("delta-mrr", comparison && comparison.mrr_delta, "");
    setDelta(
      "delta-latency",
      comparison && comparison.latency_p95_ms_delta,
      " ms",
      true,
    );
  }

  function renderGates(run) {
    const decision = run.gate_decision;
    const checks = decision ? decision.checks : [];
    const nodes = checks.map((check) => {
      const item = element("div", `gate-cell ${check.passed ? "pass" : "fail"}`);
      item.title = check.detail;
      item.append(
        element("span", "", check.name.replaceAll("_", " ")),
        element("strong", "", check.passed ? "PASS" : "FAIL"),
      );
      return item;
    });
    byId("gate-matrix").replaceChildren(...nodes);
    byId("gate-summary").textContent = decision
      ? `${checks.filter((check) => check.passed).length} of ${checks.length} checks passed`
      : "Gate decision pending";
  }

  function renderCases(run) {
    const nodes = run.results.map((result) => {
      const passed = casePassed(result);
      const row = element("div", "case-row");
      row.append(
        element("span", `rail-node ${passed ? "pass" : "fail"}`),
        element("span", "mono", result.case_id),
        element("span", "case-meta", passed ? "Resolved" : "Failed"),
        element("span", "case-meta", `${result.attempts} attempt${result.attempts === 1 ? "" : "s"}`),
        element(
          "span",
          "case-meta mono",
          result.report ? `${result.report.wall_clock_ms} ms` : result.failure_category,
        ),
      );
      if (result.error_summary) row.title = result.error_summary;
      return row;
    });
    byId("case-rail").replaceChildren(...nodes);
  }

  async function replayFailed() {
    const source = runs.find((run) => run.run_id === selectedRunId);
    if (!source) return;
    const caseIds = source.results
      .filter((result) => !casePassed(result))
      .map((result) => result.case_id);
    if (!caseIds.length) return;
    setBusy(true);
    try {
      const response = await request(`/v1/evaluations/runs/${source.run_id}/replay`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({case_ids: caseIds}),
      });
      const replay = await response.json();
      showNotice(`Replay ${replay.run_id.slice(0, 12)} completed`);
      selectedRunId = replay.run_id;
      await synchronize();
    } catch (error) {
      showNotice(error instanceof Error ? error.message : "Replay failed");
    } finally {
      setBusy(false);
    }
  }

  async function downloadReport() {
    const run = runs.find((item) => item.run_id === selectedRunId);
    if (!run) return;
    try {
      const response = await request(`/v1/evaluations/runs/${run.run_id}/report.md`, {
        headers: {Accept: "text/markdown"},
      });
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = element("a");
      link.href = url;
      link.download = `${run.run_id}.md`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      showNotice(error instanceof Error ? error.message : "Report download failed");
    }
  }

  function statusBadge(run) {
    const badge = element("span", "status-badge", gateLabel(run));
    if (run.status === "running" || run.status === "queued") {
      badge.classList.add("running");
    } else if (run.gate_decision && run.gate_decision.passed) {
      badge.classList.add("pass");
    } else if (run.gate_decision) {
      badge.classList.add("fail");
    }
    return badge;
  }

  function gateLabel(run) {
    if (run.status === "running" || run.status === "queued") return run.status;
    if (!run.gate_decision) return run.status;
    return run.gate_decision.passed ? "pass" : "fail";
  }

  function casePassed(result) {
    return Boolean(result.report && result.report.issue_resolution === 1);
  }

  function durationLabel(run) {
    if (!run.started_at || !run.completed_at) return "--";
    const duration = new Date(run.completed_at) - new Date(run.started_at);
    return duration < 1000 ? `${duration} ms` : `${(duration / 1000).toFixed(1)} s`;
  }

  function formatPercent(value) {
    return value === null || value === undefined ? "--" : `${Math.round(value * 100)}%`;
  }

  function formatDecimal(value) {
    return value === null || value === undefined ? "--" : value.toFixed(2);
  }

  function formatTime(value) {
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  }

  function setMetric(id, value) {
    byId(id).textContent = value;
  }

  function setDelta(id, value, suffix, inverse = false) {
    const node = byId(id);
    node.className = "";
    if (value === null || value === undefined) {
      node.textContent = "No baseline";
      return;
    }
    const display = suffix === "pp" ? value * 100 : value;
    node.textContent = `${display > 0 ? "+" : ""}${display.toFixed(suffix === "pp" ? 1 : 0)}${suffix}`;
    if (display === 0) return;
    const positive = inverse ? display < 0 : display > 0;
    node.classList.add(positive ? "delta-positive" : "delta-negative");
  }

  function setBusy(busy) {
    byId("refresh-button").disabled = busy;
    byId("replay-button").disabled = busy;
    document.body.setAttribute("aria-busy", String(busy));
  }

  function setConnected(connected) {
    byId("connection-dot").classList.toggle("online", connected);
    byId("connection-label").textContent = connected ? "Connected" : "Not connected";
    byId("connection-detail").textContent = connected ? "Tenant scope active" : "Identity required";
  }

  function showNotice(message) {
    const notice = byId("notice");
    notice.textContent = message;
    notice.hidden = false;
    window.setTimeout(() => {
      notice.hidden = true;
    }, 5000);
  }

  function openIdentity() {
    if (!identityDialog.open) identityDialog.showModal();
    tokenInput.focus();
  }

  function switchView(view) {
    activeView = view;
    document.querySelectorAll(".nav-item").forEach((item) => {
      item.classList.toggle("active", item.dataset.view === activeView);
    });
    byId("runs-view").hidden = activeView !== "runs";
    byId("tasks-view").hidden = activeView !== "tasks";
    byId("view-context").textContent = activeView === "runs"
      ? "Evaluation / Runs"
      : "Operations / Tasks";
    byId("view-title").textContent = activeView === "runs"
      ? "Candidate runs"
      : "Repository tasks";
  }

  byId("identity-form").addEventListener("submit", (event) => {
    event.preventDefault();
    apiToken = tokenInput.value;
    tokenInput.value = "";
    identityDialog.close();
    synchronize();
  });
  byId("dialog-cancel").addEventListener("click", () => identityDialog.close());
  byId("identity-button").addEventListener("click", openIdentity);
  byId("refresh-button").addEventListener("click", synchronize);
  byId("gate-filter").addEventListener("change", renderRuns);
  byId("replay-button").addEventListener("click", replayFailed);
  byId("download-button").addEventListener("click", downloadReport);
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", () => switchView(item.dataset.view));
  });

  switchView(activeView);
  openIdentity();
})();
