"""ACP launch selection must never substitute a vendor CLI for its adapter."""

from unittest.mock import Mock

import pytest

from omnigent import acp_agents


def test_unknown_agent_has_no_launcher():
    assert acp_agents.acp_launch_argv("unknown") is None
    assert acp_agents.acp_status("unknown") == {
        "key": "unknown",
        "supported": False,
        "ready": False,
        "kind": None,
        "argv": None,
        "reason": "no known ACP mode for this agent",
    }


@pytest.mark.parametrize("key,args", [("opencode", ["acp"]), ("gemini", ["--acp"])])
def test_native_agent_launch_and_status(monkeypatch, key, args):
    monkeypatch.setattr(acp_agents, "resolve_binary", lambda spec: f"/bin/{spec.key}")
    assert acp_agents.acp_launch_argv(key) == [f"/bin/{key}", *args]
    ready = acp_agents.acp_status(key)
    assert ready["ready"] is True
    assert ready["reason"] == ""
    assert ready["kind"] == "native"
    monkeypatch.setattr(acp_agents, "resolve_binary", lambda spec: None)
    missing = acp_agents.acp_status(key)
    assert missing["ready"] is False
    assert missing["reason"] == "agent binary not installed on this host"


def test_native_launcher_without_registry_spec_is_unavailable(monkeypatch):
    monkeypatch.setattr(acp_agents, "SPECS_BY_KEY", {})
    assert acp_agents.acp_launch_argv("opencode") is None


@pytest.mark.parametrize("key,adapter", [("codex", "codex-acp"), ("claude", "claude-agent-acp")])
def test_adapter_must_be_installed_separately(monkeypatch, key, adapter):
    which = Mock(return_value=None)
    monkeypatch.setattr(acp_agents.shutil, "which", which)
    monkeypatch.setattr(acp_agents, "search_path", lambda: "/agent-bin")
    missing = acp_agents.acp_status(key)
    assert missing["supported"] is True
    assert missing["ready"] is False
    assert adapter in missing["reason"]
    assert acp_agents.ACP_LAUNCHERS[key].adapter_source in missing["reason"]
    which.assert_called_once_with(adapter, path="/agent-bin")
    which.return_value = f"/agent-bin/{adapter}"
    assert acp_agents.acp_launch_argv(key) == [f"/agent-bin/{adapter}"]
    assert acp_agents.acp_status(key)["ready"] is True
