#!/usr/bin/env python3
"""Split retired phase1 (plain English + 3-item nav) into 12 single-fix contracts.

The monolithic phase1 bundled four concerns across eleven files - the exact
shape that spun for 85 events in phase 0 before the split. This decomposes it
with every lesson from phase 0 baked in, ordered so the tree passes both
gates after every contract:

  a   create lib/copy.ts (no consumers yet - trivially green)
  b   ams.ts stops leaking raw error detail
  c-h six operator pages: vocabulary + plain error + Retry (Models also
      gains the keyboard-operable role picker)
  i   PageShell vocabulary
  j   WorkLoopPanel vocabulary
  k   AdvancedPage + /advanced route (additive - no route removed)
  l   Sidebar nav collapses to Do / Work / Advanced (60KB file - fire this
      one with a raised max_tokens)

Brand invariant is a blocked condition on every contract: wordmark, wolf,
gold-on-dark, and the "Let's do this thing!" hero are untouchable.
"""
import json
import pathlib

BASE = pathlib.Path("contracts/aos")

GATE = {
    "syntax_command": "npm --prefix ap-web run type-check",
    "test_command": "npm --prefix ap-web test",
    "evidence_artifact_required": False,
}

BLOCKED = [
    "The run has no ability to execute shell commands.",
    "The run has no ability to write files in the workspace.",
    "The change would remove or alter the Automaton OS wordmark, the wolf "
    "mascot, the gold-on-dark theme, or the 'Let's do this thing!' hero. "
    "Phase 1 changes wording and navigation placement only, never visual "
    "identity - block instead of proceeding.",
]

VOCAB = (
    "\n\nTHE VOCABULARY (authoritative - use these words, do not invent "
    "alternatives; import shared strings from '@/lib/copy' rather than "
    "re-typing literals):\n"
    "  warden -> never shown to a user; remove the word entirely\n"
    "  automaton / automata -> 'saved routine' / 'routines'\n"
    "  execution -> 'run'\n"
    "  MCP servers -> 'connected apps'\n"
    "  ghost dispatch -> 'never started'\n"
    "  last_heartbeat -> 'last seen' plus a relative time\n"
    "  context_pct -> 'memory used', shown as a whole percent\n"
    "  run-weighted / unweighted -> 'across all runs' / 'average per routine'\n"
    "  fail-closed -> 'stops if a check fails'\n"
    "  receipt -> 'proof of what was done'\n"
    "  gate -> 'needs your OK'\n"
    "  exit code 0 / 3 / 6 -> 'finished' / 'paused for input' / 'failed a check'\n"
    "  Mtok -> 'per million words'"
)

TAIL = (
    "\n\nHOW TO WORK:\n"
    "  - FIRST RESPONSE: propose, in ONE turn, every provenance tool call this "
    "contract needs - a read_file for each named file, plus a run_shell "
    "find <dir> -maxdepth 1 -name <Stem>*.test.tsx -print for each named "
    "file's co-located test - and set done=true on that same turn. Never "
    "split discovery across turns: a turn that requests only part of the "
    "evidence strands the run, because follow-up revisions cannot execute "
    "tool calls.\n"
    "  - Read every named file with read_file before changing it. Never edit "
    "a file you have not successfully read, and never invent its contents.\n"
    "  - Make each change with a write_file call containing the complete new "
    "file contents. Write ONE file per response; when several files are "
    "named, take them in successive turns.\n"
    "  - After the final write, run BOTH gate commands verbatim as run_shell "
    "calls:\n"
    "      npm --prefix ap-web run type-check\n"
    "      npm --prefix ap-web test\n"
    "    Run them exactly as written; a scoped variant does not satisfy the "
    "gate. Also run git status --porcelain as a run_shell call - it shows new untracked files, which git diff does not - and confirm only the named files (and "
    "their co-located tests) changed.\n"
    "  - Set done=true once the edits are written and both gate commands have "
    "run. done is not a claim that the patch is committed - staging and "
    "commit are the orchestrator's job.\n"
    "  - You MAY update a named file's co-located *.test.tsx when your change "
    "alters an exported type, prop, or visible string the test asserts. "
    "Never delete a test, rename it away, or mark it skip/todo/only; update "
    "it to match the new wording.\n"
    "  - A missing file is a provable fact, not a dead end: prove presence or "
    "absence with run_shell find/grep - empty output is positive evidence of "
    "absence.\n"
    "  - If you are challenged and asked to try again, RE-EMIT the complete "
    "write_file tool_call. A response containing only {\"done\": true} is "
    "never sufficient: proposals are not carried forward between turns.\n"
    "  - BASELINE: the suite already skips 1 test file "
    "(src/loadtest/streamRenderBench.run.test.ts) and 2 tests. Inherited "
    "skips are expected; only an increase above that baseline is a failure.\n"
    "  - LARGE FILES - SURGICAL EDIT OPTION: when a named file is too large to "
    "rewrite faithfully in one response (roughly 25KB or more), do NOT escalate "
    "and do NOT attempt the full-file write. Instead perform the change as a "
    "run_shell call executing a python3 heredoc that does exact-string "
    "replacement on the file (read the file first so your match strings are "
    "exact; assert each old string occurs exactly once; write the file back). "
    "Then run git diff -- <file> as a run_shell call and inspect that the "
    "diff is precisely the intended change. A verified surgical edit fully "
    "satisfies a write requirement.\n"
    "  - Change nothing else."
)

