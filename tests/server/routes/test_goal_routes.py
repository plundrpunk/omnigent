"""Tests for the Harness Automaton goal bridge (``/v1/goal``, R9 V2).

The harness CLI is faked with a tiny executable script so the tests
exercise the real subprocess path: contract temp-file → argv → exit
code → artifact reads. Status must come only from the exit code and
artifacts — the fake deliberately prints a success-shaped JSON payload
in failure cases to prove the route never infers success from prose.
"""

from __future__ import annotations

import asyncio
import json
import re
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest


@pytest.fixture(autouse=True)
def _clear_ams_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AMS_BASE_URL", raising=False)
    monkeypatch.delenv("AMS_API_KEY", raising=False)
    monkeypatch.delenv("AOS_FLEET_GOAL_PROJECT", raising=False)


#: A fake ``automaton`` CLI. Reads the contract, writes artifacts the
#: way the real goal path does, then exits with the code smuggled in
#: via the contract's ``budget.max_steps`` (keeps the fake stateless).
_FAKE_AUTOMATON = """#!/usr/bin/env python3
import json, sys
from pathlib import Path

args = sys.argv[1:]
contract = json.loads(Path(args[args.index("--contract") + 1]).read_text())
workdir = Path(args[args.index("--workdir") + 1])
goal_id = contract["goal_id"]
exit_code = int((contract.get("budget") or {}).get("max_steps") or 0)

art = workdir / "goal" / goal_id
art.mkdir(parents=True, exist_ok=True)
art.joinpath("goal-outcome.json").write_text(json.dumps({
    "goal_id": goal_id,
    "gate": {"test_command": "ran"},
    "claimed": "complete",  # the fake ALWAYS claims success in prose
}))
if exit_code == 3:
    art.joinpath("blocker.md").write_text("# Blocked\\nthe gate failed honestly")
if exit_code == 6:
    art.joinpath("checkpoint.json").write_text(json.dumps({
        "resume_command": "automaton goal --contract c.json --resume",
    }))
print("log line noise")
print(json.dumps({"outcome": {"goal_id": goal_id, "from": "stdout"}}))
sys.exit(exit_code)
"""


def _contract(goal_id: str = "t-goal", exit_code: int = 0) -> dict:
    return {
        "goal_id": goal_id,
        "end_state": "the tests pass",
        "evidence_criteria": {"test_command": "pytest -q"},
        "budget": {"max_steps": exit_code},
    }


@pytest.fixture()
def goal_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the bridge at a fake CLI inside ``tmp_path``."""
    bin_path = tmp_path / "automaton"
    bin_path.write_text(_FAKE_AUTOMATON)
    bin_path.chmod(bin_path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("HA_AUTOMATON_BIN", str(bin_path))
    monkeypatch.setenv("HA_GOAL_CWD", str(tmp_path))
    monkeypatch.setenv("HA_GOAL_WORKDIR", "runs-goal")
    return tmp_path


async def _wait_terminal(client: httpx.AsyncClient, run_id: str) -> dict:
    for _ in range(100):
        resp = await client.get(f"/v1/goal/{run_id}")
        assert resp.status_code == 200
        run = resp.json()
        if run["status"] != "running":
            return run
        await asyncio.sleep(0.05)
    raise AssertionError("goal run never reached a terminal status")


async def test_unconfigured_answers_503(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No env ⇒ 503 with a pointer at the knobs, on every endpoint."""
    monkeypatch.delenv("HA_AUTOMATON_BIN", raising=False)
    monkeypatch.delenv("HA_GOAL_CWD", raising=False)
    for method, url in (("post", "/v1/goal"), ("get", "/v1/goal"), ("get", "/v1/goal/x")):
        resp = await getattr(client, method)(url, **({"json": {}} if method == "post" else {}))
        assert resp.status_code == 503
        assert "HA_AUTOMATON_BIN" in resp.json()["detail"]


async def test_config_endpoint_reports_state(client: httpx.AsyncClient, goal_env: Path) -> None:
    resp = await client.get("/v1/goal/config")
    assert resp.status_code == 200
    assert resp.json() == {"configured": True, "modes": ["local"]}


@pytest.mark.parametrize(
    "contract, fragment",
    [
        (None, "must be a JSON object"),
        ({"end_state": "x", "evidence_criteria": {"test_command": "t"}}, "goal_id"),
        (
            {"goal_id": "../evil", "end_state": "x", "evidence_criteria": {"test_command": "t"}},
            "goal_id",
        ),
        ({"goal_id": "g", "evidence_criteria": {"test_command": "t"}}, "end_state"),
        ({"goal_id": "g", "end_state": "x"}, "evidence_criteria"),
        ({"goal_id": "g", "end_state": "x", "evidence_criteria": {}}, "fail closed"),
        (
            {"goal_id": "g", "end_state": "x", "evidence_criteria": {"required_files": []}},
            "fail closed",
        ),
    ],
)
async def test_invalid_contracts_are_422(
    client: httpx.AsyncClient, goal_env: Path, contract: dict | None, fragment: str
) -> None:
    """An empty or path-hostile gate never reaches the subprocess."""
    resp = await client.post("/v1/goal", json={"contract": contract})
    assert resp.status_code == 422
    assert fragment in json.dumps(resp.json()["detail"])


