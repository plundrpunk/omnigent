/**
 * System — a landing of six tiles, one per registry; click to drill in.
 *
 * Complexity stays behind the click: the landing shows what exists and
 * how much of it; the drill-in shows the rows (with a Raw JSON escape
 * hatch). Data comes read-only through the server's `/v1/ams/*` bridge.
 */

import { useEffect, useMemo, useState } from "react";
import {
  BotIcon,
  CalendarClockIcon,
  CogIcon,
  PlugIcon,
  SparklesIcon,
  TargetIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageShell } from "@/components/PageShell";
import { Spinner } from "@/components/ui/spinner";
import { hostFetch } from "@/lib/host";

interface Registry {
  id: string;
  label: string;
  blurb: string;
  path: string;
  Icon: typeof BotIcon;
}

const REGISTRIES: Registry[] = [
  { id: "agents", label: "Agents", blurb: "who's in the fleet", path: "/v1/ams/api/warden/agents", Icon: BotIcon },
  { id: "skills", label: "Skills", blurb: "what they know how to do", path: "/v1/ams/api/v1/skills", Icon: SparklesIcon },
  { id: "schedules", label: "Schedules", blurb: "what runs on its own", path: "/v1/ams/api/v1/schedules", Icon: CalendarClockIcon },
  { id: "automata", label: "Automata", blurb: "reusable building blocks", path: "/v1/ams/api/v1/automata/", Icon: CogIcon },
  { id: "goals", label: "Goals", blurb: "what we're working toward", path: "/v1/ams/api/v1/goals/dashboard", Icon: TargetIcon },
  { id: "mcp", label: "MCP servers", blurb: "connected tools", path: "/v1/ams/api/v1/mcp-servers", Icon: PlugIcon },
];

function extractRows(payload: unknown): Record<string, unknown>[] {
  if (Array.isArray(payload)) return payload as Record<string, unknown>[];
  if (payload && typeof payload === "object") {
    const obj = payload as Record<string, unknown>;
    for (const key of ["data", "items", "results", "agents", "skills", "schedules", "automata", "goals", "servers"]) {
      if (Array.isArray(obj[key])) return obj[key] as Record<string, unknown>[];
    }
    const arrays = Object.values(obj).filter(Array.isArray) as Record<string, unknown>[][];
    if (arrays.length > 0) return arrays.reduce((a, b) => (b.length > a.length ? b : a));
  }
  return [];
}

function chooseColumns(rows: Record<string, unknown>[], max = 5): string[] {
  const preferred = ["name", "agent_name", "title", "slug", "status", "alive", "last_heartbeat", "context_pct", "description", "cron_expression"];
  const seen = new Set<string>();
  for (const row of rows.slice(0, 20)) {
    for (const [k, v] of Object.entries(row)) {
      if (v == null) continue;
      const t = typeof v;
      if (t === "string" || t === "number" || t === "boolean") seen.add(k);
    }
  }
  return [...preferred.filter((k) => seen.has(k)), ...[...seen].filter((k) => !preferred.includes(k))].slice(0, max);
}

function formatCell(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  const s = String(value);
  return s.length > 100 ? `${s.slice(0, 97)}…` : s;
}

type Fetched =
  | { rows: Record<string, unknown>[]; raw: unknown; total: number }
  | { error: string }
  | null;

/** True count: prefer the API's `total`/`count` field over returned-page length (endpoints paginate, default 50). */
function extractTotal(payload: unknown, rows: unknown[]): number {
  if (payload && typeof payload === "object") {
    const obj = payload as Record<string, unknown>;
    for (const key of ["total", "count", "total_count"]) {
      if (typeof obj[key] === "number") return obj[key] as number;
    }
  }
  return rows.length;
}

const PAGE_SIZE = 50;

