#!/usr/bin/env python3
"""Course corrections before the next autonomous runs.

1. split_phase0.py — e stalled because a missing expected file was an
   unresolvable dead end; f later proved the fix by deriving a run_shell
   find on its own. Teach every contract that trick up front.
2. run_phase0_chain.py — a blocked run with green gates is no longer
   committed. Green gates prove the change compiles and passes tests, not
   that it satisfies the contract; the diff is parked for human review and
   the tree left clean for the next contract. (f went through the old
   commit-despite-block path and survived hand review, but the policy
   should not depend on that.)
"""
import pathlib
import py_compile

# --- 1. splitter: missing-file search hint --------------------------------
sp = pathlib.Path("scripts/split_phase0.py")
s = sp.read_text(encoding="utf-8")
if "provable fact" in s:
    print("splitter: already applied")
else:
    lines = s.splitlines(keepends=True)
    idx = next(i for i, ln in enumerate(lines) if "Change nothing else." in ln)
    hint = '''    "  - A missing file is a provable fact, not a dead end. If an expected file "
    "(such as a co-located test) cannot be read, prove its presence or absence "
    "with run_shell: find ap-web/src/<dir> -maxdepth 1 -name <Stem>*.test.tsx "
    "-print (empty output is positive evidence of absence), and use grep -rln "
    "<symbol> ap-web/src to locate related coverage elsewhere. Never let an "
    "unresolved read loop the run.\\n"
'''
    lines.insert(idx + 1, hint)
    sp.write_text("".join(lines), encoding="utf-8")
    py_compile.compile(str(sp), doraise=True)
    print("splitter: missing-file search hint added")

# --- 2. runner: park blocked-but-green diffs instead of committing --------
rp = pathlib.Path("scripts/run_phase0_chain.py")
r = rp.read_text(encoding="utf-8")
if "parked-for-review" in r:
    print("runner: already applied")
else:
    old_if = "            if ok:\n"
    assert r.count(old_if) == 1, r.count(old_if)
    r = r.replace(old_if, '            if ok and status == "completed":\n')

    old_outcome = (
        '                entry["outcome"] = "committed" if status == "completed" \\\n'
        '                    else "committed-despite-block"\n'
    )
    assert r.count(old_outcome) == 1, r.count(old_outcome)
    r = r.replace(old_outcome, '                entry["outcome"] = "committed"\n')

    old_else = (
        "            else:\n"
        '                sh("git", "checkout", "--", "ap-web")\n'
        '                entry["outcome"] = "reverted"\n'
        '                print(f"[{letter}] reverted: {why}", flush=True)\n'
    )
    assert r.count(old_else) == 1, r.count(old_else)
    new_else = (
        "            elif ok:\n"
        "                # Green gates prove the change compiles and passes tests,\n"
        "                # not that it satisfies the contract. Park a blocked\n"
        "                # run's diff for human review instead of committing it.\n"
        '                review_dir = REPO / "contracts/aos/review"\n'
        "                review_dir.mkdir(parents=True, exist_ok=True)\n"
        '                (review_dir / f"{gid}.patch").write_text(\n'
        '                    sh("git", "diff", "ap-web").stdout\n'
        "                )\n"
        '                sh("git", "checkout", "--", "ap-web")\n'
        '                entry["outcome"] = "parked-for-review"\n'
        '                print(f"[{letter}] parked for review: {why}", flush=True)\n'
        "            else:\n"
        '                sh("git", "checkout", "--", "ap-web")\n'
        '                entry["outcome"] = "reverted"\n'
        '                print(f"[{letter}] reverted: {why}", flush=True)\n'
    )
    r = r.replace(old_else, new_else)
    rp.write_text(r, encoding="utf-8")
    py_compile.compile(str(rp), doraise=True)
    print("runner: blocked-but-green now parks for review, never auto-commits")
