# Holdout log (phase B, step B1b)

**Append-only.** New entries go at the end. An entry is never edited or removed after it is committed; a
correction is a new entry that names the entry it corrects. Times are UTC. The plan is
`injections/holdout_plan.md`, closed at B1a (`b43e890`) and not edited; anything that departs from it is
recorded here.

Every case run gets one entry, whether it matched, aborted or was retried, using this layout:

```
## <date> <time> UTC: slot <n>/30, <case> [<category>], <mode>, attempt <k> of this slot
- Command: <exact command>; harness commit <sha>
- Commits: injection <sha> (parent <sha>); discarded/recreated <shas or none>; revert <sha>
- Runs: triaged <run id> <outcome>; run A <run id> <outcome> (rerun cases); decoy <run id>; discarded <ids>;
  parent (force-back) <ids>; verification <ids>
- Result: match=Y/N, outcomes=<...>, extras=<...>, attempts=<n>
- Unexpected: <anything that differed from the spec or plan, or "nothing">
```

---

## 2026-11-21 01:12 UTC: decision, the v1.1 runs go into `runs-main.json` (option a)

- **Problem.** The sealed replay (agent repo, `src/ci_triage/replay/labdata.py`) reads exactly one listing,
  `injections/api/runs-main.json`, and finds baselines and same-commit reruns only there. That file is v1.0
  data, and `snapshot_api.py` never rewrote it. Plan section 5 says "agent repo: no change", but under that
  rule the replay would never see a v1.1 run. Raised after B1b-1 (tooling commit `b5ff756`).
- **Decision (lab owner, 2026-11-21): option (a).** Once all 30 v1.1 records exist, the v1.1 runs are appended
  to `injections/api/runs-main.json` as extra pages, with every v1.0 entry byte-identical. A lab check confirms
  that the v1.0 part is unchanged. In the agent repo, when the submodule moves to v1.1, the existing v1.0
  byte-identical replay test must pass, **or we stop**.
- **Implemented in the commit that adds this log.** `injections/runs_listing.py` does the append and the check,
  and `snapshot_api.py` and `build_index.py` use it:
  - The v1.0 part is `endpoint`, `params` and the first 2 pages (193 runs). Re-serialised exactly as
    `snapshot_api.save` writes, it must hash to sha256 `ac8549e5295794297872d68764ab3eb4fe5081d6eb63c55d6bce8ddfd6c40305`,
    the file committed in `2b362bc`. Checked before and after the append; nothing is written if it fails.
  - Exactly one page is appended. It holds every fetched run whose id is not in the v1.0 pages. Re-fetched copies
    of v1.0 runs are counted but not stored: the replay builds its run table with later pages overriding earlier
    ones, so storing them would change v1.0 replay inputs.
  - A `v1_1_append` section after `pages` records the fetch time, the counts and this entry.
  - The append happens once, and only when all 30 v1.1 records exist; before that nothing is fetched.
  - `build_index.py` runs the v1.0 check in every audit, so it fails if the v1.0 part ever changes.
  - Tests: `injections/tests/test_build_index_v11.py` (a simulated full holdout appends one page and the v1.0
    bytes equal the committed file; a changed v1.0 entry fails the audit; the append refuses to store re-fetched
    v1.0 runs or to append twice).
- **Found while checking the agent side (agent repo not changed).** `tests/test_same_commit_reruns.py` asserts
  `len(SNAPSHOT) == 60` and that the snapshot's ids equal *all* cases that `load_cases(lab)` returns from
  `index.json`. Once the v1.1 records are indexed, `index.json` lists 90 cases, so that assertion fails by
  construction, even if every v1.0 bundle and tool output is byte-identical. `tests/conftest.py`'s
  `LATEST_CASE` ("sees the most history") would also become a v1.1 case. Under the decision above, such a
  failure means we stop. Whether a test that compares only the 60 v1.0 cases counts as "the existing test
  passing" is a decision for when the submodule moves; it is not made here.
- **Status:** B1b-1 tooling at `b5ff756`; no case has been run.

## 2026-11-23 21:40 UTC: slot 1/30, inj-090 [build], decoy, attempt 1 of this slot
- Command: `.\.venv\Scripts\python.exe injections\run_injection.py injections\specs\inj-090.json` (default
  `--wait-for-green 15`); harness commit `bd588d8`
- Commits: injection `7122823` (parent `bd588d8`); discarded/recreated `a8d0a3b` (decoy attempt), recreated as
  `7122823` with the same tree (`9228bc1`) and message and a new committer date (21:40:20 to 21:50:25); revert
  `c6fc6fd`
- Runs: triaged 45273725947 F; decoy 45272047624 F; discarded none; parent (force-back) 45272459413 P;
  verification none. Revert run 45274127808 P.
- Result: match=Y, outcomes=F, extras=decoy:F,parent:P, attempts=2 (the decoy attempt plus the triaged commit;
  this slot was run once). Exit 0.
- Unexpected: nothing. Both logs fail as the spec notes predict: `docker` job, step `Build image`, at
  `[5/9] RUN pip install ...` with `unable to find user appuser: no matching entries in passwd file`, after the
  apt layer succeeded and before any Python package was downloaded (Build image step 14 s; docker job 19 s).
  `smoke` passed (13 tests) in both. The harness reported run 45273725947 as in_progress, then queued, then
  failure. That is GitHub's run status at the `needs: smoke` hand-off, not a rerun: `smoke` finished at
  21:52:29, `docker` was created at 21:52:29 and waited for a runner until 21:52:31, and the run reports queued
  while its only pending job waits. `run_attempt` is 1 and the run has exactly two jobs.

## 2026-11-23 22:39 UTC: slot 2/30, inj-062 [real_regression], fail_same, attempt 1 of this slot
- Command: `.\.venv\Scripts\python.exe injections\run_injection.py injections\specs\inj-062.json` (default
  `--wait-for-green 15`; command form as for slot 1, console output not attached to the session that wrote this
  entry); harness commit `b980783` (harness code unchanged since `bd588d8`)
- Commits: injection `2d710d7` (parent `b980783`); discarded/recreated none; revert `42e3a28`
- Runs: triaged (run B) 45283114753 F; run A 45281723668 F; decoy none; discarded none; parent (force-back)
  45282098318 P; verification none. Revert run 45283445453 P.
- Result: match=Y, outcomes=F, extras=parent:P, same_commit=fail_same (A F, B F), attempts=1. Exit 0.
- Unexpected: nothing. Both logs fail as the spec notes predict: `smoke` job ("Import & route smoke tests"),
  step 6 `Run smoke tests`, `tests/test_behaviour.py::test_chunking_char_count_matches_content_length` with
  `AttributeError: 'DocumentChunk' object has no attribute 'char_count'`, 1 failed / 12 passed; `docker` skipped.
  The harness's `same_commit_signatures` hold only the masked pytest summary and exit-code lines; the
  `AttributeError` line was compared by hand and is identical in both logs. With timestamps stripped, the two logs
  differ only in runner worker id, region, temp paths, the test's random document UUID and the pytest duration
  (4.34 s vs 5.62 s). Run A's log is 853 bytes (10 lines) longer: in setup-python's pip-cache restore
  (2,856,191,359 bytes) run A printed 22 progress lines at about 125 MB/s (westus3), run B 12 at about 235 MB/s
  (eastus), 852 bytes; the last byte is the region name. The target file keeps its UTF-8 BOM; git stores it with
  LF (`i/lf`; the CRLF seen on Windows is `core.autocrlf=true` at checkout), and `2d710d7` changes line 65 only,
  with no line-ending change. The revert restores the parent tree exactly. Run order A (22:39:15), P (22:41:42),
  B (22:48:15) as planned.
