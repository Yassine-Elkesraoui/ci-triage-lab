# Holdout plan: inj-061 to inj-090 (phase B, step B1a, not yet run)

Written on 2026-11-20. Nothing here has run in CI. The specs in `injections/specs/inj-061.json` to
`inj-090.json` are plans: their `notes` say what each case is expected to show, and every expectation still has
to be checked against a real run.

**Sources**
- Spec: `ci-triage-agent/docs/preregistration/phase-b.md`, section 1, sealed at `38a97db` (the file is unchanged since).
- Lab: v1.0 at `2b362bc`. No v1.0 file was changed: records, logs, API snapshots, v1.0 specs, manifest and index
  are untouched.
- Order of work: every phase-B predictor was sealed before this plan (`2da88da` replay change f, `aa9cd25`
  agent v2, `3ff5c94` hybrid v1). That is the order section 1 requires.

**Checks so far:** static only. Each spec's `find` occurs exactly once in its target file, after the harness's own
CRLF/BOM handling. Every mutated Python file compiles. All 30 specs pass `run_injection.load_spec()` and a dry
`apply_mutation(write=False)`. No test, pip, docker or CI command was run.

## 1. Family check against v1.0, and swaps

Every listed family was compared with the mechanisms in the 60 v1.0 specs. A swap is replaced from the same
category's list only.

| category | family as listed | verdict | reason |
|---|---|---|---|
| real_regression | boundary or off-by-one in a loop, slice or range | **swapped out** | Repeats inj-001 (`len(content) - 1`) and inj-002 (`enumerate(..., start=1)`). The tested code has no other range or slice whose change a test would notice. |
| real_regression | changed default argument value | **swapped out** | Repeats inj-007 (default added to `ChatResponse.status`) and inj-008/009 (changed `Settings` defaults). No function the 13 tests call has a default argument to change. |
| real_regression | inverted comparison or boolean operator | **swapped out** | Repeats inj-003 (inverted blank-text guard), which is also the only condition on the tested path. |
| real_regression | changed error handling | kept (2) | No v1.0 case. |
| real_regression | renamed or dropped field in a returned object | kept (**3**) | No v1.0 case. |
| flaky | dependence on the wall-clock date or hour | kept (1) | See the note below the table. |
| flaky | dependence on filesystem enumeration order | **swapped out** | Cannot be made to fail as intended here. On a GitHub-hosted runner, the directory listing order for a fixed set of names is set by the filesystem's hash seed, which comes from the runner image, so the same names list in the same order on every run and the test fails always or never. Random file names would move the cause to randomness (inj-049/050). An unordered-collection variant repeats inj-051/058. |
| flaky | collision on a shared temporary file or process-wide resource | **swapped out** | CI runs a single pytest process on a fresh VM, so nothing is left over and nothing runs in parallel. A collision can only come from threads inside one test, which repeats inj-054 (unsynchronised shared state), or from tests whose order varies, which repeats inj-056. |
| flaky | completion order of concurrent async tasks | kept (2) | Differs from inj-053 (fixed sleep instead of a join) and inj-054 (lost update): every result arrives, but it is paired by completion order. |
| flaky | ordering by generated unique ids | kept (2) | Differs from inj-057, where product code corrupts a random id bit; here the ids are correct and only the ordering assumption is wrong. |
| dependency | pin whose `python_requires` excludes the runner's Python | kept (2) | No v1.0 case. The final pip lines look like inj-011..013; the "Ignored ... require a different python version" line differs. |
| dependency | installed without an optional extra the code needs | kept (1) | No v1.0 case. |
| dependency | no wheel for the platform, source build fails | kept (1) | No v1.0 case. |
| dependency | hash mismatch under `--require-hashes` | kept (1) | No v1.0 case. |
| dependency | new release changes a default without removing an API | **swapped out** | Cannot be made to fail as intended with any confidence. `requirements.txt` is a freeze of current releases, and I know of no newer release of a pinned package whose changed default these 13 tests would notice. Finding one would need trial installs, and an unverified pick risks a case that passes, or fails some other way. |
| environment | required env var missing entirely | kept (1) | v1.0 inj-029..034 set variables to bad values; this one is absent. |
| environment | wrong working directory or `PYTHONPATH` | kept (2) | No v1.0 case. |
| environment | locale or encoding setting breaks text handling | **swapped out** | Cannot be made to fail as intended. Every file read in `api/` passes `encoding="utf-8"`, the tests are ASCII, Python 3.11 coerces a C/POSIX locale to UTF-8 (PEP 538/540), and logging swallows encoding errors. No workflow-level locale or encoding setting breaks the import or a test. The only way would be adding a locale-dependent read to the code, which would make it a code bug. |
| environment | script CI invokes lacks execute permission | kept (1) | No v1.0 case. |
| environment | test-discovery config collects no tests | kept (1) | inj-035..037 are config errors that stop pytest from starting (exit 4); here pytest starts and collects nothing (exit 5). |
| timeout | deadlock | kept (1) | v1.0 timeouts are `timeout-minutes` changes (039..042) or `sleep(600)` (043..048). |
| timeout | command blocked on stdin | kept (1) | Built around the GitHub Actions stdin problem; see inj-082. |
| timeout | polling for a local service that never starts | kept (1) | |
| timeout | unbounded retry loop | kept (1) | |
| timeout | algorithmic slowdown, still correct | kept (1) | |
| build | `COPY --from` a stage that does not exist | kept (1) | Error text resembles inj-020; the failing instruction differs. |
| build | unknown or malformed instruction | kept (1) | inj-021 is a valid instruction with a bad argument; this is a parse error. |
| build | required build argument with no default | kept (1) | |
| build | `.dockerignore` excludes a needed file | kept (1) | Log shape as inj-022; the Dockerfile is unchanged. |
| build | `USER` to a missing account / permission error in `RUN` | kept (1) | |

