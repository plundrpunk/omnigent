/**
 * Loop builder — the diagram IS the editor.
 *
 * The loop renders as a vertical flow of stage nodes with a loop-back
 * arrow; click a node to edit it in the side panel. Emits declarative
 * `aos.loop.v1` JSON (behind the Advanced toggle) that the
 * deterministic harness executes — the builder never generates code.
 */

import { useMemo, useState } from "react";
import { PlusIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PageShell } from "@/components/PageShell";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { needsYourOK, savedRoutine, stopsIfCheckFails } from "@/lib/copy";
import { cn } from "@/lib/utils";

const STAGE_TYPES = ["plan", "execute", "verify", "gate", "consolidate"] as const;
type StageType = (typeof STAGE_TYPES)[number];

interface LoopStage {
  id: number;
  name: string;
  type: StageType;
  actor: string;
  exitCondition: string;
}

const STAGE_HINTS: Record<StageType, string> = {
  plan: "decompose the goal into next actions",
  execute: `do the work (agent / ${savedRoutine} / skill)`,
  verify: "check the artifact against reality",
  gate: `${stopsIfCheckFails} — no success past a check that ${needsYourOK}`,
  consolidate: "write results + lessons back to AMS memory",
};

const STAGE_LABELS: Record<StageType, string> = {
  plan: "plan",
  execute: "execute",
  verify: "verify",
  gate: needsYourOK,
  consolidate: "consolidate",
};

const STAGE_COLOR: Record<StageType, string> = {
  plan: "fill-sky-500/15 stroke-sky-500/70",
  execute: "fill-emerald-500/15 stroke-emerald-500/70",
  verify: "fill-violet-500/15 stroke-violet-500/70",
  gate: "fill-amber-500/15 stroke-amber-500/80",
  consolidate: "fill-rose-500/15 stroke-rose-500/70",
};

let nextId = 100;
const newStage = (type: StageType, name?: string): LoopStage => ({
  id: nextId++,
  name: name ?? STAGE_LABELS[type],
  type,
  actor: "",
  exitCondition: "",
});

const DEFAULT_STAGES: LoopStage[] = [
  newStage("plan"),
  newStage("execute"),
  newStage("verify"),
  newStage("gate", `finalization ${needsYourOK}`),
  newStage("consolidate", "consolidate"),
];

// Diagram geometry.
const W = 360;
const NODE_W = 240;
const NODE_H = 56;
const GAP = 44;
const NODE_X = 40;

