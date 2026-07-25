#!/usr/bin/env python3
"""Does a large write_file response survive the codex shim intact?

The executor's response came back as bare {"done":true} with the write
payload gone. That is either an output-size limit somewhere in the codex
path, or a JSON-assembly bug in the shim. This asks for a response of a
known large size and reports exactly what comes back.
"""
import json
import subprocess
import sys

SHIM = "/Users/drfoundryos/Documents/DevFolder/Omnigent/scripts/aos-codex-shim"
LINES = int(sys.argv[1]) if len(sys.argv) > 1 else 260

prompt = (
    "Reply with ONLY a single JSON object and no prose, no markdown fence. "
    'Shape: {"done": true, "tool_calls": [{"tool": "write_file", '
    '"path": "probe.txt", "content": "<C>"}]}\n'
    f"<C> must be the literal text 'line N of the payload' for N from 1 to {LINES}, "
    "each on its own line, separated by \\n escapes inside the JSON string."
)

payload = {
    "model": "gpt-5.6-sol:medium",
    "messages": [{"role": "user", "content": prompt}],
}

proc = subprocess.run(
    ["python3", SHIM],
    input=json.dumps(payload),
    capture_output=True,
    text=True,
    timeout=600,
)

out = proc.stdout
print(f"exit code       : {proc.returncode}")
print(f"stdout bytes    : {len(out)}")
print(f"stderr (tail)   : {proc.stderr[-200:]!r}")
print()

if not out.strip():
    print("EMPTY RESPONSE — shim returned nothing")
    sys.exit(1)

print(f"first 120 chars : {out[:120]!r}")
print(f"last 120 chars  : {out[-120:]!r}")
print()

try:
    obj = json.loads(out)
except Exception as exc:
    print(f"NOT VALID JSON  : {exc}")
    print("=> response was cut off mid-structure (size limit)")
    sys.exit(2)

print(f"parsed OK, keys : {list(obj)}")
calls = obj.get("tool_calls") or []
print(f"tool_calls      : {len(calls)}")
if calls:
    c = calls[0].get("content", "")
    n = c.count("\n") + 1 if c else 0
    print(f"content bytes   : {len(c)}")
    print(f"content lines   : {n} (asked for {LINES})")
    print()
    if n >= LINES:
        print("VERDICT: large write payload survives the shim intact.")
    else:
        print(f"VERDICT: payload TRUNCATED — got {n} of {LINES} lines.")
else:
    print()
    print("VERDICT: tool_calls MISSING — this reproduces the bare-{done} failure.")