**Date or hour (flaky) is kept, not swapped as a repeat of inj-052.** inj-052 fails about half the time at any
hour, on a race between two sub-second clock reads. inj-070 compares calendar dates across time zones and fails
in a fixed daily window. The cause and the failure pattern both differ. It is kept partly because of necessity:
after the two flaky swaps, only three families remain, the minimum for 5 cases at 2 per family.

**Deviation (v3): real_regression has 3 cases in one family.** After the three swaps, real_regression has only
two families left, and the "at most 2 per family" rule allows only 4 cases. The swap rule cannot fill the fifth
case. On 2026-11-20 the lab owner chose to keep all five cases novel and put 3 in "renamed or dropped field",
rather than keep a repeat family or shrink the holdout to 29. This is outside section 1's swap rule, so it is
reported as a **v3 change** alongside the results. The six other swaps follow the rule and are not v3 changes.

**Label convention for timeouts (as in v1.0).** A job stopped by its time limit is labelled `timeout`, whatever
layer caused the hang:
- application code: inj-081 (deadlock), inj-084 (retry loop), inj-085 (regex backtracking);
- test code: inj-082 (stdin pipe);
- the workflow: inj-083 (polling).

The label follows the failure mode (the job was killed at its limit, with no assertion failure), not the file
that has to change. inj-083's cause is in `.github/workflows/ci.yml`, like v1.0's `timeout-minutes` cases
inj-039..042, and is still `timeout`, not `environment`. The fix location is what the diagnosis's file and fix
fields are scored against; the category is not.

## 2. Cases

IDs are grouped by category for readability. Run order is shuffled separately (section 4). Every commit message
is `Update <target_file>`. That names only the file the diff already shows, and no category word appears in any
message or branch name. Branch names are opaque draws (section 4). "Rerun" marks the 12 cases with a planned
earlier run of the same commit.

