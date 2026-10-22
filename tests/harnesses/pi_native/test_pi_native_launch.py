"""Pi launch orchestration with in-process HTTP and mocked daemon boundaries."""

import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import click
import httpx
import pytest

from omnigent.harnesses.pi_native import main as pi


@pytest.fixture
def http_client():
    def make(status=200, payload=None):
        response = httpx.Response(status, json={} if payload is None else payload)
        return SimpleNamespace(
            get=AsyncMock(return_value=response),
            post=AsyncMock(return_value=response),
            patch=AsyncMock(return_value=response),
        )

    return make


@pytest.mark.parametrize(
    "env,command",
    [
        ({}, "pi"),
        ({"HARNESS_PI_PATH": " legacy "}, "legacy"),
        ({"OMNIGENT_PI_PATH": " custom ", "HARNESS_PI_PATH": "legacy"}, "custom"),
    ],
)
def test_launch_resolves_configuration_without_losing_arguments(env, command):
    which = Mock(return_value="/bin/pi")
    assert pi.build_pi_launch(["--model", "test"], env=env, which=which) == pi.NativePiLaunch(
        "/bin/pi", ["/bin/pi", "--model", "test"]
    )
    which.assert_called_once_with(command)


def test_launch_missing_binary_explains_override(monkeypatch):
    # Isolate from a Pi CLI installed in a well-known location on this machine.
    monkeypatch.setattr(pi, "resolve_cli_binary", lambda command, which: None)
    with pytest.raises(click.ClickException, match="OMNIGENT_PI_PATH"):
        pi.resolve_pi_executable(env={}, which=lambda command: None)


def test_run_native_requires_server_and_preflights_tmux(monkeypatch):
    monkeypatch.setattr(pi.shutil, "which", lambda command: None)
    with pytest.raises(click.ClickException, match="tmux was not found"):
        pi.run_pi_native(server="http://server", session_id=None, pi_args=())
    monkeypatch.setattr(pi.shutil, "which", lambda command: "/bin/tmux")
    with pytest.raises(click.ClickException, match="resolved Omnigent server URL"):
        pi.run_pi_native(server=None, session_id=None, pi_args=())


def test_run_native_materializes_spec_and_normalizes_url(monkeypatch):
    monkeypatch.setattr(pi.shutil, "which", lambda command: "/bin/tmux")
    captured = {}

    def remote(base_url, spec_path, **kwargs):
        captured.update(base_url=base_url, spec=spec_path.read_text(), kwargs=kwargs)

    monkeypatch.setattr(pi, "_run_with_remote_server", remote)
    pi.run_pi_native(
        server="http://server/",
        session_id="s1",
        pi_args=("--model", "test"),
        resume_picker=True,
        auto_open_conversation=True,
    )
    assert captured["base_url"] == "http://server"
    assert "pi-native" in captured["spec"]
    assert captured["kwargs"] == {
        "session_id": "s1",
        "pi_args": ("--model", "test"),
        "resume_picker": True,
        "auto_open_conversation": True,
    }


@pytest.mark.asyncio
async def test_create_session_uploads_bundle_and_persists_launch_metadata(http_client):
    client = http_client(payload={"session_id": "s1"})
    assert (
        await pi._create_pi_session(client, b"archive", terminal_launch_args=["--model", "test"])
        == "s1"
    )
    call = client.post.await_args
    assert call.args == ("/v1/sessions",)
    assert json.loads(call.kwargs["data"]["metadata"]) == {
        "labels": pi._SESSION_LABELS,
        "terminal_launch_args": ["--model", "test"],
    }
    assert call.kwargs["files"] == {
        "bundle": ("pi-native-ui.tar.gz", b"archive", "application/gzip")
    }
    assert call.kwargs["timeout"] == 120.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,payload,message",
    [
        (500, {"detail": "bad"}, "creation failed"),
        (200, {}, "did not include session_id"),
        (200, {"session_id": 3}, "did not include session_id"),
    ],
)
async def test_create_session_rejects_failed_or_malformed_response(
    http_client, status, payload, message
):
    with pytest.raises(click.ClickException, match=message):
        await pi._create_pi_session(http_client(status, payload), b"archive")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,payload,message",
    [(404, {}, "not found"), (500, {}, "Failed to fetch"), (200, [], "non-object JSON")],
)
async def test_fetch_session_reports_response_failures(http_client, status, payload, message):
    with pytest.raises(click.ClickException, match=message):
        await pi._fetch_pi_session(http_client(status, payload), "s1")


