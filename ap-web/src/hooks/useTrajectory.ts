/**
 * Poll the unified trajectory timeline for one session while it is shown.
 *
 * Returns `null` until the first page lands. A failed poll keeps the last
 * good page (unreachable ≠ empty) and retries on the next tick.
 */

import { useEffect, useState } from "react";

import { fetchTrajectory, type TrajectoryPage } from "@/lib/trajectory";

const POLL_MS = 5000;

export function useTrajectory(
  conversationId: string,
  enabled: boolean,
  goalId?: string | null,
): TrajectoryPage | null {
  const [page, setPage] = useState<TrajectoryPage | null>(null);

  useEffect(() => {
    setPage(null);
    if (!enabled) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      try {
        const next = await fetchTrajectory(conversationId, goalId);
        if (!disposed) setPage(next);
      } catch {
        // Keep the last known truth; try again next tick.
      }
      if (!disposed) timer = setTimeout(() => void tick(), POLL_MS);
    };

    void tick();
    return () => {
      disposed = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [conversationId, enabled, goalId]);

  return page;
}
