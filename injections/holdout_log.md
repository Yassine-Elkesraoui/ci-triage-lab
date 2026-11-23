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
