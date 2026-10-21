"""Unit tests for the unified trajectory normalizer (``trajectory.v1``).

The normalizer is a pure read-side adapter: it folds Omnigent session
items, Harness Automaton goal runs / artifact events and AMS
``harness.receipt.v1`` work receipts into one typed, chronological
timeline. Nothing here touches a producer.
"""

from __future__ import annotations

from typing import Any

import pytest

from omnigent.server.trajectory import (
    SCHEMA_VERSION,
    TrajectoryScope,
    merge_trajectory,
    normalize_goal_run,
    normalize_harness_artifact,
    normalize_session_items,
    normalize_work_receipt,
)

CONV = "conv_parent"
EVENT_ID = "0b0e7a52-3c1d-4f0e-9a51-6c0b2f1d7e44"


def _receipt(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "harness.receipt.v1",
        "event_id": EVENT_ID,
        "user_id": "drew",
        "project": "fleet-autonomy",
        "work_item_id": "wi-1",
        "session_id": "goal-queue:g-1",
        "status": "completed",
        "created_at": "2026-10-21T10:00:05+00:00",
        "verifier": {"status": "passed", "verdict": "PASS", "evidence": ["tests:ok"]},
        "artifact": {"artifact_id": "art-1", "changed_files": []},
        "tool_result_ids": [],
        "metadata": {"runner": "goal-queue-daemon", "goal_id": "g-1"},
    }
    base.update(overrides)
    return base


def _artifact() -> dict[str, Any]:
    return {
        "id": "art-1",
        "goal": "make the tests pass",
        "status": "complete",
        "created_at": "2026-10-21T10:00:00+00:00",
        "updated_at": "2026-10-21T10:00:04+00:00",
        "state": {},
        "events": [
            {
                "id": "ev-plan",
                "created_at": "2026-10-21T10:00:01+00:00",
                "role": "planner",
                "content": '{"subgoals": [{"id": "sg1"}]}',
                "metadata": {
                    "phase": "planning",
                    "raw_content": "SECRET CHAIN OF THOUGHT",
                    "parsed": {"subgoals": [{"id": "sg1"}]},
                },
            },
            {
                "id": "ev-tool",
                "created_at": "2026-10-21T10:00:02+00:00",
                "role": "tool",
                "content": (
                    '{"tool": "run_shell", "ok": false, "exit_code": 1, '
                    '"call": {"command": "pytest -q"}, "stderr": "1 failed"}'
                ),
                "metadata": {"phase": "tool_exec", "ok": False, "exit_code": 1},
            },
            {
                "id": "ev-verify",
                "created_at": "2026-10-21T10:00:03+00:00",
                "role": "verifier",
                "content": '{"verdict": "PASS", "reason": "gate green"}',
                "metadata": {"phase": "verify", "subgoal_id": "sg1"},
            },
            {
                "id": "ev-gov",
                "created_at": "2026-10-21T10:00:04+00:00",
                "role": "governor",
                "content": '{"blocked": true, "reason": "token budget exhausted"}',
                "metadata": {"phase": "governance"},
            },
        ],
    }


def _run(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": "run-1",
        "goal_id": "g-1",
        "conversation_id": CONV,
        "status": "completed",
        "exit_code": 0,
        "started_at": "2026-10-21T09:59:59+00:00",
        "finished_at": "2026-10-21T10:00:06+00:00",
        "outcome": {"artifact_path": "/runs/artifacts/art-1.json"},
    }
    base.update(overrides)
    return base