| id | category | family | target | change | rerun | branch |
|---|---|---|---|---|---|---|
| inj-061 | real_regression | renamed/dropped field | api/main.py | GET / returns `state` instead of `status` | | wip-f9b439 |
| inj-062 | real_regression | renamed/dropped field | api/models/document_schemas.py | `DocumentChunk.char_count` becomes `length` (input alias kept) | fails same | wip-91d930 |
| inj-063 | real_regression | renamed/dropped field | api/main.py | GET /health drops `status` | | wip-8142c8 |
| inj-064 | real_regression | changed error handling | api/models/schemas.py | length check raises `TypeError` instead of a validation error | | wip-3ceaaf |
| inj-065 | real_regression | changed error handling | api/models/schemas.py | wrap validator swallows length `ValidationError` | fails same | wip-a05f11 |
| inj-066 | flaky | async completion order | tests/test_behaviour.py | `as_completed` results zipped with submission order | | wip-627655 |
| inj-067 | flaky | async completion order | tests/test_behaviour.py | `FIRST_COMPLETED` task taken as the first submitted | | wip-ab13df |
| inj-068 | flaky | unique-id ordering | tests/test_behaviour.py | oldest upload taken as `min(uuid4 ids)` | passes | wip-9dfb36 |
| inj-069 | flaky | unique-id ordering | tests/test_behaviour.py | newest draft taken as `max(uuid4 ids)` | | wip-0944fe |
| inj-070 | flaky | wall-clock date or hour | tests/test_app.py | `date.today()` (UTC) asserted equal to the Auckland date | passes | wip-0ff9bb |
| inj-071 | dependency | python_requires | requirements.txt | `numpy==2.5.0` (needs Python >= 3.12) | fails same | wip-a0c515 |
| inj-072 | dependency | python_requires | requirements.txt | `networkx==3.7` (needs Python >= 3.12) | | wip-6301bb |
| inj-073 | dependency | missing optional extra | requirements.txt | drop `python-multipart` (only fastapi's `standard` extra brings it) | fails same | wip-cdde8d |
| inj-074 | dependency | no wheel, source build fails | requirements.txt | `greenlet==1.1.2` (no cp311 wheel; C code predates Python 3.11). Corrected after the local check: the B1a draft `PyYAML==6.0` has cp311 wheels | | wip-047a83 |
| inj-075 | dependency | hash mismatch | requirements.txt | stale `--hash` on the first pin, `aiohappyeyeballs` | | wip-2bd8a1 |
| inj-076 | environment | env var missing | .github/workflows/ci.yml | test step needs `${SMOKE_TEST_PATHS:?}`, never set | fails same | wip-6fced1 |
| inj-077 | environment | working directory | .github/workflows/ci.yml | import step runs in `api/` | | wip-7f7695 |
| inj-078 | environment | working directory | .github/workflows/ci.yml | test step runs in `tests/` | fails same | wip-b7c1dc |
| inj-079 | environment | no execute permission | .github/workflows/ci.yml | new step `./stats.py` (mode 100644) | | wip-7e02ff |
| inj-080 | environment | test discovery | pytest.ini | `python_files = *_test.py` collects nothing | | wip-3a124c |
| inj-081 | timeout | deadlock | api/main.py | /health holds a `Lock` and calls `read_root`, which takes it again | fails same | wip-a1a6bc |
| inj-082 | timeout | blocked on stdin | tests/test_app.py | child `python -` fed through a pipe that is never closed | fails same | wip-5cbd0b |
| inj-083 | timeout | polling a service | .github/workflows/ci.yml | `until curl :8000/health` before pytest; nothing starts the server | | wip-4c96d8 |
| inj-084 | timeout | unbounded retry | api/main.py | GET / retries `importlib.metadata.version()` forever | | wip-66080f |
| inj-085 | timeout | algorithmic slowdown | api/services/chunking_service.py | debug-only regex `(?:\w+\s?)+[.!?]$` backtracks exponentially | | wip-873f9b |
| inj-086 | build | COPY --from | Dockerfile | `COPY --from=builder requirements.txt .`, no such stage | | wip-42f2dc |
| inj-087 | build | malformed instruction | Dockerfile | ENV loses a `\`, so `PIP_NO_CACHE_DIR=1` is parsed as an instruction | fails same | wip-f0bbe9 |
| inj-088 | build | required build arg | Dockerfile | `ARG PYTHON_VERSION` with no default, used in `FROM` | fails same | wip-b120a3 |
| inj-089 | build | .dockerignore | .dockerignore | `*.txt` excludes `requirements.txt` | | wip-b43c58 |
| inj-090 | build | USER | Dockerfile | `USER appuser` (no such account) before `pip install` | | wip-ce4cda |

**inj-079 and stats.py.** `stats.py` is already tracked: it came in with the baseline import (`077ff39`, mode
`100644`) and is not git-ignored. The inj-079 commit changes only `ci.yml`; it neither adds nor touches
`stats.py`. The checkout therefore has the file without its execute bit, and the step fails with
`./stats.py: Permission denied` (exit 126), not `No such file or directory` (exit 127). Before running the
case, the harness's dry run should confirm `git ls-files -s stats.py` still shows `100644`.

Specs use the v1.0 format. They add four keys that `load_spec()` ignores: `family`, `commit_message`, `branch`,
`same_commit_rerun` (`"fail_same"`, `"pass"` or `null`).

**Where branch names appear:** CI runs only on pushes to `main` (and on PRs to `main`). So a branch is the local
working branch the commit is made on, pushed as `<branch>:main`, and is never pushed as a remote branch. Every
run's `head_branch` stays `main`.

**Why every rerun is a push to main, not a PR run.** The sealed replay finds same-commit reruns in
`injections/api/runs-main.json`, which is `GET /actions/runs?branch=main`. A `pull_request` run has the feature
branch as `head_branch` and would not be listed there, so predictors would never see it.

## 3. Same-commit runs (12 cases)

| category | cases with an earlier run of the same commit | earlier run must |
|---|---|---|
| real_regression | inj-062, inj-065 | fail the same way |
| flaky | inj-068, inj-070 | pass |
| dependency | inj-071, inj-073 | fail the same way |
| environment | inj-076, inj-078 | fail the same way |
| timeout | inj-081, inj-082 | fail the same way |
| build | inj-087, inj-088 | fail the same way |

**How the earlier run is created.** It is a separate workflow run on the same head sha (new run id,
`run_attempt` 1), never a re-run attempt. The workflow (`on: push: branches: [main]`) is not changed, and
GitHub starts a run on every push of `main`. So:

1. Commit X (the injection) on the case's branch and `git push origin <branch>:main` → **run A**, the earlier run.
2. `git push --force origin X~1:main` → a run on the parent P. It is green because P is the previous green state,
   and it becomes the case's baseline run.
3. `git push origin <branch>:main` again (the same sha X) → **run B**, the triaged run.
4. Record and revert as in v1.0.

Net history on `main` is unchanged. The replay then shows, for a rerun case: run B, the baseline run on P, and
run A (`same_commit_reruns`, created before B). The order A → P → B is the same for all 12 cases, whatever their
category.

"Fails the same way" means run A matches the spec exactly as run B must: same failing job, same failing step,
same failing tests (none for most), and the same error line in the log. If A does not match, the harness aborts
as in v1.0 and nothing is recorded.

**Flaky cases with a rerun (A must pass, B must fail):**
- **inj-068.** If A fails, or if A passes and then B passes, that commit is **discarded**: force `main` back to P,
  recreate the commit with the same tree and message but a new committer date (so a new sha), and start again.
  At about 50% per run, expect about 4 attempts.
- **inj-070** is set by the clock: it passes from 00:00 to 10:59 UTC and fails from 11:00 to 23:59 UTC (NZDT; the
  boundary moves to 12:00 UTC after 2027-04-03). Push X for run A at about 10:35 UTC, so its pytest step runs
  before 11:00. Re-push X for run B at 11:00 UTC or later. The run order (section 4) places it 26th, so pause
  before it if needed. The pause sits before run A, so no run the predictor can see covers it.

**Flaky cases without a rerun (inj-066, inj-067, inj-069).** The triaged run must be the first run of its
commit. If a run passes, that commit is discarded the same way (force `main` to P, recreate X with a new date,
push again) until a run fails as expected. The passing attempt's commit is not an ancestor of the triaged
commit, so the replay keeps it and its run out of scope. Each discarded attempt (sha, run id, outcome) is
recorded with the case and listed as an unlabelled extra in the index.

The flaky label still needs a green run of the same code, as in v1.0. A discarded passing attempt provides it.
If the first run fails at once, add a **verification run** after the triaged run has completed: force `main` to
P and push X again. That run is created after the cutoff, so the replay never shows it. Repeat until one passes,
then revert.

*Why discard rather than v1.0's empty rerun commit:* an empty commit on top would leave the passing run visible
as an earlier run of the same code. That gives the predictor rerun evidence in all but name, in a case planned to
have none, and it also makes the triaged commit's own diff empty, which happens only in flaky cases. Discarding
keeps the triaged commit equal to the injection commit in all 30 cases.

**All other cases** run once, as in v1.0, except for the decoys below.

**Decoy discards (10 non-flaky cases).** Discarded attempts leave two marks in the run history: a gap in run
numbers, and an extra green run on the baseline commit (from the force-back). Without decoys, both would appear
only in flaky cases. Two non-flaky cases per category therefore get one decoy discard each. The draw uses
`random.Random(20261120 + 4)`, categories in the order real_regression, dependency, environment, timeout,
build, with `r.sample(pool, 2)` from each category's cases without a rerun (3 per category, so no rerun case was
needed):

| category | decoy cases |
|---|---|
| real_regression | inj-061, inj-064 |
| dependency | inj-072, inj-075 |
| environment | inj-079, inj-080 |
| timeout | inj-083, inj-084 |
| build | inj-089, inj-090 |

Procedure:
1. Push the injection commit X and let its run complete. That run is the decoy attempt.
2. `git push --force origin X~1:main`, which gives a green run on the parent.
3. Recreate the commit with the same tree and message and a new committer date (X′), push it, and continue as
   planned. X′'s run is the triaged run.

The decoy run is judged like any run of the spec. If it does not fail as expected, the case aborts (the spec is
wrong), exactly as a mismatching triaged run would. X is not an ancestor of X′, so the replay never shows the
decoy run.

**Audit of these marks.** For each case, the audit counts:
- (a) **extra baseline runs**: runs on the baseline commit created after the previous case's revert run and
  before the triaged run, beyond the first;
- (b) **run-number gaps**: runs on `main` between the previous case's last run and this case's triaged run that
  are outside the case's visible scope.

It reports both per category, with the 12 planned reruns shown separately, since each of those adds one parent
run by design. Expected result: every category has non-zero counts. Flaky counts depend on how many attempts
are discarded, and the report states them as observed.

**Unverified assumptions, to check on the first rerun case before relying on them:**
- GitHub starts a new push run when `main` is moved back to a sha it has already run. If no run B appears within
  the creation grace period, the harness must abort, and `main` must be left at X and reverted.
- `main` allows force pushes. Check the branch protection settings before B1b.

## 4. Run order and draws (seed 20261120)

Every draw is reproducible with Python 3.11 (`random` with integer seeds), starting from the IDs in numeric order:

```python
import random
SEED = 20261120
ids = [f"inj-{n:03d}" for n in range(61, 91)]
order = ids[:]; random.Random(SEED).shuffle(order)                 # run order
r = random.Random(SEED + 1)                                        # reruns, categories in this order:
# real_regression: r.sample(ids_rr, 2); flaky: r.choice([066..069]) besides the fixed 070;
# dependency, environment, timeout, build: r.sample(ids_cat, 2) each
# branch names: random.Random(SEED + 2).getrandbits(24) per case in id order, as wip-%06x
# inj-075's fake hash: random.Random(SEED + 3).getrandbits(256), as %064x
```

inj-070's rerun was fixed by design, not drawn: its passing run needs a scheduled time, and that same run is its
flaky evidence.

**Run order** (category in brackets; the longest run of one category is 2):

| # | case | # | case | # | case |
|---|---|---|---|---|---|
| 1 | inj-090 [build] | 11 | inj-063 [real_regression] | 21 | inj-074 [dependency] |
| 2 | inj-062 [real_regression] R | 12 | inj-087 [build] R | 22 | inj-065 [real_regression] R |
| 3 | inj-072 [dependency] | 13 | inj-066 [flaky] | 23 | inj-083 [timeout] |
| 4 | inj-089 [build] | 14 | inj-068 [flaky] R | 24 | inj-067 [flaky] |
| 5 | inj-085 [timeout] | 15 | inj-080 [environment] | 25 | inj-084 [timeout] |
| 6 | inj-071 [dependency] R | 16 | inj-086 [build] | 26 | inj-070 [flaky] R, ~10:35 UTC |
| 7 | inj-061 [real_regression] | 17 | inj-078 [environment] R | 27 | inj-073 [dependency] R |
| 8 | inj-088 [build] R | 18 | inj-082 [timeout] R | 28 | inj-077 [environment] |
| 9 | inj-069 [flaky] | 19 | inj-081 [timeout] R | 29 | inj-079 [environment] |
| 10 | inj-076 [environment] R | 20 | inj-064 [real_regression] | 30 | inj-075 [dependency] |

R = planned same-commit run. The order is fixed now. If a case aborts, it is retried in its own slot before the
next case starts, and the retry is reported.

## 5. Tooling changes needed (listed, not made)

`injections/run_injection.py`:
1. Use the spec's `commit_message` for the injection commit; no `inject:` subject anywhere. The revert keeps
   git's default `Revert "Update <file>"`.
2. Commit on the spec's `branch` and push `<branch>:main`.
3. Same-commit mode (`same_commit_rerun`): run A, force-push the parent and wait for its green run, re-push the
   same sha, run B. Judge A by the rule in section 3 and B as the triaged run, and record both in
   `observed.runs` with A's log saved (A's failing log is what `get_job_log` serves for it).
4. **Fix needed for (3):** `wait_for_run()` takes `latest_run()` on the sha, which after the re-push can return
   run A before run B exists. It must ignore run ids already seen for that sha.
5. Flaky discard loop (section 3): force-push the parent, recreate the commit with the same tree and message and a
   new committer date, push. Record every discarded attempt, and cap the attempts (for example 10) to bound cost.
6. Verification runs after the cutoff for flaky cases with no green run yet. `judge()` must accept the green
   evidence from discarded or verification runs.
7. `RecoveryState` and `print_recovery()` must cover the force-pushed states (`main` at P, X not re-pushed).
8. `--runs N` (empty rerun commits) is not used for v1.1; refuse it for ids above 060.
9. A time gate for inj-070, or do that case by hand: run A before 10:50 UTC, run B at 11:00 UTC or later.
10. Decoy mode for the 10 cases in section 3: one discarded attempt of X, judged against the spec and recorded,
    then X′. It can reuse the discard code from item 5.

`injections/build_index.py`:
1. `EXPECTED_IDS` becomes inj-001..inj-090.
2. v1.1 audit checks:
   - each of the 12 has exactly one earlier same-commit run, created before the eval run, with the planned
     outcome;
   - the other 18 have none;
   - `eval_commit_is_injection` is true for all 30;
   - every discarded or verification run is listed with an API snapshot.
   - the per-case and per-category counts of extra baseline runs and run-number gaps (section 3, decoy
     discards), reported rather than required to be zero.
3. `UNLABELLED_EXTRAS` is a hand-written dict. Discarded and verification runs need entries, or the list should
   be derived from the records.
4. Show `family` and the rerun marker in `index.json` and `INDEX.md`.

`injections/maintenance/snapshot_api.py`: snapshot `run.json` and `jobs.json` for runs A, the parent runs from
the force-backs, and discarded and verification runs, besides the eval runs. The replay maps saved logs to jobs
through `jobs.json`.

`injections/manifest.json`: regenerated by `build_index.py`; bump `schema_version` to 1.1 when tagging.

`injections/measure_flaky.py`: no change. inj-066..069 are in `tests/test_behaviour.py`, which it runs. inj-070
cannot be measured by repetition, since its outcome is set by the hour.

Agent repo: no change. The sealed replay already reads same-commit runs from `runs-main.json`; the design above
only has to make sure they are push runs on `main`.

## 6. Before the first CI run (B1b; each item runs something, so each needs a go)

- Run each spec locally on a temporary copy with pytest and a short timeout (as v1.0 did for 043..048), and
  correct any `expected_failing_tests` or notes that are wrong. Especially:
  - inj-064, that pydantic 2.13 lets `TypeError` through;
  - inj-065, that the wrap handler includes the Field constraints;
  - inj-081, inj-082, inj-084 and inj-085 hang where stated.
- **Done 2026-11-20:** `measure_flaky.py` with 30 runs for inj-066..069 (target about 50%): 37%, 67%, 50%,
  60%, no other test ever failed. Results are in `flaky_measurements_v1.1.md`; `flaky_measurements.md` is a v1.0
  file and stays unchanged.
- Check in a scratch venv:
  - inj-075 reports a hash mismatch, not missing hashes;
  - the inj-073 import error.
- **Done 2026-11-20, Docker 29.5.3 (linux/amd64), on LF copies from `git archive` with each spec applied by
  `measure_flaky.apply_to_copy`:**
  - inj-074: in `python:3.11` (full image, gcc 14.2.0), `pip install -r requirements.txt` resolves, downloads
    every wheel, then fails compiling greenlet 1.1.2's `greenlet.c` (26 C errors, gcc exit 1). Confirmed.
  - inj-079: in a `git clone` inside `python:3.11`, `stats.py` is 100644 and the step fails with
    `./stats.py: Permission denied`, exit 126. Confirmed; the note's suggested fix was wrong (no shebang) and
    is corrected.
  - inj-086..090: `docker build -t ... .` fails at the expected instruction with the expected error (086
    `COPY --from=builder`, pull access denied; 087 parse error on line 17; 088 `python:-slim` invalid reference;
    089 `"/requirements.txt": not found` at `COPY requirements.txt .`; 090 `unable to find user appuser` at the
    `pip install` RUN). `docker.io/library/builder` does not exist (Hub API 404, registry pull 401).
- **Done 2026-11-20 on Windows (not Docker):** inj-071 and inj-072 show the expected "No matching distribution"
  lines under pip 24.0; inj-070's UTC-hour window checked with tzdata. Details are in their specs.
- Confirm the force-push setting on `main`.

## 7. Known residual signals

- In the 12 rerun cases, the baseline run is created after run A. This is the same for every category, so it says
  nothing about the category, but a predictor can see it.
- Discarded attempts use up run numbers and add an extra baseline run. Each force-back creates the baseline run
  just before the next push, so the runs a predictor sees should stay consecutive. The marks remain in the full
  run history, and the decoy discards (section 3) put them in every category. The audit counts them per
  category.
- Some logs resemble v1.0 logs although the mechanism differs:
  - inj-071/072 vs inj-012 (last pip lines);
  - inj-086 vs inj-020 (`pull access denied`);
  - inj-089 vs inj-022 (`not found` on a COPY source);
  - inj-085 vs inj-044 (same hanging test name);
  - inj-067 vs inj-054 and inj-069 vs inj-049 (same failing test).
  In each pair, the diff distinguishes the two.
- inj-070's triaged run is created at about 11:00 UTC, and its failure depends on that time. This is real
  evidence, not leakage.
