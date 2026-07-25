#!/usr/bin/env python3
"""Add GET /v1/goal/{run_id}/events to the goal bridge.

The GUI could only show a status pill while the whole story - which role
spoke, what it decided, every tool call and its exit code - sat in the run
artifact on disk. This normalises those events into the same shape the
terminal watcher prints, so the panel can render them live.
"""
import pathlib
import py_compile
import tempfile

p = pathlib.Path("omnigent/server/routes/goal.py")
s = p.read_text(encoding="utf-8")

if "goal/{run_id}/events" in s:
    print("already applied")
    raise SystemExit(0)

HELPERS = '''

#: A single tool result can carry a whole source file; the panel only needs
#: enough to say what ran and whether it worked.
_EVENT_TEXT_CAP = 400
_MAX_EVENTS = 400


def _artifact_for(run: dict[str, Any]) -> Path | None:
    """Locate a run's artifact, during the run as well as after it.

    A finished run names its artifact in the CLI outcome. A running one does
    not yet, so fall back to the newest artifact touched since the run began.
    """
    outcome = run.get("outcome") or {}
    if isinstance(outcome, dict):
        raw = outcome.get("artifact_path")
        if raw:
            candidate = Path(str(raw))
            if candidate.is_file():
                return candidate
    art_dir = Path(_goal_workdir()) / "artifacts"
    if not art_dir.is_dir():
        return None
    files = [f for f in art_dir.glob("*.json") if f.is_file()]
    if not files:
        return None
    return max(files, key=lambda f: f.stat().st_mtime)


def _clip(value: Any, cap: int = _EVENT_TEXT_CAP) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= cap else text[: cap - 1] + "\\u2026"


def _normalise_event(index: int, event: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten one artifact event into something the panel can render."""
    role = event.get("role") or (event.get("metadata") or {}).get("phase") or "?"
    payload = event.get("payload")
    raw = payload.get("content") if isinstance(payload, Mapping) else None
    raw = raw if raw is not None else event.get("content")

    out: dict[str, Any] = {"index": index, "role": str(role), "kind": "note"}

    parsed: Any = None
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None

    if not isinstance(parsed, dict):
        out["summary"] = _clip(raw or "")
        return out

    # A tool result: what ran, and did it work.
    if "tool" in parsed and ("ok" in parsed or "exit_code" in parsed):
        call = parsed.get("call") if isinstance(parsed.get("call"), Mapping) else {}
        out.update(
            kind="tool",
            tool=str(parsed.get("tool") or ""),
            ok=bool(parsed.get("ok")),
            exit_code=parsed.get("exit_code"),
            target=_clip(call.get("command") or call.get("path") or "", 200),
        )
        if not parsed.get("ok"):
            out["error"] = _clip(parsed.get("stderr") or "", 200)
        return out

    # An executor turn: did it propose real work.
    if "done" in parsed and "patch" in parsed:
        calls = parsed.get("tool_calls") or []
        out.update(
            kind="executor",
            done=bool(parsed.get("done")),
            patch_keys=[str(k) for k in (parsed.get("patch") or {})][:6],
            proposed=[
                {
                    "tool": str(c.get("tool") or ""),
                    "target": _clip(c.get("command") or c.get("path") or "", 160),
                }
                for c in calls
                if isinstance(c, Mapping)
            ][:6],
            summary=_clip(parsed.get("observation") or ""),
        )
        return out

    # A gate decision.
    verdict = parsed.get("verdict") or parsed.get("action")
    if verdict:
        out.update(
            kind="verdict",
            verdict=str(verdict),
            subgoal=parsed.get("subgoal_id"),
            summary=_clip(parsed.get("reason") or ""),
        )
        return out

    out["summary"] = _clip(", ".join(list(parsed)[:6]))
    return out

'''

ROUTE = '''    @router.get("/goal/{run_id}/events")
    async def get_goal_events(
        run_id: str, request: Request, since: int = 0
    ) -> dict[str, Any]:
        """Normalised run events, for live rendering in the work-loop panel.

        ``since`` is an event index, so the client can poll for the tail
        rather than re-fetching the whole run each time.
        """
        require_user(request, auth_provider)
        _require_configured()
        run = registry.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"unknown goal run: {run_id}")

        artifact = _artifact_for(run)
        if artifact is None:
            return {"run_id": run_id, "status": run.get("status"),
                    "events": [], "total": 0}
        try:
            data = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A run writes its artifact continuously; a torn read is normal.
            return {"run_id": run_id, "status": run.get("status"),
                    "events": [], "total": 0}

        raw_events = data.get("events") or []
        start = max(0, int(since))
        window = raw_events[start : start + _MAX_EVENTS]
        return {
            "run_id": run_id,
            "status": run.get("status"),
            "artifact_id": data.get("id"),
            "total": len(raw_events),
            "events": [
                _normalise_event(start + i, e)
                for i, e in enumerate(window)
                if isinstance(e, Mapping)
            ],
        }

    return router'''

assert s.count("    return router\n") == 1
s = s.replace("    return router\n", ROUTE + "\n")

anchor = 'def _public(run: dict[str, Any]) -> dict[str, Any]:'
assert s.count(anchor) == 1
s = s.replace(anchor, HELPERS.lstrip("\n") + "\n" + anchor)

# imports the helpers rely on
if "from collections.abc import Mapping" not in s:
    s = s.replace("import json\n", "import json\nfrom collections.abc import Mapping\n", 1)
if "from pathlib import Path" not in s:
    s = s.replace("import json\n", "import json\nfrom pathlib import Path\n", 1)

with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as t:
    t.write(s)
    tmp = t.name
py_compile.compile(tmp, doraise=True)
p.write_text(s, encoding="utf-8")
print("added GET /v1/goal/{run_id}/events")
