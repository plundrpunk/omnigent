"""Unified trajectory timeline route (``GET /v1/trajectory/{conversation_id}``).

Read-side only: pulls the session's conversation items (and its
sub-agent children), the Harness goal runs started from that session
plus their artifact events, and AMS ``harness.receipt.v1`` work
receipts, then folds them through :mod:`omnigent.server.trajectory`
into one ``trajectory.v1`` timeline. No producer is touched.

- ``GET /v1/trajectory/{conversation_id}`` — the merged timeline.
  ``?goal_id=`` narrows it to one goal.
- ``GET /v1/trajectory/{conversation_id}/receipts/{event_id}`` — the
  receipt an entry links to, resolved only within that session's scope.

User isolation:

- The caller needs read access to the session (same check as
  ``GET /v1/sessions/{id}/items``); otherwise 404, and nothing —
  not even AMS — is queried.
- Goal runs are included only when their ``conversation_id`` is this
  session and, in multi-user mode, their recorded owner is the caller.
  Legacy ownerless runs are excluded. A run's artifact is read only from
  the path the run itself recorded (``outcome.artifact_path``) — never the "newest artifact"
  fallback, which could belong to someone else's run.
- AMS receipts are fetched only for this session's own ids
  (``<conversation_id>`` and ``goal-queue:<goal_id>`` of its runs), and
  in multi-user mode a receipt survives only when its ``user_id`` is the
  caller or the caller's mapped AMS identity
  (``OMNIGENT_AMS_USER_MAP="alice@example.com=alice,..."``).

AMS reachability is reported per source (``ok`` / ``unconfigured`` /
``unreachable``) so an outage reads as absence, not as "no receipts".
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import LEVEL_READ, RESERVED_USER_LOCAL, AuthProvider
from omnigent.server.routes import ams
from omnigent.server.routes._auth_helpers import require_access, require_user
from omnigent.server.routes.goal import receipt_matches_continuation
from omnigent.server.trajectory import (
    _SOURCE_PRIORITY,
    SCHEMA_VERSION,
    TrajectoryEntry,
    TrajectoryScope,
    merge_trajectory,
    normalize_goal_run,
    normalize_harness_artifact,
    normalize_session_items,
    normalize_work_receipt,
)
from omnigent.stores import ConversationStore
from omnigent.stores.permission_store import PermissionStore

logger = logging.getLogger(__name__)

#: ``(session_id, work_item_id=None) -> receipt | None``; raises when AMS is
#: unreachable. ``work_item_id`` pins a shared session id to one run.
ReceiptFetcher = Callable[..., Awaitable[Mapping[str, Any] | None]]

#: A raw ``harness.receipt.v1`` payload paired with the exact entry it
#: produced, so the receipt endpoint can scope-check the payload it returns.
_ReceiptCandidate = tuple[TrajectoryEntry, Mapping[str, Any]]

#: ``(entries, scope, per-source status, receipt candidates)``.
_Collected = tuple[
    list[TrajectoryEntry | None],
    TrajectoryScope,
    dict[str, Any],
    list[_ReceiptCandidate],
]

_ITEM_LIMIT = 1000
_CHILD_LIMIT = 20
_CHILD_ITEM_LIMIT = 200
_ARTIFACT_MAX_BYTES = 8 * 1024 * 1024
_USER_MAP_ENV = "OMNIGENT_AMS_USER_MAP"


def _ams_owners(user_id: str | None) -> frozenset[str] | None:
    """Tenant ids that count as the caller, or ``None`` in single-user mode."""
    if user_id is None or user_id == RESERVED_USER_LOCAL:
        return None
    owners = {user_id}
    for pair in (os.environ.get(_USER_MAP_ENV) or "").split(","):
        name, sep, ams_user = pair.partition("=")
        if sep and name.strip() == user_id and ams_user.strip():
            owners.add(ams_user.strip())
    return frozenset(owners)


async def _default_receipt_fetcher(
    session_id: str, work_item_id: str | None = None
) -> Mapping[str, Any] | None:
    """Latest AMS receipt for one session id (bridge credentials)."""
    base = ams._base_url()
    if not base:
        raise LookupError("unconfigured")
    key = ams._api_key()
    params = {"session_id": session_id}
    if work_item_id is not None:
        params["work_item_id"] = work_item_id
    async with httpx.AsyncClient(timeout=ams._TIMEOUT_SECONDS) as client:
        resp = await client.get(
            f"{base}/api/v1/work-receipts/latest",
            params=params,
            headers={"X-API-Key": key} if key else {},
        )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    body = resp.json()
    return body if isinstance(body, Mapping) else None


def _read_artifact(run: Mapping[str, Any]) -> dict[str, Any] | None:
    """Read the artifact the run itself recorded; never guess one."""
    outcome = run.get("outcome")
    raw = outcome.get("artifact_path") if isinstance(outcome, Mapping) else None
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        # The harness CLI reports artifact paths relative to the directory
        # it ran in, which the goal bridge sets to HA_GOAL_CWD.
        cwd = os.environ.get("HA_GOAL_CWD") or ""
        if not cwd:
            return None
        path = Path(cwd) / path
    try:
        if not path.is_file() or path.stat().st_size > _ARTIFACT_MAX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A live run rewrites its artifact; a torn read is normal.
        return None
    return data if isinstance(data, dict) else None


def _item_dicts(page_items: list[Any]) -> list[dict[str, Any]]:
    """Flat API shape plus the store timestamp.

    A ``work_receipt`` item keeps its own ``created_at`` (the receipt's
    ISO time); every other item gets the store's epoch seconds.
    """
    out = []
    for item in page_items:
        flat = item.to_api_dict()
        flat.setdefault("created_at", item.created_at)
        out.append(flat)
    return out


def _newest_items(
    conversation_store: ConversationStore, conversation_id: str, limit: int
) -> tuple[list[dict[str, Any]], bool]:
    """The newest ``limit`` items in chronological order, plus "older exist".

    ``list_items`` pages ascending by default, which would pin a capped
    session to its oldest window and hide new activity; fetch the
    descending tail instead and restore chronological order.
    """
    page = conversation_store.list_items(conversation_id, limit=limit, order="desc")
    return list(reversed(_item_dicts(page.data))), bool(page.has_more)


def _session_receipt_candidates(
    items: list[dict[str, Any]], conversation_id: str
) -> list[_ReceiptCandidate]:
    """Pair each session ``work_receipt`` item with the entry it yields.

    Mirrors :func:`normalize_session_items`, which pins a session receipt
    to the session it was read from.
    """
    out: list[_ReceiptCandidate] = []
    for item in items:
        if item.get("type") != "work_receipt":
            continue
        payload = {k: v for k, v in item.items() if k not in _ITEM_ENVELOPE_KEYS}
        entry = normalize_work_receipt(item, conversation_id=conversation_id, source="omnigent")
        if entry is not None:
            out.append((dataclasses.replace(entry, conversation_id=conversation_id), payload))
    return out


#: Item envelope keys that are not part of a ``harness.receipt.v1``.
_ITEM_ENVELOPE_KEYS = frozenset({"id", "response_id", "type", "created_by"})


def create_trajectory_router(
    conversation_store: ConversationStore,
    *,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
    goal_registry: Any | None = None,
    receipt_fetcher: ReceiptFetcher | None = None,
) -> APIRouter:
    """Build the trajectory router.

    :param conversation_store: Source of session items.
    :param auth_provider: Identifies the caller; ``None`` = single-user.
    :param permission_store: Session access grants; ``None`` disables
        enforcement (single-user mode).
    :param goal_registry: The ``/v1/goal`` run registry
        (:class:`omnigent.server.routes.goal.GoalRunRegistry`), or
        ``None`` when the goal bridge is not mounted.
    :param receipt_fetcher: AMS receipt lookup; injectable for tests.
    """
    router = APIRouter()
    fetch_receipt = receipt_fetcher or _default_receipt_fetcher

    async def _collect(conversation_id: str, request: Request) -> _Collected:
        user_id = require_user(request, auth_provider)
        await require_access(
            user_id, conversation_id, LEVEL_READ, permission_store, conversation_store
        )
        conv = await asyncio.to_thread(conversation_store.get_conversation, conversation_id)
        if conv is None:
            raise OmnigentError("Session not found", code=ErrorCode.NOT_FOUND)

        entries: list[TrajectoryEntry | None] = []
        receipt_candidates: list[_ReceiptCandidate] = []
        sources: dict[str, Any] = {}

        # ── Omnigent: the session and its sub-agent children ──
        items, truncated = await asyncio.to_thread(
            _newest_items, conversation_store, conversation_id, _ITEM_LIMIT
        )
        entries += normalize_session_items(items, conversation_id=conversation_id)
        receipt_candidates += _session_receipt_candidates(items, conversation_id)
        item_count = len(items)
        child_ids = (
            await asyncio.to_thread(
                conversation_store.list_child_conversation_ids_by_parent, [conversation_id]
            )
        ).get(conversation_id, [])[:_CHILD_LIMIT]
        children = (
            await asyncio.to_thread(conversation_store.get_conversations, child_ids)
            if child_ids
            else {}
        )
        for child_id in child_ids:
            child = children.get(child_id)
            if child is None or child.parent_conversation_id != conversation_id:
                continue
            child_items, child_truncated = await asyncio.to_thread(
                _newest_items, conversation_store, child_id, _CHILD_ITEM_LIMIT
            )
            item_count += len(child_items)
            truncated = truncated or child_truncated
            entries += normalize_session_items(
                child_items,
                conversation_id=child_id,
                subagent=child.sub_agent_name or child.title or child_id,
            )
            receipt_candidates += _session_receipt_candidates(child_items, child_id)
        sources["omnigent"] = {
            "status": "ok",
            "count": item_count,
            "truncated": truncated,
        }

        # ── Harness: goal runs started from this session ──
        runs = goal_registry.list(conversation_id) if goal_registry is not None else []
        if user_id is not None and user_id != RESERVED_USER_LOCAL:
            # Authorize before reading artifacts, inline receipts, or deriving
            # AMS lookup keys. AMS aliases apply only to receipt ownership.
            runs = [run for run in runs if run.get("owner") == user_id]
        for run in runs:
            entries += normalize_goal_run(run)
            artifact = await asyncio.to_thread(_read_artifact, run)
            if artifact is not None:
                entries += normalize_harness_artifact(
                    artifact,
                    run_id=run.get("run_id"),
                    goal_id=run.get("goal_id"),
                    conversation_id=conversation_id,
                )
            # Fleet runs (AMS dispatch) may already carry their receipt.
            inline = run.get("work_receipt")
            inline_entry = normalize_work_receipt(
                inline, conversation_id=conversation_id, source="ams"
            )
            if inline_entry is not None and isinstance(inline, Mapping):
                receipt_candidates.append((inline_entry, inline))
                entries.append(inline_entry)
        sources["harness"] = {
            "status": "ok" if goal_registry is not None else "unconfigured",
            "count": len(runs),
        }

        # ── AMS: receipts for this session's own ids ──
        # ``goal-queue:<goal_id>`` is keyed by a caller-chosen goal id, so its
        # latest receipt may be another run's (an earlier run, or another user
        # sharing the AMS bridge key). Keep it only when it names the
        # continuation of one of this session's own fleet runs.
        goal_continuations: dict[str, set[str]] = {}
        for r in runs:
            if isinstance(r.get("goal_id"), str):
                ids = goal_continuations.setdefault(f"goal-queue:{r['goal_id']}", set())
                if isinstance(r.get("continuation_id"), str):
                    ids.add(r["continuation_id"])
        lookups: list[tuple[str, str | None]] = [(conversation_id, None)]
        for session_id in sorted(goal_continuations):
            # Only fleet runs (with a continuation) can own a goal-queue receipt.
            lookups += [(session_id, cid) for cid in sorted(goal_continuations[session_id])]
        ams_status = "ok"
        ams_count = 0
        for session_id, work_item_id in lookups:
            try:
                receipt = (
                    await fetch_receipt(session_id, work_item_id=work_item_id)
                    if work_item_id is not None
                    else await fetch_receipt(session_id)
                )
            except LookupError:
                ams_status = "unconfigured"
                break
            except Exception as exc:  # noqa: BLE001 - any failure is "unreachable"
                logger.info("trajectory: AMS receipt lookup failed: %s", type(exc).__name__)
                ams_status = "unreachable"
                break
            if receipt is None:
                continue
            if work_item_id is not None and not receipt_matches_continuation(
                receipt, work_item_id
            ):
                continue
            entry = normalize_work_receipt(receipt, conversation_id=conversation_id, source="ams")
            if entry is None:
                continue
            receipt_candidates.append((entry, receipt))
            entries.append(entry)
            ams_count += 1
        sources["ams"] = {"status": ams_status, "count": ams_count}

        scope = TrajectoryScope(
            conversation_ids=frozenset({conversation_id, *child_ids}),
            owners=_ams_owners(user_id),
        )
        return entries, scope, sources, receipt_candidates

    @router.get("/trajectory/{conversation_id}")
    async def get_trajectory(
        conversation_id: str,
        request: Request,
        goal_id: str | None = Query(default=None, max_length=128),
    ) -> dict[str, Any]:
        """The merged, scoped, chronological timeline for one session."""
        entries, scope, sources, _ = await _collect(conversation_id, request)
        candidates = [e for e in entries if e is not None]
        merged = merge_trajectory(candidates, scope)
        dropped = sum(1 for e in candidates if not scope.allows(e))
        if goal_id is not None:
            merged = [e for e in merged if e.goal_id == goal_id]
        return {
            "schema_version": SCHEMA_VERSION,
            "conversation_id": conversation_id,
            "goal_id": goal_id,
            "entries": [e.to_dict() for e in merged],
            "sources": sources,
            "scope": {"dropped": dropped},
        }

    @router.get("/trajectory/{conversation_id}/receipts/{event_id}")
    async def get_trajectory_receipt(
        conversation_id: str, event_id: str, request: Request
    ) -> Mapping[str, Any]:
        """The receipt behind a timeline entry, only if it is in scope."""
        _, scope, _, candidates = await _collect(conversation_id, request)
        # Scope-check each payload through the entry it produced: two sources
        # may report the same event_id, and only an in-scope copy may be
        # returned (never a foreign payload that merely shares the id).
        allowed = [
            (entry, payload)
            for entry, payload in candidates
            if entry.receipt is not None
            and entry.receipt.kind == "work_receipt"
            and entry.receipt.id == event_id
            and scope.allows(entry)
        ]
        if not allowed:
            raise HTTPException(status_code=404, detail="Receipt not found in this session")
        # Same preference as merge_trajectory's dedupe: the AMS copy wins.
        _, payload = min(allowed, key=lambda pair: _SOURCE_PRIORITY.get(pair[0].source, 9))
        return payload

    return router
