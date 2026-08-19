import { useEffect } from "react";

import ottoFaviconUrl from "@/assets/otto-no-padding.svg?inline";

/**
 * Point the tab favicon at the Otto starfish while mounted; restore the host
 * page's icon on unmount. The embed renders inside a host page (e.g. the
 * Databricks workspace) whose own favicon would otherwise keep showing.
 */
export function useDocumentFavicon(brandingFavicon: string | null): void {
  useEffect(() => {
    const link = document.querySelector<HTMLLinkElement>('head link[rel~="icon"]');
    if (!link) return;
    const prevHref = link.getAttribute("href");
    const prevType = link.getAttribute("type");
    link.removeAttribute("type");
    link.href = brandingFavicon ?? ottoFaviconUrl;
    return () => {
      if (prevType === null) link.removeAttribute("type");
      else link.setAttribute("type", prevType);
      if (prevHref === null) link.removeAttribute("href");
      else link.setAttribute("href", prevHref);
    };
  }, [brandingFavicon]);
}
