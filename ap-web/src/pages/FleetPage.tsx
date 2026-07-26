/**
 * Fleet view (mission control) — makes AMS agent-passing visible.
 *
 * Polls the warden roster and observatory executions through the AMS
 * bridge every few seconds and renders the fleet as an SVG tree:
 * spawn/dispatch edges are derived by resolving each execution's
 * `parent_execution_id` to the parent's agent, so children appear
 * under whoever dispatched them. Dispatches that never produced a
 * running child render as dashed "ghost" edges — the historically
 * silent queued-but-never-spawned gap, made visible.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageShell } from "@/components/PageShell";
import { Spinner } from "@/components/ui/spinner";
import {
  execErrorText,
  execTokenCount,
  fetchExecutions,
  fetchWardenAgents,
  type ObservatoryExecution,
  type WardenAgent,
} from "@/lib/ams";
import { cn } from "@/lib/utils";

const POLL_MS = 3000;

interface FleetNode {
  id: string;
  label: string;
  alive: boolean;
  status: string;
  contextPct: number;
  isRoot: boolean;
}

interface FleetEdge {
  from: string;
  to: string;
  /** running | completed | failed | ghost (dispatched but never spawned) */
  kind: "running" | "completed" | "failed" | "ghost";
}

/** Resolve executions into agent→agent edges. */
function deriveEdges(
  agents: WardenAgent[],
  executions: ObservatoryExecution[],
): { edges: FleetEdge[]; execsByAgent: Map<string, ObservatoryExecution[]> } {
  const byExecId = new Map(executions.map((e) => [e.execution_id, e]));
  const agentNames = new Set(agents.map((a) => a.agent_name));
  const execsByAgent = new Map<string, ObservatoryExecution[]>();
  const edgeMap = new Map<string, FleetEdge>();

  for (const exec of executions) {
    if (exec.agent_name) {
      const list = execsByAgent.get(exec.agent_name) ?? [];
      list.push(exec);
      execsByAgent.set(exec.agent_name, list);
    }
    if (!exec.parent_execution_id) continue;
    const parent = byExecId.get(exec.parent_execution_id);
    const parentAgent = parent?.agent_name;
    const childAgent = exec.agent_name;
    if (!parentAgent || !childAgent || parentAgent === childAgent) continue;

    const key = `${parentAgent}→${childAgent}`;
    const spawned = agentNames.has(childAgent) || exec.status === "completed" || exec.status === "running";
    const kind: FleetEdge["kind"] =
      exec.status === "running"
        ? "running"
        : exec.status === "failed"
          ? "failed"
          : spawned
            ? "completed"
            : "ghost";
    // Running beats completed beats failed beats ghost when edges repeat.
    const rank = { running: 3, completed: 2, failed: 1, ghost: 0 };
    const existing = edgeMap.get(key);
    if (!existing || rank[kind] > rank[existing.kind]) {
      edgeMap.set(key, { from: parentAgent, to: childAgent, kind });
    }
  }
  return { edges: [...edgeMap.values()], execsByAgent };
}

/** Assign each node a level (root=0) from spawn edges; unparented non-roots go to level 1. */
function layoutLevels(nodes: FleetNode[], edges: FleetEdge[]): Map<string, number> {
  const parentOf = new Map<string, string>();
  for (const e of edges) {
    if (!parentOf.has(e.to)) parentOf.set(e.to, e.from);
  }
  const levels = new Map<string, number>();
  const depthOf = (id: string, hops = 0): number => {
    if (hops > 10) return hops;
    const p = parentOf.get(id);
    return p ? depthOf(p, hops + 1) + 1 : 0;
  };
  for (const n of nodes) {
    let level = depthOf(n.id);
    if (level === 0 && !n.isRoot) level = 1;
    levels.set(n.id, level);
  }
  return levels;
}

