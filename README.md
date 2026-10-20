# ci-triage-lab

The README of the copied RAG application is preserved at [`docs/app-README.md`](docs/app-README.md).

## What this is

A labelled benchmark of 60 real GitHub Actions failures for evaluating CI-failure triage agents.
Each case is a known fault injected into a copy of a FastAPI RAG application (imported from
[`rag-chatbot-platform`](https://github.com/Yassine-Elkesraoui/rag-chatbot-platform) in commit `077ff39`), pushed to `main`, run on GitHub-hosted runners, recorded
with its job log, and reverted. The fault, its category and the observed CI behaviour are recorded
per case under [`injections/`](injections/).

## Headline numbers

All figures are computed from [`injections/index.json`](injections/index.json).

| Category | Cases | CI runs | Failing job | Failing step (count) | Job duration, median (range) |
|---|---|---|---|---|---|
| real_regression | 10 | 10 | smoke | Run smoke tests (9), App imports on a clean machine (1) | 176 s (151–207) |
| flaky | 10 | 60 | smoke | Run smoke tests (10) | 175.5 s (148–202) |
| dependency | 10 | 10 | smoke | Install dependencies (8), App imports on a clean machine (2) | 47.5 s (42–161) |
| environment | 10 | 10 | smoke | Run smoke tests (5), App imports on a clean machine (4), Run actions/setup-python@v6 (1) | 167.5 s (5–204) |
| timeout | 10 | 10 | smoke (8), docker (2) | Run smoke tests (6), Install dependencies (2), Build image (2) | 337.5 s (78–429) |
| build | 10 | 10 | docker | Build image (10) | 8 s (5–155) |
| **Total** | **60** | **110** | smoke (48), docker (12) | | |

- Each flaky case was run 6 times on GitHub. 26 of the 60 flaky runs failed as labelled (43%) and 34
  were fully green. Per case, failures out of 6: 3, 4, 1, 4, 1, 3, 1, 3, 3, 3 (inj-049 to inj-058).
- In 5 of the 10 flaky cases (inj-050, 052, 054, 055, 058) the first failure is on an empty rerun
  commit, not on the injection commit.
- 21 cases have a named failing test. In the other 39, the eval run's log has no pytest summary
  (pytest never ran, did not start, or was stopped).
- Job conclusions: 58 `failure`, 2 `cancelled` (the two job-level timeouts, inj-039 and inj-040).
- 77 log files (5.0 MB) are committed: 76 referenced by records, plus 1 unlabelled extra (see
  Findings 1).
- Raw GitHub API responses for all 111 of these runs (the 110 record runs plus the extra), and the
  metadata of all 193 workflow runs on `main`, are committed under `injections/api/` (5.8 MB).
- The labelled runs span 2026-09-23 (inj-001) to 2026-10-17 (inj-060).

## How it was built

1. **Green baseline.** The application was imported (`077ff39`), made to pass CI on a clean machine
   (`812a5ac`, `cc95374`), and given behaviour tests for schemas, chunking and settings (`b37abb9`,
   the `baseline_commit` in `manifest.json`). CI run 35897349380 on `b37abb9` succeeded. The suite
   has 13 tests (4 in `tests/test_app.py`, 9 in `tests/test_behaviour.py`).
2. **Workflow under test.** [`.github/workflows/ci.yml`](.github/workflows/ci.yml) has two jobs:
   `smoke` (checkout, setup-python 3.11, `pip install -r requirements.txt`, an import check
   `from api.main import app`, then `pytest tests -v` with `timeout-minutes: 3`) and `docker`
   (`needs: smoke`, `docker build`).
3. **Sequential inject → CI → record → revert.** Cases were run one at a time with
   [`injections/run_injection.py`](injections/run_injection.py). For each spec it checks that the
   working tree is clean, in sync with `origin/main`, and that HEAD's CI run is green. It then applies
   the spec's single find/replace, commits and pushes it, and waits for the run. It compares the
   observed failing job, skipped jobs, failing tests and failing step with the spec. On a match it saves
   the failing job's log and writes `injections/inj-NNN.json`, then reverts the injection and pushes the
   revert. On any mismatch it aborts without writing a record. Every injection commit in the history
   has a matching `Revert` commit, except the empty flaky rerun commits (subject
   `inject: inj-NNN rerun k/6`), which change nothing and are not reverted.
4. **Flaky calibration.** Before a flaky case was run on GitHub,
   [`injections/measure_flaky.py`](injections/measure_flaky.py) measured its failure rate locally. It
   applies the spec to a temporary copy of the repository and runs `tests/test_behaviour.py` 30 times
   in fresh processes with `PYTHONHASHSEED` unset. Results are in
   [`injections/flaky_measurements.md`](injections/flaky_measurements.md). On GitHub each flaky case
   was then run with `--runs 6`: the injection commit, then 5 empty commits that re-trigger CI on the
   same code. A flaky label requires at least one expected failure and at least one fully green run.
5. **Token.** The harness reads GitHub through the REST API with a fine-grained personal access token
   scoped to this repository with read-only permissions (Actions: read, Contents: read, Metadata:
   read), and issues only GET requests. Commits and pushes use the author's local git credentials,
   never the token.
6. **Logs and API data as evidence.** The failing job's log is saved byte-for-byte as GitHub serves
   it, under [`injections/logs/`](injections/logs/), and committed. For flaky cases, the log of every
   failing run is kept. On 2026-10-19,
   [`injections/maintenance/snapshot_api.py`](injections/maintenance/snapshot_api.py) saved the run
   and jobs (with steps) API responses of every referenced run under `injections/api/<run_id>/`, plus
   the list of all workflow runs on `main` as `injections/api/runs-main.json`. The responses are
   stored as returned, pretty-printed.

## Evidence and retention

The repository's Actions retention setting is 400 days. It applies only to runs created after the
setting was changed, so the earliest runs, starting with inj-001's run on 23 September 2026, fall
under the 90-day default. Since 1 October 2026, GitHub deletes workflow runs, checks and statuses
once the retention period has passed, and public repositories are limited to 90 days. The run ids,
job ids and GitHub-hosted logs referenced by the records will therefore stop resolving.

The dataset is self-contained. The evidence is the records, the committed logs, the API snapshots in
`injections/api/` and the git history, and none of it depends on GitHub keeping the runs.

## The six categories

Definitions are from [`injections/manifest.json`](injections/manifest.json); everything else is
summarised from the records and logs.

| Category | Definition | How injected | Where CI breaks | Typical log evidence | Easily confused with |
|---|---|---|---|---|---|
| real_regression | A code change broke behaviour the tests assert. The fix is in the changed code. | Edit to application code: `chunking_service.py` (4), `schemas.py` (3), `config.py` (2), `routes/chat.py` (1). | `smoke` › Run smoke tests; inj-010 at App imports on a clean machine. | `FAILED tests/...` with an assertion message; inj-010: `ImportError: cannot import name 'ChatRequst'`. | flaky (same failing test); environment (same Settings assertion); the import step. |
| flaky | The test itself is unreliable. Fails on some runs and not others, independent of the commit. | Controlled nondeterminism: random test data, set ordering, one-second time resolution, an async wait, unsynchronised threads, a timing budget, test-order dependency. In `tests/test_behaviour.py` (8) or product code (inj-057, inj-058). | `smoke` › Run smoke tests, in some runs only. | One failing test with an ordinary assertion or validation error; other runs green. A single failing log reads like a regression. | real_regression; environment and timeout on `test_settings_defaults_are_sane`. |
| dependency | A package version is incompatible. Code and tests are unchanged and correct. | Edit to `requirements.txt` (10): nonexistent versions, misspelled package names, conflicting pins, an invalid specifier, two downgrades that break under NumPy 2. | `smoke` › Install dependencies (8); App imports on a clean machine (inj-059, inj-060). | pip: `ERROR: Could not find a version that satisfies the requirement`, `ERROR: Cannot install ... conflicting dependencies`, `ERROR: Invalid requirement`. Import: ``AttributeError: `np.float_` was removed in the NumPy 2.0 release`` (059), `ValueError: numpy.dtype size changed` (060). | environment and real_regression at the import step. |
| environment | Config or environment is wrong (env vars, pytest config, missing files). Code is correct. | `ci.yml` (7): invalid or out-of-range env vars on the smoke job, `python-version: "3.99"`. `pytest.ini` (3): an `addopts` option from an uninstalled plugin (`--cov`), an unmeetable `minversion`, an uninstalled `required_plugins` entry. | `smoke` › App imports on a clean machine (029–032), Run smoke tests (033–037), Run actions/setup-python@v6 (038). | `pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings` at import; the same Settings assertion as a code-default change (033/034); pytest exit 4 with `ERROR: ...` (035–037); setup-python `version 3.99 ... was not found` (038). | real_regression (033/034 vs 008/009); dependency and real_regression at the import step. |
| timeout | The job exceeded its time limit rather than failing an assertion. | `timeout-minutes: 1` on a job (039 smoke, 040 docker) or a step (041 Install dependencies, 042 Build image). `sleep(600)` in a test (043–045), in application code (046) or at module import in a test module (047, 048), hitting the baseline 3-minute limit on Run smoke tests. | `smoke` › Run smoke tests (6), Install dependencies (2); `docker` › Build image (2). | Step-level: `##[error]The action '<step>' has timed out after N minutes.` Job-level: `##[error]The operation was canceled.` No pytest summary. | A manual cancellation (job-level); build (Build image); flaky and environment on `test_settings_defaults_are_sane` (045). |
| build | The Docker image failed to build. The Python test job is unaffected. | Edit to `Dockerfile` (10): base image tag or name, `EXPOSE` value, `COPY` sources, an apt package name, the requirements filename in `RUN`, the embedding model name, a shell command name. | `docker` › Build image; `smoke` passes. | BuildKit: `ERROR: failed to build: failed to solve: ...`; for missing `COPY` sources, `failed to calculate checksum of ref ...: "/<path>": not found`. | timeout in the docker job (040, 042). |

The manifest defines flaky as an unreliable test, but inj-057 and inj-058 place the nondeterminism in product code; what all ten flaky cases share is that the outcome varies between runs on identical code.

## Dataset layout

```
injections/
  inj-001.json .. inj-060.json   one record per case (labels and observations)
  specs/inj-001.json ..          one spec per case (the input to run_injection.py)
  logs/                          saved job logs: inj-NNN.log for the eval run,
                                 inj-NNN.runK.log for other failing flaky runs,
                                 plus one unlabelled extra
  api/<run_id>/run.json, jobs.json
                                 raw API responses for every referenced run
  api/runs-main.json             raw pages of the workflow-run list for main
  index.json, INDEX.md           per-case index, generated by build_index.py
  manifest.json                  schema version, repo, baseline commit, category
                                 definitions, and the case list (generated)
  run_injection.py               inject -> CI -> record -> revert harness
  measure_flaky.py               local failure-rate measurement for flaky specs
  flaky_measurements.md          results of measure_flaky.py for inj-049..058
  build_index.py                 builds the index files and audits the dataset
  maintenance/backfill_job_fields.py
                                 documents and reproduces fields added to early records
  maintenance/snapshot_api.py    saves the API responses under api/ (read-only)
docs/app-README.md               README of the copied application
api/, tests/, Dockerfile, requirements.txt, pytest.ini, .github/workflows/ci.yml
                                 the application and workflow under test
```

`eval/` and the other application directories belong to the copied RAG application and are not part
of the benchmark.

### Record schema (`injections/inj-NNN.json`)

| Field | Meaning |
|---|---|
| `id` | Case id, `inj-001` to `inj-060`. |
| `category` | The label: one of the six categories. |
| `commit` | Short SHA of the injection commit. |
| `run_id`, `job_id` | The eval run (the first run that failed as labelled) and its failing job. |
| `target_file` | The file the injection changed. |
| `mutation` | The change as `find -> replace`. |
| `expected_failing_tests` | Test ids expected to fail (empty when pytest does not run or reach a summary). |
| `expected_failing_job` | Job key expected to fail (`smoke` or `docker`). |
| `expected_skipped_jobs` | Job keys expected to be skipped. |
| `log_file` | The eval run's failing-job log. |
| `observed.failed`, `.passed`, `.errors` | pytest counts from the eval run, or `null` if no job log had a pytest summary. |
| `observed.failing_step` | First step of the failing job with a failed, cancelled or timed-out conclusion. |
| `observed.failing_step_inferred` | `true` if no step had such a conclusion and the step was taken as the first main step that did not succeed. `false` for all 60 cases. |
| `observed.job_conclusion` | The failing job's conclusion (`failure` or `cancelled`). |
| `observed.job_started_at`, `.job_completed_at`, `.duration_s` | The failing job's timestamps and their difference in seconds. |
| `observed.jobs` | Conclusion of every job in the eval run, by job key. |
| `observed.runs` | Every CI run made for the case: `run_id`, `commit`, `conclusion`, `jobs`, `failed`, `passed`, `log_file` (`null` for green runs). |
| `notes` | The mechanism, and what a triager can and cannot see in the log. |

Two record-level caveats. inj-001 has no `observed.errors` and no `observed.runs`, because it predates
the harness. In inj-001 to inj-038, the four fields from `failing_step_inferred` to `job_completed_at`
and inj-001's `observed.jobs` were backfilled from the GitHub API on 2026-10-18; see the docstring of
[`injections/maintenance/backfill_job_fields.py`](injections/maintenance/backfill_job_fields.py).

Specs (`injections/specs/inj-NNN.json`) carry `id`, `category`, `description`, `target_file`, `find`,
`replace`, the three `expected_*` fields and `notes`. `index.json` adds derived fields per case:
`mutation_summary`, `observed_failing_job`, `runs`, `outcomes` (one letter per run: `F` failed as
labelled, `P` fully green, `X` failed some other way), `observed_failing_tests` (re-parsed from the
log), `eval_run_id`, `eval_commit`, `eval_commit_is_injection`, `injection_commit`, `log_files` and
`duration_s`.

## Using it for evaluation

- **The failure to triage** is run `eval_run_id`, on commit `eval_commit`. Its failing-job log is the
  first entry of `log_files`.
- **The ground truth** is `category`. For localisation, use `target_file` and `mutation`; for
  failure location, `expected_failing_job` and `failing_step`.
- **Flaky cases:** `eval_commit` can be an empty rerun commit (`eval_commit_is_injection: false`, 5
  cases). The diff of that commit is empty, and the code change is in `injection_commit`, an ancestor.
  Only the run history (`outcomes`) shows that the same code also passed.
- **Run and step metadata** for every run (conclusions, step names and timestamps, run attempts) is
  in `injections/api/<run_id>/`, so it remains available after GitHub deletes the runs.
- **The labels are visible in this repository.** Commit subjects name the case and describe the fault
  (`inject: inj-NNN <description>`, `Revert "inject: ..."`), and `injections/` holds the labels and
  notes. An agent under evaluation should get the code at `eval_commit` and the log, but not this
  repository's history or the `injections/` directory.

## Findings

1. **Timeouts leave three kinds of evidence.**
   - Step-level `timeout-minutes` (inj-041 to inj-048): the log has
     `##[error]The action '<step>' has timed out after N minutes.` and the job concludes `failure`.
   - Job-level `timeout-minutes`, stopped mid-step (inj-039, inj-040): the log has
     `##[error]The operation was canceled.`, which is identical to a manual cancellation, and the job
     concludes `cancelled`.
   - Job-level, stopped between steps: the log has no error line at all, and the remaining steps are
     reported `skipped`. This happened on the first inj-039 attempt (run 37651788145, commit `704caeb`),
     kept as the unlabelled `injections/logs/extra-inj-039-attempt1-run37651788145.log`.

   The job-level clock includes runner start-up, so the step that gets stopped varies. On that first
   attempt, 37 s passed between the job's `started_at` and its first step, and the job was stopped
   after setup-python, before Install dependencies started. On the labelled attempt the first step
   began 1 s after `started_at`, and the job was stopped 56 s into Install dependencies.
2. **Step conclusions can mislead.** When a job-level timeout falls between steps, no step is
   reported failed or cancelled. The step that never started is reported `skipped`, like every later
   step, so the step list alone does not show what was interrupted. When it falls mid-step, the step is
   reported `cancelled` (inj-039, inj-040). A step-level timeout reports the step as `failure`, the
   same conclusion as an ordinary failing step. `run_injection.observe()` handles the between-steps
   case by inferring the failing step (`failing_step_inferred`); none of the 60 labelled eval runs
   needed it.
3. **A fine-grained read-only token cannot read check-run annotations.** The annotations endpoint
   returns HTTP 403 `Resource not accessible by personal access token`. Through the API, a timeout
   therefore has to be inferred from the job conclusion and the job or step duration compared with
   `timeout-minutes` in the workflow. The log line, where one exists, is the only direct statement.
4. **BuildKit fails some `COPY` errors early.** In inj-026 (`COPY api/`, step 7 of 9) and inj-027
   (`COPY eval/`, step 8 of 9, the last `COPY`), the build failed in about 5 to 6 s, before any `RUN`
   step executed. The error is `failed to calculate checksum of ref ...: "/apii": not found` (a
   failure to compute the cache key), because BuildKit checksums `COPY` sources up front. A log that
   fails at an early stage therefore does not mean an early Dockerfile line. inj-022
   (`COPY requirements.txt`, the first `COPY`) fails with the same message in 7 s.
5. **`requirements.txt` was UTF-16 and treated as binary by git.** Commit `431a20b` (2026-10-02)
   converted it to UTF-8 (5504 → 2616 bytes; git's diffstat shows it as `Bin`). This came before the
   first dependency injection (inj-011, 2026-10-04), so that dependency mutations are text find/replace
   operations with readable diffs.
6. **Some cases are confusable across categories.** The cases in each group share a failing test or
   failing step, and only the diff or the run history separates them:

   | Shared symptom | Cases |
   |---|---|
   | `test_chat_request_rejects_overlong_question` fails | inj-006 (real_regression), inj-050, inj-058 (flaky) |
   | `test_chat_request_rejects_short_question` fails | inj-005 (real_regression), inj-051 (flaky) |
   | `test_settings_defaults_are_sane` fails or hangs | inj-034 (environment), inj-045 (timeout), inj-055 (flaky) |
   | Same Settings assertion (`assert 2000 < 1000` / `assert 5000 < 1000`, `assert 1.5 <= 1.0`) | inj-008, inj-009 (real_regression), inj-033, inj-034 (environment) |
   | App imports on a clean machine fails | inj-010 (real_regression), inj-029 to inj-032 (environment), inj-059, inj-060 (dependency) |
7. **Flaky rates measured locally carried over to GitHub.** Locally, the ten flaky specs failed in 37%
   to 60% of 30 runs (11/30 to 18/30). On GitHub, 26 of 60 runs failed (43%). Six runs per case are too
   few to compare cases individually: three cases (inj-051, 053, 055) failed only once in six. In 5 of
   the 10 cases the first failure is on an empty rerun commit.
8. **One baseline test turned out to be non-discriminating.** In inj-003 the blank-text guard in
   `chunk()` was inverted, so every input returns `[]`.
   `test_chunking_returns_empty_list_for_blank_text` still passed, because blank text yields `[]`
   either way. The index/document_id and char_count tests also passed, vacuously over an empty list.
   Only `test_chunking_splits_long_text_into_multiple_chunks` failed.

## Limitations

- **One small repository and one two-job workflow.** The failures reflect this application, this
  test suite and GitHub-hosted Ubuntu runners.
- **Faults are injected, not organic.** Each is a single find/replace modelled on a realistic
  pattern; none was collected from real development history.
- **10 cases per category**, too few for fine-grained per-category statistics.
- **Flaky cases are driven by controlled randomness**, timing or ordering placed in the code on
  purpose, with local failure rates of 37% to 60%.
- **Both import-time dependency cases** (inj-059, inj-060) come from the NumPy 2 migration.
- **inj-001 was run by hand before the harness existed.** Its spec was reconstructed afterwards from
  commit `6147e81`; it reproduces the commit's code change but not an incidental end-of-file newline
  change in the same commit.
- **Where a job-level timeout stops the job varies with runner start-up**, so re-running inj-039 or
  inj-040 can stop in a different step, or between steps.
- **GitHub-side runs, checks and logs will be deleted** (see Evidence and retention). After that,
  only the committed copies remain, and anything not captured in them cannot be recovered.
- **Check-run annotations were not captured.** The read-only token cannot read them (Findings 3).

## Reproduce

Rebuild the index and run the audit (offline; rewrites `injections/index.json`,
`injections/INDEX.md` and the case list in `injections/manifest.json`, and exits 1 on any audit
ERROR):

```bash
python injections/build_index.py
```

Run a single injection (PowerShell, from the repository root). This pushes a breaking commit to
`main`, waits for CI, and then reverts it. It refuses to run if the record or logs for the spec's id
already exist, if the working tree is dirty or out of sync with `origin/main`, or if HEAD's CI run
is not green.

```powershell
$env:GITHUB_TOKEN = "<fine-grained token, read-only on this repository>"
python injections/run_injection.py injections/specs/inj-NNN.json --dry-run    # preconditions and diff only
python injections/run_injection.py injections/specs/inj-NNN.json              # non-flaky: one run
python injections/run_injection.py injections/specs/inj-NNN.json --runs 6     # flaky: six runs
```

Measure a flaky spec's local failure rate (no push; works on a temporary copy):

```bash
python injections/measure_flaky.py injections/specs/inj-NNN.json 30
```

Snapshot the API data for referenced runs (read-only; existing files are never overwritten, and runs
GitHub has already deleted are listed and skipped):

```bash
python injections/maintenance/snapshot_api.py
```

## License

MIT License, copyright 2026 Mohamed Yassine El Kesraoui; see [`LICENSE`](LICENSE). The copied
application's original README (`docs/app-README.md`) also declares `license: mit`.

## Acknowledgement

The tooling (`run_injection.py`, `measure_flaky.py`, `build_index.py`,
`maintenance/backfill_job_fields.py` and `maintenance/snapshot_api.py`) was developed with Claude
Code. All specs and results were reviewed by the author, Mohamed Yassine El Kesraoui.
