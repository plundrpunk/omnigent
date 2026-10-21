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

## PR #11 coverage follow-up: gate satisfied by local recombination

The earlier section above records the original formatting repair. This follow-up
adds behavior tests only. Product code, dependency manifests, workflows,
thresholds, baseline, coverage configuration, and exclusions are unchanged.
No `pragma: no cover` was added. No push was performed.

### Exact CI computation and baseline

The producing workflow is `.github/workflows/ci.yml`. Its eleven shards are:

| Shard | Selected tests |
| --- | --- |
| runtime-harnesses | `tests/runtime/harnesses` |
| runtime-policies | `tests/runtime/policies` |
| runtime-core | `tests/runtime`, excluding the previous two directories |
| inner-rest | `tests/inner` |
| tools | `tests/tools tests/test_errors.py` |
| repl-sdk | `tests/frontends tests/repl tests/terminals` |
| server-approvals | Three named permission-request, elicitation-resolve, and policy-evaluate integration files |
| server-integration | `tests/server/integration`, excluding those three files |
| server-rest | `tests/server --ignore=tests/server/integration tests/onboarding` |
| spec-llms | `tests/spec tests/llms` |
| misc | `tests`, excluding the preceding areas and e2e |

The global pytest `addopts` additionally ignores `tests/e2e`, `tests/e2e_ui`,
`tests/e2e_live`, and `tests/integration`. Each shard uses `--cov=omnigent
--cov-report=`, `COVERAGE_CORE=sysmon`, and an
`artifacts/.coverage.<group>` data file. There are eight workers by default,
four for server shards; the workflow defines distribution per shard.

The report job downloads `pytest-*` artifacts, combines all eleven
`covdata/**/.coverage.*` files, and runs `coverage xml --ignore-errors` and
`coverage report --format=total --ignore-errors`. This is executable **line**
coverage, not branch coverage or an average of shard percentages.
`pyproject.toml` sets `source = ["omnigent"]` and `relative_files = true`.
There is **no scripts include**: neither top-level `scripts/` nor frontend
Vitest coverage contributes to this backend denominator.

Although the publisher in this historical PR branch says report-only,
`workflow_run` executes the publisher from the fork's default branch. Its
current settings are `COVERAGE_ENFORCE=true`, tolerance `0.5`, and a scan of the
latest 100 `main` commits for the most recent `Coverage` status. The same
GraphQL query was rerun on October 21 and returned:

```json
{"desc":"Total coverage: 84%","oid":"8a6c6d86f4ce6908670677406a35499487968313"}
```

This baseline is from **fork main**, not PR #11's target branch. The comparison
uses rounded `total.txt` values: `coverage report --format=total` has the
configured default zero decimal places, so the new total is `84`, and
`84 + 0.5 >= 84` passes.

### Before/after measurement

