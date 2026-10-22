"""Unified trajectory timeline (``trajectory.v1``) — a read-side adapter.

One chronological timeline for a session or goal, merged from three
event streams that today live apart:

- **omnigent** — the session's own conversation items (user turns,
  assistant turns, tool calls, sub-agent dispatch, errors, receipts),
  including the items of its sub-agent child sessions.
- **harness** — Harness Automaton goal runs started through
  ``/v1/goal`` (run lifecycle) and the events the harness wrote to the
  run's artifact JSON (``ArtifactEvent{id, created_at, role, content,
  metadata.phase}``).
- **ams** — ``harness.receipt.v1`` work receipts held by the Automaton
  Memory System.

Everything here is pure: callers hand in already-fetched records and
get back :class:`TrajectoryEntry` values. No producer is changed, no
I/O happens, and nothing is inferred — a missing timestamp stays
missing (``None``, sorted last) instead of being invented.

Content rules: reasoning items, raw model output (``raw_content``) and
chain-of-thought never reach a summary. Summaries are clipped.

Isolation: :func:`merge_trajectory` drops every entry outside a
:class:`TrajectoryScope` — entries from conversations the caller cannot
read, and entries whose tenant ``owner`` is not the caller.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

SCHEMA_VERSION = "trajectory.v1"

TrajectorySource = Literal["omnigent", "harness", "ams"]
TrajectoryStatus = Literal["ok", "failed", "blocked", "running", "paused", "info"]

#: Summary cap. The timeline says what happened; the receipt link
#: carries the detail.
SUMMARY_CAP = 240

#: Tools whose call hands work to another agent session.
DISPATCH_TOOLS = frozenset({"sys_session_send", "sys_session_create", "Task", "Agent"})

#: Item types that are transcript plumbing, private reasoning, or
#: carry no actor-visible action. Never surfaced.
_SKIPPED_ITEM_TYPES = frozenset(
    {"reasoning", "compaction", "resource_event", "function_call_output"}
)

#: When two sources hold the same receipt, the system of record wins.
_SOURCE_PRIORITY: dict[str, int] = {"ams": 0, "harness": 1, "omnigent": 2}

#: Tie-break for equal timestamps: the session action that caused work,
#: then the harness doing it, then the receipt recording it.
_SOURCE_ORDER: dict[str, int] = {"omnigent": 0, "harness": 1, "ams": 2}

_RECEIPT_STATUS: dict[str, TrajectoryStatus] = {
    "completed": "ok",
    "complete": "ok",
    "failed": "failed",
    "blocked": "blocked",
    "running": "running",
}

_GOAL_RUN_STATUS: dict[str, TrajectoryStatus] = {
    "completed": "ok",
    "blocked": "blocked",
    "paused": "paused",
    "setup_error": "failed",
    "error": "failed",
    "running": "running",
    "queued": "running",
}

_ITEM_STATUS: dict[str, TrajectoryStatus] = {
    "completed": "ok",
    "in_progress": "running",
    "incomplete": "failed",
    "failed": "failed",
}

_GOOD_VERDICTS = frozenset({"pass", "passed", "accept", "proceed", "ok"})
_BAD_VERDICTS = frozenset({"fail", "failed", "reject", "revise", "block", "blocked"})


@dataclass(frozen=True)
class ReceiptRef:
    """Where the evidence behind an entry can be read.

    :param kind: ``"work_receipt"`` (a ``harness.receipt.v1``),
        ``"goal_run"`` (the ``/v1/goal`` run record, which names the
        artifact), or ``"session_item"``.
    :param id: The receipt's own id, e.g. a receipt ``event_id``.
    :param href: Same-origin API path that returns it, or ``None``.
    """

    kind: str
    id: str
    href: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "href": self.href}


@dataclass(frozen=True)
class TrajectoryEntry:
    """One row of the unified timeline (wire schema ``trajectory.v1``).

    :param entry_id: Stable id, ``<source>:<scope>:<native id>``.
    :param source: ``"omnigent"``, ``"harness"`` or ``"ams"``.
    :param timestamp: ISO-8601 UTC, or ``None`` when the source did not
        record one.
    :param actor: Who acted — agent name, harness role, human author or
        receipt runner.
    :param action: What happened, e.g. ``"tool_call"``, ``"dispatch"``,
        ``"goal_started"``, ``"verify"``, ``"work_receipt"``.
    :param status: Normalised outcome.
    :param summary: Clipped one-line description.
    :param conversation_id: Session the entry belongs to (sub-agent
        entries carry the child session id).
    :param subagent: Sub-agent name when the entry came from a child
        session.
    :param goal_id: Goal contract id, when known.
    :param run_id: ``/v1/goal`` run id, when known.
    :param owner: Tenant user id recorded by the source (receipt
        ``user_id``); ``None`` when the source records none.
    :param receipt: Link to the supporting record, or ``None``.
    :param dedupe_key: Entries sharing a key are one event.
    :param sequence: Source-local order, used to break timestamp ties.
    """

    entry_id: str
    source: TrajectorySource
    timestamp: str | None
    actor: str
    action: str
    status: TrajectoryStatus
    summary: str
    conversation_id: str | None
    subagent: str | None = None
    goal_id: str | None = None
    run_id: str | None = None
    owner: str | None = None
    receipt: ReceiptRef | None = None
    dedupe_key: str | None = None
    sequence: int = 0
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the ``trajectory.v1`` wire shape."""
        return {
            "schema_version": self.schema_version,
            "entry_id": self.entry_id,
            "source": self.source,
            "timestamp": self.timestamp,
            "actor": self.actor,
            "action": self.action,
            "status": self.status,
            "summary": self.summary,
            "conversation_id": self.conversation_id,
            "subagent": self.subagent,
            "goal_id": self.goal_id,
            "run_id": self.run_id,
            "owner": self.owner,
            "receipt": self.receipt.to_dict() if self.receipt else None,
        }


