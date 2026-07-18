// Inline status indicators for non-tool, non-text, non-reasoning blocks.
// Each is small enough to live in one file.
//
// - ErrorBanner: destructive Alert with `[source]` + code + message.
// - RetryIndicator: muted one-liner about an in-flight retry.
// - CompactionMarker: permanent marker shown after compaction completes.
//   The in-progress state renders as a Shimmer in ChatPage, mirroring
//   the "Working…" indicator.

import { AlertCircleIcon, RotateCcwIcon, ShieldXIcon, ShrinkIcon } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

interface ErrorBannerProps {
  message: string;
  source: string;
  code: string;
}

/**
 * Loud destructive banner for `error` blocks. Falls back to `code` when
 * `message` is empty (matches the reducer's intent — never show a blank
 * panel even when the LLM error payload omits the message).
 */
export function ErrorBanner({ message, source, code }: ErrorBannerProps) {
  const display = message || code || "Unknown error";
  // The Claude SDK's upstream 401 string tells users to refresh
  // `~/.databrickscfg` — guidance that only applies to Databricks-backed
  // deployments. Keep the original text (it is the real error) but append
  // the remedy that applies elsewhere, so a local/VPS AOS user isn't sent
  // chasing a config file their setup never uses.
  const misleadingDatabricksHint =
    display.includes(".databrickscfg") && (display.includes("401") || /unauthori[sz]ed/i.test(display));
  return (
    <Alert variant="destructive">
      <AlertCircleIcon />
      <AlertTitle>
        Error{source ? ` · ${source}` : ""}
        {code && message ? ` · ${code}` : ""}
      </AlertTitle>
      <AlertDescription>{display}</AlertDescription>
      {misleadingDatabricksHint && (
        <AlertDescription className="mt-1 opacity-90">
          Note: the ~/.databrickscfg hint above only applies to Databricks-backed servers. On this
          deployment a 401 usually means the model provider credential the server holds is missing
          or expired — check provider status on the Models page, or re-authenticate with
          `omnigent login &lt;server-url&gt;`.
        </AlertDescription>
      )}
    </Alert>
  );
}

interface PolicyDeniedBannerProps {
  reason: string;
  phase: string;
}

/**
 * Warning banner for policy denials. Uses the `default` alert variant
 * (amber/warning tone) to distinguish from hard errors (destructive red).
 */
export function PolicyDeniedBanner({ reason, phase }: PolicyDeniedBannerProps) {
  return (
    <Alert>
      <ShieldXIcon />
      <AlertTitle>Blocked by policy{phase ? ` · ${phase}` : ""}</AlertTitle>
      <AlertDescription>{reason}</AlertDescription>
    </Alert>
  );
}

interface RetryIndicatorProps {
  source: string;
  attempt: number;
  maxAttempts: number;
  delaySeconds: number;
}

/**
 * Compact line that signals "we hit a transient failure and the server
 * is going to retry." No banner; reads more like a log line.
 */
export function RetryIndicator({
  source,
  attempt,
  maxAttempts,
  delaySeconds,
}: RetryIndicatorProps) {
  return (
    <div className="flex items-center gap-2 text-muted-foreground text-xs">
      <RotateCcwIcon className="size-3" />
      <span>
        Retrying {source} · attempt {attempt}/{maxAttempts}
        {delaySeconds > 0 ? ` · waiting ${delaySeconds.toFixed(1)}s` : ""}
      </span>
    </div>
  );
}

/**
 * Subtle inline marker that the conversation was compacted (older
 * history was summarized to fit context). The in-progress state is
 * rendered as a `Shimmer` in `ChatPage` to match the "Working…"
 * indicator.
 */
export function CompactionMarker() {
  return (
    <div className="flex items-center gap-2 text-muted-foreground text-xs italic">
      <ShrinkIcon className="size-3" />
      <span>Conversation compacted</span>
    </div>
  );
}
