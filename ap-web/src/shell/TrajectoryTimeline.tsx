import { useMemo, useState } from "react";

import {
  fetchReceipt,
  TRAJECTORY_SOURCES,
  type TrajectoryEntry,
  type TrajectoryPage,
  type TrajectorySource,
  type TrajectoryStatus,
} from "@/lib/trajectory";
import { cn } from "@/lib/utils";

/**
 * One chronological timeline across Omnigent session work, Harness
 * Automaton goal runs and AMS work receipts.
 *
 * Purely presentational: the server has already ordered, de-duplicated
 * and user-scoped the entries. Each row shows where the event came from,
 * when, who acted, what they did, how it ended, and a link to the receipt
 * that backs it — or says plainly that there is none.
 */

const SOURCE_LABEL: Record<TrajectorySource, string> = {
  omnigent: "Omnigent",
  harness: "Harness",
  ams: "AMS",
};

const SOURCE_TONE: Record<TrajectorySource, string> = {
  omnigent: "bg-primary/10 text-primary",
  harness: "bg-cyan-500/10 text-cyan-500",
  ams: "bg-purple-500/10 text-purple-400",
};

const SOURCE_GAP_LABEL: Record<TrajectorySource, string> = {
  omnigent: "Session events",
  harness: "Harness goal runs",
  ams: "AMS receipts",
};

const STATUS_TONE: Record<TrajectoryStatus, string> = {
  ok: "text-success",
  failed: "text-destructive",
  blocked: "text-destructive",
  running: "text-primary",
  paused: "text-warning",
  info: "text-muted-foreground",
};

const RECEIPT_KIND_LABEL: Record<string, string> = {
  work_receipt: "work receipt",
  goal_run: "goal run receipt",
  session_item: "session item",
};

function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function clipJson(value: unknown, max = 2000): string {
  const text = JSON.stringify(value, null, 2) ?? "null";
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

function ReceiptLink({ entry }: { entry: TrajectoryEntry }) {
  const [state, setState] = useState<
    | { kind: "closed" }
    | { kind: "loading" }
    | { kind: "open"; body: unknown }
    | { kind: "error"; message: string }
  >({ kind: "closed" });
  const receipt = entry.receipt;

  if (!receipt) {
    return <span className="text-muted-foreground/60">No receipt</span>;
  }
  const label = RECEIPT_KIND_LABEL[receipt.kind] ?? receipt.kind;
  const href = receipt.href;

  const toggle = async () => {
    if (state.kind === "open" || state.kind === "error") {
      setState({ kind: "closed" });
      return;
    }
    if (!href) return;
    setState({ kind: "loading" });
    try {
      setState({ kind: "open", body: await fetchReceipt(href) });
    } catch (err) {
      setState({ kind: "error", message: err instanceof Error ? err.message : "unavailable" });
    }
  };

  return (
    <div className="min-w-0">
      <button
        type="button"
        disabled={!href}
        onClick={() => void toggle()}
        aria-label={`Open ${label} ${receipt.id}`}
        title={href ?? `${label} ${receipt.id} (no link)`}
        className="truncate font-mono text-[10px] text-primary underline-offset-2 hover:underline disabled:text-muted-foreground disabled:no-underline"
      >
        {label} · {receipt.id.slice(0, 12)}
      </button>
      {state.kind === "loading" && (
        <p className="text-[10px] text-muted-foreground">Loading receipt…</p>
      )}
      {state.kind === "error" && (
        <p className="text-[10px] text-destructive">Receipt unavailable: {state.message}</p>
      )}
      {state.kind === "open" && (
        <pre
          data-testid="trajectory-receipt"
          className="mt-1 max-h-48 overflow-auto rounded bg-muted/40 p-2 font-mono text-[10px] leading-4 whitespace-pre-wrap text-foreground/90"
        >
          {clipJson(state.body)}
        </pre>
      )}
    </div>
  );
}

function EntryRow({ entry }: { entry: TrajectoryEntry }) {
  return (
    <li
      data-testid="trajectory-entry"
      className="grid grid-cols-[64px_minmax(0,1fr)] gap-x-2 border-border/60 border-b py-1.5 text-[11px] last:border-0"
    >
      <div className="flex flex-col gap-0.5">
        <span
          className={cn(
            "inline-flex w-fit rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide",
            SOURCE_TONE[entry.source],
          )}
        >
          {SOURCE_LABEL[entry.source]}
        </span>
        {entry.timestamp ? (
          <time
            dateTime={entry.timestamp}
            title={entry.timestamp}
            className="font-mono text-[10px] text-muted-foreground"
          >
            {formatTime(entry.timestamp)}
          </time>
        ) : (
          <span className="text-[10px] italic text-muted-foreground/70">time not reported</span>
        )}
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-1.5">
          <span className="font-medium text-foreground">{entry.actor}</span>
          {entry.subagent && (
            <span className="text-[10px] text-muted-foreground">↳ {entry.subagent}</span>
          )}
          <span className="font-mono text-[10px] text-muted-foreground">{entry.action}</span>
          <span className={cn("text-[10px] font-semibold", STATUS_TONE[entry.status])}>
            {entry.status}
          </span>
        </div>
        {entry.summary && (
          <p className="truncate text-muted-foreground" title={entry.summary}>
            {entry.summary}
          </p>
        )}
        <ReceiptLink entry={entry} />
      </div>
    </li>
  );
}

export function TrajectoryTimeline({ page }: { page: TrajectoryPage | null }) {
  const [filter, setFilter] = useState<TrajectorySource | "all">("all");
  const visible = useMemo(
    () =>
      page === null
        ? []
        : filter === "all"
          ? page.entries
          : page.entries.filter((entry) => entry.source === filter),
    [page, filter],
  );

  if (page === null) {
    return <p className="text-[11px] text-muted-foreground">Loading trajectory…</p>;
  }

  const gaps = TRAJECTORY_SOURCES.flatMap((source) => {
    const state = page.sources[source];
    return state && state.status !== "ok" ? [{ source, status: state.status }] : [];
  });

  return (
    <div data-testid="trajectory-timeline" className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by source">
        {(["all", ...TRAJECTORY_SOURCES] as const).map((key) => (
          <button
            key={key}
            type="button"
            aria-pressed={filter === key}
            onClick={() => setFilter(key)}
            className={cn(
              "rounded-full border px-2 py-0.5 text-[10px]",
              filter === key
                ? "border-primary/40 bg-primary/10 text-primary"
                : "border-border text-muted-foreground hover:text-foreground",
            )}
          >
            {key === "all" ? "All" : SOURCE_LABEL[key]}
          </button>
        ))}
      </div>
      {gaps.map(({ source, status }) => (
        <p key={source} className="text-[10px] text-warning">
          {SOURCE_GAP_LABEL[source]} unavailable ({status})
        </p>
      ))}
      {page.dropped > 0 && (
        <p className="text-[10px] text-muted-foreground">
          {page.dropped} entr{page.dropped === 1 ? "y" : "ies"} withheld (not yours)
        </p>
      )}
      {visible.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No events recorded yet.</p>
      ) : (
        <ol className="flex flex-col">
          {visible.map((entry) => (
            <EntryRow key={entry.entry_id} entry={entry} />
          ))}
        </ol>
      )}
    </div>
  );
}