@pytest.mark.asyncio
async def test_fetch_and_ensure_escape_session_id_and_set_native_terminal(http_client):
    client = http_client(payload={"id": "s/1"})
    assert await pi._fetch_pi_session(client, "s/1") == {"id": "s/1"}
    client.get.assert_awaited_once_with("/v1/sessions/s%2F1")
    await pi._ensure_pi_terminal_on_runner(client, "s/1")
    client.post.assert_awaited_once_with(
        "/v1/sessions/s%2F1/resources/terminals",
        json={"terminal": "pi", "session_key": "main", "ensure_native_terminal": True},
        timeout=60.0,
    )
    client.post.return_value = httpx.Response(500, json={"detail": "offline"})
    with pytest.raises(click.ClickException, match="terminal ensure failed"):
        await pi._ensure_pi_terminal_on_runner(client, "s1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,payload",
    [
        (404, {}),
        (409, {"detail": "not bound to a runner"}),
        (503, {"detail": "runner offline"}),
        (200, {"id": "term", "metadata": {"running": False}}),
    ],
)
async def test_find_terminal_treats_unavailable_runner_as_not_ready(http_client, status, payload):
    assert await pi._find_running_pi_terminal(http_client(status, payload), "s1") is None


@pytest.mark.asyncio
async def test_find_terminal_rejects_other_errors_and_decodes_ready_metadata(http_client):
    with pytest.raises(click.ClickException, match="Failed to fetch Pi terminal"):
        await pi._find_running_pi_terminal(http_client(403, {"detail": "forbidden"}), "s1")
    result = await pi._find_running_pi_terminal(
        http_client(
            payload={
                "id": "term",
                "metadata": {"tmux_socket": "/tmp/pi.sock", "tmux_target": "pi:0"},
            }
        ),
        "s1",
    )
    assert result == pi.LaunchedPiTerminal("term", Path("/tmp/pi.sock"), "pi:0")


@pytest.mark.parametrize(
    "payload,message", [([], "non-object"), ({}, "terminal id"), ({"id": 1}, "terminal id")]
)
def test_terminal_metadata_requires_object_and_string_id(payload, message):
    with pytest.raises(click.ClickException, match=message):
        pi._launched_pi_terminal_from_payload(payload)


def test_optional_terminal_metadata_ignores_wrong_types():
    assert pi._launched_pi_terminal_from_payload(
        {"id": "term", "metadata": {"tmux_socket": 1, "tmux_target": []}}
    ) == pi.LaunchedPiTerminal("term", None, None)


@pytest.mark.asyncio
async def test_wait_for_terminal_retries_until_ready_and_times_out(monkeypatch):
    terminal = pi.LaunchedPiTerminal("term", None, None)
    find = AsyncMock(side_effect=[None, terminal])
    monkeypatch.setattr(pi, "_find_running_pi_terminal", find)
    sleep = AsyncMock()
    monkeypatch.setattr(pi.asyncio, "sleep", sleep)
    assert await pi._wait_for_pi_terminal_ready(object(), "s1", timeout_s=1) == terminal
    sleep.assert_awaited_once_with(0.2)
    with pytest.raises(click.ClickException, match="did not create the Pi terminal"):
        await pi._wait_for_pi_terminal_ready(object(), "s1", timeout_s=0)


