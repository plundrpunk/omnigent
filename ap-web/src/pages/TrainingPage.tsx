/**
 * Training — two honestly-labeled success numbers and one picture.
 *
 * The headline is the run-weighted success rate (successes / runs);
 * the secondary line is the unweighted per-automaton average — the
 * two answer different questions, so both are shown and labeled.
 * Below: a "needs attention" strip for sub-50% categories, then a
 * horizontal bar chart of per-category success rates from the AMS
 * automata population (the evidence base the eval deploy gate rules
 * on). Full table lives behind "Details". Promotion stays
 * fail-closed — this page has no promote button.
 */

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageShell } from "@/components/PageShell";
import { Spinner } from "@/components/ui/spinner";
import { fetchBayesianStats, type BayesianStats } from "@/lib/ams";
import {
  acrossAllRuns,
  averagePerRoutine,
  needsYourOK,
  proofOfWhatWasDone,
  routines,
  run,
  stopsIfCheckFails,
} from "@/lib/copy";

const pct = (x: number | undefined) => (x == null ? "—" : `${(x * 100).toFixed(1)}%`);

export function TrainingPage() {
  const [stats, setStats] = useState<BayesianStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showDetails, setShowDetails] = useState(false);
  const [worstFirst, setWorstFirst] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    void fetchBayesianStats()
      .then((s) => {
        if (!cancelled) setStats(s);
      })
      .catch(() => {
        if (!cancelled) setError("Training data couldn’t be loaded; please try again.");
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  const summary = stats?.summary;
  const categories = [...(stats?.categories ?? [])].sort((a, b) =>
    worstFirst
      ? a.avg_success_rate - b.avg_success_rate
      : b.total_executions - a.total_executions,
  );
  // Sub-50% categories, worst first — surfaced without any expanding.
  const needsAttention = [...(stats?.categories ?? [])]
    .filter((c) => c.avg_success_rate < 0.5)
    .sort((a, b) => a.avg_success_rate - b.avg_success_rate);
  // Run-weighted rate: every recorded run counts once. Absent/empty
  // summary fields render as "—", never an invented number.
  const runWeighted =
    summary && summary.total_executions > 0
      ? summary.total_successes / summary.total_executions
      : undefined;
  const runWeightedOk = runWeighted != null && Number.isFinite(runWeighted);

  return (
    <PageShell
      title="Training"
      subtitle="How well the fleet's building blocks actually perform."
      actions={
        <Button variant="outline" size="sm" onClick={() => setRefreshKey((k) => k + 1)}>
          Refresh
        </Button>
      }
    >
      {error && (
        <div className="mb-3 flex items-center justify-between gap-3 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <span>{error}</span>
          <Button variant="outline" size="sm" onClick={() => setRefreshKey((k) => k + 1)}>
            Retry
          </Button>
        </div>
      )}

      {!stats ? (
        <div className="flex items-center gap-2 py-10 text-muted-foreground">
          <Spinner /> Loading…
        </div>
      ) : (
        <div className="space-y-6">
          {summary && (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {[
                {
                  label: `success rate (${acrossAllRuns})`,
                  value: runWeightedOk ? pct(runWeighted) : "—",
                },
                {
                  label: averagePerRoutine,
                  value: pct(summary.overall_success_rate),
                },
                { label: `${run}s recorded`, value: summary.total_executions.toLocaleString() },
                { label: routines, value: String(summary.total_automata) },
              ].map((c) => (
                <div key={c.label} className="rounded-xl border border-border bg-card p-5 text-center">
                  <div className="text-3xl font-semibold tabular-nums">{c.value}</div>
                  <div className="mt-1 text-xs text-muted-foreground">{c.label}</div>
                </div>
              ))}
            </div>
          )}

          {needsAttention.length > 0 && (
            <div className="rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm">
              <span className="font-semibold text-destructive">
                Needs attention (below 50%):
              </span>{" "}
              {needsAttention.map((c) => (
                <Badge key={c.category} variant="destructive" className="mr-1">
                  {c.category} {pct(c.avg_success_rate)}
                </Badge>
              ))}
            </div>
          )}

          {/* one picture: success by category */}
          <div className="rounded-xl border border-border bg-card/50 p-5">
            <div className="mb-2 flex justify-end">
              <Button variant="ghost" size="sm" onClick={() => setWorstFirst((v) => !v)}>
                {worstFirst ? "Sort by volume" : "Worst first"}
              </Button>
            </div>
            <svg
              viewBox={`0 0 640 ${categories.length * 30 + 8}`}
              className="h-auto w-full"
            >
              {categories.map((c, i) => {
                const y = i * 30 + 4;
                const w = Math.max(4, c.avg_success_rate * 380);
                const color =
                  c.avg_success_rate >= 0.9
                    ? "fill-emerald-500/80"
                    : c.avg_success_rate >= 0.6
                      ? "fill-warning/80"
                      : "fill-destructive/80";
                return (
                  <g key={c.category}>
                    <text x={148} y={y + 15} textAnchor="end" className="fill-foreground text-[12px]">
                      {c.category}
                    </text>
                    <rect x={160} y={y + 4} width={380} height={14} rx={7} className="fill-muted" />
                    <rect x={160} y={y + 4} width={w} height={14} rx={7} className={color} />
                    <text x={550} y={y + 15} className="fill-muted-foreground text-[11px] tabular-nums">
                      {pct(c.avg_success_rate)}
                    </text>
                    {c.avg_ci_width != null && c.avg_ci_width > 0 && (
                      // Bayesian CI whisker centered on the rate.
                      <line
                        x1={160 + Math.max(0, (c.avg_success_rate - c.avg_ci_width / 2) * 380)}
                        x2={160 + Math.min(380, (c.avg_success_rate + c.avg_ci_width / 2) * 380)}
                        y1={y + 11}
                        y2={y + 11}
                        strokeWidth={2}
                        className="stroke-foreground/50"
                      />
                    )}
                  </g>
                );
              })}
            </svg>
          </div>

          <div>
            <Button variant="ghost" size="sm" onClick={() => setShowDetails((v) => !v)}>
              {showDetails ? "Hide details" : "Details"}
            </Button>
            {showDetails && (
              <div className="mt-2 overflow-auto rounded-xl border border-border">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-card text-left">
                      {["category", routines, "successes", `${run}s`, "avg duration", ""].map((h) => (
                        <th key={h} className="px-3 py-2 font-medium text-muted-foreground">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {categories.map((c) => (
                      <tr key={c.category} className="border-b border-border/50 last:border-0">
                        <td className="px-3 py-2 font-medium">{c.category}</td>
                        <td className="px-3 py-2 tabular-nums">{c.automata_count}</td>
                        <td className="px-3 py-2 tabular-nums">{c.total_successes.toLocaleString()}</td>
                        <td className="px-3 py-2 tabular-nums">{c.total_executions.toLocaleString()}</td>
                        <td className="px-3 py-2 tabular-nums">
                          {c.avg_duration_ms != null ? `${Math.round(c.avg_duration_ms)}ms` : "—"}
                        </td>
                        <td className="px-3 py-2">
                          {c.avg_success_rate < 0.5 && <Badge variant="destructive">needs attention</Badge>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="mt-3 text-xs text-muted-foreground">
              Each promotion {stopsIfCheckFails} and {needsYourOK} — {proofOfWhatWasDone}, not vibes.
            </p>
          </div>
        </div>
      )}
    </PageShell>
  );
}
