# Memory & Handoffs — private default account

This local branch prepares Drew's private layer in `plundrpunk/omnigent`.
It does not target the upstream `omnigent-ai/omnigent` repository. PR #7
remains draft and unchanged remotely. No production service or credential
has been changed.

The rescued UI was adapted onto fork main
`5c3d02910cf390b106728cf61f2dd12fce9727ef` in local commit
`a67c34ae2722fe4a537ed2546c939dd5f377fec0`. Only the Memory & Handoffs
change was retained: frontend paths use `web/`, the current sidebar and
authenticated transport, and the existing bridge security checks remain.

## Private account behavior

The operator has explicitly confirmed that the existing default AMS account
is his, except for accounts prepared separately. The unconditional 503 gate
has therefore been replaced with a bounded private-operator path:

- Authentication still runs first. A missing auth provider cannot enable this
  surface. An unauthenticated request in strict header/accounts mode gets 401.
- The existing explicit single-user runtime's reserved `local` identity can
  use the configured AMS connection. It is emitted by the auth provider;
  supplying `local` through an identity header or session cookie is rejected
  by existing authentication. The Mac's existing DB has that `local` admin
  identity, and its launch configuration supplies the AMS connection.
- For named accounts, only the operator username resolved by the existing
  first-admin bootstrap convention can use this default connection, and it
  must pass the existing admin permission check. That convention uses
  `OMNIGENT_ACCOUNTS_INIT_ADMIN_USERNAME`, otherwise the server OS username.
  A different account, even another admin, is denied; it never defaults to
  Drew's AMS tenant. Existing separate-account configuration is untouched.
- The configured server-side AMS key supplies the tenant identity already
  understood by AMS. The AMS backend resolves configured key mappings first,
  otherwise its private default tenant; record queries are owner-scoped.
  The browser cannot supply an owner override, credentials, or a request body.
- `GET /v1/ams/management/status` reports whether the authorized operator's
  AMS URL and key are configured. It does not probe AMS or claim reachability.

The exact management table permits listing active/archived memories by tier,
reading a selected memory, listing pending continuations by project, reading
a selected continuation, and deleting a selected memory with an explicit
`soft_delete=true` (archive) or `false` (permanent deletion). Record IDs must
be UUIDs; queries are validated. Upstream errors, including owner-scoped 404s,
are preserved. A successful deletion returns an empty HTTP 204.

The page retains its existing confirmation dialogs. Permanent memory deletion
requires the exact memory ID. Continuations can be inspected only: claim,
complete, removal, and bulk cleanup are outside this change.

## Verification

All forwarding tests use a synthetic key and mocked AMS transport. No live
AMS data is read or mutated, no provider request is made, and no dependency
is installed. The tests cover the existing local identity, actual signed
accounts cookies, another admin, separately prepared accounts, absent auth,
owner override attempts, selected-record archive/delete, tenant-scoped 404s,
unlisted actions, missing connection configuration, and preserved bridge checks.

From the repository root, using the existing environment:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -o addopts='' -p no:cacheprovider -p pytest_asyncio.plugin -p pytest_playwright_visual_snapshot.plugin -p pytest_playwright.pytest_playwright -p pytest_timeout tests/server/routes/test_ams_management_gate.py tests/server/routes/test_ams_write_routes.py tests/server/routes/test_ams_startup_check.py -q
```

From `web`, using existing installed dependencies:

```sh
npm run test -- src/pages/MemoryManagementPage.test.tsx src/lib/memoryManagement.test.ts src/shell/Sidebar.test.tsx src/App.test.tsx
npm run type-check
npm run lint
npm run build
```

After separately approving installation of this private layer, sign in as the
operator and open Memory & Handoffs. Verify active/archived memory filters,
selected-record inspection, and pending continuation notes. A separately
prepared account should see the private-operator denial. Do not use real
archive/delete actions as a smoke test.

This is a locally prepared change. Publishing to Drew's fork and installing
or deploying it require separate approval. Browser/mobile QA and live AMS
acceptance have not been performed in this task.
