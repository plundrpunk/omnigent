#!/usr/bin/env python3
"""Self-evolving runner for the AOS simplification plan.

Drives a chain of goal contracts through the AOS goal bridge
(``POST /v1/goal``), advancing to the next phase only when the harness
gate actually passes. State is journalled to disk after every transition,
so the chain survives a server restart, a laptop sleep, or being killed
mid-phase — unlike the bridge's own registry, which is in-memory only.

Truth rules, inherited from the bridge and kept here:

- A phase advances **only** on exit 0. Nothing else counts as success.
- Exit 3 (blocked) is not retried blindly. The blocker's own gate
  violations are folded into a *narrower* remediation contract, which is
  run in place of a naive retry. That is the "self-evolving" step: the
  chain learns what it failed at and asks a smaller question next.
- Exit 6 (paused) and exit 2 (setup error) stop the chain. A human reads
  the checkpoint.
- Nothing is inferred. Every status in the journal came from an exit code.

Usage
-----
    python3 scripts/aos_chain.py contracts/aos/chain.json            # run
    python3 scripts/aos_chain.py contracts/aos/chain.json --status   # inspect
    python3 scripts/aos_chain.py contracts/aos/chain.json --reset    # start over
    python3 scripts/aos_chain.py contracts/aos/chain.json --dry-run  # plan only

The journal lives next to the chain file as ``<chain_id>.journal.json``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

POLL_SECONDS = 15
TERMINAL = {"completed", "blocked", "paused", "setup_error", "error"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- http


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"content-type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"POST {url} -> HTTP {exc.code}: {body[:600]}") from None


def get_json(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"GET {url} -> HTTP {exc.code}: {body[:600]}") from None


# ------------------------------------------------------------- journal


class Journal:
    """Append-only record of what actually happened, persisted every write."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )
        self.data.setdefault("chain_id", None)
        self.data.setdefault("started_at", now())
        self.data.setdefault("cursor", 0)
        self.data.setdefault("entries", [])

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def record(self, **fields: Any) -> None:
        self.data["entries"].append({"at": now(), **fields})
        self.save()

    @property
    def cursor(self) -> int:
        return int(self.data["cursor"])

    @cursor.setter
    def cursor(self, value: int) -> None:
        self.data["cursor"] = value
        self.save()

    def remediations_for(self, goal_id: str) -> int:
        return sum(
            1
            for e in self.data["entries"]
            if e.get("kind") == "remediation" and e.get("parent_goal_id") == goal_id
        )


# ------------------------------------------------------ self-evolution


def author_remediation(contract: dict[str, Any], blocker_md: str, attempt: int) -> dict[str, Any]:
    """Derive a narrower contract from what the gate actually complained about.

    This is deliberately mechanical, not clever: the violations are quoted
    verbatim into the end_state so the next run is told precisely what it
    failed, and the gate is left untouched so it cannot be softened to
    manufacture a pass.
    """
    violations: list[str] = []
    in_section = False
    for line in (blocker_md or "").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## gate violations"):
            in_section = True
            continue
        if in_section:
            if stripped.startswith("##"):
                break
            if stripped.startswith("- "):
                violations.append(stripped[2:].strip())

    reason = ""
    for line in (blocker_md or "").splitlines():
        if line.strip().startswith("- reason:"):
            reason = line.split(":", 1)[1].strip()
            break

    quoted = "\n".join(f"  - {v}" for v in violations) or "  - (none reported)"
    remediated = dict(contract)
    remediated["goal_id"] = f"{contract['goal_id']}-fix{attempt}"
    remediated["end_state"] = (
        f"{contract['end_state']}\n\n"
        f"PRIOR ATTEMPT FAILED ITS GATE. Do not restate the plan; close these "
        f"specific gaps and nothing else.\n"
        f"Reported reason: {reason or 'not reported'}\n"
        f"Gate violations to clear:\n{quoted}\n"
        f"Leave every already-satisfied part of the end state alone. Do not "
        f"weaken, delete or skip any test in order to pass."
    )
    meta = dict(contract.get("metadata") or {})
    meta.update(
        {
            "remediation_of": contract["goal_id"],
            "attempt": attempt,
            "violations": violations,
        }
    )
    remediated["metadata"] = meta
    return remediated


# ------------------------------------------------------------- running


def run_phase(cfg: dict[str, Any], contract: dict[str, Any], root: Path) -> dict[str, Any]:
    """Start one goal run and block until the bridge reports it terminal."""
    d = cfg["defaults"]
    payload: dict[str, Any] = {"contract": contract}
    for key in ("provider", "model", "exec", "limits"):
        if d.get(key):
            payload[key] = d[key]

    started = post_json(f"{cfg['server']}/v1/goal", payload)
    run_id = started["run_id"]
    log(f"  started {contract['goal_id']} -> run {run_id}")

    while True:
        time.sleep(POLL_SECONDS)
        run = get_json(f"{cfg['server']}/v1/goal/{run_id}")
        status = run.get("status")
        if status in TERMINAL:
            log(f"  {contract['goal_id']}: {status} (exit {run.get('exit_code')})")
            return run
        log(f"  {contract['goal_id']}: {status}…")


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True
    ).stdout.strip()


