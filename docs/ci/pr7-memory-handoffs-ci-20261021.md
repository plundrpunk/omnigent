# PR #7 Memory & Handoffs: local CI repair

Starting point: `77bb7eeed513904f5b62e857a95f5809b4cf9d7e`, local branch
`coord/omnigent-7`, targeting `fork/feat/ams-bridge-boot-check`. No push or
operation against `origin` was performed.

## Failures and fixes

- **Pre-commit / frontend Check formatting:** seven PR-owned frontend files
  did not match Prettier. `pre-commit run --all-files` applied formatting only;
  all other hooks passed. Committed separately as `9f6e331f5`.
- **Server-rest:** the only failed test in
  [CI run 39656580259](https://github.com/plundrpunk/omnigent/actions/runs/39656580259/job/119418409685)
  was `tests/server/test_openapi_drift.py::test_openapi_json_matches_generator_output`.
  The local full-shard reproduction matched CI: **1 failed, 1,571 passed,
  3 xfailed, 80 warnings**. The snapshot omitted the PR's
  `/v1/ams/management/status` and `/v1/ams/management/{path}` paths.
- **PR-specific snapshot nondeterminism:** the management catch-all registered
  five methods in one FastAPI route. FastAPI derives its operation ID from
  `list(route.methods)[0]`; Python hash seeds 1 and 2 produced `put` and `post`
  suffixes respectively, also duplicating that ID across all five operations.
  Separate single-method decorators now provide stable, unique operation IDs.
  The same handler, authorization, 503 response, and no-outbound-request gate
  remain. Added
  `tests/server/routes/test_ams_management_gate.py::test_management_openapi_has_stable_unique_operation_ids`
  and regenerated `openapi.json` with `.venv/bin/python scripts/dump_openapi.py`.
  The artifact adds exactly two paths; existing paths are unchanged.

## Validation

Local macOS arm64, Python 3.12.12, Node 20.20.2, npm 11.12.1. Python dependencies
were installed with `uv sync --locked --extra all --extra dev`; the supplied
frontend dependency symlink was retained. No manifests or locks changed.

| Check | Result |
| --- | --- |
| `pre-commit run --all-files` | All 13 hooks passed |
| Server-rest, workflow selection and flags below | 1,573 passed, 3 xfailed, 79 warnings |
| OpenAPI `--check`, `PYTHONHASHSEED=1`, `2`, and `3` | All three passed |
| `npm run format:check` in `ap-web` | All matched files use Prettier code style |
| `npm run type-check` in `ap-web` | Passed |
| `npm test` in `ap-web` | 173 files passed, 1 skipped; 2,786 tests passed, 3 expected failures, 2 skipped |
| `npm run test:coverage` in `ap-web` (CI command) | Passed with the same test counts |
| `git diff --check` | Passed |

Reproduction uses the toolchain/cache paths documented in
`docs/ci/base-precommit-debt-20261021.md`:

```sh
export PATH=/private/tmp/omnigent-ci-toolchain/node_modules/.bin:$PATH
export PRE_COMMIT_HOME=/private/tmp/omnigent-ci-pre-commit
export OMNIGENT_SKIP_WEB_UI=true
export UV_INDEX_URL=https://pypi.org/simple
export UV_CACHE_DIR=/private/tmp/omnigent-ci-uv-cache
export UV_PYTHON_INSTALL_DIR=/private/tmp/omnigent-ci-python
.venv/bin/pre-commit run --all-files
mkdir -p artifacts/progress
env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY -u DATABRICKS_TOKEN \
  PYTHONFAULTHANDLER=1 PYTEST_PROGRESS_LOG_DIR=artifacts/progress \
  OMNIGENT_TOKEN_USAGE_JSON=artifacts/tokens-server-rest.json \
  COVERAGE_FILE=artifacts/.coverage.server-rest COVERAGE_CORE=sysmon \
  uv run pytest tests/server --ignore=tests/server/integration tests/onboarding \
    -n 4 --dist=loadfile --timeout=300 \
    --junitxml=artifacts/pytest-server-rest.xml --cov=omnigent --cov-report= \
    -v --tb=long --showlocals --log-level=INFO -r a
for seed in 1 2 3; do
  PYTHONHASHSEED=$seed .venv/bin/python scripts/dump_openapi.py --check || exit
done
(cd ap-web && npm run format:check && npm run type-check && npm test)
```

## Base and environment findings

The three existing backend expected failures remain unchanged:

- `tests/onboarding/test_wizard_coverage.py::test_antigravity_supervisor_keeps_selected_harness`
- `tests/server/routes/test_goal_routes.py::test_artifact_fallback_does_not_attach_preexisting_run`
- `tests/server/routes/test_goal_routes.py::test_running_events_resolve_workdir_against_harness_cwd`

No new skip, xfail, threshold change, or disabled check was introduced.
Environment-only startup problems were resolved by permitted reruns:
Python dependency installation initially hit sandbox DNS restrictions;
pytest-rerunfailures could not bind its loopback socket before collection;
TypeScript could not write `.tsbuildinfo` through the dependency symlink into
the sibling worktree. These failures had no test IDs. Git also required
permission to update worktree metadata outside the writable directory.
Existing jsdom canvas/React warnings did not fail frontend tests.

These are local results, not a new Linux CI run or a full-matrix coverage claim.
Browser/mobile QA and deployment were not run: the frontend change is formatter
output, and backend validation exercises the existing management gate and spec.
After separately authorized publication, rerun PR CI on the resulting head.

Logs: `/private/tmp/omnigent-pr7-{ci-failed,precommit-before,precommit-final,server-rest-before,server-rest-after,format-check,type-check,npm-test,npm-coverage}.log`.
Backend JUnit: `artifacts/pytest-server-rest.xml`.
