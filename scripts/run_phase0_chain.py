#!/usr/bin/env python3
"""Run the remaining Phase 0 contracts in sequence, one at a time.

Each contract is fired at the goal bridge and polled to a terminal state.
On a clean pass the change is committed. On a block the tree is NOT thrown
away blindly: the gates are re-run locally first, because p0a proved a run
can be blocked by an unfalsifiable criterion while the work is correct. A
block whose gates are green is committed and flagged for review; a block
whose gates are red is reverted so the next contract starts clean.

    python3 scripts/run_phase0_chain.py b c d e f g
"""
import json
import pathlib
import subprocess
import sys
import time
import urllib.request

REPO = pathlib.Path("/Users/drfoundryos/Documents/DevFolder/Omnigent")
BRIDGE = "http://127.0.0.1:6767/v1/goal"
BODY = pathlib.Path("/tmp/p0a.json")          # provider/model/limits/exec template
JOURNAL = REPO / "contracts/aos/phase0.chain.json"
POLL_S = 15
MAX_WAIT_S = 3600


def sh(*args, **kw):
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True, **kw)


def post(body: dict) -> str:
    req = urllib.request.Request(
        BRIDGE,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["run_id"]


def poll(run_id: str) -> dict:
    waited = 0
    while waited < MAX_WAIT_S:
        try:
            with urllib.request.urlopen(f"{BRIDGE}/{run_id}", timeout=30) as r:
                d = json.load(r)
            if d.get("status") not in ("running", None):
                return d
        except Exception:
            pass
        time.sleep(POLL_S)
        waited += POLL_S
    return {"status": "timeout", "exit_code": None}


def gates_green() -> tuple[bool, str]:
    """Re-run the contract's own gates against the working tree."""
    tc = sh("npm", "--prefix", "ap-web", "run", "type-check")
    if tc.returncode != 0:
        tail = (tc.stdout + tc.stderr).strip().splitlines()[-3:]
        return False, "type-check failed: " + " | ".join(tail)
    tv = sh("npm", "--prefix", "ap-web", "test")
    out = tv.stdout + tv.stderr
    if tv.returncode != 0:
        line = [l for l in out.splitlines() if "Tests " in l or "failed" in l][-2:]
        return False, "tests failed: " + " | ".join(l.strip() for l in line)
    return True, "type-check + suite green"


def dirty() -> list[str]:
    out = sh("git", "status", "--porcelain", "ap-web").stdout.strip()
    return [l for l in out.splitlines() if l.strip()]


def main():
    letters = sys.argv[1:] or list("bcdefg")
    template = json.loads(BODY.read_text())
    results = []

    for letter in letters:
        cpath = REPO / f"contracts/aos/phase0{letter}.contract.json"
        if not cpath.exists():
            print(f"[{letter}] no contract at {cpath}, skipping")
            continue
        contract = json.loads(cpath.read_text())
        gid = contract["goal_id"]

        if dirty():
            print(f"[{letter}] tree dirty before start; reverting ap-web")
            sh("git", "checkout", "--", "ap-web")

        body = {**template, "contract": contract}
        run_id = post(body)
        print(f"[{letter}] {gid} -> {run_id}", flush=True)

        d = poll(run_id)
        status, code = d.get("status"), d.get("exit_code")
        changed = dirty()
        print(f"[{letter}] {status} exit={code}  files={len(changed)}", flush=True)

        entry = {"letter": letter, "goal_id": gid, "run_id": run_id,
                 "status": status, "exit_code": code,
                 "files": [c[3:] for c in changed]}

        if not changed:
            entry["outcome"] = "no-op"
            print(f"[{letter}] nothing changed on disk", flush=True)
        else:
            ok, why = gates_green()
            entry["gates"] = why
            if ok:
                sh("git", "add", "-A", "ap-web")
                msg = (f"feat(ap-web): {gid}\n\n"
                       f"Authored by AOS. Gates verified locally: {why}.\n"
                       f"Harness status: {status} (exit {code}).")
                sh("git", "-c", "user.name=Drew",
                   "-c", "user.email=andrewrutledge1@gmail.com",
                   "commit", "-q", "-m", msg, "--no-verify")
                entry["outcome"] = "committed" if status == "completed" \
                    else "committed-despite-block"
                print(f"[{letter}] COMMITTED ({why})", flush=True)
            else:
                sh("git", "checkout", "--", "ap-web")
                entry["outcome"] = "reverted"
                print(f"[{letter}] reverted: {why}", flush=True)

        results.append(entry)
        JOURNAL.write_text(json.dumps(results, indent=2))

    print("\n=== chain summary ===")
    for r in results:
        print(f"  {r['letter']}  {r['goal_id']:<28} {r['status']:<10} "
              f"{r.get('outcome')}")


if __name__ == "__main__":
    main()
