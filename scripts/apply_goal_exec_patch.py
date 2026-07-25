#!/usr/bin/env python3
"""Apply the AOS goal-bridge exec patch to omnigent/server/routes/goal.py.

Idempotent: refuses to double-apply. Every replacement must match exactly
once or the script aborts without touching the file.
"""
import sys, pathlib, py_compile, tempfile, shutil

P = pathlib.Path.home() / "Documents/DevFolder/Omnigent/omnigent/server/routes/goal.py"
src = P.read_text(encoding="utf-8")

if "HA_GOAL_EXEC_ROOTS" in src:
    print("ALREADY APPLIED — no change"); sys.exit(0)

EDITS = []

EDITS.append((
"""- ``HA_GOAL_WORKDIR`` — ``--workdir`` passed to the CLI (default
  ``runs-goal``); artifacts land in ``<workdir>/goal/<goal_id>/``.

Truth rules (Drew's law):""",
"""- ``HA_GOAL_WORKDIR`` — ``--workdir`` passed to the CLI (default
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

Truth rules (Drew's law):"""))

EDITS.append((
'''_ARTIFACT_CAP_BYTES = 64 * 1024
_STDERR_TAIL_CHARS = 8 * 1024


def _automaton_bin() -> str:''',
'''_ARTIFACT_CAP_BYTES = 64 * 1024
_STDERR_TAIL_CHARS = 8 * 1024

#: Sandbox modes forwarded to ``--exec-sandbox``. ``none`` is deliberately
#: absent: a bridge-started run never gets an unsandboxed shell.
_EXEC_SANDBOXES = ("subprocess", "docker")

#: Default sandbox when an exec block omits one — the stricter of the two.
_DEFAULT_EXEC_SANDBOX = "subprocess"

#: ``--command`` is a delegation target (a coding CLI). Keep it a bare
#: token so a request can never smuggle arguments or shell metacharacters.
_EXEC_COMMAND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _automaton_bin() -> str:'''))

EDITS.append((
'''def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()''',
'''def _exec_roots() -> list[Path]:
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
    return datetime.now(timezone.utc).isoformat()'''))

EDITS.append((
'''    provider = run.get("provider")
    if provider:
        argv += ["--provider", provider]
    try:
        proc = await asyncio.create_subprocess_exec(''',
'''    provider = run.get("provider")
    if provider:
        argv += ["--provider", provider]
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
        proc = await asyncio.create_subprocess_exec('''))

EDITS.append((
'''            raise HTTPException(status_code=422, detail="provider must be a simple token")
        if registry.running_count()''',
'''            raise HTTPException(status_code=422, detail="provider must be a simple token")
        exec_spec = _validate_exec(body.get("exec"))
        if registry.running_count()'''))

EDITS.append((
'''            "conversation_id": conversation_id,
            "provider": provider,
            "contract": contract,''',
'''            "conversation_id": conversation_id,
            "provider": provider,
            # Recorded so the run record can never claim less access than
            # the subprocess actually got. ``None`` means reason-only.
            "exec": exec_spec,
            "contract": contract,'''))

out = src
for i, (old, new) in enumerate(EDITS, 1):
    n = out.count(old)
    if n != 1:
        print(f"ABORT: edit {i} matched {n} times (expected exactly 1)"); sys.exit(1)
    out = out.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as t:
    t.write(out); tmp = t.name
try:
    py_compile.compile(tmp, doraise=True)
except py_compile.PyCompileError as e:
    print("ABORT: patched file does not compile:", e); sys.exit(1)

shutil.copy2(P, str(P) + ".bak")
P.write_text(out, encoding="utf-8")
print(f"PATCHED OK — {len(EDITS)} edits, {src.count(chr(10))} -> {out.count(chr(10))} lines")
print(f"backup at {P}.bak")
