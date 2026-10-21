"""Route tests for ``GET /v1/trajectory/{conversation_id}``.

Built on a minimal FastAPI app (real SQLite conversation + permission
stores, a header-based auth stub, a fake AMS receipt fetcher) so the
user-isolation rules are exercised end to end without booting the full
server.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.requests import HTTPConnection

from omnigent.entities.conversation import (
    FunctionCallData,
    MessageData,
    NewConversationItem,
    WorkReceiptData,
)
from omnigent.errors import OmnigentError
from omnigent.server.auth import LEVEL_OWNER, LEVEL_READ, AuthProvider
from omnigent.server.routes.goal import GoalRunRegistry, create_goal_router
from omnigent.server.routes.trajectory import create_trajectory_router
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

pytestmark = pytest.mark.asyncio

ALICE = "alice@example.com"
BOB = "bob@example.com"
RECEIPT_ID = "0b0e7a52-3c1d-4f0e-9a51-6c0b2f1d7e44"
OTHER_RECEIPT_ID = "1b0e7a52-3c1d-4f0e-9a51-6c0b2f1d7e44"


class _HeaderAuth(AuthProvider):
    def get_user_id(self, request: HTTPConnection) -> str | None:
        return request.headers.get("X-Test-User")


def _receipt(event_id: str, user_id: str, session_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "schema_version": "harness.receipt.v1",
        "event_id": event_id,
        "user_id": user_id,
        "project": "fleet-autonomy",
        "work_item_id": "wi-1",
        "session_id": session_id,
        "status": "completed",
        "created_at": "2026-10-21T10:00:05+00:00",
        "verifier": {"status": "passed", "evidence": [], "tool_result_ids": []},
        "artifact": {"artifact_id": "art-1", "changed_files": []},
        "tool_result_ids": [],
        "metadata": {"goal_id": "g-1", **extra},
    }


class _FakeAms:
    def __init__(self) -> None:
        self.receipts: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.work_items: list[str | None] = []
        self.fail = False

    async def __call__(
        self, session_id: str, work_item_id: str | None = None
    ) -> dict[str, Any] | None:
        # Behaves like an AMS build without work_item_id support: the
        # session's latest receipt comes back whatever work item was asked.
        self.calls.append(session_id)
        self.work_items.append(work_item_id)
        if self.fail:
            raise RuntimeError("AMS down")
        return self.receipts.get(session_id)


@pytest.fixture()
def stores(db_uri: str) -> tuple[SqlAlchemyConversationStore, SqlAlchemyPermissionStore]:
    return SqlAlchemyConversationStore(db_uri), SqlAlchemyPermissionStore(db_uri)


@pytest.fixture()
def fake_ams() -> _FakeAms:
    return _FakeAms()


@pytest.fixture()
def goal_router(stores) -> APIRouter:
    conv_store, perm_store = stores
    return create_goal_router(
        auth_provider=_HeaderAuth(),
        conversation_store=conv_store,
        permission_store=perm_store,
    )


@pytest.fixture()
def registry(goal_router: APIRouter) -> GoalRunRegistry:
    return goal_router.state


@pytest.fixture()
def seeded(
    stores: tuple[SqlAlchemyConversationStore, SqlAlchemyPermissionStore],
    tmp_path: Path,
    registry: GoalRunRegistry,
    fake_ams: _FakeAms,
) -> dict[str, str]:
    conv_store, perm_store = stores
    alice_conv = conv_store.create_conversation()
    child = conv_store.create_conversation(
        kind="sub_agent",
        parent_conversation_id=alice_conv.id,
        sub_agent_name="researcher",
    )
    bob_conv = conv_store.create_conversation()
    for user, conv in ((ALICE, alice_conv.id), (BOB, bob_conv.id)):
        perm_store.ensure_user(user)
        perm_store.grant(user, conv, LEVEL_OWNER)

    conv_store.append(
        alice_conv.id,
        [
            NewConversationItem(
                type="message",
                response_id="resp_1",
                data=MessageData(role="user", content=[{"type": "input_text", "text": "ship it"}]),
                created_by=ALICE,
            ),
            NewConversationItem(
                type="function_call",
                response_id="resp_1",
                data=FunctionCallData(
                    agent="coder",
                    name="sys_session_send",
                    arguments='{"agent": "researcher"}',
                    call_id="call_1",
                ),
            ),
            NewConversationItem(
                type="work_receipt",
                response_id="resp_1",
                data=WorkReceiptData.model_validate(_receipt(RECEIPT_ID, "drew", alice_conv.id)),
            ),
        ],
    )
    conv_store.append(
        child.id,
        [
            NewConversationItem(
                type="message",
                response_id="resp_c",
                data=MessageData(
                    role="assistant",
                    agent="researcher",
                    content=[{"type": "output_text", "text": "found it"}],
                ),
            )
        ],
    )
    conv_store.append(
        bob_conv.id,
        [
            NewConversationItem(
                type="message",
                response_id="resp_b",
                data=MessageData(
                    role="user", content=[{"type": "input_text", "text": "bob secret"}]
                ),
                created_by=BOB,
            )
        ],
    )

    artifact = tmp_path / "art-1.json"
    artifact.write_text(
        json.dumps(
            {
                "id": "art-1",
                "status": "complete",
                "events": [
                    {
                        "id": "ev-1",
                        "created_at": "2026-10-21T10:00:01+00:00",
                        "role": "executor",
                        "content": '{"done": true, "patch": {}, "observation": "wrote file"}',
                        "metadata": {"phase": "execute"},
                    }
                ],
            }
        )
    )
    for run_id, conv, goal, owner in (
        ("run-alice", alice_conv.id, "g-1", ALICE),
        ("run-bob", bob_conv.id, "g-bob", BOB),
    ):
        registry.create(
            {
                "run_id": run_id,
                "goal_id": goal,
                "conversation_id": conv,
                "owner": owner,
                # goal-queue receipts are joined to a run by its continuation.
                "continuation_id": "wi-1",
                "status": "completed",
                "exit_code": 0,
                "started_at": "2026-10-21T10:00:00+00:00",
                "finished_at": "2026-10-21T10:00:06+00:00",
                "outcome": {"artifact_path": str(artifact)} if goal == "g-1" else None,
            }
        )

    fake_ams.receipts["goal-queue:g-1"] = _receipt(OTHER_RECEIPT_ID, "drew", "goal-queue:g-1")
    return {"alice": alice_conv.id, "child": child.id, "bob": bob_conv.id}


@pytest_asyncio.fixture()
async def client(
    stores: tuple[SqlAlchemyConversationStore, SqlAlchemyPermissionStore],
    registry: GoalRunRegistry,
    fake_ams: _FakeAms,
    monkeypatch: pytest.MonkeyPatch,
    goal_router: APIRouter,
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("OMNIGENT_AMS_USER_MAP", f"{ALICE}=drew")
    conv_store, perm_store = stores
    app = FastAPI()
    app.include_router(goal_router, prefix="/v1")

    @app.exception_handler(OmnigentError)
    async def _err(_request: Request, exc: OmnigentError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content={"error": exc.message})

    app.include_router(
        create_trajectory_router(
            conv_store,
            auth_provider=_HeaderAuth(),
            permission_store=perm_store,
            goal_registry=registry,
            receipt_fetcher=fake_ams,
        ),
        prefix="/v1",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_owner_sees_merged_timeline(
    client: httpx.AsyncClient, seeded: dict[str, str]
) -> None:
    resp = await client.get(f"/v1/trajectory/{seeded['alice']}", headers={"X-Test-User": ALICE})
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == "trajectory.v1"
    entries = body["entries"]
    assert {e["source"] for e in entries} == {"omnigent", "harness", "ams"}
    actions = [e["action"] for e in entries]
    assert "user_message" in actions
    assert "dispatch" in actions
    assert "execute" in actions
    assert "goal_finished" in actions
    # Subagent work is folded in, labelled with its child lane.
    child = [e for e in entries if e["conversation_id"] == seeded["child"]]
    assert child and child[0]["subagent"] == "researcher"
    # Both receipts: one from the session item, one from AMS for the goal queue.
    receipt_ids = sorted(e["receipt"]["id"] for e in entries if e["action"] == "work_receipt")
    assert receipt_ids == sorted([RECEIPT_ID, OTHER_RECEIPT_ID])
    assert body["sources"]["ams"]["status"] == "ok"


async def test_other_users_runs_and_items_never_leak(
    client: httpx.AsyncClient, seeded: dict[str, str]
) -> None:
    resp = await client.get(f"/v1/trajectory/{seeded['alice']}", headers={"X-Test-User": ALICE})
    text = resp.text
    assert "run-bob" not in text
    assert "bob secret" not in text
    assert "g-bob" not in text


async def test_cannot_read_someone_elses_trajectory(
    client: httpx.AsyncClient, seeded: dict[str, str], fake_ams: _FakeAms
) -> None:
    resp = await client.get(f"/v1/trajectory/{seeded['bob']}", headers={"X-Test-User": ALICE})
    assert resp.status_code == 404
    assert fake_ams.calls == []


async def test_unauthenticated_is_rejected(
    client: httpx.AsyncClient, seeded: dict[str, str]
) -> None:
    resp = await client.get(f"/v1/trajectory/{seeded['alice']}")
    assert resp.status_code == 401


async def test_receipts_for_unmapped_ams_user_are_dropped(
    client: httpx.AsyncClient, seeded: dict[str, str], fake_ams: _FakeAms
) -> None:
    fake_ams.receipts["goal-queue:g-1"] = _receipt(OTHER_RECEIPT_ID, "mallory", "goal-queue:g-1")
    resp = await client.get(f"/v1/trajectory/{seeded['alice']}", headers={"X-Test-User": ALICE})
    body = resp.json()
    owners = {e["owner"] for e in body["entries"] if e["owner"]}
    assert owners == {"drew"}
    assert body["scope"]["dropped"] >= 1


async def test_goal_queue_receipt_for_another_continuation_is_dropped(
    client: httpx.AsyncClient, seeded: dict[str, str], fake_ams: _FakeAms
) -> None:
    """``goal-queue:<goal_id>`` is keyed by a caller-chosen id.

    Another user sharing the AMS bridge key (same mapped AMS owner) can run
    the same goal id; their receipt is the session's "latest" but names a
    different continuation, so it must not join this session's timeline.
    """
    fake_ams.receipts["goal-queue:g-1"] = _receipt(
        OTHER_RECEIPT_ID, "drew", "goal-queue:g-1", entered_by="carol"
    ) | {"work_item_id": "cont-carol", "continuation_id": "cont-carol"}
    resp = await client.get(f"/v1/trajectory/{seeded['alice']}", headers={"X-Test-User": ALICE})
    assert resp.status_code == 200
    body = resp.json()
    assert "goal-queue:g-1" in fake_ams.calls
    assert "wi-1" in fake_ams.work_items  # the lookup is pinned to the run
    assert OTHER_RECEIPT_ID not in json.dumps(body["entries"])
    assert "carol" not in json.dumps(body)
    assert body["sources"]["ams"]["count"] == 0
    link = await client.get(
        f"/v1/trajectory/{seeded['alice']}/receipts/{OTHER_RECEIPT_ID}",
        headers={"X-Test-User": ALICE},
    )
    assert link.status_code == 404


async def test_goal_filter_narrows_to_goal_entries(
    client: httpx.AsyncClient, seeded: dict[str, str]
) -> None:
    resp = await client.get(
        f"/v1/trajectory/{seeded['alice']}",
        params={"goal_id": "g-1"},
        headers={"X-Test-User": ALICE},
    )
    entries = resp.json()["entries"]
    assert entries
    assert {e["goal_id"] for e in entries} == {"g-1"}


async def test_ams_outage_is_reported_not_hidden(
    client: httpx.AsyncClient, seeded: dict[str, str], fake_ams: _FakeAms
) -> None:
    fake_ams.fail = True
    resp = await client.get(f"/v1/trajectory/{seeded['alice']}", headers={"X-Test-User": ALICE})
    assert resp.status_code == 200
    body = resp.json()
    assert body["sources"]["ams"]["status"] == "unreachable"
    assert any(e["source"] == "omnigent" for e in body["entries"])


async def test_receipt_link_resolves_only_in_scope(
    client: httpx.AsyncClient, seeded: dict[str, str]
) -> None:
    ok = await client.get(
        f"/v1/trajectory/{seeded['alice']}/receipts/{OTHER_RECEIPT_ID}",
        headers={"X-Test-User": ALICE},
    )
    assert ok.status_code == 200
    assert ok.json()["event_id"] == OTHER_RECEIPT_ID

    missing = await client.get(
        f"/v1/trajectory/{seeded['alice']}/receipts/2b0e7a52-3c1d-4f0e-9a51-6c0b2f1d7e44",
        headers={"X-Test-User": ALICE},
    )
    assert missing.status_code == 404

    foreign = await client.get(
        f"/v1/trajectory/{seeded['bob']}/receipts/{OTHER_RECEIPT_ID}",
        headers={"X-Test-User": ALICE},
    )
    assert foreign.status_code == 404


async def test_relative_artifact_path_resolves_against_goal_cwd(
    client: httpx.AsyncClient,
    seeded: dict[str, str],
    registry: GoalRunRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The harness CLI reports ``runs-goal/artifacts/<id>.json`` relative to its cwd."""
    rel = Path("runs-goal") / "artifacts" / "art-rel.json"
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text(
        json.dumps(
            {
                "id": "art-rel",
                "events": [
                    {
                        "id": "ev-r",
                        "created_at": "2026-10-21T11:00:00+00:00",
                        "role": "critic",
                        "content": '{"verdict": "ACCEPT", "reason": "fine"}',
                        "metadata": {"phase": "critique"},
                    }
                ],
            }
        )
    )
    monkeypatch.setenv("HA_GOAL_CWD", str(tmp_path))
    registry.create(
        {
            "run_id": "run-rel",
            "owner": ALICE,
            "goal_id": "g-rel",
            "conversation_id": seeded["alice"],
            "status": "completed",
            "exit_code": 0,
            "started_at": "2026-10-21T10:59:00+00:00",
            "finished_at": "2026-10-21T11:00:01+00:00",
            "outcome": {"artifact_path": str(rel)},
        }
    )
    resp = await client.get(
        f"/v1/trajectory/{seeded['alice']}",
        params={"goal_id": "g-rel"},
        headers={"X-Test-User": ALICE},
    )
    actions = [e["action"] for e in resp.json()["entries"]]
    assert actions == ["goal_started", "critique", "goal_finished"]


