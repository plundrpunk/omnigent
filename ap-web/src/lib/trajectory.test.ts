import { afterEach, describe, expect, it, vi } from "vitest";

import { hostFetch } from "./host";
import {
  fetchReceipt,
  fetchTrajectory,
  parseTrajectoryEntry,
  parseTrajectoryPage,
  type TrajectoryEntry,
} from "./trajectory";

vi.mock("./host", () => ({ hostFetch: vi.fn() }));

const mockedFetch = vi.mocked(hostFetch);

function response(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

const ENTRY: TrajectoryEntry = {
  schema_version: "trajectory.v1",
  entry_id: "harness:goal-run:run-1:finished",
  source: "harness",
  timestamp: "2026-10-21T10:00:06+00:00",
  actor: "harness-automaton",
  action: "goal_finished",
  status: "ok",
  summary: "g-1 completed (exit 0)",
  conversation_id: "conv_1",
  subagent: null,
  goal_id: "g-1",
  run_id: "run-1",
  owner: null,
  receipt: { kind: "goal_run", id: "run-1", href: "/v1/goal/run-1" },
};

afterEach(() => {
  vi.clearAllMocks();
});

describe("parseTrajectoryEntry", () => {
  it("accepts a well-formed trajectory.v1 entry", () => {
    expect(parseTrajectoryEntry(ENTRY)).toEqual(ENTRY);
  });

  it("rejects entries from an unknown schema or source", () => {
    expect(parseTrajectoryEntry({ ...ENTRY, schema_version: "trajectory.v2" })).toBeNull();
    expect(parseTrajectoryEntry({ ...ENTRY, source: "elsewhere" })).toBeNull();
    expect(parseTrajectoryEntry(null)).toBeNull();
  });

  it("keeps an untimed entry as untimed instead of inventing a time", () => {
    expect(parseTrajectoryEntry({ ...ENTRY, timestamp: null })?.timestamp).toBeNull();
  });

  it("degrades an unknown status to info and a malformed receipt to none", () => {
    const parsed = parseTrajectoryEntry({ ...ENTRY, status: "great", receipt: { id: 3 } });
    expect(parsed?.status).toBe("info");
    expect(parsed?.receipt).toBeNull();
  });

  it("drops a receipt href that is not a same-origin API path", () => {
    const parsed = parseTrajectoryEntry({
      ...ENTRY,
      receipt: { kind: "goal_run", id: "x", href: "https://evil.example/steal" },
    });
    expect(parsed?.receipt?.href).toBeNull();
  });
});

describe("parseTrajectoryPage", () => {
  it("keeps valid entries, drops malformed ones, and reads source status", () => {
    const page = parseTrajectoryPage({
      schema_version: "trajectory.v1",
      conversation_id: "conv_1",
      entries: [ENTRY, { nope: true }],
      sources: { ams: { status: "unreachable", count: 0 } },
      scope: { dropped: 2 },
    });
    expect(page?.entries).toEqual([ENTRY]);
    expect(page?.sources.ams?.status).toBe("unreachable");
    expect(page?.dropped).toBe(2);
  });

  it("returns null for a body that is not a trajectory page", () => {
    expect(parseTrajectoryPage({ schema_version: "other" })).toBeNull();
  });
});

describe("fetchTrajectory", () => {
  it("encodes the conversation and goal ids", async () => {
    mockedFetch.mockResolvedValue(
      response(200, { schema_version: "trajectory.v1", conversation_id: "c/1", entries: [] }),
    );
    await fetchTrajectory("c/1", "g 1");
    expect(mockedFetch).toHaveBeenCalledWith("/v1/trajectory/c%2F1?goal_id=g%201");
  });

  it("throws on an HTTP error so the caller keeps its last truth", async () => {
    mockedFetch.mockResolvedValue(response(500, {}));
    await expect(fetchTrajectory("c")).rejects.toThrow("HTTP 500");
  });
});

describe("fetchReceipt", () => {
  it("fetches only same-origin API paths", async () => {
    mockedFetch.mockResolvedValue(response(200, { event_id: "e1" }));
    await expect(fetchReceipt("/v1/goal/run-1")).resolves.toEqual({ event_id: "e1" });
    await expect(fetchReceipt("https://evil.example")).rejects.toThrow();
    expect(mockedFetch).toHaveBeenCalledTimes(1);
  });
});
