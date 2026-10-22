import { type ReactNode, useEffect } from "react";
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FALLBACK_SERVER_INFO, type ServerInfo } from "@/lib/capabilities";
import { CapabilitiesProvider } from "@/lib/CapabilitiesContext";
import { FLEET_UI_HINT_STORAGE_KEY } from "@/lib/fleetUi";
import { ThemeProvider } from "./ThemeProvider";

const themeState = vi.hoisted(() => ({
  setThemeSource: vi.fn(),
  theme: "system" as string | undefined,
  defaultTheme: undefined as string | undefined,
  mounts: 0,
}));

vi.mock("@/lib/nativeBridge", () => ({
  setThemeSource: themeState.setThemeSource,
}));

vi.mock("next-themes", () => ({
  ThemeProvider: ({ children, defaultTheme }: { children: ReactNode; defaultTheme?: string }) => {
    themeState.defaultTheme = defaultTheme;
    useEffect(() => {
      themeState.mounts += 1;
    }, []);
    return children;
  },
  useTheme: () => ({ theme: themeState.theme }),
}));

beforeEach(() => {
  themeState.setThemeSource.mockClear();
  themeState.theme = "system";
  themeState.defaultTheme = undefined;
  themeState.mounts = 0;
  localStorage.clear();
});

afterEach(cleanup);

describe("ThemeProvider native theme sync", () => {
  it("tells the native shell to follow the system theme by default", () => {
    render(<ThemeProvider>content</ThemeProvider>);
    expect(themeState.setThemeSource).toHaveBeenCalledWith("system");
  });

  it("updates the native shell when the user selects an explicit theme", () => {
    const { rerender } = render(<ThemeProvider>content</ThemeProvider>);
    themeState.setThemeSource.mockClear();

    themeState.theme = "light";
    rerender(<ThemeProvider>content</ThemeProvider>);

    expect(themeState.setThemeSource).toHaveBeenCalledWith("light");
  });
});

describe("ThemeProvider default theme", () => {
  const withInfo = (info: ServerInfo | "loading") => (
    <CapabilitiesProvider info={info}>
      <ThemeProvider>content</ThemeProvider>
    </CapabilitiesProvider>
  );

  it("follows the system theme without the fleet look", () => {
    render(withInfo(FALLBACK_SERVER_INFO));
    expect(themeState.defaultTheme).toBe("system");
    expect(localStorage.getItem(FLEET_UI_HINT_STORAGE_KEY)).toBeNull();
  });

  it("defaults to dark under the fleet look and remembers it for the next boot", () => {
    const { rerender } = render(withInfo("loading"));
    expect(themeState.defaultTheme).toBe("system");
    expect(themeState.mounts).toBe(1);

    rerender(withInfo({ ...FALLBACK_SERVER_INFO, fleet_ui: true }));

    // next-themes reads defaultTheme only on mount, so the provider remounts.
    expect(themeState.defaultTheme).toBe("dark");
    expect(themeState.mounts).toBe(2);
    expect(localStorage.getItem(FLEET_UI_HINT_STORAGE_KEY)).toBe("1");
  });

  it("paints dark from the cached hint and does not remount once the probe agrees", () => {
    localStorage.setItem(FLEET_UI_HINT_STORAGE_KEY, "1");
    const { rerender } = render(withInfo("loading"));
    expect(themeState.defaultTheme).toBe("dark");

    rerender(withInfo({ ...FALLBACK_SERVER_INFO, fleet_ui: true }));

    expect(themeState.mounts).toBe(1);
  });

  it("does not remount when the user already chose a theme", () => {
    localStorage.setItem("web-theme", "light");
    const { rerender } = render(withInfo("loading"));

    rerender(withInfo({ ...FALLBACK_SERVER_INFO, fleet_ui: true }));

    expect(themeState.mounts).toBe(1);
  });
});