async def test_receipt_link_never_returns_foreign_payload_sharing_an_event_id(
    client: httpx.AsyncClient, seeded: dict[str, str], fake_ams: _FakeAms
) -> None:
    """An out-of-scope AMS copy must not overwrite the in-scope session copy."""
    fake_ams.receipts["goal-queue:g-1"] = _receipt(
        RECEIPT_ID, "mallory", "goal-queue:g-1", secret="mallory-only"
    )
    resp = await client.get(
        f"/v1/trajectory/{seeded['alice']}/receipts/{RECEIPT_ID}",
        headers={"X-Test-User": ALICE},
    )
    assert resp.status_code == 200
    assert resp.json()["user_id"] == "drew"
    assert "mallory" not in resp.text

    # The payload served is the one whose entry the timeline shows.
    timeline = await client.get(
        f"/v1/trajectory/{seeded['alice']}", headers={"X-Test-User": ALICE}
    )
    shown = [e for e in timeline.json()["entries"] if (e["receipt"] or {}).get("id") == RECEIPT_ID]
    assert [e["owner"] for e in shown] == ["drew"]


async def test_receipt_link_prefers_in_scope_ams_copy(
    client: httpx.AsyncClient, seeded: dict[str, str], fake_ams: _FakeAms
) -> None:
    fake_ams.receipts["goal-queue:g-1"] = _receipt(
        RECEIPT_ID, "drew", "goal-queue:g-1", copy="ams"
    )
    resp = await client.get(
        f"/v1/trajectory/{seeded['alice']}/receipts/{RECEIPT_ID}",
        headers={"X-Test-User": ALICE},
    )
    assert resp.status_code == 200
    assert resp.json()["metadata"]["copy"] == "ams"


