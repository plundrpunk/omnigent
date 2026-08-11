import type { AvailableAgent } from "@/hooks/useAvailableAgents";

export const WRAPPER_LABEL_KEY = "omnigent.wrapper";
export const UI_MODE_LABEL_KEY = "omnigent.ui";
export const UI_MODE_TERMINAL_VALUE = "terminal";

export type NativeCodingAgentIconKind = "claude" | "codex" | "pi";
export type NativeCodingAgentCapability = "permissionMode" | "approvalMode";

export interface NativeCodingAgentSpec {
  key: NativeCodingAgentIconKind;
  agentName: string;
  /**
   * Every agent name that IS this native coding agent rather than
   * merely being built on its harness: the canonical wrapper row plus
   * the legacy seeded row. Only these collapse into one picker entry.
   * Harness alone is not identity -- a custom agent can be built on
   * `claude-native` and still be its own choice (#abot_prime).
   */
  aliasAgentNames: readonly string[];
  harness: string;
  wrapperLabel: string;
  displayName: string;
  iconKind: NativeCodingAgentIconKind;
  sortRank: number;
  capabilities?: readonly NativeCodingAgentCapability[];
}

export const NATIVE_CODING_AGENTS = [
  {
    key: "claude",
    agentName: "claude-native-ui",
    aliasAgentNames: ["claude-native-ui", "claude_code"],
    harness: "claude-native",
    wrapperLabel: "claude-code-native-ui",
    displayName: "Claude Code",
    iconKind: "claude",
    sortRank: 10,
    capabilities: ["permissionMode"],
  },
  {
    key: "codex",
    agentName: "codex-native-ui",
    aliasAgentNames: ["codex-native-ui", "codex"],
    harness: "codex-native",
    wrapperLabel: "codex-native-ui",
    displayName: "Codex",
    iconKind: "codex",
    sortRank: 20,
    capabilities: ["approvalMode"],
  },
  {
    key: "pi",
    agentName: "pi-native-ui",
    aliasAgentNames: ["pi-native-ui", "pi"],
    harness: "pi-native",
    wrapperLabel: "pi-native-ui",
    displayName: "Pi",
    iconKind: "pi",
    sortRank: 30,
  },
] as const satisfies readonly NativeCodingAgentSpec[];

const BY_AGENT_NAME: Map<string, NativeCodingAgentSpec> = new Map(
  NATIVE_CODING_AGENTS.map((agent) => [agent.agentName, agent]),
);
const BY_HARNESS: Map<string, NativeCodingAgentSpec> = new Map(
  NATIVE_CODING_AGENTS.map((agent) => [agent.harness, agent]),
);
const BY_ALIAS_NAME: Map<string, NativeCodingAgentSpec> = new Map(
  NATIVE_CODING_AGENTS.flatMap((agent) =>
    agent.aliasAgentNames.map((alias) => [alias, agent] as const),
  ),
);
const BY_WRAPPER: Map<string, NativeCodingAgentSpec> = new Map(
  NATIVE_CODING_AGENTS.map((agent) => [agent.wrapperLabel, agent]),
);

// Reversed harness spellings that fold to a canonical native `harness`.
// Mirrors omnigent.harness_aliases on the server: only `native-pi` is a
// supported reversed alias (claude/codex use the canonical form).
const HARNESS_ALIASES: Record<string, string> = {
  "native-pi": "pi-native",
};

export function nativeCodingAgentForAgentName(
  name: string | null | undefined,
): NativeCodingAgentSpec | undefined {
  return name == null ? undefined : BY_AGENT_NAME.get(name);
}

export function nativeCodingAgentForHarness(
  harness: string | null | undefined,
): NativeCodingAgentSpec | undefined {
  if (harness == null) return undefined;
  return BY_HARNESS.get(HARNESS_ALIASES[harness] ?? harness);
}

/**
 * The native coding agent this name IS, if any -- identity, not
 * capability. Use this to decide whether two picker rows are the same
 * choice, or what to label a row.
 *
 * Deliberately narrower than {@link nativeCodingAgentForAvailableAgent},
 * which matches on harness first and so claims EVERY agent built on
 * `claude-native` / `codex-native` / `pi-native`. That breadth is right
 * for icons and capability probes (a custom claude-native agent really
 * does support permission modes) and wrong for identity: it collapsed
 * custom orchestrators out of the picker entirely, leaving them
 * unlaunchable.
 */
export function nativeCodingAgentRowForName(
  name: string | null | undefined,
): NativeCodingAgentSpec | undefined {
  return name == null ? undefined : BY_ALIAS_NAME.get(name);
}

export function nativeCodingAgentForWrapper(
  wrapper: string | null | undefined,
): NativeCodingAgentSpec | undefined {
  return wrapper == null ? undefined : BY_WRAPPER.get(wrapper);
}

export function nativeCodingAgentForAvailableAgent(
  agent: Pick<AvailableAgent, "name" | "harness"> | null | undefined,
): NativeCodingAgentSpec | undefined {
  if (agent == null) return undefined;
  return nativeCodingAgentForHarness(agent.harness) ?? nativeCodingAgentForAgentName(agent.name);
}

export function isNativeCodingAgent(
  agent: Pick<AvailableAgent, "name" | "harness"> | null | undefined,
): boolean {
  return nativeCodingAgentForAvailableAgent(agent) !== undefined;
}

export function isNativeWrapper(wrapper: string | null | undefined): boolean {
  return nativeCodingAgentForWrapper(wrapper) !== undefined;
}

export function nativeWrapperLabelsForAgent(
  agent: Pick<AvailableAgent, "name" | "harness"> | null | undefined,
): Record<string, string> | undefined {
  const nativeAgent = nativeCodingAgentForAvailableAgent(agent);
  if (nativeAgent === undefined) return undefined;
  return {
    [UI_MODE_LABEL_KEY]: UI_MODE_TERMINAL_VALUE,
    [WRAPPER_LABEL_KEY]: nativeAgent.wrapperLabel,
  };
}

export function nativeDisplayNameForAgent(agent: Pick<AvailableAgent, "name" | "harness">): string {
  return (
    nativeCodingAgentForAvailableAgent(agent)?.displayName ??
    nativeCodingAgentForAgentName(agent.name)?.displayName ??
    agent.name
  );
}

export function nativeAgentSortRank(agent: Pick<AvailableAgent, "name" | "harness">): number {
  return nativeCodingAgentForAvailableAgent(agent)?.sortRank ?? Number.POSITIVE_INFINITY;
}

export function nativeAgentHasCapability(
  agent: Pick<AvailableAgent, "name" | "harness"> | null | undefined,
  capability: NativeCodingAgentCapability,
): boolean {
  return nativeCodingAgentForAvailableAgent(agent)?.capabilities?.includes(capability) ?? false;
}
