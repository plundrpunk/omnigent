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
import stat
from pathlib import Path

import httpx
import pytest

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
    assert resp.json() == {"configured": True}


@pytest.mark.parametrize(
    "contract, fragment",
    [
        (None, "must be a JSON object"),
        ({"end_state": "x", "evidence_criteria": {"test_command": "t"}}, "goal_id"),
        ({"goal_id": "../evil", "end_state": "x", "evidence_criteria": {"test_command": "t"}}, "goal_id"),
        ({"goal_id": "g", "evidence_criteria": {"test_command": "t"}}, "end_state"),
        ({"goal_id": "g", "end_state": "x"}, "evidence_criteria"),
        ({"goal_id": "g", "end_state": "x", "evidence_criteria": {}}, "fail closed"),
        ({"goal_id": "g", "end_state": "x", "evidence_criteria": {"required_files": []}}, "fail closed"),
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