PAGE_COMMON = (
    "Route every user-visible string on this page through the vocabulary, "
    "importing from '@/lib/copy' where a mapped term appears. Where this "
    "page catches an AmsError, render a plain one-sentence explanation "
    "(never raw JSON or a stringified detail field) plus a Retry control "
    "that re-runs the fetch."
)

FIXES = [
    {
        "letter": "a",
        "id": "aos-p1a-copy-module",
        "files": ["ap-web/src/lib/copy.ts"],
        "what": (
            "Create the NEW module ap-web/src/lib/copy.ts. It exports the "
            "vocabulary below as named constants (one authoritative mapping "
            "object plus direct named exports for the common terms) and a "
            "small relativeTime(iso: string): string helper for 'last seen'. "
            "No existing file imports it yet - create only this file and "
            "modify nothing else." + VOCAB
        ),
        "check": "lib/copy.ts exists and exports the vocabulary mapping.",
    },
    {
        "letter": "b",
        "id": "aos-p1b-ams-error-detail",
        "files": ["ap-web/src/lib/ams.ts"],
        "what": (
            "No raw AMS bridge error detail may reach a user: stop "
            "JSON.stringify-ing the detail field into the AmsError message. "
            "The error's user-facing message becomes a plain English "
            "sentence; keep the structured detail available on the error "
            "object for logging, just never as the display string."
        ),
        "check": "ams.ts no longer stringifies detail into the message.",
    },
    {
        "letter": "c",
        "id": "aos-p1c-system-copy",
        "files": ["ap-web/src/pages/SystemPage.tsx"],
        "what": (
            PAGE_COMMON + " On this page in particular: context_pct is "
            "labelled 'memory used' and shown as a whole percent, and 'MCP "
            "servers' becomes 'connected apps'." + VOCAB
        ),
        "check": "SystemPage uses the plain vocabulary and a Retry error state.",
    },
    {
        "letter": "d",
        "id": "aos-p1d-fleet-copy",
        "files": ["ap-web/src/pages/FleetPage.tsx"],
        "what": (
            PAGE_COMMON + " On this page in particular: 'ghost dispatch' "
            "becomes 'never started', last_heartbeat becomes 'last seen' "
            "with a relative time (use the helper from '@/lib/copy'), the "
            "word 'warden' disappears from user-visible copy, and "
            "execution becomes 'run'." + VOCAB
        ),
        "check": "FleetPage uses the plain vocabulary and a Retry error state.",
    },
    {
        "letter": "e",
        "id": "aos-p1e-patterns-copy",
        "files": ["ap-web/src/pages/PatternsPage.tsx"],
        "what": (
            PAGE_COMMON + " This page already has distinct loading/empty/"
            "error branches with Retry from phase 0 - keep them; this "
            "contract is about the wording." + VOCAB
        ),
        "check": "PatternsPage uses the plain vocabulary.",
    },
    {
        "letter": "f",
        "id": "aos-p1f-models-copy-keyboard",
        "files": ["ap-web/src/pages/ModelsPage.tsx"],
        "what": (
            PAGE_COMMON + " On this page in particular: 'Mtok' becomes 'per "
            "million words'. ADDITIONALLY the role picker becomes keyboard "
            "operable: the role node gets tabIndex={0} and an onKeyDown "
            "handler that activates the same action as click on Enter and "
            "on Space." + VOCAB
        ),
        "check": (
            "ModelsPage uses the plain vocabulary and its role node is "
            "focusable and activates on Enter/Space."
        ),
    },
    {
        "letter": "g",
        "id": "aos-p1g-loops-copy",
        "files": ["ap-web/src/pages/LoopsPage.tsx"],
        "what": PAGE_COMMON + VOCAB,
        "check": "LoopsPage uses the plain vocabulary.",
    },
    {
        "letter": "h",
        "id": "aos-p1h-training-copy",
        "files": ["ap-web/src/pages/TrainingPage.tsx"],
        "what": (
            PAGE_COMMON + " On this page in particular: 'run-weighted' and "
            "'unweighted' become 'across all runs' and 'average per "
            "routine'." + VOCAB
        ),
        "check": "TrainingPage uses the plain vocabulary.",
    },
    {
        "letter": "i",
        "id": "aos-p1i-pageshell-copy",
        "files": ["ap-web/src/components/PageShell.tsx"],
        "what": (
            "Route the user-visible strings in this shared shell component "
            "through the vocabulary, importing from '@/lib/copy' where a "
            "mapped term appears." + VOCAB
        ),
        "check": "PageShell uses the plain vocabulary.",
    },
    {
        "letter": "j",
        "id": "aos-p1j-workloop-copy",
        "files": ["ap-web/src/shell/WorkLoopPanel.tsx"],
        "what": (
            "Route the user-visible strings in the Work Loop panel through "
            "the vocabulary: 'receipt' becomes 'proof of what was done', "
            "'gate' becomes 'needs your OK', and exit codes 0/3/6 are "
            "presented as 'finished'/'paused for input'/'failed a check'. "
            "The goal-run event feed and its behaviour are untouched - this "
            "is wording only." + VOCAB
        ),
        "check": "WorkLoopPanel uses the plain vocabulary; feed intact.",
    },
    {
        "letter": "k",
        "id": "aos-p1k-advanced-page",
        "files": ["ap-web/src/pages/AdvancedPage.tsx", "ap-web/src/App.tsx"],
        "what": (
            "Create the NEW page ap-web/src/pages/AdvancedPage.tsx: a plain "
            "page that presents the six operator destinations (System, "
            "Fleet, Patterns, Models, Loops, Training) as links or tabs, "
            "described in the plain vocabulary, PLUS an Agents section that "
            "lists the locally discovered coding agents from the existing "
            "GET /coding-agents discovery endpoint - read "
            "omnigent/server/routes/coding_agents.py first for the exact "
            "mounted path and response shape; a simple name/kind/status "
            "list is enough. This gives the ACP-discovered agents their "
            "first visible home in the product. Then register a route for "
            "it in ap-web/src/App.tsx at path '/advanced'. This step is "
            "purely ADDITIVE: no existing route is removed or renamed, so "
            "every deep link keeps working. Two files - write them in two "
            "separate turns." + VOCAB
        ),
        "check": "AdvancedPage exists with the six destinations and an "
        "Agents section fed by coding-agent discovery; /advanced routes to it; "
        "no route removed.",
    },
    {
        "letter": "l",
        "id": "aos-p1l-sidebar-three-nav",
        "files": ["ap-web/src/shell/Sidebar.tsx"],
        "what": (
            "The sidebar's primary navigation is reduced to exactly three "
            "destinations: 'Do' (the existing chat/new-session route), "
            "'Work' (the existing inbox route), and 'Advanced' (the "
            "/advanced route added in the previous step). The six operator "
            "items (System, Fleet, Patterns, Models, Loops, Training) leave "
            "the top level - they are reachable from inside Advanced. "
            "Routes themselves are untouched, so deep links keep working. "
            "Everything else in this file - session list, search, account "
            "menu, and every brand element - stays byte-for-byte as it is. "
            "This is a large file (~60KB): re-read it fully before writing, "
            "and re-emit the complete write if challenged." + VOCAB
        ),
        "check": (
            "Sidebar has exactly three primary nav items; operator pages "
            "reachable via Advanced; brand untouched."
        ),
    },
]

