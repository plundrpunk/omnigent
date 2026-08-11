/**
 * Models page — role → provider routing drawn as a mapping diagram.
 *
 * Roles on the left, providers on the right, curves showing who routes
 * where; line and dot color carry provider health. A banner leads the
 * page whenever a routed provider isn't online. Costs and endpoints
 * live in the provider cards below the diagram.
 *
 * Editing (P3.1): select a role node → provider picker → PUT
 * role-mappings through the bridge's write table → diagram refetches.
 * AMS validates the provider against its registry. No optimistic redraw —
 * the diagram only ever shows what AMS confirmed.
 */

import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageShell } from "@/components/PageShell";
import { Spinner } from "@/components/ui/spinner";
import {
  AmsError,
  fetchProviders,
  fetchRoleMappings,
  fetchWardenAgents,
  putFleetModel,
  putRoleMappings,
  wardenDefaultModel,
  type LlmProvider,
  type WardenAgent,
} from "@/lib/ams";
import { perMillionWords } from "@/lib/copy";
import { hostFetch } from "@/lib/host";

const ROW_H = 44;
const PAD = 24;

/** One locally installed CLI coding agent, from GET /v1/coding-agents. */
interface CliAgent {
  key: string;
  display_name?: string;
  vendor?: string;
  installed: boolean;
  version?: string;
  models?: { id: string; label?: string }[];
}

/**
 * Model strings the fleet picker offers. Valid = anything the AMS LLM
 * router's prefix table resolves (registry.py MODEL_PROVIDER_ROUTES):
 * codex, gpt-*, claude-*, gemini-*, kimi-*, moonshot-*, kilo/*, plus
 * fireworks-hosted open-model prefixes. Curated, not exhaustive — the
 * free-text input takes anything the router can resolve.
 */
// Curated by Drew (2026-07-28): codex resolves server-side to gpt-5.6
// at high reasoning effort (ABOT_CODEX_BACKEND_MODEL /
// OPENAI_REASONING_EFFORT); the rest are offered exactly as listed.
const FLEET_MODEL_OPTIONS = [
  "codex",
  "gpt-5.5",
  "claude-sonnet-5",
  "claude-opus-5",
  "claude-fable-5",
  "gemini-3.1-pro",
  "kimi-code-k3",
  "kilo/auto",
];

/** Honest cost label: $0 means plan-included, absent means unknown. */
function costLabel(p: LlmProvider): string {
  const inC = p.cost_per_mtok_input;
  const outC = p.cost_per_mtok_output;
  if (inC == null && outC == null) return "cost unknown";
  if ((inC ?? 0) === 0 && (outC ?? 0) === 0) return "Included in plan";
  return `$${inC} in / $${outC} out ${perMillionWords}`;
}

