"""Agent Client Protocol (ACP) transport.

One stdio JSON-RPC 2.0 client that can drive *any* ACP-speaking coding agent.

Why this exists
---------------
Omnigent's existing native integrations (``claude_native*.py``,
``codex_native*.py``, ``pi_native*.py``) each boot a vendor TUI in a terminal
and hook it: a bridge, a forwarder, a hook and a state module per agent, five
or six files apiece. Every new agent pays that cost again.

ACP (https://agentclientprotocol.com) removes it. The agent is a subprocess
speaking newline-delimited JSON-RPC on stdio, and the protocol already carries
everything the TUI hooks were reverse-engineering — streamed thoughts, tool
calls, permission requests, and the model catalog. One client serves every
compliant agent, so adding one becomes a registry entry rather than a module.

Measured against ``opencode acp`` on this machine: ``session/new`` returns 112
models with display names and the current selection, where the vendor's own
``opencode models`` subcommand reports 50.

Semantics note
--------------
ACP agents are **not** native-TUI harnesses in Omnigent's sense
(:data:`omnigent.harness_aliases.NATIVE_HARNESSES`). Those type into a resident
terminal and mirror a transcript back. An ACP agent is headless and
protocol-driven, so it must not inherit terminal-mirroring or history-replay
suppression. It is closer to an SDK harness that happens to run out-of-process.

Safety
------
Following the hardening Buzz's ACP harness settles on: bounded reads, a hard
timeout on every request, and process-group kill on every exit path so a wedged
agent cannot outlive its session.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Sequence

logger = logging.getLogger(__name__)

#: ACP protocol version this client implements.
PROTOCOL_VERSION = 1

#: Hard ceiling on a single JSON-RPC line. An agent that emits more than this
#: is malfunctioning; we drop the line rather than buffer without bound.
_MAX_LINE_BYTES = 8 * 1024 * 1024

_DEFAULT_REQUEST_TIMEOUT_S = 60.0
_INITIALIZE_TIMEOUT_S = 45.0
_SHUTDOWN_GRACE_S = 5.0


class ACPError(RuntimeError):
    """An ACP-level failure: transport died, timed out, or returned an error."""

    def __init__(self, message: str, *, code: int | None = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


@dataclass
class ACPModel:
    """One model advertised by an ACP agent's ``session/new`` response."""

    model_id: str
    name: str = ""
    description: str = ""

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> ACPModel:
        model_id = raw.get("modelId") or raw.get("id") or ""
        return cls(
            model_id=model_id,
            name=raw.get("name") or model_id,
            description=raw.get("description") or "",
        )


@dataclass
class ACPSession:
    """A live ACP session and the model state the agent reported for it."""

    session_id: str
    current_model_id: str | None = None
    available_models: list[ACPModel] = field(default_factory=list)
    modes: dict[str, Any] | None = None


@dataclass
class ACPSessionUpdate:
    """One streamed ``session/update`` notification."""

    session_id: str
    kind: str
    payload: dict[str, Any]


#: Callback invoked when the agent asks the user to approve a tool call.
#: Receives the raw ``session/request_permission`` params and returns the
#: chosen option id, or ``None`` to cancel. Default policy denies.
PermissionHandler = Callable[[dict[str, Any]], "str | None"]


def deny_all_permissions(params: dict[str, Any]) -> str | None:
    """Default permission policy: refuse everything.

    Callers that want real tool use must pass an explicit handler. Defaulting
    to deny means an unattended session cannot be talked into running commands
    by whatever the agent decides to ask for.
    """
    logger.info(
        "acp permission request denied by default policy: %s",
        json.dumps(params.get("toolCall", {}))[:200],
    )
    return None


