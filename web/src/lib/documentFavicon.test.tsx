import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useDocumentFavicon } from "./documentFavicon";

function TestFavicon({ brandingFavicon }: { brandingFavicon: string | null }) {
  useDocumentFavicon(brandingFavicon);
  return null;
}

describe("useDocumentFavicon", () => {
  let link: HTMLLinkElement;

  beforeEach(() => {
    link = document.createElement("link");
    link.rel = "icon";
    link.setAttribute("type", "image/x-icon");
    link.setAttribute("href", "https://workspace.example/favicon.ico");
    document.head.appendChild(link);
  });

  afterEach(() => {
    cleanup();
    link.remove();
  });

  it("points the tab icon at the Otto starfish", () => {
    render(<TestFavicon brandingFavicon={null} />);
    expect(link.href).toMatch(/^data:image\/svg\+xml/);
  });

  it("prefers an operator branding favicon when configured", () => {
    render(<TestFavicon brandingFavicon="/v1/branding/logo/favicon" />);
    expect(link.href).toMatch(/\/v1\/branding\/logo\/favicon$/);
  });

  it("restores the host page's icon on unmount", () => {
    const { unmount } = render(<TestFavicon brandingFavicon={null} />);
    unmount();
    expect(link.getAttribute("href")).toBe("https://workspace.example/favicon.ico");
    expect(link.getAttribute("type")).toBe("image/x-icon");
  });
});
