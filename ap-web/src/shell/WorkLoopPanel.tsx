import { useMemo } from "react";
import {
  AlertTriangleIcon,
  BotIcon,
  CheckCircle2Icon,
  CircleIcon,
  FileCheck2Icon,
  LoaderCircleIcon,
  RouteIcon,
  ShieldAlertIcon,
  WaypointsIcon,
  XCircleIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import type { AnyBlock } from "@/lib/blocks";
import { checkpointResumeCommand, type GoalRun } from "@/lib/goal";
import { Link } from "@/lib/routing";
import type { SessionStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useGoalRuns } from "@/hooks/useGoalRuns";
import type { SessionLiveness } from "@/hooks/useSessionLiveness";
import { useChatStore } from "@/store/chatStore";

type LoopStageState = "complete" | "active" | "attention" | "blocked" | "pending";

interface LoopStage {
  id: "intake" | "run" | "verify" | "receipt";
  label: string;
  detail: string;
  state: LoopStageState;
}

interface DeriveWorkLoopInput {
  blocks: AnyBlock[];
  sessionStatus: SessionStatus;
  uiStatus: "idle" | "streaming";
  liveness: SessionLiveness;
  pendingApprovalCount: number;
  changedCount: number;
  agentCount: number;
  agentsWorking: number;
  fallbackObjective?: string | null;
}

export interface WorkLoopSnapshot {
  objective: string | null;
  readiness: {
    label: string;
    detail: string;
    tone: "ready" | "starting" | "blocked" | "unknown";
  };
  overallLabel: string;
  overallTone: "ready" | "active" | "attention" | "blocked" | "muted";
  stages: LoopStage[];
  pendingApprovals: number;
  toolCount: number;
  artifactCount: number;
  latestResponseId: string | null;
  resultStatus: string;
  verifierStatus: string;
  verifierTone: "success" | "warning" | "error";
  verifierReason: string | null;
  receiptEventId: string | null;
  workItemId: string | null;
  artifactId: string | null;
  errorMessage: string | null;
}

export interface WorkLoopPanelProps {
  conversationId: string;
  sessionTitle?: string | null;
  liveness: SessionLiveness;
  pendingApprovalCount: number;
  changedCount: number;
  agentCount: number;
  agentsWorking: number;
  showFilesPanel: boolean;
  onOpenFiles: () => void;
  onOpenAgents: () => void;
}

const STAGE_TONE: Record<LoopStageState, string> = {
  complete: "border-success/30 bg-success/10 text-success",
  active: "border-primary/30 bg-primary/10 text-primary",
  attention: "border-warning/30 bg-warning/10 text-warning",
  blocked: "border-destructive/30 bg-destructive/10 text-destructive",
  pending: "border-border bg-muted/40 text-muted-foreground",
};

const OVERALL_TONE: Record<WorkLoopSnapshot["overallTone"], string> = {
  ready: "bg-success/15 text-success",
  active: "bg-primary/15 text-primary",
  attention: "bg-warning/15 text-warning",
  blocked: "bg-destructive/15 text-destructive",
  muted: "bg-muted text-muted-foreground",
};

function latestBlock<T extends AnyBlock["type"]>(
  blocks: AnyBlock[],
  type: T,
): Extract<AnyBlock, { type: T }> | null {
  for (let i = blocks.length - 1; i >= 0; i -= 1) {
    const block = blocks[i];
    if (block.type === type) return block as Extract<AnyBlock, { type: T }>;
  }
  return null;
}

function clipText(value: string, max = 220): string {
  const clean = value.replace(/\s+/g, " ").trim();
  if (clean.length <= max) return clean;
  return `${clean.slice(0, max - 1).trimEnd()}…`;
}

function objectiveFromBlocks(blocks: AnyBlock[]): string | null {
  const message = latestBlock(blocks, "user_message");
  if (!message) return null;
  const text = message.content
    .filter((part) => part.type === "input_text")
    .map((part) => part.text)
    .join(" ");
  return text.trim() ? clipText(text) : null;
}

function readinessFor(liveness: SessionLiveness): WorkLoopSnapshot["readiness"] {
  switch (liveness.kind) {
    case "online":
      return {
        label: "Execution ready",
        detail: "The runner is connected and can accept work.",
        tone: "ready",
      };
    case "starting":
      return {
        label: "Execution target starting",
        detail: "The runner is connecting. The loop can continue when it registers.",
        tone: "starting",
      };
    case "runner_asleep":
      return {
        label: "Ready on send",
        detail: "The host is reachable and will wake the runner on the next turn.",
        tone: "ready",
      };
    case "host_offline":
      return {
        label: "Host offline",
        detail: liveness.isOwner
          ? "Execution is blocked. Reconnect the host from its machine or fork the session."
          : "Execution is blocked. The session owner must reconnect the host, or you can fork it.",
        tone: "blocked",
      };
    case "local_stranded":
      return {
        label: "Runner unavailable",
        detail: "This session has no reachable host. Restart locally or fork to continue.",
        tone: "blocked",
      };
    case "unknown":
      return {
        label: "Checking execution target",
        detail: "Runner and host liveness have not been observed yet.",
        tone: "unknown",
      };
  }
}

/**
 * Fold the existing session truth into the Work Loop view.
 *
 * Verification is deliberately never inferred from prose or a successful
 * response. Until the server emits a verifier result, the UI reports the gap
 * as "Not reported" instead of manufacturing a green checkmark.
 */
export function deriveWorkLoopSnapshot(input: DeriveWorkLoopInput): WorkLoopSnapshot {
  const objective = objectiveFromBlocks(input.blocks) ?? input.fallbackObjective?.trim() ?? null;
  const readiness = readinessFor(input.liveness);
  const responseEnd = latestBlock(input.blocks, "response_end");
  const receiptBlock = latestBlock(input.blocks, "work_receipt");
  const receipt = receiptBlock?.receipt ?? null;
  const latestError = latestBlock(input.blocks, "error");
  let latestTraceBlock: AnyBlock | null = null;
  for (let i = input.blocks.length - 1; i >= 0; i -= 1) {
    if (input.blocks[i].ctx.responseId.trim()) {
      latestTraceBlock = input.blocks[i];
      break;
    }
  }
  const executionStarted = input.blocks.some(
    (block) =>
      block.type === "response_start" ||
      block.type === "tool_group" ||
      block.type === "native_tool" ||
      block.type === "work_receipt" ||
      block.type === "response_end",
  );
  const executionActive =
    input.uiStatus === "streaming" ||
    input.sessionStatus === "running" ||
    input.sessionStatus === "waiting" ||
    input.agentsWorking > 0;
  // History hydration retains response-scoped blocks but not every transient
  // response_end event. Idle + persisted execution evidence is therefore a
  // settled run with an unavailable terminal status, not an active run and
  // not proof of completion.
  const executionSettled =
    receipt !== null ||
    responseEnd !== null ||
    (executionStarted && input.sessionStatus === "idle" && input.uiStatus === "idle");
  const executionFailed =
    receipt?.status === "failed" ||
    receipt?.status === "blocked" ||
    input.sessionStatus === "failed" ||
    latestError !== null;
  const executionBlocked = readiness.tone === "blocked";
  const pendingFromBlocks = input.blocks.filter(
    (block) => block.type === "elicitation" && block.status === "pending",
  ).length;
  const pendingApprovals = Math.max(input.pendingApprovalCount, pendingFromBlocks);
  const toolCount = input.blocks.reduce((count, block) => {
    if (block.type === "tool_group") return count + block.executions.length;
    if (block.type === "native_tool") return count + 1;
    return count;
  }, 0);
  const observedArtifactCount =
    input.changedCount + input.blocks.filter((block) => block.type === "file").length;
  const receiptArtifactCount = receipt ? Math.max(1, receipt.artifact.changed_files.length) : 0;
  const artifactCount = Math.max(observedArtifactCount, receiptArtifactCount);

  let runState: LoopStageState = "pending";
  let runDetail = "Waiting for the first execution event.";
  if (receipt) {
    runState = receipt.status === "completed" ? "complete" : "blocked";
    runDetail = `Harness reported ${receipt.status} for artifact ${receipt.artifact.artifact_id}.`;
  } else if (executionBlocked) {
    runState = "blocked";
    runDetail = readiness.detail;
  } else if (executionFailed) {
    runState = "blocked";
    runDetail = latestError?.message || latestError?.code || "The latest run failed.";
  } else if (executionActive) {
    runState = "active";
    runDetail = `${Math.max(input.agentsWorking, 1)} agent${Math.max(input.agentsWorking, 1) === 1 ? " is" : "s are"} working.`;
  } else if (responseEnd) {
    runState = "complete";
    runDetail = `Run ended with status: ${responseEnd.status}.`;
  } else if (executionSettled) {
    runState = "attention";
    runDetail = "Run is idle; its explicit terminal status was not retained in history.";
  } else if (executionStarted) {
    runState = "attention";
    runDetail = "Execution emitted events but has no terminal response yet.";
  }

  let verifyState: LoopStageState = "pending";
  let verifyDetail = "Waiting for a terminal run result.";
  if (receipt?.verifier.status === "passed") {
    verifyState = "complete";
    verifyDetail = receipt.verifier.reason ?? "Harness verifier accepted the execution evidence.";
  } else if (receipt?.verifier.status === "failed" || receipt?.verifier.status === "error") {
    verifyState = "blocked";
    verifyDetail =
      receipt.verifier.reason ?? "Harness verifier did not accept the execution evidence.";
  } else if (receipt?.verifier.status === "not_run") {
    verifyState = "attention";
    verifyDetail = receipt.verifier.reason ?? "Harness reported that verifier review did not run.";
  } else if (executionFailed) {
    verifyState = "blocked";
    verifyDetail = "Verification cannot complete while the run is failed.";
  } else if (pendingApprovals > 0) {
    verifyState = "attention";
    verifyDetail = `${pendingApprovals} human gate${pendingApprovals === 1 ? " is" : "s are"} waiting in Inbox.`;
  } else if (executionSettled) {
    verifyState = "attention";
    verifyDetail = "Verifier evidence is not reported by the current session contract.";
  }

  const stages: LoopStage[] = [
    {
      id: "intake",
      label: "Intake",
      detail: objective
        ? "Objective captured from the latest user turn."
        : "No objective captured yet.",
      state: objective ? "complete" : "pending",
    },
    { id: "run", label: "Run", detail: runDetail, state: runState },
    { id: "verify", label: "Verify", detail: verifyDetail, state: verifyState },
    {
      id: "receipt",
      label: "Receipt",
      detail: receipt
        ? `Versioned ${receipt.schema_version} receipt is persisted and available below.`
        : responseEnd
          ? "Trace and outcome fields are available below."
          : executionSettled
            ? "Trace recovered from history; terminal outcome is unavailable."
            : "A receipt appears when the run reaches a terminal response.",
      state: receipt || responseEnd ? "complete" : executionSettled ? "attention" : "pending",
    },
  ];

  const verifierPassed = receipt?.verifier.status === "passed";

  let overallLabel = objective ? "Ready" : "Draft";
  let overallTone: WorkLoopSnapshot["overallTone"] = objective ? "ready" : "muted";
  if (verifierPassed && receipt?.status === "completed") {
    overallLabel = "Verified";
    overallTone = "ready";
  } else if (receipt?.verifier.status === "failed" || receipt?.verifier.status === "error") {
    overallLabel = "Verification failed";
    overallTone = "blocked";
  } else if (receipt?.verifier.status === "not_run") {
    overallLabel = "Needs verification";
    overallTone = "attention";
  } else if (executionBlocked || executionFailed) {
    overallLabel = "Blocked";
    overallTone = "blocked";
  } else if (executionActive) {
    overallLabel = "Running";
    overallTone = "active";
  } else if (pendingApprovals > 0) {
    overallLabel = "Needs review";
    overallTone = "attention";
  } else if (executionSettled) {
    overallLabel = "Needs verification";
    overallTone = "attention";
  }

  return {
    objective,
    readiness,
    overallLabel,
    overallTone,
    stages,
    pendingApprovals,
    toolCount,
    artifactCount,
    latestResponseId:
      receipt?.response_id ??
      responseEnd?.response?.id ??
      responseEnd?.ctx.responseId ??
      latestTraceBlock?.ctx.responseId ??
      null,
    resultStatus:
      receipt?.status ??
      responseEnd?.status ??
      (executionSettled ? "settled · status unavailable" : input.sessionStatus),
    verifierStatus:
      receipt?.verifier.status === "passed"
        ? "Passed"
        : receipt?.verifier.status === "failed"
          ? "Failed"
          : receipt?.verifier.status === "error"
            ? "Error"
            : receipt?.verifier.status === "not_run"
              ? "Not run"
              : "Not reported",
    verifierTone:
      receipt?.verifier.status === "passed"
        ? "success"
        : receipt?.verifier.status === "failed" || receipt?.verifier.status === "error"
          ? "error"
          : "warning",
    verifierReason: receipt?.verifier.reason ?? null,
    receiptEventId: receipt?.event_id ?? null,
    workItemId: receipt?.work_item_id ?? null,
    artifactId: receipt?.artifact.artifact_id ?? null,
    errorMessage: latestError?.message || latestError?.code || null,
  };
}

const GOAL_STATUS_META: Record<
  GoalRun["status"],
  { label: string; tone: string; spinning?: boolean }
> = {
  running: { label: "Running", tone: "bg-primary/15 text-primary", spinning: true },
  completed: { label: "Gate passed", tone: "bg-success/15 text-success" },
  blocked: { label: "Blocked by gate", tone: "bg-destructive/15 text-destructive" },
  paused: { label: "Paused", tone: "bg-warning/15 text-warning" },
  setup_error: { label: "Setup error", tone: "bg-destructive/15 text-destructive" },
  error: { label: "Error", tone: "bg-destructive/15 text-destructive" },
};

function clipArtifact(text: string, max = 600): string {
  return text.length <= max ? text : `${text.slice(0, max)}…`;
}

/**
 * Verbatim rendering of the harness' goal runs (R9 V2).
 *
 * Every field shown here is the harness' own output: status is the CLI
 * exit code's mapping, blocker/checkpoint text is quoted as written.
 * The section renders nothing when there are no runs — absence is
 * absence, not an empty-state invitation.
 */
export function GoalRunsSection({ runs }: { runs: GoalRun[] }) {
  if (runs.length === 0) return null;
  return (
    <section
      data-testid="goal-runs-section"
      className="rounded-lg border border-border bg-card p-3"
    >
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-medium text-foreground">Goal runs (Harness)</span>
        <span className="font-mono text-[10px] text-muted-foreground">
          {runs.length} run{runs.length === 1 ? "" : "s"}
        </span>
      </div>
      <div className="flex flex-col gap-3">
        {runs.map((run) => {
          const meta = GOAL_STATUS_META[run.status] ?? {
            label: run.status,
            tone: "bg-muted text-muted-foreground",
          };
          const resume = checkpointResumeCommand(run.checkpoint);
          return (
            <div
              key={run.run_id}
              data-testid="goal-run-card"
              className="rounded-md border border-border/70 p-2.5"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-mono text-xs text-foreground" title={run.goal_id}>
                  {run.goal_id}
                </span>
                <span
                  className={cn(
                    "inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
                    meta.tone,
                  )}
                >
                  {meta.spinning && <LoaderCircleIcon className="size-3 animate-spin" />}
                  {meta.label}
                </span>
              </div>
              <p className="mt-1 font-mono text-[10px] text-muted-foreground">
                {run.exit_code === null ? "exit —" : `exit ${run.exit_code}`}
                {run.provider ? ` · ${run.provider}` : ""}
                {run.finished_at ? ` · finished ${run.finished_at}` : ""}
              </p>
              {run.error && (
                <p className="mt-1.5 text-[11px] leading-4 text-destructive">{run.error}</p>
              )}
              {run.blocker_md && (
                <div className="mt-1.5">
                  <p className="text-[10px] font-medium uppercase tracking-wide text-destructive">
                    Blocker (verbatim)
                  </p>
                  <pre className="mt-1 overflow-x-auto rounded bg-muted/40 p-2 font-mono text-[10px] leading-4 whitespace-pre-wrap text-foreground/90">
                    {clipArtifact(run.blocker_md)}
                  </pre>
                </div>
              )}
              {run.status === "paused" && (
                <div className="mt-1.5">
                  <p className="text-[10px] font-medium uppercase tracking-wide text-warning">
                    Resume command
                  </p>
                  <pre className="mt-1 overflow-x-auto rounded bg-muted/40 p-2 font-mono text-[10px] leading-4 whitespace-pre-wrap text-foreground/90">
                    {resume ?? clipArtifact(run.checkpoint ?? "checkpoint.json not reported")}
                  </pre>
                </div>
              )}
              {run.outcome && (
                <details className="mt-1.5">
                  <summary className="cursor-pointer text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                    goal-outcome.json
                  </summary>
                  <pre className="mt-1 max-h-48 overflow-auto rounded bg-muted/40 p-2 font-mono text-[10px] leading-4 whitespace-pre-wrap text-foreground/90">
                    {clipArtifact(JSON.stringify(run.outcome, null, 2), 2000)}
                  </pre>
                </details>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function StageIcon({ state }: { state: LoopStageState }) {
  if (state === "complete") return <CheckCircle2Icon className="size-4" />;
  if (state === "active") return <LoaderCircleIcon className="size-4 animate-spin" />;
  if (state === "attention") return <ShieldAlertIcon className="size-4" />;
  if (state === "blocked") return <XCircleIcon className="size-4" />;
  return <CircleIcon className="size-4" />;
}

function formatCost(cost: number | null): string {
  if (cost === null) return "—";
  return cost < 0.01 ? `$${cost.toFixed(4)}` : `$${cost.toFixed(2)}`;
}

export function WorkLoopPanel({
  conversationId,
  sessionTitle,
  liveness,
  pendingApprovalCount,
  changedCount,
  agentCount,
  agentsWorking,
  showFilesPanel,
  onOpenFiles,
  onOpenAgents,
}: WorkLoopPanelProps) {
  const blocks = useChatStore((state) => state.blocks);
  const sessionStatus = useChatStore((state) => state.sessionStatus);
  const uiStatus = useChatStore((state) => state.status);
  const sessionCostUsd = useChatStore((state) => state.sessionCostUsd);
  const boundAgentName = useChatStore((state) => state.boundAgentName);
  const gitBranch = useChatStore((state) => state.gitBranch);
  const goalRuns = useGoalRuns(conversationId);

  const snapshot = useMemo(
    () =>
      deriveWorkLoopSnapshot({
        blocks,
        sessionStatus,
        uiStatus,
        liveness,
        pendingApprovalCount,
        changedCount,
        agentCount,
        agentsWorking,
        fallbackObjective: sessionTitle,
      }),
    [
      blocks,
      sessionStatus,
      uiStatus,
      liveness,
      pendingApprovalCount,
      changedCount,
      agentCount,
      agentsWorking,
      sessionTitle,
    ],
  );

  const readinessTone = {
    ready: "border-success/30 bg-success/5 text-success",
    starting: "border-primary/30 bg-primary/5 text-primary",
    blocked: "border-destructive/30 bg-destructive/5 text-destructive",
    unknown: "border-border bg-muted/40 text-muted-foreground",
  }[snapshot.readiness.tone];

  return (
    <div data-testid="work-loop-panel" className="min-h-0 flex-1 overflow-y-auto p-3">
      <div className="flex flex-col gap-3">
        <header className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-2.5">
            <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <WaypointsIcon className="size-4" />
            </span>
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-foreground">Work Loop</h2>
              <p className="text-xs text-muted-foreground">Objective → run → verify → receipt</p>
            </div>
          </div>
          <span
            className={cn(
              "inline-flex shrink-0 items-center rounded-full px-2 py-1 text-[10px] font-semibold uppercase tracking-wide",
              OVERALL_TONE[snapshot.overallTone],
            )}
          >
            {snapshot.overallLabel}
          </span>
        </header>

        <section aria-live="polite" className={cn("rounded-lg border px-3 py-2.5", readinessTone)}>
          <div className="flex items-start gap-2">
            {snapshot.readiness.tone === "blocked" ? (
              <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
            ) : (
              <RouteIcon className="mt-0.5 size-4 shrink-0" />
            )}
            <div className="min-w-0">
              <p className="text-xs font-semibold">{snapshot.readiness.label}</p>
              <p className="mt-0.5 text-[11px] leading-4 opacity-80">{snapshot.readiness.detail}</p>
            </div>
          </div>
        </section>

        <section className="rounded-lg border border-border bg-card p-3">
          <div className="mb-2 text-xs font-medium text-foreground">Observed stages</div>
          <div className="grid grid-cols-2 gap-2">
            {snapshot.stages.map((stage) => (
              <div
                key={stage.id}
                className={cn(
                  "flex items-center gap-2 rounded-md border px-2 py-1.5 text-xs",
                  STAGE_TONE[stage.state],
                )}
              >
                <StageIcon state={stage.state} />
                <span className="font-medium">{stage.label}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-lg border border-border bg-card p-3">
          <div className="mb-2 flex items-center gap-2 text-xs font-medium text-foreground">
            <RouteIcon className="size-3.5 text-muted-foreground" />
            Objective
          </div>
          <p className="text-xs leading-5 text-foreground/90">
            {snapshot.objective ?? "Send a message to define this loop's objective."}
          </p>
        </section>

        <section className="rounded-lg border border-border bg-card p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-medium text-foreground">Run timeline</span>
            <span className="font-mono text-[10px] text-muted-foreground">
              {snapshot.toolCount} tool{snapshot.toolCount === 1 ? "" : "s"}
            </span>
          </div>
          <div className="flex flex-col gap-2">
            {snapshot.stages.map((stage) => (
              <div key={stage.id} className="flex items-start gap-2.5">
                <span
                  className={cn(
                    "mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full border",
                    STAGE_TONE[stage.state],
                  )}
                >
                  <StageIcon state={stage.state} />
                </span>
                <div className="min-w-0 flex-1 border-border/70 border-b pb-2 last:border-0 last:pb-0">
                  <p className="text-xs font-medium text-foreground">{stage.label}</p>
                  <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground">
                    {stage.detail}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </section>

        {goalRuns !== null && <GoalRunsSection runs={goalRuns} />}

        <section className="rounded-lg border border-border bg-card p-3">
          <div className="mb-3 flex items-center gap-2 text-xs font-medium text-foreground">
            <FileCheck2Icon className="size-3.5 text-muted-foreground" />
            Receipt
          </div>
          <dl className="grid grid-cols-[88px_minmax(0,1fr)] gap-x-3 gap-y-2 text-[11px]">
            <dt className="text-muted-foreground">Session</dt>
            <dd className="truncate font-mono text-foreground" title={conversationId}>
              {conversationId}
            </dd>
            <dt className="text-muted-foreground">Response</dt>
            <dd
              className="truncate font-mono text-foreground"
              title={snapshot.latestResponseId ?? undefined}
            >
              {snapshot.latestResponseId ?? "—"}
            </dd>
            <dt className="text-muted-foreground">Result</dt>
            <dd className="capitalize text-foreground">{snapshot.resultStatus}</dd>
            <dt className="text-muted-foreground">Verifier</dt>
            <dd
              className={cn(
                "font-medium",
                snapshot.verifierTone === "success"
                  ? "text-success"
                  : snapshot.verifierTone === "error"
                    ? "text-destructive"
                    : "text-warning",
              )}
              title={snapshot.verifierReason ?? undefined}
            >
              {snapshot.verifierStatus}
            </dd>
            {snapshot.receiptEventId && (
              <>
                <dt className="text-muted-foreground">Receipt event</dt>
                <dd className="truncate font-mono text-foreground" title={snapshot.receiptEventId}>
                  {snapshot.receiptEventId}
                </dd>
              </>
            )}
            {snapshot.workItemId && (
              <>
                <dt className="text-muted-foreground">Work item</dt>
                <dd className="truncate font-mono text-foreground" title={snapshot.workItemId}>
                  {snapshot.workItemId}
                </dd>
              </>
            )}
            {snapshot.artifactId && (
              <>
                <dt className="text-muted-foreground">Artifact</dt>
                <dd className="truncate font-mono text-foreground" title={snapshot.artifactId}>
                  {snapshot.artifactId}
                </dd>
              </>
            )}
            <dt className="text-muted-foreground">Executor</dt>
            <dd className="truncate text-foreground">{boundAgentName ?? "—"}</dd>
            <dt className="text-muted-foreground">Delegation</dt>
            <dd className="text-foreground">
              {agentCount} agent{agentCount === 1 ? "" : "s"}
              {agentsWorking > 0 ? ` · ${agentsWorking} working` : ""}
            </dd>
            <dt className="text-muted-foreground">Artifacts</dt>
            <dd className="text-foreground">
              {snapshot.artifactCount > 0 ? `${snapshot.artifactCount} observed` : "None reported"}
            </dd>
            <dt className="text-muted-foreground">Cost</dt>
            <dd className="font-mono text-foreground">{formatCost(sessionCostUsd)}</dd>
            {gitBranch && (
              <>
                <dt className="text-muted-foreground">Branch</dt>
                <dd className="truncate font-mono text-foreground" title={gitBranch}>
                  {gitBranch}
                </dd>
              </>
            )}
          </dl>
        </section>

        <div className="grid grid-cols-2 gap-2">
          {showFilesPanel && (
            <Button variant="outline" size="sm" onClick={onOpenFiles} className="gap-1.5 text-xs">
              <FileCheck2Icon className="size-3.5" />
              Files
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={onOpenAgents} className="gap-1.5 text-xs">
            <BotIcon className="size-3.5" />
            Agents
          </Button>
          <Button asChild variant="outline" size="sm" className="col-span-2 gap-1.5 text-xs">
            <Link to="/inbox">
              <ShieldAlertIcon className="size-3.5" />
              {snapshot.pendingApprovals > 0
                ? `Review ${snapshot.pendingApprovals} waiting gate${snapshot.pendingApprovals === 1 ? "" : "s"}`
                : "Open Inbox"}
            </Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
