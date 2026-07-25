/**
 * Live run events from the Harness goal bridge.
 *
 * The run card could only show a status pill; everything that explains a
 * run -- which role spoke, what it decided, every tool call and its exit
 * code -- lived in the artifact on disk. `/v1/goal/{id}/events` normalises
 * those, and this polls the tail while a run is active.
 */

import { hostFetch } from "@/lib/host";

/** One flattened artifact event. `kind` selects how the panel renders it. */
export interface GoalEvent {
  index: number;
  role: string;
  kind: "note" | "tool" | "executor" | "verdict";
  summary?: string;
  /** kind === "tool" */
  tool?: string;
  ok?: boolean;
  exit_code?: number | null;
  target?: string;
  error?: string;
  /** kind === "executor" */
  done?: boolean;
  patch_keys?: string[];
  proposed?: { tool: string; target: string }[];
  /** kind === "verdict" */
  verdict?: string;
  subgoal?: string | null;
}

export interface GoalEventPage {
  run_id: string;
  status: string | null;
  artifact_id?: string | null;
  total: number;
  events: GoalEvent[];
}

/**
 * Fetch events for one run.
 *
 * @param since event index to resume from, so a live run pulls only the
 *   tail instead of the whole history on every tick.
 * @returns the page, or `null` when the bridge is unconfigured (503) --
 *   callers must render that as absence, not as "no events".
 */
export async function fetchGoalEvents(
  runId: string,
  since = 0,
): Promise<GoalEventPage | null> {
  const resp = await hostFetch(
    `/v1/goal/${encodeURIComponent(runId)}/events?since=${since}`,
  );
  if (resp.status === 503) return null;
  if (!resp.ok) throw new Error(`goal bridge answered HTTP ${resp.status}`);
  const body: unknown = await resp.json().catch(() => null);
  if (!body || typeof body !== "object") return null;
  const page = body as GoalEventPage;
  return {
    ...page,
    events: Array.isArray(page.events) ? page.events : [],
    total: typeof page.total === "number" ? page.total : 0,
  };
}

/** Terminal runs never gain events, so polling them is pure waste. */
export function isRunActive(status: string | null | undefined): boolean {
  return status === "running" || status === "queued" || status == null;
}
