#!/usr/bin/env python3
"""Register Drew's local CLI agent templates with the local Omnigent store."""

from __future__ import annotations

from pathlib import Path

from omnigent.cli import _default_artifact_location, _default_db_uri, _preregister_agent
from omnigent.runtime.agent_cache import AgentCache
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore

ROOT = Path(__file__).resolve().parents[1]
AGENT_SPECS = [
    # The six CLI channels.
    ROOT / "examples/local-cli-agents/claude-code",
    ROOT / "examples/local-cli-agents/codex",
    ROOT / "examples/local-cli-agents/kimi-code",
    ROOT / "examples/local-cli-agents/gemini-desktop",
    ROOT / "examples/local-cli-agents/copilot",
    ROOT / "examples/local-cli-agents/grok-cli",
    # AMS fleet orchestrator (AMS MCP toolbag attached via tools/mcp/ams.yaml).
    ROOT / "examples/local-cli-agents/abot-prime",
    # DRF work-loop operator (AMS toolbag + HA WorkItem bridge terminal).
    ROOT / "examples/local-cli-agents/drf-command",
    # Harness Automaton goal-contract runner (fail-closed deliverable work).
    ROOT / "examples/local-cli-agents/harness-goal",
]


def main() -> None:
    artifact_store = LocalArtifactStore(_default_artifact_location())
    agent_store = SqlAlchemyAgentStore(_default_db_uri())
    agent_cache = AgentCache(artifact_store, Path(_default_artifact_location()) / ".agent_cache")

    for spec in AGENT_SPECS:
        _preregister_agent(spec, agent_store, artifact_store, agent_cache)


if __name__ == "__main__":
    main()
