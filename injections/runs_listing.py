"""``injections/api/runs-main.json``: its v1.0 part, and the v1.1 runs appended to it.

Decision of 2026-11-21 (``injections/holdout_log.md``): the sealed replay reads only this file, so once all 30
v1.1 records exist the v1.1 runs are appended to it as one extra page, and every v1.0 byte stays as it was.

- The v1.0 part is the file as snapshotted on 2026-10-19 (committed in 2b362bc): ``endpoint``, ``params`` and
  its first two pages. Re-serialised the way ``snapshot_api.save`` writes it, it must hash to
  ``V10_SHA256``; ``v10_problems`` checks that.
- The appended page holds only runs whose ids are not in the v1.0 pages. The replay builds its run table with
  later pages overriding earlier ones, so a re-fetched copy of a v1.0 run would change v1.0 inputs; those
  copies are counted in ``v1_1_append`` and not stored.
- ``v1_1_append`` (after ``pages``) records when and how the append was made.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

V10_PAGES = 2
V10_RUNS = 193
V10_SHA256 = "ac8549e5295794297872d68764ab3eb4fe5081d6eb63c55d6bce8ddfd6c40305"
APPEND_KEY = "v1_1_append"


def dumps(data: Any) -> str:
    """Serialise exactly as ``snapshot_api.save`` does."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def v10_bytes(listing: dict[str, Any]) -> bytes:
    """The v1.0 file, rebuilt from the v1.0 part of ``listing``."""
    part = {"endpoint": listing["endpoint"], "params": listing["params"], "pages": listing["pages"][:V10_PAGES]}
    return dumps(part).encode("utf-8")


def v10_ids(listing: dict[str, Any]) -> set[int]:
    return {run["id"] for page in listing["pages"][:V10_PAGES] for run in page["workflow_runs"]}


def v10_problems(listing: dict[str, Any]) -> list[str]:
    """Empty if the v1.0 part is byte-identical and nothing appended shadows a v1.0 run."""
    problems = []
    if hashlib.sha256(v10_bytes(listing)).hexdigest() != V10_SHA256:
        problems.append("the v1.0 part of injections/api/runs-main.json changed (endpoint, params or its first "
                        f"{V10_PAGES} pages no longer hash to {V10_SHA256[:12]}...)")
    extra_pages = listing["pages"][V10_PAGES:]
    if extra_pages and APPEND_KEY not in listing:
        problems.append(f"runs-main.json has pages beyond the v1.0 ones but no {APPEND_KEY!r} section")
    if APPEND_KEY in listing and len(extra_pages) != 1:
        problems.append(f"runs-main.json should have exactly one appended page, has {len(extra_pages)}")
    old = v10_ids(listing)
    shadowing = sorted(run["id"] for page in extra_pages for run in page["workflow_runs"] if run["id"] in old)
    if shadowing:
        problems.append(f"appended pages repeat v1.0 runs {shadowing[:5]}..., which would override v1.0 replay data")
    return problems


def is_appended(listing: dict[str, Any]) -> bool:
    return APPEND_KEY in listing


def append_v11(listing: dict[str, Any], fetched_pages: list[dict[str, Any]], fetched_at: str) -> dict[str, Any]:
    """``listing`` plus one page with every fetched run that is not already in the v1.0 pages."""
    if is_appended(listing):
        raise ValueError("the v1.1 runs are already appended")
    problems = v10_problems(listing)
    if problems:
        raise ValueError("; ".join(problems))
    old = v10_ids(listing)
    fetched = [run for page in fetched_pages for run in page["workflow_runs"]]
    new = [run for run in fetched if run["id"] not in old]
    result = {
        "endpoint": listing["endpoint"],
        "params": listing["params"],
        "pages": listing["pages"][:V10_PAGES] + [{"total_count": len(new), "workflow_runs": new}],
        APPEND_KEY: {
            "fetched_at": fetched_at,
            "params": listing["params"],
            "pages_fetched": len(fetched_pages),
            "total_count_at_fetch": fetched_pages[-1]["total_count"] if fetched_pages else 0,
            "runs_fetched": len(fetched),
            "runs_appended": len(new),
            "v1_0_runs_refetched_not_stored": len(fetched) - len(new),
            "decision": "injections/holdout_log.md, 2026-11-21",
        },
    }
    if v10_problems(result) or v10_bytes(result) != v10_bytes(listing):
        raise ValueError("appending would change the v1.0 part; nothing written")
    return result
