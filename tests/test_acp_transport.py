"""Lifecycle checks for the ACP subprocess transport."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from omnigent.acp_transport import ACPClient


@pytest.mark.asyncio
async def test_start_retains_stderr_drain_until_eof(monkeypatch):
    """Diagnostics must keep draining after start returns, without another spawn."""
    stderr = asyncio.StreamReader()
    proc = SimpleNamespace(stdout=asyncio.StreamReader(), stderr=stderr, returncode=0)
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    client = ACPClient(["fake-acp"])

    await client.start()
    drain = client._stderr_task
    try:
        assert drain is not None
        assert not drain.done()
        await client.start()
        spawn.assert_awaited_once()
        assert client._stderr_task is drain

        stderr.feed_data(b"agent diagnostic\n")
        stderr.feed_eof()
        await asyncio.wait_for(drain, timeout=1)
        assert client._stderr_tail == ["agent diagnostic"]
    finally:
        stderr.feed_eof()
        if drain is not None and not drain.done():
            drain.cancel()
        await client.close()


@pytest.mark.asyncio
async def test_initialize_and_session_commands_preserve_protocol(monkeypatch):
    from omnigent.acp_transport import ACPModel

    client = ACPClient(["agent"], cwd="/repo")
    responses = [
        {"agentCapabilities": {"loadSession": True}, "authMethods": [{"id": "login"}, {}]},
        {
            "sessionId": "s1",
            "models": {
                "currentModelId": "m1",
                "availableModels": [
                    {"modelId": "m1", "name": "One", "description": "Fast"},
                    {"id": "m2"},
                    {},
                ],
            },
            "modes": {"currentModeId": "ask"},
        },
        {},
        {"stopReason": "end_turn"},
    ]
    request = AsyncMock(side_effect=responses)
    monkeypatch.setattr(client, "request", request)
    await client.initialize()
    assert client.agent_capabilities == {"loadSession": True}
    copy = client.agent_capabilities
    copy.clear()
    assert client.agent_capabilities == {"loadSession": True}
    assert client.auth_methods == ["login", ""]
    initialize = request.await_args_list[0]
    assert initialize.args[0] == "initialize"
    assert initialize.args[1]["clientCapabilities"]["fs"] == {
        "readTextFile": False,
        "writeTextFile": False,
    }
    session = await client.new_session(mcp_servers=[{"name": "tools"}])
    assert session.session_id == "s1"
    assert session.current_model_id == "m1"
    assert session.available_models == [
        ACPModel("m1", "One", "Fast"),
        ACPModel("m2", "m2"),
        ACPModel("", ""),
    ]
    assert session.modes == {"currentModeId": "ask"}
    assert request.await_args_list[1].args == (
        "session/new",
        {"cwd": "/repo", "mcpServers": [{"name": "tools"}]},
    )
    await client.set_model("s1", "m2")
    assert request.await_args_list[2].args == (
        "session/set_model",
        {"sessionId": "s1", "modelId": "m2"},
    )
    assert await client.prompt("s1", "hello", timeout=2) == {"stopReason": "end_turn"}
    assert request.await_args_list[3].args == (
        "session/prompt",
        {"sessionId": "s1", "prompt": [{"type": "text", "text": "hello"}]},
    )
    assert request.await_args_list[3].kwargs == {"timeout": 2}


@pytest.mark.asyncio
async def test_request_dispatches_result_errors_and_timeout(monkeypatch):
    from omnigent.acp_transport import ACPError

    client = ACPClient(["agent"], request_timeout=0.01)
    with pytest.raises(ACPError, match="call start"):
        await client.request("initialize")
    client._proc = SimpleNamespace(returncode=0)
    write = AsyncMock()
    monkeypatch.setattr(client, "_write", write)
    task = asyncio.create_task(client.request("echo", {"value": 1}))
    await asyncio.sleep(0)
    sent = write.await_args.args[0]
    assert sent == {"jsonrpc": "2.0", "id": 1, "method": "echo", "params": {"value": 1}}
    await client._dispatch({"id": 1, "result": {"value": 1}})
    assert await task == {"value": 1}
    task = asyncio.create_task(client.request("bad"))
    await asyncio.sleep(0)
    await client._dispatch(
        {"id": 2, "error": {"code": -1, "message": "denied", "data": {"why": "auth"}}}
    )
    with pytest.raises(ACPError, match="bad: denied") as error:
        await task
    assert error.value.code == -1
    assert error.value.data == {"why": "auth"}
    with pytest.raises(ACPError, match="slow timed out"):
        await client.request("slow")
    assert client._pending == {}
    await client._dispatch({"id": 3, "result": {}})  # late timed-out response
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("option", [None, "allow-once"])
async def test_permission_request_returns_explicit_outcome(monkeypatch, option):
    client = ACPClient(["agent"], permission_handler=(lambda params: option) if option else None)
    write = AsyncMock()
    monkeypatch.setattr(client, "_write", write)
    await client._dispatch(
        {
            "id": 7,
            "method": "session/request_permission",
            "params": {"toolCall": {"title": "command"}},
        }
    )
    outcome = (
        {"outcome": "cancelled"} if option is None else {"outcome": "selected", "optionId": option}
    )
    write.assert_awaited_once_with({"jsonrpc": "2.0", "id": 7, "result": {"outcome": outcome}})


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["fs/read_text_file", "fs/write_text_file", "unknown/method"])
async def test_unsupported_agent_requests_are_answered_without_host_access(monkeypatch, method):
    client = ACPClient(["agent"])
    write = AsyncMock()
    monkeypatch.setattr(client, "_write", write)
    await client._dispatch({"id": "r1", "method": method, "params": {"path": "/private"}})
    response = write.await_args.args[0]
    assert response["id"] == "r1"
    assert response["error"]["code"] == -32601
    assert method in response["error"]["message"]


@pytest.mark.asyncio
async def test_notifications_stream_updates_and_cancel_without_request_id(monkeypatch):
    client = ACPClient(["agent"])
    write = AsyncMock()
    monkeypatch.setattr(client, "_write", write)
    await client.cancel("s1")
    write.assert_awaited_once_with(
        {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": "s1"}}
    )
    await client._dispatch(
        {
            "method": "session/update",
            "params": {
                "sessionId": "s1",
                "update": {"sessionUpdate": "agent_message_chunk", "content": {"text": "hello"}},
            },
        }
    )
    await client._dispatch({"method": "ignored"})
    await client.close()
    updates = [update async for update in client.updates()]
    assert len(updates) == 1
    assert updates[0].session_id == "s1"
    assert updates[0].kind == "agent_message_chunk"
    assert updates[0].payload["content"] == {"text": "hello"}


@pytest.mark.asyncio
async def test_write_encodes_newline_json_and_reports_closed_pipe():
    import json
    from unittest.mock import Mock

    from omnigent.acp_transport import ACPError

    client = ACPClient(["agent"])
    with pytest.raises(ACPError, match="not running"):
        await client.notify("ping")
    stdin = SimpleNamespace(write=Mock(), drain=AsyncMock())
    client._proc = SimpleNamespace(stdin=stdin, returncode=0)
    await client.notify("ping")
    encoded = stdin.write.call_args.args[0]
    assert encoded.endswith(b"\n")
    assert json.loads(encoded) == {"jsonrpc": "2.0", "method": "ping", "params": {}}
    stdin.drain.side_effect = BrokenPipeError("closed")
    with pytest.raises(ACPError, match="stdin closed"):
        await client.notify("ping")
    await client.close()


@pytest.mark.asyncio
async def test_reader_ignores_banner_and_oversized_line_then_fails_pending_on_eof(monkeypatch):
    from omnigent import acp_transport as transport

    client = ACPClient(["agent"])
    stdout = SimpleNamespace(
        readline=AsyncMock(
            side_effect=[
                ValueError("buffer"),
                b"x" * 81,
                b" \n",
                b"banner\n",
                b'{"id": 1, "result": {"ok": true}}\n',
                b"",
            ]
        )
    )
    client._proc = SimpleNamespace(stdout=stdout, returncode=0)
    monkeypatch.setattr(transport, "_MAX_LINE_BYTES", 80)
    success = asyncio.get_running_loop().create_future()
    failed = asyncio.get_running_loop().create_future()
    client._pending = {1: success, 2: failed}
    client._stderr_tail = ["useful diagnostic"]
    await client._read_loop()
    assert success.result()["result"] == {"ok": True}
    with pytest.raises(transport.ACPError, match="useful diagnostic"):
        await failed
    assert client._pending == {}
    assert [update async for update in client.updates()] == []
    await client.close()


@pytest.mark.asyncio
async def test_stderr_tail_is_bounded_and_replaces_invalid_bytes():
    client = ACPClient(["agent"])
    stderr = asyncio.StreamReader()
    stderr.feed_data(b"\n" + b"\n".join(str(i).encode() for i in range(25)) + b"\nlast\xff\n")
    stderr.feed_eof()
    client._proc = SimpleNamespace(stderr=stderr, returncode=0)
    await client._drain_stderr()
    assert len(client._stderr_tail) == 20
    assert client._stderr_tail[0] == "6"
    assert client._stderr_tail[-1] == "last\ufffd"
    await client.close()


@pytest.mark.asyncio
async def test_spawn_error_includes_agent_name(monkeypatch):
    from omnigent.acp_transport import ACPError

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("missing"))
    )
    with pytest.raises(ACPError, match="failed to spawn ACP agent 'missing-agent'"):
        await ACPClient(["missing-agent"]).start()


@pytest.mark.asyncio
async def test_close_escalates_to_process_group_kill_and_fails_pending(monkeypatch):
    import signal
    from unittest.mock import Mock

    from omnigent import acp_transport as transport

    client = ACPClient(["agent"])
    proc = SimpleNamespace(
        pid=123, returncode=None, wait=AsyncMock(side_effect=[TimeoutError, None])
    )
    client._proc = proc
    future = asyncio.get_running_loop().create_future()
    client._pending[1] = future
    kill = Mock()
    monkeypatch.setattr(transport.os, "getpgid", lambda pid: 456)
    monkeypatch.setattr(transport.os, "killpg", kill)
    await client.close()
    assert [call.args for call in kill.call_args_list] == [
        (456, signal.SIGTERM),
        (456, signal.SIGKILL),
    ]
    with pytest.raises(transport.ACPError, match="client closed"):
        await future
    assert client._proc is None
    assert client._pending == {}


@pytest.mark.asyncio
async def test_list_models_closes_client_when_handshake_fails(monkeypatch):
    from omnigent import acp_transport as transport

    start = AsyncMock()
    close = AsyncMock()
    initialize = AsyncMock(side_effect=transport.ACPError("auth required"))
    monkeypatch.setattr(transport.ACPClient, "start", start)
    monkeypatch.setattr(transport.ACPClient, "close", close)
    monkeypatch.setattr(transport.ACPClient, "initialize", initialize)
    with pytest.raises(transport.ACPError, match="auth required"):
        await transport.list_models(["agent"], cwd="/repo")
    start.assert_awaited_once()
    close.assert_awaited_once()
    initialize.side_effect = None
    session = transport.ACPSession("s1")
    monkeypatch.setattr(transport.ACPClient, "new_session", AsyncMock(return_value=session))
    assert await transport.list_models(["agent"]) is session
    assert close.await_count == 2


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="ACPClient.request leaks its pending future when writing to agent stdin fails",
)
async def test_failed_request_write_releases_pending_future(monkeypatch):
    from omnigent.acp_transport import ACPError

    client = ACPClient(["agent"])
    client._proc = SimpleNamespace(returncode=0)
    monkeypatch.setattr(client, "_write", AsyncMock(side_effect=ACPError("stdin closed")))
    try:
        with pytest.raises(ACPError, match="stdin closed"):
            await client.request("session/prompt", {"sessionId": "s1"})
        assert client._pending == {}
    finally:
        # Consume close errors from the leaked futures so the regression test
        # does not leave unrelated unhandled-future warnings behind.
        pending = list(client._pending.values())
        await client.close()
        for future in pending:
            if future.done() and not future.cancelled():
                future.exception()
