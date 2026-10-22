import { type ReactNode, useEffect, useState } from "react";
import { ThemeProvider as NextThemesProvider, useTheme } from "next-themes";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import {
  FLEET_DEFAULT_THEME,
  isFleetUiEnabled,
  readFleetUiHint,
  rememberFleetUi,
  UPSTREAM_DEFAULT_THEME,
} from "@/lib/fleetUi";
import { setThemeSource } from "@/lib/nativeBridge";

const THEME_STORAGE_KEY = "web-theme";

function hasStoredTheme(): boolean {
  try {
    return localStorage.getItem(THEME_STORAGE_KEY) !== null;
  } catch {
    return false;
  }
}

/**
 * Mirrors the in-app theme selection onto native shell chrome. Renders nothing.
 */
function NativeThemeSync() {
  const { theme } = useTheme();
  useEffect(() => {
    if (theme === "light" || theme === "dark" || theme === "system") {
      setThemeSource(theme);
    }
  }, [theme]);
  return null;
}

/**
 * App-wide theme provider configured for Tailwind's `.dark` class variant.
 *
 * Defaults to system preference (dark under the fleet look,
 * ``OMNIGENT_FLEET_UI``) and stores explicit user selection under
 * an web-specific key so it does not collide with unrelated local apps
 * on the same host.
 *
 * @param children React tree that should inherit theme context.
 * @returns React provider wrapping the app.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const info = useServerInfo();
  const [fleetUiHint] = useState(readFleetUiHint);
  const [storedThemeAtBoot] = useState(hasStoredTheme);
  const fleetUi = info === "loading" ? fleetUiHint : isFleetUiEnabled(info);
  useEffect(() => {
    if (info !== "loading") rememberFleetUi(isFleetUiEnabled(info));
  }, [info]);
  const defaultTheme = fleetUi ? FLEET_DEFAULT_THEME : UPSTREAM_DEFAULT_THEME;
  return (
    <NextThemesProvider
      // next-themes reads defaultTheme only on mount, so remount when the
      // resolved look disagrees with the cached hint and no theme was chosen.
      key={storedThemeAtBoot ? "stored" : defaultTheme}
      attribute="class"
      defaultTheme={defaultTheme}
      enableSystem
      disableTransitionOnChange
      storageKey={THEME_STORAGE_KEY}
    >
      <NativeThemeSync />
      {children}
    </NextThemesProvider>
  );
}
