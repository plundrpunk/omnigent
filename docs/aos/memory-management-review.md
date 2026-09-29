# Memory & Handoffs — rebased result, live access gated

The rescued PR #7 change is rebased locally onto `fork/main` at
`5c3d02910cf390b106728cf61f2dd12fce9727ef` (September 29, 2026).
The original PR head is `80abaf831cad2384114217709b297f27e4ebcce6`,
with parent `f87e471b5623bd7121411383ed84d93b6e8be44c`.
Only that rescued commit was replayed; the 55 unrelated AOS baseline
commits are excluded. GitHub PR #7 is still draft and has not been updated.

## Conflict resolution

- Follow main's `ap-web` → `web` rename for the five new frontend files.
- Register `/memory` using main's lazy route and analytics wrapper.
- Main removed Advanced, so expose Memory & Handoffs in the existing sidebar,
  preserve embedded routing, and avoid highlighting New session on `/memory`.
- Use main's `authenticatedFetch` transport and current lint/format conventions.
- Preserve the current AMS router signature, auth helper, admin checks, startup
  check and explicit write table. Insert management denial before its catch-all.
- No unrelated AOS pages, server changes, dependencies or lockfile edits.

## Behavior and decisions

One page combines Memories and Continuations tabs. Synthetic frontend tests
exercise inspection, memory archive/permanent-delete confirmation, cancellation,
duplicate submission, stale detail responses, empty/error retry and HTTP 204.
Continuation removal is absent.

Live management remains unavailable. `/v1/ams/management/status` reports
`available: false`; record reads and mutations deny access without contacting
AMS. A configured shared key, forged `user_id`, or a different authenticated
caller does not bypass the gate. There is no enable switch or fallback route.

Before enabling access, decide and implement a trusted AOS-principal → AMS-owner
binding, deny unmapped callers, and prove real two-user isolation. Synthetic
denial checks are not proof of working owner-scoped access. Continuation removal
still requires a choice between reversible archive (with separately approved
schema work) and permanent delete, plus a supported selected-record API.

## Validation — September 29, 2026

- PASS: 95 frontend tests across management page/transport, sidebar and App.
- PASS: frontend type check, whole-web oxlint and production build.
- PASS: 43 backend tests across management denial, bridge startup and existing
  write-table regression. Six management cases include real auth-helper checks,
  unauthenticated denial, two synthetic callers, forged owner IDs, configured
  shared credentials and no outbound requests.
- PASS: original checkout HEAD/status unchanged; all nine dirty files hash-match
  the preflight receipt. The original rescued worktree remains unchanged.
- PASS: all applicable pre-commit hooks, including Ruff, Pyrefly, frontend
  formatting/lint/type check, test-quality lint and file hygiene.
- PASS: `git diff --check`.
- NOT TESTED: running browser primary journey, mobile layout, screenshots,
  oversized rendered content and browser console/network inspection. A fresh
  loopback bind probe failed with `PermissionError: [Errno 1] Operation not
  permitted`; no elevated server retry or live-management probe was made.
- NOT TESTED: live ownership mapping, live reads/writes or production acceptance.

The build reports existing `::highlight` CSS optimization and large-chunk
warnings. Backend tests report an existing Starlette TestClient deprecation.
Python plugin autoload is disabled to avoid the socket-opening rerun plugin;
required asyncio, Playwright config and timeout plugins are loaded explicitly.
The isolated backend environment uses main's unchanged lockfile, dev group and
existing `all` extra, with `OMNIGENT_SKIP_WEB_UI=true` for package installation.
The frontend is built separately with the installed matching-main dependency
snapshot. No package manifests or lockfiles changed.

## Verify locally

Worktree: `/Users/drfoundryos/Documents/DevFolder/_worktrees/omnigent/pr7-memory-main-20260929`

From `web`:

```sh
npm run test -- src/pages/MemoryManagementPage.test.tsx src/lib/memoryManagement.test.ts src/shell/Sidebar.test.tsx src/App.test.tsx
npm run type-check
npm run lint
npm run build
```

From the repository root:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -o addopts='' -p pytest_asyncio.plugin -p pytest_playwright_visual_snapshot.plugin -p pytest_playwright.pytest_playwright -p pytest_timeout tests/server/routes/test_ams_write_routes.py tests/server/routes/test_ams_management_gate.py tests/server/routes/test_ams_startup_check.py -q
```

In an environment permitting local preview, open `/memory` through the sidebar
and confirm “Live management unavailable” appears with no memory/continuation
list requests. Check mobile stacking and browser console/network errors.

Review the local branch against `fork/main`; do not merge or deploy this result.
Local receipts: `/Users/drfoundryos/Documents/DevFolder/_artifacts/omnigent-pr7-rebase-20260929/`.
