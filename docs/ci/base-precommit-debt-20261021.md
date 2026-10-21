# Base-branch CI debt: local repair and remaining coverage gate

Scope: `feat/ams-bridge-boot-check` at `f87e471b5`, repaired on
`chore/base-precommit-debt-20261021`. No push, merge, deployment, or live-checkout
file changes. Existing tests, hooks, coverage configuration, and dependency
manifests remain enabled and unchanged.

## Commits

- `4b621439d`: configured Ruff autofixes/formatting, Prettier, trailing whitespace,
  and EOF normalization only (80 tracked files).
- `8034d222f`: remaining fork-owned lint errors. Retains the ACP stderr task and
  adds a focused lifecycle regression test; uses context-managed files/cleanup,
  explicit non-strict zip, and JSON/I/O exception types in diagnostic scripts.
  The heartbeat retains its live token-count closure. Wrapped embedded source
  templates have identical Python ASTs before and after the change.
- `3545fa41b`: regenerate `openapi.json` for six existing routes. No route changes.
- `13a8d26e0`: only pass `--no-experimental-webstorage` to Vitest workers when the
  running Node version supports it.

All commits carry the requested co-author trailer and DCO sign-off.

## Toolchain and reproducibility

Local host: macOS arm64; Python 3.12.12; Node 20.20.2; npm 11.12.1.
GitHub used Ubuntu and Python 3.12.15. These are local results, not a new CI run
or proof of Linux-only behavior. Dependencies were installed from the existing
locks with `uv sync --locked --extra all --extra dev` and
`npm ci --legacy-peer-deps --no-audit --no-fund`. No dependencies were added.

The temporary Node/npm installation used for verification is
`/private/tmp/omnigent-ci-toolchain/node_modules/.bin`. Set it first in `PATH`
for the npm commands below. Python commands use this worktree's `.venv`.
Set `OMNIGENT_SKIP_WEB_UI=true`, `UV_INDEX_URL=https://pypi.org/simple`,
`UV_CACHE_DIR=/private/tmp/omnigent-ci-uv-cache`, and
`UV_PYTHON_INSTALL_DIR=/private/tmp/omnigent-ci-python` for the uv command below.

### Pre-commit checks: PASS locally

```sh
PRE_COMMIT_HOME=/private/tmp/omnigent-ci-pre-commit \
  .venv/bin/pre-commit run --all-files
.venv/bin/python scripts/normalize_uv_lock_registry.py --check uv.lock
(cd ap-web && npm run type-check)
git diff --check
```

All 13 configured hooks pass. TypeScript checking passes. The remaining manual
Ruff violations were in fork-added files, not inherited upstream files.

### Lock freshness: PASS, no changed file to commit

Run twice with CI's exact npm 11.12.1:

```sh
cd ap-web
NPM_CONFIG_REGISTRY=https://registry.npmjs.org/ \
  npm install --package-lock-only --legacy-peer-deps --no-audit --no-fund
git diff --exit-code -- package-lock.json
```

Both refreshes exit successfully with a byte-identical lockfile. Therefore there
is no lockfile commit: manufacturing metadata or version churn would violate
the requested scope. In the supplied lint run, the “out of date” text appears
inside the displayed shell script; the actual freshness step succeeded and
execution proceeded to pre-commit. Existing Node engine warnings are nonfatal.

### Pytest (server-rest): PASS, 1,393 tests

Workflow-equivalent command (same selection, four workers, distribution,
timeout, coverage, and reporting flags):

```sh
mkdir -p artifacts/progress
env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY -u DATABRICKS_TOKEN \
  PYTHONFAULTHANDLER=1 PYTEST_PROGRESS_LOG_DIR=artifacts/progress \
  OMNIGENT_TOKEN_USAGE_JSON=artifacts/tokens-server-rest.json \
  COVERAGE_FILE=artifacts/.coverage.server-rest COVERAGE_CORE=sysmon \
  uv run pytest tests/server --ignore=tests/server/integration tests/onboarding \
    -n 4 --dist=loadfile --timeout=300 \
    --junitxml=artifacts/pytest-server-rest.xml --cov=omnigent --cov-report= \
    -v --tb=long --showlocals --log-level=INFO -r a
```

Before: 1 failed, 1,392 passed. After: 1,393 passed, 79 existing warnings.
The sole failing test was:

`tests/server/test_openapi_drift.py::test_openapi_json_matches_generator_output`

Classification: **fork/base-branch regression**. The fork added coding-agent,
goal-event, and auto-approve routes without fully regenerating the snapshot.
`python scripts/dump_openapi.py --check` passes on an isolated archive of upstream
ancestor `90080fe73`; it failed on the starting fork snapshot. Regeneration adds
six already-implemented paths. This is an ancestor control, not a claim about
current upstream main's entire test suite.

