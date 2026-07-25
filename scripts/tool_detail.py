#!/usr/bin/env python3
"""Dump the first N tool results in full so we can see why the executor stalled."""
import json, sys

path = sys.argv[1]
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 6
d = json.load(open(path))

def content_of(e):
    p = e.get("payload")
    if isinstance(p, dict) and "content" in p:
        return p["content"]
    return e.get("content")

shown = 0
for i, e in enumerate(d.get("events", [])):
    c = content_of(e)
    if not isinstance(c, str) or not c.strip().startswith("{"):
        continue
    try:
        p = json.loads(c)
    except Exception:
        continue
    if not isinstance(p, dict) or "tool" not in p:
        continue
    print(f"--- event[{i}] tool={p.get('tool')} subgoal={p.get('subgoal_id')}")
    print(f"    ok       = {p.get('ok')}   exit_code = {p.get('exit_code')}")
    call = p.get("call")
    if isinstance(call, dict):
        for k, v in call.items():
            print(f"    call.{k} = {str(v)[:220]}")
    print(f"    stdout   = {str(p.get('stdout'))[:400]}")
    print(f"    stderr   = {str(p.get('stderr'))[:400]}")
    cv = p.get("contract_violations")
    if cv:
        print(f"    VIOLATIONS = {cv}")
    print(f"    contract_status = {p.get('contract_status')}")
    print()
    shown += 1
    if shown >= limit:
        break
print(f"(showed {shown} tool results)")
