#!/usr/bin/env python3
"""milack — warden-managed CLI lifecycle with 60% rollover and zero compression.

The Milack Protocol (R10, AOS_EXECUTION_PLAN_2026-07-18):
  new context window at 60% -> roll up in a warden death ritual and
  continuation, picked up by the next generation with the most
  high-leverage tokens, every time. In-place compression never happens;
  full-fidelity logs archive uncompressed.

Zero dependencies (stdlib only) so it runs under any host python.

Usage:
  python3 scripts/milack.py run \
      --agent-name milack-demo \
      --goal "Chew through the token soup" \
      --window-tokens 200000 --rollover 0.60 --max-generations 5 \
      --log-dir /tmp/milack-logs \
      -- <command that streams output> [args...]

  The wrapped command's PROMPT (goal + inherited handoff + rollup
  instruction) is written to a file whose path replaces any literal
  `{PROMPT_FILE}` argument, and is also exported as $MILACK_PROMPT_FILE.

Environment:
  AMS_BASE_URL  (e.g. https://automaton-memory.com)   [required]
  AMS_API_KEY   (sent as X-API-Key)                    [required]

Lifecycle per generation:
  birth ritual (inherits any pending continuation for this agent_id)
    -> spawn child, tee output to an uncompressed generation log
    -> heartbeat context%% (~= bytes/4 vs --window-tokens) every 10s
    -> at rollover%%: graceful stop -> extract MILACK-ROLLUP block from
       output (fallback: tagged tail) -> death ritual (saves memories,
       creates continuation) -> next generation inherits it.
Exit: child exits 0 before rollover -> final death ritual, done.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HEARTBEAT_SECS = 10
GRACE_SECS = 15
ROLLUP_RE = re.compile(r"MILACK-ROLLUP:?\s*(.{1,4000}?)\s*(?:END-ROLLUP|\Z)", re.S)

ROLLUP_INSTRUCTION = """
## Milack protocol (context lifecycle)
Your context window is managed externally: at 60% usage you will be
stopped and reborn fresh. Periodically (and ALWAYS when told to stop)
print a block of the form:

MILACK-ROLLUP:
<the most HIGH-LEVERAGE state for your successor, <=2000 chars:
 what is done, what is next (imperative), live blockers verbatim,
 decisions + reasons, pointers to artifacts. Leverage, not recency.>
END-ROLLUP

