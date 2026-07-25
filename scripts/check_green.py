#!/usr/bin/env python3
"""Why did _has_green_result never match? Compare what the gate wants to what ran."""
import json, sys

d = json.load(open(sys.argv[1]))

def norm(c):
    return " ".join(str(c).split())

wanted = "test -s EXEC_PREFLIGHT.txt"
found = []

def walk(o):
    if isinstance(o, dict):
        if o.get("tool") == "run_shell":
            call = o.get("call")
            cmd = ""
            if isinstance(call, dict):
                cmd = call.get("command")
            cmd2 = o.get("command")
            found.append({
                "ok": o.get("ok"),
                "call.command": cmd,
                "call.command_type": type(cmd).__name__,
                "result.command": cmd2,
                "call_id": o.get("call_id") or o.get("id"),
                "exit_code": o.get("exit_code"),
            })
        for v in o.values():
            walk(v)
    elif isinstance(o, list):
        for v in o:
            walk(v)

# events carry JSON-encoded strings; decode them too
def deep(o, depth=0):
    if depth > 6:
        return
    walk(o)
    if isinstance(o, dict):
        for v in o.values():
            if isinstance(v, str) and v.strip().startswith(("{", "[")):
                try:
                    deep(json.loads(v), depth + 1)
                except Exception:
                    pass
            else:
                deep(v, depth + 1) if isinstance(v, (dict, list)) else None
    elif isinstance(o, list):
        for v in o:
            deep(v, depth + 1) if isinstance(v, (dict, list, str)) else None

deep(d)

print(f"wanted (normalized): {norm(wanted)!r}\n")
seen = set()
for f in found:
    key = json.dumps(f, sort_keys=True, default=str)
    if key in seen:
        continue
    seen.add(key)
    cc = f["call.command"]
    print(f"ok={f['ok']} exit={f['exit_code']} call_id={f['call_id']}")
    print(f"  call.command  = {cc!r}  ({f['call.command_type']})")
    print(f"  normalized    = {norm(cc)!r}")
    print(f"  MATCHES GATE? {norm(cc) == norm(wanted)}")
    print()
print(f"total run_shell results seen: {len(seen)}")
