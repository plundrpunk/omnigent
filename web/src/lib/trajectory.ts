/**
 * Client for the unified trajectory timeline (`GET /v1/trajectory/{id}`,
 * see `omnigent/server/routes/trajectory.py`).
 *
 * The server merges three event streams into one chronological,
 * user-scoped `trajectory.v1` list:
 *
 * - `omnigent` — the session's items (and its sub-agent children),
 * - `harness`  — Harness Automaton goal runs and their artifact events,
 * - `ams`      — `harness.receipt.v1` work receipts held by AMS.
 *
 * This module only validates the wire shape. Ordering, deduplication and
 * user isolation are the server's job; anything malformed is dropped here
 * rather than rendered half-parsed.
 */

import { authenticatedFetch } from "@/lib/identity";

export const TRAJECTORY_SCHEMA_V1 = "trajectory.v1" as const;

export type TrajectorySource = "omnigent" | "harness" | "ams";
export type TrajectoryStatus = "ok" | "failed" | "blocked" | "running" | "paused" | "info";

export const TRAJECTORY_SOURCES: readonly TrajectorySource[] = ["omnigent", "harness", "ams"];

const STATUSES: ReadonlySet<string> = new Set([
  "ok",
  "failed",
  "blocked",
  "running",
  "paused",
  "info",
]);

export interface TrajectoryReceiptRef {
  /** `work_receipt` (harness.receipt.v1), `goal_run` (/v1/goal record), `session_item`. */
  kind: string;
  id: string;
  /** Same-origin API path that returns the receipt, or `null`. */
  href: string | null;
}

export interface TrajectoryEntry {
  schema_version: typeof TRAJECTORY_SCHEMA_V1;
  entry_id: string;
  source: TrajectorySource;
  /** ISO-8601 UTC, or `null` when the source recorded no time. */
  timestamp: string | null;
  actor: string;
  action: string;
  status: TrajectoryStatus;
  summary: string;
  conversation_id: string | null;
  subagent: string | null;
  goal_id: string | null;
  run_id: string | null;
  owner: string | null;
  receipt: TrajectoryReceiptRef | null;
}

export interface TrajectorySourceState {
  /** `ok`, `unconfigured` or `unreachable`. */
  status: string;
  count: number;
}

export interface TrajectoryPage {
  conversationId: string;
  goalId: string | null;
  entries: TrajectoryEntry[];
  sources: Partial<Record<TrajectorySource, TrajectorySourceState>>;
  /** Entries the server withheld because they belong to someone else. */
  dropped: number;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

/** Only same-origin API paths are followed; anything else is dropped. */
export function isSafeReceiptHref(href: unknown): href is string {
  return typeof href === "string" && href.startsWith("/v1/") && !href.startsWith("//");
}

function parseReceipt(value: unknown): TrajectoryReceiptRef | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  const kind = str(raw.kind);
  const id = str(raw.id);
  if (!kind || !id) return null;
  return { kind, id, href: isSafeReceiptHref(raw.href) ? raw.href : null };
}

export function parseTrajectoryEntry(value: unknown): TrajectoryEntry | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  if (raw.schema_version !== TRAJECTORY_SCHEMA_V1) return null;
  const entryId = str(raw.entry_id);
  const source = raw.source;
  if (!entryId || (source !== "omnigent" && source !== "harness" && source !== "ams")) {
    return null;
  }
  const status = typeof raw.status === "string" && STATUSES.has(raw.status) ? raw.status : "info";
  return {
    schema_version: TRAJECTORY_SCHEMA_V1,
    entry_id: entryId,
    source,
    timestamp: str(raw.timestamp),
    actor: str(raw.actor) ?? "unknown",
    action: str(raw.action) ?? "event",
    status: status as TrajectoryStatus,
    summary: typeof raw.summary === "string" ? raw.summary : "",
    conversation_id: str(raw.conversation_id),
    subagent: str(raw.subagent),
    goal_id: str(raw.goal_id),
    run_id: str(raw.run_id),
    owner: str(raw.owner),
    receipt: parseReceipt(raw.receipt),
  };
}

export function parseTrajectoryPage(value: unknown): TrajectoryPage | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  if (raw.schema_version !== TRAJECTORY_SCHEMA_V1) return null;
  const entries = Array.isArray(raw.entries)
    ? raw.entries
        .map(parseTrajectoryEntry)
        .filter((entry): entry is TrajectoryEntry => entry !== null)
    : [];
  const sources: TrajectoryPage["sources"] = {};
  if (raw.sources && typeof raw.sources === "object" && !Array.isArray(raw.sources)) {
    for (const source of TRAJECTORY_SOURCES) {
      const state = (raw.sources as Record<string, unknown>)[source];
      if (state && typeof state === "object") {
        const s = state as Record<string, unknown>;
        sources[source] = {
          status: typeof s.status === "string" ? s.status : "unknown",
          count: typeof s.count === "number" ? s.count : 0,
        };
      }
    }
  }
  const scope = raw.scope as Record<string, unknown> | undefined;
  return {
    conversationId: str(raw.conversation_id) ?? "",
    goalId: str(raw.goal_id),
    entries,
    sources,
    dropped: typeof scope?.dropped === "number" ? scope.dropped : 0,
  };
}

/**
 * Fetch one session's (optionally one goal's) trajectory.
 *
 * @throws on HTTP or shape errors, so a poller keeps its last good page
 *   instead of replacing it with a fabricated empty one.
 */
export async function fetchTrajectory(
  conversationId: string,
  goalId?: string | null,
): Promise<TrajectoryPage> {
  const query = goalId ? `?goal_id=${encodeURIComponent(goalId)}` : "";
  const resp = await authenticatedFetch(
    `/v1/trajectory/${encodeURIComponent(conversationId)}${query}`,
  );
  if (!resp.ok) throw new Error(`trajectory answered HTTP ${resp.status}`);
  const page = parseTrajectoryPage(await resp.json().catch(() => null));
  if (page === null) throw new Error("trajectory returned an unexpected body");
  return page;
}

/** Follow an entry's receipt link (same-origin API paths only). */
export async function fetchReceipt(href: string): Promise<unknown> {
  if (!isSafeReceiptHref(href)) throw new Error("refusing to follow a non-API receipt link");
  const resp = await authenticatedFetch(href);
  if (!resp.ok) throw new Error(`receipt answered HTTP ${resp.status}`);
  return resp.json();
}
