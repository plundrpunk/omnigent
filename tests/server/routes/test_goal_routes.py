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


async def test_config_endpoint_reports_state(
    client: httpx.AsyncClient, goal_env: Path
) -> None:
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


async def test_list_filters_by_conversation(
    client: httpx.AsyncClient, goal_env: Path
) -> None:
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
    client: httpx.AsyncClient, fleet_env: dict, monkeypatch: pytest.MonkeyPatch,
    project: str | None,
) -> None:
    if project:
        monkeypatch.setenv("AOS_FLEET_GOAL_PROJECT", project)
    contract = _contract()
    contract["end_state"] = "deliver résumé " * 100
    contract["metadata"] = {
        "caller": {"keep": True}, "origin": "old", "omnigent_conversation_id": "old",
    }
    response = await client.post("/v1/goal", json={
        "contract": contract, "dispatch": "fleet", "conversation_id": "conv-fleet",
    })
    assert response.status_code == 202
    run = response.json()
    assert run["dispatch"] == "fleet"
    assert run["status"] == "queued"
    assert run["continuation_id"] == "continuation-1"
    assert run["work_receipt"] is None
    assert run["contract"]["metadata"] == {
        "caller": {"keep": True}, "origin": "omnigent-aos",
        "omnigent_conversation_id": "conv-fleet",
    }
    (call,) = fleet_env["calls"]
    assert call["method"] == "POST"
    assert call["url"] == "http://ams.test/api/v1/continuations/"
    assert call["headers"] == {"X-API-Key": "fleet-test-key"}
    marker = "HA-GOALCONTRACT-JSON\n```json\n" + json.dumps(
        run["contract"], indent=2, sort_keys=True,
    ) + "\n```"
    assert call["json"] == {
        "original_goal": contract["end_state"][:500],
        "next_action": "goal-queue daemon: run the embedded goal contract with automaton goal",
        "handoff_notes": marker,
        "project": project or "fleet-autonomy",
        "task_type": "goal", "agent_id": "omnigent-aos", "blockers": [],
        "remaining_subtasks": [], "priority_memories": [],
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
    client: httpx.AsyncClient, fleet_env: dict, extra: dict,
) -> None:
    response = await client.post("/v1/goal", json={
        "contract": _contract(), "dispatch": "fleet", **extra,
    })
    assert response.status_code == 422
    assert "daemon" in response.json()["detail"]
    assert fleet_env["calls"] == []


@pytest.mark.parametrize("changes", [
    {"goal_id": "../bad"}, {"evidence_criteria": {}}, {"metadata": []},
])
async def test_fleet_validates_contract(
    client: httpx.AsyncClient, fleet_env: dict, changes: dict,
) -> None:
    response = await client.post("/v1/goal", json={
        "contract": {**_contract(), **changes}, "dispatch": "fleet",
    })
    assert response.status_code == 422
    assert fleet_env["calls"] == []


async def test_fleet_unconfigured(
    client: httpx.AsyncClient, fleet_env: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AMS_BASE_URL")
    response = await client.post("/v1/goal", json={"contract": _contract(), "dispatch": "fleet"})
    assert response.status_code == 503
    assert "AMS bridge not configured" in response.json()["detail"]
    assert fleet_env["calls"] == []


@pytest.mark.parametrize("upstream", [
    httpx.Response(401, text="fleet-test-key", headers={"X-API-Key": "fleet-test-key"}),
    httpx.Response(500, json={"detail": "fleet-test-key"}),
    httpx.ConnectError("fleet-test-key"),
    httpx.Response(200, text="fleet-test-key"),
    httpx.Response(201, json={}),
])
async def test_fleet_enqueue_errors_are_private(
    client: httpx.AsyncClient, fleet_env: dict, upstream: Any,
) -> None:
    fleet_env["responses"].append(upstream)
    response = await client.post("/v1/goal", json={"contract": _contract(), "dispatch": "fleet"})
    assert response.status_code == 502
    assert "fleet-test-key" not in response.text
    assert "X-API-Key" not in response.text
    if isinstance(upstream, httpx.Response) and upstream.status_code >= 400:
        assert response.json()["detail"] == f"AMS HTTP {upstream.status_code}"
    assert (await client.get("/v1/goal")).json()["runs"] == []


@pytest.mark.parametrize("state, expected", [
    ("pending", "queued"), ("claimed", "running"), ("in_progress", "running"),
    ("completed", "completed"), ("failed", "blocked"),
])
async def test_fleet_refresh_uses_ams_state(
    client: httpx.AsyncClient, fleet_env: dict, state: str, expected: str,
) -> None:
    response = await client.post("/v1/goal", json={"contract": _contract(), "dispatch": "fleet"})
    run = response.json()
    assert run["contract"]["metadata"] == {"origin": "omnigent-aos"}
    receipt = {"event_id": "receipt-1", "payload": {"status": "completed"}}
    upstream = {
        "status": state, "summary": "verbatim completion claim",
        "outcome": {"claimed": "completed"}, "completed_at": "2026-10-21T12:00:00Z",
    }
    fleet_env["responses"].extend([
        httpx.Response(200, json=upstream), httpx.Response(200, json=receipt),
    ])
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
        "session_id": "goal-queue:t-goal", "project": "fleet-autonomy",
    }
    assert all(call["headers"] == {"X-API-Key": "fleet-test-key"} for call in fleet_env["calls"])


