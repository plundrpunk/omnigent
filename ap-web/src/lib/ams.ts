/**
 * Shared client for the server's AMS bridge (`/v1/ams/*`).
 *
 * All requests ride {@link hostFetch} so they work standalone and
 * embedded; the AMS API key never reaches the browser (the bridge
 * attaches it server-side — see `omnigent/server/routes/ams.py`).
 */

import { hostFetch } from "@/lib/host";

export class AmsError extends Error {
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "AmsError";
    this.status = status;
  }
}

/** GET an AMS path (relative, e.g. `api/warden/agents`) via the bridge. */
export async function amsGet<T = unknown>(path: string): Promise<T> {
  const resp = await hostFetch(`/v1/ams/${path}`);
  const body: unknown = await resp.json().catch(() => null);
  if (!resp.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? JSON.stringify((body as { detail: unknown }).detail).slice(0, 300)
        : `HTTP ${resp.status}`;
    throw new AmsError(detail, resp.status);
  }
  return body as T;
}

// ── Warden fleet ─────────────────────────────────────────────

export interface WardenAgent {
  agent_id: string;
  agent_name: string;
  status: string;
  alive: boolean;
  context_pct: number;
  registered_at?: string;
  metadata?: Record<string, unknown>;
}

export async function fetchWardenAgents(): Promise<WardenAgent[]> {
  const body = await amsGet<{ agents?: WardenAgent[] }>("api/warden/agents");
  return body.agents ?? [];
}

// ── Observatory executions ───────────────────────────────────

export interface ObservatoryExecution {
  execution_id: string;
  agent_id: string;
  agent_name: string;
  /** JSON string; may carry parent_execution_id / correlation_id. */
  task?: string;
  model?: string;
  status: string;
  created_at?: number;
  started_at?: number;
  /** Parsed out of `task` by {@link parseExecutionTask}. */
  parent_execution_id?: string | null;
  task_summary?: string;
}

/** Parse the `task` JSON payload, folding parentage onto the record. */
export function parseExecutionTask(exec: ObservatoryExecution): ObservatoryExecution {
  if (!exec.task) return exec;
  try {
    const parsed: unknown = JSON.parse(exec.task);
    if (parsed && typeof parsed === "object") {
      const obj = parsed as Record<string, unknown>;
      return {
        ...exec,
        parent_execution_id:
          typeof obj.parent_execution_id === "string" ? obj.parent_execution_id : null,
        task_summary: typeof obj.task === "string" ? obj.task : exec.task,
      };
    }
  } catch {
    // Task payloads are free-form; a non-JSON task is just its own summary.
  }
  return { ...exec, task_summary: exec.task };
}

export async function fetchExecutions(): Promise<ObservatoryExecution[]> {
  const body = await amsGet<{ executions?: ObservatoryExecution[] }>("observatory/ps");
  return (body.executions ?? []).map(parseExecutionTask);
}

// ── LLM providers / role mappings ────────────────────────────

export interface LlmProvider {
  name: string;
  type: string;
  endpoint?: string;
  model?: string;
  status?: string;
  latency_ms?: number | null;
  cost_per_mtok_input?: number | null;
  cost_per_mtok_output?: number | null;
}

export interface ProvidersResponse {
  providers?: LlmProvider[];
  role_mappings?: Record<string, string>;
}

export async function fetchProviders(): Promise<ProvidersResponse> {
  return amsGet<ProvidersResponse>("api/v1/llm-providers");
}

export async function fetchRoleMappings(): Promise<Record<string, string>> {
  return amsGet<Record<string, string>>("api/v1/llm-providers/role-mappings");
}

// ── Automata stats (training / eval gate window) ─────────────

export interface BayesianCategory {
  category: string;
  automata_count: number;
  total_executions: number;
  total_successes: number;
  total_failures: number;
  avg_success_rate: number;
  avg_ci_width?: number;
  avg_duration_ms?: number;
}

export interface BayesianStats {
  summary?: {
    total_automata: number;
    total_executions: number;
    total_successes: number;
    total_failures: number;
    overall_success_rate: number;
    high_confidence_count?: number;
  };
  categories?: BayesianCategory[];
}

export async function fetchBayesianStats(): Promise<BayesianStats> {
  return amsGet<BayesianStats>("api/v1/automata/stats/bayesian");
}
