import { afterEach, describe, expect, it, vi } from "vitest";

import { checkpointResumeCommand, fetchGoalRuns } from "./goal";
import { hostFetch } from "./host";

vi.mock("./host", () => ({ hostFetch: vi.fn() }));

const mockedFetch = vi.mocked(hostFetch);

function response(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("fetchGoalRuns", () => {
  it("returns the runs list for the conversation", async () => {
    mockedFetch.mockResolvedValue(response(200, { runs: [{ run_id: "r1" }] }));
    const runs = await fetchGoalRuns("conv/1");
    expect(runs).toEqual([{ run_id: "r1" }]);
    // The conversation id must be encoded, not spliced.
    expect(mockedFetch).toHaveBeenCalledWith("/v1/goal?conversation_id=conv%2F1");
  });

  it("maps an unconfigured bridge (503) to null, not an empty list", async () => {
    mockedFetch.mockResolvedValue(response(503, { detail: "not configured" }));
    await expect(fetchGoalRuns("c")).resolves.toBeNull();
  });

  it("treats a malformed body as empty, never inventing runs", async () => {
    mockedFetch.mockResolvedValue(response(200, { runs: "nope" }));
    await expect(fetchGoalRuns("c")).resolves.toEqual([]);
  });

  it("throws on other HTTP errors so callers keep the last truth", async () => {
    mockedFetch.mockResolvedValue(response(500, null));
    await expect(fetchGoalRuns("c")).rejects.toThrow("HTTP 500");
  });
});

describe("checkpointResumeCommand", () => {
  it("extracts resume_command from a verbatim checkpoint", () => {
    const checkpoint = JSON.stringify({ resume_command: "automaton goal --resume x" });
    expect(checkpointResumeCommand(checkpoint)).toBe("automaton goal --resume x");
  });

  it("returns null for absent, non-JSON, or command-less checkpoints", () => {
    expect(checkpointResumeCommand(null)).toBeNull();
    expect(checkpointResumeCommand("not json {")).toBeNull();
    expect(checkpointResumeCommand(JSON.stringify({ note: "no command" }))).toBeNull();
    expect(checkpointResumeCommand(JSON.stringify({ resume_command: "  " }))).toBeNull();
  });
});
