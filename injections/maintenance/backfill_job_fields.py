"""Backfill job fields that the early injection records were written without.

Provenance of the fields this script added (all values fetched READ-ONLY from
the GitHub API; no existing value was changed):

- 2026-10-18: ``observed.failing_step_inferred``, ``observed.job_conclusion``,
  ``observed.job_started_at`` and ``observed.job_completed_at`` added to the
  top-level ``observed`` of inj-001 .. inj-038, which were written before
  ``run_injection.observe()`` recorded them. Source: ``GET /actions/jobs/{job_id}``
  for the record's ``job_id``; ``failing_step_inferred`` uses the same rule as
  ``observe()``. This step first ran from two scratch copies of the code below,
  merged here unchanged.
- 2026-10-18: ``observed.jobs`` added to inj-001, which was run by hand before
  the harness existed and had no job conclusions. Source:
  ``GET /actions/runs/{run_id}/jobs``, job names mapped to keys with
  ``run_injection.workflow_jobs()``, in API order as ``observe()`` does.

Entries in ``observed.runs`` were not touched: ``write_record()`` never writes
these fields there, in any record.

Before anything is written, each fetched value is cross-checked against the
record: the job belongs to the recorded run, maps to ``expected_failing_job``,
reproduces the stored ``failing_step`` and ``duration_s``, and is from run
attempt 1. Values are inserted as text at ``observe()``'s key position, so
every existing byte of the record is kept; each file is verified (same data
plus only the new keys, no line removed) before it is written.

Usage (from the repository root, with ``GITHUB_TOKEN`` set)::

    python injections/maintenance/backfill_job_fields.py           # fetch and report only
    python injections/maintenance/backfill_job_fields.py --write   # also write missing fields

Fields already present are never overwritten; they are re-checked against the
API and any difference is reported. Re-running after a write is a no-op.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import requests

INJECTIONS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = INJECTIONS_DIR.parent
sys.path.insert(0, str(INJECTIONS_DIR))
from run_injection import FAILED_CONCLUSIONS, parse_timestamp, repo_slug, workflow_jobs  # noqa: E402

JOB_FIELDS = ("failing_step_inferred", "job_conclusion", "job_started_at", "job_completed_at")
JOB_FIELD_RECORDS = [f"inj-{n:03d}" for n in range(1, 39)]
JOBS_MAP_RECORDS = ["inj-001"]


class GitHubReadOnly:
    """GET-only GitHub client; returns None instead of raising on HTTP errors."""

    def __init__(self, token: str, slug: str) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        self._base = f"https://api.github.com/repos/{slug}"

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        response = self._session.get(f"{self._base}{path}", params=params, timeout=60)
        return response.json() if response.status_code == 200 else None


def failing_step_rule(job: dict[str, Any]) -> tuple[str | None, bool]:
    """Return ``(failing_step, failing_step_inferred)`` exactly as ``run_injection.observe()`` does."""
    step = next((s["name"] for s in job["steps"] if s["conclusion"] in FAILED_CONCLUSIONS), None)
    inferred = False
    if step is None:
        step = next(
            (
                s["name"]
                for s in sorted(job["steps"], key=lambda s: s["number"])
                if s["name"] not in ("Set up job", "Complete job")
                and not s["name"].startswith("Post ")
                and s["conclusion"] != "success"
            ),
            None,
        )
        inferred = step is not None
    return step, inferred


def fetch_job_fields(gh: GitHubReadOnly, record: dict[str, Any], names_to_keys: dict[str, str]) -> tuple[dict, list]:
    """Fetch the four job fields for a record's failing job. Returns ``(values, problems)``."""
    job = gh.get(f"/actions/jobs/{record['job_id']}")
    if job is None:
        return {f: None for f in JOB_FIELDS}, [f"job {record['job_id']} could not be fetched"]
    observed = record["observed"]
    step, inferred = failing_step_rule(job)
    values = {
        "failing_step_inferred": inferred,
        "job_conclusion": job["conclusion"],
        "job_started_at": job["started_at"],
        "job_completed_at": job["completed_at"],
    }
    problems = []
    if str(job["run_id"]) != record["run_id"]:
        problems.append(f"job belongs to run {job['run_id']}, record says {record['run_id']}")
    if names_to_keys.get(job["name"]) != record["expected_failing_job"]:
        problems.append(f"job {job['name']!r} is not {record['expected_failing_job']!r}")
    if step != observed.get("failing_step"):
        problems.append(f"rule gives failing_step {step!r}, record has {observed.get('failing_step')!r}")
    if job["started_at"] and job["completed_at"]:
        duration = round((parse_timestamp(job["completed_at"]) - parse_timestamp(job["started_at"])).total_seconds())
        if duration != observed.get("duration_s"):
            problems.append(f"timestamps give duration {duration}, record has {observed.get('duration_s')}")
    if job.get("run_attempt", 1) != 1:
        problems.append(f"job is from run attempt {job['run_attempt']}")
    return values, problems


