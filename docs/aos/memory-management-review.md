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
  use the connection only when the CLI supplies an actual loopback listen
  host and the request has loopback server/peer socket addresses. A missing
  or wildcard listen host fails closed. The same local identity must pass
  the existing admin permission store; a missing store or denying store fails.
- Host must identify loopback and the actual socket port. Browser Origin must
  match that Host and scheme exactly; the existing SDK's internal sentinel
  is accepted only within the same verified loopback boundary. Cross-site
  Fetch Metadata, forwarding/proxy headers, and mismatched or hostile Origins
  are rejected before any record request. This is a request-level policy,
  not a claim that browser CSRF or DNS-rebinding behavior has been proven.
- Reserved `local` supplied through an identity header or signed session
  cookie is rejected by existing authentication. The Mac's existing DB has
  the real local admin row; its launch configuration binds `127.0.0.1`.
- All named accounts, including other admins, remain denied. The repository
  has no persisted trusted operator account ID binding for this connection;
  OS usernames and bootstrap environment names are not used as evidence.
  No additional field is needed for the verified local path. Named-account
  support would require that specific persisted operator binding, separately.
- Alternate `/v1/ams/api/v1/memories` reads and memory search use the same
  private-account boundary. Another account cannot bypass the management
  namespace to use the default key. Caller-supplied owner overrides are
  rejected. Existing separately scoped account settings remain untouched.
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
accounts cookies, real app/store wiring, hostile Origin and proxy requests,
non-loopback and unknown listen hosts, a denying local admin store, another
admin, separately prepared accounts, absent auth,
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

After separately approving installation of this private layer, open Memory &
Handoffs on the operator’s direct local instance. Verify active/archived memory filters,
selected-record inspection, and pending continuation notes. A separately
prepared account should see the private-operator denial. Do not use real
archive/delete actions as a smoke test.

This is a locally prepared change. Publishing to Drew's fork and installing
or deploying it require separate approval. Browser/mobile QA and live AMS
acceptance have not been performed in this task.
