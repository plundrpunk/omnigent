"""Behavior coverage for diagnostic overlay rendering and terminal actions."""

from __future__ import annotations

import json
import subprocess
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from omnigent_ui_sdk import OverlayTarget, RichBlockFormatter
from rich.console import Console, Group

from omnigent.repl import _repl as repl


def _plain(renderable) -> str:
    stream = StringIO()
    Console(file=stream, width=240, color_system=None).print(renderable)
    return stream.getvalue()


@pytest.fixture
def client():
    return SimpleNamespace(
        responses=SimpleNamespace(get=AsyncMock()),
        sessions=SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(labels={})),
            list_items=AsyncMock(return_value=[]),
        ),
    )


@pytest.fixture
def session():
    return SimpleNamespace(current_response_id=None, model="test-model", session_id="conv-main")


def _terminal_items():
    return [
        {"type": "function_call", "call_id": "launch", "name": "sys_terminal_launch"},
        {
            "type": "function_call_output",
            "call_id": "launch",
            "output": json.dumps(
                {
                    "terminal": "bash",
                    "session": "s1",
                    "tmux_socket": "/tmp/test socket",
                }
            ),
        },
    ]


@pytest.fixture
def terminal_target():
    return OverlayTarget(key="terminal::conv-main::bash::s1", label="bash:s1", icon="💻")


@pytest.mark.parametrize("mode", ["new", "response_error", "no_conversation"])
async def test_target_collection_without_conversation_preserves_main(client, mode):
    session = SimpleNamespace(current_response_id=None)
    if mode != "new":
        session.current_response_id = "resp-1"
        client.responses.get.return_value = SimpleNamespace(conversation=None)
    if mode == "response_error":
        client.responses.get.side_effect = ConnectionError("offline")
    targets = await repl._collect_overview_targets(client, session)
    assert [(t.key, t.label) for t in targets] == [("main", "main")]
    client.sessions.list_items.assert_not_awaited()


async def test_target_collection_deduplicates_workers_and_includes_their_terminals(client):
    handle = {
        "kind": "sub_agent",
        "conversation_id": "conv-worker",
        "agent": "coder",
        "title": "implement",
    }
    output = {"type": "function_call_output", "output": json.dumps(handle)}
    items = [
        {"type": "message"},
        {"type": "function_call_output", "output": None},
        {"type": "function_call_output", "output": "not JSON"},
        {"type": "function_call_output", "output": json.dumps({**handle, "agent": None})},
        output,
        output,
        {"type": "function_call_output", "output": json.dumps({**handle, "conversation_id": 7})},
        *_terminal_items(),
    ]

    async def list_items(conv_id, **kwargs):
        return items if conv_id == "conv-main" else _terminal_items()

    client.sessions.list_items.side_effect = list_items
    client.responses.get.return_value = SimpleNamespace(
        conversation=SimpleNamespace(id="conv-main")
    )
    targets = await repl._collect_overview_targets(
        client, SimpleNamespace(current_response_id="resp-main")
    )
    assert [(t.key, t.label) for t in targets] == [
        ("conv-main", "main"),
        ("conv-worker", "coder:implement"),
        ("terminal::conv-main::bash::s1", "bash:s1"),
        ("terminal::conv-worker::bash::s1", "bash:s1"),
    ]
    assert client.sessions.list_items.await_count == 2


async def test_target_collection_retains_resolved_main_on_fetch_failure(
    client, session, monkeypatch
):
    monkeypatch.setattr(
        repl, "_list_all_conversation_items", AsyncMock(side_effect=OSError("offline"))
    )
    targets = await repl._collect_overview_targets(client, session)
    assert [(t.key, t.label) for t in targets] == [("conv-main", "main")]
    client.responses.get.assert_not_awaited()


async def test_debug_overview_renders_labels_logs_and_correlated_tools(client, session, tmp_path):
    client.sessions.get.return_value = SimpleNamespace(labels={"z": "private", "a": "safe"})
    client.sessions.list_items.return_value = [
        {"type": "function_call", "call_id": "call-1", "name": "read", "arguments": "file.txt"},
        {
            "type": "function_call_output",
            "call_id": "call-1",
            "status": "completed",
            "output": "found",
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "inspect"}],
        },
    ]
    logs = {
        key: tmp_path / key
        for key in ("server_log_path", "runner_log_path", "event_log_path", "cli_log_path")
    }
    result = await repl._build_debug_overview(
        OverlayTarget(key="conv-main", label="main"),
        client=client,
        session=session,
        agent_name="test-agent",
        fmt=RichBlockFormatter(),
        **logs,
    )
    text = _plain(result)
    for expected in [
        "Session: main",
        "Session ID: conv-main",
        "Agent: test-agent",
        "Model: test-model",
        "Labels: a=safe, z=private",
        "Messages: 3",
        "type=tool_call_request name=read",
        "type=tool_call_complete name=read",
        "status: completed",
        "found",
        "type=user_message",
        "inspect",
    ]:
        assert expected in text
    for path in logs.values():
        assert str(path) in text
    client.responses.get.assert_not_awaited()


