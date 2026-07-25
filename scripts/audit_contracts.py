#!/usr/bin/env python3
"""Check the phase contracts for the two defects that just blocked the preflight."""
import json, glob, os

NEG = ("no other", "nothing else", "was not modified", "absence of")

for f in sorted(glob.glob("contracts/aos/*.json")):
    name = os.path.basename(f)
    if name == "chain.json":
        continue
    c = json.load(open(f))
    ec = c.get("evidence_criteria", {}) or {}
    insp = c.get("inspection_criteria", []) or []
    neg = [x for x in insp if any(n in x.lower() for n in NEG)]
    print(name)
    print(f"  evidence_artifact_required = {ec.get('evidence_artifact_required')}")
    print(f"  syntax_command             = {ec.get('syntax_command')!r}")
    print(f"  required_files             = {ec.get('required_files')}")
    print(f"  negative-proof criteria    = {len(neg)}")
    for n in neg:
        print(f"      ! {n[:100]}")
    print()