- Open question closed (plan section 3, "Unverified assumptions"): a re-pushed commit to `main` starts a new run.
  Run B 45283114753 is a new run id on the same sha as run A (`2d710d7bf7db10ccf1099858b2dbadf2b609ab7f`), with
  `run_attempt` 1 and event `push`. The force-back also confirms the second assumption: `main` accepted the force
  push, and it started a new run (45282098318) on `b980783`, which already had run 45276287232.

## 2026-11-24 20:45 UTC: slot 3/30, inj-072 [dependency], decoy, attempt 1 of this slot
- Command: `.\.venv\Scripts\python.exe injections\run_injection.py injections\specs\inj-072.json` (default
  `--wait-for-green 15`; command form as for slots 1 and 2. The operator reported exit 0, but the console output
  was not attached to the session that wrote this entry, so the command is not confirmed from it); harness commit
  `01ea7a6` (harness code unchanged since `bd588d8`)
- Commits: injection `9739013` (parent `01ea7a6`); discarded/recreated `45c6533` (decoy attempt), recreated as
  `9739013` with the same tree (`e7771c4`), message and author date, and a new committer date (20:45:39 to
  20:54:01); revert `3a18f29`
- Runs: triaged 45485565146 F; decoy 45484139250 F; discarded none; parent (force-back) 45484275547 P (13 passed);
  verification none. Revert run 45485754207: `smoke` passed and `docker` was still in progress when this entry
  was written (21:01).
- Result: match=Y, outcomes=F, extras=decoy:F,parent:P, attempts=2 (the decoy attempt plus the triaged commit;
  this slot was run once). Exit 0 (operator's report).
- Unexpected: nothing. Both logs fail as the spec notes predict: `smoke` job, step `Install dependencies`
  (`pip install -r requirements.txt`), with `ERROR: Ignored the following yanked versions: 2.6, 2.6.1`, then
  `ERROR: Ignored the following versions that require a different python version: 3.7 Requires-Python
  >=3.12,!=3.14.1; 3.7rc0 Requires-Python >=3.12,!=3.14.1`, then `Could not find a version ...` and
  `ERROR: No matching distribution found for networkx==3.7`, exit code 1; no pytest summary; `docker` skipped.
  These four lines were compared by hand and are identical in both logs, and match the local pip 24.0 check
  word for word. The quick failure is that pip error, not a setup or runner problem: in both runs checkout
  succeeded, setup-python set up CPython 3.11.18 and restored the pip cache, and the failure comes after that, in
  `Install dependencies`. Triaged job 43 s (20:54:08 to 20:54:51), most of it the 2,856,191,359-byte pip-cache
  restore; decoy job about 27 s from first to last log line. With timestamps stripped, the two logs differ only
  in worker id, region (decoy eastus, triaged westus), temp paths, the checked-out sha and the number of
  cache-restore progress lines (13 vs 26). `9739013` changes line 65 of `requirements.txt` only
  (`networkx==3.6.1` to `networkx==3.7`): ASCII, LF, no BOM, 2,616 to 2,614 bytes. The revert restores the
  parent tree exactly. `main` and `origin/main` are both at `3a18f29`; no `wip-` branch is left locally or on
  origin.

## 2026-11-24 21:01 UTC: correction to the slot 2 entry (inj-062), command confirmed
- The slot 2 entry says the console output was not attached and gives the command only "as for slot 1". The
  operator confirms the slot 2 command was exactly:
  `.\.venv\Scripts\python.exe injections\run_injection.py injections\specs\inj-062.json`

## 2026-11-24 21:01 UTC: standing procedure, error lines in rerun cases
- For every rerun case, compare the error lines of both runs (run A and run B) by hand and record them in the
  entry. `same_commit_signatures` alone is not enough: it holds only the masked pytest summary and exit-code
  lines (see slot 2), so it cannot show that the two runs failed on the same error.
- An offline check over all 12 rerun cases runs after slot 30.

## 2026-11-24 22:06 UTC: slot 4/30, inj-089 [build], decoy, attempt 1 of this slot
- Command: `.\.venv\Scripts\python.exe injections\run_injection.py injections\specs\inj-089.json` (default
  `--wait-for-green 15`), confirmed from the console transcript the operator saved outside the repo in
  `C:\Users\Pitbull\Documents\holdout-console\`. It is saved under a mangled file name, not the `inj-089.txt`
  that was announced. Harness commit `65a3352` (harness code unchanged since `bd588d8`).
- Commits: injection `0f82875` (parent `65a3352`); discarded/recreated `544090f` (decoy attempt), recreated as
  `0f82875` with the same tree (`9a8df58`), message and author date, and a new committer date (22:06:08 to
  22:15:56); revert `76ab099`
- Runs: triaged 45499287478 F; decoy 45497666802 F; discarded none; parent (force-back) 45498082081 P;
  verification none. Revert run 45499686936 P (smoke and docker both success, docker done 22:25:54).
- Result: match=Y, outcomes=F, extras=decoy:F,parent:P, attempts=2 (the decoy attempt plus the triaged commit;
  this slot was run once). Console summary `inj-089 build mode=decoy outcomes=F extras=decoy:F,parent:P
  attempts=2 match=Y`, exit 0.
- Unexpected: nothing. Both logs fail as the spec notes predict. In each run, `smoke` passed (all six steps
  succeeded; 13 passed, 0 failed, parsed by the harness from the smoke log) and `docker` ("Docker image builds")
  failed at step 3 `Build image`, so no job was skipped. The error is at `#8 [4/9] COPY requirements.txt .`
  (Dockerfile:28): `failed to calculate checksum of ref ...: "/requirements.txt": not found`, then
  `ERROR: failed to build: failed to solve: failed to compute cache key: ...`, exit code 1. The concurrent
  `[2/9] RUN apt-get ...` was CANCELED. Lines compared by hand: identical in both logs apart from the BuildKit
  ref ids. Docker job 14 s triaged (22:18:02 to 22:18:16), 6 s decoy; from the start of `docker build` to the
  error took 5.5 s triaged and 2.0 s decoy. With timestamps stripped, the two logs also differ in worker id, region and cloud (decoy
  southcentralus/Azure, triaged us-east-1/AWS), runner image build (20261116.202.1 vs 20261116.88.1, same
  image version), temp paths, the checked-out sha, the metadata-load time and the order in which BuildKit
  printed `[internal] load build context` and `[1/9] FROM`.
  The saved log is 12,837 bytes (decoy 12,852) because `observe()` saves only the failing job's log
  (`run_injection.py`, the failing-job log comes first and is kept as evidence). Here that is the docker job:
  checkout plus a build that stops at its fourth step, with no setup-python, no pip-cache restore progress lines
  and no `pip install` output. The smoke log is read only for the pytest counts and is not saved. inj-072's
  28,390-byte log was a smoke job.
  `0f82875` adds one line, `*.txt`, at the end of `.dockerignore` (line 51, after `*.log`) and changes nothing
  else: ASCII, stored with LF (`i/lf`; `w/crlf` is `core.autocrlf` at checkout), 940 to 946 bytes. The revert
  restores the parent tree exactly. `main` and `origin/main` are both at `76ab099`; no `wip-` branch is left
  locally or on origin.

## 2026-11-24 22:34 UTC: correction to the slot 3 entry (inj-072), command and exit confirmed
- The slot 3 entry gives the command only as "command form as for slots 1 and 2" and says it was not confirmed
  from console output. The operator confirms the command was exactly
  `.\.venv\Scripts\python.exe injections\run_injection.py injections\specs\inj-072.json`, that it ended with
  `inj-072 dependency mode=decoy outcomes=F extras=decoy:F,parent:P attempts=2 match=Y`, and that it exited 0.
  The console transcript saved for slot 4 also covers the slot 3 run and shows the same command, summary line
  and `$LASTEXITCODE` 0.

## 2026-11-24 22:34 UTC: follow-up to the slot 3 entry (inj-072), revert run completed
- The slot 3 entry says revert run 45485754207 (`3a18f29`) had `smoke` passed and `docker` still in progress at
  21:01. It completed P: `smoke` success (20:55:15 to 20:57:21) and `docker` success (20:57:24 to 21:02:29).

## 2026-11-24 22:59 UTC: slot 5/30, inj-085 [timeout], single, attempt 1 of this slot
- Command: `.\.venv\Scripts\python.exe injections\run_injection.py injections\specs\inj-085.json` (default
  `--wait-for-green 15`), confirmed from the console output in
  `C:\Users\Pitbull\Documents\holdout-console\inj-085.txt` (outside the repo; the first export under that name
  was the wrong terminal tab and was replaced at 23:52); harness commit `7d58553` (harness code unchanged since
  `bd588d8`)
- Commits: injection `f8510f7` (parent `7d58553`); discarded/recreated none; revert `7ee4466`
- Runs: triaged 45505927824 F; decoy none; discarded none; parent (force-back) none; verification none. Revert
  run 45506721670 P. One push: 45505927824 is the only run on `f8510f7`.
- Result: match=Y, outcomes=F, extras=none, attempts=1. Console summary `inj-085 timeout mode=single outcomes=F
  extras=none attempts=1 match=Y`, exit 0.
- Unexpected: one departure from the spec notes (plan vs reality, below); otherwise as predicted. `smoke` job,
  step 6 `Run smoke tests` failed: `##[error]The action 'Run smoke tests' has timed out after 3 minutes.`, which
  is also the job's only annotation; `docker` skipped. Steps 1-5 succeeded (setup-python restored the pip cache,
  install and clean import passed). pytest collected 13 items, and 9 PASSED by 23:01:04. The log is then silent
  until the timeout at 23:04:10 (step 23:00:57 to 23:04:10, about 3 m 12 s). There is no pytest summary, and
  cleanup ends with `Terminate orphan process: pid (2518) (python)`, so pytest was still running when the step
  was stopped: the step's timeout, not a crash or a runner problem. Job 298 s.
  **Plan vs reality.** The spec notes predict that the last `-v` line is
  `tests/test_behaviour.py::test_chunking_splits_long_text_into_multiple_chunks`. That line is not in the log:
  the last pytest line is `tests/test_behaviour.py::test_chunking_returns_empty_list_for_blank_text PASSED [ 69%]`,
  followed directly by the timeout line. The hanging test is identified only by inference: it is the 10th of the
  13 collected tests, and the plan's local check (section 6, inj-085 row) saw it hang. v1.0 inj-044's log has the
  same shape (9th PASSED, then the timeout line), so the inj-085 vs inj-044 resemblance in plan section 7 holds,
  but the text the two logs share is the last PASSED line and the timeout line, not the hanging test's name.
  *Hypothesis, not verified:* `pytest -v` writes the test id without a newline and finishes the line only when
  the test ends; the runner records complete lines, so the unfinished line was lost when the step was killed.
  **Effect on the label and grader (checked):** none depends on the test name appearing in the log. The lab's
  label fields come from the spec and the API: `category` from the spec; `failing_step` and the failing job from
  the job's step conclusions (`failing_step_inferred` false); `expected_failing_tests` is empty, so
  `build_index.py` skips the failing-tests check. The agent repo's ground truth (`evaluation/ground_truth.py`)
  reads category, target file, find/replace, failing job and step from `index.json` and the spec, and the
  restored/removed lines from the revert; it does not read `notes`. The grader (`evaluation/grader.py`) scores
  category, step, job, file, fix match and action against those, and grounds quotes only against the text the
  tools actually returned, so a quote of the missing line would be ungrounded, which is correct.
  Also found: the harness's own match check (`run_injection.py` `compare`) checks the failing job, skipped jobs
  and the failing-test set, and that a failing step exists. It does not check that the failure is a timeout, so
  for inj-085 match=Y would also hold for any other failure of that step that leaves no pytest summary. The
  timeout itself was confirmed by hand, above.
  `f8510f7` adds the spec's six lines after line 86 of `api/services/chunking_service.py` and changes nothing
  else: UTF-8 without BOM, stored with LF (`i/lf`), 4,356 to 4,590 bytes. The revert restores the parent tree
  exactly. `main` and `origin/main` are both at `7ee4466`; no `wip-` branch is left locally or on origin.