Never summarize in place; never compress your own context. Full logs
are archived for you.
"""


def _api(base: str, key: str, path: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{base.rstrip('/')}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"X-API-Key": key, "Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:  # surface AMS errors honestly
        body = exc.read().decode()[:400]
        print(f"[milack] AMS {path} -> HTTP {exc.code}: {body}", file=sys.stderr)
        return {"error": exc.code, "body": body}
    except Exception as exc:  # noqa: BLE001 — network hiccups shouldn't kill the child
        print(f"[milack] AMS {path} failed: {exc}", file=sys.stderr)
        return {"error": str(exc)}


def extract_rollup(text: str) -> tuple[str, str]:
    """Return (rollup, provenance) — explicit block or tagged-tail fallback."""
    matches = ROLLUP_RE.findall(text)
    if matches:
        return matches[-1].strip(), "extracted"
    return text[-2000:].strip(), "ambiguous-tail-fallback"


def run(args: argparse.Namespace, command: list[str]) -> int:
    base = os.environ.get("AMS_BASE_URL", "")
    key = os.environ.get("AMS_API_KEY", "")
    if not base or not key:
        print("[milack] AMS_BASE_URL and AMS_API_KEY are required", file=sys.stderr)
        return 2

    os.makedirs(args.log_dir, exist_ok=True)
    agent_id = args.agent_name
    rollover_tokens = int(args.window_tokens * args.rollover)
    handoff = ""

    for gen in range(1, args.max_generations + 1):
        # ── birth ritual: register + inherit pending continuation ──
        birth = _api(base, key, "/api/warden/birth", {
            "agent_id": agent_id,
            "agent_name": args.agent_name,
            "metadata": {"milack": True, "generation": gen,
                         "window_tokens": args.window_tokens,
                         "rollover": args.rollover},
        })
        inherited = birth.get("continuation") or {}
        if inherited:
            handoff = json.dumps(inherited, indent=2)[:3000]
        print(f"[milack] gen {gen} born (inherited continuation: {bool(inherited)})")

        # ── prompt file: goal + inheritance + protocol ──
        prompt = (
            f"## Goal\n{args.goal}\n\n"
            + (f"## Inherited handoff (from generation {gen - 1})\n{handoff}\n\n"
               if handoff else "## Inherited handoff\nNone — you are generation 1.\n\n")
            + ROLLUP_INSTRUCTION
        )
        fd, prompt_file = tempfile.mkstemp(prefix=f"milack-{agent_id}-g{gen}-", suffix=".md")
        with os.fdopen(fd, "w") as f:
            f.write(prompt)

        cmd = [prompt_file if c == "{PROMPT_FILE}" else c for c in command]
        env = dict(os.environ, MILACK_PROMPT_FILE=prompt_file,
                   MILACK_GENERATION=str(gen), MILACK_AGENT_ID=agent_id)

        log_path = os.path.join(args.log_dir, f"{agent_id}-gen{gen}.log")
        tokens = len(prompt) // 4  # inherited prompt counts against the window
        stop_reason = "exited"
        buf: list[str] = []

        with open(log_path, "w") as log:
            child = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env=env,
            )

            hb_stop = threading.Event()

            def heartbeat() -> None:
                while not hb_stop.wait(HEARTBEAT_SECS):
                    pct = min(100.0, tokens / args.window_tokens * 100)
                    _api(base, key, "/api/warden/heartbeat", {
                        "agent_id": agent_id, "agent_name": args.agent_name,
                        "status": "working", "context_pct": round(pct, 2),
                        "metadata": {"milack": True, "generation": gen},
                    })

            hb = threading.Thread(target=heartbeat, daemon=True)
            hb.start()

            assert child.stdout is not None
            for line in child.stdout:
                log.write(line)          # full fidelity, no compression
                buf.append(line)
                tokens += max(1, len(line) // 4)
                if tokens >= rollover_tokens:
                    stop_reason = "rollover"
                    print(f"[milack] gen {gen}: {tokens} tokens "
                          f"(~{tokens / args.window_tokens:.0%}) — rolling over")
                    child.send_signal(signal.SIGINT)
                    try:
                        child.wait(timeout=GRACE_SECS)
                    except subprocess.TimeoutExpired:
                        child.kill()
                    # drain what remains after the signal
                    for rest in child.stdout:
                        log.write(rest)
                        buf.append(rest)
                    break

            child.wait()
            hb_stop.set()
            hb.join(timeout=2)

        output = "".join(buf)
        rollup, provenance = extract_rollup(output)
        pct = min(100.0, tokens / args.window_tokens * 100)
        done = stop_reason == "exited" and child.returncode == 0

        # ── death ritual: memories + continuation, mark dead ──
        _api(base, key, "/api/warden/death", {
            "agent_id": agent_id,
            "original_goal": args.goal,
            "next_action": "goal complete" if done
                           else "resume from handoff and continue the goal",
            "handoff_notes": f"[{provenance}] {rollup}",
            "context_pct": round(pct, 2),
            "memories": [{
                "title": f"milack {agent_id} gen {gen} full log",
                "content": (f"Generation {gen} of {agent_id}. stop={stop_reason} "
                            f"rc={child.returncode} tokens~{tokens} "
                            f"({pct:.1f}%). Uncompressed log: {log_path}\n\n"
                            f"Rollup [{provenance}]:\n{rollup}"),
                "memory_tier": "episodic",
                "tags": ["milack", agent_id, f"gen-{gen}", provenance],
            }],
        })
        print(f"[milack] gen {gen} died ({stop_reason}, rc={child.returncode}, "
              f"~{pct:.1f}% ctx, rollup: {provenance}) — log: {log_path}")

        if done:
            print(f"[milack] goal complete after {gen} generation(s)")
            return 0
        if stop_reason == "exited":
            print(f"[milack] child exited rc={child.returncode} before rollover — "
                  "stopping (blocker, not rolling)")
            return 3
        handoff = rollup  # next generation inherits the high-leverage tokens

    print(f"[milack] max generations ({args.max_generations}) reached")
    return 6


def main() -> int:
    parser = argparse.ArgumentParser(prog="milack", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a command under milack lifecycle")
    r.add_argument("--agent-name", required=True)
    r.add_argument("--goal", required=True)
    r.add_argument("--window-tokens", type=int, default=200_000)
    r.add_argument("--rollover", type=float, default=0.60)
    r.add_argument("--max-generations", type=int, default=5)
    r.add_argument("--log-dir", default="/tmp/milack-logs")
    r.add_argument("command", nargs=argparse.REMAINDER,
                   help="-- command to run (use {PROMPT_FILE} for the prompt path)")
    args = parser.parse_args()
    command = [c for c in args.command if c != "--"]
    if not command:
        parser.error("no command given after --")
    return run(args, command)


if __name__ == "__main__":
    raise SystemExit(main())
