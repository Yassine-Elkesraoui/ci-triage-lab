"""Snapshot the raw GitHub Actions API data behind the records, READ-ONLY.

GitHub deletes workflow runs, checks and statuses once the repository's
retention period has passed, so run and job ids in the records will stop
resolving. This script saves the API responses while they still exist:

- ``injections/api/<run_id>/run.json``: ``GET /actions/runs/{run_id}``
- ``injections/api/<run_id>/jobs.json``: ``GET /actions/runs/{run_id}/jobs?filter=all&per_page=100``
  (every attempt's jobs, with their steps)
- ``injections/api/runs-main.json``: every page of
  ``GET /actions/runs?branch=main&per_page=100`` (run metadata only, no jobs)

Runs covered: every run id referenced by a record (top-level ``run_id`` and
every entry of ``observed.runs``, including flaky reruns) plus the unlabelled
extra inj-039 attempt, run 37651788145.

Each response body is saved exactly as returned (same keys, order and values),
pretty-printed with two-space indentation. ``runs-main.json`` wraps the raw
pages in ``{"endpoint", "params", "pages": [...]}`` so that nothing is merged
or dropped. A jobs response that does not fit in one page aborts rather than
being merged.

Existing snapshot files are never overwritten, since a later fetch could have
lost data that has since expired; delete a file by hand to re-fetch it. Runs
that no longer exist (HTTP 404) are skipped and listed. Only GET requests
are made, with the fine-grained read-only token in ``GITHUB_TOKEN``.

First run on 2026-10-19.

Usage (from the repository root)::

    python injections/maintenance/snapshot_api.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

INJECTIONS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = INJECTIONS_DIR.parent
API_DIR = INJECTIONS_DIR / "api"
sys.path.insert(0, str(INJECTIONS_DIR))
from run_injection import repo_slug  # noqa: E402

EXTRA_RUN_IDS = ["37651788145"]  # first inj-039 attempt, see build_index.UNLABELLED_EXTRAS
PER_PAGE = 100


class NotFound(Exception):
    """The requested resource no longer exists."""


class GitHubReadOnly:
    """GET-only GitHub client returning parsed JSON bodies."""

    def __init__(self, token: str, slug: str) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        self._base = f"https://api.github.com/repos/{slug}"

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self._session.get(f"{self._base}{path}", params=params, timeout=60)
        if response.status_code == 404:
            raise NotFound(path)
        if response.status_code != 200:
            raise SystemExit(f"GitHub API {path} returned HTTP {response.status_code}; nothing more fetched")
        return response.json()


def referenced_run_ids() -> list[str]:
    """Every run id in every record, plus the extras, sorted numerically."""
    ids = set(EXTRA_RUN_IDS)
    for path in sorted(INJECTIONS_DIR.glob("inj-*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        ids.add(record["run_id"])
        ids.update(run["run_id"] for run in record["observed"].get("runs") or [])
    return sorted(ids, key=int)


def save(path: Path, data: Any) -> None:
    """Write ``data`` pretty-printed; never overwrite."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN environment variable is not set")
    gh = GitHubReadOnly(token, repo_slug())

    run_ids = referenced_run_ids()
    saved, present, missing = [], [], []
    for run_id in run_ids:
        run_path = API_DIR / run_id / "run.json"
        jobs_path = API_DIR / run_id / "jobs.json"
        if run_path.exists() and jobs_path.exists():
            present.append(run_id)
            continue
        try:
            run = gh.get(f"/actions/runs/{run_id}")
            jobs = gh.get(f"/actions/runs/{run_id}/jobs", params={"filter": "all", "per_page": PER_PAGE})
        except NotFound:
            missing.append(run_id)
            continue
        if jobs["total_count"] > len(jobs["jobs"]):
            raise SystemExit(f"run {run_id}: {jobs['total_count']} jobs do not fit in one page; nothing saved for it")
        if not run_path.exists():
            save(run_path, run)
        if not jobs_path.exists():
            save(jobs_path, jobs)
        saved.append(run_id)

    list_path = API_DIR / "runs-main.json"
    listed = None
    if list_path.exists():
        print(f"{list_path.relative_to(REPO_ROOT).as_posix()} already exists; not refetched")
    else:
        params = {"branch": "main", "per_page": PER_PAGE}
        pages = []
        while True:
            page = gh.get("/actions/runs", params={**params, "page": len(pages) + 1})
            pages.append(page)
            listed = sum(len(p["workflow_runs"]) for p in pages)
            if not page["workflow_runs"] or listed >= page["total_count"]:
                break
        save(list_path, {"endpoint": "GET /repos/{owner}/{repo}/actions/runs", "params": params, "pages": pages})
        print(f"runs-main.json: {listed} runs in {len(pages)} page(s), total_count {pages[-1]['total_count']}")

    print(f"referenced runs: {len(run_ids)} (records + {len(EXTRA_RUN_IDS)} extra)")
    print(f"snapshotted now: {len(saved)}; already present: {len(present)}")
    print(f"missing (HTTP 404, skipped): {missing or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
