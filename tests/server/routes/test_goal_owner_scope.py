"""Goal reads must enforce owner isolation before accessing backing data."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from omnigent.errors import OmnigentError
from omnigent.server.api_errors import register_api_error_handlers
from omnigent.server.auth import UnifiedAuthProvider
from omnigent.server.routes import goal
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore


@pytest.fixture()
async def owner_api(db_uri, monkeypatch, tmp_path):
    monkeypatch.delenv("AMS_BASE_URL", raising=False)
    monkeypatch.setenv("HA_AUTOMATON_BIN", "/unused/automaton")
    monkeypatch.setenv("HA_GOAL_CWD", str(tmp_path))
    monkeypatch.setenv("HA_GOAL_WORKDIR", str(tmp_path))
    permissions = SqlAlchemyPermissionStore(db_uri)
    for user in ("alice", "bob", "admin"):
        permissions.ensure_user(user, is_admin=user == "admin")
    router = goal.create_goal_router(
        UnifiedAuthProvider(source="header", local_single_user=False),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        permission_store=permissions,
    )
    app = FastAPI()
    register_api_error_handlers(app)

    @app.exception_handler(OmnigentError)
    async def handle_error(request, exc):
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    app.include_router(router, prefix="/v1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, router.state


def _headers(user):
    return {"X-Forwarded-Email": user}


@pytest.mark.parametrize("dispatch", ["local", "fleet"])
async def test_created_runs_use_authenticated_owner(owner_api, monkeypatch, dispatch):
    client, registry = owner_api

    async def execute(run):
        run["status"] = "completed"

    async def enqueue(contract, conversation_id):
        return {
            "run_id": contract["goal_id"],
            "conversation_id": conversation_id,
            "dispatch": "fleet",
            "status": "running",
            "started_at": "now",
        }

    monkeypatch.setattr(goal, "_execute_goal_run", execute)
    monkeypatch.setattr(goal, "_enqueue_fleet_goal", enqueue)
    monkeypatch.setattr(goal, "_refresh_fleet_goal", AsyncMock())
    for user, other in (("alice", "bob"), ("bob", "alice")):
        response = await client.post(
            "/v1/goal",
            headers=_headers(user),
            json={
                "dispatch": dispatch,
                "owner": other,
                "contract": {
                    "goal_id": f"{user}-goal",
                    "end_state": "done",
                    "evidence_criteria": {"test_command": "true"},
                },
            },
        )
        assert response.status_code == 202
        assert response.json()["owner"] == user
        run_id = response.json()["run_id"]
        assert registry.get(run_id)["owner"] == user
        assert (await client.get(f"/v1/goal/{run_id}", headers=_headers(user))).status_code == 200
        for suffix in ("", "/events"):
            assert (
                await client.get(f"/v1/goal/{run_id}{suffix}", headers=_headers(other))
            ).status_code == 404


@pytest.mark.parametrize("dispatch", ["local", "fleet"])
async def test_two_owners_only_read_their_own_runs(owner_api, monkeypatch, tmp_path, dispatch):
    client, registry = owner_api
    refresh = AsyncMock()
    monkeypatch.setattr(goal, "_refresh_fleet_goal", refresh)
    for user in ("alice", "bob"):
        artifact = tmp_path / f"{user}.json"
        artifact.write_text(json.dumps({"events": [{"content": f"{user} private events"}]}))
        registry.create(
            {
                "run_id": f"{user}-run",
                "owner": user,
                "conversation_id": "shared-session",
                "started_at": "now",
                "status": "running",
                "dispatch": dispatch,
                "outcome": {"artifact_path": str(artifact)},
            }
        )

    # Concurrent readers must not share a cached or mutable caller identity.
    async def check_user(user, other):
        for params in ({}, {"conversation_id": "shared-session"}):
            response = await client.get("/v1/goal", headers=_headers(user), params=params)
            assert response.status_code == 200
            assert [r["run_id"] for r in response.json()["runs"]] == [f"{user}-run"]
            assert other not in response.text
        for suffix in ("", "/events", "/events?since=999999999999999999999"):
            response = await client.get(f"/v1/goal/{user}-run{suffix}", headers=_headers(user))
            assert response.status_code == 200
            assert other not in response.text
            denied = await client.get(f"/v1/goal/{other}-run{suffix}", headers=_headers(user))
            missing = await client.get(f"/v1/goal/missing{suffix}", headers=_headers(user))
            assert denied.status_code == missing.status_code == 404
            assert denied.json() == missing.json()
            assert other not in denied.text

    await asyncio.gather(check_user("alice", "bob"), check_user("bob", "alice"))
    assert refresh.await_count == (6 if dispatch == "fleet" else 0)


@pytest.mark.parametrize("owner", [None, "", "missing"])
async def test_legacy_runs_require_explicit_admin(owner_api, owner):
    client, registry = owner_api
    run = {"run_id": "legacy", "started_at": "now", "status": "running"}
    if owner != "missing":
        run["owner"] = owner
    registry.create(run)
    registry.create({**run, "run_id": "owned", "owner": "bob"})
    for user in ("alice", "admin"):
        response = await client.get("/v1/goal", headers=_headers(user))
        assert [r["run_id"] for r in response.json()["runs"]] == (
            ["legacy"] if user == "admin" else []
        )
        for suffix in ("", "/events"):
            response = await client.get(f"/v1/goal/legacy{suffix}", headers=_headers(user))
            assert response.status_code == (200 if user == "admin" else 404)
            denied = await client.get(f"/v1/goal/owned{suffix}", headers=_headers(user))
            assert denied.status_code == 404
            assert "bob" not in denied.text


async def test_auth_and_scope_precede_backing_reads(owner_api, monkeypatch):
    client, registry = owner_api
    refresh = AsyncMock(side_effect=AssertionError("unauthorized fleet refresh"))
    artifact = Mock(side_effect=AssertionError("unauthorized artifact read"))
    monkeypatch.setattr(goal, "_refresh_fleet_goal", refresh)
    monkeypatch.setattr(goal, "_artifact_for", artifact)
    monkeypatch.delenv("HA_AUTOMATON_BIN")
    monkeypatch.delenv("HA_GOAL_CWD")
    for dispatch in ("local", "fleet"):
        registry.create({"run_id": dispatch, "owner": "bob", "dispatch": dispatch})
        for suffix in ("", "/events"):
            url = f"/v1/goal/{dispatch}{suffix}"
            assert (await client.get(url)).status_code == 401
            assert (await client.get(url, headers=_headers("alice"))).status_code == 404
    assert (await client.get("/v1/goal")).status_code == 401
    refresh.assert_not_awaited()
    artifact.assert_not_called()


async def test_owned_events_do_not_fall_back_to_another_runs_artifact(owner_api, tmp_path):
    client, registry = owner_api
    registry.create({"run_id": "alice-run", "owner": "alice", "status": "running"})
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "bob.json").write_text('{"events":[{"content":"bob private events"}]}')
    response = await client.get("/v1/goal/alice-run/events", headers=_headers("alice"))
    assert response.status_code == 200
    assert response.json()["events"] == []
    assert "bob" not in response.text


@pytest.mark.parametrize("auth_enabled", [False, True])
async def test_legacy_hidden_without_admin_store(monkeypatch, auth_enabled):
    monkeypatch.setenv("AMS_BASE_URL", "http://ams.test")
    auth = UnifiedAuthProvider(source="header", local_single_user=False) if auth_enabled else None
    router = goal.create_goal_router(auth)
    router.state.create({"run_id": "legacy", "started_at": "now", "status": "running"})
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=_headers("admin")
    ) as client:
        assert (await client.get("/v1/goal")).json() == {"runs": []}
        for suffix in ("", "/events"):
            assert (await client.get(f"/v1/goal/legacy{suffix}")).status_code == 404
