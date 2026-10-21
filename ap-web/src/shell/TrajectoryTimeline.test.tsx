import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchReceipt,
  fetchTrajectory,
  type TrajectoryEntry,
  type TrajectoryPage,
} from "@/lib/trajectory";
import { TrajectoryTimeline } from "./TrajectoryTimeline";
import { TrajectorySection } from "./WorkLoopPanel";

vi.mock("@/lib/trajectory", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/trajectory")>()),
  fetchReceipt: vi.fn(),
  fetchTrajectory: vi.fn(),
}));

const mockedReceipt = vi.mocked(fetchReceipt);
const mockedTrajectory = vi.mocked(fetchTrajectory);

function entry(overrides: Partial<TrajectoryEntry>): TrajectoryEntry {
  return {
    schema_version: "trajectory.v1",
    entry_id: "e",
    source: "omnigent",
    timestamp: "2026-10-21T10:00:00+00:00",
    actor: "coder",
    action: "tool_call",
    status: "ok",
    summary: "run_tests",
    conversation_id: "conv_1",
    subagent: null,
    goal_id: null,
    run_id: null,
    owner: null,
    receipt: null,
    ...overrides,
  };
}

function page(entries: TrajectoryEntry[], sources: TrajectoryPage["sources"] = {}): TrajectoryPage {
  return { conversationId: "conv_1", goalId: null, entries, sources, dropped: 0 };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("TrajectoryTimeline", () => {
  it("shows a loading state before the first page arrives", () => {
    render(<TrajectoryTimeline page={null} />);
    expect(screen.getByText(/Loading trajectory/)).toBeTruthy();
  });

  it("says plainly when nothing has been recorded", () => {
    render(<TrajectoryTimeline page={page([])} />);
    expect(screen.getByText(/No events recorded/)).toBeTruthy();
  });

  it("renders source, time, actor, action, status and receipt for every entry", () => {
    render(
      <TrajectoryTimeline
        page={page([
          entry({ entry_id: "a", source: "omnigent", actor: "alice", action: "user_message" }),
          entry({
            entry_id: "b",
            source: "harness",
            actor: "verifier",
            action: "verify",
            status: "failed",
            receipt: { kind: "goal_run", id: "run-1", href: "/v1/goal/run-1" },
          }),
          entry({
            entry_id: "c",
            source: "ams",
            actor: "goal-queue-daemon",
            action: "work_receipt",
            status: "blocked",
            timestamp: null,
            receipt: {
              kind: "work_receipt",
              id: "r-1",
              href: "/v1/trajectory/conv_1/receipts/r-1",
            },
          }),
        ])}
      />,
    );
    const rows = screen.getAllByTestId("trajectory-entry");
    expect(rows).toHaveLength(3);

    const [first, second, third] = rows;
    expect(within(first).getByText("Omnigent")).toBeTruthy();
    expect(within(first).getByText("alice")).toBeTruthy();
    expect(within(first).getByText("user_message")).toBeTruthy();
    expect(within(first).getByText("No receipt")).toBeTruthy();

    expect(within(second).getByText("Harness")).toBeTruthy();
    expect(within(second).getByText("verifier")).toBeTruthy();
    expect(within(second).getByText("failed")).toBeTruthy();
    expect(within(second).getByRole("button", { name: /goal run receipt/i })).toBeTruthy();

    expect(within(third).getByText("AMS")).toBeTruthy();
    expect(within(third).getByText("blocked")).toBeTruthy();
    expect(within(third).getByText("time not reported")).toBeTruthy();
    expect(within(third).getByRole("button", { name: /work receipt/i })).toBeTruthy();

    // Machine-readable time for each timed row.
    expect(first.querySelector("time")?.getAttribute("dateTime")).toBe("2026-10-21T10:00:00+00:00");
  });

  it("labels sub-agent work with its sub-agent", () => {
    render(<TrajectoryTimeline page={page([entry({ subagent: "researcher" })])} />);
    expect(screen.getByText("↳ researcher")).toBeTruthy();
  });

  it("filters by source", () => {
    render(
      <TrajectoryTimeline
        page={page([
          entry({ entry_id: "a", source: "omnigent" }),
          entry({ entry_id: "b", source: "harness" }),
        ])}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Harness" }));
    const rows = screen.getAllByTestId("trajectory-entry");
    expect(rows).toHaveLength(1);
    expect(within(rows[0]).getByText("Harness")).toBeTruthy();
  });

  it("reports a source that could not be reached instead of hiding it", () => {
    render(
      <TrajectoryTimeline page={page([entry({})], { ams: { status: "unreachable", count: 0 } })} />,
    );
    expect(screen.getByText(/AMS receipts unavailable \(unreachable\)/)).toBeTruthy();
  });

  it("opens a receipt inline when its link is followed", async () => {
    mockedReceipt.mockResolvedValue({ event_id: "r-1", status: "completed" });
    render(
      <TrajectoryTimeline
        page={page([
          entry({
            action: "work_receipt",
            receipt: {
              kind: "work_receipt",
              id: "r-1",
              href: "/v1/trajectory/conv_1/receipts/r-1",
            },
          }),
        ])}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /work receipt/i }));
    await waitFor(() =>
      expect(screen.getByTestId("trajectory-receipt").textContent).toContain("r-1"),
    );
    expect(mockedReceipt).toHaveBeenCalledWith("/v1/trajectory/conv_1/receipts/r-1");
  });
});

describe("TrajectorySection (Work Loop panel)", () => {
  it("does not poll until opened, then shows the session's timeline", async () => {
    mockedTrajectory.mockResolvedValue(page([entry({ actor: "researcher" })]));
    render(<TrajectorySection conversationId="conv_1" />);
    expect(mockedTrajectory).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("trajectory-toggle"));
    await waitFor(() => expect(screen.getAllByTestId("trajectory-entry")).toHaveLength(1));
    expect(mockedTrajectory).toHaveBeenCalledWith("conv_1", undefined);
    expect(screen.getByText("researcher")).toBeTruthy();
  });
});