def commit_phase(root: Path, goal_id: str) -> str | None:
    """Commit whatever the phase produced. Returns the sha, or None if clean."""
    if not git(root, "status", "--porcelain"):
        return None
    subprocess.run(["git", "add", "-A"], cwd=root, check=False)
    subprocess.run(
        ["git", "commit", "-m", f"{goal_id}: gate passed", "--no-verify"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    return git(root, "rev-parse", "--short", "HEAD")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("chain", type=Path)
    ap.add_argument("--status", action="store_true", help="print the journal and exit")
    ap.add_argument("--reset", action="store_true", help="discard the journal")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    args = ap.parse_args()

    chain_path = args.chain.resolve()
    cfg = json.loads(chain_path.read_text(encoding="utf-8"))
    root = Path(cfg["workspace"])
    journal_path = chain_path.with_name(f"{cfg['chain_id']}.journal.json")

    if args.reset and journal_path.exists():
        journal_path.unlink()
        log("journal reset")

    j = Journal(journal_path)
    j.data["chain_id"] = cfg["chain_id"]
    j.save()

    if args.status:
        print(json.dumps(j.data, indent=2))
        return 0

    phases = cfg["phases"]
    if args.dry_run:
        for i, ph in enumerate(phases):
            c = json.loads((root / ph["contract"]).read_text(encoding="utf-8"))
            mark = "done" if i < j.cursor else ("next" if i == j.cursor else "queued")
            print(f"  [{mark:6}] {i}. {c['goal_id']}")
        return 0

    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch != cfg.get("branch"):
        log(f"REFUSING: workspace is on '{branch}', chain expects '{cfg['branch']}'")
        return 2

    log(f"chain {cfg['chain_id']} — {len(phases)} phases, resuming at {j.cursor}")

    while j.cursor < len(phases):
        phase = phases[j.cursor]
        contract = json.loads((root / phase["contract"]).read_text(encoding="utf-8"))
        goal_id = contract["goal_id"]
        log(f"phase {j.cursor}: {goal_id}")

        run = run_phase(cfg, contract, root)
        status = run.get("status")
        j.record(
            kind="phase",
            index=j.cursor,
            goal_id=goal_id,
            run_id=run.get("run_id"),
            status=status,
            exit_code=run.get("exit_code"),
            artifact_dir=run.get("artifact_dir"),
        )

        if status == "completed":
            sha = commit_phase(root, goal_id) if phase.get("commit_on_pass") else None
            j.record(kind="commit", goal_id=goal_id, sha=sha)
            log(f"  gate passed{f' — committed {sha}' if sha else ' (no changes)'}")
            j.cursor = j.cursor + 1
            continue

        if status == "blocked" and cfg.get("on_blocked", {}).get("author_remediation"):
            attempts = j.remediations_for(goal_id)
            cap = int(cfg["on_blocked"].get("max_remediations_per_phase", 2))
            if attempts < cap:
                fix = author_remediation(contract, run.get("blocker_md") or "", attempts + 1)
                out = root / phase["contract"].replace(".contract.json", f"-fix{attempts + 1}.contract.json")
                out.write_text(json.dumps(fix, indent=2), encoding="utf-8")
                j.record(
                    kind="remediation",
                    parent_goal_id=goal_id,
                    goal_id=fix["goal_id"],
                    attempt=attempts + 1,
                    contract=str(out.relative_to(root)),
                    violations=fix["metadata"].get("violations", []),
                )
                log(f"  blocked — authored remediation {fix['goal_id']}, retrying narrower")
                fix_run = run_phase(cfg, fix, root)
                j.record(
                    kind="phase",
                    index=j.cursor,
                    goal_id=fix["goal_id"],
                    run_id=fix_run.get("run_id"),
                    status=fix_run.get("status"),
                    exit_code=fix_run.get("exit_code"),
                )
                if fix_run.get("status") == "completed":
                    sha = commit_phase(root, fix["goal_id"]) if phase.get("commit_on_pass") else None
                    j.record(kind="commit", goal_id=fix["goal_id"], sha=sha)
                    log(f"  remediation passed{f' — committed {sha}' if sha else ''}")
                    j.cursor = j.cursor + 1
                    continue

        log(f"  stopping — {goal_id} ended {status}")
        log(f"  artifacts: {run.get('artifact_dir')}")
        if run.get("blocker_md"):
            log("  --- blocker ---")
            print(run["blocker_md"][:2000])
        return 1

    log("chain complete — every phase passed its gate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