def fetch_jobs_map(gh: GitHubReadOnly, record: dict[str, Any], names_to_keys: dict[str, str]) -> tuple[dict, list]:
    """Fetch ``{job_key: conclusion}`` for a record's run. Returns ``(jobs, problems)``."""
    run = gh.get(f"/actions/runs/{record['run_id']}")
    listing = gh.get(f"/actions/runs/{record['run_id']}/jobs", params={"per_page": 100})
    if run is None or listing is None:
        return None, [f"run {record['run_id']} could not be fetched"]
    problems = []
    jobs: dict[str, str] = {}
    for job in listing["jobs"]:
        key = names_to_keys.get(job["name"])
        if key is None:
            problems.append(f"job {job['name']!r} is not declared in ci.yml")
            continue
        jobs[key] = job["conclusion"]
        if str(job["id"]) == record["job_id"] and key != record["expected_failing_job"]:
            problems.append(f"record job_id {record['job_id']} is job {key!r}, expected {record['expected_failing_job']!r}")
    if run.get("run_attempt", 1) != 1:
        problems.append(f"run is on attempt {run['run_attempt']}")
    if record["job_id"] not in {str(job["id"]) for job in listing["jobs"]}:
        problems.append(f"record job_id {record['job_id']} is not in run {record['run_id']}")
    failing = [key for key, conclusion in jobs.items() if conclusion in FAILED_CONCLUSIONS]
    if failing != [record["expected_failing_job"]]:
        problems.append(f"failing jobs {failing}, expected [{record['expected_failing_job']!r}]")
    return jobs, problems


def insert_job_fields(text: str, values: dict[str, Any]) -> str:
    """Insert the four fields right after ``observed.failing_step``, in either record layout."""
    if re.search(r'^  "observed": \{"', text, re.MULTILINE):  # inj-001: observed on one line
        pattern = re.compile(r'("failing_step": "(?:[^"\\]|\\.)*")(, )')
        insert = "".join(f', "{f}": {json.dumps(v)}' for f, v in values.items())
        new_text, count = pattern.subn(lambda m: m.group(1) + insert + m.group(2), text)
    else:  # json.dumps(indent=2) layout: observed's keys sit at 4 spaces
        pattern = re.compile(r'^(    "failing_step": (?:"(?:[^"\\]|\\.)*"|null),)(\r?\n)', re.MULTILINE)

        def add(match: re.Match) -> str:
            eol = match.group(2)
            lines = "".join(f'    "{f}": {json.dumps(v, ensure_ascii=False)},{eol}' for f, v in values.items())
            return match.group(1) + eol + lines

        new_text, count = pattern.subn(add, text)
    if count != 1:
        raise SystemExit(f"failing_step anchor matched {count} times; nothing written")
    return new_text