async def test_capped_sessions_show_the_newest_window(
    client: httpx.AsyncClient,
    seeded: dict[str, str],
    stores: tuple[SqlAlchemyConversationStore, SqlAlchemyPermissionStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past the item cap, new activity still appears (oldest items roll off)."""
    from omnigent.server.routes import trajectory as trajectory_routes

    conv_store, _ = stores
    monkeypatch.setattr(trajectory_routes, "_ITEM_LIMIT", 2)
    monkeypatch.setattr(trajectory_routes, "_CHILD_ITEM_LIMIT", 1)
    conv_store.append(
        seeded["alice"],
        [
            NewConversationItem(
                type="message",
                response_id="resp_2",
                data=MessageData(
                    role="user", content=[{"type": "input_text", "text": "newest parent"}]
                ),
                created_by=ALICE,
            )
        ],
    )
    conv_store.append(
        seeded["child"],
        [
            NewConversationItem(
                type="message",
                response_id="resp_c2",
                data=MessageData(
                    role="assistant",
                    agent="researcher",
                    content=[{"type": "output_text", "text": "newest child"}],
                ),
            )
        ],
    )
    resp = await client.get(f"/v1/trajectory/{seeded['alice']}", headers={"X-Test-User": ALICE})
    body = resp.json()
    summaries = [e["summary"] for e in body["entries"] if e["source"] == "omnigent"]
    assert any("newest parent" in s for s in summaries)
    assert any("newest child" in s for s in summaries)
    assert not any("ship it" in s for s in summaries)
    assert not any("found it" in s for s in summaries)
    assert body["sources"]["omnigent"]["truncated"] is True
    assert body["sources"]["omnigent"]["count"] == 3
    # The surviving window is still in chronological order.
    parent = [e for e in body["entries"] if e["conversation_id"] == seeded["alice"]]
    omni = [e for e in parent if e["source"] == "omnigent"]
    assert [e["action"] for e in omni][-1] == "user_message"


@pytest.mark.parametrize("owner", [BOB, None, "", "  ", "drew"])
async def test_foreign_or_legacy_runs_on_own_session_are_excluded(
    client, seeded, registry, fake_ams, monkeypatch, owner
) -> None:
    from omnigent.server.routes import trajectory as trajectory_routes

    foreign = {
        **registry.get("run-alice"),
        "run_id": "injected-run",
        "goal_id": "injected-goal",
        "work_receipt": _receipt(OTHER_RECEIPT_ID, "drew", seeded["alice"]),
    }
    if owner is None:
        foreign.pop("owner")
    else:
        foreign["owner"] = owner
    registry.create(foreign)
    read_runs = []
    read_artifact = trajectory_routes._read_artifact

    def track_artifact(run):
        read_runs.append(run["run_id"])
        return read_artifact(run)

    monkeypatch.setattr(trajectory_routes, "_read_artifact", track_artifact)
    response = await client.get(
        f"/v1/trajectory/{seeded['alice']}", headers={"X-Test-User": ALICE}
    )
    assert response.status_code == 200
    assert "injected-run" not in response.text
    assert "injected-goal" not in response.text
    assert response.json()["sources"]["harness"]["count"] == 1
    assert read_runs == ["run-alice"]
    assert "goal-queue:injected-goal" not in fake_ams.calls
    assert {e["run_id"] for e in response.json()["entries"] if e["run_id"]} == {"run-alice"}


@pytest.mark.parametrize(
    "user_fields", [{}, {"user_id": None}, {"user_id": ""}, {"user_id": "  "}]
)
async def test_ownerless_receipt_is_absent_from_timeline_and_detail(
    client, seeded, fake_ams, user_fields
) -> None:
    receipt = _receipt(OTHER_RECEIPT_ID, "drew", "goal-queue:g-1")
    receipt.pop("user_id")
    receipt.update(user_fields)
    fake_ams.receipts["goal-queue:g-1"] = receipt
    url = f"/v1/trajectory/{seeded['alice']}"
    response = await client.get(url, headers={"X-Test-User": ALICE})
    assert response.status_code == 200
    assert OTHER_RECEIPT_ID not in response.text
    detail = await client.get(f"{url}/receipts/{OTHER_RECEIPT_ID}", headers={"X-Test-User": ALICE})
    assert detail.status_code == 404


@pytest.fixture()
def dispatch_stubs(monkeypatch):
    from omnigent.server.routes import goal

    monkeypatch.setenv("HA_AUTOMATON_BIN", "/unused/automaton")
    monkeypatch.setenv("HA_GOAL_CWD", "/unused/workspace")
    execute = AsyncMock()
    enqueue = AsyncMock(return_value={"continuation_id": "test-continuation"})
    monkeypatch.setattr(goal, "_execute_goal_run", execute)
    monkeypatch.setattr(goal, "_fleet_request", enqueue)
    return execute, enqueue


def _goal_body(dispatch, conversation_id):
    return {
        "contract": {
            "goal_id": "new-goal",
            "end_state": "tests pass",
            "evidence_criteria": {"test_command": "pytest -q"},
        },
        "dispatch": dispatch,
        "conversation_id": conversation_id,
        "owner": BOB,  # A client cannot override authenticated attribution.
    }


@pytest.mark.parametrize("dispatch", ["local", "fleet"])
@pytest.mark.parametrize("session", ["bob", "missing", "read_only"])
async def test_goal_rejects_inaccessible_session_before_dispatch(
    client, seeded, registry, stores, dispatch_stubs, dispatch, session
) -> None:
    _, permissions = stores
    if session == "read_only":
        permissions.grant(ALICE, seeded["bob"], LEVEL_READ)
    conversation_id = "0" * 32 if session == "missing" else seeded["bob"]
    before = registry.list()
    response = await client.post(
        "/v1/goal",
        json=_goal_body(dispatch, conversation_id),
        headers={"X-Test-User": ALICE},
    )
    assert response.status_code == (403 if session == "read_only" else 404)
    assert registry.list() == before
    for stub in dispatch_stubs:
        stub.assert_not_called()


@pytest.mark.parametrize("dispatch", ["local", "fleet"])
@pytest.mark.parametrize("session", ["alice", "child", None])
async def test_goal_records_authenticated_owner_and_own_run_appears(
    client, seeded, registry, dispatch_stubs, dispatch, session
) -> None:
    conversation_id = seeded[session] if session else None
    response = await client.post(
        "/v1/goal",
        json=_goal_body(dispatch, conversation_id),
        headers={"X-Test-User": ALICE},
    )
    assert response.status_code == 202
    run = registry.get(response.json()["run_id"])
    assert run["owner"] == ALICE
    assert run["conversation_id"] == conversation_id
    if conversation_id is not None:
        timeline = await client.get(
            f"/v1/trajectory/{conversation_id}", headers={"X-Test-User": ALICE}
        )
        assert timeline.status_code == 200
        assert any(e["run_id"] == run["run_id"] for e in timeline.json()["entries"])
    if dispatch == "fleet":
        dispatch_stubs[1].assert_awaited_once()
        dispatch_stubs[0].assert_not_called()


@pytest.mark.parametrize("dispatch", ["local", "fleet"])
async def test_concurrent_callers_cannot_attach_to_same_private_session(
    client, seeded, registry, dispatch_stubs, dispatch
) -> None:
    owner, intruder = await asyncio.gather(
        *(
            client.post(
                "/v1/goal",
                json=_goal_body(dispatch, seeded["alice"]),
                headers={"X-Test-User": user},
            )
            for user in (ALICE, BOB)
        )
    )
    assert owner.status_code == 202
    assert intruder.status_code == 404
    created = [r for r in registry.list() if r["goal_id"] == "new-goal"]
    assert len(created) == 1
    assert created[0]["owner"] == ALICE


@pytest.mark.parametrize("auth_provider", [None, _HeaderAuth()])
async def test_single_user_keeps_legacy_run_and_ownerless_receipt(
    stores, seeded, registry, fake_ams, auth_provider
) -> None:
    registry.get("run-alice").pop("owner")
    fake_ams.receipts["goal-queue:g-1"].pop("user_id")
    app = FastAPI()
    app.include_router(
        create_trajectory_router(
            stores[0],
            auth_provider=auth_provider,
            goal_registry=registry,
            receipt_fetcher=fake_ams,
        ),
        prefix="/v1",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as local:
        response = await local.get(
            f"/v1/trajectory/{seeded['alice']}", headers={"X-Test-User": "local"}
        )
        assert response.status_code == 200
        assert any(e["run_id"] == "run-alice" for e in response.json()["entries"])
        assert OTHER_RECEIPT_ID in response.text
        detail = await local.get(
            f"/v1/trajectory/{seeded['alice']}/receipts/{OTHER_RECEIPT_ID}",
            headers={"X-Test-User": "local"},
        )
        assert detail.status_code == 200
        assert "user_id" not in detail.json()
