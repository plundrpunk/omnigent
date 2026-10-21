export const HARNESS_RECEIPT_SCHEMA_V1 = "harness.receipt.v1" as const;

export type HarnessVerifierStatus = "passed" | "failed" | "not_run" | "error";
export type HarnessReceiptStatus = "completed" | "failed" | "blocked";

export interface HarnessVerifierResultV1 {
  status: HarnessVerifierStatus;
  verdict?: string;
  reason?: string;
  evidence: string[];
  verifier_event_id?: string;
  tool_result_ids: string[];
}

export interface HarnessArtifactRefV1 {
  artifact_id: string;
  artifact_path?: string;
  trace_report?: string;
  changed_files: string[];
}

export interface HarnessReceiptEventV1 {
  schema_version: typeof HARNESS_RECEIPT_SCHEMA_V1;
  event_id: string;
  user_id: string;
  project: string;
  work_item_id: string;
  continuation_id?: string;
  session_id: string;
  response_id?: string;
  trace_id?: string;
  status: HarnessReceiptStatus;
  created_at: string;
  verifier: HarnessVerifierResultV1;
  artifact: HarnessArtifactRefV1;
  tool_result_ids: string[];
  cost_usd?: number;
  metadata: Record<string, unknown>;
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

export function parseHarnessReceiptEventV1(value: unknown): HarnessReceiptEventV1 | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  if (raw.schema_version !== HARNESS_RECEIPT_SCHEMA_V1) return null;
  if (
    typeof raw.event_id !== "string" ||
    typeof raw.user_id !== "string" ||
    typeof raw.project !== "string" ||
    typeof raw.work_item_id !== "string" ||
    typeof raw.session_id !== "string" ||
    typeof raw.created_at !== "string"
  ) {
    return null;
  }
  if (raw.status !== "completed" && raw.status !== "failed" && raw.status !== "blocked") {
    return null;
  }
  const verifierRaw = raw.verifier;
  const artifactRaw = raw.artifact;
  if (
    !verifierRaw ||
    typeof verifierRaw !== "object" ||
    Array.isArray(verifierRaw) ||
    !artifactRaw ||
    typeof artifactRaw !== "object" ||
    Array.isArray(artifactRaw)
  ) {
    return null;
  }
  const verifier = verifierRaw as Record<string, unknown>;
  const artifact = artifactRaw as Record<string, unknown>;
  if (
    verifier.status !== "passed" &&
    verifier.status !== "failed" &&
    verifier.status !== "not_run" &&
    verifier.status !== "error"
  ) {
    return null;
  }
  if (typeof artifact.artifact_id !== "string") return null;
  return {
    schema_version: HARNESS_RECEIPT_SCHEMA_V1,
    event_id: raw.event_id,
    user_id: raw.user_id,
    project: raw.project,
    work_item_id: raw.work_item_id,
    ...(optionalString(raw.continuation_id)
      ? { continuation_id: optionalString(raw.continuation_id) }
      : {}),
    session_id: raw.session_id,
    ...(optionalString(raw.response_id) ? { response_id: optionalString(raw.response_id) } : {}),
    ...(optionalString(raw.trace_id) ? { trace_id: optionalString(raw.trace_id) } : {}),
    status: raw.status,
    created_at: raw.created_at,
    verifier: {
      status: verifier.status,
      ...(optionalString(verifier.verdict) ? { verdict: optionalString(verifier.verdict) } : {}),
      ...(optionalString(verifier.reason) ? { reason: optionalString(verifier.reason) } : {}),
      evidence: strings(verifier.evidence),
      ...(optionalString(verifier.verifier_event_id)
        ? { verifier_event_id: optionalString(verifier.verifier_event_id) }
        : {}),
      tool_result_ids: strings(verifier.tool_result_ids),
    },
    artifact: {
      artifact_id: artifact.artifact_id,
      ...(optionalString(artifact.artifact_path)
        ? { artifact_path: optionalString(artifact.artifact_path) }
        : {}),
      ...(optionalString(artifact.trace_report)
        ? { trace_report: optionalString(artifact.trace_report) }
        : {}),
      changed_files: strings(artifact.changed_files),
    },
    tool_result_ids: strings(raw.tool_result_ids),
    ...(typeof raw.cost_usd === "number" && raw.cost_usd >= 0 ? { cost_usd: raw.cost_usd } : {}),
    metadata:
      raw.metadata && typeof raw.metadata === "object" && !Array.isArray(raw.metadata)
        ? (raw.metadata as Record<string, unknown>)
        : {},
  };
}
