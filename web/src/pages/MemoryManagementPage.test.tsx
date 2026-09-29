import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryManagementPage } from "./MemoryManagementPage";
import type { ManagementClient, ManagedRecord } from "@/lib/memoryManagement";

const first: ManagedRecord = {
  memory_id: "11111111-1111-4111-8111-111111111111",
  status: "active",
  memory_metadata: { title: "First memory" },
  full_content: "Full first content",
};
const second: ManagedRecord = {
  ...first,
  memory_id: "22222222-2222-4222-8222-222222222222",
  memory_metadata: { title: "Second memory" },
  full_content: "Full second content",
};
function client(): ManagementClient {
  return {
    access: vi.fn().mockResolvedValue({ available: true }),
    list: vi.fn().mockResolvedValue({ records: [first, second], total: 2 }),
    detail: vi
      .fn()
      .mockImplementation((_kind, id) => Promise.resolve(id === first.memory_id ? first : second)),
    remove: vi.fn().mockResolvedValue(undefined),
  };
}
async function openFirst() {
  fireEvent.click(await screen.findByRole("button", { name: /First memory/ }));
  await screen.findByText("Full first content");
}
describe("combined management", () => {
  it("fails closed before listing", async () => {
    const api = client();
    vi.mocked(api.access).mockResolvedValue({ available: false });
    render(<MemoryManagementPage client={api} />);
    await screen.findByText("Live management unavailable");
    expect(api.list).not.toHaveBeenCalled();
    expect(api.remove).not.toHaveBeenCalled();
  });
  it("inspection and cancellation never delete", async () => {
    const api = client();
    render(<MemoryManagementPage client={api} />);
    await openFirst();
    fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
    expect(screen.getByRole("button", { name: "Confirm permanent deletion" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.remove).not.toHaveBeenCalled();
  });
  it("requires exact ID and sends one deletion while pending", async () => {
    const api = client();
    let finish!: () => void;
    vi.mocked(api.remove).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    render(<MemoryManagementPage client={api} />);
    await openFirst();
    fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
    const input = screen.getByRole("textbox", { name: "Confirm memory ID" });
    fireEvent.change(input, { target: { value: "wrong" } });
    expect(screen.getByRole("button", { name: "Confirm permanent deletion" })).toBeDisabled();
    fireEvent.change(input, { target: { value: first.memory_id } });
    const button = screen.getByRole("button", { name: "Confirm permanent deletion" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(api.remove).toHaveBeenCalledTimes(1);
    expect(api.remove).toHaveBeenCalledWith(first.memory_id, false);
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    finish();
    await screen.findByText("Selected memory permanently deleted.");
  });
  it("keeps failed archive open without claiming success", async () => {
    const api = client();
    vi.mocked(api.remove).mockRejectedValue(new Error("Archive failed"));
    render(<MemoryManagementPage client={api} />);
    await openFirst();
    fireEvent.click(screen.getByRole("button", { name: "Archive memory" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm archive" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(api.remove).toHaveBeenCalledWith(first.memory_id, true);
    expect(screen.queryByText("Selected memory archived.")).not.toBeInTheDocument();
  });
  it("ignores stale detail responses", async () => {
    const api = client();
    let finish!: (value: ManagedRecord) => void;
    vi.mocked(api.detail).mockImplementation((_kind, id) =>
      id === first.memory_id
        ? new Promise((resolve) => {
            finish = resolve;
          })
        : Promise.resolve(second),
    );
    render(<MemoryManagementPage client={api} />);
    fireEvent.click(await screen.findByRole("button", { name: /First memory/ }));
    fireEvent.click(screen.getByRole("button", { name: /Second memory/ }));
    await screen.findByText("Full second content");
    finish(first);
    await waitFor(() => expect(screen.queryByText("Full first content")).not.toBeInTheDocument());
  });
  it("distinguishes failure and empty results on retry", async () => {
    const api = client();
    vi.mocked(api.list)
      .mockRejectedValueOnce(new Error("Unavailable now"))
      .mockResolvedValue({ records: [], total: 0 });
    render(<MemoryManagementPage client={api} />);
    await screen.findByText("Unavailable now");
    expect(screen.queryByText("No records match these filters.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText("No records match these filters.");
  });
  it("inspects continuation notes without removal", async () => {
    const api = client();
    const record = {
      continuation_id: "33333333-3333-4333-8333-333333333333",
      status: "pending",
      original_goal: "Finish QA",
      handoff_notes: "Exact notes",
    };
    vi.mocked(api.list).mockResolvedValue({ records: [record] });
    vi.mocked(api.detail).mockResolvedValue(record);
    render(<MemoryManagementPage client={api} />);
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Continuations" }), {
      button: 0,
      ctrlKey: false,
    });
    fireEvent.click(await screen.findByRole("button", { name: /Finish QA/ }));
    await screen.findByText("Exact notes");
    expect(screen.queryByRole("button", { name: "Delete permanently" })).not.toBeInTheDocument();
    expect(api.remove).not.toHaveBeenCalled();
  });
});