const NODE_W = 148;
const NODE_H = 44;
const LEVEL_GAP = 96;
const COL_GAP = 16;

interface Placed extends FleetNode {
  x: number;
  y: number;
}

function place(nodes: FleetNode[], levels: Map<string, number>): { placed: Placed[]; width: number; height: number } {
  const byLevel = new Map<number, FleetNode[]>();
  for (const n of nodes) {
    const lvl = levels.get(n.id) ?? 1;
    const list = byLevel.get(lvl) ?? [];
    list.push(n);
    byLevel.set(lvl, list);
  }
  // Wrap wide levels into sub-rows so nodes stay readable.
  const MAX_COLS = 7;
  const width = Math.max(720, MAX_COLS * (NODE_W + COL_GAP) + COL_GAP);
  const SUBROW_GAP = 14;
  const placed: Placed[] = [];
  const levelKeys = [...byLevel.keys()].sort((a, b) => a - b);
  let y = 24;
  for (const lvl of levelKeys) {
    const row = byLevel.get(lvl) ?? [];
    // Alive agents first, then alphabetical — keeps the living fleet visible.
    row.sort((a, b) => Number(b.alive) - Number(a.alive) || a.label.localeCompare(b.label));
    const subrows = Math.ceil(row.length / MAX_COLS);
    for (let s = 0; s < subrows; s++) {
      const chunk = row.slice(s * MAX_COLS, (s + 1) * MAX_COLS);
      const rowWidth = chunk.length * (NODE_W + COL_GAP) - COL_GAP;
      const x0 = (width - rowWidth) / 2;
      chunk.forEach((n, i) => {
        placed.push({ ...n, x: x0 + i * (NODE_W + COL_GAP), y });
      });
      y += NODE_H + (s < subrows - 1 ? SUBROW_GAP : 0);
    }
    y += LEVEL_GAP;
  }
  const height = y - LEVEL_GAP + NODE_H + 24;
  return { placed, width, height };
}

const EDGE_STYLE: Record<FleetEdge["kind"], { stroke: string; dash?: string; opacity: number }> = {
  running: { stroke: "var(--color-warning, #eab308)", opacity: 0.95 },
  completed: { stroke: "currentColor", opacity: 0.35 },
  failed: { stroke: "var(--color-destructive, #ef4444)", opacity: 0.8 },
  ghost: { stroke: "currentColor", dash: "4 4", opacity: 0.3 },
};

