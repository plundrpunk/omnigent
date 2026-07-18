import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnyBlock, BlockContext } from "@/lib/blocks";
import { useChatStore } from "@/store/chatStore";
import { deriveWorkLoopSnapshot, WorkLoopPanel } from "./WorkLoopPanel";

const ctx: BlockContext = {
  agent: null,
  depth: 0,
  turn: 1,
  timestamp: 1,
  responseId: "resp_123",
  itemId: "item_123",
};

beforeEach(() => {
  useChatStore.setState({
    blocks: [],
    sessionStatus: "idle",
    status: "idle",
    sessionCostUsd: null,
    boundAgentName: null,
    gitBranch: null,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("deriveWorkLoopSnapshot", () => {
  it("stops at 75% and reports missing verifier evidence after a completed run", () => {
    const blocks: AnyBlock[] = [
      {
        type: "user_message",
        ctx,
        content: [{ type: "input_text", text: "Implement the session receipt" }],
      },
      {
        type: "response_start",
        ctx,
        model: "test",
        responseId: "resp_123",
        conversationId: "conv_1",
      },
      {
        type: "tool_group",
        ctx,
        iteration: 1,
        executions: [
          {
            name: "run_tests",
            arguments: {},
            argsSummary: "",
            callId: "call_1",
            agentName: "main",
            executedBy: "server",
            output: "passed",
          },
        ],
      },
      { type: "response_end", ctx, status: "completed", response: null },
    ];

    const snapshot = deriveWorkLoopSnapshot({
      blocks,
      sessionStatus: "idle",
      uiStatus: "idle",
      liveness: { kind: "online" },
      pendingApprovalCount: 0,
      changedCount: 2,
      agentCount: 1,
      agentsWorking: 0,
    });

    expect(snapshot.objective).toBe("Implement the session receipt");
    expect(snapshot.progress).toBe(75);
    expect(snapshot.overallLabel).toBe("Needs verification");
    expect(snapshot.verifierStatus).toBe("Not reported");
    expect(snapshot.latestResponseId).toBe("resp_123");
    expect(snapshot.toolCount).toBe(1);
  });

  it("makes host liveness a blocking gate instead of a muted footer status", () => {
    const snapshot = deriveWorkLoopSnapshot({
      blocks: [],
      sessionStatus: "idle",
      uiStatus: "idle",
      liveness: { kind: "host_offline", isOwner: true },
      pendingApprovalCount: 0,
      changedCount: 0,
      agentCount: 1,
      agentsWorking: 0,
      fallbackObjective: "Resume the run",
    });

    expect(snapshot.overallLabel).toBe("Blocked");
    expect(snapshot.readiness.label).toBe("Host offline");
    expect(snapshot.stages.find((stage) => stage.id === "run")?.state).toBe("blocked");
  });

  it("recovers a trace from hydrated history without claiming a terminal outcome", () => {
    const snapshot = deriveWorkLoopSnapshot({
      blocks: [
        {
          type: "tool_group",
          ctx,
          iteration: 1,
          executions: [],
        },
      ],
      sessionStatus: "idle",
      uiStatus: "idle",
      liveness: { kind: "online" },
      pendingApprovalCount: 0,
      changedCount: 0,
      agentCount: 1,
      agentsWorking: 0,
      fallbackObjective: "Inspect the historical run",
    });

    expect(snapshot.progress).toBe(75);
    expect(snapshot.latestResponseId).toBe("resp_123");
    expect(snapshot.resultStatus).toBe("settled · status unavailable");
    expect(snapshot.overallLabel).toBe("Needs verification");
  });

  it("reaches 100% only from a passed versioned Harness verifier receipt", () => {
    const receiptBlock: AnyBlock = {
      type: "work_receipt",
      ctx,
      receipt: {
        schema_version: "harness.receipt.v1",
        event_id: "28f721ce-cf1a-4c64-b8d4-dcd7d3d6a225",
        user_id: "user-1",
        project: "harness-automaton",
        work_item_id: "wi-1",
        session_id: "conv_1",
        response_id: "resp_123",
        status: "completed",
        created_at: "2026-07-10T00:00:00+00:00",
        verifier: {
          status: "passed",
          verdict: "ACCEPT",
          reason: "required command passed",
          evidence: ["tests passed"],
          tool_result_ids: ["tool-result-1"],
        },
        artifact: {
          artifact_id: "artifact-1",
          changed_files: ["src/example.ts"],
        },
        tool_result_ids: ["tool-result-1"],
        metadata: {},
      },
    };
    const snapshot = deriveWorkLoopSnapshot({
      blocks: [
        {
          type: "user_message",
          ctx,
          content: [{ type: "input_text", text: "Ship a verified receipt" }],
        },
        receiptBlock,
      ],
      sessionStatus: "idle",
      uiStatus: "idle",
      liveness: { kind: "host_offline", isOwner: true },
      pendingApprovalCount: 0,
      changedCount: 0,
      agentCount: 1,
      agentsWorking: 0,
    });

    expect(snapshot.progress).toBe(100);
    expect(snapshot.overallLabel).toBe("Verified");
    expect(snapshot.verifierStatus).toBe("Passed");
    expect(snapshot.resultStatus).toBe("completed");
    expect(snapshot.receiptEventId).toBe("28f721ce-cf1a-4c64-b8d4-dcd7d3d6a225");
    expect(snapshot.artifactId).toBe("artifact-1");
  });
});

describe("WorkLoopPanel", () => {
  it("renders the structured receipt and links to the human-gate inbox", () => {
    useChatStore.setState({
      blocks: [{ type: "response_end", ctx, status: "completed", response: null }],
      sessionCostUsd: 0.0042,
      boundAgentName: "abot_prime",
      gitBranch: "feature/work-loop",
    });

    render(
      <MemoryRouter>
        <WorkLoopPanel
          conversationId="conv_1"
          sessionTitle="Ship Work Loop v0"
          liveness={{ kind: "online" }}
          pendingApprovalCount={1}
          changedCount={2}
          agentCount={3}
          agentsWorking={0}
          showFilesPanel
          onOpenFiles={vi.fn()}
          onOpenAgents={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "Work Loop" })).toBeInTheDocument();
    expect(screen.getByText("Not reported")).toBeInTheDocument();
    expect(screen.getByText("$0.0042")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Review 1 waiting gate/i })).toHaveAttribute(
      "href",
      "/inbox",
    );
  });
});