@dataclass(frozen=True)
class TrajectoryScope:
    """What the caller is allowed to see.

    :param conversation_ids: Sessions the caller can read (the
        requested session and its sub-agent children).
    :param owners: Tenant ids that count as the caller. ``None`` means
        single-user mode: no owner constraint.
    """

    conversation_ids: frozenset[str]
    owners: frozenset[str] | None = None

    def allows(self, entry: TrajectoryEntry) -> bool:
        if entry.conversation_id is None or entry.conversation_id not in self.conversation_ids:
            return False
        if self.owners is not None:
            # AMS records lack the session store's permission boundary;
            # missing ownership must fail closed in multi-user mode.
            if entry.source == "ams" or entry.owner is not None:
                return entry.owner in self.owners
        return True


# ── Helpers ──────────────────────────────────────────────────────────


def clip(value: Any, cap: int = SUMMARY_CAP) -> str:
    """Collapse whitespace and cap length."""
    text = " ".join(str(value or "").split())
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def to_iso(value: Any) -> str | None:
    """Normalise an epoch (s or ms) or ISO-8601 string to ISO UTC.

    :returns: ISO-8601 with ``+00:00``, or ``None`` when unparseable.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if value > 1e12 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat()
    return None


def _epoch(iso: str | None) -> float | None:
    if iso is None:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _json_object(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def receipt_href(conversation_id: str | None, event_id: str) -> str | None:
    """API path that resolves a receipt within a session's scope."""
    if not conversation_id:
        return None
    return f"/v1/trajectory/{conversation_id}/receipts/{event_id}"


def goal_run_href(run_id: str) -> str:
    return f"/v1/goal/{run_id}"


# ── Omnigent session items ───────────────────────────────────────────


def _message_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts = [
        part.get("text", "")
        for part in content
        if isinstance(part, Mapping)
        and part.get("type") in ("input_text", "output_text")
        and isinstance(part.get("text"), str)
    ]
    return " ".join(parts)


