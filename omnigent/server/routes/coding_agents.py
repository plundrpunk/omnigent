"""Local coding-agent discovery and ACP endpoints.

Exposes:

``GET /v1/coding-agents``
    Which coding-agent CLIs are installed on **the machine running this
    server**, with the model catalog each one reports.
``GET /v1/coding-agents/acp``
    Which of them can be driven over the Agent Client Protocol, and why the
    rest cannot.
``GET /v1/coding-agents/{key}``
    One agent's record.
``GET /v1/coding-agents/{key}/acp/models``
    The agent's *live* catalog, taken straight from an ACP ``session/new``
    handshake rather than reconstructed by shelling out.

Why this is not part of the AMS bridge (:mod:`omnigent.server.routes.ams`):
``AMS_BASE_URL`` normally points at a remote Automaton Memory System, so a
proxied answer would describe the *remote* host's binaries, not this one.
Local capability is only answerable locally, so discovery runs in-process.

Complements :mod:`omnigent.model_catalog`, which resolves *provider* models
over HTTP. That answers "which models could we call?"; this answers "which
coding agents can we actually launch here, and what will each of them run?"

Route order matters: ``/coding-agents/acp`` is declared before
``/coding-agents/{agent_key}`` so the literal path is not swallowed by the
parameterised one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from omnigent.acp_agents import ACP_LAUNCHERS, acp_launch_argv, acp_status
from omnigent.acp_transport import ACPError, list_models
from omnigent.coding_agent_discovery import (
    SPECS_BY_KEY,
    clear_coding_agent_cache,
    discover_coding_agents,
)
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user


def create_coding_agents_router(
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the coding-agent discovery router.

    :param auth_provider: Auth provider used to identify the requesting user.
        ``None`` in single-user mode.
    :returns: A configured :class:`~fastapi.APIRouter`.
    """
    router = APIRouter()

    @router.get("/coding-agents")
    async def list_coding_agents(
        request: Request,
        refresh: bool = False,
        installed_only: bool = False,
        with_models: bool = True,
    ) -> dict[str, Any]:
        """Scan this host for installed coding agents.

        :param refresh: Drop the model cache and re-probe each CLI.
        :param installed_only: Omit agents that are not present on disk.
        :param with_models: Enumerate models (the slow part; ~2s cold).
        """
        require_user(request, auth_provider)
        if refresh:
            clear_coding_agent_cache()
        payload = await discover_coding_agents(with_models=with_models)
        if installed_only:
            payload["agents"] = [
                agent for agent in payload["agents"] if agent.get("installed")
            ]
        return payload

    @router.get("/coding-agents/acp")
    async def list_acp_agents(request: Request) -> dict[str, Any]:
        """Report ACP readiness for every agent that has a known ACP mode.

        Agents needing an adapter report ``ready: false`` with the reason, so
        a caller can fall back to the native-TUI path instead of failing.
        """
        require_user(request, auth_provider)
        agents = [acp_status(key) for key in ACP_LAUNCHERS]
        return {
            "agents": agents,
            "counts": {
                "supported": len(agents),
                "ready": len([a for a in agents if a["ready"]]),
            },
        }

    @router.get("/coding-agents/{agent_key}")
    async def get_coding_agent(
        agent_key: str, request: Request, refresh: bool = False
    ) -> dict[str, Any]:
        """Return one agent's record, including its full model catalog.

        :param agent_key: Registry key, e.g. ``claude`` or ``opencode``.
        :param refresh: Drop the model cache and re-probe first.
        """
        require_user(request, auth_provider)
        if agent_key not in SPECS_BY_KEY:
            raise HTTPException(
                status_code=404, detail=f"Unknown coding agent: {agent_key}"
            )
        if refresh:
            clear_coding_agent_cache()
        payload = await discover_coding_agents()
        for agent in payload["agents"]:
            if agent.get("key") == agent_key:
                return agent
        raise HTTPException(
            status_code=404, detail=f"Unknown coding agent: {agent_key}"
        )

    @router.get("/coding-agents/{agent_key}/acp/models")
    async def get_acp_models(
        agent_key: str, request: Request, cwd: str | None = None
    ) -> dict[str, Any]:
        """Ask the agent itself, over ACP, which models it can run.

        Spawns the agent, performs ``initialize`` + ``session/new``, reads
        ``models.availableModels`` off the response, and tears the process
        down. Authoritative and self-updating -- no curated list to drift.

        :param agent_key: Registry key of an ACP-capable agent.
        :param cwd: Working directory for the probe session.
        """
        require_user(request, auth_provider)

        status = acp_status(agent_key)
        if not status["supported"]:
            raise HTTPException(
                status_code=404,
                detail=f"No known ACP mode for coding agent: {agent_key}",
            )
        argv = acp_launch_argv(agent_key)
        if argv is None:
            raise HTTPException(status_code=409, detail=str(status["reason"]))

        try:
            session = await list_models(argv, cwd=cwd)
        except ACPError as exc:
            # The agent is installed but would not complete the handshake --
            # usually an auth problem. 502 keeps that distinct from 404/409.
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return {
            "key": agent_key,
            "source": "acp",
            "argv": argv,
            "session_id": session.session_id,
            "current_model_id": session.current_model_id,
            "count": len(session.available_models),
            "models": [
                {
                    "id": m.model_id,
                    "label": m.name,
                    "description": m.description,
                    "source": "acp",
                }
                for m in session.available_models
            ],
        }

    return router