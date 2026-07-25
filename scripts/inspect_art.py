#!/usr/bin/env python3
"""Show what the executor actually emitted, so we can see why `done` never lands."""
import json, sys, re

path = sys.argv[1]
d = json.load(open(path))
ev = d.get("events", [])
print(f"events: {len(ev)}")
print(f"artifact status: {d.get('status')}")
print()

def content_of(e):
    p = e.get("payload")
    if isinstance(p, dict) and "content" in p:
        return p["content"]
    return e.get("content")

for i, e in enumerate(ev):
    c = content_of(e)
    if not isinstance(c, str):
        continue
    s = c.strip()
    if not s.startswith("{"):
        continue
    try:
        parsed = json.loads(s)
    except Exception:
        continue
    if not isinstance(parsed, dict):
        continue
    keys = set(parsed.keys())
    # executor turns are the ones carrying a patch / done / tool intent
    if keys & {"done", "patch", "tool_calls", "subgoal_id", "notes"}:
        print(f"[{i}] keys={sorted(keys)}")
        print(f"    done = {parsed.get('done')!r}  (type {type(parsed.get('done')).__name__})")
        for k in ("subgoal_id", "reason", "notes"):
            if k in parsed:
                print(f"    {k} = {str(parsed[k])[:160]}")
        if "patch" in parsed:
            print(f"    patch keys = {list(parsed['patch'])[:5]}")
        print()