function useRegistry(path: string, page = 0): Fetched {
  const [state, setState] = useState<Fetched>(null);
  useEffect(() => {
    let cancelled = false;
    setState(null);
    const sep = path.includes("?") ? "&" : "?";
    const paged = `${path}${sep}limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`;
    void hostFetch(paged)
      .then(async (resp) => {
        const body: unknown = await resp.json().catch(() => null);
        if (cancelled) return;
        if (!resp.ok) {
          setState({ error: `HTTP ${resp.status}` });
          return;
        }
        const rows = extractRows(body);
        setState({ rows, raw: body, total: extractTotal(body, rows) });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ error: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [path, page]);
  return state;
}

function Tile({ registry, onOpen }: { registry: Registry; onOpen: () => void }) {
  const data = useRegistry(registry.path);
  const count = data && "rows" in data ? data.total : null;
  return (
    <button
      onClick={onOpen}
      className="group rounded-xl border border-border bg-card p-5 text-left transition hover:border-primary/50 hover:bg-card/80"
    >
      <registry.Icon className="size-5 text-muted-foreground group-hover:text-primary" />
      <div className="mt-3 text-2xl font-semibold tabular-nums">
        {data === null ? <Spinner className="size-5" /> : "error" in data ? "—" : count}
      </div>
      <div className="mt-0.5 text-sm font-medium">{registry.label}</div>
      <div className="text-xs text-muted-foreground">{registry.blurb}</div>
    </button>
  );
}

function DrillIn({ registry, onBack }: { registry: Registry; onBack: () => void }) {
  const [page, setPage] = useState(0);
  const data = useRegistry(registry.path, page);
  const [showRaw, setShowRaw] = useState(false);
  const rows = data && "rows" in data ? data.rows : [];
  const columns = useMemo(() => chooseColumns(rows), [rows]);
  const total = data && "rows" in data ? data.total : 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={onBack}>
          ← All registries
        </Button>
        <h2 className="font-semibold">{registry.label}</h2>
        {data && "rows" in data && <Badge variant="secondary">{total}</Badge>}
        <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setShowRaw((v) => !v)}>
          {showRaw ? "Table" : "Raw JSON"}
        </Button>
      </div>

      {pageCount > 1 && (
        <div className="flex flex-wrap items-center gap-1">
          <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
            ←
          </Button>
          {Array.from({ length: pageCount }, (_, i) => (
            <Button
              key={i}
              variant={i === page ? "default" : "ghost"}
              size="sm"
              className="min-w-8 px-2 tabular-nums"
              onClick={() => setPage(i)}
            >
              {i + 1}
            </Button>
          ))}
          <Button
            variant="outline"
            size="sm"
            disabled={page >= pageCount - 1}
            onClick={() => setPage((p) => p + 1)}
          >
            →
          </Button>
          <span className="ml-2 text-xs text-muted-foreground">
            {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
          </span>
        </div>
      )}

      {data === null ? (
        <div className="flex items-center gap-2 py-10 text-muted-foreground">
          <Spinner /> Loading…
        </div>
      ) : "error" in data ? (
        <div className="py-10 text-sm text-destructive">Couldn&apos;t load: {data.error}</div>
      ) : showRaw ? (
        <pre className="max-h-[65vh] overflow-auto rounded-xl border border-border bg-card p-3 text-xs">
          {JSON.stringify(data.raw, null, 2)}
        </pre>
      ) : rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
          Nothing here yet.
        </div>
      ) : (
        <div className="overflow-auto rounded-xl border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-card text-left">
                {columns.map((col) => (
                  <th key={col} className="whitespace-nowrap px-3 py-2 font-medium text-muted-foreground">
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i} className="border-b border-border/50 last:border-0 hover:bg-muted/40">
                  {columns.map((col) => (
                    <td key={col} className="max-w-[26rem] truncate px-3 py-2">
                      {formatCell(row[col])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function SystemPage() {
  const [open, setOpen] = useState<Registry | null>(null);

  return (
    <PageShell
      title="System"
      subtitle={open ? undefined : "Everything the system knows about itself."}
    >
      {open ? (
        <DrillIn registry={open} onBack={() => setOpen(null)} />
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          {REGISTRIES.map((r) => (
            <Tile key={r.id} registry={r} onOpen={() => setOpen(r)} />
          ))}
        </div>
      )}
    </PageShell>
  );
}
