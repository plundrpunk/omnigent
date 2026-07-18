"""GUI → Harness Automaton goal bridge (R9 V2).

Exposes a structured, non-terminal surface for running goal contracts
through the harness' fail-closed gate:

- ``POST /v1/goal`` — validate a goal contract, start
  ``automaton goal --contract <tmp> --workdir <dir> --json`` in the
  background, answer ``202`` with a run record.
- ``GET /v1/goal`` — list runs (optionally filtered by
  ``conversation_id``), newest first.
- ``GET /v1/goal/{run_id}`` — one run, including the parsed
  ``goal-outcome.json`` and verbatim ``blocker.md`` /
  ``checkpoint.json`` artifacts once the run is terminal.

Configuration comes from the server environment:

- ``HA_AUTOMATON_BIN`` — path to the harness-automaton CLI (e.g.
  ``…/harness-automaton/.venv/bin/automaton``). Unset ⇒ 503.
- ``HA_GOAL_CWD`` — working directory for the subprocess (the
  harness-automaton checkout). Unset ⇒ 503.
- ``HA_GOAL_WORKDIR`` — ``--workdir`` passed to the CLI (default
  ``runs-goal``); artifacts land in ``<workdir>/goal/<goal_id>/``.

Truth rules (Drew's law): the run status comes *only* from the CLI exit
code (0=completed / 3=blocked / 6=paused / 2=setup_error); outcome data
comes *only* from the CLI's JSON payload and on-disk artifacts, quoted
verbatim. Nothing is inferred, nothing is synthesized; absence is
reported as absence.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user

#: CLI exit code → run status. Anything else is an ``error`` (never a
#: success): unknown codes must not be able to read as completion.
_EXIT_STATUS: dict[int, str] = {
    0: "completed",
    2: "setup_error",
    3: "blocked",
    6: "paused",
}

#: Gate keys, at least one of which must be non-empty for a contract to
#: be accepted (mirrors the harness' own fail-closed construction).
_GATE_KEYS = (
    "required_files",
    "syntax_command",
    "test_command",
    "evidence_artifact_required",
)

_GOAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

#: Cap concurrent subprocess runs; the goal path has no streaming, so
#: each run holds a process until its gate settles.
_MAX_CONCURRENT_RUNS = 3

#: Per-artifact read cap — blocker.md / checkpoint.json / outcome are
#: quoted verbatim but must not be able to balloon a JSON response.
_ARTIFACT_CAP_BYTES = 64 * 1024
_STDERR_TAIL_CHARS = 8 * 1024


def _automaton_bin() -> str:
    return os.environ.get("HA_AUTOMATON_BIN") or ""


def _goal_cwd() -> str:
    return os.environ.get("HA_GOAL_CWD") or ""


def _goal_workdir() -> str:
    return os.environ.get("HA_GOAL_WORKDIR") or "runs-goal"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_contract(contract: Any) -> dict[str, Any]:
    """Reject obviously invalid contracts with a structured 422.

    The harness remains the authority on contract semantics; this check
    only refuses payloads that could never pass its gate (or that would
    be unsafe to splice into an artifact path).
    """
    if not isinstance(contract, dict):
        raise HTTPException(status_code=422, detail="contract must be a JSON object")
    goal_id = contract.get("goal_id")
    if not isinstance(goal_id, str) or not _GOAL_ID_RE.match(goal_id):
        raise HTTPException(
            status_code=422,
            detail=(
                "contract.goal_id must match "
                "[A-Za-z0-9][A-Za-z0-9._-]{0,127} (it names the artifact dir)"
            ),
        )
    end_state = contract.get("end_state")
    if not isinstance(end_state, str) or not end_state.strip():
        raise HTTPException(status_code=422, detail="contract.end_state must be a non-empty string")
    gate = contract.get("evidence_criteria")
    if not isinstance(gate, dict) or not any(gate.get(k) for k in _GATE_KEYS):
        raise HTTPException(
            status_code=422,
            detail=(
                "contract.evidence_criteria must set at least one of "
                f"{', '.join(_GATE_KEYS)} — an empty gate cannot fail closed"
            ),
        )
    return contract


def _parse_trailing_json(stdout: str) -> dict[str, Any]:
    """Parse the CLI's final JSON payload from mixed stdout.

    Same contract as ``ams_work_server.build_subprocess_goal_runner``:
    scan from the last line upward for the start of a JSON block; the
    ``outcome`` key is preferred when present.
    """
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                parsed = json.loads(stdout[stdout.index(line) :])
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                outcome = parsed.get("outcome")
                return outcome if isinstance(outcome, dict) else parsed
            break
    return {}


def _read_artifact(path: Path) -> str | None:
    """Read a run artifact verbatim (size-capped); ``None`` if absent."""
    try:
        if not path.is_file():
            return None
        data = path.read_bytes()[:_ARTIFACT_CAP_BYTES]
        return data.decode("utf-8", errors="replace")
    except OSError:
        return None


class GoalRunRegistry:
    """In-memory registry of goal runs for one server process.

    Runs are plain dicts (JSON-shaped end to end). V2 scope: process
    lifetime only — the durable record is the artifact directory the
    harness itself writes.
    """

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}

    def create(self, run: dict[str, Any]) -> None:
        self._runs[run["run_id"]] = run

    def get(self, run_id: str) -> dict[str, Any] | None:
        return self._runs.get(run_id)

    def list(self, conversation_id: str | None = None) -> list[dict[str, Any]]:
        runs = list(self._runs.values())
        if conversation_id is not None:
            runs = [r for r in runs if r.get("conversation_id") == conversation_id]
        return sorted(runs, key=lambda r: r["started_at"], reverse=True)

    def running_count(self) -> int:
        return sum(1 for r in self._runs.values() if r["status"] == "running")


async def _execute_goal_run(run: dict[str, Any]) -> None:
    """Run the CLI to completion and fold exit code + artifacts into ``run``."""
    contract = run["contract"]
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(contract, handle)
        contract_path = handle.name
    argv = [
        _automaton_bin(),
        "goal",
        "--contract",
        contract_path,
        "--workdir",
        _goal_workdir(),
        "--json",
    ]
    provider = run.get("provider")
    if provider:
        argv += ["--provider", provider]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=_goal_cwd(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        exit_code = proc.returncode if proc.returncode is not None else -1
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
    except OSError as exc:
        run.update(
            status="error",
            exit_code=None,
            error=f"failed to launch automaton: {exc}",
            finished_at=_now_iso(),
        )
        return
    finally:
        try:
            os.unlink(contract_path)
        except OSError:
            pass

    workdir = Path(_goal_workdir())
    if not workdir.is_absolute():
        workdir = Path(_goal_cwd()) / workdir
    artifact_dir = workdir / "goal" / contract["goal_id"]
    outcome = _parse_trailing_json(stdout)
    outcome_file = _read_artifact(artifact_dir / "goal-outcome.json")
    if outcome_file is not None:
        try:
            parsed = json.loads(outcome_file)
            if isinstance(parsed, dict):
                outcome = parsed
        except json.JSONDecodeError:
            pass  # keep the stdout-parsed payload; never invent one

    run.update(
        status=_EXIT_STATUS.get(exit_code, "error"),
        exit_code=exit_code,
        outcome=outcome or None,
        blocker_md=_read_artifact(artifact_dir / "blocker.md"),
        checkpoint=_read_artifact(artifact_dir / "checkpoint.json"),
        stderr_tail=stderr[-_STDERR_TAIL_CHARS:] or None,
        artifact_dir=str(artifact_dir),
        finished_at=_now_iso(),
    )


def create_goal_router(auth_provider: AuthProvider | None = None) -> APIRouter:
    """Build the goal-run router.

    :param auth_provider: Auth provider used to identify the requesting
        user. ``None`` in single-user mode (endpoint is open).
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()
    registry = GoalRunRegistry()
    # Exposed for tests: the registry is per-router, not module-global.
    router.state = registry  # type: ignore[attr-defined]

    def _require_configured() -> None:
        if not _automaton_bin() or not _goal_cwd():
            raise HTTPException(
                status_code=503,
                detail=(
                    "Goal bridge not configured — set HA_AUTOMATON_BIN and "
                    "HA_GOAL_CWD in the server environment."
                ),
            )

    @router.get("/goal/config")
    async def goal_config(request: Request) -> dict[str, Any]:
        """Report whether the bridge is configured (never leaks paths)."""
        require_user(request, auth_provider)
        return {"configured": bool(_automaton_bin() and _goal_cwd())}

    @router.post("/goal", status_code=202)
    async def start_goal(request: Request) -> dict[str, Any]:
        """Validate the contract and start a background goal run."""
        require_user(request, auth_provider)
        _require_configured()
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=422, detail="request body must be JSON") from None
        if not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="request body must be a JSON object")
        contract = _validate_contract(body.get("contract"))
        conversation_id = body.get("conversation_id")
        if conversation_id is not None and not isinstance(conversation_id, str):
            raise HTTPException(status_code=422, detail="conversation_id must be a string")
        provider = body.get("provider")
        if provider is not None and (
            not isinstance(provider, str) or not re.match(r"^[A-Za-z0-9._-]+$", provider)
        ):
            raise HTTPException(status_code=422, detail="provider must be a simple token")
        if registry.running_count() >= _MAX_CONCURRENT_RUNS:
            raise HTTPException(
                status_code=429,
                detail=f"Too many concurrent goal runs (max {_MAX_CONCURRENT_RUNS})",
            )
        run: dict[str, Any] = {
            "run_id": str(uuid.uuid4()),
            "goal_id": contract["goal_id"],
            "conversation_id": conversation_id,
            "provider": provider,
            "contract": contract,
            "status": "running",
            "exit_code": None,
            "outcome": None,
            "blocker_md": None,
            "checkpoint": None,
            "stderr_tail": None,
            "error": None,
            "started_at": _now_iso(),
            "finished_at": None,
        }
        registry.create(run)
        task = asyncio.create_task(_execute_goal_run(run))
        # Keep a reference so the task isn't GC'd mid-flight.
        run["_task"] = task
        task.add_done_callback(lambda _t: run.pop("_task", None))
        return _public(run)

    @router.get("/goal")
    async def list_goals(request: Request, conversation_id: str | None = None) -> dict[str, Any]:
        """List runs, newest first; empty list means exactly that."""
        require_user(request, auth_provider)
        _require_configured()
        return {"runs": [_public(r) for r in registry.list(conversation_id)]}

    @router.get("/goal/{run_id}")
    async def get_goal(run_id: str, request: Request) -> dict[str, Any]:
        """One run by id."""
        require_user(request, auth_provider)
        _require_configured()
        run = registry.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"unknown goal run: {run_id}")
        return _public(run)

    return router


def _public(run: dict[str, Any]) -> dict[str, Any]:
    """Serializable view of a run (drops the internal task handle)."""
    return {k: v for k, v in run.items() if not k.startswith("_")}