def _items() -> list[dict[str, Any]]:
    return [
        {
            "id": "msg_user",
            "type": "message",
            "status": "completed",
            "response_id": "resp_1",
            "created_at": 1792576790,  # 2026-10-21T09:59:50Z
            "role": "user",
            "content": [{"type": "input_text", "text": "Run the goal please"}],
            "created_by": "alice@example.com",
        },
        {
            "id": "fc_1",
            "type": "function_call",
            "status": "completed",
            "response_id": "resp_1",
            "created_at": 1792576791,
            "model": "coder",
            "name": "sys_session_send",
            "arguments": '{"agent": "researcher", "message": "look"}',
            "call_id": "call_1",
        },
        {
            "id": "reason_1",
            "type": "reasoning",
            "status": "completed",
            "response_id": "resp_1",
            "created_at": 1792576791,
            "model": "coder",
            "summary": [{"type": "summary_text", "text": "private reasoning"}],
        },
        {
            "id": "err_1",
            "type": "error",
            "status": "completed",
            "response_id": "resp_1",
            "created_at": 1792576792,
            "source": "execution",
            "code": "runner_down",
            "message": "Runner went away",
        },
        {
            "id": "wr_1",
            "type": "work_receipt",
            "status": "completed",
            "response_id": "resp_1",
            "created_at": 1792576807,
            **_receipt(session_id=CONV),
        },
    ]


# ── Per-source normalization ─────────────────────────────────────────


def test_session_items_normalize_to_typed_entries() -> None:
    entries = normalize_session_items(_items(), conversation_id=CONV)
    by_id = {e.entry_id: e for e in entries}

    user = by_id["omnigent:conv_parent:msg_user"]
    assert user.schema_version == SCHEMA_VERSION
    assert user.source == "omnigent"
    assert user.action == "user_message"
    assert user.actor == "alice@example.com"
    assert user.timestamp == "2026-10-21T09:59:50+00:00"
    assert user.summary == "Run the goal please"
    assert user.receipt is None

    dispatch = by_id["omnigent:conv_parent:fc_1"]
    assert dispatch.action == "dispatch"
    assert dispatch.actor == "coder"
    assert dispatch.status == "ok"
    assert "sys_session_send" in dispatch.summary

    error = by_id["omnigent:conv_parent:err_1"]
    assert error.action == "error"
    assert error.status == "failed"


def test_session_reasoning_is_never_surfaced() -> None:
    entries = normalize_session_items(_items(), conversation_id=CONV)
    assert all("reason_1" not in e.entry_id for e in entries)
    assert all("private reasoning" not in e.summary for e in entries)


def test_session_work_receipt_links_to_receipt() -> None:
    entries = normalize_session_items(_items(), conversation_id=CONV)
    receipt = next(e for e in entries if e.action == "work_receipt")
    assert receipt.status == "ok"
    assert receipt.owner == "drew"
    assert receipt.receipt is not None
    assert receipt.receipt.kind == "work_receipt"
    assert receipt.receipt.id == EVENT_ID
    assert receipt.receipt.href == f"/v1/trajectory/{CONV}/receipts/{EVENT_ID}"
    assert receipt.dedupe_key == f"receipt:{EVENT_ID}"


def test_subagent_items_carry_their_child_lane() -> None:
    entries = normalize_session_items(
        _items()[:2], conversation_id="conv_child", subagent="researcher"
    )
    assert {e.conversation_id for e in entries} == {"conv_child"}
    assert all(e.subagent == "researcher" for e in entries)


def test_goal_run_lifecycle_links_to_run_record() -> None:
    entries = normalize_goal_run(_run())
    assert [e.action for e in entries] == ["goal_started", "goal_finished"]
    started, finished = entries
    assert started.source == "harness"
    assert started.status == "running"
    assert finished.status == "ok"
    assert finished.goal_id == "g-1"
    assert finished.conversation_id == CONV
    assert finished.receipt is not None
    assert finished.receipt.kind == "goal_run"
    assert finished.receipt.href == "/v1/goal/run-1"


def test_running_goal_run_has_no_finished_entry() -> None:
    entries = normalize_goal_run(_run(status="running", finished_at=None, exit_code=None))
    assert [e.action for e in entries] == ["goal_started"]


