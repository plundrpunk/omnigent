"""Tracing hierarchy and serialization without a live tracking backend."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omnigent.inner import tracing


@pytest.fixture
def spans(monkeypatch):
    created = []

    def start(**kwargs):
        span = Mock(end_time_ns=None, span_id=f"span-{len(created)}")
        created.append((kwargs, span))
        return span

    backend = SimpleNamespace(start_span_no_context=Mock(side_effect=start))
    monkeypatch.setitem(sys.modules, "mlflow", backend)
    return created


def test_tracing_toggle_and_missing_dependency(monkeypatch):
    monkeypatch.setattr(tracing, "_tracing_enabled", False)
    tracing.enable_tracing()
    assert tracing.is_tracing_enabled()
    tracing.disable_tracing()
    assert not tracing.is_tracing_enabled()
    monkeypatch.setitem(sys.modules, "mlflow", None)
    with pytest.raises(ImportError, match="requires the"):
        tracing._mlflow()


def test_child_agent_inherits_tool_parent_and_restores_it(spans):
    parent = tracing.TracingContext()
    assert not parent.active
    root = parent.start_agent_span("parent", "request", model="fixture-model")
    assert parent.active
    tool = parent.start_tool_span("delegate", {"task": "child"})
    child = parent.create_child_context()
    assert child.enabled
    child_root = child.start_agent_span("child", "work")
    assert spans[-1][0]["parent_span"] is tool
    child.end_agent_span(child_root, "done")
    assert not child.active
    assert child._current_span is tool
    parent.end_tool_span(tool, {"ok": True}, duration_ms=12, parent_span=root)
    tool.set_outputs.assert_called_once_with({"result": {"ok": True}})
    tool.set_attribute.assert_called_once_with("duration_ms", 12)
    assert parent._current_span is root
    parent.end_agent_span(root, "final")
    root.set_outputs.assert_called_once_with({"response": "final"})
    assert not parent.active
    assert parent._current_span is None


def test_ended_parent_becomes_lineage_attribute(spans):
    ctx = tracing.TracingContext()
    parent = ctx.start_agent_span("parent", "work")
    parent.end_time_ns = 123
    child = ctx.create_child_context()
    root = child.start_agent_span("child", "later")
    kwargs, _ = spans[-1]
    assert kwargs["parent_span"] is None
    assert kwargs["attributes"] == {"agent.name": "child", "parent_span_id": parent.span_id}
    child.end_agent_span(root, None, error="failed")
    root.set_status.assert_called_once_with("ERROR")
    root.set_attribute.assert_called_once_with("error.message", "failed")


@pytest.mark.parametrize("error", [None, "unavailable"])
def test_llm_and_tool_span_outputs_status_and_parent(spans, error):
    ctx = tracing.TracingContext()
    root = ctx.start_agent_span("agent", "prompt")
    llm = ctx.start_llm_span([{"role": "user", "content": "hello"}], model="fixture")
    assert spans[-1][0] == {
        "name": "llm_call",
        "span_type": "CHAT_MODEL",
        "parent_span": root,
        "inputs": {"messages": [{"role": "user", "content": "hello"}]},
        "attributes": {"model": "fixture"},
    }
    ctx.end_llm_span(llm, None, error=error)
    llm.set_outputs.assert_called_once_with({"response": None})
    llm.set_status.assert_called_once_with("ERROR" if error else "OK")
    llm.end.assert_called_once()
    tool = ctx.start_tool_span("read", {"path": "fixture"})
    ctx.end_tool_span(tool, "result", error=error, parent_span=root)
    tool.set_status.assert_called_once_with("ERROR" if error else "OK")
    assert ctx._current_span is root


@pytest.mark.parametrize("action", ["allow", "deny"])
def test_policy_trace_records_decision(spans, action):
    ctx = tracing.TracingContext()
    span = ctx.start_policy_span("guard", "pre", {"value": "input"})
    assert spans[-1][0]["inputs"] == {
        "policy": "guard",
        "phase": "pre",
        "content": {"value": "input"},
    }
    ctx.end_policy_span(span, action=action)
    span.set_outputs.assert_called_once_with({"action": action, "reason": None})
    span.set_status.assert_called_once_with("ERROR" if action == "deny" else "OK")
    for end in (ctx.end_agent_span, ctx.end_llm_span, ctx.end_tool_span, ctx.end_policy_span):
        if end == ctx.end_agent_span:
            end(None, None)
        else:
            end(None)
    assert not ctx.active


def test_trace_serialization_bounds_messages_without_mutating_input():
    text = "x" * 2001
    messages = [
        {"role": "system", "content": "old"},
        {"role": "user", "content": text},
        {"content": None},
    ]
    assert tracing._truncate_messages(messages, max_messages=2) == [
        {"role": "user", "content": "x" * 2000 + "...(truncated)"},
        {"role": "unknown", "content": ""},
    ]
    assert messages[1]["content"] == text
    assert tracing._truncate_messages(None) == []
    assert tracing._safe_serialize({1: (None, True, 4, "long text")}, max_len=4) == {
        "1": [None, True, 4, "long...(truncated)"]
    }

    class Opaque:
        def __str__(self):
            return "opaque value"

    assert tracing._safe_serialize(Opaque(), max_len=5) == '"opaq...(truncated)'
    assert tracing._safe_serialize(Opaque()) == '"opaque value"'