@pytest.fixture
def daemon(monkeypatch):
    client = AsyncMock()
    client.patch.return_value = httpx.Response(200, json={})
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(pi.httpx, "AsyncClient", Mock(return_value=context))
    boundaries = {}
    for name in (
        "wait_for_host_online",
        "launch_or_reuse_daemon_runner",
        "wait_for_runner_online",
        "_bind_session_runner",
        "_ensure_pi_terminal_on_runner",
        "_wait_for_pi_terminal_ready",
        "_create_pi_session",
        "_fetch_pi_session",
        "_find_running_pi_terminal",
    ):
        boundaries[name] = AsyncMock()
        monkeypatch.setattr(pi, name, boundaries[name])
    boundaries["_create_pi_session"].return_value = "new-session"
    boundaries["_fetch_pi_session"].return_value = {"labels": pi._SESSION_LABELS}
    boundaries["_find_running_pi_terminal"].return_value = None
    boundaries["launch_or_reuse_daemon_runner"].return_value = "runner-1"
    boundaries["_wait_for_pi_terminal_ready"].return_value = pi.LaunchedPiTerminal(
        "term", Path("/tmp/pi.sock"), "pi:0"
    )
    return client, boundaries


async def prepare(**kwargs):
    arguments = {
        "base_url": "http://server",
        "headers": {"Authorization": "fixture"},
        "session_id": None,
        "session_bundle": b"bundle",
        "pi_args": ("--model", "test"),
        "host_id": "host-1",
        "workspace": "/repo",
    }
    arguments.update(kwargs)
    return await pi._prepare_pi_terminal_via_daemon(**arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize("session_id", [None, "existing"])
async def test_prepare_creates_or_resumes_then_binds_and_ensures_terminal(daemon, session_id):
    client, boundaries = daemon
    progress = Mock()
    result = await prepare(session_id=session_id, startup_progress=progress)
    expected_id = session_id or "new-session"
    assert result == pi.PreparedPiTerminal(
        expected_id, "term", Path("/tmp/pi.sock"), "pi:0", session_id is not None
    )
    if session_id:
        client.patch.assert_awaited_once_with(
            "/v1/sessions/existing", json={"terminal_launch_args": ["--model", "test"]}
        )
        boundaries["_create_pi_session"].assert_not_awaited()
    else:
        boundaries["_create_pi_session"].assert_awaited_once_with(
            client, b"bundle", terminal_launch_args=["--model", "test"]
        )
    boundaries["launch_or_reuse_daemon_runner"].assert_awaited_once_with(
        client,
        host_id="host-1",
        session_id=expected_id,
        workspace="/repo",
        fresh=session_id is None,
    )
    boundaries["_bind_session_runner"].assert_awaited_once_with(client, expected_id, "runner-1")
    boundaries["_ensure_pi_terminal_on_runner"].assert_awaited_once_with(client, expected_id)
    assert progress.update.call_args.args == ("Pi terminal ready.",)


@pytest.mark.asyncio
async def test_prepare_reattaches_without_restarting_or_overwriting_arguments(daemon, capsys):
    client, boundaries = daemon
    boundaries["_find_running_pi_terminal"].return_value = pi.LaunchedPiTerminal(
        "existing-term", Path("/tmp/socket"), "pi"
    )
    result = await prepare(session_id="existing")
    assert result.reattached is True
    assert result.terminal_id == "existing-term"
    assert "Ignoring Pi launch args" in capsys.readouterr().err
    client.patch.assert_not_awaited()
    boundaries["launch_or_reuse_daemon_runner"].assert_not_awaited()


@pytest.mark.asyncio
async def test_prepare_requires_bundle_for_new_session(daemon):
    with pytest.raises(click.ClickException, match="requires a session bundle"):
        await prepare(session_bundle=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"labels": {"other": "wrapper"}}, []])
async def test_prepare_rejects_non_pi_session(daemon, payload):
    _client, boundaries = daemon
    boundaries["_fetch_pi_session"].return_value = payload
    with pytest.raises(click.ClickException, match="not a pi-native session"):
        await prepare(session_id="existing")
    boundaries["launch_or_reuse_daemon_runner"].assert_not_awaited()


