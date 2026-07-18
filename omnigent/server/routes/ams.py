"""GUI → AMS bridge (read-only).

Exposes ``GET /v1/ams/{path}`` — a whitelisted passthrough to the
Automaton Memory System REST API so the web UI can render registry
data (skills, schedules, automata, agents, goals, warden fleet,
observatory executions) without shipping the AMS API key to the
browser.

Configuration comes from the server environment:

- ``AMS_BASE_URL`` — e.g. ``https://automaton-memory.com`` or
  ``http://127.0.0.1:8000``. Unset ⇒ endpoints answer 503.
- ``AMS_API_KEY`` — sent as ``X-API-Key`` when set.

Only GET requests to whitelisted path prefixes are forwarded; every
other path answers 403. Mutations are intentionally unsupported —
add specific POST routes deliberately when a feature needs them.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user

#: Path prefixes (relative to the AMS base URL) the proxy will forward.
_ALLOWED_GET_PREFIXES: tuple[str, ...] = (
    "api/v1/skills",
    "api/v1/schedules",
    "api/v1/automata",
    "api/v1/agents",
    "api/v1/goals",
    "api/v1/tasks",
    "api/v1/memories",
    "api/v1/llm-providers",
    "api/v1/mcp-servers",
    "api/warden",
    "observatory",
    "health",
)

_TIMEOUT_SECONDS = 20.0


def _base_url() -> str:
    return (os.environ.get("AMS_BASE_URL") or "").rstrip("/")


def _api_key() -> str:
    return os.environ.get("AMS_API_KEY") or ""


def _path_allowed(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _ALLOWED_GET_PREFIXES)


def create_ams_router(auth_provider: AuthProvider | None = None) -> APIRouter:
    """Build the AMS bridge router.

    :param auth_provider: Auth provider used to identify the requesting
        user. ``None`` in single-user mode (endpoint is open).
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()

    @router.get("/ams/config")
    async def ams_config(request: Request) -> dict[str, Any]:
        """Report whether the bridge is configured (never leaks the key)."""
        require_user(request, auth_provider)
        base = _base_url()
        return {
            "configured": bool(base),
            "base_url": base or None,
            "has_api_key": bool(_api_key()),
        }

    @router.get("/ams/{path:path}")
    async def ams_proxy(path: str, request: Request) -> Any:
        """Forward a whitelisted GET to the AMS REST API."""
        require_user(request, auth_provider)
        base = _base_url()
        if not base:
            raise HTTPException(
                status_code=503,
                detail=(
                    "AMS bridge not configured — set AMS_BASE_URL "
                    "(and AMS_API_KEY) in the server environment."
                ),
            )
        if not _path_allowed(path):
            raise HTTPException(
                status_code=403,
                detail=f"Path not in AMS read whitelist: {path}",
            )
        headers: dict[str, str] = {}
        key = _api_key()
        if key:
            headers["X-API-Key"] = key
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                resp = await client.get(
                    f"{base}/{path}",
                    params=dict(request.query_params),
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"AMS unreachable: {exc}") from exc
        try:
            payload = resp.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=502, detail="AMS returned a non-JSON response"
            ) from exc
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=payload)
        return payload

    return router
