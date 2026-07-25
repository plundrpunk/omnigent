"""Which coding agents can be driven over ACP, and how to launch them.

:mod:`omnigent.acp_transport` is agent-agnostic -- it drives whatever argv you
hand it. This module is the small amount of per-agent knowledge that remains
once the protocol does the rest: the subcommand or flag that puts each CLI into
ACP mode.

Contrast with the TUI-native integrations. ``claude_native*.py`` is five files
and ``codex_native*.py`` is six, because each reverse-engineers a terminal. An
ACP agent is one row in the table below.

Verified against the binaries installed on this machine:

===========  ==================  ===============================================
Agent        Launch              Notes
===========  ==================  ===============================================
opencode     ``opencode acp``    Built in; advertises 112 models at session/new.
gemini       ``gemini --acp``    Built in (``--experimental-acp`` is deprecated).
codex        adapter             No native ACP; needs ``codex-acp``.
claude       adapter             No native ACP; needs ``claude-agent-acp``.
===========  ==================  ===============================================

The two adapters are separate binaries that are not assumed to be installed;
:func:`acp_launch_argv` returns ``None`` for them unless the adapter resolves on
PATH, so a caller can degrade to the existing native-TUI path instead.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from omnigent.coding_agent_discovery import SPECS_BY_KEY, resolve_binary, search_path


@dataclass(frozen=True)
class ACPLauncher:
    """How to start one agent in ACP mode."""

    key: str
    #: Arguments appended to the agent binary. Empty when an adapter is used.
    args: tuple[str, ...] = ()
    #: ``native`` (the CLI speaks ACP itself) or ``adapter`` (a wrapper does).
    kind: str = "native"
    #: Adapter binary name, when ``kind == "adapter"``.
    adapter_binary: str = ""
    #: Where to get the adapter, for the error message.
    adapter_source: str = ""
    note: str = ""


ACP_LAUNCHERS: dict[str, ACPLauncher] = {
    "opencode": ACPLauncher(
        key="opencode",
        args=("acp",),
        kind="native",
        note="Built-in ACP server; the richest catalog on this host.",
    ),
    "gemini": ACPLauncher(
        key="gemini",
        args=("--acp",),
        kind="native",
        note="Built-in. --experimental-acp is the deprecated spelling.",
    ),
    "codex": ACPLauncher(
        key="codex",
        kind="adapter",
        adapter_binary="codex-acp",
        adapter_source="github.com/agentclientprotocol/codex-acp",
        note="Codex speaks its own app-server protocol, not ACP.",
    ),
    "claude": ACPLauncher(
        key="claude",
        kind="adapter",
        adapter_binary="claude-agent-acp",
        adapter_source="github.com/agentclientprotocol/claude-agent-acp",
        note="Claude Code has no ACP mode; the adapter provides one.",
    ),
}


def acp_launch_argv(key: str) -> list[str] | None:
    """Return the argv that starts *key* in ACP mode on this machine.

    :param key: A :mod:`omnigent.coding_agent_discovery` registry key.
    :returns: Argv ready for :class:`~omnigent.acp_transport.ACPClient`, or
        ``None`` when the agent is not installed, is not ACP-capable, or needs
        an adapter that is not present.
    """
    launcher = ACP_LAUNCHERS.get(key)
    if launcher is None:
        return None

    if launcher.kind == "adapter":
        adapter = shutil.which(launcher.adapter_binary, path=search_path())
        return [adapter] if adapter else None

    spec = SPECS_BY_KEY.get(key)
    if spec is None:
        return None
    binary = resolve_binary(spec)
    if binary is None:
        return None
    return [binary, *launcher.args]


def acp_status(key: str) -> dict[str, object]:
    """Describe ACP readiness for *key*, including why it is unavailable.

    :param key: A coding-agent registry key.
    :returns: A JSON-shaped record with ``supported``, ``ready``, ``argv`` and
        a human-readable ``reason`` when not ready.
    """
    launcher = ACP_LAUNCHERS.get(key)
    if launcher is None:
        return {
            "key": key,
            "supported": False,
            "ready": False,
            "kind": None,
            "argv": None,
            "reason": "no known ACP mode for this agent",
        }

    argv = acp_launch_argv(key)
    reason = ""
    if argv is None:
        if launcher.kind == "adapter":
            reason = (
                "requires the "
                + launcher.adapter_binary
                + " adapter ("
                + launcher.adapter_source
                + "), which is not on PATH"
            )
        else:
            reason = "agent binary not installed on this host"

    return {
        "key": key,
        "supported": True,
        "ready": argv is not None,
        "kind": launcher.kind,
        "argv": argv,
        "note": launcher.note,
        "reason": reason,
    }