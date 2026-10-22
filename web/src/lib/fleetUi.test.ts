import { afterEach, describe, expect, it } from "vitest";
import { FALLBACK_SERVER_INFO } from "./capabilities";
import {
  documentBrandingFor,
  FLEET_APP_NAME,
  FLEET_FAVICON_PATH,
  FLEET_UI_HINT_STORAGE_KEY,
  isFleetUiEnabled,
  readFleetUiHint,
  rememberFleetUi,
} from "./fleetUi";

const branding = {
  app_name: "Acme",
  heading: null,
  logos: { main: null, loading: null, favicon: "/v1/branding/logo/favicon" },
  powered_by: true,
};

afterEach(() => localStorage.clear());

describe("isFleetUiEnabled", () => {
  it("is off while loading and for servers that omit or disable it", () => {
    expect(isFleetUiEnabled("loading")).toBe(false);
    expect(isFleetUiEnabled(FALLBACK_SERVER_INFO)).toBe(false);
    expect(isFleetUiEnabled({ ...FALLBACK_SERVER_INFO, fleet_ui: false })).toBe(false);
  });

  it("is on only when the server advertises fleet_ui", () => {
    expect(isFleetUiEnabled({ ...FALLBACK_SERVER_INFO, fleet_ui: true })).toBe(true);
  });
});

describe("documentBrandingFor", () => {
  it("leaves index.html's title and favicon alone for upstream's look", () => {
    expect(documentBrandingFor(FALLBACK_SERVER_INFO)).toEqual({ title: null, faviconUrl: null });
  });

  it("uses the fleet name and favicon under the fleet look", () => {
    expect(documentBrandingFor({ ...FALLBACK_SERVER_INFO, fleet_ui: true })).toEqual({
      title: FLEET_APP_NAME,
      faviconUrl: FLEET_FAVICON_PATH,
    });
  });

  it("lets operator branding win over the fleet defaults", () => {
    expect(documentBrandingFor({ ...FALLBACK_SERVER_INFO, fleet_ui: true, branding })).toEqual({
      title: "Acme",
      faviconUrl: "/v1/branding/logo/favicon",
    });
  });
});

describe("fleet UI boot hint", () => {
  it("round-trips the last resolved setting", () => {
    expect(readFleetUiHint()).toBe(false);
    rememberFleetUi(true);
    expect(localStorage.getItem(FLEET_UI_HINT_STORAGE_KEY)).toBe("1");
    expect(readFleetUiHint()).toBe(true);
    rememberFleetUi(false);
    expect(localStorage.getItem(FLEET_UI_HINT_STORAGE_KEY)).toBeNull();
    expect(readFleetUiHint()).toBe(false);
  });
});