@pytest.mark.asyncio
async def test_prepare_aborts_on_launch_config_update_error(daemon):
    client, boundaries = daemon
    client.patch.return_value = httpx.Response(500, json={"detail": "write failed"})
    with pytest.raises(click.ClickException, match="launch config update failed"):
        await prepare(session_id="existing")
    boundaries["launch_or_reuse_daemon_runner"].assert_not_awaited()


@pytest.mark.asyncio
async def test_attach_checks_metadata_socket_and_tmux_before_spawning(tmp_path, monkeypatch):
    socket = tmp_path / "socket"
    prepared = pi.PreparedPiTerminal("s1", "term", socket, "pi:0", False)
    assert "socket path" in pi._direct_tmux_unavailable_reason(replace(prepared, tmux_socket=None))
    assert "tmux target" in pi._direct_tmux_unavailable_reason(replace(prepared, tmux_target=None))
    assert "not reachable" in pi._direct_tmux_unavailable_reason(prepared)
    with pytest.raises(click.ClickException, match="requires direct tmux attach"):
        await pi._attach_terminal_resource(prepared)
    socket.touch()
    monkeypatch.setattr(pi.shutil, "which", lambda command: None)
    assert "not available on PATH" in pi._direct_tmux_unavailable_reason(prepared)
    monkeypatch.setattr(pi.shutil, "which", lambda command: "/bin/tmux")
    assert pi._direct_tmux_unavailable_reason(prepared) is None
    attach = AsyncMock()
    monkeypatch.setattr(pi, "_attach_direct_tmux", attach)
    await pi._attach_terminal_resource(prepared)
    attach.assert_awaited_once_with(socket, "pi:0")


@pytest.mark.asyncio
async def test_direct_attach_removes_parent_tmux_environment(monkeypatch):
    monkeypatch.setenv("TMUX", "parent-session")
    process = SimpleNamespace(wait=AsyncMock(return_value=0))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(pi.asyncio, "create_subprocess_exec", spawn)
    await pi._attach_direct_tmux(Path("/tmp/socket"), "pi:0")
    assert spawn.call_args.args == (
        "tmux",
        "-S",
        "/tmp/socket",
        "-f",
        os.devnull,
        "attach",
        "-t",
        "pi:0",
    )
    assert "TMUX" not in spawn.call_args.kwargs["env"]
    process.wait.assert_awaited_once_with()


def test_resume_uses_explicit_session_before_picker_and_new_session_default():
    arguments = {
        "base_url": "http://server",
        "headers": {},
        "session_id": "s1",
        "resume_picker": True,
    }
    assert pi._resolve_session_id_for_resume(**arguments) == "s1"
    arguments.update(session_id=None, resume_picker=False)
    assert pi._resolve_session_id_for_resume(**arguments) is None


def test_resume_picker_filters_sdk_conversations_to_pi(monkeypatch):
    import omnigent_client

    from omnigent.repl import _resume_picker

    context = AsyncMock()
    client = object()
    context.__aenter__.return_value = client
    factory = Mock(return_value=context)
    picker = AsyncMock(return_value="picked")
    monkeypatch.setattr(omnigent_client, "OmnigentClient", factory)
    monkeypatch.setattr(_resume_picker, "pick_conversation_by_wrapper_label_from_sdk", picker)
    assert (
        pi._resolve_session_id_for_resume(
            base_url="http://server", headers={}, session_id=None, resume_picker=True
        )
        == "picked"
    )
    factory.assert_called_once_with(base_url="http://server", headers=None)
    picker.assert_awaited_once()
    args, kwargs = picker.await_args
    assert args == (client,)
    # Upstream also scopes the picker to this machine's host id.
    assert kwargs["wrapper_value"] == pi._WRAPPER_LABEL_VALUE
    assert kwargs["agent_name"] == "pi-native-ui"
    assert "host_id" in kwargs


