/**
 * Client for the server's Harness Automaton goal bridge (`/v1/goal`,
 * R9 V2 — see `omnigent/server/routes/goal.py`).
 *
 * Truth rules: a run's `status` comes from the CLI exit code only;
 * `outcome` / `blocker_md` / `checkpoint` are quoted verbatim from the
 * harness' own artifacts. An unconfigured bridge reads as `null`
 * (absence), never as an empty success.
 */

import { hostFetch } from "@/lib/host";

export type GoalRunStatus =
  | "running"
  | "completed"
  | "blocked"
  | "paused"
  | "setup_error"
  | "error";

export interface GoalRun {
  run_id: string;
  goal_id: string;
  conversation_id: string | null;
  provider: string | null;
  status: GoalRunStatus;
  exit_code: number | null;
  /** Parsed goal-outcome.json (or the CLI's stdout payload), verbatim. */
  outcome: Record<string, unknown> | null;
  /** blocker.md, verbatim (exit 3). */
  blocker_md: string | null;
  /** checkpoint.json, verbatim text (exit 6). */
  checkpoint: string | null;
  stderr_tail: string | null;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

/**
 * Fetch this conversation's goal runs.
 *
 * @returns the runs (possibly empty), or `null` when the bridge is not
 *   configured on the server (503) — callers must render that as
 *   absence, not as "no runs".
 */
export async function fetchGoalRuns(conversationId: string): Promise<GoalRun[] | null> {
  const resp = await hostFetch(`/v1/goal?conversation_id=${encodeURIComponent(conversationId)}`);
  if (resp.status === 503) return null;
  if (!resp.ok) throw new Error(`goal bridge answered HTTP ${resp.status}`);
  const body: unknown = await resp.json().catch(() => null);
  const runs = (body as { runs?: unknown } | null)?.runs;
  return Array.isArray(runs) ? (runs as GoalRun[]) : [];
}

/**
 * Pull the ready-to-run resume command out of a verbatim checkpoint.
 *
 * @returns the `resume_command` string when the checkpoint parses and
 *   carries one; `null` otherwise (the caller shows the raw text —
 *   never a fabricated command).
 */
export function checkpointResumeCommand(checkpoint: string | null): string | null {
  if (!checkpoint) return null;
  try {
    const parsed: unknown = JSON.parse(checkpoint);
    const command = (parsed as { resume_command?: unknown } | null)?.resume_command;
    return typeof command === "string" && command.trim() ? command : null;
  } catch {
    return null;
  }
}
