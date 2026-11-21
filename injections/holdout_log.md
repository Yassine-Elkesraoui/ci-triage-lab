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
