"""Drive the real REPL input/event callbacks through a scripted terminal host."""

import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from omnigent_ui_sdk import DARK_THEME
from rich.console import Console

from omnigent.repl import _event_tape
from omnigent.repl import _repl as repl
from omnigent.server import schemas as wire


class ScriptedHost:
    def __init__(self, driver, **kwargs):
        self.driver = driver
        self.outputs = []
        self.overlays = []
        self._toolbar_hints = kwargs["toolbar_hints"]
        self.start_timer = Mock()
        self.stop_timer = Mock()
        self.update_context_usage = Mock()
        self.request_exit = Mock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def run(self, on_input):
        await self.driver(self, on_input)

    def output(self, value):
        self.outputs.append(value)

    def add_overlay(self, value):
        self.overlays.append(value)

    def text(self):
        buffer = io.StringIO()
        console = Console(file=buffer, width=160, color_system=None)
        for item in self.outputs:
            console.print(getattr(item, "renderable", getattr(item, "text", item)))
        return buffer.getvalue()


@pytest.fixture
def journey(monkeypatch, tmp_path):
    session = SimpleNamespace(
        session_id="conv_fixture",
        _agent_name="fixture-agent",
        model="fixture-agent",
        _pending_local_user_sends=0,
        _pending_local_skill_slash_commands=[],
        _pending_local_tasks={},
        _tool_callables={"echo": Mock()},
        _turn_done=asyncio.Event(),
        context_window=4096,
        _spawn_client_tool=Mock(),
        _handle_elicitation=AsyncMock(),
        cancel=AsyncMock(),
        aclose=AsyncMock(),
        _notify_session_start_once=Mock(),
    )
    sends = []

    async def send(text, files=None):
        sends.append((text, files))
        if False:
            yield None

    session.send = send
    factory = Mock(return_value=session)
    monkeypatch.setattr(repl, "_SessionsChatReplAdapter", factory)
    monkeypatch.setattr(repl, "_load_startup_theme", lambda: DARK_THEME)
    monkeypatch.setattr(repl, "_build_startup_header", lambda *args: None)
    monkeypatch.setattr(repl, "_refresh_session_metadata", AsyncMock())
    monkeypatch.setattr(repl, "_update_context_ring_estimate", AsyncMock())
    monkeypatch.setattr(_event_tape, "open_event_log", lambda sid: tmp_path / "events.jsonl")
    hosts = []

    async def run(driver, **kwargs):
        def make_host(**options):
            host = ScriptedHost(driver, **options)
            hosts.append(host)
            return host

        monkeypatch.setattr(repl, "TerminalHost", make_host)
        result = await repl.run_repl(
            SimpleNamespace(), "fixture-agent", None, session_bundle=b"fixture", **kwargs
        )
        assert result == "conv_fixture"
        session.aclose.assert_awaited()
        return hosts[-1]

    return SimpleNamespace(
        run=run, session=session, sends=sends, factory=factory, log=tmp_path / "events.jsonl"
    )


def event(cls, type_name, **fields):
    return cls(type=type_name, **fields)


def status(value, **fields):
    return event(
        wire.SessionStatusEvent,
        "session.status",
        conversation_id="conv_fixture",
        status=value,
        **fields,
    )


def item(**fields):
    return event(wire.OutputItemDoneEvent, "response.output_item.done", item=fields)


@pytest.mark.parametrize("debug", [False, True])
async def test_turn_stream_tool_boundary_and_persisted_echo(journey, debug):
    async def driver(host, on_input):
        host.on_toggle_tool_output()
        await on_input("hello")
        emit = journey.session._on_event
        emit(status("running"))
        emit(
            event(wire.OutputTextDeltaEvent, "response.output_text.delta", delta="Working on it.")
        )
        emit(
            item(
                type="function_call",
                name="echo",
                call_id="call1",
                arguments='{"value":"sample"}',
                status="action_required",
            )
        )
        before = len(host.outputs)
        emit(
            item(
                type="message",
                role="assistant",
                content=[{"type": "output_text", "text": "Working on it."}],
            )
        )
        assert len(host.outputs) == before, (
            "Persisted copy of streamed segment must not render twice"
        )
        emit(item(type="function_call_output", call_id="call1", output="tool result"))
        emit(event(wire.OutputTextDeltaEvent, "response.output_text.delta", delta="Finished."))
        emit(
            item(
                type="message",
                role="assistant",
                content=[{"type": "output_text", "text": "Finished."}],
            )
        )
        emit(status("idle"))
        await asyncio.sleep(0)
        assert journey.session._turn_done.is_set()
        journey.session._spawn_client_tool.assert_called_once_with(
            "conv_fixture", "call1", "echo", '{"value":"sample"}'
        )
        assert journey.session._live_call_id_to_tool_metadata == {
            "call1": ("echo", {"value": "sample"})
        }
        host.on_toggle_tool_output()
        assert (
            "Ctrl+T show tools" in host._toolbar_hints
            or "Ctrl+T hide tools" in host._toolbar_hints
        )
        host.on_help()

    host = await journey.run(driver, debug_events=debug, resume_parts=["omnigent", "chat"])
    text = host.text()
    for expected in (
        "hello",
        "Working on it.",
        "tool result",
        "Finished.",
        "/help",
        "--resume conv_fixture",
    ):
        assert expected in text
    assert len(host.overlays) == (2 if debug else 1)
    if debug:
        rows = [json.loads(line) for line in journey.log.read_text().splitlines()]
        assert len(rows) == 8
        targets = await host.overlays[-1].targets_builder()
        assert targets
        detail = await host.overlays[-1].builder(targets[0])
        assert detail is not None
        assert "No events" in str(await host.overlays[-1].builder(None))


