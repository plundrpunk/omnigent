/**
 * Pattern library — diagram-first orchestration patterns.
 *
 * Each card leads with a glyph of the pattern's shape; one line of
 * text and a Set-this-up button. Details (how it works, evidence,
 * caveats) expand on demand. Honesty lifecycle badges are earned:
 * scouted → vetted → proven-here; nothing is fabricated.
 */

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageShell } from "@/components/PageShell";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { amsGet } from "@/lib/ams";
import {
  failedACheck,
  finished,
  needsYourOK,
  neverStarted,
  pausedForInput,
  proofOfWhatWasDone,
  run,
  stopsIfCheckFails,
} from "@/lib/copy";
import { useNavigate } from "@/lib/routing";

type Honesty = "proven-here" | "vetted" | "scouted";
type GlyphKind = "fanout" | "dispatch" | "chain" | "debate" | "gate" | "promotion";

interface Pattern {
  id: string;
  glyph: GlyphKind;
  name: string;
  honesty: Honesty;
  oneLiner: string;
  how: string;
  evidence: string;
  caveats?: string;
  setupPrompt: string;
}

const PATTERNS: Pattern[] = [
  {
    id: "fan-out-fan-in",
    glyph: "fanout",
    name: "Fan-out / fan-in",
    honesty: "proven-here",
    oneLiner: "Split work across parallel specialists, merge one answer.",
    how: "Prime decomposes the goal, dispatches each subtask to a specialist, then a fan-in step synthesizes the rollup under one correlation id.",
    evidence: "Recorded fleet runs (AUG pitch wave, exec:fleet-1ccac297…) with fan-in rollups in AMS.",
    setupPrompt:
      "Run a fan-out/fan-in: decompose the following goal into independent subtasks, dispatch each to the best specialist agent, then synthesize all results into one rollup with a shared correlation id. Goal: <YOUR GOAL>",
  },
  {
    id: "tl-dispatch",
    glyph: "dispatch",
    name: "Team-lead dispatch",
    honesty: "vetted",
    oneLiner: "Route a goal to the domain team-lead, who delegates.",
    how: "dispatch_to_tl hands the task to a TL agent; the TL plans, spawns workers, and reports upward.",
    evidence: "tl-marketing dispatches verified end-to-end; TL fleet registered.",
    caveats:
      `Known gap (memory 45ea10b3): dispatches can queue without spawning — watch for work that ${neverStarted} on Fleet.`,
    setupPrompt:
      "Dispatch this to the appropriate team lead via dispatch_to_tl, have them decompose it across their team, and report the rollup: <YOUR GOAL>",
  },
  {
    id: "swarm-chain",
    glyph: "chain",
    name: "Swarm chain",
    honesty: "vetted",
    oneLiner: "Stages in sequence — each output feeds the next.",
    how: "swarm_chain runs agents in order (draft → critique → revise → finalize); state rides in the chain payload.",
    evidence: `swarm_chain ${run}s recorded in AMS; stats visible in Training.`,
    setupPrompt:
      "Run a swarm chain over these stages (each stage consumes the previous output): 1) draft, 2) critique, 3) revise, 4) finalize. Subject: <YOUR SUBJECT>",
  },
  {
    id: "model-debate",
    glyph: "debate",
    name: "Model debate",
    honesty: "scouted",
    oneLiner: "Two models argue opposing sides; a judge rules.",
    how: "Two agents on different models argue assigned positions for N rounds; a third summarizes and rules with reasons.",
    evidence: `No local ${proofOfWhatWasDone} yet — claim from external literature only.`,
    setupPrompt:
      "Set up a model debate: assign two different models opposing positions on the question below, run 2 argument rounds, then have a judge model rule with reasons. Question: <YOUR QUESTION>",
  },
  {
    id: "goal-contract",
    glyph: "gate",
    name: "Goal-contract run",
    honesty: "proven-here",
    oneLiner: `Autonomous work that can't lie — ${needsYourOK} + honest outcomes.`,
    how: `A goal contract declares deliverables and final checks that ${needsYourOK}; headless runs report ${finished}, ${pausedForInput}, or ${failedACheck} with blocker.md / checkpoint.json artifacts.`,
    evidence: `Harness+AMS 10x WS2/WS3 recorded ${proofOfWhatWasDone} for checks and outcomes (2026-07-17).`,
    setupPrompt:
      `Create a goal contract for the following outcome with explicit deliverables and final checks that ${needsYourOK}, then run it headless with honest outcomes: <YOUR OUTCOME>`,
  },
  {
    id: "eval-gated-promotion",
    glyph: "promotion",
    name: `Promotion that ${needsYourOK}`,
    honesty: "proven-here",
    oneLiner: "Candidates replace incumbents only by beating them.",
    how: `Candidate runs the eval suite; promotion requires ${proofOfWhatWasDone}; regressions are rejected with a diff. It ${stopsIfCheckFails}.`,
    evidence: "WS4 eval deploy check: recorded promotion rejection with a diff (2026-07-17).",
    setupPrompt:
      `Propose an improved version of <TARGET>, run it through the eval deploy check against the incumbent, and promote only with passing ${proofOfWhatWasDone} — reject with a diff otherwise.`,
  },
];