written = []
for fx in FIXES:
    primary = fx["files"][0]
    file_list = "\n".join(f"  {p}" for p in fx["files"])
    contract = {
        "goal_id": fx["id"],
        "end_state": (
            f"EXACT FILES for this contract (workspace-relative - use these "
            f"verbatim; do not guess):\n{file_list}\n\n"
            f"{fx['what']}\n\n"
            f"Change nothing else, in these or any other files, beyond what "
            f"is described above."
            f"{TAIL}"
        ),
        "evidence_criteria": {**GATE, "required_files": [primary]},
        "inspection_criteria": [
            fx["check"],
            "Only the named files and, if needed, their co-located tests "
            "were modified.",
            "No test was deleted, renamed away, or marked skip/todo/only. "
            "BASELINE: 1 skipped test file and 2 skipped tests are "
            "inherited and expected; only an increase is a failure.",
            "The wordmark, wolf, gold-on-dark theme and 'Let's do this "
            "thing!' hero are untouched.",
            "The change is reviewable in a diff.",
        ],
        "blocked_conditions": BLOCKED,
        "budget": (
            {"max_total_tokens": 400000, "max_wallclock_s": 2700, "max_usd": 12.0}
            if fx["letter"] in ("k", "l")
            else {"max_total_tokens": 250000, "max_wallclock_s": 1800, "max_usd": 8.0}
        ),
    }
    path = BASE / f"phase1{fx['letter']}.contract.json"
    path.write_text(json.dumps(contract, indent=2))
    written.append((path.name, fx["id"], ", ".join(fx["files"])))

print(f"wrote {len(written)} phase-1 contracts:\n")
for name, gid, files in written:
    print(f"  {name:28} {gid:30} {files}")