The initial sandboxed test invocation failed before collection with
`PermissionError: [Errno 1] Operation not permitted` in pytest-rerunfailures'
loopback socket bind. Classification: **environment-only**, resolved by running
the same command with local sockets permitted. No test was disabled.

The focused ACP/native-agent check also passes (4 tests):

```sh
.venv/bin/python -m pytest tests/test_acp_transport.py \
  tests/test_native_coding_agents.py -q
```

### npm test: PASS locally, including CI's coverage command

```sh
cd ap-web
npm run format:check
npm test
npm run test:coverage
```

Each test run reports 171 passed files and one skipped file; 2,777 passed tests,
three expected failures and two skips. Those existing exclusions are unchanged.
UI line coverage: 76.09%; statements 73.89%, branches 69.02%, functions 69.12%.
Existing jsdom canvas and React warnings do not fail the suite.

The supplied CI npm job stopped at formatting and never ran tests. After fixing
formatting, local reproduction exposed `node: bad option:
--no-experimental-webstorage`, before any test ID ran. Classification:
**fork/base-branch regression**, introduced by `b4eae0e452` in `vite.config.ts`.
The runtime support check fixes Node 20 worker startup and preserves Node 25's
storage workaround. Under Node 25, these existing tests also pass (210 tests):

```sh
npm test -- src/store/chatStore.test.ts src/pages/ModelsPage.test.tsx
```

### Coverage report versus Coverage status: remaining gate

The supplied CI's **Coverage report job succeeded**. Local coverage XML generation
also passes:

```sh
.venv/bin/coverage xml --data-file=artifacts/.coverage.server-rest \
  -o artifacts/server-rest-coverage.xml --ignore-errors
```

That local file covers only server-rest; it is not a full-matrix coverage total.
The other ten backend shards were not rerun locally. All ten passed in the
supplied CI run.

The separate **Coverage commit status remains failing on existing CI evidence**.
Its publisher runs from the fork's default branch, whose gate is stricter than
this historical branch's report-only workflow. The publisher's log identifies:

- Measured PR #10 artifact: 54,166 / 67,407 lines = 80.36%, published as `80`.
- Main baseline: `84`, from `8a6c6d86f4ce6908670677406a35499487968313`.
- Tolerance: 0.5 percentage points; enforcement enabled.
- Local reproduction of the comparison: `80 + 0.5 >= 84` is false.

```sh
awk -v c=80 -v b=84 -v t=0.5 \
  'BEGIN { print (c + t >= b) ? "PASS" : "FAIL"; exit (c + t >= b) ? 0 : 1 }'
```

There is **no failing test ID for this status**: it is an aggregate coverage
regression, not a pytest failure or local setup problem. The six fork-added
backend modules account for 984 executable lines, 451 covered and 533 uncovered
in the supplied artifact. Excluding those modules for diagnosis gives only
80.87%, so they do not explain the entire four-point difference. No coverage
exclusions were changed. The baseline artifact is no longer downloadable;
therefore exact attribution of the remaining difference to inherited upstream
coverage versus modifications of existing fork code is **unresolved**, not
asserted to be an upstream test failure.

Clearing this remaining status requires a comparable full-matrix measurement
and real coverage improvements; these local formatting, artifact, and runner
repairs do not establish that the 84% gate is met. No threshold was lowered,
status overridden, workflow disabled, or production behavior changed to hide it.

## Evidence

- [Lint run 39619579354](https://github.com/plundrpunk/omnigent/actions/runs/39619579354)
- [Backend run 39619579655](https://github.com/plundrpunk/omnigent/actions/runs/39619579655)
- [Frontend run 39619579314](https://github.com/plundrpunk/omnigent/actions/runs/39619579314)
- [Coverage publisher 39620266488](https://github.com/plundrpunk/omnigent/actions/runs/39620266488)
- [Baseline run 33634420998](https://github.com/plundrpunk/omnigent/actions/runs/33634420998)

Local logs are `/private/tmp/omnigent-precommit-committed.log`,
`/private/tmp/omnigent-server-rest-before.log`,
`/private/tmp/omnigent-server-rest-after.log`,
`/private/tmp/omnigent-npm-test-final.log`,
`/private/tmp/omnigent-vitest-fixed.log`, and
`/private/tmp/omnigent-node25-regression.log`.

Next: review these local commits. After separately authorized publication, rerun
the full CI matrix on the repaired base and re-evaluate its aggregate coverage.
