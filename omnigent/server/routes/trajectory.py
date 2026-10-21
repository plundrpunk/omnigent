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
  session. A run's artifact is read only from the path the run itself
  recorded (``outcome.artifact_path``) — never the "newest artifact"
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
from omnigent.server.trajectory import (
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

#: ``(session_id) -> receipt | None``; raises when AMS is unreachable.
ReceiptFetcher = Callable[[str], Awaitable[Mapping[str, Any] | None]]

#: ``(entries, scope, per-source status, raw receipts by event_id)``.
_Collected = tuple[
    list[TrajectoryEntry | None],
    TrajectoryScope,
    dict[str, Any],
    dict[str, Mapping[str, Any]],
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


async def _default_receipt_fetcher(session_id: str) -> Mapping[str, Any] | None:
    """Latest AMS receipt for one session id (bridge credentials)."""
    base = ams._base_url()
    if not base:
        raise LookupError("unconfigured")
    key = ams._api_key()
    async with httpx.AsyncClient(timeout=ams._TIMEOUT_SECONDS) as client:
        resp = await client.get(
            f"{base}/api/v1/work-receipts/latest",
            params={"session_id": session_id},
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
        raw_receipts: dict[str, Mapping[str, Any]] = {}
        sources: dict[str, Any] = {}

        # ── Omnigent: the session and its sub-agent children ──
        page = await asyncio.to_thread(
            conversation_store.list_items, conversation_id, limit=_ITEM_LIMIT
        )
        items = _item_dicts(page.data)
        entries += normalize_session_items(items, conversation_id=conversation_id)
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
            child_page = await asyncio.to_thread(
                conversation_store.list_items, child_id, limit=_CHILD_ITEM_LIMIT
            )
            child_items = _item_dicts(child_page.data)
            items += child_items
            entries += normalize_session_items(
                child_items,
                conversation_id=child_id,
                subagent=child.sub_agent_name or child.title or child_id,
            )
        for item in items:
            if item.get("type") == "work_receipt" and isinstance(item.get("event_id"), str):
                raw_receipts[item["event_id"]] = {
                    k: v for k, v in item.items() if k not in _ITEM_ENVELOPE_KEYS
                }
        sources["omnigent"] = {
            "status": "ok",
            "count": len(items),
            "truncated": bool(page.has_more),
        }

        # ── Harness: goal runs started from this session ──
        runs = goal_registry.list(conversation_id) if goal_registry is not None else []
        for run in runs:
            entries += normalize_goal_run(run)
            artifact = _read_artifact(run)
            if artifact is not None:
                entries += normalize_harness_artifact(
                    artifact,
                    run_id=run.get("run_id"),
                    goal_id=run.get("goal_id"),
                    conversation_id=conversation_id,
                )
            # Fleet runs (AMS dispatch) may already carry their receipt.
            inline = run.get("work_receipt")
            if isinstance(inline, Mapping) and isinstance(inline.get("event_id"), str):
                raw_receipts[inline["event_id"]] = inline
                entries.append(
                    normalize_work_receipt(inline, conversation_id=conversation_id, source="ams")
                )
        sources["harness"] = {
            "status": "ok" if goal_registry is not None else "unconfigured",
            "count": len(runs),
        }

        # ── AMS: receipts for this session's own ids ──
        session_ids = [
            conversation_id,
            *sorted(
                {f"goal-queue:{r['goal_id']}" for r in runs if isinstance(r.get("goal_id"), str)}
            ),
        ]
        ams_status = "ok"
        ams_count = 0
        for session_id in session_ids:
            try:
                receipt = await fetch_receipt(session_id)
            except LookupError:
                ams_status = "unconfigured"
                break
            except Exception as exc:  # noqa: BLE001 - any failure is "unreachable"
                logger.info("trajectory: AMS receipt lookup failed: %s", type(exc).__name__)
                ams_status = "unreachable"
                break
            if receipt is None:
                continue
            entry = normalize_work_receipt(receipt, conversation_id=conversation_id, source="ams")
            if entry is None:
                continue
            raw_receipts[str(receipt.get("event_id"))] = receipt
            entries.append(entry)
            ams_count += 1
        sources["ams"] = {"status": ams_status, "count": ams_count}

        scope = TrajectoryScope(
            conversation_ids=frozenset({conversation_id, *child_ids}),
            owners=_ams_owners(user_id),
        )
        return entries, scope, sources, raw_receipts

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
        entries, scope, _, raw_receipts = await _collect(conversation_id, request)
        visible = {
            e.receipt.id
            for e in merge_trajectory(entries, scope)
            if e.receipt is not None and e.receipt.kind == "work_receipt"
        }
        receipt = raw_receipts.get(event_id)
        if receipt is None or event_id not in visible:
            raise HTTPException(status_code=404, detail="Receipt not found in this session")
        return receipt

    return router