def test_blocked_and_paused_goal_runs_keep_their_status() -> None:
    blocked = normalize_goal_run(_run(status="blocked", exit_code=3))[-1]
    paused = normalize_goal_run(_run(status="paused", exit_code=6))[-1]
    assert blocked.status == "blocked"
    assert paused.status == "paused"


def test_harness_artifact_events_normalize() -> None:
    entries = normalize_harness_artifact(
        _artifact(), run_id="run-1", goal_id="g-1", conversation_id=CONV
    )
    assert [e.action for e in entries] == ["planning", "tool_exec", "verify", "governance"]
    plan, tool, verify, gov = entries
    assert plan.actor == "planner"
    assert plan.entry_id == "harness:art-1:ev-plan"
    assert tool.status == "failed"
    assert "pytest -q" in tool.summary
    assert verify.status == "ok"
    assert "gate green" in verify.summary
    assert gov.status == "blocked"
    assert all(e.receipt and e.receipt.href == "/v1/goal/run-1" for e in entries)


def test_harness_artifact_never_leaks_raw_model_content() -> None:
    entries = normalize_harness_artifact(
        _artifact(), run_id="run-1", goal_id="g-1", conversation_id=CONV
    )
    assert all("CHAIN OF THOUGHT" not in e.summary for e in entries)


def test_ams_receipt_normalizes() -> None:
    entry = normalize_work_receipt(_receipt(), conversation_id=CONV, source="ams")
    assert entry is not None
    assert entry.source == "ams"
    assert entry.actor == "goal-queue-daemon"
    assert entry.action == "work_receipt"
    assert entry.status == "ok"
    assert entry.owner == "drew"
    assert entry.goal_id == "g-1"
    assert entry.timestamp == "2026-10-21T10:00:05+00:00"
    assert "passed" in entry.summary


def test_receipt_status_mapping() -> None:
    blocked = normalize_work_receipt(_receipt(status="blocked"), conversation_id=CONV)
    failed = normalize_work_receipt(_receipt(status="failed"), conversation_id=CONV)
    assert blocked is not None and blocked.status == "blocked"
    assert failed is not None and failed.status == "failed"


def test_malformed_receipt_is_dropped() -> None:
    assert normalize_work_receipt({"event_id": "x"}, conversation_id=CONV) is None
    assert normalize_work_receipt(None, conversation_id=CONV) is None


def test_receipt_origin_conversation_wins_over_query_conversation() -> None:
    entry = normalize_work_receipt(
        _receipt(metadata={"origin": {"omnigent_conversation_id": "conv_other"}}),
        conversation_id=CONV,
    )
    assert entry is not None
    assert entry.conversation_id == "conv_other"


# ── Merge: ordering, dedupe, scope ───────────────────────────────────


def _scope(owners: frozenset[str] | None = None) -> TrajectoryScope:
    return TrajectoryScope(conversation_ids=frozenset({CONV}), owners=owners)


def test_merge_orders_chronologically_across_sources() -> None:
    merged = merge_trajectory(
        [
            normalize_work_receipt(_receipt(), conversation_id=CONV, source="ams"),
            *normalize_harness_artifact(
                _artifact(), run_id="run-1", goal_id="g-1", conversation_id=CONV
            ),
            *normalize_goal_run(_run()),
            *normalize_session_items(_items()[:2], conversation_id=CONV),
        ],
        _scope(),
    )
    stamps = [e.timestamp for e in merged]
    assert stamps == sorted(stamps)
    assert [e.source for e in merged][:2] == ["omnigent", "omnigent"]
    assert merged[-1].action == "goal_finished"
    assert {e.source for e in merged} == {"omnigent", "harness", "ams"}


def test_merge_is_stable_for_equal_timestamps() -> None:
    items = _items()[:2]
    items[1]["created_at"] = items[0]["created_at"]
    merged = merge_trajectory(normalize_session_items(items, conversation_id=CONV), _scope())
    assert [e.entry_id.rsplit(":", 1)[-1] for e in merged] == ["msg_user", "fc_1"]


