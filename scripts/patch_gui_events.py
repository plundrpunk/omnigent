#!/usr/bin/env python3
"""Render the run event feed inside the Work Loop panel.

The run card showed a status pill and, once terminal, a blocker blob. The
reasoning that produced that outcome -- roles, decisions, tool calls and
exit codes -- was only visible by reading the artifact JSON. This lifts the
card into its own component so it can hold expand state and follow the
live event stream.
"""
import pathlib
import re
import subprocess
import sys

WEB = pathlib.Path("ap-web")
PANEL = WEB / "src/shell/WorkLoopPanel.tsx"

src = PANEL.read_text(encoding="utf-8")
if "GoalRunEvents" in src:
    print("already applied")
    sys.exit(0)

# 1. imports
anchor_import = 'import { cn } from "@/lib/utils";'
if anchor_import not in src:
    # fall back to the first local import
    m = re.search(r'^import .*?from "@/.*?";$', src, re.M)
    if not m:
        sys.exit("could not find an anchor import")
    anchor_import = m.group(0)
src = src.replace(
    anchor_import,
    anchor_import
    + '\nimport { GoalRunEvents } from "@/shell/GoalRunEvents";'
    + '\nimport { useGoalEvents } from "@/hooks/useGoalEvents";',
    1,
)

# 2. replace the inline map body with a dedicated card component
old_map = """      <div className="flex flex-col gap-3">
        {runs.map((run) => {
          const meta = GOAL_STATUS_META[run.status] ?? {
            label: run.status,
            tone: "bg-muted text-muted-foreground",
          };
          const resume = checkpointResumeCommand(run.checkpoint);
          return (
            <div
              key={run.run_id}
              data-testid="goal-run-card"
              className="rounded-md border border-border/70 p-2.5"
            >"""
new_map = """      <div className="flex flex-col gap-3">
        {runs.map((run) => (
          <GoalRunCard key={run.run_id} run={run} />
        ))}
      </div>
    </section>
  );
}

/**
 * One goal run, with an expandable live feed of what the harness did.
 *
 * Kept separate from {@link GoalRunsSection} so each card owns its expand
 * state and its own event subscription -- hooks cannot live inside a map
 * callback, and only an opened card should poll.
 */
function GoalRunCard({ run }: { run: GoalRun }) {
  const [showDetail, setShowDetail] = useState(false);
  const events = useGoalEvents(run.run_id, run.status, showDetail);
  const meta = GOAL_STATUS_META[run.status] ?? {
    label: run.status,
    tone: "bg-muted text-muted-foreground",
  };
  const resume = checkpointResumeCommand(run.checkpoint);
  return (
    <>
            <div
              data-testid="goal-run-card"
              className="rounded-md border border-border/70 p-2.5"
            >"""
assert src.count(old_map) == 1, f"map anchor matched {src.count(old_map)}x"
src = src.replace(old_map, new_map)

# 3. close the new component and add the detail toggle
old_tail = """              {run.outcome && (
                <details className="mt-1.5">
                  <summary className="cursor-pointer text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                    goal-outcome.json
                  </summary>
                  <pre className="mt-1 max-h-48 overflow-auto rounded bg-muted/40 p-2 font-mono text-[10px] leading-4 whitespace-pre-wrap text-foreground/90">
                    {clipArtifact(JSON.stringify(run.outcome, null, 2), 2000)}
                  </pre>
                </details>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}"""
new_tail = """              {run.outcome && (
                <details className="mt-1.5">
                  <summary className="cursor-pointer text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                    goal-outcome.json
                  </summary>
                  <pre className="mt-1 max-h-48 overflow-auto rounded bg-muted/40 p-2 font-mono text-[10px] leading-4 whitespace-pre-wrap text-foreground/90">
                    {clipArtifact(JSON.stringify(run.outcome, null, 2), 2000)}
                  </pre>
                </details>
              )}
              <button
                type="button"
                data-testid="goal-run-detail-toggle"
                onClick={() => setShowDetail((open) => !open)}
                className="mt-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground hover:text-foreground"
              >
                {showDetail ? "Hide run detail" : "Show run detail"}
              </button>
              {showDetail && (
                <div className="mt-1.5">
                  <GoalRunEvents events={events} />
                </div>
              )}
            </div>
    </>
  );
}"""
assert src.count(old_tail) == 1, f"tail anchor matched {src.count(old_tail)}x"
src = src.replace(old_tail, new_tail)

# 4. make sure useState is imported
if not re.search(r'import \{[^}]*\buseState\b[^}]*\} from "react";', src):
    m = re.search(r'import \{([^}]*)\} from "react";', src)
    if m:
        src = src.replace(m.group(0), f'import {{{m.group(1).rstrip()}, useState }} from "react";', 1)
    else:
        src = 'import { useState } from "react";\n' + src

PANEL.write_text(src, encoding="utf-8")
print("WorkLoopPanel.tsx patched")

r = subprocess.run(["npm", "--prefix", "ap-web", "run", "type-check"],
                   capture_output=True, text=True)
print("type-check:", "PASS" if r.returncode == 0 else "FAIL")
if r.returncode != 0:
    print((r.stdout + r.stderr)[-1500:])
    sys.exit(1)
