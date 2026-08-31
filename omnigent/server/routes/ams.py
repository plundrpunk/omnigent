"""GUI → AMS bridge (reads + an explicit write table).

Exposes ``GET /v1/ams/{path}`` — a whitelisted passthrough to the
Automaton Memory System REST API so the web UI can render registry
data (skills, schedules, automata, agents, goals, warden fleet,
observatory executions) without shipping the AMS API key to the
browser.

Writes are an *explicit table* of method + exact path shape — nothing
generic rides through. Models & Assignment (P1/P3.2):

- ``PUT api/v1/llm-providers/role-mappings`` — role → provider edits
- ``PUT api/v1/llm-providers/spawn-defaults`` — fresh-worker defaults
- ``PATCH api/v1/agents/{agent_id}`` — per-agent config (model)
- ``POST api/warden/agents/{agent_id}/directive`` — live reassign
- ``PUT api/warden/agents/{agent_id}/model`` — fleet model assignment

Loops (the ``aos.loop.v1`` builder saves and runs through AMS automata
and schedules — see ``ap-web/src/pages/LoopsPage.tsx``):

- ``POST api/v1/automata/`` — save a loop as an automaton
- ``PUT api/v1/automata/{automaton_id}`` — edit a saved loop
- ``POST api/v1/automata/execute`` — run one now
- ``POST api/v1/schedules`` — put a loop on a cron
- ``PUT api/v1/schedules/{schedule_id}`` — edit that schedule
- ``POST api/v1/schedules/{schedule_id}/{enable,disable,run}``

Note what is *not* here. Automata carry executable code, so only the
create/update/execute trio is reachable: ``/suggest``,
``/convert-procedure``, ``/ab-test``, ``/{id}/version``,
``/{id}/rollback/{n}``, ``/compositions/*`` and every ``DELETE`` stay
off the table until a feature actually needs them. Path parameters are
matched as UUIDs (AMS's own key shape), not as ``.+``.

Every forwarded write is logged (user, method, path). Anything not in
the table answers 403.

Two surfaces deliberately do *not* ride this bridge: the harness goal
contract runner is a first-class Omnigent route
(``POST /v1/goal`` — see ``routes/goal.py``), and session mutations go
through ``routes/sessions.py``. Neither needs an entry here.

Configuration comes from the server environment:

- ``AMS_BASE_URL`` — e.g. ``https://automaton-memory.com`` or
  ``http://127.0.0.1:8000``. Unset ⇒ endpoints answer 503.
- ``AMS_API_KEY`` — sent as ``X-API-Key`` when set.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user

logger = logging.getLogger(__name__)

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
    # Local coding-agent discovery: which CLI agents are installed on the
    # host running AMS, and the model catalog each one reports.
    # Read-only; see app/automaton_os/agent_discovery.py in agent-memory-backend.
    "api/cli/discover",
    "api/cli/models",
    "api/cli/health",
    "api/warden",
    "observatory",
    "health",
)

#: The write table: (HTTP method, exact-path regex). Additions here are
#: deliberate, reviewed acts — never widen a pattern to "save a row".
_AGENT_ID = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
#: AMS keys automata and schedules by UUID (``automaton_id: UUID`` in
#: ``app/api/automata.py``, ``UUID(schedule_id)`` in ``app/api/schedules.py``).
#: Matching the real key shape — not ``[^/]+`` — keeps a stray segment from
#: reaching AMS just because it sat in the right position.
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_ALLOWED_WRITE_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("PUT", re.compile(r"^api/v1/llm-providers/role-mappings$")),
    ("PUT", re.compile(r"^api/v1/llm-providers/spawn-defaults$")),
    ("PATCH", re.compile(rf"^api/v1/agents/{_AGENT_ID}$")),
    ("POST", re.compile(rf"^api/warden/agents/{_AGENT_ID}/directive$")),
    # Fleet model assignment (Models page, P3.2): edits the hand's
    # HAND.toml default_model and restarts its container — the only
    # write path the abot runtime actually reads at startup.
    ("PUT", re.compile(rf"^api/warden/agents/{_AGENT_ID}/model$")),
    # Loops (Loops page): a loop is stored as an AMS automaton and run
    # either on demand or on a schedule. AMS declares create as
    # ``@router.post("/")``, so the trailing slash is the real path;
    # both spellings are accepted here and forwarded verbatim.
    ("POST", re.compile(r"^api/v1/automata/?$")),
    ("PUT", re.compile(rf"^api/v1/automata/{_UUID}$")),
    ("POST", re.compile(r"^api/v1/automata/execute$")),
    # ...and the schedule that drives it. AMS declares create as
    # ``@router.post("")`` — no trailing slash on that one.
    ("POST", re.compile(r"^api/v1/schedules/?$")),
    ("PUT", re.compile(rf"^api/v1/schedules/{_UUID}$")),
    ("POST", re.compile(rf"^api/v1/schedules/{_UUID}/(?:enable|disable|run)$")),
    # Patterns page (Discover tab): hybrid search is the only AMS route
    # that filters by tag (GET /api/v1/memories/ ignores tag params).
    # Read-shaped query behind a POST verb -- it mutates nothing.
    ("POST", re.compile(r"^api/v1/memories/search$")),
)

_TIMEOUT_SECONDS = 20.0


def _base_url() -> str:
    return (os.environ.get("AMS_BASE_URL") or "").rstrip("/")


def _api_key() -> str:
    return os.environ.get("AMS_API_KEY") or ""


def _path_allowed(path: str) -> bool:
    """Whitelist check on a *normalized* path.

    Rejects traversal (`..`), absolute paths, and empty/dot segments so
    `api/v1/skills/../../admin` can't ride a whitelisted prefix past the
    check (K3 review finding).
    """
    if path.startswith("/") or "//" in path or "\\" in path:
        return False
    segments = path.rstrip("/").split("/")  # tolerate one trailing slash
    if any(seg in ("", ".", "..") for seg in segments):
        return False
    return any(path == p or path.startswith(p + "/") for p in _ALLOWED_GET_PREFIXES)


def create_ams_router(
    auth_provider: AuthProvider | None = None,
    permission_store: Any | None = None,
) -> APIRouter:
    """Build the AMS bridge router.

    :param auth_provider: Auth provider used to identify the requesting
        user. ``None`` in single-user mode (endpoint is open).
    :param permission_store: Store providing ``is_admin``. Required in
        multi-user mode for the write table; without it every write is
        refused rather than allowed.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()

    async def _require_admin(request: Request) -> str | None:
        """Writes reach shared AMS state, so they are admin-only.

        The bridge forwards with one server-side service key, so AMS sees the
        server rather than the end user. Every authenticated user editing
        model assignments, agent directives, automata and schedules is not an
        access model. Until the bridge forwards user identity, writes are
        restricted to admins.
        """
        user = require_user(request, auth_provider)
        if auth_provider is None:
            # Single-user mode: the only caller is the operator.
            return user
        if permission_store is None:
            raise HTTPException(
                status_code=403,
                detail=(
                    "AMS writes require an admin check, and no permission "
                    "store is configured on this server."
                ),
            )
        is_admin = await asyncio.to_thread(permission_store.is_admin, user)
        if not is_admin:
            raise HTTPException(
                status_code=403,
                detail="AMS writes are restricted to admin users.",
            )
        return user

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

    def _write_allowed(method: str, path: str) -> bool:
        """Exact-table check; reuses the read guard's normalization rules."""
        if not _path_allowed(path):
            return False
        return any(m == method and rx.match(path) for m, rx in _ALLOWED_WRITE_ROUTES)

    async def _forward_write(method: str, path: str, request: Request) -> Any:
        user = await _require_admin(request)
        base = _base_url()
        if not base:
            raise HTTPException(
                status_code=503,
                detail=(
                    "AMS bridge not configured — set AMS_BASE_URL "
                    "(and AMS_API_KEY) in the server environment."
                ),
            )
        if not _write_allowed(method, path):
            raise HTTPException(
                status_code=403,
                detail=f"{method} not in AMS write table: {path}",
            )
        # Action endpoints (schedule enable/disable/run) take no body, so an
        # empty request forwards as a bodiless one. A body that is present but
        # not JSON is still a 422 — it never reaches AMS half-parsed.
        raw = await request.body()
        body: Any = None
        if raw:
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise HTTPException(status_code=422, detail="request body must be JSON") from None
        logger.info("ams-bridge write: user=%s %s /%s", user or "single-user", method, path)
        headers: dict[str, str] = {}
        key = _api_key()
        if key:
            headers["X-API-Key"] = key
        send_kwargs: dict[str, Any] = {"headers": headers}
        if body is not None:
            send_kwargs["json"] = body
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                resp = await client.request(method, f"{base}/{path}", **send_kwargs)
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

    @router.put("/ams/{path:path}")
    async def ams_put(path: str, request: Request) -> Any:
        """Forward a write-table PUT to the AMS REST API."""
        return await _forward_write("PUT", path, request)

    @router.patch("/ams/{path:path}")
    async def ams_patch(path: str, request: Request) -> Any:
        """Forward a write-table PATCH to the AMS REST API."""
        return await _forward_write("PATCH", path, request)

    @router.post("/ams/{path:path}")
    async def ams_post(path: str, request: Request) -> Any:
        """Forward a write-table POST to the AMS REST API."""
        return await _forward_write("POST", path, request)

    return router


