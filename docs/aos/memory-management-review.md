# AOS Memory & Handoffs — local implementation, live access gated

Route: `/memory`, reached from Advanced → Memory & Handoffs. One page with Memories and Continuations tabs. Uses existing AOS theme variables, Radix tabs/dialog, a 1.61 spacing/type scale, 44px controls and responsive stacked panels.

## Implemented

- Memory browsing with supported tier/status filters and 25-record pagination; explicit loaded-title/ID filtering, not full-text or hybrid search.
- Full content and all returned metadata/timestamps; selection response ordering prevents late responses replacing the current inspection.
- Selected memory archive confirmation, separate permanent deletion requiring the exact ID, duplicate-submit guard, cancellation, and retained errors. HTTP 204 and `soft_delete=true/false` handled explicitly.
- Pending continuation list (up to 50), exact project filter, goal/next action/notes/blockers/subtasks/metadata inspection. No claim, resume or unsupported removal actions.
- Dedicated `/v1/ams/management/*` namespace fails closed on the server. Status reports unavailable; record reads and mutations return 503 without contacting AMS. No switch, credential fallback or browser flag enables it.

## Identity gate

Source inspection: existing `require_user` identifies AOS callers, but the generic AMS bridge forwards an environment-wide `AMS_API_KEY`. AMS resolves API-key ownership separately. The local `com.drf.omnigent` LaunchAgent environment contains `AMS_BASE_URL` and `AMS_API_KEY`, with no caller-to-owner binding. No secret values were printed and no live record queries were made.

Missing contract: a trusted, server-verified association between the authenticated AOS principal and the AMS `user_id` of every read/write. A shared key or DEFAULT_USER_ID is not that proof. Before enabling forwarding, implement/verify that contract, deny unmapped callers and forged IDs, and prove two distinct users cannot access one another's records. The current denial tests prove only fail-closed behavior, not working multi-user isolation.

Continuation removal is separately blocked on the user's pending choice: reversible archive with approved schema work versus permanent deletion with confirmation. Existing selected removal API does not exist. No schema or lifecycle changes are included.

## Validation receipts (2026-09-18)

- PASS: 9 Vitest checks, including access denial/no list, inspection/cancel no mutation, exact-ID confirmation, single submission, archive failure, stale detail responses, load failure vs empty retry, continuation inspection/no removal, 204/query semantics and no fallback.
- PASS: 3 pytest cases for no outbound requests from the new namespace for single-user, owner A and owner B, including forged IDs and read/write verbs. Synthetic identities only.
- PASS: `npm run build` (includes TypeScript build); existing large bundle warning.
- PASS: focused frontend oxlint; Python ruff with `RUF100` excluded because the existing file has two pre-existing unused-noqa findings unrelated to this change.
- PASS: `git diff --check`.
- NOT TESTED: running browser journey, mobile layout, visual/contrast/accessibility inspection, console/network QA, large content rendering, live management and real two-user acceptance.

Browser QA was attempted using a synthetic loopback-only server. Sandbox denied its port bind; automatic approval review rejected elevated retry because standing policy forbids that retry. No alternative launch or bypass was attempted. Approval resolution is required before browser QA resumes.

## Run next

From `ap-web`: `npm run test -- src/pages/MemoryManagementPage.test.tsx src/lib/memoryManagement.test.ts`, then `npm run build`.

Backend focused check from repository root, using installed Python dependencies:

```
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -o addopts='' tests/server/routes/test_ams_management_gate.py -q --noconftest
```

Autoload is disabled because the installed rerun plugin opens a socket during initialization (denied in this sandbox). These tests do not use that plugin; harmless unknown plugin-configuration warnings remain.

Fixture server (requires resolved launch approval): `/private/tmp/aos-management-brief-20260918/management_fixture.py`; serves the actual built AOS from this worktree on `127.0.0.1:6781`, with synthetic records only. Modes in adjacent `management-mode`: normal, empty, error, denied, slow, write-error. Writes affect only synthetic in-process records. Request receipts go to adjacent `management-requests.jsonl`.

## Integration boundary

Worktree `/Users/drfoundryos/Documents/DevFolder/_worktrees/omnigent/aos-memory-management-20260918`, branch `codex/aos-memory-management-20260918`, baseline `f87e471b5623bd7121411383ed84d93b6e8be44c`. Fresh fork fetch showed 2769 behind / 55 ahead of fork/main. This requested AOS runtime baseline is base/scope dirty relative to upstream (148 unrelated baseline files), while the new working diff is limited to this page. Do not open a broad upstream PR from this branch. Apply the scoped patch to the appropriate AOS integration base after its owner confirms that base. No commit, push, PR, migration, dependency install, deployment or real deletion was performed.
