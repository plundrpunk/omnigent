import { useEffect, useRef, useState } from "react";

import {
  fetchGoalEvents,
  isRunActive,
  type GoalEvent,
} from "@/lib/goalEvents";

/** How often an active run is polled for new events. */
const POLL_MS = 2000;

/**
 * Follow one goal run's events.
 *
 * Accumulates rather than replaces: each tick asks only for events after
 * the ones already held, so a long run does not re-transfer its history.
 * Polling stops as soon as the run reaches a terminal status.
 *
 * @returns the events so far, or `null` while nothing has loaded yet.
 */
export function useGoalEvents(
  runId: string | null,
  status: string | null,
  enabled: boolean,
): GoalEvent[] | null {
  const [events, setEvents] = useState<GoalEvent[] | null>(null);
  // Ref, not state: the poll loop reads this without re-subscribing.
  const seen = useRef(0);

  useEffect(() => {
    if (!runId || !enabled) {
      setEvents(null);
      seen.current = 0;
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      try {
        const page = await fetchGoalEvents(runId, seen.current);
        if (cancelled) return;
        if (page) {
          if (page.events.length > 0) {
            seen.current = page.total;
            setEvents((prev) => [...(prev ?? []), ...page.events]);
          } else {
            // Mark "loaded, empty" without discarding what is already held.
            // Reading `events` here would be a stale closure -- it is
            // excluded from the deps so the poll loop is not restarted on
            // every append -- so resolve against the previous value instead.
            setEvents((prev) => prev ?? []);
          }
          if (!isRunActive(page.status)) return; // terminal: stop polling
        }
      } catch {
        // A run writes its artifact continuously, so a torn read is
        // expected. Keep polling rather than surfacing a transient error.
      }
      if (!cancelled) timer = setTimeout(tick, POLL_MS);
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // `events` is deliberately excluded: including it would restart the
    // poll loop on every append.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, status, enabled]);

  return events;
}
