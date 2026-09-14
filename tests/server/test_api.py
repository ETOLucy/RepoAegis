import httpx
from fastapi import FastAPI

from repoaegis.server.models import ApprovalKind, TaskStatus


async def test_healthz(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_create_list_get_roundtrip(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/tasks", json={"issue_url": "https://github.com/o/r/issues/9"})
    assert r.status_code == 201
    task = r.json()
    assert task["status"] == "queued"
    assert task["title"] == "o/r#9"

    assert [t["id"] for t in (await client.get("/api/tasks")).json()] == [task["id"]]
    assert (await client.get(f"/api/tasks/{task['id']}")).json() == task

    events = (await client.get(f"/api/tasks/{task['id']}/events")).json()
    assert [e["type"] for e in events] == ["task.created"]


async def test_validation_and_404(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/tasks", json={"issue_url": "not a url"})).status_code == 422
    assert (await client.get("/api/tasks/missing")).status_code == 404
    assert (await client.get("/api/tasks/missing/events")).status_code == 404


async def open_plan_gate(app: FastAPI, client: httpx.AsyncClient) -> tuple[str, str]:
    """Create a task and walk it to a pending plan gate through the app's own wiring."""
    task_id = (
        await client.post("/api/tasks", json={"issue_url": "https://github.com/o/r/issues/2"})
    ).json()["id"]
    machine = app.state.machine
    await machine.advance(task_id, TaskStatus.PLANNING)
    await machine.advance(task_id, TaskStatus.AWAITING_APPROVAL)
    approval = await app.state.gate.request(
        task_id, ApprovalKind.PLAN, {"plan": {"summary": "s"}}, subject="s"
    )
    return task_id, approval.id


async def test_approving_over_http_moves_the_task(app: FastAPI, client: httpx.AsyncClient) -> None:
    task_id, approval_id = await open_plan_gate(app, client)

    listed = (await client.get(f"/api/tasks/{task_id}/approvals")).json()
    assert [a["status"] for a in listed] == ["pending"]
    payload_hash = listed[0]["payload_hash"]

    answered = await client.post(
        f"/api/approvals/{approval_id}/decision",
        json={"decision": "approve", "payload_hash": payload_hash},
        headers={"X-Actor": "user:lucy"},
    )
    assert answered.status_code == 200
    assert answered.json()["status"] == "approved"
    assert answered.json()["decided_by"] == "user:lucy"

    assert (await client.get(f"/api/tasks/{task_id}")).json()["status"] == "solving"


async def test_double_click_is_harmless_but_reversal_is_a_conflict(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    _, approval_id = await open_plan_gate(app, client)
    url = f"/api/approvals/{approval_id}/decision"

    first = await client.post(url, json={"decision": "approve"})
    second = await client.post(url, json={"decision": "approve"})
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()

    reversal = await client.post(url, json={"decision": "reject"})
    assert reversal.status_code == 409


async def test_decision_rejects_a_stale_content_hash(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    _, approval_id = await open_plan_gate(app, client)
    answered = await client.post(
        f"/api/approvals/{approval_id}/decision",
        json={"decision": "approve", "payload_hash": "0" * 64},
    )
    assert answered.status_code == 409


async def test_unknown_approval_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/approvals/missing")).status_code == 404
    answered = await client.post("/api/approvals/missing/decision", json={"decision": "approve"})
    assert answered.status_code == 404