def normalize_session_items(
    items: Iterable[Mapping[str, Any]],
    *,
    conversation_id: str,
    subagent: str | None = None,
) -> list[TrajectoryEntry]:
    """Normalise Omnigent conversation items.

    :param items: Items in the flat API shape (``to_api_dict()``) plus
        ``created_at`` (epoch seconds), in conversation order.
    :param conversation_id: Session the items belong to.
    :param subagent: Sub-agent name when these are a child session's
        items.
    """
    entries: list[TrajectoryEntry] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            continue
        item_type = item.get("type")
        item_id = _str(item.get("id"))
        if not item_id or item_type in _SKIPPED_ITEM_TYPES:
            continue
        timestamp = to_iso(item.get("created_at"))
        entry_id = f"omnigent:{conversation_id}:{item_id}"
        agent = _str(item.get("model")) or _str(item.get("agent"))
        item_status = _ITEM_STATUS.get(str(item.get("status") or ""), "info")
        common: dict[str, Any] = {
            "entry_id": entry_id,
            "source": "omnigent",
            "timestamp": timestamp,
            "conversation_id": conversation_id,
            "subagent": subagent,
            "sequence": index,
        }

        if item_type == "message":
            if item.get("is_meta"):
                continue
            role = item.get("role")
            if role == "user":
                entries.append(
                    TrajectoryEntry(
                        actor=_str(item.get("created_by")) or "user",
                        action="user_message",
                        status="info",
                        summary=clip(_message_text(item.get("content"))),
                        **common,
                    )
                )
            else:
                entries.append(
                    TrajectoryEntry(
                        actor=agent or subagent or "agent",
                        action="assistant_message",
                        status="failed" if item.get("interrupted") else "info",
                        summary=clip(_message_text(item.get("content"))),
                        **common,
                    )
                )
        elif item_type == "function_call":
            name = _str(item.get("name")) or "tool"
            args = item.get("arguments")
            entries.append(
                TrajectoryEntry(
                    actor=agent or subagent or "agent",
                    action="dispatch" if name in DISPATCH_TOOLS else "tool_call",
                    status=item_status,
                    summary=clip(f"{name} {clip(args, 160)}" if args else name),
                    **common,
                )
            )
        elif item_type == "native_tool":
            native = _mapping(item.get("item"))
            entries.append(
                TrajectoryEntry(
                    actor=agent or subagent or "agent",
                    action="native_tool",
                    status=_ITEM_STATUS.get(str(native.get("status") or ""), item_status),
                    summary=clip(native.get("type") or "native tool"),
                    **common,
                )
            )
        elif item_type == "slash_command":
            entries.append(
                TrajectoryEntry(
                    actor=agent or subagent or "agent",
                    action="slash_command",
                    status="info",
                    summary=clip(f"/{item.get('name') or ''} {item.get('arguments') or ''}"),
                    **common,
                )
            )
        elif item_type == "terminal_command":
            if item.get("kind") != "input":
                continue
            entries.append(
                TrajectoryEntry(
                    actor=_str(item.get("created_by")) or "user",
                    action="terminal_command",
                    status="info",
                    summary=clip(item.get("input")),
                    **common,
                )
            )
        elif item_type == "error":
            entries.append(
                TrajectoryEntry(
                    actor=agent or subagent or str(item.get("source") or "system"),
                    action="error",
                    status="failed",
                    summary=clip(f"{item.get('code') or 'error'}: {item.get('message') or ''}"),
                    **common,
                )
            )
        elif item_type == "work_receipt":
            receipt = normalize_work_receipt(
                item, conversation_id=conversation_id, source="omnigent"
            )
            if receipt is None:
                continue
            entries.append(
                TrajectoryEntry(
                    entry_id=entry_id,
                    source="omnigent",
                    timestamp=receipt.timestamp or timestamp,
                    actor=receipt.actor,
                    action=receipt.action,
                    status=receipt.status,
                    summary=receipt.summary,
                    conversation_id=conversation_id,
                    subagent=subagent,
                    goal_id=receipt.goal_id,
                    owner=receipt.owner,
                    receipt=receipt.receipt,
                    dedupe_key=receipt.dedupe_key,
                    sequence=index,
                )
            )
    return entries


# ── Harness goal runs + artifact events ──────────────────────────────