const HONESTY_STYLE: Record<Honesty, { label: string; variant: "default" | "secondary" | "outline" }> = {
  "proven-here": { label: "proven here", variant: "default" },
  vetted: { label: "vetted", variant: "secondary" },
  scouted: { label: "scouted", variant: "outline" },
};

/** Small node for glyphs. */
function N({ x, y, r = 7, cls = "fill-primary/70" }: { x: number; y: number; r?: number; cls?: string }) {
  return <circle cx={x} cy={y} r={r} className={cls} />;
}
function E({ x1, y1, x2, y2, dash }: { x1: number; y1: number; x2: number; y2: number; dash?: boolean }) {
  return (
    <line
      x1={x1}
      y1={y1}
      x2={x2}
      y2={y2}
      className="stroke-muted-foreground"
      strokeWidth={1.5}
      strokeDasharray={dash ? "3 3" : undefined}
    />
  );
}

/** The pattern's shape, drawn — this is the point of the card. */
function PatternGlyph({ kind }: { kind: GlyphKind }) {
  const common = "h-24 w-full";
  switch (kind) {
    case "fanout":
      return (
        <svg viewBox="0 0 200 90" className={common}>
          <E x1={30} y1={45} x2={95} y2={15} />
          <E x1={30} y1={45} x2={95} y2={45} />
          <E x1={30} y1={45} x2={95} y2={75} />
          <E x1={105} y1={15} x2={170} y2={45} />
          <E x1={105} y1={45} x2={170} y2={45} />
          <E x1={105} y1={75} x2={170} y2={45} />
          <N x={30} y={45} r={9} />
          <N x={100} y={15} cls="fill-emerald-500/70" />
          <N x={100} y={45} cls="fill-emerald-500/70" />
          <N x={100} y={75} cls="fill-emerald-500/70" />
          <N x={170} y={45} r={9} cls="fill-violet-500/70" />
        </svg>
      );
    case "dispatch":
      return (
        <svg viewBox="0 0 200 90" className={common}>
          <E x1={25} y1={45} x2={90} y2={45} />
          <E x1={100} y1={45} x2={165} y2={15} />
          <E x1={100} y1={45} x2={165} y2={45} />
          <E x1={100} y1={45} x2={165} y2={75} />
          <N x={25} y={45} r={9} />
          <N x={95} y={45} r={8} cls="fill-amber-500/70" />
          <N x={168} y={15} cls="fill-emerald-500/70" />
          <N x={168} y={45} cls="fill-emerald-500/70" />
          <N x={168} y={75} cls="fill-emerald-500/70" />
        </svg>
      );
    case "chain":
      return (
        <svg viewBox="0 0 200 90" className={common}>
          <E x1={25} y1={45} x2={70} y2={45} />
          <E x1={75} y1={45} x2={120} y2={45} />
          <E x1={125} y1={45} x2={170} y2={45} />
          <N x={25} y={45} />
          <N x={75} y={45} cls="fill-emerald-500/70" />
          <N x={125} y={45} cls="fill-violet-500/70" />
          <N x={175} y={45} cls="fill-amber-500/70" />
        </svg>
      );
    case "debate":
      return (
        <svg viewBox="0 0 200 90" className={common}>
          <E x1={40} y1={25} x2={95} y2={25} dash />
          <E x1={95} y1={25} x2={40} y2={25} dash />
          <E x1={45} y1={30} x2={95} y2={60} />
          <E x1={155} y1={30} x2={105} y2={60} />
          <E x1={160} y1={25} x2={105} y2={25} dash />
          <N x={40} y={25} r={9} cls="fill-sky-500/70" />
          <N x={160} y={25} r={9} cls="fill-rose-500/70" />
          <N x={100} y={65} r={10} cls="fill-amber-500/80" />
        </svg>
      );
    case "gate":
      return (
        <svg viewBox="0 0 200 90" className={common}>
          <E x1={25} y1={45} x2={85} y2={45} />
          <E x1={115} y1={45} x2={172} y2={45} />
          <polygon
            points="100,28 118,45 100,62 82,45"
            className="fill-amber-500/20 stroke-amber-500/80"
            strokeWidth={1.5}
          />
          <N x={25} y={45} r={9} />
          <N x={175} y={45} r={9} cls="fill-emerald-500/70" />
        </svg>
      );
    case "promotion":
      return (
        <svg viewBox="0 0 200 90" className={common}>
          <E x1={40} y1={20} x2={92} y2={40} />
          <E x1={40} y1={70} x2={92} y2={50} />
          <E x1={118} y1={45} x2={168} y2={25} />
          <E x1={118} y1={45} x2={168} y2={65} dash />
          <polygon
            points="105,30 122,45 105,60 88,45"
            className="fill-amber-500/20 stroke-amber-500/80"
            strokeWidth={1.5}
          />
          <N x={40} y={20} cls="fill-sky-500/70" />
          <N x={40} y={70} cls="fill-muted-foreground/60" />
          <N x={170} y={25} r={8} cls="fill-emerald-500/70" />
          <N x={170} y={65} r={6} cls="fill-destructive/70" />
        </svg>
      );
  }
}