export function ModelsPage() {
  const [providers, setProviders] = useState<LlmProvider[] | null>(null);
  const [roles, setRoles] = useState<Record<string, string> | null>(null);
  const [activeProvider, setActiveProvider] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [selectedRole, setSelectedRole] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Fleet + CLI-agent state (Models page P2/P3.2).
  const [cliAgents, setCliAgents] = useState<CliAgent[] | null>(null);
  const [fleet, setFleet] = useState<WardenAgent[] | null>(null);
  const [selectedHand, setSelectedHand] = useState<string | null>(null);
  const [fleetSaving, setFleetSaving] = useState(false);
  const [fleetNotice, setFleetNotice] = useState<string | null>(null);
  const [customModel, setCustomModel] = useState("");

  const toggleHand = (hand: string) => {
    setFleetNotice(null);
    setCustomModel("");
    setSelectedHand((current) => (current === hand ? null : hand));
  };

  const assignFleetModel = async (hand: string, model: string) => {
    setFleetSaving(true);
    setFleetNotice(null);
    try {
      const result = await putFleetModel(hand, model);
      setSelectedHand(null);
      setFleetNotice(
        result.restarted
          ? `${hand} → ${model} — HAND.toml updated, container restarted; the hand re-registers within ~15s.`
          : `${hand} → ${model} — HAND.toml updated, but the restart failed: ${result.restart_error ?? "unknown"}. Restart the container to apply.`,
      );
      // The hand re-registers on boot; refetch after it has had a moment.
      window.setTimeout(() => setRefreshKey((k) => k + 1), 15_000);
    } catch (err: unknown) {
      setFleetNotice(
        err instanceof AmsError
          ? "AMS couldn't save this fleet assignment."
          : err instanceof Error
            ? err.message
            : String(err),
      );
    } finally {
      setFleetSaving(false);
    }
  };

  const toggleRole = (role: string) => {
    setSaveError(null);
    setSelectedRole((current) => (current === role ? null : role));
  };

  const assignRole = async (role: string, providerType: string) => {
    setSaving(true);
    setSaveError(null);
    try {
      await putRoleMappings({ [role]: providerType });
      setSelectedRole(null);
      setRefreshKey((k) => k + 1); // redraw only from AMS-confirmed state
    } catch (err: unknown) {
      setSaveError(
        err instanceof AmsError
          ? "AMS couldn't save this role assignment."
          : err instanceof Error
            ? err.message
            : String(err),
      );
    } finally {
      setSaving(false);
    }
  };

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
        if (!cancelled) {
          setError(
            err instanceof AmsError
              ? "AMS couldn't load the model routing information."
              : err instanceof Error
                ? err.message
                : String(err),
          );
        }
      });
    // Fleet + CLI inventory load best-effort alongside the diagram — a
    // failure here must not blank role routing, so each catches to empty.
    void fetchWardenAgents()
      .then((agents) => {
        if (!cancelled) setFleet(agents);
      })
      .catch(() => {
        if (!cancelled) setFleet([]);
      });
    void hostFetch("/v1/coding-agents")
      .then(async (resp) => {
        const body = (await resp.json()) as { agents?: CliAgent[] };
        if (!cancelled) setCliAgents(Array.isArray(body.agents) ? body.agents : []);
      })
      .catch(() => {
        if (!cancelled) setCliAgents([]);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  // Team leads first (they own model routing for their teams), then Prime.
  const fleetHands = (fleet ?? [])
    .filter((a) => {
      const arch = a.metadata?.["archetype"];
      return arch === "team-lead" || /^tl-/.test(a.agent_id);
    })
    .sort((a, b) => a.agent_id.localeCompare(b.agent_id));

  // Installed CLI agents' model ids join the fleet picker — but only ids
  // the AMS LLM router can actually resolve. The router is prefix-based
  // (registry.py MODEL_PROVIDER_ROUTES); offering an unroutable id (e.g.
  // Claude's bare "opus" alias) would mint assignments that 400 on the
  // TL's first completion, so unroutable ids are dropped, not risked.
  // The picker offers EXACTLY the curated list -- CLI-discovered model
  // ids stay display-only in the cards below (Drew, 2026-07-28:
  // "erase the rest").
  const fleetModelOptions = FLEET_MODEL_OPTIONS;

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
      subtitle="Who thinks with what — select a role to reroute it to a provider."
      actions={
        <Button variant="outline" size="sm" onClick={() => setRefreshKey((k) => k + 1)}>
          Refresh
        </Button>
      }
    >
      {error && (
        <div className="mb-3 flex items-center justify-between gap-3 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <span>{error}</span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setRefreshKey((k) => k + 1)}
          >
            Retry
          </Button>
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

          <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            System roles → AMS providers
          </h2>
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
              {/* role nodes — select to reroute */}
              {diagram.roleNames.map((role, i) => {
                const y = PAD + i * ROW_H + ROW_H / 2;
                const selected = role === selectedRole;
                return (
                  <g
                    key={role}
                    data-testid={`role-node-${role}`}
                    role="button"
                    tabIndex={0}
                    aria-label={`Reroute ${role}`}
                    className="cursor-pointer"
                    onClick={() => toggleRole(role)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        toggleRole(role);
                      }
                    }}
                  >
                    <rect
                      x={16}
                      y={y - 15}
                      width={LEFT_X - 16}
                      height={30}
                      rx={15}
                      className={
                        selected
                          ? "fill-primary/25 stroke-primary"
                          : "fill-primary/10 stroke-primary/50"
                      }
                      strokeWidth={selected ? 1.8 : 1.2}
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

          {selectedRole && (
            <div
              data-testid="role-picker"
              className="rounded-xl border border-primary/40 bg-primary/5 p-4"
            >
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-medium">
                  Route <span className="font-mono">{selectedRole}</span> to:
                </p>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={saving}
                  onClick={() => setSelectedRole(null)}
                >
                  Cancel
                </Button>
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                {diagram.providerTypes.map((type) => {
                  const p = providerByType.get(type);
                  const current = roles[selectedRole] === type;
                  return (
                    <Button
                      key={type}
                      variant={current ? "secondary" : "outline"}
                      size="sm"
                      disabled={saving || current}
                      className="gap-1.5 font-mono text-xs"
                      onClick={() => void assignRole(selectedRole, type)}
                    >
                      <span
                        className={`size-2 rounded-full ${
                          p?.status === "online" ? "bg-emerald-500" : "bg-destructive"
                        }`}
                      />
                      {type}
                      {current ? " (current)" : ""}
                    </Button>
                  );
                })}
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                {saving
                  ? "Saving — waiting for AMS to confirm…"
                  : "Applies immediately via PUT role-mappings; the diagram redraws from AMS-confirmed state only."}
              </p>
              {saveError && (
                <p className="mt-2 text-xs text-destructive">{saveError}</p>
              )}
            </div>
          )}

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

          {/* ── Fleet teams: who runs on what ─────────────────────── */}
          <div className="space-y-3" data-testid="fleet-models">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              Fleet teams → models
            </h2>
            <p className="text-xs text-muted-foreground">
              Each team lead runs on its hand&apos;s default model. Assigning
              here edits the hand&apos;s HAND.toml and restarts its container —
              the change is load-bearing, not a display value.
            </p>
            {fleetNotice && (
              <p className="rounded-lg border border-primary/40 bg-primary/5 px-3 py-2 text-xs">
                {fleetNotice}
              </p>
            )}
            {fleet === null ? (
              <div className="flex items-center gap-2 py-4 text-muted-foreground">
                <Spinner /> Loading fleet…
              </div>
            ) : fleetHands.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No team-lead hands are registered with the warden.
              </p>
            ) : (
              <div className="overflow-hidden rounded-xl border border-border">
                <table className="w-full text-sm">
                  <thead className="bg-card/70 text-left text-xs uppercase tracking-wide text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2">Team lead</th>
                      <th className="px-3 py-2">Domain</th>
                      <th className="px-3 py-2">Model</th>
                      <th className="px-3 py-2">State</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border bg-card/40">
                    {fleetHands.map((hand) => {
                      const model = wardenDefaultModel(hand);
                      const domain = hand.metadata?.["domain"];
                      const selected = selectedHand === hand.agent_id;
                      return (
                        <tr key={hand.agent_id}>
                          <td className="px-3 py-2 font-mono text-xs">{hand.agent_id}</td>
                          <td className="px-3 py-2 text-xs text-muted-foreground">
                            {typeof domain === "string" ? domain : "—"}
                          </td>
                          <td className="px-3 py-2">
                            <Button
                              variant={selected ? "secondary" : "outline"}
                              size="sm"
                              className="h-7 font-mono text-xs"
                              data-testid={`fleet-model-${hand.agent_id}`}
                              onClick={() => toggleHand(hand.agent_id)}
                            >
                              {model ?? "unreported"}
                            </Button>
                          </td>
                          <td className="px-3 py-2 text-xs text-muted-foreground">
                            {hand.alive ? hand.status : "offline"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            {selectedHand && (
              <div
                data-testid="fleet-model-picker"
                className="rounded-xl border border-primary/40 bg-primary/5 p-4"
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-medium">
                    Run <span className="font-mono">{selectedHand}</span> on:
                  </p>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={fleetSaving}
                    onClick={() => setSelectedHand(null)}
                  >
                    Cancel
                  </Button>
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {fleetModelOptions.map((model) => (
                    <Button
                      key={model}
                      variant="outline"
                      size="sm"
                      disabled={fleetSaving}
                      className="font-mono text-xs"
                      onClick={() => void assignFleetModel(selectedHand, model)}
                    >
                      {model}
                    </Button>
                  ))}
                </div>
                <div className="mt-3 flex items-center gap-2">
                  <input
                    value={customModel}
                    onChange={(e) => setCustomModel(e.target.value)}
                    placeholder="any routable model, e.g. claude-opus-4-5"
                    className="h-8 flex-1 rounded-md border border-border bg-background px-2 font-mono text-xs"
                  />
                  <Button
                    size="sm"
                    disabled={fleetSaving || customModel.trim().length === 0}
                    onClick={() => void assignFleetModel(selectedHand, customModel.trim())}
                  >
                    Assign
                  </Button>
                </div>
                <p className="mt-2 text-xs text-muted-foreground">
                  {fleetSaving
                    ? "Saving — editing HAND.toml and restarting the hand…"
                    : "codex runs gpt-5.6 at high reasoning effort. Free-text takes anything the AMS router resolves by prefix (gpt-*, claude-*, gemini-*, kimi-*, kilo/*)."}
                </p>
              </div>
            )}
          </div>

          {/* ── CLI agents on this host ───────────────────────────── */}
          <div className="space-y-3" data-testid="cli-agents">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              CLI agents on this host
            </h2>
            <p className="text-xs text-muted-foreground">
              Locally installed coding CLIs. Their models feed the fleet
              picker above; plan-backed CLIs run at no per-token cost.
            </p>
            {cliAgents === null ? (
              <div className="flex items-center gap-2 py-4 text-muted-foreground">
                <Spinner /> Scanning…
              </div>
            ) : (
              <div className="grid gap-3 md:grid-cols-3">
                {cliAgents.map((a) => (
                  <div
                    key={a.key}
                    className="rounded-xl border border-border bg-card p-4"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-semibold">
                        {a.display_name ?? a.key}
                      </span>
                      <Badge variant={a.installed ? "default" : "outline"}>
                        {a.installed ? "installed" : "not installed"}
                      </Badge>
                    </div>
                    <p className="mt-1 truncate text-xs text-muted-foreground">
                      {a.vendor ?? "—"}
                      {a.version ? ` · ${a.version}` : ""}
                    </p>
                    {a.installed && (a.models?.length ?? 0) > 0 && (
                      <p className="mt-2 truncate font-mono text-xs text-muted-foreground">
                        {(a.models ?? [])
                          .slice(0, 3)
                          .map((m) => m.id)
                          .join(" · ")}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </PageShell>
  );
}