def normalize_goal_run(run: Mapping[str, Any]) -> list[TrajectoryEntry]:
    """Lifecycle entries for one ``/v1/goal`` run record.

    A run yields ``goal_started`` and — once terminal — ``goal_finished``.
    Status comes from the run record only (itself derived from the CLI
    exit code), never from prose.
    """
    run_id = _str(run.get("run_id"))
    if not run_id:
        return []
    goal_id = _str(run.get("goal_id"))
    conversation_id = _str(run.get("conversation_id"))
    status = str(run.get("status") or "")
    ref = ReceiptRef(kind="goal_run", id=run_id, href=goal_run_href(run_id))
    dispatch = _str(run.get("dispatch")) or "local"
    entries = [
        TrajectoryEntry(
            entry_id=f"harness:goal-run:{run_id}:started",
            source="harness",
            timestamp=to_iso(run.get("started_at")),
            actor="omnigent-aos" if dispatch == "fleet" else "harness-automaton",
            action="goal_started",
            status="running",
            summary=clip(f"{goal_id or run_id} dispatched ({dispatch})"),
            conversation_id=conversation_id,
            goal_id=goal_id,
            run_id=run_id,
            receipt=ref,
            sequence=0,
        )
    ]
    if status and status not in ("running", "queued"):
        exit_code = run.get("exit_code")
        detail = f" (exit {exit_code})" if exit_code is not None else ""
        entries.append(
            TrajectoryEntry(
                entry_id=f"harness:goal-run:{run_id}:finished",
                source="harness",
                timestamp=to_iso(run.get("finished_at")),
                actor="harness-automaton",
                action="goal_finished",
                status=_GOAL_RUN_STATUS.get(status, "failed"),
                summary=clip(f"{goal_id or run_id} {status}{detail}"),
                conversation_id=conversation_id,
                goal_id=goal_id,
                run_id=run_id,
                receipt=ref,
                sequence=1,
            )
        )
    return entries


def _verdict_status(verdict: Any) -> TrajectoryStatus:
    text = str(verdict or "").strip().lower()
    if text in _GOOD_VERDICTS:
        return "ok"
    if text in _BAD_VERDICTS:
        return "failed"
    return "info"


def _harness_summary(
    role: str, phase: str, parsed: dict[str, Any] | None
) -> tuple[str, TrajectoryStatus]:
    """Describe one artifact event without leaking model internals."""
    if parsed is None:
        # Free text from a model turn: show only that it happened.
        return clip(f"{role} {phase}".strip()), "info"
    if "tool" in parsed and ("ok" in parsed or "exit_code" in parsed):
        call = _mapping(parsed.get("call"))
        target = call.get("command") or call.get("path") or ""
        ok = bool(parsed.get("ok"))
        exit_code = parsed.get("exit_code")
        tail = f" exit={exit_code}" if exit_code is not None else ""
        return clip(f"{parsed.get('tool')} {clip(target, 160)}{tail}"), "ok" if ok else "failed"
    if parsed.get("blocked") is True:
        return clip(f"blocked: {parsed.get('reason') or ''}"), "blocked"
    verdict = parsed.get("verdict") or parsed.get("action")
    if verdict:
        reason = parsed.get("reason") or ""
        return clip(f"{verdict} {reason}"), _verdict_status(verdict)
    if "done" in parsed and "patch" in parsed:
        return clip(parsed.get("observation") or f"done={parsed.get('done')}"), "info"
    if isinstance(parsed.get("subgoals"), list):
        return clip(f"planned {len(parsed['subgoals'])} subgoal(s)"), "info"
    keys = [str(k) for k in parsed if "reason" not in str(k) and "prompt" not in str(k)]
    return clip(", ".join(keys[:6])), "info"