def insert_jobs_map(text: str, jobs: dict[str, str]) -> str:
    """Append ``"jobs"`` as the last key of a one-line ``observed`` object, as ``observe()`` orders it."""
    pattern = re.compile(r'^(  "observed": \{(?:[^"}]|"(?:[^"\\]|\\.)*")*)(\})', re.MULTILINE)
    new_text, count = pattern.subn(lambda m: m.group(1) + f', "jobs": {json.dumps(jobs)}' + m.group(2), text)
    if count != 1:
        raise SystemExit(f"one-line observed object matched {count} times; nothing written")
    return new_text


def verify(old_text: str, new_text: str, expected_observed: dict[str, Any], max_removed: int) -> None:
    """Check the edit added exactly the intended keys and removed at most ``max_removed`` lines."""
    old, new = json.loads(old_text), json.loads(new_text)
    assert list(new) == list(old) and new == dict(old, observed=expected_observed)
    assert list(new["observed"]) == list(expected_observed)
    removed = sum(line.startswith("- ") for line in difflib.ndiff(old_text.splitlines(True), new_text.splitlines(True)))
    assert removed <= max_removed, f"{removed} lines removed"


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill job fields into early injection records.")
    parser.add_argument("--write", action="store_true", help="write missing fields (default: report only)")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN environment variable is not set")
    gh = GitHubReadOnly(token, repo_slug())
    names_to_keys = workflow_jobs(REPO_ROOT / ".github" / "workflows" / "ci.yml")

    filled, nulls, problems = [], [], []
    pending: dict[Path, str] = {}
    for case in sorted(set(JOB_FIELD_RECORDS) | set(JOBS_MAP_RECORDS)):
        path = INJECTIONS_DIR / f"{case}.json"
        text = path.read_bytes().decode("utf-8")
        record = json.loads(text)
        observed = record["observed"]
        new_text = text

        if case in JOB_FIELD_RECORDS:
            values, found = fetch_job_fields(gh, record, names_to_keys)
            problems += [f"{case}: {p}" for p in found]
            stored = {f: observed[f] for f in JOB_FIELDS if f in observed}
            problems += [f"{case}: stored {f}={v!r}, API gives {values[f]!r}" for f, v in stored.items() if values[f] != v]
            if not stored:
                expected = {}
                for key, value in observed.items():
                    expected[key] = value
                    if key == "failing_step":
                        expected.update(values)
                candidate = insert_job_fields(new_text, values)
                verify(new_text, candidate, expected, max_removed=1 if case == "inj-001" else 0)
                new_text, observed = candidate, expected
                filled += [f"{case}.observed.{f}" for f in JOB_FIELDS]
                nulls += [f"{case}.observed.{f}" for f, v in values.items() if v is None]
            elif len(stored) != len(JOB_FIELDS):
                problems.append(f"{case}: only some job fields present {sorted(stored)}; not touched")

        if case in JOBS_MAP_RECORDS:
            jobs, found = fetch_jobs_map(gh, record, names_to_keys)
            problems += [f"{case}: {p}" for p in found]
            if "jobs" in observed:
                if jobs is not None and observed["jobs"] != jobs:
                    problems.append(f"{case}: stored jobs {observed['jobs']}, API gives {jobs}")
            elif jobs is None:
                nulls.append(f"{case}.observed.jobs (not written)")
            else:
                candidate = insert_jobs_map(new_text, jobs)
                verify(new_text, candidate, {**observed, "jobs": jobs}, max_removed=1)
                new_text = candidate
                filled.append(f"{case}.observed.jobs = {json.dumps(jobs)}")

        if new_text != text:
            pending[path] = new_text

    # All or nothing: a problem in any record blocks every write.
    if args.write and not problems:
        for path, new_text in pending.items():
            path.write_bytes(new_text.encode("utf-8"))

    print(f"{'filled' if args.write and not problems else 'would fill'} {len(filled)} field(s)"
          + ("".join(f"\n  {f}" for f in filled) if len(filled) <= 8 else
             f": {filled[0]} .. {filled[-1]}"))
    print(f"null (unfetchable): {nulls or 'none'}")
    print(f"cross-check problems: {problems or 'none'}")
    if problems and args.write:
        print("nothing written because of the problems above")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
