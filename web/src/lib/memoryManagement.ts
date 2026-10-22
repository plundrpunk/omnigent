import { authenticatedFetch } from "@/lib/identity";

export type RecordKind = "memories" | "continuations";
export interface ManagedRecord {
  memory_id?: string;
  continuation_id?: string;
  file_path?: string;
  memory_tier?: string;
  memory_metadata?: Record<string, unknown>;
  status: string;
  project?: string;
  original_goal?: string;
  next_action?: string;
  handoff_notes?: string;
  full_content?: string;
  content?: string;
  [key: string]: unknown;
}
export interface BrowseOptions {
  kind: RecordKind;
  tier: string;
  status: string;
  project: string;
  offset: number;
}
export interface ManagementAccess {
  available: boolean;
  reason?: string;
}
export interface ManagementClient {
  access: () => Promise<ManagementAccess>;
  list: (options: BrowseOptions) => Promise<{ records: ManagedRecord[]; total?: number }>;
  detail: (kind: RecordKind, id: string) => Promise<ManagedRecord>;
  remove: (id: string, archive: boolean) => Promise<void>;
}
export const recordId = (record: ManagedRecord) => record.memory_id ?? record.continuation_id ?? "";
export function recordTitle(record: ManagedRecord): string {
  const title = record.memory_metadata?.title;
  return (
    record.original_goal ||
    (typeof title === "string" ? title : "") ||
    record.file_path?.split("/").pop() ||
    recordId(record)
  );
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(`/v1/ams/management/${path}`, init);
  if (!response.ok)
    throw new Error(
      response.status === 503
        ? "Live management is unavailable until account ownership is verified."
        : "The request failed. Refresh and try again; no success was confirmed.",
    );
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

// Dedicated namespace: never fall back to the existing shared-key bridge.
// The server deliberately denies record access until the identity contract is implemented.
export const managementClient: ManagementClient = {
  access: () => request<ManagementAccess>("status"),
  async list({ kind, tier, status, project, offset }) {
    const query = new URLSearchParams(
      kind === "memories"
        ? { status, limit: "25", offset: String(offset), ...(tier ? { memory_tier: tier } : {}) }
        : { limit: "50", ...(project ? { project } : {}) },
    );
    if (kind === "memories") {
      const data = await request<{ memories: ManagedRecord[]; total: number }>(
        `memories/?${query}`,
      );
      return { records: data.memories, total: data.total };
    }
    const data = await request<{ continuations: ManagedRecord[] }>(
      `continuations/pending?${query}`,
    );
    return { records: data.continuations };
  },
  detail: (kind, id) => request<ManagedRecord>(`${kind}/${encodeURIComponent(id)}`),
  remove: (id, archive) =>
    request<void>(`memories/${encodeURIComponent(id)}?soft_delete=${archive}`, {
      method: "DELETE",
    }),
};
