from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from repo_maintenance_agent.api.app import create_app
from repo_maintenance_agent.api.auth import StaticTokenAuthenticator
from repo_maintenance_agent.storage.memory import InMemoryTaskRepository


def _app(*, production: bool = False):
    return create_app(
        repository=InMemoryTaskRepository(),
        authenticator=StaticTokenAuthenticator({"console-test-identity": "tenant-a"}),
        production=production,
    )


async def test_console_shell_and_assets_are_available_in_production() -> None:
    app = _app(production=True)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        page = await client.get("/console")
        styles = await client.get("/console/app.css")
        script = await client.get("/console/app.js")

    assert page.status_code == 200
    assert app.title == "RepoAegis"
    assert page.headers["content-type"].startswith("text/html")
    assert "<title>Evaluation operations | RepoAegis</title>" in page.text
    assert 'id="run-table-body"' in page.text
    assert styles.headers["content-type"].startswith("text/css")
    assert script.headers["content-type"].startswith("text/javascript")
    assert page.headers["content-security-policy"].startswith("default-src 'self'")


async def test_console_does_not_persist_or_embed_api_credentials() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app()),
        base_url="http://testserver",
    ) as client:
        page = await client.get("/console")
        script = await client.get("/console/app.js")

    combined = page.text + script.text
    assert "localStorage" not in combined
    assert "sessionStorage" not in combined
    assert "document.cookie" not in combined
    assert "console-test-identity" not in combined
    assert 'type="password"' in page.text
