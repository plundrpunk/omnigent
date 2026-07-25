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
- ``HA_GOAL_EXEC_ROOTS`` — ``os.pathsep``-separated absolute directories
  a run is permitted to execute in and write to. **Unset ⇒ every exec
  request is refused with 422.** A run can only ever touch a path that
  resolves (symlinks included) inside one of these roots.

Execution is opt-in per request and fail-closed by construction. Without
an ``exec`` block the CLI is invoked exactly as before — reason-only, no
shell, no writes. With one, the caller must name a workspace, and that
workspace must sit inside ``HA_GOAL_EXEC_ROOTS``; anything else is a 422
before a process is ever spawned. The resolved settings are recorded on
the run and returned by ``GET /v1/goal``, so a run can never write
somewhere the record doesn't admit to.

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

#: Sandbox modes forwarded to ``--exec-sandbox``. ``none`` is deliberately
#: absent: a bridge-started run never gets an unsandboxed shell.
_EXEC_SANDBOXES = ("subprocess", "docker")

#: Default sandbox when an exec block omits one — the stricter of the two.
_DEFAULT_EXEC_SANDBOX = "subprocess"

#: ``--command`` is a completion endpoint (a CLI or shim). A bare name or
#: an absolute path is allowed; no spaces, quotes, or shell metacharacters,
#: so a request can never smuggle arguments into the argv the harness runs.
_EXEC_COMMAND_RE = re.compile(r"^/?[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")

#: Endpoint knobs for openai-compatible / anthropic / ollama providers.
#: ``api_key_env`` is the NAME of a server-side env var — a request can
#: select which key the server uses but can never supply or read one.
_ENDPOINT_FIELDS: dict[str, str] = {
    "base_url": "--base-url",
    "api_key_env": "--api-key-env",
}

_API_KEY_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _validate_endpoint(body: dict[str, Any]) -> dict[str, str]:
    """Validate optional ``base_url`` / ``api_key_env`` overrides."""
    out: dict[str, str] = {}
    base_url = body.get("base_url")
    if base_url is not None:
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=422, detail="base_url must be an http(s) URL"
            )
        out["base_url"] = base_url
    api_key_env = body.get("api_key_env")
    if api_key_env is not None:
        if not isinstance(api_key_env, str) or not _API_KEY_ENV_RE.match(api_key_env):
            raise HTTPException(
                status_code=422,
                detail="api_key_env must be an environment variable NAME, not a key",
            )
        if api_key_env not in os.environ:
            raise HTTPException(
                status_code=422,
                detail=f"{api_key_env} is not set in the server environment",
            )
        out["api_key_env"] = api_key_env
    return out

#: Loop budgets forwarded to the CLI. The harness defaults ``max_revisions``
#: to 1 — a single failed verification ends the run — which is far too tight
#: for multi-file work, so the caller must be able to raise it.
_LIMIT_FLAGS: dict[str, str] = {
    "max_revisions": "--max-revisions",
    "max_subgoals": "--max-subgoals",
    "max_replans": "--max-replans",
    "max_loop_iterations": "--max-loop-iterations",
    "max_tool_calls": "--max-tool-calls",
    "max_tokens": "--max-tokens",
    "timeout": "--timeout",
}

#: Ceilings. A request may tune a budget but never remove it.
_LIMIT_MAX: dict[str, int] = {
    "max_revisions": 20,
    "max_subgoals": 40,
    "max_replans": 20,
    "max_loop_iterations": 200,
    "max_tool_calls": 2000,
    "max_tokens": 4_000_000,
    "timeout": 7200,
}


def _validate_limits(spec: Any) -> dict[str, int]:
    """Validate an optional ``limits`` block of positive ints under a ceiling."""
    if spec is None:
        return {}
    if not isinstance(spec, dict):
        raise HTTPException(status_code=422, detail="limits must be a JSON object")
    out: dict[str, int] = {}
    for key, value in spec.items():
        if key not in _LIMIT_FLAGS:
            raise HTTPException(
                status_code=422,
                detail=f"unknown limit {key!r}; allowed: {', '.join(sorted(_LIMIT_FLAGS))}",
            )
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise HTTPException(status_code=422, detail=f"limits.{key} must be a positive integer")
        ceiling = _LIMIT_MAX[key]
        if value > ceiling:
            raise HTTPException(
                status_code=422, detail=f"limits.{key} exceeds the ceiling of {ceiling}"
            )
        out[key] = value
    return out


def _automaton_bin() -> str:
    return os.environ.get("HA_AUTOMATON_BIN") or ""


def _goal_cwd() -> str:
    return os.environ.get("HA_GOAL_CWD") or ""