#: Opt-in hard requirement: when truthy, a missing or dead AMS bridge
#: aborts server boot instead of merely logging. Deployments where AOS
#: without AMS is useless (the Mac Studio desktop) set this to "1".
_REQUIRE_AMS_ENV = "OMNIGENT_REQUIRE_AMS"

_startup_logger = logging.getLogger("omnigent.server.ams")


async def check_bridge_at_startup(
    *,
    probe: Any | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Fail-loud AMS bridge check, run once from the server lifespan.

    Without this, a server started with missing/wrong ``AMS_BASE_URL`` /
    ``AMS_API_KEY`` boots silently and every AMS-backed page (Training,
    System, Fleet, Providers) 503s per-request — which is how the
    2026-08-20 "Training data couldn't be loaded" outage stayed invisible
    for days. This check runs at boot and shouts.

    :param probe: Optional async callable ``(base_url, api_key) -> int``
        returning the health status code — injectable for tests. The
        default probes ``GET {AMS_BASE_URL}/health`` with a 5s timeout.
    :param logger: Logger override for tests.
    :returns: ``{"state": "ok" | "unconfigured" | "unreachable" |
        "unhealthy", "base_url": ..., "detail": ...}``.
    :raises RuntimeError: when :data:`_REQUIRE_AMS_ENV` is truthy and the
        state is anything but ``ok``.
    """
    log = logger or _startup_logger
    base = _base_url()
    required = os.environ.get(_REQUIRE_AMS_ENV, "").strip().lower() in ("1", "true", "yes")

    def _shout(level: int, headline: str, detail: str) -> None:
        bar = "=" * 72
        log.log(
            level,
            "\n%s\nAMS BRIDGE: %s\n%s\n%s\n%s",
            bar,
            headline,
            detail,
            "Fix: set AMS_BASE_URL and AMS_API_KEY in the server environment "
            "(LaunchAgent com.drf.omnigent on the Mac; /etc/goal-queue on the VPS) "
            "and restart. Status endpoint: GET /v1/ams/config",
            bar,
        )

    if not base:
        detail = "AMS_BASE_URL is not set; every AMS-backed page will fail with 503."
        _shout(logging.ERROR if required else logging.WARNING, "NOT CONFIGURED", detail)
        if required:
            raise RuntimeError(f"AMS bridge required ({_REQUIRE_AMS_ENV}=1) but not configured")
        return {"state": "unconfigured", "base_url": None, "detail": detail}

    if probe is None:

        async def probe(base_url: str, api_key: str) -> int:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{base_url}/health",
                    headers={"X-API-Key": api_key} if api_key else {},
                )
                return response.status_code

    try:
        status_code = await probe(base, _api_key())
    except Exception as exc:
        detail = f"probe of {base}/health failed: {exc!r}"
        _shout(logging.ERROR, "UNREACHABLE", detail)
        if required:
            raise RuntimeError(f"AMS bridge required but unreachable: {exc!r}") from exc
        return {"state": "unreachable", "base_url": base, "detail": detail}

    if status_code != 200:
        detail = f"{base}/health returned HTTP {status_code}"
        _shout(logging.ERROR, "UNHEALTHY", detail)
        if required:
            raise RuntimeError(f"AMS bridge required but unhealthy: HTTP {status_code}")
        return {"state": "unhealthy", "base_url": base, "detail": detail}

    log.info("AMS bridge OK: %s (api key %s)", base, "set" if _api_key() else "NOT set")
    return {"state": "ok", "base_url": base, "detail": "healthy"}
