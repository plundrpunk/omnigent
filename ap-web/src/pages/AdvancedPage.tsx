import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  acrossAllRuns,
  averagePerRoutine,
  connectedApps,
  failedACheck,
  finished,
  lastSeen,
  memoryUsed,
  needsYourOK,
  neverStarted,
  pausedForInput,
  perMillionWords,
  proofOfWhatWasDone,
  routines,
  stopsIfCheckFails,
} from "@/lib/copy";

interface CodingAgent {
  key: string;
  display_name?: string;
  vendor?: string;
  installed: boolean;
}

interface CodingAgentsResponse {
  agents: CodingAgent[];
}

const destinations = [
  {
    name: "System",
    path: "../system",
    description: `Review ${connectedApps} and ${proofOfWhatWasDone}.`,
  },
  {
    name: "Fleet",
    path: "../fleet",
    description: `See agent ${lastSeen}, ${memoryUsed}, and ${neverStarted} states.`,
  },
  {
    name: "Patterns",
    path: "../patterns",
    description: `Compare results ${acrossAllRuns} and the ${averagePerRoutine}.`,
  },
  {
    name: "Models",
    path: "../models",
    description: `Review model availability and cost ${perMillionWords}.`,
  },
  {
    name: "Loops",
    path: "../loops",
    description: `Manage ${routines}; each ${stopsIfCheckFails}.`,
  },
  {
    name: "Training",
    path: "../training",
    description: `Review work that ${needsYourOK} and results marked ${finished}, ${pausedForInput}, or ${failedACheck}.`,
  },
] as const;

export function AdvancedPage() {
  const [agents, setAgents] = useState<CodingAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();

    async function loadAgents() {
      setLoading(true);
      setError(false);

      try {
        const response = await fetch("/v1/coding-agents", {
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`Request failed with status ${response.status}`);
        }

        const payload = (await response.json()) as CodingAgentsResponse;
        setAgents(Array.isArray(payload.agents) ? payload.agents : []);
      } catch (loadError) {
        if (loadError instanceof DOMException && loadError.name === "AbortError") {
          return;
        }
        setError(true);
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    }

    void loadAgents();
    return () => controller.abort();
  }, []);

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-8 px-6 py-8">
      <header>
        <h1 className="text-2xl font-semibold text-foreground">Advanced</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Operator tools and locally discovered coding agents.
        </p>
      </header>

      <section aria-labelledby="destinations-heading">
        <h2 id="destinations-heading" className="text-lg font-semibold text-foreground">
          Destinations
        </h2>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {destinations.map((destination) => (
            <Link
              key={destination.name}
              to={destination.path}
              relative="path"
              className="rounded-lg border border-border bg-card p-4 transition-colors hover:bg-accent"
            >
              <h3 className="font-medium text-foreground">{destination.name}</h3>
              <p className="mt-1 text-sm text-muted-foreground">{destination.description}</p>
            </Link>
          ))}
        </div>
      </section>

      <section aria-labelledby="agents-heading">
        <h2 id="agents-heading" className="text-lg font-semibold text-foreground">
          Agents
        </h2>

        {loading && <p className="mt-4 text-sm text-muted-foreground">Loading agents…</p>}
        {!loading && error && (
          <p className="mt-4 text-sm text-destructive" role="alert">
            Agents could not be loaded.
          </p>
        )}
        {!loading && !error && agents.length === 0 && (
          <p className="mt-4 text-sm text-muted-foreground">No agents were discovered.</p>
        )}
        {!loading && !error && agents.length > 0 && (
          <ul className="mt-4 divide-y divide-border rounded-lg border border-border bg-card">
            {agents.map((agent) => (
              <li key={agent.key} className="flex items-center justify-between gap-4 px-4 py-3">
                <div>
                  <p className="font-medium text-foreground">{agent.display_name || agent.key}</p>
                  <p className="text-sm text-muted-foreground">
                    {agent.vendor || "Vendor unavailable"}
                  </p>
                </div>
                <span
                  className={
                    agent.installed
                      ? "text-sm font-medium text-emerald-600 dark:text-emerald-400"
                      : "text-sm font-medium text-muted-foreground"
                  }
                >
                  {agent.installed ? "Installed" : "Unavailable"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
