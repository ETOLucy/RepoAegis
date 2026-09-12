import httpx


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