@pytest.mark.parametrize("mode", ["fresh", "resolve_error", "resolved", "subagent", "empty"])
async def test_debug_overview_resolves_selected_session(client, mode):
    session = SimpleNamespace(current_response_id=None, model="model")
    target = OverlayTarget(key="main", label="main")
    if mode in ("resolve_error", "resolved"):
        session.current_response_id = "resp-1"
        client.responses.get.return_value = SimpleNamespace(
            conversation=SimpleNamespace(id="conv-1")
        )
    if mode == "resolve_error":
        client.responses.get.side_effect = ConnectionError("offline")
    if mode == "subagent":
        target = OverlayTarget(key="worker", label="coder:fix")
    if mode == "empty":
        session.session_id = "conv-1"
    text = _plain(
        await repl._build_debug_overview(
            target,
            client=client,
            session=session,
            agent_name="agent",
            fmt=RichBlockFormatter(),
        )
    )
    if mode in ("fresh", "resolve_error"):
        assert "No conversation yet. Send a message to start one." in text
        client.sessions.get.assert_not_awaited()
    else:
        assert "Labels: (none)" in text
        assert "Messages: 0" in text
        assert "(no messages yet)" in text
    if mode == "resolve_error":
        assert "Failed to resolve conversation: ConnectionError: offline" in text
    if mode == "subagent":
        assert "Session: coder:fix" in text
        assert "Agent:" not in text
        client.sessions.get.assert_awaited_once_with("worker")


@pytest.mark.parametrize("failure", ["labels", "items"])
async def test_debug_overview_shows_partial_failure(client, session, monkeypatch, failure):
    if failure == "labels":
        client.sessions.get.side_effect = RuntimeError("metadata unavailable")
    else:
        monkeypatch.setattr(
            repl, "_list_all_conversation_items", AsyncMock(side_effect=OSError("lost"))
        )
    text = _plain(
        await repl._build_debug_overview(
            None,
            client=client,
            session=session,
            agent_name="agent",
            fmt=RichBlockFormatter(),
        )
    )
    if failure == "labels":
        assert "Labels fetch failed: RuntimeError: metadata unavailable" in text
        assert "Messages: 0" in text
    else:
        assert "Failed to fetch conversation items: OSError: lost" in text
        assert "Messages:" not in text


@pytest.mark.parametrize(
    "alive,snapshot,expected",
    [
        (True, "first\n[not markup]\n", "[not markup]"),
        (True, None, "unavailable (tmux capture-pane failed)"),
        (True, " \n", "(empty terminal screen)"),
        (False, None, "launch a new terminal to recover"),
    ],
)
async def test_terminal_overview_reports_liveness_and_screen(
    client, session, terminal_target, monkeypatch, alive, snapshot, expected
):
    client.sessions.list_items.return_value = _terminal_items()
    probe = Mock(return_value=alive)
    capture = Mock(return_value=snapshot)
    monkeypatch.setattr(repl, "_tmux_session_alive", probe)
    monkeypatch.setattr(repl, "_tmux_pane_snapshot", capture)
    text = _plain(
        await repl._build_debug_overview(
            terminal_target,
            client=client,
            session=session,
            agent_name="agent",
            fmt=RichBlockFormatter(),
        )
    )
    assert "Terminal: bash:s1" in text
    assert "Owner conversation: conv-main" in text
    assert f"Status: {'live' if alive else 'dead'}" in text
    assert "tmux -S '/tmp/test socket' attach -t main" in text
    assert expected in " ".join(text.split())
    probe.assert_called_once_with("/tmp/test socket", "main")
    if alive:
        capture.assert_called_once_with("/tmp/test socket", "main")
    else:
        capture.assert_not_called()


@pytest.mark.parametrize("fetch_error", [False, True])
async def test_terminal_overview_missing_history_is_diagnostic(
    client, terminal_target, monkeypatch, fetch_error
):
    if fetch_error:
        monkeypatch.setattr(
            repl, "_list_all_conversation_items", AsyncMock(side_effect=OSError("lost"))
        )
    text = _plain(
        await repl._build_terminal_overview(
            ("conv-main", "bash", "s1"),
            target=terminal_target,
            client=client,
            fmt=RichBlockFormatter(),
        )
    )
    assert "Terminal: bash:s1" in text
    assert (
        "Failed to fetch conversation items: OSError: lost"
        if fetch_error
        else "not found in conversation history"
    ) in text


