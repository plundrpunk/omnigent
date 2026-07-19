import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchProviders, fetchRoleMappings, putRoleMappings } from "@/lib/ams";
import { ModelsPage } from "./ModelsPage";

vi.mock("@/lib/ams", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/ams")>();
  return {
    ...actual,
    fetchProviders: vi.fn(),
    fetchRoleMappings: vi.fn(),
    putRoleMappings: vi.fn(),
  };
});

const mockedProviders = vi.mocked(fetchProviders);
const mockedRoles = vi.mocked(fetchRoleMappings);
const mockedPut = vi.mocked(putRoleMappings);

beforeEach(() => {
  mockedProviders.mockResolvedValue({
    providers: [
      { name: "Grok Code", type: "grok_code", status: "online" },
      { name: "Kimi Code", type: "kimi_code", status: "error" },
    ],
    active_provider: "kimi_code",
  });
  mockedRoles.mockResolvedValue({ orchestrator: "kimi_code", agent: "kimi_code" });
  mockedPut.mockResolvedValue({ ok: true });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ModelsPage role picker", () => {
  it("opens the picker on role click and PUTs only the changed role", async () => {
    render(<ModelsPage />);
    await screen.findByTestId("role-node-orchestrator");

    fireEvent.click(screen.getByTestId("role-node-orchestrator"));
    const picker = await screen.findByTestId("role-picker");
    expect(picker).toBeInTheDocument();
    // Current mapping is disabled and labeled — you can't "change" to it.
    expect(screen.getByRole("button", { name: /kimi_code \(current\)/ })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /grok_code/ }));
    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith({ orchestrator: "grok_code" }),
    );
    // Redraw comes from a refetch, not an optimistic write.
    await waitFor(() => expect(mockedRoles).toHaveBeenCalledTimes(2));
  });

  it("keeps the diagram untouched and shows the AMS error verbatim on 400", async () => {
    mockedPut.mockRejectedValue(new Error("Provider 'grok_code' not registered. Available: []"));
    render(<ModelsPage />);
    await screen.findByTestId("role-node-orchestrator");

    fireEvent.click(screen.getByTestId("role-node-orchestrator"));
    fireEvent.click(screen.getByRole("button", { name: /grok_code/ }));

    expect(await screen.findByText(/not registered/)).toBeInTheDocument();
    // No refetch — the mapping AMS holds is unchanged.
    expect(mockedRoles).toHaveBeenCalledTimes(1);
  });

  it("closes the picker via cancel without writing", async () => {
    render(<ModelsPage />);
    await screen.findByTestId("role-node-agent");

    fireEvent.click(screen.getByTestId("role-node-agent"));
    await screen.findByTestId("role-picker");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByTestId("role-picker")).toBeNull();
    expect(mockedPut).not.toHaveBeenCalled();
  });
});