def test_bridge_directory_uses_shared_session_mapping(monkeypatch):
    bridge = Mock(return_value=Path("/tmp/bridge"))
    monkeypatch.setattr(pi, "bridge_dir_for_session_id", bridge)
    assert pi.pi_bridge_dir_for_session("s1") == Path("/tmp/bridge")
    bridge.assert_called_once_with("s1")


@pytest.fixture
def remote_launch(monkeypatch):
    from contextlib import nullcontext

    from omnigent import chat, cli
    from omnigent.host import identity

    monkeypatch.setattr(chat, "_remote_headers", Mock(return_value={"Authorization": "fixture"}))
    bundle = Mock(return_value=b"bundle")
    monkeypatch.setattr(chat, "_bundle_agent", bundle)
    ensure = Mock()
    monkeypatch.setattr(cli, "_ensure_host_daemon", ensure)
    monkeypatch.setattr(
        identity, "load_or_create_host_identity", lambda: SimpleNamespace(host_id="host-1")
    )
    progress = Mock()
    monkeypatch.setattr(pi, "runner_startup_progress", lambda **kwargs: nullcontext(progress))
    prepare_terminal = AsyncMock(
        return_value=pi.PreparedPiTerminal("s1", "term", Path("/tmp/socket"), "pi:0", False)
    )
    monkeypatch.setattr(pi, "_prepare_pi_terminal_via_daemon", prepare_terminal)
    attach = AsyncMock()
    browser = Mock()
    hint = Mock()
    monkeypatch.setattr(pi, "_attach_terminal_resource", attach)
    monkeypatch.setattr(pi, "open_conversation_link_if_enabled", browser)
    monkeypatch.setattr(pi, "echo_native_resume_hint", hint)
    return SimpleNamespace(
        bundle=bundle,
        ensure=ensure,
        prepare=prepare_terminal,
        attach=attach,
        browser=browser,
        hint=hint,
    )


@pytest.mark.parametrize("session_id", [None, "existing"])
def test_remote_launch_bundles_only_new_sessions_and_attaches(
    remote_launch, session_id, monkeypatch, capsys
):
    monkeypatch.setattr(pi, "_resolve_session_id_for_resume", lambda **kwargs: session_id)
    pi._run_with_remote_server(
        "http://server",
        Path("/tmp/spec.yaml"),
        session_id=session_id,
        resume_picker=False,
        pi_args=("--model", "test"),
        auto_open_conversation=True,
    )
    remote_launch.ensure.assert_called_once_with("http://server")
    assert remote_launch.bundle.call_count == int(session_id is None)
    kwargs = remote_launch.prepare.await_args.kwargs
    assert kwargs["session_id"] == session_id
    assert kwargs["session_bundle"] == (b"bundle" if session_id is None else None)
    assert kwargs["host_id"] == "host-1"
    assert kwargs["pi_args"] == ("--model", "test")
    remote_launch.attach.assert_awaited_once_with(remote_launch.prepare.return_value)
    assert remote_launch.browser.call_args.kwargs["enabled"] is True
    assert remote_launch.hint.call_count == int(session_id is None)
    assert "Web UI:" in capsys.readouterr().err


def test_remote_launch_picker_cancel_does_not_start_daemon(remote_launch, monkeypatch):
    monkeypatch.setattr(pi, "_resolve_session_id_for_resume", lambda **kwargs: None)
    pi._run_with_remote_server(
        "http://server", Path("/tmp/spec"), session_id=None, resume_picker=True, pi_args=()
    )
    remote_launch.ensure.assert_not_called()
    remote_launch.prepare.assert_not_awaited()


def test_remote_connection_error_becomes_actionable_cli_error(remote_launch, monkeypatch):
    monkeypatch.setattr(
        pi, "_resolve_session_id_for_resume", Mock(side_effect=httpx.ConnectError("offline"))
    )
    with pytest.raises(
        click.ClickException, match="Could not reach the omnigent server at http://server"
    ):
        pi._run_with_remote_server(
            "http://server", Path("/tmp/spec"), session_id=None, resume_picker=False, pi_args=()
        )
    remote_launch.ensure.assert_not_called()
