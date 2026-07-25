#!/usr/bin/env python3
"""Split the monolithic Phase 0 contract into 7 single-fix contracts.

Phase 0 bundled seven independent fixes across seven files. The executor
read the files, then refused to claim completion it could not honestly
support, and burned the loop budget refining proposals it never wrote.
Each fix below is scoped to one file and one concrete, checkable change,
matching the shape of the preflight contract that passed.
"""
import json
import pathlib

BASE = pathlib.Path("contracts/aos")
SRC = json.loads((BASE / "phase0.contract.json").read_text())

GATE = {
    "syntax_command": "npm --prefix ap-web run type-check",
    "test_command": "npm --prefix ap-web test",
    "evidence_artifact_required": False,
}

COMMON_TAIL = (
    "\n\nHOW TO WORK:\n"
    "  - Read the file with read_file before changing it. Never edit a file you "
    "have not successfully read, and never invent its contents.\n"
    "  - Make the change with a write_file call containing the complete new file "
    "contents.\n"
    "  - Then run BOTH gate commands verbatim as run_shell calls:\n"
    "      npm --prefix ap-web run type-check\n"
    "      npm --prefix ap-web test\n"
    "    Run them exactly as written. A scoped variant (adding a filename) does "
    "not satisfy the gate.\n"
    "  - Set done=true once the edit is written and both commands have been run.\n"
    "  - Change nothing outside the single file named above."
)

FIXES = [
    {
        "id": "aos-p0a-workloop-progress",
        "file": "ap-web/src/shell/WorkLoopPanel.tsx",
        "what": (
            "Remove the 'Definition of done' percentage progress bar, which is "
            "structurally unable to reach 100%. Replace it with the stages the app "
            "can actually observe. Delete the copy 'Completion stops short of 100% "
            "until the execution contract reports verifier evidence' entirely."
        ),
        "check": (
            "WorkLoopPanel.tsx contains no percentage-of-done progress bar and no "
            "'evidenced' copy."
        ),
    },
    {
        "id": "aos-p0b-workloop-counts",
        "file": "ap-web/src/shell/WorkLoopPanel.tsx",
        "what": (
            "Remove the two fabricated minimum counts. "
            "Math.max(1, receipt.artifact.changed_files.length) must report the true "
            "length including zero. Math.max(input.agentsWorking, 1) must report the "
            "true observed count including zero."
        ),
        "check": (
            "No Math.max(..., 1) remains in WorkLoopPanel.tsx for artifact or agent "
            "counts."
        ),
    },
    {
        "id": "aos-p0c-patterns-discover",
        "file": "ap-web/src/pages/PatternsPage.tsx",
        "what": (
            "Use the trailing-slash path 'api/v1/memories/?tag=discover-candidate&"
            "limit=50' for the Discover fetch. Render loading, empty and failed as "
            "three visually distinct states. A failed fetch shows a plain error line "
            "plus a Retry control, and never shows the string about the scout "
            "automaton not having shipped."
        ),
        "check": (
            "PatternsPage.tsx renders distinct loading, empty and error branches, and "
            "the error branch offers a retry."
        ),
    },
    {
        "id": "aos-p0d-training-empirical",
        "file": "ap-web/src/pages/TrainingPage.tsx",
        "what": (
            "The Details table must report the empirical pair from the payload "
            "(total_successes and total_executions) rather than presenting the "
            "smoothed avg_success_rate under a column headed 'success'."
        ),
        "check": (
            "TrainingPage.tsx surfaces total_successes and total_executions from the "
            "payload."
        ),
    },
    {
        "id": "aos-p0e-ams-parent-link",
        "file": "ap-web/src/lib/ams.ts",
        "what": (
            "Preserve the server's authoritative parent link: parent_execution_id "
            "must fall back to the top-level field when the task blob omits it, "
            "instead of overwriting it with null."
        ),
        "check": "ams.ts uses a nullish fallback to the top-level parent_execution_id.",
    },
    {
        "id": "aos-p0f-fleet-edges",
        "file": "ap-web/src/pages/FleetPage.tsx",
        "what": (
            "Show all dispatch edges by default and label the toggle for what it "
            "actually does. A failed poll marks the data stale rather than continuing "
            "to animate liveness dots from cached state."
        ),
        "check": (
            "FleetPage.tsx defaults to showing all dispatch edges and marks stale data "
            "on a failed poll."
        ),
    },
    {
        "id": "aos-p0g-system-numeric",
        "file": "ap-web/src/pages/SystemPage.tsx",
        "what": (
            "formatCell must coerce numeric strings before rounding so context_pct "
            "renders as 32.5 rather than 32.507000000000005."
        ),
        "check": "SystemPage.tsx coerces numeric strings in formatCell before rounding.",
    },
]

written = []
for i, fx in enumerate(FIXES):
    c = {
        "goal_id": fx["id"],
        "end_state": (
            f"In the single file {fx['file']}, make exactly this change:\n\n"
            f"{fx['what']}\n\n"
            f"Change nothing else, in this or any other file."
            f"{COMMON_TAIL}"
        ),
        "evidence_criteria": {**GATE, "required_files": [fx["file"]]},
        "inspection_criteria": [
            fx["check"],
            f"Only {fx['file']} was modified.",
            "No test was deleted, renamed away, or marked skip/todo/only to make the "
            "suite pass.",
            "The change is small, local, and reviewable in a diff.",
        ],
        "blocked_conditions": SRC.get("blocked_conditions", []),
        # Each fix is a fraction of the original scope, so it gets a fraction of
        # the budget. Generous enough to read, write, and run both gate commands.
        "budget": {
            "max_total_tokens": 250000,
            "max_wallclock_s": 1800,
            "max_usd": 8.0,
        },
    }
    p = BASE / f"phase0{chr(97 + i)}.contract.json"
    p.write_text(json.dumps(c, indent=2))
    written.append((p.name, fx["id"], fx["file"]))

print(f"wrote {len(written)} single-fix contracts:\n")
for name, gid, f in written:
    print(f"  {name:32} {gid:28} {f}")
