#!/usr/bin/env python3
"""Live tail of what the harness is actually doing, role by role.

The GUI shows a status pill; the detail lives in the run artifact. This
follows the newest artifact (or one you name) and prints each turn as it
lands: which role spoke, what it decided, every tool call with its exit
code, and each gate verdict.

    python3 scripts/aos_watch.py              # follow the newest run
    python3 scripts/aos_watch.py <artifact-id>
    python3 scripts/aos_watch.py --once       # print history and exit
"""
import json
import os
import sys
import time
import glob

ART_DIR = os.path.expanduser(
    "~/Documents/DevFolder/harness-automaton/runs-goal/artifacts"
)

C = {
    "planner": "\033[95m", "executor": "\033[96m", "critic": "\033[93m",
    "verifier": "\033[92m", "revisor": "\033[91m", "governor": "\033[90m",
    "synthesizer": "\033[94m", "optimizer": "\033[94m",
    "tool": "\033[97m", "dim": "\033[2m", "off": "\033[0m", "bold": "\033[1m",
}


def newest():
    files = glob.glob(os.path.join(ART_DIR, "*.json"))
    if not files:
        sys.exit(f"no artifacts in {ART_DIR}")
    return max(files, key=os.path.getmtime)


def parsed(ev):
    p = ev.get("payload")
    c = p.get("content") if isinstance(p, dict) and "content" in p else ev.get("content")
    if not isinstance(c, str):
        return None, c
    try:
        return json.loads(c), c
    except Exception:
        return None, c


def show(i, ev):
    role = ev.get("role") or (ev.get("metadata") or {}).get("phase") or "?"
    obj, raw = parsed(ev)
    col = C.get(role, "")
    head = f"{C['dim']}[{i:>3}]{C['off']} {col}{C['bold']}{role:<12}{C['off']}"

    if not isinstance(obj, dict):
        txt = (raw or "").strip().replace("\n", " ")
        print(f"{head} {txt[:150]}")
        return

    # A tool result
    if "tool" in obj and ("ok" in obj or "exit_code" in obj):
        ok = obj.get("ok")
        mark = f"{C['verifier']}OK{C['off']}" if ok else f"{C['revisor']}FAIL{C['off']}"
        call = obj.get("call") if isinstance(obj.get("call"), dict) else {}
        what = call.get("command") or call.get("path") or ""
        print(f"{head} {C['tool']}{obj.get('tool'):<11}{C['off']} {mark} "
              f"exit={obj.get('exit_code')}  {str(what)[:90]}")
        err = (obj.get("stderr") or "").strip()
        if err and not ok:
            print(f"      {C['revisor']}{err.splitlines()[0][:130]}{C['off']}")
        return

    # An executor turn
    if "done" in obj and "patch" in obj:
        calls = obj.get("tool_calls") or []
        print(f"{head} done={obj.get('done')}  patch={list(obj.get('patch') or {})[:3]}  "
              f"proposes {len(calls)} tool call(s)")
        for c_ in calls[:6]:
            if isinstance(c_, dict):
                d = c_.get("command") or c_.get("path") or ""
                print(f"      {C['dim']}-> {c_.get('tool')}: {str(d)[:90]}{C['off']}")
        obs = (obj.get("observation") or "").strip().replace("\n", " ")
        if obs:
            print(f"      {C['dim']}{obs[:140]}{C['off']}")
        return

    # A gate / verdict turn
    if "verdict" in obj or "action" in obj:
        v = obj.get("verdict") or obj.get("action")
        vc = C["verifier"] if v in ("ACCEPT", "accept") else C["revisor"]
        print(f"{head} {vc}{v}{C['off']}  sg={obj.get('subgoal_id')}")
        r = obj.get("reason")
        if r:
            r = " ".join(str(r).split())
            print(f"      {C['dim']}{r[:190]}{C['off']}")
        return

    keys = ", ".join(list(obj)[:6])
    print(f"{head} {C['dim']}{{{keys}}}{C['off']}")


def main():
    once = "--once" in sys.argv
    rest = [a for a in sys.argv[1:] if not a.startswith("-")]
    path = os.path.join(ART_DIR, f"{rest[0]}.json") if rest else newest()

    print(f"{C['bold']}watching{C['off']} {os.path.basename(path)}")
    print(f"{C['dim']}{'-' * 78}{C['off']}")
    seen = 0
    while True:
        try:
            d = json.load(open(path))
        except Exception:
            time.sleep(1)
            continue
        ev = d.get("events", [])
        for i in range(seen, len(ev)):
            show(i, ev[i])
        seen = len(ev)
        status = d.get("status")
        if once or status in ("completed", "blocked", "paused", "error"):
            print(f"{C['dim']}{'-' * 78}{C['off']}")
            print(f"{C['bold']}status: {status}{C['off']}  ({seen} events)")
            return
        time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