async def test_completed_run_reads_outcome_artifact(
    client: httpx.AsyncClient, goal_env: Path
) -> None:
    """Exit 0 ⇒ completed, outcome from goal-outcome.json, no blocker."""
    resp = await client.post(
        "/v1/goal",
        json={"contract": _contract(exit_code=0), "conversation_id": "conv-1"},
    )
    assert resp.status_code == 202
    run = await _wait_terminal(client, resp.json()["run_id"])
    assert run["status"] == "completed"
    assert run["exit_code"] == 0
    assert run["outcome"]["gate"] == {"test_command": "ran"}
    assert run["blocker_md"] is None
    assert run["checkpoint"] is None
    assert run["conversation_id"] == "conv-1"
    assert run["finished_at"] is not None


async def test_blocked_run_quotes_blocker_verbatim(
    client: httpx.AsyncClient, goal_env: Path
) -> None:
    """Exit 3 ⇒ blocked even though the payload claims completion."""
    resp = await client.post("/v1/goal", json={"contract": _contract("t-block", exit_code=3)})
    run = await _wait_terminal(client, resp.json()["run_id"])
    assert run["status"] == "blocked"
    assert run["exit_code"] == 3
    # The fake's outcome says "complete" — the status must not.
    assert run["outcome"]["claimed"] == "complete"
    assert "the gate failed honestly" in run["blocker_md"]


async def test_paused_run_surfaces_resume_command(
    client: httpx.AsyncClient, goal_env: Path
) -> None:
    """Exit 6 ⇒ paused with the checkpoint quoted verbatim."""
    resp = await client.post("/v1/goal", json={"contract": _contract("t-pause", exit_code=6)})
    run = await _wait_terminal(client, resp.json()["run_id"])
    assert run["status"] == "paused"
    assert "resume_command" in run["checkpoint"]


async def test_unknown_exit_code_is_error_not_success(
    client: httpx.AsyncClient, goal_env: Path
) -> None:
    """An unmapped exit code must read as error, never completion."""
    resp = await client.post("/v1/goal", json={"contract": _contract("t-weird", exit_code=7)})
    run = await _wait_terminal(client, resp.json()["run_id"])
    assert run["status"] == "error"
    assert run["exit_code"] == 7


async def test_list_filters_by_conversation(client: httpx.AsyncClient, goal_env: Path) -> None:
    for conv in ("conv-a", "conv-b"):
        resp = await client.post(
            "/v1/goal",
            json={"contract": _contract(f"t-{conv}"), "conversation_id": conv},
        )
        await _wait_terminal(client, resp.json()["run_id"])
    resp = await client.get("/v1/goal", params={"conversation_id": "conv-a"})
    runs = resp.json()["runs"]
    assert [r["goal_id"] for r in runs] == ["t-conv-a"]
    resp = await client.get("/v1/goal")
    assert len(resp.json()["runs"]) >= 2


async def test_unknown_run_is_404(client: httpx.AsyncClient, goal_env: Path) -> None:
    resp = await client.get("/v1/goal/nope")
    assert resp.status_code == 404


