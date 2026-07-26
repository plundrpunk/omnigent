#!/usr/bin/env python3
"""Finish Phase 0e by hand and teach the chain runner about phase prefixes.

e's run produced the correct parent_execution_id fallback on disk, but its
first write also lengthened one decorative comment rule by a single
character; the corrected rewrite was never re-emitted and the revision
budget died. The semantic change is three lines and fully reviewable, so:
restore the separator to HEAD's exact bytes, keep the real fix, and let
the gates decide.
"""
import pathlib
import py_compile
import subprocess

# --- 1. strip the stray separator character from e's write ----------------
target = pathlib.Path("ap-web/src/lib/ams.ts")
orig = subprocess.run(
    ["git", "show", "HEAD:ap-web/src/lib/ams.ts"],
    capture_output=True, text=True, check=True,
).stdout
cur = target.read_text(encoding="utf-8")


def sep_line(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("// ") and "LLM providers" in line:
            return line
    raise SystemExit("separator line not found")


old_line, new_line = sep_line(orig), sep_line(cur)
if old_line != new_line:
    assert cur.count(new_line) == 1
    target.write_text(cur.replace(new_line, old_line), encoding="utf-8")
    print("ams.ts: separator restored to HEAD's exact bytes; fix preserved")
else:
    print("ams.ts: separator already clean")

# --- 2. runner: phase prefix support --------------------------------------
rp = pathlib.Path("scripts/run_phase0_chain.py")
r = rp.read_text(encoding="utf-8")
if 'startswith("phase")' in r:
    print("runner: prefix support already present")
else:
    old = '    letters = sys.argv[1:] or list("bcdefg")\n'
    assert r.count(old) == 1, r.count(old)
    new = (
        "    args = sys.argv[1:]\n"
        '    prefix = next((a for a in args if a.startswith("phase")), "phase0")\n'
        '    letters = [a for a in args if not a.startswith("phase")]\n'
        "    if not letters:\n"
        "        letters = sorted(\n"
        "            p.stem[len(prefix)]\n"
        '            for p in (REPO / "contracts/aos").glob(f"{prefix}?.contract.json")\n'
        "        )\n"
    )
    r = r.replace(old, new)

    old2 = '        cpath = REPO / f"contracts/aos/phase0{letter}.contract.json"\n'
    assert r.count(old2) == 1
    r = r.replace(
        old2, '        cpath = REPO / f"contracts/aos/{prefix}{letter}.contract.json"\n'
    )

    old3 = "        JOURNAL.write_text(json.dumps(results, indent=2))\n"
    assert r.count(old3) == 1
    r = r.replace(
        old3,
        '        (REPO / f"contracts/aos/{prefix}.chain.json").write_text(\n'
        "            json.dumps(results, indent=2)\n"
        "        )\n",
    )
    rp.write_text(r, encoding="utf-8")
    py_compile.compile(str(rp), doraise=True)
    print("runner: now takes a phase prefix, e.g. run_phase0_chain.py phase1")
