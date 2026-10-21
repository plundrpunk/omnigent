"""Exercise the child runner's request/response contract in process."""

import io
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omnigent.tools import _runner as runner


@pytest.fixture
def request_runner(monkeypatch, tmp_path):
    responses = []
    monkeypatch.setattr(runner, "_write_response", responses.append)

    def invoke(source, request):
        module = tmp_path / "tool.py"
        module.write_text(source)
        body = {"module_path": str(module), **request}
        monkeypatch.setattr(
            runner.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(body).encode()))
        )
        runner.main()
        return responses.pop()

    return invoke


@pytest.mark.parametrize("asynchronous", [False, True])
def test_dispatch_decorated_tool_and_serialize_result(request_runner, asynchronous):
    source = (
        "from omnigent_client import tool\n"
        "@tool\n"
        f"{'async ' if asynchronous else ''}def double(value: int) -> dict[str, int]:\n"
        '    """Double the supplied value."""\n'
        '    return {"doubled": value * 2}\n'
    )
    result = request_runner(source, {"tool_name": "double", "arguments": {"value": 21}})
    assert json.loads(result["result"]) == {"doubled": 42}


@pytest.mark.parametrize(
    ("source", "name", "error"),
    [
        ("", "", "missing 'tool_name'"),
        ("", "absent", "not found"),
        ("value = 4", "value", "not callable"),
        ("def helper(): return 1", "helper", "not decorated"),
        (
            'raise RuntimeError("broken import")',
            "anything",
            "Import error: RuntimeError: broken import",
        ),
        (
            'def fail(): raise ValueError("bad input")\nfail._omnigent_tool_metadata = object()',
            "fail",
            "ValueError: bad input",
        ),
    ],
)
def test_dispatch_rejects_invalid_targets(request_runner, source, name, error):
    assert error in request_runner(source, {"tool_name": name})["error"]


def test_invalid_json_returns_protocol_error(monkeypatch):
    response = Mock()
    monkeypatch.setattr(runner, "_write_response", response)
    monkeypatch.setattr(runner.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"{bad")))
    runner.main()
    assert response.call_args.args[0]["error"].startswith("Invalid request JSON:")


@pytest.mark.parametrize("path", ["", "/missing/tool.py", "/missing/tool.unknown"])
def test_module_load_failure_returns_error(monkeypatch, path):
    response = Mock()
    monkeypatch.setattr(runner, "_write_response", response)
    assert runner._load_module(path) is None
    assert "error" in response.call_args.args[0]


def test_tool_state_is_parent_scoped_and_overrides_caller_value(tmp_path):
    def needs_state(tool_state):
        return tool_state

    args = {"tool_state": "caller-controlled"}
    runner._maybe_inject_tool_state(needs_state, args, str(tmp_path))
    from omnigent_client.tools import ToolState

    assert isinstance(args["tool_state"], ToolState)
    assert args["tool_state"] is not None
    with pytest.raises(RuntimeError, match="no state_root"):
        runner._maybe_inject_tool_state(needs_state, {}, None)
    args = {"value": 4}
    runner._maybe_inject_tool_state(lambda value: value, args, None)
    assert args == {"value": 4}
    runner._maybe_inject_tool_state(object(), args, None)
    assert args == {"value": 4}


def test_serialization_preserves_strings_and_handles_unsupported_values():
    def target():
        return None

    assert runner._serialize_result(target, "already text") == "already text"
    assert json.loads(runner._serialize_result(target, {"values": {1, 2}})) == {"values": "{1, 2}"}
    target._omnigent_tool_metadata = SimpleNamespace(return_annotation=object())
    assert json.loads(runner._serialize_result(target, [1, 2])) == [1, 2]
    circular = []
    circular.append(circular)
    assert "unserializable return value: list" in runner._serialize_result(target, circular)


def test_response_file_descriptor_is_closed_after_complete_json(monkeypatch):
    read_fd, write_fd = os.pipe()
    monkeypatch.delenv("_AP_RESPONSE_MODE", raising=False)
    monkeypatch.setenv("_AP_RESPONSE_FD", str(write_fd))
    try:
        runner._write_response({"result": "hello"})
        assert json.loads(os.read(read_fd, 4096)) == {"result": "hello"}
        with pytest.raises(OSError):
            os.fstat(write_fd)
    finally:
        os.close(read_fd)


def test_stdout_response_is_prefixed_and_flushed(monkeypatch):
    buffer = io.BytesIO()
    monkeypatch.setattr(runner.sys, "stdout", SimpleNamespace(buffer=buffer, fileno=lambda: 1))
    monkeypatch.setenv("_AP_RESPONSE_MODE", "stdout")
    runner._write_response({"result": "ok"})
    assert buffer.getvalue() == b'__AP_RESPONSE__:{"result": "ok"}\n'
