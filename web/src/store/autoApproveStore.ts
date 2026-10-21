/**
 * Per-session "approve everything" switch.
 *
 * Enabling does two things at once:
 *   1. server-side: PUT /v1/sessions/{id}/auto-approve sets the
 *      omnigent.auto_approve label, so the PermissionRequest hook
 *      answers allow WITHOUT ever minting a card (works unattended);
 *   2. client-side: ApprovalCard auto-accepts any elicitation that
 *      still renders while the switch is on -- SDK-harness approvals
 *      ride the elicitation path rather than the hook, so the label
 *      alone would not cover them.
 *
 * Deliberately per-session and non-persistent: trusting one session
 * with everything says nothing about the next one.
 */
import { create } from "zustand";

import { putAutoApprove } from "@/lib/sessionsApi";

interface AutoApproveState {
  /** sessionId -> enabled. Absent means off. */
  enabled: Record<string, boolean>;
  /** Last toggle error, for the checkbox to surface. */
  error: string | null;
  setEnabled: (sessionId: string, on: boolean) => Promise<void>;
}

export const useAutoApproveStore = create<AutoApproveState>((set) => ({
  enabled: {},
  error: null,
  setEnabled: async (sessionId, on) => {
    try {
      await putAutoApprove(sessionId, on);
      set((s) => ({
        enabled: { ...s.enabled, [sessionId]: on },
        error: null,
      }));
    } catch (err: unknown) {
      set({ error: err instanceof Error ? err.message : String(err) });
    }
  },
}));