def test_merge_dedupes_receipt_seen_in_session_and_ams() -> None:
    session = normalize_session_items(_items(), conversation_id=CONV)
    ams = normalize_work_receipt(_receipt(session_id=CONV), conversation_id=CONV, source="ams")
    merged = merge_trajectory([*session, ams, ams], _scope())
    receipts = [e for e in merged if e.action == "work_receipt"]
    assert len(receipts) == 1
    # AMS is the receipt's system of record, so its copy wins.
    assert receipts[0].source == "ams"


def test_merge_dedupes_artifact_shared_by_resumed_runs() -> None:
    first = normalize_harness_artifact(
        _artifact(), run_id="run-1", goal_id="g-1", conversation_id=CONV
    )
    resumed = normalize_harness_artifact(
        _artifact(), run_id="run-2", goal_id="g-1", conversation_id=CONV
    )
    merged = merge_trajectory([*first, *resumed], _scope())
    assert len(merged) == len(first)


def test_untimed_entries_sort_last_and_say_so() -> None:
    receipt = normalize_work_receipt(_receipt(created_at="not a time"), conversation_id=CONV)
    assert receipt is not None
    assert receipt.timestamp is None
    merged = merge_trajectory(
        [receipt, *normalize_session_items(_items()[:1], conversation_id=CONV)], _scope()
    )
    assert merged[-1] is receipt


def test_scope_drops_other_conversations() -> None:
    mine = normalize_goal_run(_run())
    theirs = normalize_goal_run(_run(run_id="run-x", conversation_id="conv_someone_else"))
    merged = merge_trajectory([*mine, *theirs], _scope())
    assert {e.run_id for e in merged} == {"run-1"}


def test_scope_drops_receipts_owned_by_another_user() -> None:
    mine = normalize_work_receipt(_receipt(), conversation_id=CONV, source="ams")
    theirs = normalize_work_receipt(
        _receipt(event_id="1b0e7a52-3c1d-4f0e-9a51-6c0b2f1d7e44", user_id="mallory"),
        conversation_id=CONV,
        source="ams",
    )
    merged = merge_trajectory([mine, theirs], _scope(owners=frozenset({"drew"})))
    assert [e.owner for e in merged] == ["drew"]


@pytest.mark.parametrize(
    "user_fields", [{}, {"user_id": None}, {"user_id": ""}, {"user_id": "  "}]
)
@pytest.mark.parametrize("owners", [None, frozenset(), frozenset({"drew"})])
def test_ownerless_ams_receipts_require_single_user_mode(user_fields, owners) -> None:
    receipt = _receipt()
    receipt.pop("user_id")
    receipt.update(user_fields)
    entry = normalize_work_receipt(receipt, conversation_id=CONV, source="ams")
    assert entry is not None
    assert merge_trajectory([entry], _scope(owners)) == ([entry] if owners is None else [])


def test_scope_without_owner_constraint_keeps_unowned_and_owned() -> None:
    mine = normalize_work_receipt(_receipt(), conversation_id=CONV, source="ams")
    merged = merge_trajectory([mine, *normalize_goal_run(_run())], _scope(owners=None))
    assert len(merged) == 3


def test_entry_serializes_to_wire_shape() -> None:
    entry = normalize_goal_run(_run())[-1]
    wire = entry.to_dict()
    assert wire == {
        "schema_version": "trajectory.v1",
        "entry_id": "harness:goal-run:run-1:finished",
        "source": "harness",
        "timestamp": "2026-10-21T10:00:06+00:00",
        "actor": "harness-automaton",
        "action": "goal_finished",
        "status": "ok",
        "summary": "g-1 completed (exit 0)",
        "conversation_id": CONV,
        "subagent": None,
        "goal_id": "g-1",
        "run_id": "run-1",
        "owner": None,
        "receipt": {"kind": "goal_run", "id": "run-1", "href": "/v1/goal/run-1"},
    }