Downloaded all eleven raw shard artifacts and the summary from
[PR #11 CI run 39651253417](https://github.com/plundrpunk/omnigent/actions/runs/39651253417),
whose head is exactly `c83fc77b5b29bf2ab15abcc98b9225fe2211cf23`.
Recombining those data files locally exactly reproduces that run's XML counts.
The previous report's 80.36% was from PR #10; this exact PR #11 baseline is 80.40%.

| Measurement | Covered / executable lines | Exact percentage | `total.txt` |
| --- | --- | --- | --- |
| Before: eleven PR #11 CI shards | 53,860 / 66,988 | 80.402460% | 80 |
| After: same shards + clean local changed-test run | 56,526 / 66,988 | 84.382277% | 84 |

Gain: **2,666 covered lines**, with an unchanged denominator. The after result
is a **hybrid local recombination**, not a fresh Linux CI run: unchanged
production source makes the CI baseline reusable, while the new test execution
was measured on macOS/Python 3.12.12 with coverage 7.14.1. The CI XML identifies
coverage 7.16.2; both versions produce identical baseline counts here.

The new tests were run together with `-n 8 --dist=loadfile --timeout=300`,
`COVERAGE_CORE=sysmon`, `--cov=omnigent --cov-report=`, and the same three
provider credentials unset as CI. Result: **390 passed, 4 strict xfailed**
across all sixteen changed test files (includes existing cases in expanded
files). No live provider, agent CLI, browser, or AMS service was contacted.
Tests use temporary files and mocked subprocess/HTTP boundaries, while exercising
real parsers, configuration generation, routing, state transitions, and rendering.

Reproduction using the retained evidence:

```sh
# Run from this worktree; the downloaded artifacts retain relative source paths.
COVERAGE_FILE=/private/tmp/omnigent-pr11-ci/before.coverage \
  .venv/bin/coverage combine --keep /private/tmp/omnigent-pr11-ci/*/.coverage.*
COVERAGE_FILE=/private/tmp/omnigent-pr11-ci/final.coverage \
  .venv/bin/coverage combine --keep \
  /private/tmp/omnigent-pr11-ci/before.coverage \
  /private/tmp/omnigent-pr11-final-added.coverage

# Report in the plain source archive, just as CI's reporting checkout does.
cd /private/tmp/omnigent-pr11-ci/checkout
/Users/drfoundryos/Documents/DevFolder/_worktrees/coord-omnigent-base-debt/.venv/bin/coverage \
  report --data-file=../final.coverage --format=total --ignore-errors
/Users/drfoundryos/Documents/DevFolder/_worktrees/coord-omnigent-base-debt/.venv/bin/coverage \
  report --data-file=../final.coverage --show-missing --ignore-errors
```

`checkout/` contains `git archive c83fc77b5 omnigent pyproject.toml`. The generated
`omnigent/_build_info.py` is absent there, exactly as in the CI report checkout;
the existing `--ignore-errors` behavior ignores it. No report-only exclusion was
introduced. Full per-file reports are retained at
`/private/tmp/omnigent-pr11-ci/before-missing.txt` and `final-missing.txt`, with
`before.json`, `final.json`, and `final.xml` alongside them. The clean parallel
run is `/private/tmp/omnigent-pr11-final-added.log` and its coverage data is
`/private/tmp/omnigent-pr11-final-added.coverage`.

### Behavior tests and principal coverage gains

| Module | Before covered / total | After covered / total | Gain |
| --- | --- | --- | --- |
| `omnigent/acp_agents.py` | 15/38 | 38/38 | +23 |
| `omnigent/acp_transport.py` | 119/241 | 237/241 | +118 |
| `omnigent/coding_agent_discovery.py` | 56/157 | 157/157 | +101 |
| `omnigent/inner/pi_native_executor.py` | 0/66 | 66/66 | +66 |
| `omnigent/inner/tracing.py` | 27/145 | 138/145 | +111 |
| `omnigent/onboarding/interactive.py` | 91/185 | 180/185 | +89 |
| `omnigent/onboarding/setup.py` | 96/251 | 242/251 | +146 |
| `omnigent/onboarding/wizard.py` | 0/724 | 721/724 | +721 |
| `omnigent/pi_native.py` | 74/249 | 248/249 | +174 |
| `omnigent/repl/_repl.py` | 1181/2460 | 1875/2460 | +694 |
| `omnigent/repl/_theme_picker.py` | 88/186 | 186/186 | +98 |
| `omnigent/server/routes/coding_agents.py` | 19/51 | 51/51 | +32 |
| `omnigent/server/routes/goal.py` | 195/356 | 356/356 | +161 |
| `omnigent/tools/_runner.py` | 0/122 | 121/122 | +121 |

The fork-only modules cannot provide the required ~2,410 additional lines, so
coverage work includes previously unmeasured shipped onboarding, Pi, REPL,
tracing, and tool-runner behavior. Assertions cover:

- ACP request correlation, permissions, denied filesystem access, malformed or
  oversized wire input, timeouts, process-group shutdown, and model sessions.
- CLI discovery precedence, model parsing, cache expiry, scan failures, counts,
  and coding-agent API filtering/404 behavior.
- Goal validation, launch argv, concurrency limits, cleanup, outcomes, bounded
  event normalization/pagination; AMS boot probe and fail-before-harness ordering.
- Onboarding terminal navigation/cancellation, credential choices, YAML output,
  config persistence, supervisor selection, OAuth failures, and overwrite consent.
- Pi fresh/resumed sessions, HTTP failure handling, terminal readiness and attach,
  latest-user-message queueing, and attachment references.
- REPL streamed-text deduplication across tool boundaries, input routing,
  cancellation, visible errors, diagnostic overlays, terminal attach quoting,
  theme persistence, and terminal restoration. OS/stdio mocks are module-local
  so they do not interfere with xdist workers.
- Tool-runner decorated-function dispatch, sync/async results, state injection,
  rejected targets, serialization, and file-descriptor/stdout response protocols;
  tracing parentage, span status, outputs, and bounded serialization.

### Bugs exposed, intentionally not fixed

All four tests are `xfail(strict=True)` with explicit reasons:

1. `test_failed_request_write_releases_pending_future`: an ACP stdin write error
   leaves the request's future in `_pending` until client close.
2. `test_artifact_fallback_does_not_attach_preexisting_run`: goal artifact fallback
   ignores `started_at` and may attach events from a previous run.
3. `test_running_events_resolve_workdir_against_harness_cwd`: goal artifact lookup
   resolves relative `HA_GOAL_WORKDIR` against server cwd rather than `HA_GOAL_CWD`.
4. `test_antigravity_supervisor_keeps_selected_harness`: selecting an Antigravity
   supervisor returns an `openai-agents` harness configuration.

These xfails test the desired behavior and will fail as unexpected passes when
fixed, prompting removal of the markers. They do not hide failures in existing
tests. Production fixes are outside this request.

### Broader validation and limits

After fixing test-fixture interference with xdist, all changed files pass both
serially and with eight workers. Applicable pre-commit hooks pass; frontend,
lock-registry, and YAML hooks have no changed inputs.

The five affected CI selections were also attempted with their workflow worker
counts/distribution/timeout. Local results are not uniformly green:

| Selection | Local result |
| --- | --- |
| Changed test files, `-n 8 --dist=loadfile` | 390 passed, 4 strict xfailed |
| server-rest (including onboarding) | 1,569 passed, 3 strict xfailed |
| tools | 541 passed, 5 existing failures |
| inner-rest | 1,198 passed, 30 skipped, 11 existing failures; 3 subtests passed |
| repl-sdk | 963 passed, 9 existing failures |
| misc (interrupted after timeout) | 3,801 passed, 1 skipped, 5 xfailed, 3 existing failures, 2 reruns |

All **25 failures** from tools, inner-rest, and repl-sdk were replayed from a pristine
`git archive c83fc77b5` checkout, without any added tests: **25 failed** again.
The failures involve host-scope skill discovery, Linux bubblewrap/seccomp,
macOS Unix socket path length, and terminal/tmux rendering assumptions. They
are pre-existing local reproduction limits, not newly marked xfails, and were
not repaired or skipped here. The baseline control is
`/private/tmp/omnigent-pr11-baseline-control.log` plus its XML. Original shard
logs are under `/private/tmp/omnigent-pr11-shards/`; corrected server-rest and
repl-sdk reruns are under `/private/tmp/omnigent-pr11-recheck/`.

The misc shard reached 98% before an existing configuration test hit its
300-second timeout and the worker stalled. The run was interrupted and is
**incomplete**, not green. Its two other failing CLI tests and the hanging
configuration test were separately replayed on pristine `c83fc77b5`: two
failures and the same timeout (bounded to 15 seconds for that control).
See `/private/tmp/omnigent-pr11-baseline-misc.log`. The five misc xfails include
four pre-existing filesystem-isolation xfails plus the new ACP regression.
These partial/failed broader runs are **not** inputs to the reported after
aggregate; it uses only the eleven successful baseline CI shards and the
completed, passing changed-test run.

These tests change no web UI. Browser/mobile QA and deployment were not run.
The remaining external check is a new Linux CI run after separately authorized
publication; this local commit does not change the currently published status.
