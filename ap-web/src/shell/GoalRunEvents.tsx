import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils";
import type { GoalEvent } from "@/lib/goalEvents";

/**
 * The live event feed for one goal run.
 *
 * Mirrors what the terminal watcher prints: which role spoke, what it
 * decided, and every tool call with its exit code -- so a blocked run can
 * be understood from the panel instead of by reading the artifact JSON.
 */

/**
 * Keep a scroll container pinned to its newest content.
 *
 * A live run appends constantly, so the feed follows the tail by default.
 * It stops following the moment the reader scrolls up — reading back through
 * a blocked run should not be yanked away by the next event — and resumes
 * once they return to the bottom.
 */
function useStickToBottom(dep: number) {
  const ref = useRef<HTMLDivElement | null>(null);
  const stuck = useRef(true);

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    // A few px of slack: fractional scroll heights never land exactly.
    stuck.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  };

  useEffect(() => {
    const el = ref.current;
    if (el && stuck.current) el.scrollTop = el.scrollHeight;
  }, [dep]);

  return { ref, onScroll };
}

const ROLE_TONE: Record<string, string> = {
  planner: "text-purple-400",
  executor: "text-cyan-400",
  critic: "text-yellow-400",
  verifier: "text-green-400",
  revisor: "text-red-400",
  orchestrator: "text-foreground",
  governor: "text-muted-foreground",
  safety: "text-muted-foreground",
};

/** Verdicts that mean the run moved forward. */
const GOOD_VERDICT = /^(ACCEPT|proceed|PASS)$/i;

function Verdict({ value }: { value: string }) {
  return (
    <span
      className={cn(
        "font-semibold",
        GOOD_VERDICT.test(value) ? "text-green-400" : "text-red-400",
      )}
    >
      {value}
    </span>
  );
}

function EventRow({ event }: { event: GoalEvent }) {
  const roleTone = ROLE_TONE[event.role] ?? "text-muted-foreground";

  return (
    <div className="flex gap-2 py-1 font-mono text-[10px] leading-relaxed">
      <span className="w-6 shrink-0 text-right text-muted-foreground/60">
        {event.index}
      </span>
      <span className={cn("w-20 shrink-0 font-semibold", roleTone)}>
        {event.role}
      </span>
      <div className="min-w-0 flex-1">
        {event.kind === "tool" && (
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-foreground">{event.tool}</span>
            <span className={event.ok ? "text-green-400" : "text-red-400"}>
              {event.ok ? "OK" : "FAIL"}
            </span>
            {event.exit_code != null && (
              <span className="text-muted-foreground">
                exit={event.exit_code}
              </span>
            )}
            {event.target && (
              <span className="truncate text-muted-foreground" title={event.target}>
                {event.target}
              </span>
            )}
          </div>
        )}

        {event.kind === "executor" && (
          <div>
            <span className="text-muted-foreground">
              done={String(event.done)}
            </span>
            {event.patch_keys && event.patch_keys.length > 0 && (
              <span className="ml-2 text-muted-foreground">
                patch=[{event.patch_keys.join(", ")}]
              </span>
            )}
            {event.proposed?.map((p, i) => (
              <div key={i} className="text-muted-foreground/80">
                → {p.tool}: <span className="truncate">{p.target}</span>
              </div>
            ))}
          </div>
        )}

        {event.kind === "verdict" && event.verdict && (
          <div>
            <Verdict value={event.verdict} />
            {event.subgoal && (
              <span className="ml-2 text-muted-foreground">{event.subgoal}</span>
            )}
          </div>
        )}

        {event.error && (
          <div className="text-red-400/90" title={event.error}>
            {event.error}
          </div>
        )}
        {event.summary && (
          <div className="text-muted-foreground/70">{event.summary}</div>
        )}
      </div>
    </div>
  );
}

export function GoalRunEvents({ events }: { events: GoalEvent[] | null }) {
  const { ref, onScroll } = useStickToBottom(events?.length ?? 0);

  if (events === null) {
    return (
      <div className="px-2 py-1.5 text-[10px] text-muted-foreground">
        Loading run detail…
      </div>
    );
  }
  if (events.length === 0) {
    return (
      <div className="px-2 py-1.5 text-[10px] text-muted-foreground">
        No events recorded yet.
      </div>
    );
  }
  return (
    <div
      ref={ref}
      onScroll={onScroll}
      data-testid="goal-run-events"
      className="max-h-80 overflow-y-auto rounded border border-border/50 bg-background/40 px-1.5 py-1"
    >
      {events.map((e) => (
        <EventRow key={e.index} event={e} />
      ))}
    </div>
  );
}
