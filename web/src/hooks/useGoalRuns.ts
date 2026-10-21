/**
 * Poll the goal bridge for this conversation's runs (R9 V2).
 *
 * `runs === null` means the bridge is unconfigured or unreachable —
 * the UI must render nothing rather than an empty state pretending to
 * be knowledge. Polling stops once the bridge reports itself
 * unconfigured (503) and only re-arms on remount.
 */

import { useEffect, useRef, useState } from "react";

import { fetchGoalRuns, type GoalRun } from "@/lib/goal";

const POLL_MS = 5000;

export function useGoalRuns(conversationId: string): GoalRun[] | null {
  const [runs, setRuns] = useState<GoalRun[] | null>(null);
  const stoppedRef = useRef(false);

  useEffect(() => {
    stoppedRef.current = false;
    setRuns(null);
    let timer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    const tick = async () => {
      try {
        const next = await fetchGoalRuns(conversationId);
        if (disposed) return;
        if (next === null) {
          // Bridge unconfigured: report absence and stop polling.
          stoppedRef.current = true;
          setRuns(null);
          return;
        }
        setRuns(next);
      } catch {
        // Unreachable ≠ empty: keep the last known truth (or absence)
        // and try again next tick.
      }
      if (!disposed && !stoppedRef.current) {
        timer = setTimeout(() => void tick(), POLL_MS);
      }
    };

    void tick();
    return () => {
      disposed = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [conversationId]);

  return runs;
}