def _goal_workdir() -> str:
    return os.environ.get("HA_GOAL_WORKDIR") or "runs-goal"


def _exec_roots() -> list[Path]:
    """Absolute, symlink-resolved roots a run may execute inside.

    Empty when ``HA_GOAL_EXEC_ROOTS`` is unset — which is what makes the
    exec path fail closed: no roots configured, no execution, ever.
    """
    raw = os.environ.get("HA_GOAL_EXEC_ROOTS") or ""
    roots: list[Path] = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        try:
            roots.append(Path(part).expanduser().resolve(strict=True))
        except OSError:
            continue  # a root that doesn't exist grants nothing
    return roots


def _validate_exec(spec: Any) -> dict[str, Any] | None:
    """Validate an optional ``exec`` block; ``None`` means reason-only.

    Refuses anything the allowlist doesn't cover *before* a process is
    spawned. The returned dict carries the resolved workspace, so the
    subprocess and the run record can never disagree about it.
    """
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise HTTPException(status_code=422, detail="exec must be a JSON object")

    roots = _exec_roots()
    if not roots:
        raise HTTPException(
            status_code=422,
            detail=(
                "execution is not enabled on this server — set HA_GOAL_EXEC_ROOTS "
                "to the absolute directories a goal run may write to"
            ),
        )

    workspace = spec.get("workspace")
    if not isinstance(workspace, str) or not workspace.strip():
        raise HTTPException(
            status_code=422,
            detail="exec.workspace must be an absolute path inside an allowlisted root",
        )
    try:
        resolved = Path(workspace).expanduser().resolve(strict=True)
    except OSError:
        raise HTTPException(
            status_code=422, detail=f"exec.workspace does not exist: {workspace}"
        ) from None
    if not resolved.is_dir():
        raise HTTPException(
            status_code=422, detail=f"exec.workspace is not a directory: {workspace}"
        )
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise HTTPException(
            status_code=422,
            detail=(
                "exec.workspace is outside every allowlisted root — refusing to run. "
                "Add it to HA_GOAL_EXEC_ROOTS if this is intended."
            ),
        )

    sandbox = spec.get("sandbox", _DEFAULT_EXEC_SANDBOX)
    if sandbox not in _EXEC_SANDBOXES:
        raise HTTPException(
            status_code=422,
            detail=f"exec.sandbox must be one of {', '.join(_EXEC_SANDBOXES)}",
        )

    allow_write = spec.get("allow_write", False)
    if not isinstance(allow_write, bool):
        raise HTTPException(status_code=422, detail="exec.allow_write must be a boolean")

    command = spec.get("command")
    if command is not None and (
        not isinstance(command, str) or not _EXEC_COMMAND_RE.match(command)
    ):
        raise HTTPException(
            status_code=422,
            detail="exec.command must be a bare token (no arguments, no shell characters)",
        )

    resolved_spec: dict[str, Any] = {
        "workspace": str(resolved),
        "sandbox": sandbox,
        "allow_write": allow_write,
    }
    if command is not None:
        resolved_spec["command"] = command
    return resolved_spec


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
    model = run.get("model")
    if model:
        argv += ["--model", model]
    for key, flag in _LIMIT_FLAGS.items():
        value = (run.get("limits") or {}).get(key)
        if value:
            argv += [flag, str(value)]
    for key, flag in _ENDPOINT_FIELDS.items():
        value = (run.get("endpoint") or {}).get(key)
        if value:
            argv += [flag, str(value)]
    # Execution is opt-in. ``exec_spec`` was allowlist-validated at request
    # time, so by here the workspace is already known-safe and resolved.
    exec_spec = run.get("exec")
    if exec_spec:
        argv += [
            "--allow-exec",
            "--exec-sandbox",
            exec_spec["sandbox"],
            "--exec-workspace",
            exec_spec["workspace"],
        ]
        if exec_spec["allow_write"]:
            argv.append("--exec-allow-write")
        if exec_spec.get("command"):
            argv += ["--command", exec_spec["command"]]
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
        exec_spec = _validate_exec(body.get("exec"))
        model = body.get("model")
        if model is not None and (
            not isinstance(model, str) or not re.match(r"^[A-Za-z0-9./:_-]+$", model)
        ):
            raise HTTPException(status_code=422, detail="model must be a simple token")
        limits = _validate_limits(body.get("limits"))
        endpoint = _validate_endpoint(body)
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
            "model": model,
            "limits": limits or None,
            "endpoint": endpoint or None,
            # Recorded so the run record can never claim less access than
            # the subprocess actually got. ``None`` means reason-only.
            "exec": exec_spec,
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