@pytest.fixture()
def fleet_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Capture AMS calls without dispatching real fleet work."""
    monkeypatch.delenv("HA_AUTOMATON_BIN", raising=False)
    monkeypatch.delenv("HA_GOAL_CWD", raising=False)
    monkeypatch.setenv("AMS_BASE_URL", "http://ams.test/")
    monkeypatch.setenv("AMS_API_KEY", "fleet-test-key")
    state: dict[str, list] = {"calls": [], "responses": []}

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None: ...

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc: Any) -> None: ...

        async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
            state["calls"].append({"method": method, "url": url, **kwargs})
            if state["responses"]:
                response = state["responses"].pop(0)
                if isinstance(response, Exception):
                    raise response
                return response
            assert method == "POST"
            return httpx.Response(201, json={"continuation_id": "continuation-1"})

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    return state


@pytest.mark.parametrize("project", [None, "fleet-custom"])
async def test_fleet_enqueue_payload(
    client: httpx.AsyncClient,
    fleet_env: dict,
    monkeypatch: pytest.MonkeyPatch,
    project: str | None,
) -> None:
    if project:
        monkeypatch.setenv("AOS_FLEET_GOAL_PROJECT", project)
    contract = _contract()
    contract["end_state"] = "deliver résumé " * 100
    contract["metadata"] = {
        "caller": {"keep": True},
        "origin": "old",
        "omnigent_conversation_id": "old",
    }
    response = await client.post(
        "/v1/goal",
        json={
            "contract": contract,
            "dispatch": "fleet",
            "conversation_id": "conv-fleet",
        },
    )
    assert response.status_code == 202
    run = response.json()
    assert run["dispatch"] == "fleet"
    assert run["status"] == "queued"
    assert run["continuation_id"] == "continuation-1"
    assert run["work_receipt"] is None
    assert run["contract"]["metadata"] == {
        "caller": {"keep": True},
        "origin": "omnigent-aos",
        "omnigent_conversation_id": "conv-fleet",
    }
    (call,) = fleet_env["calls"]
    assert call["method"] == "POST"
    assert call["url"] == "http://ams.test/api/v1/continuations/"
    assert call["headers"] == {"X-API-Key": "fleet-test-key"}
    marker = (
        "HA-GOALCONTRACT-JSON\n```json\n"
        + json.dumps(
            run["contract"],
            indent=2,
            sort_keys=True,
        )
        + "\n```"
    )
    assert call["json"] == {
        "original_goal": contract["end_state"][:500],
        "next_action": "goal-queue daemon: run the embedded goal contract with automaton goal",
        "handoff_notes": marker,
        "project": project or "fleet-autonomy",
        "task_type": "goal",
        "agent_id": "omnigent-aos",
        "blockers": [],
        "remaining_subtasks": [],
        "priority_memories": [],
    }
    # Copied from HA's extractor: exact marker and nested JSON round-trip.
    match = re.search(r"HA-GOALCONTRACT-JSON\s*```json\s*(\{.*?\})\s*```", marker, re.DOTALL)
    assert match is not None
    assert json.loads(match.group(1)) == run["contract"]
    assert "fleet-test-key" not in response.text
    listed = await client.get("/v1/goal", params={"conversation_id": "conv-fleet"})
    assert listed.json()["runs"] == [run]


@pytest.mark.parametrize("extra", [{"exec": {}}, {"exec": None}, {"exec": {"workspace": "/tmp"}}])
async def test_fleet_rejects_exec(
    client: httpx.AsyncClient,
    fleet_env: dict,
    extra: dict,
) -> None:
    response = await client.post(
        "/v1/goal",
        json={
            "contract": _contract(),
            "dispatch": "fleet",
            **extra,
        },
    )
    assert response.status_code == 422
    assert "daemon" in response.json()["detail"]
    assert fleet_env["calls"] == []


@pytest.mark.parametrize(
    "changes",
    [
        {"goal_id": "../bad"},
        {"evidence_criteria": {}},
        {"metadata": []},
    ],
)
async def test_fleet_validates_contract(
    client: httpx.AsyncClient,
    fleet_env: dict,
    changes: dict,
) -> None:
    response = await client.post(
        "/v1/goal",
        json={
            "contract": {**_contract(), **changes},
            "dispatch": "fleet",
        },
    )
    assert response.status_code == 422
    assert fleet_env["calls"] == []


async def test_fleet_unconfigured(
    client: httpx.AsyncClient,
    fleet_env: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AMS_BASE_URL")
    response = await client.post("/v1/goal", json={"contract": _contract(), "dispatch": "fleet"})
    assert response.status_code == 503
    assert "AMS bridge not configured" in response.json()["detail"]
    assert fleet_env["calls"] == []


@pytest.mark.parametrize(
    "upstream",
    [
        httpx.Response(401, text="fleet-test-key", headers={"X-API-Key": "fleet-test-key"}),
        httpx.Response(500, json={"detail": "fleet-test-key"}),
        httpx.ConnectError("fleet-test-key"),
        httpx.Response(200, text="fleet-test-key"),
        httpx.Response(201, json={}),
    ],
)
async def test_fleet_enqueue_errors_are_private(
    client: httpx.AsyncClient,
    fleet_env: dict,
    upstream: Any,
) -> None:
    fleet_env["responses"].append(upstream)
    response = await client.post("/v1/goal", json={"contract": _contract(), "dispatch": "fleet"})
    assert response.status_code == 502
    assert "fleet-test-key" not in response.text
    assert "X-API-Key" not in response.text
    if isinstance(upstream, httpx.Response) and upstream.status_code >= 400:
        assert response.json()["detail"] == f"AMS HTTP {upstream.status_code}"
    assert (await client.get("/v1/goal")).json()["runs"] == []


@pytest.mark.parametrize(
    "state, expected",
    [
        ("pending", "queued"),
        ("claimed", "running"),
        ("in_progress", "running"),
        ("completed", "completed"),
        ("failed", "blocked"),
    ],
)
async def test_fleet_refresh_uses_ams_state(
    client: httpx.AsyncClient,
    fleet_env: dict,
    state: str,
    expected: str,
) -> None:
    response = await client.post("/v1/goal", json={"contract": _contract(), "dispatch": "fleet"})
    run = response.json()
    assert run["contract"]["metadata"] == {"origin": "omnigent-aos"}
    receipt = {"event_id": "receipt-1", "payload": {"status": "completed"}}
    upstream = {
        "status": state,
        "summary": "verbatim completion claim",
        "outcome": {"claimed": "completed"},
        "completed_at": "2026-10-21T12:00:00Z",
    }
    fleet_env["responses"].extend(
        [
            httpx.Response(200, json=upstream),
            httpx.Response(200, json=receipt),
        ]
    )
    response = await client.get(f"/v1/goal/{run['run_id']}")
    assert response.status_code == 200
    refreshed = response.json()
    assert refreshed["status"] == expected  # prose/receipt cannot override AMS state
    assert refreshed["summary"] == upstream["summary"]
    assert refreshed["outcome"] == upstream["outcome"]
    assert refreshed["finished_at"] == upstream["completed_at"]
    assert refreshed["work_receipt"] == receipt
    assert fleet_env["calls"][1]["url"] == "http://ams.test/api/v1/continuations/continuation-1"
    assert fleet_env["calls"][2]["url"] == "http://ams.test/api/v1/work-receipts/latest"
    assert fleet_env["calls"][2]["params"] == {
        "session_id": "goal-queue:t-goal",
        "project": "fleet-autonomy",
    }
    assert all(call["headers"] == {"X-API-Key": "fleet-test-key"} for call in fleet_env["calls"])


@pytest.mark.parametrize(
    "receipt_response",
    [
        httpx.Response(404),
        httpx.Response(403),
        httpx.Response(204),
        httpx.Response(200, content=b"null"),
        httpx.Response(200, json={}),
        httpx.Response(500),
        httpx.ConnectError("private"),
    ],
)
async def test_fleet_receipt_absence_is_null(
    client: httpx.AsyncClient,
    fleet_env: dict,
    receipt_response: Any,
) -> None:
    response = await client.post("/v1/goal", json={"contract": _contract(), "dispatch": "fleet"})
    fleet_env["responses"].extend(
        [
            httpx.Response(200, json={"status": "completed"}),
            receipt_response,
        ]
    )
    response = await client.get(f"/v1/goal/{response.json()['run_id']}")
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["outcome"] is None
    assert response.json()["summary"] is None
    assert response.json()["work_receipt"] is None


@pytest.mark.parametrize(
    "upstream",
    [
        httpx.Response(403, text="fleet-test-key"),
        httpx.Response(200, json={"status": "unknown", "summary": "done"}),
    ],
)
async def test_fleet_refresh_failure_does_not_invent_status(
    client: httpx.AsyncClient,
    fleet_env: dict,
    upstream: httpx.Response,
) -> None:
    response = await client.post("/v1/goal", json={"contract": _contract(), "dispatch": "fleet"})
    fleet_env["responses"].append(upstream)
    response = await client.get(f"/v1/goal/{response.json()['run_id']}")
    assert response.status_code == 502
    assert "fleet-test-key" not in response.text
    assert (await client.get("/v1/goal")).json()["runs"][0]["status"] == "queued"


@pytest.mark.parametrize(
    "local, fleet, modes",
    [
        (False, False, []),
        (True, False, ["local"]),
        (False, True, ["fleet"]),
        (True, True, ["local", "fleet"]),
    ],
)
async def test_config_modes(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    local: bool,
    fleet: bool,
    modes: list[str],
) -> None:
    monkeypatch.setenv("HA_AUTOMATON_BIN", "automaton" if local else "")
    monkeypatch.setenv("HA_GOAL_CWD", "/tmp" if local else "")
    monkeypatch.setenv("AMS_BASE_URL", "http://ams.test" if fleet else "")
    response = await client.get("/v1/goal/config")
    assert response.json() == {"configured": bool(modes), "modes": modes}


async def test_fleet_running_does_not_consume_local_slots() -> None:
    from omnigent.server.routes.goal import GoalRunRegistry

    registry = GoalRunRegistry()
    for index in range(3):
        registry.create({"run_id": str(index), "dispatch": "fleet", "status": "running"})
    assert registry.running_count() == 0
    registry.create({"run_id": "local", "status": "running"})
    assert registry.running_count() == 1


async def test_fleet_concurrent_enqueues_have_distinct_records(
    client: httpx.AsyncClient,
    fleet_env: dict,
) -> None:
    fleet_env["responses"].extend(
        [
            httpx.Response(201, json={"continuation_id": f"continuation-{index}"})
            for index in range(2)
        ]
    )
    responses = await asyncio.gather(
        *[
            client.post(
                "/v1/goal", json={"contract": _contract(f"goal-{index}"), "dispatch": "fleet"}
            )
            for index in range(2)
        ]
    )
    assert all(response.status_code == 202 for response in responses)
    assert len({response.json()["run_id"] for response in responses}) == 2
    assert {response.json()["continuation_id"] for response in responses} == {
        "continuation-0",
        "continuation-1",
    }
    assert len((await client.get("/v1/goal")).json()["runs"]) == 2


async def test_fleet_events_never_read_local_artifacts(
    client: httpx.AsyncClient,
    fleet_env: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnigent.server.routes import goal

    def fail_if_read(run: dict) -> None:
        raise AssertionError("fleet run must not read local artifacts")

    monkeypatch.setattr(goal, "_artifact_for", fail_if_read)
    response = await client.post("/v1/goal", json={"contract": _contract(), "dispatch": "fleet"})
    run_id = response.json()["run_id"]
    fleet_env["responses"].extend(
        [
            httpx.Response(200, json={"status": "claimed"}),
            httpx.Response(404),
        ]
    )
    response = await client.get(f"/v1/goal/{run_id}/events")
    assert response.status_code == 200
    assert response.json() == {"run_id": run_id, "status": "running", "events": [], "total": 0}


async def test_fleet_refresh_clears_stale_receipt_and_keeps_original_project(
    client: httpx.AsyncClient,
    fleet_env: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AOS_FLEET_GOAL_PROJECT", "custom-project")
    response = await client.post("/v1/goal", json={"contract": _contract(), "dispatch": "fleet"})
    run_id = response.json()["run_id"]
    monkeypatch.setenv("AOS_FLEET_GOAL_PROJECT", "changed-project")
    fleet_env["responses"].extend(
        [
            httpx.Response(200, json={"status": "claimed"}),
            httpx.Response(200, json={"event_id": "receipt-1"}),
            httpx.Response(200, json={"status": "completed"}),
            httpx.Response(403),
        ]
    )
    first = await client.get(f"/v1/goal/{run_id}")
    assert first.json()["work_receipt"] == {"event_id": "receipt-1"}
    second = await client.get(f"/v1/goal/{run_id}")
    assert second.json()["status"] == "completed"
    assert second.json()["work_receipt"] is None
    for call in (fleet_env["calls"][2], fleet_env["calls"][4]):
        assert call["params"]["project"] == "custom-project"


@pytest.fixture()
async def goal_api(goal_env: Path):
    """Exercise the goal router in isolation, without starting a harness host."""
    from fastapi import FastAPI

    from omnigent.server.routes.goal import create_goal_router

    router = create_goal_router()
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, router.state


@pytest.mark.parametrize("body", [b"{", b"\xff", b"[]", b"null"])
async def test_invalid_json_never_creates_run(goal_api, body: bytes) -> None:
    client, registry = goal_api
    response = await client.post("/v1/goal", content=body)
    assert response.status_code == 422
    assert "JSON" in response.json()["detail"]
    assert registry.list() == []


@pytest.mark.parametrize(
    "fields, detail",
    [
        ({"conversation_id": 7}, "conversation_id"),
        ({"provider": "bad provider"}, "provider"),
        ({"model": "model;echo"}, "model"),
        ({"limits": []}, "limits must"),
        ({"limits": {"unknown": 1}}, "unknown limit"),
        ({"limits": {"max_tokens": True}}, "positive integer"),
        ({"limits": {"max_tokens": 0}}, "positive integer"),
        ({"limits": {"max_tokens": 4_000_001}}, "ceiling"),
        ({"base_url": "file:///tmp"}, "http(s)"),
        ({"api_key_env": "secret-value"}, "NAME"),
        ({"api_key_env": "GOAL_TEST_MISSING_KEY"}, "not set"),
        ({"role_models": []}, "JSON object"),
        ({"role_models": {"orchestrator": "model"}}, "unknown role"),
        ({"role_models": {"planner": "bad backend"}}, "provider:model"),
        ({"exec": []}, "JSON object"),
    ],
)
async def test_invalid_options_refuse_run(goal_api, monkeypatch, fields, detail) -> None:
    client, registry = goal_api
    monkeypatch.delenv("GOAL_TEST_MISSING_KEY", raising=False)
    response = await client.post("/v1/goal", json={"contract": _contract(), **fields})
    assert response.status_code == 422
    assert detail in response.json()["detail"]
    assert registry.running_count() == 0


@pytest.mark.parametrize(
    "spec, detail",
    [
        ({}, "workspace"),
        ({"workspace": "missing"}, "does not exist"),
        ({"workspace": "file"}, "not a directory"),
        ({"workspace": "outside"}, "outside every"),
        ({"workspace": "symlink"}, "outside every"),
        ({"sandbox": "none"}, "sandbox"),
        ({"allow_write": 1}, "boolean"),
        ({"command": "python -c"}, "bare token"),
    ],
)
async def test_execution_validation_checks_resolved_workspace(
    goal_api, goal_env, monkeypatch, spec, detail
) -> None:
    client, registry = goal_api
    root = goal_env / "allowed"
    root.mkdir()
    outside = goal_env / "outside"
    outside.mkdir()
    file = root / "file"
    file.write_text("not a directory")
    link = root / "symlink"
    link.symlink_to(outside, target_is_directory=True)
    paths = {"missing": root / "missing", "file": file, "outside": outside, "symlink": link}
    monkeypatch.setenv("HA_GOAL_EXEC_ROOTS", str(root))
    if "workspace" in spec:
        spec = {**spec, "workspace": str(paths[spec["workspace"]])}
    elif spec:
        spec = {"workspace": str(root), **spec}
    response = await client.post("/v1/goal", json={"contract": _contract(), "exec": spec})
    assert response.status_code == 422
    assert detail in response.json()["detail"]
    assert registry.list() == []


async def test_missing_exec_roots_fail_closed(goal_api, monkeypatch, goal_env) -> None:
    client, registry = goal_api
    monkeypatch.setenv("HA_GOAL_EXEC_ROOTS", f" :{goal_env / 'absent'}:")
    response = await client.post(
        "/v1/goal", json={"contract": _contract(), "exec": {"workspace": str(goal_env)}}
    )
    assert response.status_code == 422
    assert "not enabled" in response.json()["detail"]
    assert registry.list() == []


async def test_concurrent_limit_refuses_fourth_run(goal_api) -> None:
    client, registry = goal_api
    for index in range(3):
        registry.create({"run_id": str(index), "status": "running", "started_at": str(index)})
    response = await client.post("/v1/goal", json={"contract": _contract()})
    assert response.status_code == 429
    assert registry.running_count() == 3


async def test_valid_options_are_recorded_and_forwarded(goal_api, goal_env, monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from omnigent.server.routes import goal

    client, registry = goal_api
    monkeypatch.setenv("HA_GOAL_EXEC_ROOTS", str(goal_env))
    monkeypatch.setenv("GOAL_TEST_API_KEY", "test-secret")
    contract_paths = []
    captured = []

    async def spawn(*argv, **kwargs):
        captured.append((argv, kwargs))
        path = Path(argv[argv.index("--contract") + 1])
        contract_paths.append(path)
        assert json.loads(path.read_text()) == _contract()
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate.return_value = (b'{"outcome":{"verified":true}}', b"")
        return proc

    monkeypatch.setattr(goal.asyncio, "create_subprocess_exec", spawn)
    response = await client.post(
        "/v1/goal",
        json={
            "contract": _contract(),
            "provider": "openai-compatible",
            "model": "test/model:reasoning",
            "limits": {"max_tokens": 100, "timeout": 20},
            "base_url": "https://provider.invalid/v1",
            "api_key_env": "GOAL_TEST_API_KEY",
            "role_models": {"planner": "shim:model:high"},
            "exec": {"workspace": str(goal_env), "allow_write": True, "command": "shim"},
        },
    )
    assert response.status_code == 202
    initial = response.json()
    assert initial["exec"] == {
        "workspace": str(goal_env.resolve()),
        "allow_write": True,
        "command": "shim",
        "sandbox": "subprocess",
    }
    run = await _wait_terminal(client, initial["run_id"])
    assert run["status"] == "completed"
    assert run["outcome"] == {"verified": True}
    assert "_task" not in run
    assert "test-secret" not in json.dumps(run)
    argv, kwargs = captured[0]
    for flag, expected in {
        "--provider": "openai-compatible",
        "--model": "test/model:reasoning",
        "--max-tokens": "100",
        "--timeout": "20",
        "--base-url": "https://provider.invalid/v1",
        "--api-key-env": "GOAL_TEST_API_KEY",
        "--role-model": "planner=shim:model:high",
        "--exec-sandbox": "subprocess",
        "--exec-workspace": str(goal_env.resolve()),
        "--command": "shim",
    }.items():
        assert argv[argv.index(flag) + 1] == expected
    assert "--allow-exec" in argv
    assert "--exec-allow-write" in argv
    assert kwargs["cwd"] == str(goal_env)
    assert all(not path.exists() for path in contract_paths)
    assert registry.running_count() == 0


async def test_launch_error_is_terminal_and_removes_contract(goal_api, monkeypatch) -> None:
    from omnigent.server.routes import goal

    client, registry = goal_api
    paths = []

    async def spawn(*argv, **kwargs):
        paths.append(Path(argv[argv.index("--contract") + 1]))
        raise FileNotFoundError("missing executable")

    monkeypatch.setattr(goal.asyncio, "create_subprocess_exec", spawn)
    response = await client.post("/v1/goal", json={"contract": _contract()})
    run = await _wait_terminal(client, response.json()["run_id"])
    assert run["status"] == "error"
    assert run["exit_code"] is None
    assert "missing executable" in run["error"]
    assert run["finished_at"] is not None
    assert all(not path.exists() for path in paths)
    assert registry.running_count() == 0


@pytest.mark.parametrize("artifact", ["{torn", "[]"])
async def test_invalid_outcome_artifact_preserves_stdout(
    goal_env, monkeypatch, artifact: str
) -> None:
    from unittest.mock import AsyncMock

    from omnigent.server.routes import goal

    workdir = goal_env / "absolute-workdir"
    monkeypatch.setenv("HA_GOAL_WORKDIR", str(workdir))
    art = workdir / "goal" / "t-goal"
    art.mkdir(parents=True)
    (art / "goal-outcome.json").write_text(artifact)
    proc = AsyncMock()
    proc.returncode = None
    proc.communicate.return_value = (b'{"source":"stdout"}', b"x" * 9000)
    monkeypatch.setattr(goal.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    run = {"contract": _contract()}
    await goal._execute_goal_run(run)
    assert run["outcome"] == {"source": "stdout"}
    assert run["exit_code"] == -1
    assert run["status"] == "error"
    assert run["stderr_tail"] == "x" * goal._STDERR_TAIL_CHARS
    assert run["artifact_dir"] == str(art)


@pytest.mark.parametrize(
    "stdout, expected",
    [
        ("noise", {}),
        ('log\n{"broken"', {}),
        ('log\n{"value":1}', {"value": 1}),
        ('log\n{"outcome":{"verified":true}}', {"verified": True}),
    ],
)
def test_trailing_json_never_invents_outcomes(stdout, expected) -> None:
    from omnigent.server.routes.goal import _parse_trailing_json

    assert _parse_trailing_json(stdout) == expected


def test_artifact_read_caps_bytes_and_handles_read_error(tmp_path, monkeypatch) -> None:
    from omnigent.server.routes.goal import _ARTIFACT_CAP_BYTES, _read_artifact

    artifact = tmp_path / "blocker.md"
    artifact.write_bytes(b"\xff" + b"a" * _ARTIFACT_CAP_BYTES)
    assert _read_artifact(artifact) == "\ufffd" + "a" * (_ARTIFACT_CAP_BYTES - 1)

    def denied(self):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_bytes", denied)
    assert _read_artifact(artifact) is None


async def test_events_unknown_and_absent_artifact(goal_api, monkeypatch, goal_env) -> None:
    client, registry = goal_api
    monkeypatch.setenv("HA_GOAL_WORKDIR", str(goal_env / "runs"))
    assert (await client.get("/v1/goal/unknown/events")).status_code == 404
    registry.create({"run_id": "run", "status": "running", "started_at": "now"})
    assert (await client.get("/v1/goal/run/events")).json() == {
        "run_id": "run",
        "status": "running",
        "events": [],
        "total": 0,
    }
    (goal_env / "runs" / "artifacts").mkdir(parents=True)
    assert (await client.get("/v1/goal/run/events")).json()["events"] == []


async def test_events_pagination_preserves_indices_and_recovers_torn_reads(
    goal_api, tmp_path, monkeypatch
) -> None:
    from omnigent.server.routes import goal

    client, registry = goal_api
    artifact = tmp_path / "events.json"
    artifact.write_text("{torn")
    registry.create(
        {
            "run_id": "run",
            "status": "running",
            "started_at": "now",
            "outcome": {"artifact_path": str(artifact)},
        }
    )
    assert (await client.get("/v1/goal/run/events")).json()["total"] == 0
    events = [{"content": "first"}, None, {"content": "third"}, {"content": "fourth"}]
    artifact.write_text(json.dumps({"id": "artifact-id", "events": events}))
    monkeypatch.setattr(goal, "_MAX_EVENTS", 2)
    response = (await client.get("/v1/goal/run/events", params={"since": 1})).json()
    assert response["total"] == 4
    assert response["artifact_id"] == "artifact-id"
    assert response["events"] == [{"index": 2, "role": "?", "kind": "note", "summary": "third"}]
    assert (await client.get("/v1/goal/run/events", params={"since": -2})).json()["events"][0][
        "index"
    ] == 0
    assert (await client.get("/v1/goal/run/events", params={"since": 100})).json()["events"] == []


def test_artifact_fallback_selects_most_recent_file(tmp_path, monkeypatch) -> None:
    import os

    from omnigent.server.routes.goal import _artifact_for

    monkeypatch.setenv("HA_GOAL_WORKDIR", str(tmp_path))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    old = artifacts / "old.json"
    new = artifacts / "new.json"
    old.write_text("{}")
    new.write_text("{}")
    (artifacts / "directory.json").mkdir()
    os.utime(old, (100, 100))
    os.utime(new, (200, 200))
    assert _artifact_for({"outcome": {"artifact_path": str(tmp_path / "missing")}}) == new
    assert _artifact_for({"outcome": {"artifact_path": str(old)}}) == old


@pytest.mark.parametrize(
    "event, expected",
    [
        (
            {"metadata": {"phase": "critic"}, "content": "  line\n two "},
            {"role": "critic", "kind": "note", "summary": "line two"},
        ),
        ({"content": "{invalid"}, {"role": "?", "kind": "note", "summary": "{invalid"}),
        (
            {
                "role": "executor",
                "payload": {
                    "content": (
                        '{"tool":"shell","ok":true,"exit_code":0,"call":{"command":"echo ok"}}'
                    )
                },
            },
            {
                "role": "executor",
                "kind": "tool",
                "tool": "shell",
                "ok": True,
                "exit_code": 0,
                "target": "echo ok",
            },
        ),
        (
            {
                "content": (
                    '{"tool":"read","ok":false,"call":{"path":"missing"},"stderr":"not found"}'
                )
            },
            {
                "role": "?",
                "kind": "tool",
                "tool": "read",
                "ok": False,
                "exit_code": None,
                "target": "missing",
                "error": "not found",
            },
        ),
        (
            {"content": '{"verdict":"blocked","subgoal_id":"s1","reason":"missing proof"}'},
            {
                "role": "?",
                "kind": "verdict",
                "verdict": "blocked",
                "subgoal": "s1",
                "summary": "missing proof",
            },
        ),
        (
            {"content": '{"action":"retry","reason":"gate failed"}'},
            {
                "role": "?",
                "kind": "verdict",
                "verdict": "retry",
                "subgoal": None,
                "summary": "gate failed",
            },
        ),
        (
            {"content": '{"first":1,"second":2}'},
            {"role": "?", "kind": "note", "summary": "first, second"},
        ),
    ],
)
def test_normalised_events_preserve_meaning(event, expected) -> None:
    from omnigent.server.routes.goal import _normalise_event

    assert _normalise_event(9, event) == {"index": 9, **expected}


def test_executor_event_bounds_detail_and_ignores_invalid_calls() -> None:
    from omnigent.server.routes.goal import _normalise_event

    content = {
        "done": False,
        "patch": {str(i): i for i in range(8)},
        "tool_calls": [None] + [{"tool": "read", "path": "x" * 200}] * 8,
        "observation": "z" * 500,
    }
    event = _normalise_event(0, {"content": json.dumps(content)})
    assert event["kind"] == "executor"
    assert event["done"] is False
    assert event["patch_keys"] == [str(i) for i in range(6)]
    assert event["proposed"] == [{"tool": "read", "target": "x" * 159 + "\u2026"}] * 6
    assert event["summary"] == "z" * 399 + "\u2026"


@pytest.mark.xfail(
    strict=True,
    reason="Goal artifact fallback ignores started_at and can attach a previous run's events",
)
def test_artifact_fallback_does_not_attach_preexisting_run(tmp_path, monkeypatch) -> None:
    import os

    from omnigent.server.routes.goal import _artifact_for

    monkeypatch.setenv("HA_GOAL_WORKDIR", str(tmp_path))
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    stale = artifact_dir / "previous-run.json"
    stale.write_text('{"events":[{"content":"previous run"}]}')
    os.utime(stale, (100, 100))
    assert _artifact_for({"started_at": "2026-10-21T12:00:00+00:00"}) is None


@pytest.mark.xfail(
    strict=True,
    reason="Goal event fallback resolves relative workdir against server cwd, not HA_GOAL_CWD",
)
def test_running_events_resolve_workdir_against_harness_cwd(goal_env, monkeypatch) -> None:
    from omnigent.server.routes.goal import _artifact_for

    server_cwd = goal_env / "server"
    server_cwd.mkdir()
    monkeypatch.chdir(server_cwd)
    artifact_dir = goal_env / "runs-goal" / "artifacts"
    artifact_dir.mkdir(parents=True)
    artifact = artifact_dir / "current.json"
    artifact.write_text('{"events":[]}')
    assert _artifact_for({"started_at": "2026-10-21T12:00:00+00:00"}) == artifact