@pytest.mark.parametrize(
    "item, expected",
    [
        (
            {"type": "function_call", "arguments": "x"},
            ["tool_call_request", "(missing name)", "args: x"],
        ),
        (
            {"type": "function_call_output", "output": "hello\nworld", "status": "failed"},
            ["tool_call_complete", "(missing call_id)", "status: failed", "hello", "world"],
        ),
        ({"type": "function_call_output", "call_id": "missing"}, ["name=(unknown)"]),
        (
            {"type": "reasoning", "content": "think\n\nverify"},
            ["type=reasoning", "think", "verify"],
        ),
        ({"type": "reasoning"}, ["type=reasoning"]),
        ({"type": "new_event"}, ["type=new_event"]),
        ({}, ["type=(unknown)"]),
        (
            {
                "type": "message",
                "role": "assistant",
                "model": "model-a",
                "content": [
                    None,
                    {"type": "image"},
                    {"type": "output_text"},
                    {"type": "output_text", "text": "answer"},
                ],
            },
            ["type=assistant_message", "model=model-a", "answer"],
        ),
        ({"type": "message", "content": "invalid"}, ["type=assistant_message"]),
    ],
)
def test_event_rendering_surfaces_partial_and_unknown_items(item, expected):
    text = _plain(Group(*repl._render_overview_event(3, item, {}, RichBlockFormatter())))
    for fragment in expected:
        assert fragment in text


@pytest.mark.parametrize("kind", ["message", "function_call_output", "reasoning"])
def test_event_rendering_caps_large_previews(kind):
    long = "x" * 400 + "TAIL_MUST_NOT_APPEAR"
    item = {
        "type": kind,
        "output": long,
        "summary": long,
        "content": [{"type": "output_text", "text": long}],
    }
    text = _plain(Group(*repl._render_overview_event(1, item, {}, RichBlockFormatter())))
    assert "TAIL_MUST_NOT_APPEAR" not in text
    assert text.count("x") == 400
    if kind != "reasoning":
        assert "…" in text


@pytest.mark.parametrize("read_only", [False, True])
async def test_terminal_attach_quotes_socket_and_respects_read_only(
    client, terminal_target, monkeypatch, read_only
):
    client.sessions.list_items.return_value = _terminal_items()
    monkeypatch.setenv("TMUX", "/tmp/outer,1,0")
    monkeypatch.setattr(repl, "_tmux_session_alive", lambda *args: True)
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)
    await repl._open_terminal_in_tmux(terminal_target, client=client, read_only=read_only)
    inner = "tmux -S '/tmp/test socket' attach" + (" -r" if read_only else "") + " -t main"
    run.assert_called_once_with(["tmux", "new-window", inner], check=True, timeout=5)


@pytest.mark.parametrize(
    "failure", ["not_terminal", "outside_tmux", "fetch", "closed", "dead", "spawn"]
)
async def test_terminal_attach_failures_do_not_spawn_or_crash(
    client, terminal_target, monkeypatch, capsys, failure
):
    monkeypatch.setenv("TMUX", "/tmp/outer,1,0")
    monkeypatch.setattr(repl, "_tmux_session_alive", lambda *args: failure != "dead")
    client.sessions.list_items.return_value = _terminal_items()
    run = Mock(side_effect=FileNotFoundError("tmux"))
    monkeypatch.setattr(subprocess, "run", run)
    if failure == "not_terminal":
        terminal_target = OverlayTarget(key="main", label="main")
    elif failure == "outside_tmux":
        monkeypatch.delenv("TMUX")
    elif failure == "fetch":
        monkeypatch.setattr(
            repl, "_list_all_conversation_items", AsyncMock(side_effect=OSError("lost"))
        )
    elif failure == "closed":
        client.sessions.list_items.return_value = []
    await repl._open_terminal_in_tmux(terminal_target, client=client, read_only=False)
    stderr = capsys.readouterr().err
    if failure == "spawn":
        run.assert_called_once()
        assert "tmux new-window failed" in stderr
        assert "Run manually:" in stderr
    else:
        run.assert_not_called()
        expected = {
            "not_terminal": "",
            "outside_tmux": "not running inside tmux",
            "fetch": "items fetch failed",
            "closed": "no longer live",
            "dead": "session is gone",
        }
        assert expected[failure] in stderr
