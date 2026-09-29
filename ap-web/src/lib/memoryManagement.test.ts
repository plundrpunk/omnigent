import { beforeEach, describe, expect, it, vi } from "vitest";
import { managementClient } from "./memoryManagement";
import { hostFetch } from "./host";
vi.mock("./host", () => ({ hostFetch: vi.fn() }));
describe("management transport", () => {
  beforeEach(() => vi.resetAllMocks());
  it("preserves soft_delete and handles 204", async () => {
    vi.mocked(hostFetch).mockResolvedValue(new Response(null, { status: 204 }));
    await managementClient.remove("selected-id", true); expect(hostFetch).toHaveBeenLastCalledWith("/v1/ams/management/memories/selected-id?soft_delete=true", { method: "DELETE" });
    await managementClient.remove("selected-id", false); expect(hostFetch).toHaveBeenLastCalledWith("/v1/ams/management/memories/selected-id?soft_delete=false", { method: "DELETE" });
  });
  it("never falls back to shared-key routes", async () => {
    vi.mocked(hostFetch).mockResolvedValue(new Response(null, { status: 503 })); await expect(managementClient.detail("memories", "forged-id")).rejects.toThrow("ownership"); expect(hostFetch).toHaveBeenCalledTimes(1);
  });
});