@pytest.mark.parametrize("receipt_response", [
    httpx.Response(404), httpx.Response(403), httpx.Response(204),
    httpx.Response(200, content=b"null"), httpx.Response(200, json={}),
    httpx.Response(500), httpx.ConnectError("private"),
])
async def test_fleet_receipt_absence_is_null(
    client: httpx.AsyncClient, fleet_env: dict, receipt_response: Any,
) -> None:
    response = await client.post("/v1/goal", json={"contract": _contract(), "dispatch": "fleet"})
    fleet_env["responses"].extend([
        httpx.Response(200, json={"status": "completed"}), receipt_response,
    ])
    response = await client.get(f"/v1/goal/{response.json()['run_id']}")
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["outcome"] is None
    assert response.json()["summary"] is None
    assert response.json()["work_receipt"] is None


@pytest.mark.parametrize("upstream", [
    httpx.Response(403, text="fleet-test-key"),
    httpx.Response(200, json={"status": "unknown", "summary": "done"}),
])
async def test_fleet_refresh_failure_does_not_invent_status(
    client: httpx.AsyncClient, fleet_env: dict, upstream: httpx.Response,
) -> None:
    response = await client.post("/v1/goal", json={"contract": _contract(), "dispatch": "fleet"})
    fleet_env["responses"].append(upstream)
    response = await client.get(f"/v1/goal/{response.json()['run_id']}")
    assert response.status_code == 502
    assert "fleet-test-key" not in response.text
    assert (await client.get("/v1/goal")).json()["runs"][0]["status"] == "queued"


@pytest.mark.parametrize("local, fleet, modes", [
    (False, False, []), (True, False, ["local"]),
    (False, True, ["fleet"]), (True, True, ["local", "fleet"]),
])
async def test_config_modes(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
    local: bool, fleet: bool, modes: list[str],
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
    client: httpx.AsyncClient, fleet_env: dict,
) -> None:
    fleet_env["responses"].extend([
        httpx.Response(201, json={"continuation_id": f"continuation-{index}"})
        for index in range(2)
    ])
    responses = await asyncio.gather(*[
        client.post("/v1/goal", json={"contract": _contract(f"goal-{index}"), "dispatch": "fleet"})
        for index in range(2)
    ])
    assert all(response.status_code == 202 for response in responses)
    assert len({response.json()["run_id"] for response in responses}) == 2
    assert {response.json()["continuation_id"] for response in responses} == {
        "continuation-0", "continuation-1",
    }
    assert len((await client.get("/v1/goal")).json()["runs"]) == 2


async def test_fleet_events_never_read_local_artifacts(
    client: httpx.AsyncClient, fleet_env: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnigent.server.routes import goal

    def fail_if_read(run: dict) -> None:
        raise AssertionError("fleet run must not read local artifacts")

    monkeypatch.setattr(goal, "_artifact_for", fail_if_read)
    response = await client.post("/v1/goal", json={"contract": _contract(), "dispatch": "fleet"})
    run_id = response.json()["run_id"]
    fleet_env["responses"].extend([
        httpx.Response(200, json={"status": "claimed"}), httpx.Response(404),
    ])
    response = await client.get(f"/v1/goal/{run_id}/events")
    assert response.status_code == 200
    assert response.json() == {"run_id": run_id, "status": "running", "events": [], "total": 0}


async def test_fleet_refresh_clears_stale_receipt_and_keeps_original_project(
    client: httpx.AsyncClient, fleet_env: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AOS_FLEET_GOAL_PROJECT", "custom-project")
    response = await client.post("/v1/goal", json={"contract": _contract(), "dispatch": "fleet"})
    run_id = response.json()["run_id"]
    monkeypatch.setenv("AOS_FLEET_GOAL_PROJECT", "changed-project")
    fleet_env["responses"].extend([
        httpx.Response(200, json={"status": "claimed"}),
        httpx.Response(200, json={"event_id": "receipt-1"}),
        httpx.Response(200, json={"status": "completed"}), httpx.Response(403),
    ])
    first = await client.get(f"/v1/goal/{run_id}")
    assert first.json()["work_receipt"] == {"event_id": "receipt-1"}
    second = await client.get(f"/v1/goal/{run_id}")
    assert second.json()["status"] == "completed"
    assert second.json()["work_receipt"] is None
    for call in (fleet_env["calls"][2], fleet_env["calls"][4]):
        assert call["params"]["project"] == "custom-project"
