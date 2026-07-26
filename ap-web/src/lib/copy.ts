export const vocabulary = {
  automaton: 'saved routine',
  automata: 'routines',
  execution: 'run',
  'MCP servers': 'connected apps',
  'ghost dispatch': 'never started',
  last_heartbeat: 'last seen',
  context_pct: 'memory used',
  'run-weighted': 'across all runs',
  unweighted: 'average per routine',
  'fail-closed': 'stops if a check fails',
  receipt: 'proof of what was done',
  gate: 'needs your OK',
  'exit code 0': 'finished',
  'exit code 3': 'paused for input',
  'exit code 6': 'failed a check',
  Mtok: 'per million words',
} as const;

export const savedRoutine = vocabulary.automaton;
export const routines = vocabulary.automata;
export const run = vocabulary.execution;
export const connectedApps = vocabulary['MCP servers'];
export const neverStarted = vocabulary['ghost dispatch'];
export const lastSeen = vocabulary.last_heartbeat;
export const memoryUsed = vocabulary.context_pct;
export const acrossAllRuns = vocabulary['run-weighted'];
export const averagePerRoutine = vocabulary.unweighted;
export const stopsIfCheckFails = vocabulary['fail-closed'];
export const proofOfWhatWasDone = vocabulary.receipt;
export const needsYourOK = vocabulary.gate;
export const finished = vocabulary['exit code 0'];
export const pausedForInput = vocabulary['exit code 3'];
export const failedACheck = vocabulary['exit code 6'];
export const perMillionWords = vocabulary.Mtok;

export function relativeTime(iso: string): string {
  const timestamp = Date.parse(iso);

  if (!Number.isFinite(timestamp)) {
    return neverStarted;
  }

  const elapsedSeconds = Math.floor((Date.now() - timestamp) / 1_000);

  if (elapsedSeconds < 60) {
    return 'just now';
  }

  const elapsedMinutes = Math.floor(elapsedSeconds / 60);
  if (elapsedMinutes < 60) {
    return `${elapsedMinutes}m ago`;
  }

  const elapsedHours = Math.floor(elapsedMinutes / 60);
  if (elapsedHours < 24) {
    return `${elapsedHours}h ago`;
  }

  const elapsedDays = Math.floor(elapsedHours / 24);
  if (elapsedDays < 30) {
    return `${elapsedDays}d ago`;
  }

  const elapsedMonths = Math.floor(elapsedDays / 30);
  if (elapsedMonths < 12) {
    return `${elapsedMonths}mo ago`;
  }

  return `${Math.floor(elapsedDays / 365)}y ago`;
}
