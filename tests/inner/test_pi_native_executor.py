"""Pi bridge queue behavior without launching a native agent."""

from unittest.mock import Mock

import pytest

from omnigent.inner import pi_native_executor as pi
from omnigent.inner.executor import ExecutorError, TurnComplete


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    monkeypatch.setenv(pi.PI_NATIVE_BRIDGE_DIR_ENV_VAR, str(tmp_path))
    monkeypatch.setenv(pi.PI_NATIVE_REQUEST_SESSION_ID_ENV_VAR, " session-fixture ")
    enqueue = Mock()
    monkeypatch.setattr(pi, "enqueue_user_message", enqueue)
    return pi.PiNativeExecutor(), enqueue, tmp_path


async def test_turn_sends_latest_user_message_only(bridge):
    executor, enqueue, directory = bridge
    messages = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "later"},
    ]
    events = [e async for e in executor.run_turn(messages, [], "ignored")]
    assert len(events) == 1 and isinstance(events[0], TurnComplete)
    enqueue.assert_called_once_with(directory, "new")
    assert not executor.supports_streaming()
    assert executor.supports_live_message_queue()
    assert executor._request_session_id == "session-fixture"


@pytest.mark.parametrize(
    "messages",
    [[], [{"role": "assistant", "content": "not user"}], [{"role": "user", "content": None}]],
)
async def test_empty_turn_reports_error_without_queueing(bridge, messages):
    executor, enqueue, _ = bridge
    events = [e async for e in executor.run_turn(messages, [], "")]
    assert len(events) == 1 and isinstance(events[0], ExecutorError)
    assert "no user text" in events[0].message
    enqueue.assert_not_called()


async def test_steering_materializes_attachments_and_preserves_text(bridge, monkeypatch):
    executor, enqueue, directory = bridge
    materialize = Mock(side_effect=[directory / "image.png", None])
    monkeypatch.setattr(pi, "materialize_attachment", materialize)
    blocks = [
        None,
        {"type": "input_text", "text": "inspect"},
        {"type": "input_text", "text": 2},
        {"type": "input_image", "image_url": "fixture"},
        {"type": "input_file"},
        {"type": "unknown"},
    ]
    assert await executor.enqueue_session_message("unused", blocks)
    enqueue.assert_called_once_with(directory, f"[Attached: {directory / 'image.png'}]\n\ninspect")
    assert materialize.call_count == 2
    assert not await executor.enqueue_session_message("unused", [])
    assert pi._content_to_text(42, directory) == "42"


def test_missing_bridge_env_fails_closed(monkeypatch, tmp_path):
    monkeypatch.delenv(pi.PI_NATIVE_BRIDGE_DIR_ENV_VAR, raising=False)
    monkeypatch.delenv(pi.PI_NATIVE_REQUEST_SESSION_ID_ENV_VAR, raising=False)
    with pytest.raises(RuntimeError, match="required for pi-native"):
        pi.PiNativeExecutor()
    executor = pi.PiNativeExecutor(bridge_dir=tmp_path)
    assert executor._bridge_dir == tmp_path
    assert executor._request_session_id is None
