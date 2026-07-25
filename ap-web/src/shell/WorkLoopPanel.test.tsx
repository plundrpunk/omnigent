import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnyBlock, BlockContext } from "@/lib/blocks";
import { useChatStore } from "@/store/chatStore";
import { deriveWorkLoopSnapshot, GoalRunsSection, WorkLoopPanel } from "./WorkLoopPanel";

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
    expect(snapshot.stages.map(({ label, state }) => ({ label, state }))).toEqual([
      { label: "Intake", state: "complete" },
      { label: "Run", state: "complete" },
      { label: "Verify", state: "attention" },
      { label: "Receipt", state: "complete" },
    ]);
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

    expect(snapshot.stages.map(({ label, state }) => ({ label, state }))).toEqual([
      { label: "Intake", state: "complete" },
      { label: "Run", state: "attention" },
      { label: "Verify", state: "attention" },
      { label: "Receipt", state: "attention" },
    ]);
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

    expect(snapshot.stages.map(({ label, state }) => ({ label, state }))).toEqual([
      { label: "Intake", state: "complete" },
      { label: "Run", state: "complete" },
      { label: "Verify", state: "complete" },
      { label: "Receipt", state: "complete" },
    ]);
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

describe("GoalRunsSection", () => {
  const baseRun = {
    run_id: "r-1",
    goal_id: "fix-tests",
    conversation_id: "conv_1",
    provider: "codex",
    status: "completed" as const,
    exit_code: 0,
    outcome: { gate: { test_command: "ran" } },
    blocker_md: null,
    checkpoint: null,
    stderr_tail: null,
    error: null,
    started_at: "2026-07-18T00:00:00Z",
    finished_at: "2026-07-18T00:01:00Z",
  };

  it("renders nothing for an empty run list — absence is absence", () => {
    const { container } = render(<GoalRunsSection runs={[]} />);
    expect(container.querySelector("[data-testid=goal-runs-section]")).toBeNull();
  });

  it("shows a gate-passed run with its exit code and outcome verbatim", () => {
    render(<GoalRunsSection runs={[baseRun]} />);
    expect(screen.getByText("fix-tests")).toBeInTheDocument();
    expect(screen.getByText("Gate passed")).toBeInTheDocument();
    expect(screen.getByText(/exit 0 · codex/)).toBeInTheDocument();
    expect(screen.getByText("goal-outcome.json")).toBeInTheDocument();
  });

  it("labels exit 3 as blocked and quotes blocker.md verbatim", () => {
    render(
      <GoalRunsSection
        runs={[
          {
            ...baseRun,
            run_id: "r-2",
            status: "blocked",
            exit_code: 3,
            blocker_md: "# Blocked\nthe gate failed honestly",
          },
        ]}
      />,
    );
    expect(screen.getByText("Blocked by gate")).toBeInTheDocument();
    expect(screen.getByText(/the gate failed honestly/)).toBeInTheDocument();
  });

  it("surfaces the checkpoint resume command for a paused run", () => {
    render(
      <GoalRunsSection
        runs={[
          {
            ...baseRun,
            run_id: "r-3",
            status: "paused",
            exit_code: 6,
            checkpoint: JSON.stringify({ resume_command: "automaton goal --resume r-3" }),
          },
        ]}
      />,
    );
    expect(screen.getByText("Paused")).toBeInTheDocument();
    expect(screen.getByText("automaton goal --resume r-3")).toBeInTheDocument();
  });

  it("shows the raw checkpoint when no resume command is parseable", () => {
    render(
      <GoalRunsSection
        runs={[{ ...baseRun, run_id: "r-4", status: "paused", exit_code: 6, checkpoint: "raw text" }]}
      />,
    );
    expect(screen.getByText("raw text")).toBeInTheDocument();
  });
});