export function LoopsPage() {
  const [loopName, setLoopName] = useState("my-loop");
  const [goal, setGoal] = useState("");
  const [maxIterations, setMaxIterations] = useState(5);
  const [stages, setStages] = useState<LoopStage[]>(DEFAULT_STAGES);
  const [selectedId, setSelectedId] = useState<number | null>(DEFAULT_STAGES[0].id);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [copied, setCopied] = useState(false);

  const selected = stages.find((s) => s.id === selectedId) ?? null;
  const hasGate = stages.some((s) => s.type === "gate");

  const config = useMemo(
    () => ({
      kind: "aos.loop.v1",
      name: loopName,
      goal,
      max_iterations: maxIterations,
      deterministic: true,
      stages: stages.map((s, i) => ({
        order: i + 1,
        name: s.name,
        type: s.type,
        actor: s.actor || null,
        exit_condition: s.exitCondition || null,
      })),
      invariants: { runtime_ai_mutation: false, gates_fail_closed: true },
    }),
    [loopName, goal, maxIterations, stages],
  );
  const configJson = JSON.stringify(config, null, 2);
  const displayConfig = useMemo(
    () => ({
      name: config.name,
      goal: config.goal,
      max_iterations: config.max_iterations,
      deterministic: config.deterministic,
      stages: config.stages.map((stage) => ({
        ...stage,
        type: stage.type === "gate" ? needsYourOK : stage.type,
      })),
      invariants: {
        runtime_ai_mutation: config.invariants.runtime_ai_mutation,
        [stopsIfCheckFails]: config.invariants.gates_fail_closed,
      },
    }),
    [config],
  );
  const displayConfigJson = JSON.stringify(displayConfig, null, 2);

  const update = (id: number, patch: Partial<LoopStage>) =>
    setStages((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  const move = (id: number, dir: -1 | 1) =>
    setStages((prev) => {
      const i = prev.findIndex((s) => s.id === id);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  const remove = (id: number) => {
    setStages((prev) => prev.filter((s) => s.id !== id));
    if (selectedId === id) setSelectedId(null);
  };
  const addStage = () => {
    const s = newStage("execute");
    setStages((prev) => [...prev, s]);
    setSelectedId(s.id);
  };

  const copy = () =>
    void navigator.clipboard.writeText(configJson).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  const download = () => {
    const blob = new Blob([configJson], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${loopName || "loop"}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const height = 24 + stages.length * (NODE_H + GAP);

  return (
    <PageShell
      title="Loop builder"
      subtitle="Click a stage to edit it. The loop runs top to bottom, then cycles."
      wide
      actions={
        <>
          <Button size="sm" onClick={copy}>
            {copied ? "Copied ✓" : "Copy config"}
          </Button>
          <Button size="sm" variant="outline" onClick={download}>
            Download
          </Button>
        </>
      }
    >
      <div className="grid gap-6 lg:grid-cols-[minmax(0,420px)_1fr]">
        {/* ── The loop diagram (the editor) ── */}
        <div className="rounded-xl border border-border bg-card/50 p-4">
          <svg viewBox={`0 0 ${W} ${height}`} className="mx-auto h-auto w-full max-w-[400px]">
            <defs>
              <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                <path d="M0,0 L8,4 L0,8 z" className="fill-muted-foreground" />
              </marker>
            </defs>

            {/* loop-back arrow: last stage → first stage */}
            {stages.length > 1 && (
              <>
                <path
                  d={`M ${NODE_X + NODE_W} ${12 + (stages.length - 1) * (NODE_H + GAP) + NODE_H / 2}
                      C ${W - 8} ${12 + (stages.length - 1) * (NODE_H + GAP) + NODE_H / 2},
                        ${W - 8} ${12 + NODE_H / 2},
                        ${NODE_X + NODE_W + 6} ${12 + NODE_H / 2}`}
                  fill="none"
                  strokeDasharray="5 4"
                  className="stroke-muted-foreground/60"
                  strokeWidth={1.5}
                  markerEnd="url(#arrow)"
                />
                <text
                  x={W - 14}
                  y={height / 2}
                  textAnchor="middle"
                  className="fill-muted-foreground text-[10px]"
                  transform={`rotate(90 ${W - 14} ${height / 2})`}
                >
                  ↻ up to {maxIterations}×
                </text>
              </>
            )}

            {stages.map((s, i) => {
              const y = 12 + i * (NODE_H + GAP);
              const isGate = s.type === "gate";
              const sel = s.id === selectedId;
              return (
                <g key={s.id} className="cursor-pointer" onClick={() => setSelectedId(s.id)}>
                  {/* connector to next stage */}
                  {i < stages.length - 1 && (
                    <line
                      x1={NODE_X + NODE_W / 2}
                      y1={y + NODE_H}
                      x2={NODE_X + NODE_W / 2}
                      y2={y + NODE_H + GAP - 6}
                      className="stroke-muted-foreground/70"
                      strokeWidth={1.5}
                      markerEnd="url(#arrow)"
                    />
                  )}
                  {isGate ? (
                    <polygon
                      points={`${NODE_X + NODE_W / 2},${y - 4} ${NODE_X + NODE_W + 10},${y + NODE_H / 2} ${NODE_X + NODE_W / 2},${y + NODE_H + 4} ${NODE_X - 10},${y + NODE_H / 2}`}
                      className={cn(STAGE_COLOR[s.type], sel && "stroke-primary")}
                      strokeWidth={sel ? 2.5 : 1.5}
                    />
                  ) : (
                    <rect
                      x={NODE_X}
                      y={y}
                      width={NODE_W}
                      height={NODE_H}
                      rx={14}
                      className={cn(STAGE_COLOR[s.type], sel && "stroke-primary")}
                      strokeWidth={sel ? 2.5 : 1.5}
                    />
                  )}
                  <text
                    x={NODE_X + NODE_W / 2}
                    y={y + NODE_H / 2 - 4}
                    textAnchor="middle"
                    className="fill-foreground text-[13px] font-medium"
                  >
                    {s.name.length > 24 ? `${s.name.slice(0, 23)}…` : s.name}
                  </text>
                  <text
                    x={NODE_X + NODE_W / 2}
                    y={y + NODE_H / 2 + 13}
                    textAnchor="middle"
                    className="fill-muted-foreground text-[10px]"
                  >
                    {STAGE_LABELS[s.type]}
                    {s.actor ? ` · ${s.actor.slice(0, 20)}` : ""}
                  </text>
                </g>
              );
            })}
          </svg>

          <div className="mt-2 flex justify-center">
            <Button variant="outline" size="sm" onClick={addStage}>
              <PlusIcon className="size-4" /> Add stage
            </Button>
          </div>

          {!hasGate && (
            <p className="mt-3 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-center text-xs text-warning">
              No {needsYourOK} step in this loop — it could claim success it hasn&apos;t earned.
            </p>
          )}
        </div>

        {/* ── Side panel: loop settings + selected stage ── */}
        <div className="space-y-4">
          <div className="rounded-xl border border-border bg-card p-4">
            <h2 className="mb-3 text-sm font-medium text-muted-foreground">Loop</h2>
            <div className="grid grid-cols-[1fr_110px] gap-2">
              <Input value={loopName} onChange={(e) => setLoopName(e.target.value)} placeholder="loop name" />
              <Input
                type="number"
                min={1}
                max={100}
                value={maxIterations}
                onChange={(e) => setMaxIterations(Number(e.target.value) || 1)}
              />
            </div>
            <Input
              className="mt-2"
              placeholder="What should this loop accomplish?"
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
            />
          </div>

          {selected ? (
            <div className="rounded-xl border border-primary/40 bg-card p-4">
              <h2 className="mb-3 text-sm font-medium text-muted-foreground">
                Selected stage
              </h2>
              <div className="space-y-2">
                <div className="grid grid-cols-[1fr_150px] gap-2">
                  <Input
                    value={selected.name}
                    onChange={(e) => update(selected.id, { name: e.target.value })}
                  />
                  <Select
                    value={selected.type}
                    onValueChange={(v) => update(selected.id, { type: v as StageType })}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {STAGE_TYPES.map((t) => (
                        <SelectItem key={t} value={t}>
                          {STAGE_LABELS[t]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <p className="text-xs text-muted-foreground">{STAGE_HINTS[selected.type]}</p>
                <Input
                  placeholder={`actor — agent, ${savedRoutine}, or skill (optional)`}
                  value={selected.actor}
                  onChange={(e) => update(selected.id, { actor: e.target.value })}
                />
                <Input
                  placeholder="exit condition (optional)"
                  value={selected.exitCondition}
                  onChange={(e) => update(selected.id, { exitCondition: e.target.value })}
                />
                <div className="flex items-center gap-2 pt-1">
                  <Button variant="outline" size="sm" onClick={() => move(selected.id, -1)}>
                    Move up
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => move(selected.id, 1)}>
                    Move down
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="ml-auto text-destructive"
                    onClick={() => remove(selected.id)}
                  >
                    Remove
                  </Button>
                </div>
              </div>
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
              Click a stage in the diagram to edit it.
            </div>
          )}

          <div>
            <Button variant="ghost" size="sm" onClick={() => setShowAdvanced((v) => !v)}>
              {showAdvanced ? "Hide" : "Advanced"} — display-only view
            </Button>
            {showAdvanced && (
              <pre className="mt-2 max-h-80 overflow-auto rounded-lg border border-border bg-card p-3 text-xs">
                {displayConfigJson}
              </pre>
            )}
          </div>
        </div>
      </div>
    </PageShell>
  );
}
