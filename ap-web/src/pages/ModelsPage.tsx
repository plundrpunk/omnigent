/**
 * Models page — role → provider routing drawn as a mapping diagram.
 *
 * Roles on the left, providers on the right, curves showing who routes
 * where; line and dot color carry provider health. A banner leads the
 * page whenever a routed provider isn't online. Costs and endpoints
 * live in the provider cards below the diagram. Read-only until the
 * bridge grows a reviewed write whitelist.
 */

import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageShell } from "@/components/PageShell";
import { Spinner } from "@/components/ui/spinner";
import { fetchProviders, fetchRoleMappings, type LlmProvider } from "@/lib/ams";

const ROW_H = 44;
const PAD = 24;

/** Honest cost label: $0 means plan-included, absent means unknown. */
function costLabel(p: LlmProvider): string {
  const inC = p.cost_per_mtok_input;
  const outC = p.cost_per_mtok_output;
  if (inC == null && outC == null) return "cost unknown";
  if ((inC ?? 0) === 0 && (outC ?? 0) === 0) return "Included in plan";
  return `$${inC} in / $${outC} out per Mtok`;
}

export function ModelsPage() {
  const [providers, setProviders] = useState<LlmProvider[] | null>(null);
  const [roles, setRoles] = useState<Record<string, string> | null>(null);
  const [activeProvider, setActiveProvider] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    void Promise.all([fetchProviders(), fetchRoleMappings()])
      .then(([p, r]) => {
        if (!cancelled) {
          setProviders(p.providers ?? []);
          setRoles(r);
          setActiveProvider(p.active_provider ?? null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  const diagram = useMemo(() => {
    if (!providers || !roles) return null;
    const roleNames = Object.keys(roles);
    const providerTypes = [...new Set([...providers.map((p) => p.type), ...Object.values(roles)])];
    const height = PAD * 2 + Math.max(roleNames.length, providerTypes.length) * ROW_H;
    return { roleNames, providerTypes, height };
  }, [providers, roles]);

  const providerByType = new Map((providers ?? []).map((p) => [p.type, p]));

  // Routed-but-offline providers: anything referenced by role_mappings or
  // named as the active provider whose status isn't "online".
  const allRoleNames = roles ? Object.keys(roles) : [];
  const routedTypes = new Set([
    ...Object.values(roles ?? {}),
    ...(activeProvider ? [activeProvider] : []),
  ]);
  const offlineRouted = [...routedTypes].flatMap((type) => {
    const p = providerByType.get(type);
    if (!p || p.status === "online") return [];
    const affected = allRoleNames.filter((r) => roles?.[r] === type);
    const roleText =
      affected.length === 0
        ? "no roles routed"
        : affected.length === allRoleNames.length && affected.length > 2
          ? `${affected.slice(0, 2).join(", ")}, … (all ${affected.length} roles)`
          : affected.join(", ");
    return [
      {
        type,
        name: p.name,
        status: p.status ?? "unknown",
        roleText,
        isActive: type === activeProvider,
      },
    ];
  });

  const W = 640;
  const LEFT_X = 150;
  const RIGHT_X = W - 160;

  return (
    <PageShell
      title="Models"
      subtitle="Who thinks with what — each role routes to a provider. Read-only for now."
      actions={
        <Button variant="outline" size="sm" onClick={() => setRefreshKey((k) => k + 1)}>
          Refresh
        </Button>
      }
    >
      {error && (
        <div className="mb-3 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          Couldn&apos;t load: {error}
        </div>
      )}

      {!diagram || !providers || !roles ? (
        <div className="flex items-center gap-2 py-10 text-muted-foreground">
          <Spinner /> Loading…
        </div>
      ) : (
        <div className="space-y-6">
          {offlineRouted.length > 0 && (
            <div className="rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
              <p className="font-semibold">Routed provider offline</p>
              <ul className="mt-1 space-y-1">
                {offlineRouted.map((o) => (
                  <li key={o.type}>
                    <span className="font-medium">{o.name}</span>
                    {` — status: ${o.status} · routes: ${o.roleText}`}
                    {o.isActive ? " · active provider" : ""}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="overflow-x-auto rounded-xl border border-border bg-card/50 p-4">
            <svg
              viewBox={`0 0 ${W} ${diagram.height}`}
              className="mx-auto h-auto w-full max-w-3xl min-w-[560px]"
            >
              {/* connections */}
              {diagram.roleNames.map((role, i) => {
                const target = roles[role];
                const j = diagram.providerTypes.indexOf(target);
                if (j < 0) return null;
                const y1 = PAD + i * ROW_H + ROW_H / 2;
                const y2 = PAD + j * ROW_H + ROW_H / 2;
                const p = providerByType.get(target);
                const healthy = p?.status === "online";
                return (
                  <path
                    key={role}
                    d={`M ${LEFT_X} ${y1} C ${(LEFT_X + RIGHT_X) / 2} ${y1}, ${(LEFT_X + RIGHT_X) / 2} ${y2}, ${RIGHT_X} ${y2}`}
                    fill="none"
                    strokeWidth={1.6}
                    className={healthy ? "stroke-emerald-500/60" : "stroke-destructive/60"}
                  />
                );
              })}
              {/* role nodes */}
              {diagram.roleNames.map((role, i) => {
                const y = PAD + i * ROW_H + ROW_H / 2;
                return (
                  <g key={role}>
                    <rect
                      x={16}
                      y={y - 15}
                      width={LEFT_X - 16}
                      height={30}
                      rx={15}
                      className="fill-primary/10 stroke-primary/50"
                      strokeWidth={1.2}
                    />
                    <text
                      x={(16 + LEFT_X) / 2}
                      y={y + 4}
                      textAnchor="middle"
                      className="fill-foreground text-[12px] font-medium"
                    >
                      {role}
                    </text>
                  </g>
                );
              })}
              {/* provider nodes */}
              {diagram.providerTypes.map((type, j) => {
                const y = PAD + j * ROW_H + ROW_H / 2;
                const p = providerByType.get(type);
                const healthy = p?.status === "online";
                return (
                  <g key={type}>
                    <rect
                      x={RIGHT_X}
                      y={y - 15}
                      width={W - RIGHT_X - 16}
                      height={30}
                      rx={15}
                      className={
                        healthy
                          ? "fill-emerald-500/10 stroke-emerald-500/60"
                          : "fill-destructive/10 stroke-destructive/60"
                      }
                      strokeWidth={1.2}
                    />
                    <circle
                      cx={RIGHT_X + 14}
                      cy={y}
                      r={4}
                      className={healthy ? "fill-emerald-500" : "fill-destructive"}
                    />
                    <text
                      x={RIGHT_X + 26}
                      y={y + 4}
                      className="fill-foreground text-[12px] font-medium"
                    >
                      {type}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            {providers.map((p) => (
              <div key={p.type} className="rounded-xl border border-border bg-card p-4">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-semibold">{p.name}</span>
                  <span className="flex shrink-0 items-center gap-1">
                    {p.type === activeProvider && <Badge variant="secondary">active</Badge>}
                    <Badge variant={p.status === "online" ? "default" : "destructive"}>
                      {p.status ?? "unknown"}
                    </Badge>
                  </span>
                </div>
                <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
                  {p.model ?? "—"}
                </p>
                <p className="truncate font-mono text-xs text-muted-foreground">
                  {p.endpoint ?? "—"}
                </p>
                <p className="mt-2 text-xs text-muted-foreground">
                  {p.latency_ms != null ? `${p.latency_ms}ms · ` : ""}
                  {costLabel(p)}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}
    </PageShell>
  );
}
