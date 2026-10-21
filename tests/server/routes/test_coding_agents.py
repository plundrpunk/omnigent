"""Coding-agent API behavior with deterministic local-discovery boundaries."""

from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI

from omnigent.acp_transport import ACPError, ACPModel, ACPSession
from omnigent.server.routes import coding_agents as routes


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(routes.create_coding_agents_router(), prefix="/v1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_list_refreshes_filters_and_forwards_model_option(client, monkeypatch):
    clear = Mock()
    scan = AsyncMock(
        return_value={
            "agents": [{"key": "claude", "installed": True}, {"key": "codex", "installed": False}],
            "counts": {"known": 2, "installed": 1},
        }
    )
    monkeypatch.setattr(routes, "clear_coding_agent_cache", clear)
    monkeypatch.setattr(routes, "discover_coding_agents", scan)
    response = await client.get(
        "/v1/coding-agents?refresh=true&installed_only=true&with_models=false"
    )
    assert response.status_code == 200
    assert response.json() == {
        "agents": [{"key": "claude", "installed": True}],
        "counts": {"known": 2, "installed": 1},
    }
    clear.assert_called_once_with()
    scan.assert_awaited_once_with(with_models=False)


@pytest.mark.asyncio
async def test_acp_literal_route_precedes_agent_lookup(client, monkeypatch):
    monkeypatch.setattr(routes, "ACP_LAUNCHERS", {"one": object(), "two": object()})
    monkeypatch.setattr(routes, "acp_status", lambda key: {"key": key, "ready": key == "one"})
    response = await client.get("/v1/coding-agents/acp")
    assert response.status_code == 200
    assert response.json() == {
        "agents": [{"key": "one", "ready": True}, {"key": "two", "ready": False}],
        "counts": {"supported": 2, "ready": 1},
    }


@pytest.mark.asyncio
async def test_get_agent_refreshes_and_returns_matching_record(client, monkeypatch):
    clear = Mock()
    scan = AsyncMock(
        return_value={"agents": [{"key": "codex"}, {"key": "claude", "version": "2"}]}
    )
    monkeypatch.setattr(routes, "clear_coding_agent_cache", clear)
    monkeypatch.setattr(routes, "discover_coding_agents", scan)
    response = await client.get("/v1/coding-agents/claude?refresh=true")
    assert response.status_code == 200
    assert response.json() == {"key": "claude", "version": "2"}
    clear.assert_called_once_with()
    scan.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("key,scanned", [("not-known", False), ("claude", True)])
async def test_get_agent_returns_404_for_unknown_or_missing_record(
    client, monkeypatch, key, scanned
):
    scan = AsyncMock(return_value={"agents": []})
    monkeypatch.setattr(routes, "discover_coding_agents", scan)
    response = await client.get(f"/v1/coding-agents/{key}")
    assert response.status_code == 404
    assert response.json()["detail"] == f"Unknown coding agent: {key}"
    assert scan.await_count == int(scanned)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "supported,argv,error,code",
    [
        (False, None, None, 404),
        (True, None, None, 409),
        (True, ["agent"], ACPError("authentication failed"), 502),
    ],
)
async def test_acp_models_distinguishes_unavailable_and_failed_handshake(
    client, monkeypatch, supported, argv, error, code
):
    monkeypatch.setattr(
        routes, "acp_status", lambda key: {"supported": supported, "reason": "adapter missing"}
    )
    monkeypatch.setattr(routes, "acp_launch_argv", lambda key: argv)
    models = AsyncMock(side_effect=error)
    monkeypatch.setattr(routes, "list_models", models)
    response = await client.get("/v1/coding-agents/claude/acp/models")
    assert response.status_code == code
    expected = {
        404: "No known ACP mode for coding agent: claude",
        409: "adapter missing",
        502: "authentication failed",
    }
    assert response.json()["detail"] == expected[code]
    assert models.await_count == int(code == 502)


@pytest.mark.asyncio
async def test_acp_models_serializes_live_catalog_and_forwards_cwd(client, monkeypatch):
    monkeypatch.setattr(routes, "acp_status", lambda key: {"supported": True})
    monkeypatch.setattr(routes, "acp_launch_argv", lambda key: ["agent", "acp"])
    models = AsyncMock(
        return_value=ACPSession("session-1", "model-1", [ACPModel("model-1", "Model One", "Fast")])
    )
    monkeypatch.setattr(routes, "list_models", models)
    response = await client.get("/v1/coding-agents/opencode/acp/models", params={"cwd": "/repo"})
    assert response.status_code == 200
    assert response.json() == {
        "key": "opencode",
        "source": "acp",
        "argv": ["agent", "acp"],
        "session_id": "session-1",
        "current_model_id": "model-1",
        "count": 1,
        "models": [
            {"id": "model-1", "label": "Model One", "description": "Fast", "source": "acp"}
        ],
    }
    models.assert_awaited_once_with(["agent", "acp"], cwd="/repo")
