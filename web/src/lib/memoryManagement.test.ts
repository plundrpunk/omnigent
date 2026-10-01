import { beforeEach, describe, expect, it, vi } from "vitest";
import { managementClient } from "./memoryManagement";
import { authenticatedFetch } from "./identity";

vi.mock("./identity", () => ({ authenticatedFetch: vi.fn() }));
describe("management transport", () => {
  beforeEach(() => vi.resetAllMocks());
  it("preserves soft_delete and handles 204", async () => {
    vi.mocked(authenticatedFetch).mockResolvedValue(new Response(null, { status: 204 }));
    await managementClient.remove("selected-id", true);
    expect(authenticatedFetch).toHaveBeenLastCalledWith(
      "/v1/ams/management/memories/selected-id?soft_delete=true",
      { method: "DELETE" },
    );
    await managementClient.remove("selected-id", false);
    expect(authenticatedFetch).toHaveBeenLastCalledWith(
      "/v1/ams/management/memories/selected-id?soft_delete=false",
      { method: "DELETE" },
    );
  });
  it("does not retry failed management through the general bridge", async () => {
    vi.mocked(authenticatedFetch).mockResolvedValue(new Response(null, { status: 503 }));
    await expect(managementClient.detail("memories", "forged-id")).rejects.toThrow(
      "AMS connection",
    );
    expect(authenticatedFetch).toHaveBeenCalledTimes(1);
  });
  it("explains a separately prepared account denial", async () => {
    vi.mocked(authenticatedFetch).mockResolvedValue(new Response(null, { status: 403 }));
    await expect(managementClient.access()).rejects.toThrow("direct local instance");
    expect(authenticatedFetch).toHaveBeenCalledTimes(1);
  });
});