export function FleetPage() {
  const [agents, setAgents] = useState<WardenAgent[] | null>(null);
  const [executions, setExecutions] = useState<ObservatoryExecution[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [showHistory, setShowHistory] = useState(true);
  const timerRef = useRef<number | null>(null);

  const poll = useCallback(async () => {
    try {
      const [a, e] = await Promise.all([fetchWardenAgents(), fetchExecutions()]);
      setAgents(a);
      setExecutions(e);
      setError(null);
      setStale(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStale(true);
    }
  }, []);

  useEffect(() => {
    void poll();
    if (paused) return;
    const id = window.setInterval(() => void poll(), POLL_MS);
    timerRef.current = id;
    return () => window.clearInterval(id);
  }, [poll, paused]);

  const graph = useMemo(() => {
    if (!agents) return null;
    const visibleAgents = showHistory ? agents : agents.filter((a) => a.alive);
    const nodes: FleetNode[] = visibleAgents.map((a) => ({
      id: a.agent_name,
      label: a.agent_name,
      alive: a.alive,
      status: a.status,
      contextPct: Number(a.context_pct) || 0,
      isRoot: /prime/i.test(a.agent_name),
    }));
    const { edges: allEdges, execsByAgent } = deriveEdges(agents, executions);
    // Default view: all dispatches. The filtered view keeps active/ghost dispatches only.
    const edges = showHistory
      ? allEdges
      : allEdges.filter((e) => e.kind === "running" || e.kind === "ghost");
    // Executions can reference agents the warden no longer lists — add
    // them as transient nodes so dispatch targets are never invisible.
    const known = new Set(nodes.map((n) => n.id));
    for (const e of edges) {
      for (const id of [e.from, e.to]) {
        if (!known.has(id)) {
          known.add(id);
          nodes.push({ id, label: id, alive: false, status: "transient", contextPct: 0, isRoot: false });
        }
      }
    }
    const levels = layoutLevels(nodes, edges);
    const { placed, width, height } = place(nodes, levels);
    return { placed, edges, execsByAgent, width, height };
  }, [agents, executions, showHistory]);

  const selectedAgent = agents?.find((a) => a.agent_name === selected);
  // Transient nodes (seen only in executions) still deserve a panel — execs exist even without warden data.
  const selectedNode = graph?.placed.find((n) => n.id === selected) ?? null;
  const selectedExecs = (graph?.execsByAgent.get(selected ?? "") ?? []).slice(0, 8);
  const aliveCount = agents?.filter((a) => a.alive).length ?? 0;
  const runningCount = executions.filter((e) => e.status === "running").length;
  const ghostCount = graph?.edges.filter((e) => e.kind === "ghost").length ?? 0;

  return (
    <PageShell
      title="Fleet"
      subtitle={`Your agents, live — updated every ${POLL_MS / 1000}s.`}
      wide
      actions={
        <>
          <Badge variant="secondary">{aliveCount} alive</Badge>
          <Badge variant="secondary">{runningCount} running</Badge>
          {ghostCount > 0 && <Badge variant="destructive">{ghostCount} ghost dispatches</Badge>}
          <Button variant="outline" size="sm" onClick={() => setShowHistory((v) => !v)}>
            {showHistory ? "Show active only" : "Show all dispatches"}
          </Button>
          <Button variant="outline" size="sm" onClick={() => setPaused((p) => !p)}>
            {paused ? "Resume" : "Pause"}
          </Button>
        </>
      }
    >

      {error && (
        <div className="mb-3 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          AMS unreachable: {error}
        </div>
      )}

      {!graph ? (
        <div className="flex items-center gap-2 py-10 text-muted-foreground">
          <Spinner /> Loading fleet…
        </div>
      ) : (
        <div className="flex gap-4">
          <div className="min-w-0 flex-1 overflow-x-auto rounded-xl border border-border bg-card/50">
            <svg
              viewBox={`0 0 ${graph.width} ${graph.height}`}
              preserveAspectRatio="xMidYMin meet"
              className="h-auto w-full min-w-[720px] text-foreground"
            >
              {graph.edges.map((e) => {
                const from = graph.placed.find((n) => n.id === e.from);
                const to = graph.placed.find((n) => n.id === e.to);
                if (!from || !to) return null;
                const x1 = from.x + NODE_W / 2;
                const y1 = from.y + NODE_H;
                const x2 = to.x + NODE_W / 2;
                const y2 = to.y;
                const style = EDGE_STYLE[e.kind];
                return (
                  <path
                    key={`${e.from}-${e.to}`}
                    d={`M ${x1} ${y1} C ${x1} ${(y1 + y2) / 2}, ${x2} ${(y1 + y2) / 2}, ${x2} ${y2}`}
                    fill="none"
                    stroke={style.stroke}
                    strokeWidth={e.kind === "running" ? 2 : 1.4}
                    strokeDasharray={style.dash}
                    opacity={style.opacity}
                  />
                );
              })}
              {graph.placed.map((n) => (
                <g
                  key={n.id}
                  transform={`translate(${n.x}, ${n.y})`}
                  className="cursor-pointer"
                  onClick={() => setSelected(n.id === selected ? null : n.id)}
                >
                  <title>
                    {`${n.label} — ${n.status}${n.contextPct > 0 ? ` · ctx ${n.contextPct.toFixed(1)}%` : ""}`}
                  </title>
                  <rect
                    width={NODE_W}
                    height={NODE_H}
                    rx={10}
                    className={cn(
                      "stroke-1",
                      n.id === selected
                        ? "fill-primary/20 stroke-primary"
                        : n.alive
                          ? "fill-emerald-500/10 stroke-emerald-500/60"
                          : "fill-muted stroke-border",
                    )}
                  />
                  {n.alive && (
                    <circle cx={12} cy={NODE_H / 2} r={4} className="fill-emerald-500">
                      {!stale && (
                        <animate attributeName="opacity" values="1;0.3;1" dur="2s" repeatCount="indefinite" />
                      )}
                    </circle>
                  )}
                  <text
                    x={n.alive ? 24 : 12}
                    y={NODE_H / 2 - 2}
                    className="fill-current text-[11px] font-medium"
                  >
                    {n.label.length > 18 ? `${n.label.slice(0, 17)}…` : n.label}
                  </text>
                  <text
                    x={n.alive ? 24 : 12}
                    y={NODE_H / 2 + 13}
                    className="fill-current text-[10px] opacity-60"
                  >
                    {n.status}
                    {n.contextPct > 0 ? ` · ctx ${n.contextPct.toFixed(1)}%` : ""}
                  </text>
                </g>
              ))}
            </svg>
          </div>

          {selectedNode && (
            <div className="w-80 shrink-0 overflow-auto rounded-lg border border-border bg-card p-4">
              <div className="mb-2 flex items-center justify-between">
                <h2 className="font-semibold">{selectedNode.label}</h2>
                <Badge variant={selectedNode.alive ? "default" : "secondary"}>
                  {selectedAgent ? (selectedAgent.alive ? "alive" : "gone") : "transient"}
                </Badge>
              </div>
              {selectedAgent ? (
                <dl className="space-y-1 text-sm">
                  <div className="flex justify-between">
                    <dt className="text-muted-foreground">status</dt>
                    <dd>{selectedAgent.status}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-muted-foreground">context</dt>
                    <dd>{(Number(selectedAgent.context_pct) || 0).toFixed(1)}%</dd>
                  </div>
                  {selectedAgent.registered_at && (
                    <div className="flex justify-between gap-2">
                      <dt className="text-muted-foreground">registered</dt>
                      <dd className="truncate">{selectedAgent.registered_at.slice(0, 19)}</dd>
                    </div>
                  )}
                </dl>
              ) : (
                <p className="text-sm text-muted-foreground">
                  Not in the warden roster — known only from executions.
                </p>
              )}
              <h3 className="mt-4 mb-1 text-sm font-medium">Recent executions</h3>
              {selectedExecs.length === 0 ? (
                <p className="text-sm text-muted-foreground">None observed.</p>
              ) : (
                <ul className="space-y-2">
                  {selectedExecs.map((e) => (
                    <li key={e.execution_id} className="rounded-md border border-border/60 p-2 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-mono">{e.execution_id.slice(0, 18)}</span>
                        <Badge
                          variant={
                            e.status === "failed"
                              ? "destructive"
                              : e.status === "running"
                                ? "default"
                                : "secondary"
                          }
                        >
                          {e.status}
                        </Badge>
                      </div>
                      {e.task_summary && (
                        <p className="mt-1 line-clamp-3 text-muted-foreground">{e.task_summary}</p>
                      )}
                      {(e.model || execTokenCount(e) != null) && (
                        <div className="mt-1 flex flex-wrap gap-x-3 text-muted-foreground">
                          {e.model && <span className="font-mono">{e.model}</span>}
                          {execTokenCount(e) != null && <span>{execTokenCount(e)!.toLocaleString()} tok</span>}
                        </div>
                      )}
                      {execErrorText(e) && (
                        <p className="mt-1 whitespace-pre-wrap break-words text-destructive">
                          {execErrorText(e)}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </PageShell>
  );
}