class ACPClient:
    """Drives one ACP agent subprocess over stdio.

    Typical use::

        async with ACPClient(["opencode", "acp"], cwd="/repo") as client:
            await client.initialize()
            session = await client.new_session()
            print(len(session.available_models))

    :param argv: Command and arguments that start the agent in ACP mode.
    :param cwd: Working directory for the agent process.
    :param env: Extra environment variables layered over ``os.environ``.
    :param permission_handler: Policy for ``session/request_permission``.
    :param request_timeout: Default per-request timeout in seconds.
    """

    def __init__(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        permission_handler: PermissionHandler | None = None,
        request_timeout: float = _DEFAULT_REQUEST_TIMEOUT_S,
    ) -> None:
        self._argv = list(argv)
        self._cwd = cwd
        self._env_overrides = dict(env or {})
        self._permission_handler = permission_handler or deny_all_permissions
        self._request_timeout = request_timeout

        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._updates: asyncio.Queue[ACPSessionUpdate | None] = asyncio.Queue()
        self._stderr_tail: list[str] = []
        self._agent_capabilities: dict[str, Any] = {}
        self._auth_methods: list[dict[str, Any]] = []

    # -- lifecycle ---------------------------------------------------------
    async def __aenter__(self) -> ACPClient:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def start(self) -> None:
        """Spawn the agent process and begin reading its output."""
        if self._proc is not None:
            return

        env = os.environ.copy()
        # A parent Claude Code session leaks state into child agents.
        env.pop("CLAUDECODE", None)
        env.setdefault("NO_COLOR", "1")
        env.update(self._env_overrides)

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
                env=env,
                # Own process group so we can kill the whole tree, not just
                # the launcher shim most of these CLIs ship with.
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            raise ACPError(f"failed to spawn ACP agent {self._argv[0]!r}: {exc}") from exc

        self._reader_task = asyncio.create_task(self._read_loop())
        asyncio.create_task(self._drain_stderr())

    async def close(self) -> None:
        """Terminate the agent, killing the process group on every path."""
        proc = self._proc
        self._proc = None

        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None

        for future in self._pending.values():
            if not future.done():
                future.set_exception(ACPError("ACP client closed"))
        self._pending.clear()
        await self._updates.put(None)

        if proc is None or proc.returncode is not None:
            return

        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=_SHUTDOWN_GRACE_S)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError, OSError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=_SHUTDOWN_GRACE_S)

    # -- wire --------------------------------------------------------------
    async def _drain_stderr(self) -> None:
        """Keep the last few stderr lines for error messages."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        while True:
            try:
                line = await proc.stderr.readline()
            except (asyncio.CancelledError, ValueError):
                return
            if not line:
                return
            text = line.decode(errors="replace").rstrip()
            if text:
                self._stderr_tail.append(text)
                del self._stderr_tail[:-20]

    async def _read_loop(self) -> None:
        """Dispatch every inbound line: response, notification, or request."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        while True:
            try:
                line = await proc.stdout.readline()
            except ValueError:
                logger.warning("acp line exceeded buffer; skipping")
                continue
            except asyncio.CancelledError:
                raise
            if not line:
                break
            if len(line) > _MAX_LINE_BYTES:
                logger.warning("acp line over %d bytes; dropped", _MAX_LINE_BYTES)
                continue
            text = line.decode(errors="replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                # Agents occasionally emit banner text before the protocol
                # settles; that is noise, not a fault.
                logger.debug("acp non-json line: %s", text[:200])
                continue
            await self._dispatch(message)

        # stdout closed: fail anything still waiting.
        for future in self._pending.values():
            if not future.done():
                future.set_exception(
                    ACPError(
                        "ACP agent exited; stderr: "
                        + " | ".join(self._stderr_tail[-5:])
                    )
                )
        self._pending.clear()
        await self._updates.put(None)

    async def _dispatch(self, message: dict[str, Any]) -> None:
        msg_id = message.get("id")
        method = message.get("method")

        # Response to something we sent.
        if msg_id is not None and method is None:
            future = self._pending.pop(msg_id, None)
            if future and not future.done():
                future.set_result(message)
            return

        # Request from the agent — must be answered or the agent blocks.
        if msg_id is not None and method is not None:
            await self._handle_agent_request(msg_id, method, message.get("params") or {})
            return

        # Notification.
        if method == "session/update":
            params = message.get("params") or {}
            update = params.get("update") or {}
            await self._updates.put(
                ACPSessionUpdate(
                    session_id=params.get("sessionId") or "",
                    kind=update.get("sessionUpdate") or "",
                    payload=update,
                )
            )
        elif method:
            logger.debug("acp notification %s", method)

    async def _handle_agent_request(
        self, msg_id: Any, method: str, params: dict[str, Any]
    ) -> None:
        """Answer an agent-initiated request.

        Only the calls needed to keep a session moving are implemented;
        anything else gets a proper JSON-RPC "method not found" rather than
        silence, which would wedge the agent.
        """
        if method == "session/request_permission":
            option_id = self._permission_handler(params)
            if option_id is None:
                outcome: dict[str, Any] = {"outcome": "cancelled"}
            else:
                outcome = {"outcome": "selected", "optionId": option_id}
            await self._respond(msg_id, {"outcome": outcome})
            return

        # File access is declined: this client advertises no fs capability,
        # so a compliant agent should not ask, and an agent that asks anyway
        # does not get the host filesystem through us.
        if method in ("fs/read_text_file", "fs/write_text_file"):
            await self._respond_error(
                msg_id, -32601, f"client does not provide {method}"
            )
            return

        await self._respond_error(msg_id, -32601, f"method not found: {method}")

    async def _respond(self, msg_id: Any, result: dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "id": msg_id, "result": result})

    async def _respond_error(self, msg_id: Any, code: int, message: str) -> None:
        await self._write(
            {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
        )

    async def _write(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise ACPError("ACP agent is not running")
        try:
            proc.stdin.write((json.dumps(message) + "\n").encode())
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise ACPError(f"ACP agent stdin closed: {exc}") from exc

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and await its result.

        :param method: ACP method name.
        :param params: Method parameters.
        :param timeout: Override the client's default timeout.
        :returns: The ``result`` object.
        :raises ACPError: On timeout, transport failure, or an error response.
        """
        if self._proc is None:
            raise ACPError("ACP agent is not running; call start() first")

        self._next_id += 1
        msg_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future

        await self._write(
            {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
        )

        try:
            message = await asyncio.wait_for(
                future, timeout=timeout or self._request_timeout
            )
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise ACPError(
                f"{method} timed out after {timeout or self._request_timeout}s"
            ) from None

        if "error" in message:
            err = message["error"] or {}
            raise ACPError(
                f"{method}: {err.get('message', 'unknown error')}",
                code=err.get("code"),
                data=err.get("data"),
            )
        return message.get("result") or {}

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        await self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # -- protocol ----------------------------------------------------------
    async def initialize(self) -> dict[str, Any]:
        """Perform the ACP ``initialize`` handshake.

        :returns: The agent's initialize result.
        """
        result = await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {
                    # We deliberately provide no filesystem access; the agent
                    # uses its own tools under its own trust level.
                    "fs": {"readTextFile": False, "writeTextFile": False}
                },
            },
            timeout=_INITIALIZE_TIMEOUT_S,
        )
        self._agent_capabilities = result.get("agentCapabilities") or {}
        self._auth_methods = result.get("authMethods") or []
        return result

    @property
    def agent_capabilities(self) -> dict[str, Any]:
        return dict(self._agent_capabilities)

    @property
    def auth_methods(self) -> list[str]:
        return [m.get("id", "") for m in self._auth_methods]

    async def new_session(
        self,
        *,
        cwd: str | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
    ) -> ACPSession:
        """Create a session and capture the model catalog it advertises.

        This is the call that makes ACP worth adopting: the response carries
        ``models.availableModels``, so the catalog arrives with the session
        instead of being reconstructed by shelling out.

        :param cwd: Working directory for the session; defaults to the
            client's cwd, then the process cwd.
        :param mcp_servers: MCP server definitions to expose to the agent.
        :returns: The live session and its model state.
        """
        result = await self.request(
            "session/new",
            {
                "cwd": cwd or self._cwd or os.getcwd(),
                "mcpServers": mcp_servers or [],
            },
        )
        models = result.get("models") or {}
        return ACPSession(
            session_id=result.get("sessionId") or "",
            current_model_id=models.get("currentModelId"),
            available_models=[
                ACPModel.from_wire(m) for m in (models.get("availableModels") or [])
            ],
            modes=result.get("modes"),
        )

    async def set_model(self, session_id: str, model_id: str) -> None:
        """Switch the session's model."""
        await self.request(
            "session/set_model", {"sessionId": session_id, "modelId": model_id}
        )

    async def prompt(
        self,
        session_id: str,
        text: str,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a user turn and await the stop reason.

        Streamed output arrives separately via :meth:`updates`.

        :returns: The ``session/prompt`` result, including ``stopReason``.
        """
        return await self.request(
            "session/prompt",
            {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": text}],
            },
            timeout=timeout or max(self._request_timeout, 600.0),
        )

    async def cancel(self, session_id: str) -> None:
        """Ask the agent to abandon the in-flight turn."""
        await self.notify("session/cancel", {"sessionId": session_id})

    async def updates(self) -> AsyncIterator[ACPSessionUpdate]:
        """Stream ``session/update`` notifications until the agent exits."""
        while True:
            item = await self._updates.get()
            if item is None:
                return
            yield item


async def list_models(
    argv: Sequence[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> ACPSession:
    """Start an agent, handshake, and return its advertised model catalog.

    A one-shot convenience for discovery: spawn, ``initialize``,
    ``session/new``, tear down.

    :param argv: Command that starts the agent in ACP mode.
    :param cwd: Working directory for the agent.
    :param env: Extra environment variables.
    :returns: The session, including ``available_models``.
    :raises ACPError: If the agent cannot be started or refuses the handshake.
    """
    async with ACPClient(argv, cwd=cwd, env=env) as client:
        await client.initialize()
        return await client.new_session()