@pytest.mark.parametrize("debug", [False, True])
async def test_status_reasoning_compaction_and_errors_are_visible(journey, debug):
    async def driver(host, on_input):
        emit = journey.session._on_event
        emit(status("running"))
        emit(event(wire.ReasoningStartedEvent, "response.reasoning.started"))
        emit(
            event(
                wire.ReasoningTextDeltaEvent,
                "response.reasoning_text.delta",
                delta="checking fixtures",
            )
        )
        emit(
            event(
                wire.ReasoningSummaryTextDeltaEvent,
                "response.reasoning_summary_text.delta",
                delta="summary",
            )
        )
        emit(event(wire.CompactionInProgressEvent, "response.compaction.in_progress"))
        emit(event(wire.CompactionCompletedEvent, "response.compaction.completed"))
        emit(
            event(
                wire.ErrorEvent,
                "response.error",
                source="tool",
                error={"code": "bad_tool", "message": "tool unavailable"},
            )
        )
        emit(status("failed", error={"code": "setup_failed", "message": "setup unavailable"}))
        emit(
            event(
                wire.SessionAgentChangedEvent,
                "session.agent_changed",
                conversation_id="conv_fixture",
                agent_id="agent2",
                agent_name="new-agent",
            )
        )
        emit(status("waiting"))
        emit(SimpleNamespace(type="future.unrecognized"))
        await asyncio.sleep(0)
        repl._refresh_session_metadata.assert_awaited()

    host = await journey.run(driver, debug_events=debug)
    for expected in (
        "checking fixtures",
        "summary",
        "Compacting",
        "Compaction complete",
        "tool unavailable",
        "setup unavailable",
    ):
        assert expected in host.text()
    host.stop_timer.assert_called()


async def test_consumed_inputs_echo_remote_user_but_not_local_or_meta(journey):
    async def driver(host, on_input):
        emit = journey.session._on_event

        def consumed(text, **kwargs):
            return event(
                wire.SessionInputConsumedEvent,
                "session.input.consumed",
                data={
                    "item_id": "input",
                    "type": "message",
                    "data": {
                        "role": "user",
                        "content": [{"type": "input_text", "text": text}],
                        **kwargs,
                    },
                },
            )

        emit(consumed("remote-user-text"))
        journey.session._pending_local_user_sends = 1
        emit(consumed("local-duplicate"))
        assert journey.session._pending_local_user_sends == 0
        emit(consumed("hidden-meta", is_meta=True))
        pending = asyncio.create_task(asyncio.sleep(30))
        journey.session._pending_local_tasks["call-cancel"] = pending
        emit(
            event(
                wire.ClientTaskCancelEvent,
                "response.client_task.cancel",
                task_id="task1",
                call_id="call-cancel",
            )
        )
        await asyncio.sleep(0)
        assert pending.cancelled()

    host = await journey.run(driver, debug_events=True)
    assert "remote-user-text" in host.text()
    assert "local-duplicate" not in host.text()
    assert "hidden-meta" not in host.text()


async def test_input_paths_attachments_and_slash_command_dispatch(journey, tmp_path):
    attachment = tmp_path / "note.txt"
    attachment.write_text("fixture")

    async def driver(host, on_input):
        await on_input("/help")
        await on_input("/tmp/fixture.txt")
        await on_input("read", [SimpleNamespace(path=str(attachment))])
        await on_input("/quit")
        host.request_exit.assert_called_once()

    host = await journey.run(driver)
    assert journey.sends[0] == ("/tmp/fixture.txt", None)
    assert journey.sends[1][0].startswith("read ")
    assert journey.sends[1][1] == [str(attachment)]
    assert "/help" in host.text()


@pytest.mark.parametrize("cancelled", [False, True])
async def test_send_failure_and_cancellation_surface_and_cleanup(journey, cancelled):
    async def send(*args, **kwargs):
        if cancelled:
            raise asyncio.CancelledError()
        raise RuntimeError("transport unavailable")
        yield

    journey.session.send = send

    async def driver(host, on_input):
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await on_input("request")
            journey.session.cancel.assert_awaited_once()
        else:
            await on_input("request")

    host = await journey.run(driver)
    assert ("cancelled" if cancelled else "transport unavailable") in host.text()
    host.stop_timer.assert_called()