## 2026-11-24 23:55 UTC: standing procedure extended, spec predictions in the offline check
- Extends the standing procedure entry of 2026-11-24 21:01 UTC (error lines in rerun cases). The offline check
  after slot 30 also verifies that each case's failing log contains the error text its spec notes predict
  ("has timed out after" for timeout cases), and records every spec prediction that turned out wrong (slot 5,
  inj-085: the hanging test's `-v` line, see that entry).
- Reason: the harness's match check (`compare`) checks jobs, skipped jobs, failing tests and that a failing step
  exists, not the error text, so a case can match with a different failure than the one its notes describe.

## 2026-11-25 16:52 UTC: slot 6/30, inj-071 [dependency], fail_same, attempt 1 of this slot
- Command: `.\.venv\Scripts\python.exe injections\run_injection.py injections\specs\inj-071.json` (default
  `--wait-for-green 15`), confirmed from the console output in
  `C:\Users\Pitbull\Documents\holdout-console\inj-071.txt` (outside the repo; exported at 16:50, after the
  session that checked this slot first found no file under that name); harness commit `483fb90` (harness code
  unchanged since `bd588d8`)
- Commits: injection `3fb69f0` (parent `483fb90`), committed on `wip-a0c515` and pushed to `main`;
  discarded/recreated none; revert `d028ae5`
- Runs: triaged (run B) 45654014154 F; run A 45652715678 F; decoy none; discarded none; parent (force-back)
  45652898652 P (13 passed); verification none. Revert run 45654218522 P.
- Result: match=Y, outcomes=FF, extras=parent:P, attempts=1. Console summary `inj-071 dependency mode=fail_same
  outcomes=FF extras=parent:P attempts=1 match=Y`, exit 0 (`$LASTEXITCODE` 0).
- Unexpected: nothing. Both runs fail as the spec notes predict: `smoke` job, step `Install dependencies`
  (`pip install -r requirements.txt`), `docker` skipped, no pytest summary. In both runs checkout succeeded,
  setup-python set up CPython 3.11.18 and restored the pip cache (same key), and the failure comes after that.
  **Error lines, run A vs run B (standing procedure of 2026-11-24 21:01 UTC).** All `ERROR` and `##[error]`
  lines were extracted from both logs, timestamps stripped, and diffed: identical, 5 lines and 1,749 bytes each:
  `ERROR: Ignored the following yanked versions: 2.4.0`; `ERROR: Ignored the following versions that require a
  different python version: ...` (includes `2.5.0 Requires-Python >=3.12`); `ERROR: Could not find a version
  that satisfies the requirement numpy==2.5.0 (from versions: ... 2.4.6)`; `ERROR: No matching distribution
  found for numpy==2.5.0`; `##[error]Process completed with exit code 1.`
  **Spec prediction (standing procedure of 2026-11-24 23:55 UTC).** Holds: both predicted lines (`Could not find
  a version that satisfies the requirement numpy==2.5.0`, `No matching distribution found for numpy==2.5.0`) are
  in both logs; 2.5.0 is in the Ignored line with its Requires-Python and absent from the `from versions` list;
  the yanked-versions line seen in the local pip 24.0 check is also there. No spec prediction turned out wrong.
  Run A job about 34 s from first to last log line (16:20:32 to 16:21:06); run B job 46 s (16:27:17 to
  16:28:03). The two logs differ in length (304 vs 316 lines) only in the setup-python section (12 more
  cache-restore lines in run B). `3fb69f0` changes line 66 of `requirements.txt` only (`numpy==2.4.4` to
  `numpy==2.5.0`): ASCII, stored with LF (`i/lf`), 2,616 bytes before and after. The revert restores the parent
  tree exactly. `main` and `origin/main` are both at `d028ae5`; no `wip-` branch is left locally or on origin.

## 2026-11-25 17:49 UTC: slot 7/30, inj-061 [real_regression], decoy, attempt 1 of this slot
- Command: `.\.venv\Scripts\python.exe -u injections\run_injection.py injections\specs\inj-061.json` (default
  `--wait-for-green 15`), run by Claude Code in the background with all output written to
  `C:\Users\Pitbull\Documents\holdout-console\inj-061.txt` (outside the repo). The exit code was captured by the
  wrapping shell and is not in that file. Harness commit `9ab6293` (harness code unchanged since `bd588d8`).
- Commits: injection `918da45` (parent `9ab6293`); discarded/recreated `ba13849` (decoy attempt), recreated as
  `918da45` with the same tree (`6ffcfb8`), message and author date, and a new committer date (17:49:48 to
  18:00:10); revert `7adf517`
- Runs: triaged 45670616233 F; decoy 45668849388 F; discarded none; parent (force-back) 45669337564 P (13 passed);
  verification none. Revert run 45671000212 P (smoke and docker both success, docker done 18:08:21).
- Result: match=Y, outcomes=F, extras=decoy:F,parent:P, attempts=2 (the decoy attempt plus the triaged commit;
  this slot was run once). Console summary `inj-061 real_regression mode=decoy outcomes=F extras=decoy:F,parent:P
  attempts=2 match=Y`, exit 0.
- Unexpected: nothing. Both logs fail as the spec notes predict: `smoke` job, steps 1-5 succeeded (the import
  step printed `CLEAN IMPORT OK`), step 6 `Run smoke tests` failed; `docker` skipped.
  `tests/test_app.py::test_root_endpoint` fails at `assert body["status"] == "running"` (tests/test_app.py:40) with
  `KeyError: 'status'`, `1 failed, 12 passed`, then `##[error]Process completed with exit code 1.`
  Lines compared by hand (decoy vs triaged; `##[error]`, `E`, `KeyError`, `FAILED` and summary lines, timestamps
  stripped): identical apart from the pytest duration (3.67 s triaged, 4.57 s decoy). Smoke job 104 s triaged
  (18:00:17 to 18:02:01), 147 s decoy (17:49:54 to 17:52:21). Logs 74,020 bytes / 697 lines triaged and 73,936
  bytes / 696 lines decoy. As the notes say, the log names the missing key but not the new one.
  **Spec prediction (standing procedure of 2026-11-24 23:55 UTC).** Holds: `KeyError: 'status'` at
  `body["status"]`, 1 failed / 12 passed, import step passes. No spec prediction turned out wrong.
  `918da45` changes line 92 of `api/main.py` only (`"status": "running",` to `"state": "running",`): UTF-8
  without BOM, stored with LF (`i/lf`; `w/crlf` is `core.autocrlf` at checkout), 3,738 to 3,737 bytes. The revert
  restores the parent tree exactly (`4734709`). `main` and `origin/main` are both at `7adf517`; no `wip-` branch
  is left locally or on origin.

## 2026-11-25 18:16 UTC: slot 8/30, inj-088 [build], fail_same, attempt 1 of this slot
- Command: `.\.venv\Scripts\python.exe -u injections\run_injection.py injections\specs\inj-088.json` (default
  `--wait-for-green 15`), run by Claude Code in the background with all output written to
  `C:\Users\Pitbull\Documents\holdout-console\inj-088.txt` (outside the repo). The exit code was captured by the
  wrapping shell and is not in that file. Harness commit `1c4db53` (harness code unchanged since `bd588d8`).
- Commits: injection `8276fbc` (parent `1c4db53`), committed on `wip-b120a3` and pushed to `main`;
  discarded/recreated none; revert `90c6fa0`
- Runs: triaged (run B) 45675386098 F; run A 45673534583 F; decoy none; discarded none; parent (force-back)
  45673956631 P (13 passed); verification none. Revert run 45675800661 P (smoke and docker both success, docker
  done 18:36:56).
- Result: match=Y, outcomes=FF, extras=parent:P, attempts=1. Console summary `inj-088 build mode=fail_same
  outcomes=FF extras=parent:P attempts=1 match=Y`, exit 0.
- Unexpected: nothing; one cosmetic log difference between A and B, below. Both runs fail as the spec notes
  predict: `smoke` passed (all six steps succeeded; 13 passed), `docker` ("Docker image builds") failed at step 3
  `Build image`; no job skipped. BuildKit reports `InvalidDefaultArgInFrom: Default value for ARG
  python:${PYTHON_VERSION}-slim results in empty or invalid base image name (line 13)` in its end-of-build warning
  summary, shows `Dockerfile:13` (`>>> FROM python:${PYTHON_VERSION}-slim`), then `ERROR: failed to build: failed
  to solve: failed to parse stage name "python:-slim": invalid reference format` and `##[error]Process completed
  with exit code 1.`
  **Error lines, run A vs run B (standing procedure of 2026-11-24 21:01 UTC).** All `ERROR`, `##[error]`, `WARN`
  and `InvalidDefaultArgInFrom` lines were extracted from both logs, timestamps stripped, and diffed. The `ERROR`
  and `##[error]` lines are identical (the two lines above), and so are the harness's `same_commit_signatures`. The
  one difference: run B's step `#1 [internal] load build definition` prints an inline `#1 WARN:
  InvalidDefaultArgInFrom ...` line (`#1 DONE 0.0s`); run A's does not, and prints an extra
  `#1 transferring dockerfile:` progress line instead (`#1 DONE 0.2s`). Both logs carry the warning in the
  end-of-build summary. This is BuildKit's plain progress output, which depends on step timing, not a different
  failure. Otherwise the two logs (144 lines each; 11,370 bytes A, 11,489 B) differ only in temp paths.
  **Spec prediction (standing procedure of 2026-11-24 23:55 UTC).** Holds: `failed to parse stage name
  "python:-slim": invalid reference format`, preceded by the `InvalidDefaultArgInFrom` warning, in both logs;
  smoke passes; fails within seconds (`docker build` start to error 9.8 s A, 1.7 s B; docker job 15 s A,
  18:19:00 to 18:19:15, and 7 s B, 18:29:51 to 18:29:58). No spec prediction turned out wrong.
  Run order A (18:16:58), P (18:19:27), B (18:27:42) as planned. `8276fbc` replaces line 12 of `Dockerfile`
  (`FROM python:3.11-slim`) with two lines (`ARG PYTHON_VERSION`, `FROM python:${PYTHON_VERSION}-slim`) and
  changes nothing else: UTF-8 without BOM, stored with LF (`i/lf`), 2,380 to 2,412 bytes. The revert restores
  the parent tree exactly (`d604ab4`). `main` and `origin/main` are both at `90c6fa0`; no `wip-` branch is left
  locally or on origin.

## 2026-11-25 18:45 UTC: slot 9/30, inj-069 [flaky], flaky, attempt 1 of this slot
- Command: `.\.venv\Scripts\python.exe -u injections\run_injection.py injections\specs\inj-069.json` (default
  `--wait-for-green 15`), run by Claude Code in the background with all output written to
  `C:\Users\Pitbull\Documents\holdout-console\inj-069.txt` (outside the repo). The exit code was captured by the
  wrapping shell and is not in that file. Harness commit `9d3ddc7` (harness code unchanged since `bd588d8`).
- Commits: injection `8ddc06a` (parent `9d3ddc7`), committed on `wip-0944fe` and pushed to `main`;
  discarded/recreated `f7656fd`, `6e37df5`, `b4cb39c`, `682ce23`, `b00abdd`, `e518d13`, `c65b536` (attempts 1 to
  7), each recreated with the same tree (`c0b3919`), parent, message and author date (18:45:02) and a new
  committer date (18:45:02 to 20:26:11 across the eight commits); revert `5121c2e`
- Runs: triaged 45694950047 F (attempt 8); decoy none; discarded 45678315062, 45680646188, 45683033620,
  45685223004, 45687754935, 45690108891, 45692529720 (all P, 13 passed); parent (force-back) 45679515500,
  45681864662, 45684123414, 45686522846, 45688944453, 45691399192, 45693723868 (all P, 13 passed); verification
  none (the discarded passing runs give the green run of the same code, plan section 3). Revert run 45695328843 P
  (smoke and docker both success, docker done 20:35:21).
- Result: match=Y, outcomes=F, extras=discarded:P,parent:P seven times, attempts=8. Console summary `inj-069 flaky
  mode=flaky outcomes=F extras=discarded:P,parent:P,discarded:P,parent:P,discarded:P,parent:P,discarded:P,parent:P,discarded:P,parent:P,discarded:P,parent:P,discarded:P,parent:P
  attempts=8 match=Y`, exit 0.
- Unexpected: the first 7 attempts passed (plan vs reality, below); the case itself failed as predicted. The
  harness stopped at the first failing run, within its 10-attempt cap. `smoke` job, steps 1-5 succeeded, step 6
  `Run smoke tests` failed; `docker` skipped. `tests/test_behaviour.py::test_chat_request_accepts_valid_question`
  FAILED with `pydantic_core._pydantic_core.ValidationError: 1 validation error for ChatRequest`, `question`
  `String should have at least 3 characters [type=string_too_short, input_value='?', input_type=str]`,
  `1 failed, 12 passed in 4.38s`, then `##[error]Process completed with exit code 1.` Smoke job 124 s (20:26:18
  to 20:28:22). Log 75,280 bytes / 708 lines.
  **Spec prediction (standing procedure of 2026-11-24 23:55 UTC).** Holds for the failure: the stale
  one-character draft `'?'` was chosen and `ChatRequest` raised `ValidationError` (`string_too_short`), only the
  expected test failed. The rate is discussed below.
  **Why the 7 passes are taken as chance, not a broken case (checked during the slot).** All 8 commits have the
  tree `c0b3919` and the parent `9d3ddc7`. The log of one passing run (attempt 4) shows it checked out the
  injected commit and ran and passed the injected test. `uuid4` is the standard library's, and the test file has
  no seed or patch. A local repeat of the test's comparison chose the stale draft in 50.5% of 20,000 tries.
  **Plan vs reality (lab owner's note).** 7 passing attempts before the failure at attempt 8. At the measured
  Windows rate (60% fail, plan section 6: 18/30) that streak is about 0.2% (0.4^7 = 0.16%), so the CI fail rate
  for inj-069 looks much lower than measured on Windows (rough estimate: 1 failure in 8 attempts).
  *Checked while writing this entry:* the outcome is set by comparing two `uuid4` values, which Python draws from
  `os.urandom`, so the mechanism does not depend on the platform and gives 50%. 18/30 on Windows is within
  sampling error of 50%, and at 50% the streak has probability 0.5^7 = 0.78%. The 1-in-8 figure comes from one
  sample that stops at the first failure, so it is a rough estimate as stated, not a measured rate.
  **Known residual signal (lab owner's note).** The history before the triaged run holds 7 green runs of the
  identical tree, a stronger flaky hint than in other flaky cases. The B3 evaluation must mention it for inj-069.
  *Checked while writing this entry:* the sealed replay (agent repo `src/ci_triage/replay/source.py`,
  `ReplaySource`, last changed in `2da88da`) scopes runs by sha, not by tree: the case's own commits
  (`9d3ddc7..8ddc06a`, here only `8ddc06a`), earlier runs of `8ddc06a` itself, and one baseline run (the latest
  green run on the parent or one of its ancestors before the cutoff, here 45693723868). The 7 discarded runs are on other
  shas, so `list_runs` and `get_run` do not serve them. They are in the full run history on GitHub and will be in
  `runs-main.json` once the v1.1 runs are appended. The other 6 force-back runs on `9d3ddc7` are out of scope
  the same way.
  **Decoy-audit marks (plan section 3).** The seven discards add seven extra baseline runs on `9d3ddc7`, more than
  the one a decoy case adds; the audit after slot 30 counts them with the run-number gaps.
  `8ddc06a` replaces line 22 of `tests/test_behaviour.py` (`request = ChatRequest(question="What is RAG?")`) with
  the spec's five lines and changes nothing else (`uuid4` was already imported at line 7): UTF-8 without BOM,
  stored with LF (`i/lf`; `w/crlf` is `core.autocrlf` at checkout), 2,997 to 3,160 bytes. The revert restores the
  parent tree exactly (`9ad9a91`). `main` and `origin/main` are both at `5121c2e`; no `wip-` branch is left
  locally or on origin.

## 2026-11-27 15:41 UTC: slot 10/30, inj-076 [environment], fail_same, attempt 1 of this slot
- Command: `.\.venv\Scripts\python.exe -u injections\run_injection.py injections\specs\inj-076.json` (default
  `--wait-for-green 15`), run by Claude Code in the background with all output written to
  `C:\Users\Pitbull\Documents\holdout-console\inj-076.txt` (outside the repo). The exit code was captured by the
  wrapping shell and is not in that file. Harness commit `4bbde4e` (harness code unchanged since `bd588d8`).
- Commits: injection `c81c021` (parent `4bbde4e`), committed on `wip-6fced1` and pushed to `main`;
  discarded/recreated none; revert `92d4e70`
- Runs: triaged (run B) 46028341898 F; run A 46027132583 F; decoy none; discarded none; parent (force-back)
  46027440061 P (13 passed); verification none. Revert run 46028647070 P (smoke and docker both success, docker
  done 15:57:48).
- Result: match=Y, outcomes=FF, extras=parent:P, attempts=1. Console summary `inj-076 environment mode=fail_same
  outcomes=FF extras=parent:P attempts=1 match=Y`, exit 0.
- Unexpected: nothing. Both runs fail as the spec notes predict: `smoke` job, steps 1-5 succeeded (install
  passed and the import step printed `CLEAN IMPORT OK`), step 6 `Run smoke tests` failed; `docker` skipped. The
  step fails at once, before pytest starts, with `/home/runner/work/_temp/<uuid>.sh: line 1: SMOKE_TEST_PATHS:
  SMOKE_TEST_PATHS is not set`, then `##[error]Process completed with exit code 1.` No pytest output at all
  (no session header, no summary), so `failed`/`passed` are null in the record. Every run is a `push` run on
  `main` with `run_attempt` 1.
  **Error lines, run A vs run B (standing procedure of 2026-11-24 21:01 UTC).** All `ERROR`, `##[error]` and
  `is not set` lines were extracted from both logs, timestamps stripped, and diffed: identical apart from the
  runner's temp script name (`0aed073f-...sh` in A, `7a717d10-...sh` in B). The harness's
  `same_commit_signatures` (which mask that name) are identical too.
  **Spec prediction (standing procedure of 2026-11-24 23:55 UTC).** Holds: install and import pass; `Run smoke
  tests` fails at once with `...sh: line 1: SMOKE_TEST_PATHS: SMOKE_TEST_PATHS is not set` and exit code 1;
  no pytest summary. No spec prediction turned out wrong.
  Run order A (15:41:25), P (15:43:34), B (15:49:46) as planned. Smoke job 109 s A (15:41:28 to 15:43:17), 99 s
  B (15:49:49 to 15:51:28), most of it the pip-cache restore and `pip install`; the failing step took about 0.01 s
  in both. Logs 70,975 bytes / 660 lines A and 70,806 bytes / 658 lines B. With timestamps stripped they differ
  only in worker id, region and cloud (A eastus/Azure, B us-east-1/AWS), temp paths, the number of cache-restore
  progress lines (12 vs 10) and the timestamp in the app's own startup log line.
  `c81c021` changes line 25 of `.github/workflows/ci.yml` only (`run: python -m pytest tests -v` to
  `run: python -m pytest "${SMOKE_TEST_PATHS:?SMOKE_TEST_PATHS is not set}" -v`): ASCII, stored with LF
  (`i/lf`; `w/crlf` is `core.autocrlf` at checkout), 810 to 855 bytes. The revert restores the parent tree
  exactly (`b5b4a1b`). `main` and `origin/main` are both at `92d4e70`; no `wip-` branch is left locally or on
  origin.

## 2026-11-27 16:06 UTC: slot 11/30, inj-063 [real_regression], single, attempt 1 of this slot
- Command: `.\.venv\Scripts\python.exe -u injections\run_injection.py injections\specs\inj-063.json` (default
  `--wait-for-green 15`), run by Claude Code in the background with all output written to
  `C:\Users\Pitbull\Documents\holdout-console\inj-063.txt` (outside the repo). The exit code was captured by the
  wrapping shell and is not in that file. Harness commit `b21389d` (harness code unchanged since `bd588d8`).
- Commits: injection `a385f85` (parent `b21389d`), committed on `wip-8142c8` and pushed to `main`;
  discarded/recreated none; revert `6b850b2`
- Runs: triaged 46030766187 F; decoy none; discarded none; parent (force-back) none; verification none. Revert
  run 46031123210 P (smoke and docker both success, docker done 16:16:26). One push: 46030766187 is the only run
  on `a385f85` (`push`, `run_attempt` 1).
- Result: match=Y, outcomes=F, extras=none, attempts=1. Console summary `inj-063 real_regression mode=single
  outcomes=F extras=none attempts=1 match=Y`, exit 0.
- Unexpected: nothing. The log fails as the spec notes predict: `smoke` job, steps 1-5 succeeded (the import
  step printed `CLEAN IMPORT OK`), step 6 `Run smoke tests` failed; `docker` skipped.
  `tests/test_app.py::test_health_endpoint` fails at `assert response.json()["status"] == "healthy"`
  (tests/test_app.py:47) with `KeyError: 'status'`, `1 failed, 12 passed in 4.71s`, then
  `##[error]Process completed with exit code 1.` Smoke job 135 s (16:06:39 to 16:08:54), region westus3. Log
  74,866 bytes / 706 lines. As the notes say, the log names the missing key; that the field was dropped rather
  than renamed shows only in the diff.
  **Spec prediction (standing procedure of 2026-11-24 23:55 UTC).** Holds: `KeyError: 'status'` at
  `response.json()["status"]`, 1 failed / 12 passed, import step passes. No spec prediction turned out wrong.
  `a385f85` removes line 113 of `api/main.py` (`"status": "healthy",` in `health_check()`) and changes nothing
  else: UTF-8 without BOM, stored with LF (`i/lf`; `w/crlf` is `core.autocrlf` at checkout), 3,738 to 3,709
  bytes. The revert restores the parent tree exactly (`aa09086`). `main` and `origin/main` are both at
  `6b850b2`; no `wip-` branch is left locally or on origin.

## 2026-11-27 20:04 UTC: slot 12/30, inj-087 [build], fail_same, attempt 1 of this slot
- Command: `.\.venv\Scripts\python.exe -u injections\run_injection.py injections\specs\inj-087.json` (default
  `--wait-for-green 15`), run by Claude Code in the background with all output written to
  `C:\Users\Pitbull\Documents\holdout-console\inj-087.txt` (outside the repo). The exit code was captured by the
  wrapping shell and is not in that file. Harness commit `2289d33` (harness code unchanged since `bd588d8`).
- Commits: injection `32114ee` (parent `2289d33`), committed on `wip-f0bbe9` and pushed to `main`;
  discarded/recreated none; revert `2e6f004`
- Runs: triaged (run B) 46062220345 F; run A 46061120070 F; decoy none; discarded none; parent (force-back)
  46061453445 P (13 passed); verification none. Revert run 46062513979 P (smoke and docker both success, docker
  done 20:23:22).
- Result: match=Y, outcomes=FF, extras=parent:P, attempts=1. Console summary `inj-087 build mode=fail_same
  outcomes=FF extras=parent:P attempts=1 match=Y`, exit 0.
- Unexpected: nothing. Both runs fail as the spec notes predict: `smoke` passed (all six steps succeeded;
  13 passed), `docker` ("Docker image builds") failed at step 3 `Build image`; no job skipped. Every run is a
  `push` run on `main` with `run_attempt` 1; run numbers 265 (A), 266 (P), 267 (B), 268 (revert), no gap.
  BuildKit prints only `#1 [internal] load build definition from Dockerfile` (`#1 DONE 0.0s`), then shows
  `Dockerfile:17` with lines 15-19 (`>>>` at line 17, `    PIP_NO_CACHE_DIR=1`), then `ERROR: failed to build:
  failed to solve: dockerfile parse error on line 17: unknown instruction: PIP_NO_CACHE_DIR=1` and
  `##[error]Process completed with exit code 1.` No BuildKit step after `#1` appears in either log.
  **Error lines, run A vs run B (standing procedure of 2026-11-24 21:01 UTC).** All `ERROR`, `##[error]`, `WARN`,
  `Dockerfile:` and `>>>` lines were extracted from both logs, timestamps stripped, and diffed: identical (the
  lines above; no `WARN` line in either). The harness's `same_commit_signatures` are identical too. With
  timestamps stripped, the two logs (140 lines each; 10,851 bytes A, 10,839 B) differ only in worker id, region
  and cloud (A southcentralus/Azure, B us-west-2/AWS), temp paths, and one BuildKit progress line
  (`#1 transferring dockerfile: 2.42kB 0.0s done` in A, `... 2.42kB done` in B). Both logs check out `32114ee`.
  **Spec prediction (standing procedure of 2026-11-24 23:55 UTC).** Holds: `dockerfile parse error on line 17:
  unknown instruction: PIP_NO_CACHE_DIR=1`, line number confirmed as 17; the parser rejects the file before any
  build step; smoke passes; fails within seconds (`docker build` start to error 3.1 s A, 3.9 s B; docker job 8 s
  A, 20:07:05 to 20:07:13, and 13 s B, 20:16:09 to 20:16:22). No spec prediction turned out wrong.
  Run order A (20:04:47), P (20:07:35), B (20:14:08) as planned. Smoke job 134 s A (20:04:49 to 20:07:03), 117 s
  B (20:14:10 to 20:16:07). `32114ee` changes line 16 of `Dockerfile` only (`    PYTHONDONTWRITEBYTECODE=1 \` to
  `    PYTHONDONTWRITEBYTECODE=1`) and changes nothing else: UTF-8 without BOM, stored with LF (`i/lf`; `w/crlf`
  is `core.autocrlf` at checkout), 2,380 to 2,378 bytes. The revert restores the parent tree exactly
  (`57805b9`). `main` and `origin/main` are both at `2e6f004`; no `wip-` branch is left locally or on origin.

## 2026-11-27 20:24 UTC: correction to the slot 9 entry (inj-069), rate and residual signal
- Corrects the two lab owner's notes in the slot 9 entry. Recorded with the slot 12 entry, in the same commit;
  no extra CI run.
- **Note 1 ("Plan vs reality").** The operator withdraws its conclusion that the CI fail rate for inj-069 is much
  lower than the Windows measurement (rough estimate 1 failure in 8 attempts). The test compares two `uuid4`
  values, so it fails 50% of the time on any platform. The 7-pass streak is bad luck (0.5^7 = 0.78% at 50%),
  not a lower CI rate.
- **Note 2 ("Known residual signal").** It applies only to predictors that read the full run listing (the run
  history on GitHub, or `runs-main.json` once the v1.1 runs are appended). The sealed replay scopes runs by
  commit sha, so it does not serve the 7 discarded runs or the other 6 force-back runs on `9d3ddc7`.

## 2026-11-27 20:30 UTC: slot 13/30, inj-066 [flaky], flaky, attempt 1 of this slot
- Command: `.\.venv\Scripts\python.exe -u injections\run_injection.py injections\specs\inj-066.json` (default
  `--wait-for-green 15`), run by Claude Code in the background with all output written to
  `C:\Users\Pitbull\Documents\holdout-console\inj-066.txt` (outside the repo). The exit code was captured by the
  wrapping shell and is not in that file. Harness commit `ac55175` (harness code unchanged since `bd588d8`).
- Commits: injection `ba81c45` (parent `ac55175`), committed on `wip-627655` and pushed to `main`;
  discarded/recreated `465591d` (attempt 1), recreated as `ba81c45` with the same tree (`cff33b4`), parent,
  message and author date (20:30:21) and a new committer date (20:30:21 to 20:44:26); revert `e84c1d0`
- Runs: triaged 46065674059 F (attempt 2); decoy none; discarded 46064081357 P (13 passed); parent (force-back)
  46064917718 P (13 passed); verification none (the discarded passing run gives the green run of the same code,
  plan section 3). Revert run 46065954672 P (smoke and docker both success, docker done 20:54:15).
- Result: match=Y, outcomes=F, extras=discarded:P,parent:P, attempts=2. Console summary `inj-066 flaky
  mode=flaky outcomes=F extras=discarded:P,parent:P attempts=2 match=Y`, exit 0.
- Unexpected: nothing. The harness stopped at the first failing run, within its 10-attempt cap. Every run is a
  `push` run on `main` with `run_attempt` 1; run numbers 270 (attempt 1), 271 (P), 272 (triaged), 273 (revert),
  no gap. The triaged log fails as the spec notes predict: `smoke` job, steps 1-5 succeeded (the import step
  printed `CLEAN IMPORT OK`), step 6 `Run smoke tests` failed; `docker` skipped.
  `tests/test_behaviour.py::test_chunking_attaches_sequential_indexes_and_document_id` FAILED at
  `assert all(chunk.document_id == document_id for chunk in chunks)` (tests/test_behaviour.py:79) with
  `E assert False` / `E + where False = all(<generator object ...>)`, after the index assertion on the line
  above passed; `1 failed, 12 passed in 4.51s`, then `##[error]Process completed with exit code 1.` Smoke job
  130 s (20:44:33 to 20:46:43), region eastus2. Log 76,660 bytes / 719 lines.
  **Spec prediction (standing procedure of 2026-11-24 23:55 UTC).** Holds for the failure: the document_id
  assertion fails with `assert False` / `where False = all(...)`, and only the expected test failed. The 50% rate
  cannot be checked from two runs; one pass then one fail is consistent with it. No spec prediction turned out
  wrong.
  **Discarded attempt checked.** The smoke log of run 46064081357 (attempt 1, fetched for this check, not saved
  in the repo) checked out `465591d` and shows
  `tests/test_behaviour.py::test_chunking_attaches_sequential_indexes_and_document_id PASSED` and
  `13 passed in 4.29s` (region centralus), so the passing attempt ran the injected test.
  **Decoy-audit marks (plan section 3).** The discard adds one extra baseline run on `ac55175` (46064917718); the
  audit after slot 30 counts it with the run-number gaps.
  `ba81c45` replaces the body of `test_chunking_attaches_sequential_indexes_and_document_id` (lines 63-67, after
  the unchanged `def` line 62) with the spec's lines (now 63-79) and changes nothing else (`uuid4` was already
  imported at line 7): UTF-8 without BOM, stored with LF (`i/lf`; `w/crlf` is `core.autocrlf` at checkout),
  2,997 to 3,458 bytes. The revert restores the parent tree exactly (`b72599e`). `main` and `origin/main` are
  both at `e84c1d0`; no `wip-` branch is left locally or on origin.

## 2026-11-27 21:02 UTC: slot 14/30, inj-068 [flaky], flaky_rerun, attempt 1 of this slot
- Command: `.\.venv\Scripts\python.exe -u injections\run_injection.py injections\specs\inj-068.json` (default
  `--wait-for-green 15`), run by Claude Code in the background with all output written to
  `C:\Users\Pitbull\Documents\holdout-console\inj-068.txt` (outside the repo). The exit code was captured by the
  wrapping shell and is not in that file. Harness commit `ef6b0d1` (harness code unchanged since `bd588d8`).
- Commits: injection `d88d512` (parent `ef6b0d1`), committed on `wip-9dfb36` and pushed to `main`;
  discarded/recreated `918a2d1`, `79f4824`, `187685b`, `f6a46c2`, `c41d661`, `86ff190`, `b6217f1`, `0db3f66`,
  `d633290` (attempts 1 to 9), each recreated with the same tree (`d5336b4`), parent, message and author date
  (21:02:48) and a new committer date (21:02:48 to 23:32:03 across the ten commits); revert `d9025b3`
- Runs: triaged (run B) 46084408880 F; run A 46083029077 P (13 passed); decoy none; discarded 46067679192 F,
  46068736096 P, 46070448541 P, 46072166985 F, 46073253688 F, 46074271143 F, 46075528147 F, 46076501607 F,
  46077486753 P, 46078836590 P, 46080326955 P, 46081767174 P; parent (force-back) 46067966566, 46069571219,
  46071344833, 46072431645, 46073511438, 46074504351, 46075785135, 46076714647, 46078128050, 46079593247,
  46081101816, 46082426677, 46083690866 (all P, 13 passed; the last is attempt 10's planned A-to-B parent run);
  verification none. Revert run 46084594531 P (smoke and docker both success, docker done 23:56:27).
- Result: match=Y, outcomes=PF, extras=25 (discarded and parent runs, in the console summary), attempts=10.
  Console summary `inj-068 flaky mode=flaky_rerun outcomes=PF extras=discarded:F,parent:P,parent:P,discarded:P,discarded:P,parent:P,discarded:F,parent:P,discarded:F,parent:P,discarded:F,parent:P,discarded:F,parent:P,discarded:F,parent:P,parent:P,discarded:P,discarded:P,parent:P,parent:P,discarded:P,discarded:P,parent:P,parent:P
  attempts=10 match=Y`, exit 0.
- Unexpected: the case matched only at attempt 10, the last attempt the harness's 10-attempt cap allows. The
  session stopped before writing this entry. The operator accepted attempt 10: it is within the sealed cap, so it
  is a valid result, and rejecting it would bias the sample. Otherwise as predicted.
  **Attempts.** flaky_rerun needs run A to pass and run B to fail:
  - attempts 1, 3, 4, 5, 6, 7: run A failed, discarded (no run B);
  - attempts 2, 8, 9: run A passed, the parent run passed, run B passed, discarded;
  - attempt 10: run A 46083029077 passed, parent run 46083690866 passed, run B 46084408880 failed (triaged).
  **Rate (lab owner's note).** 7 failures in 13 injection runs (6 failing A runs plus the triaged B), which is
  consistent with 50%. At 50% per run, an attempt succeeds (A passes, B fails) about 25% of the time, so needing
  all 10 attempts (no success in the first 9) had a chance of 0.75^9, about 7.5%.
  Every run is a `push` run on `main` with `run_attempt` 1; run numbers 275 to 301 for the case and 302 for the
  revert, no gap. The triaged log fails as the spec notes predict: `smoke` job, steps 1-5 succeeded (the import
  step printed `CLEAN IMPORT OK`), step 6 `Run smoke tests` failed; `docker` skipped.
  `tests/test_behaviour.py::test_chunking_splits_long_text_into_multiple_chunks` FAILED at `assert len(chunks) > 1`
  (tests/test_behaviour.py:63) with `AssertionError: assert 1 > 1`, `where 1 = len([DocumentChunk(...,
  content='A short note.', char_count=13)])`, `1 failed, 12 passed in 5.27s`, then
  `##[error]Process completed with exit code 1.` Smoke job 113 s (23:47:17 to 23:49:10), region eastus. Log
  75,339 bytes / 705 lines.
  **Run A checked.** The smoke log of run 46083029077 (fetched for this check, not saved in the repo) checked out
  `d88d512` and shows `tests/test_behaviour.py::test_chunking_splits_long_text_into_multiple_chunks PASSED` and
  `13 passed in 5.48s` (region eastus), so run A ran the injected test on the triaged sha.
  **Discarded failing runs.** The six failing discarded A runs each checked out their own attempt's sha and fail
  the same way as the triaged run (`AssertionError: assert 1 > 1` on `content='A short note.'`, 1 failed /
  12 passed, only the expected test). Their logs are saved as `logs/extra-inj-068-discarded<k>-run<run_id>.log`.
  `<k>` numbers discarded runs, not attempts: attempts 2, 8 and 9 each discarded two runs (A and B, both
  passing, so no log), so the logs are `discarded1` (attempt 1) and `discarded4` to `discarded8` (attempts 3 to
  7).
  **Spec prediction (standing procedure of 2026-11-24 23:55 UTC).** Holds: the short note sorts first, yields one
  chunk, and `assert 1 > 1` fails; only the expected test failed; run A passed as planned. No spec prediction
  turned out wrong.
  **Decoy-audit marks (plan section 3).** 13 force-back runs on `ef6b0d1`: one is the planned A-to-B parent run
  of attempt 10, the other 12 come from the nine discarded attempts. The audit after slot 30 counts them with the
  run-number gaps.
  `d88d512` replaces line 58 of `tests/test_behaviour.py` (`chunks = _service().chunk(uuid4(), text)`) with the
  spec's five lines (now 58-62) and changes nothing else (`uuid4` was already imported at line 7): UTF-8 without
  BOM, stored with LF (`i/lf`; `w/crlf` is `core.autocrlf` at checkout), 2,997 to 3,186 bytes. The revert
  restores the parent tree exactly (`900079c`). `main` and `origin/main` are both at `d9025b3`; no `wip-` branch
  is left locally or on origin.

## 2026-11-28 00:03 UTC: slot 15/30, inj-080 [environment], decoy, attempt 1 of this slot
- Command: `.\.venv\Scripts\python.exe -u injections\run_injection.py injections\specs\inj-080.json` (default
  `--wait-for-green 15`), run by Claude Code in the background with all output written to
  `C:\Users\Pitbull\Documents\holdout-console\inj-080.txt` (outside the repo). The exit code was captured by the
  wrapping shell and is not in that file. Harness commit `9e904dd` (harness code unchanged since `bd588d8`).
- Commits: injection `32bb060` (parent `9e904dd`), committed on `wip-3a124c` and pushed to `main`;
  discarded/recreated `6f3b348` (decoy attempt), recreated as `32bb060` with the same tree (`e466d1e`), parent,
  message and author date, and a new committer date (00:03:51 to 00:12:10); revert `73df41e`
- Runs: triaged 46086622087 F; decoy 46085869716 F; discarded none; parent (force-back) 46086093943 P (13 passed);
  verification none. Revert run 46086836936 P (smoke and docker both success, docker done 00:21:22).
- Result: match=Y, outcomes=F, extras=decoy:F,parent:P, attempts=2 (the decoy attempt plus the triaged commit;
  this slot was run once). Console summary `inj-080 environment mode=decoy outcomes=F extras=decoy:F,parent:P
  attempts=2 match=Y`, exit 0.
- Unexpected: nothing. Both logs fail as the spec notes predict: `smoke` job, steps 1-5 succeeded (install
  passed and the import step printed `CLEAN IMPORT OK`), step 6 `Run smoke tests` failed; `docker` skipped.
  pytest starts normally (`rootdir: /home/runner/work/ci-triage-lab/ci-triage-lab`, `configfile: pytest.ini`),
  then `collecting ... collected 0 items`, `no tests ran in 0.00s` and `##[error]Process completed with exit code
  5.` There is no pytest count summary, so `failed`/`passed` are null in the record (for the decoy run too). Every
  run is a `push` run on `main` with `run_attempt` 1; run numbers 304 (decoy), 305 (P), 306 (triaged), 307
  (revert), no gap.
  Lines compared by hand (decoy vs triaged; `configfile`, `collect`, `no tests ran` and `##[error]` lines,
  timestamps stripped): identical. With timestamps stripped, the two logs (72,054 bytes / 675 lines decoy, 72,133
  bytes / 676 lines triaged) differ in worker id, region (decoy westcentralus, triaged westus3), temp paths, the
  checked-out sha and the cache-restore progress lines. Both logs check out their own sha. Smoke job 120 s decoy
  (00:03:57 to 00:05:57), 119 s triaged (00:12:17 to 00:14:16); the pytest step about 0.9 s in both.
  **Spec prediction (standing procedure of 2026-11-24 23:55 UTC).** Holds: install and import pass; pytest
  accepts its configuration and collects nothing: `collected 0 items`, `no tests ran`, exit code 5. No spec
  prediction turned out wrong.
  `32bb060` adds one line, `python_files = *_test.py`, at the end of `pytest.ini` (line 4, after
  `testpaths = tests`) and changes nothing else: ASCII, stored with LF (`i/lf`; `w/crlf` is `core.autocrlf` at
  checkout), 42 to 67 bytes. The revert restores the parent tree exactly (`d3d81d9`). `main` and `origin/main` are
  both at `73df41e`; no `wip-` branch is left locally or on origin.