function PatternCard({ pattern }: { pattern: Pattern }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const style = HONESTY_STYLE[pattern.honesty];

  const setUp = () => {
    void navigator.clipboard
      .writeText(pattern.setupPrompt)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => undefined);
    navigate("/");
  };

  return (
    <div className="flex flex-col rounded-xl border border-border bg-card p-4">
      <PatternGlyph kind={pattern.glyph} />
      <div className="mt-2 flex items-center justify-between gap-2">
        <h2 className="font-semibold">{pattern.name}</h2>
        <Badge variant={style.variant}>{style.label}</Badge>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">{pattern.oneLiner}</p>
      <div className="mt-3 flex items-center gap-2">
        <Button size="sm" onClick={setUp}>
          {copied ? "Copied ✓" : "Set this up"}
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setOpen((v) => !v)}>
          {open ? "Less" : "Details"}
        </Button>
      </div>
      {open && (
        <div className="mt-3 space-y-2 border-t border-border/60 pt-3 text-sm">
          <p className="text-muted-foreground">{pattern.how}</p>
          <p className="text-xs text-muted-foreground">
            <span className="font-medium">Evidence: </span>
            {pattern.evidence}
          </p>
          {pattern.caveats && (
            <p className="text-xs text-warning">
              <span className="font-medium">Caveat: </span>
              {pattern.caveats}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

interface CandidateMemory {
  memory_id?: string;
  title?: string;
  content_snippet?: string;
}

function DiscoverFeed() {
  const [candidates, setCandidates] = useState<CandidateMemory[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setCandidates(null);
    setFailed(false);
    void amsGet<{ memories?: CandidateMemory[]; results?: CandidateMemory[] }>(
      "api/v1/memories/?tag=discover-candidate&limit=50",
    )
      .then((body) => {
        if (!cancelled) setCandidates(body.memories ?? body.results ?? []);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [retryKey]);

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Scouted candidates land here weekly and wait for defenseclaw vetting — nothing
        joins the library without {proofOfWhatWasDone}.
      </p>
      {failed ? (
        <div className="space-y-2">
          <p className="text-sm text-destructive">Could not load Discover candidates.</p>
          <Button variant="outline" size="sm" onClick={() => setRetryKey((key) => key + 1)}>
            Retry
          </Button>
        </div>
      ) : candidates === null ? (
        <div className="rounded-xl border border-border p-8 text-center text-sm text-muted-foreground">
          Loading Discover candidates…
        </div>
      ) : candidates.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
          No candidates are awaiting vetting.
        </div>
      ) : (
        <ul className="space-y-2">
          {candidates.map((c, i) => (
            <li key={c.memory_id ?? i} className="rounded-lg border border-border bg-card p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium">{c.title ?? "Untitled candidate"}</span>
                <Badge variant="outline">awaiting vetting</Badge>
              </div>
              {c.content_snippet && (
                <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{c.content_snippet}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function PatternsPage() {
  return (
    <PageShell
      title="Patterns"
      subtitle="Ways your agents can work together — badges are earned, never claimed."
    >
      <Tabs defaultValue="library">
        <TabsList>
          <TabsTrigger value="library">Library</TabsTrigger>
          <TabsTrigger value="discover">Discover</TabsTrigger>
        </TabsList>
        <TabsContent value="library">
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {PATTERNS.map((p) => (
              <PatternCard key={p.id} pattern={p} />
            ))}
          </div>
        </TabsContent>
        <TabsContent value="discover">
          <DiscoverFeed />
        </TabsContent>
      </Tabs>
    </PageShell>
  );
}