def normalize_harness_artifact(
    artifact: Mapping[str, Any],
    *,
    run_id: str | None,
    goal_id: str | None,
    conversation_id: str | None,
) -> list[TrajectoryEntry]:
    """Normalise the ``events`` of one harness artifact JSON.

    :param artifact: Parsed ``<workdir>/artifacts/<id>.json``.
    :param run_id: The ``/v1/goal`` run that names this artifact.
    """
    artifact_id = _str(artifact.get("id")) or "artifact"
    events = artifact.get("events")
    if not isinstance(events, list):
        return []
    ref = ReceiptRef(kind="goal_run", id=run_id, href=goal_run_href(run_id)) if run_id else None
    entries: list[TrajectoryEntry] = []
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            continue
        event_id = _str(event.get("id")) or str(index)
        metadata = _mapping(event.get("metadata"))
        role = _str(event.get("role")) or "harness"
        phase = _str(metadata.get("phase")) or role
        parsed = _json_object(event.get("content"))
        summary, status = _harness_summary(role, phase, parsed)
        if phase == "tool_exec" and "ok" in metadata and parsed is None:
            status = "ok" if metadata.get("ok") else "failed"
        entries.append(
            TrajectoryEntry(
                entry_id=f"harness:{artifact_id}:{event_id}",
                source="harness",
                timestamp=to_iso(event.get("created_at")),
                actor=role,
                action=phase,
                status=status,
                summary=summary,
                conversation_id=conversation_id,
                goal_id=goal_id,
                run_id=run_id,
                receipt=ref,
                sequence=index,
            )
        )
    return entries


# ── Work receipts (AMS or session copies) ────────────────────────────


def normalize_work_receipt(
    receipt: Any,
    *,
    conversation_id: str | None,
    source: TrajectorySource = "ams",
) -> TrajectoryEntry | None:
    """Normalise one ``harness.receipt.v1`` work receipt.

    :param conversation_id: Session the receipt was fetched for. A
        receipt that names its own Omnigent origin conversation keeps
        that one instead, so scope checks see where it really belongs.
    :returns: ``None`` when the payload is not a receipt.
    """
    if not isinstance(receipt, Mapping):
        return None
    event_id = _str(receipt.get("event_id"))
    status = _str(receipt.get("status"))
    if not event_id or not status or receipt.get("schema_version") != "harness.receipt.v1":
        return None
    metadata = _mapping(receipt.get("metadata"))
    origin = _mapping(metadata.get("origin"))
    origin_conv = _str(origin.get("omnigent_conversation_id"))
    verifier = _mapping(receipt.get("verifier"))
    verdict = _str(verifier.get("verdict"))
    summary = f"{status} · verifier {verifier.get('status') or 'not reported'}"
    if verdict:
        summary += f" ({verdict})"
    conv = origin_conv or conversation_id
    return TrajectoryEntry(
        entry_id=f"{source}:receipt:{event_id}",
        source=source,
        timestamp=to_iso(receipt.get("created_at")),
        actor=_str(metadata.get("runner")) or "harness-automaton",
        action="work_receipt",
        status=_RECEIPT_STATUS.get(status, "info"),
        summary=clip(summary),
        conversation_id=conv,
        goal_id=_str(metadata.get("goal_id")),
        owner=_str(receipt.get("user_id")),
        receipt=ReceiptRef(kind="work_receipt", id=event_id, href=receipt_href(conv, event_id)),
        dedupe_key=f"receipt:{event_id}",
    )


# ── Merge ────────────────────────────────────────────────────────────


def merge_trajectory(
    entries: Iterable[TrajectoryEntry | None],
    scope: TrajectoryScope,
) -> list[TrajectoryEntry]:
    """Scope-filter, deduplicate and order entries chronologically.

    Ordering: timestamp ascending (untimed last), then source, then the
    source-local sequence — so equal timestamps keep their native order.
    Deduplication: one entry per ``dedupe_key`` (falling back to
    ``entry_id``); for receipts the AMS copy wins.
    """
    chosen: dict[str, TrajectoryEntry] = {}
    for entry in entries:
        if entry is None or not scope.allows(entry):
            continue
        key = entry.dedupe_key or entry.entry_id
        current = chosen.get(key)
        if current is None or (
            _SOURCE_PRIORITY.get(entry.source, 9) < _SOURCE_PRIORITY.get(current.source, 9)
        ):
            chosen[key] = entry

    def sort_key(entry: TrajectoryEntry) -> tuple[int, float, int, int]:
        epoch = _epoch(entry.timestamp)
        return (
            1 if epoch is None else 0,
            epoch or 0.0,
            _SOURCE_ORDER.get(entry.source, 9),
            entry.sequence,
        )

    return sorted(chosen.values(), key=sort_key)
