import type { ServerInfo } from "./capabilities";
import { useServerInfo } from "./CapabilitiesContext";
import { DEFAULT_APP_NAME } from "./branding";

/** Product name the fleet look uses in place of upstream's default. */
export const FLEET_APP_NAME = "Automaton OS";
/** Fleet (wolf) favicon, served from `web/public` next to upstream's favicon. */
export const FLEET_FAVICON_PATH = "/fleet-favicon.svg";
/** Default color theme for the fleet look; upstream follows the system. */
export const FLEET_DEFAULT_THEME = "dark";
export const UPSTREAM_DEFAULT_THEME = "system";

/** Whether the server selected the fleet look; loading and older servers are off. */
export function isFleetUiEnabled(info: ServerInfo | "loading"): boolean {
  return info !== "loading" && info.fleet_ui === true;
}

/** Whether the fleet look (`OMNIGENT_FLEET_UI`) is active for this server. */
export function useFleetUi(): boolean {
  return isFleetUiEnabled(useServerInfo());
}

/** Built-in product name for the active look, ignoring operator branding. */
export function useProductName(): string {
  return useFleetUi() ? FLEET_APP_NAME : DEFAULT_APP_NAME;
}

/**
 * Document title and favicon for the resolved server info: operator branding
 * wins, then the fleet defaults, else null to keep `index.html`'s values.
 */
export function documentBrandingFor(info: ServerInfo): {
  title: string | null;
  faviconUrl: string | null;
} {
  const fleet = isFleetUiEnabled(info);
  return {
    title: info.branding?.app_name ?? (fleet ? FLEET_APP_NAME : null),
    faviconUrl: info.branding?.logos.favicon ?? (fleet ? FLEET_FAVICON_PATH : null),
  };
}

/** Last resolved fleet setting, so the next boot paints the right theme first. */
export const FLEET_UI_HINT_STORAGE_KEY = "omnigent:fleet-ui";

/** Read the cached fleet setting from the previous boot; storage errors read as off. */
export function readFleetUiHint(): boolean {
  try {
    return localStorage.getItem(FLEET_UI_HINT_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/** Cache the resolved fleet setting for the next boot's first paint. */
export function rememberFleetUi(enabled: boolean): void {
  try {
    if (enabled) localStorage.setItem(FLEET_UI_HINT_STORAGE_KEY, "1");
    else localStorage.removeItem(FLEET_UI_HINT_STORAGE_KEY);
  } catch {
    // localStorage access errors are non-fatal.
  }
}
