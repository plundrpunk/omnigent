/**
 * Shared frame for AOS pages (System, Fleet, Patterns, Models, Loops,
 * Training). Owns the scroll: the AppShell content area doesn't scroll
 * (the chat page manages its own), so every non-chat page wraps itself
 * in this scrolling viewport.
 */

import type { ReactNode } from "react";

export function PageShell({
  title,
  subtitle,
  actions,
  children,
  wide = false,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <div className="h-full min-h-0 overflow-y-auto">
      <div className={`mx-auto w-full ${wide ? "max-w-7xl" : "max-w-5xl"} px-6 py-6`}>
        <div className="mb-5 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h1 className="text-xl font-semibold">{title}</h1>
            {subtitle && <p className="text-sm text-muted-foreground">{subtitle}</p>}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
        {children}
      </div>
    </div>
  );
}
