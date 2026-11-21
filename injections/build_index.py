"""Build the injection index from the records and specs, and audit the lab.

Usage (from the repository root)::

    python injections/build_index.py

Reads every ``injections/inj-*.json`` record, ``injections/specs/*.json`` spec
and ``injections/logs/*.log`` file and regenerates, deterministically (running
it twice gives identical files):

- ``injections/index.json``: one entry per case
- ``injections/INDEX.md``: the same as tables grouped by category
- ``injections/manifest.json``: its ``injections`` list; every other field is kept

It then audits the lab, prints every ERROR and WARNING, and exits 1 if there
is any ERROR. The files are written either way, so a failing audit can be
inspected. Nothing is fetched from the network and no record is modified.

v1.1 holdout records (inj-061..inj-090, written by ``holdout_runner.py``) are audited by
their own rules (holdout_plan.md section 5): planned same-commit runs, decoys, discarded,
parent and verification runs (``holdout.extra_runs``), and the per-category counts of extra
baseline runs and run-number gaps. They are appended after the v1.0 entries, so with no v1.1
record present every output is byte-identical to the v1.0 build. Run-number gaps need the
v1.1 main-branch listing ``api/runs-main-v1.1.json`` (written by ``maintenance/snapshot_api.py``
once all 30 cases are recorded); until then they are reported as not available.

Per-run outcomes are derived from each run's job conclusions with the same
rule as ``run_injection.compare()`` at job level: F = only the expected job
failed and the expected jobs were skipped, P = the run and every job
succeeded, X = anything else, ? = the record has no job conclusions.
Failing tests are re-parsed from the saved logs with
``run_injection.parse_pytest()``.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from holdout_runner import DECOY_CASES
from run_injection import FAILED_CONCLUSIONS, FLAKY, parse_pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INJECTIONS_DIR = REPO_ROOT / "injections"
SPECS_DIR = INJECTIONS_DIR / "specs"
LOGS_DIR = INJECTIONS_DIR / "logs"
API_DIR = INJECTIONS_DIR / "api"
MANIFEST_PATH = INJECTIONS_DIR / "manifest.json"
INDEX_JSON_PATH = INJECTIONS_DIR / "index.json"
INDEX_MD_PATH = INJECTIONS_DIR / "INDEX.md"

V10_IDS = [f"inj-{n:03d}" for n in range(1, 61)]
V11_IDS = [f"inj-{n:03d}" for n in range(61, 91)]
EXPECTED_IDS = V10_IDS + V11_IDS
CASES_PER_CATEGORY = 10
V11_CASES_PER_CATEGORY = 5
V11_LISTING_PATH = API_DIR / "runs-main-v1.1.json"
EXTRA_LOG_NAME = re.compile(r"extra-(inj-\d{3})-(decoy|discarded|parent|verification)(\d+)-run(\d+)\.log")

# Logs kept on purpose that belong to no labelled case.
UNLABELLED_EXTRAS = {
    "injections/logs/extra-inj-039-attempt1-run37651788145.log": {
        "related_case": "inj-039",
        "run_id": "37651788145",
        "note": "Log of an earlier inj-039 attempt (run 37651788145, attempt 1), not of the labelled "
                "run in inj-039.json. Unlabelled: not one of the 60 cases.",
    },
}


def rel(path: Path) -> str:
    """Return ``path`` relative to the repo root with forward slashes."""
    return path.relative_to(REPO_ROOT).as_posix()


def load_json(path: Path) -> Any:
    """Read a UTF-8 JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(data: Any) -> str:
    """Serialise exactly as ``run_injection.write_record`` does."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def record_runs(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Return ``observed.runs``, or one run built from the top level for records that predate it."""
    observed = record["observed"]
    if observed.get("runs"):
        return observed["runs"]
    return [{
        "run_id": record["run_id"],
        "commit": record["commit"],
        "conclusion": None,
        "jobs": observed.get("jobs"),
        "log_file": record["log_file"],
    }]


def failing_jobs(jobs: dict[str, str] | None) -> list[str] | None:
    """Job keys whose conclusion counts as failed, or None if not recorded."""
    if not jobs:
        return None
    return [key for key, conclusion in jobs.items() if conclusion in FAILED_CONCLUSIONS]


def run_outcome(run: dict[str, Any], record: dict[str, Any]) -> str:
    """One-letter outcome of a run, as defined in the module docstring."""
    jobs = run.get("jobs")
    if not jobs:
        return "?"
    if run["conclusion"] == "success" and all(c == "success" for c in jobs.values()):
        return "P"
    expected_job = record["expected_failing_job"]
    expected_skipped = record["expected_skipped_jobs"]
    skipped = sorted(key for key, c in jobs.items() if c == "skipped")
    others_ok = all(c == "success" for key, c in jobs.items()
                    if key != expected_job and key not in expected_skipped)
    if failing_jobs(jobs) == [expected_job] and skipped == sorted(expected_skipped) and others_ok:
        return "F"
    return "X"


def log_failing_tests(log_file: str | None) -> list[str] | None:
    """Failing test ids parsed from a saved log, or None if it has no pytest summary."""
    if log_file is None or not (REPO_ROOT / log_file).is_file():
        return None
    result = parse_pytest((REPO_ROOT / log_file).read_bytes().decode("utf-8", errors="replace"))
    return sorted(result[1]) if result else None


def missing_snapshot(run_id: str) -> list[str]:
    """Files of the API snapshot for ``run_id`` that are missing or empty (see maintenance/snapshot_api.py)."""
    paths = [API_DIR / run_id / "run.json", API_DIR / run_id / "jobs.json"]
    return [rel(path) for path in paths if not path.is_file() or path.stat().st_size == 0]


def mutation_summary(record: dict[str, Any], spec: dict[str, Any] | None) -> str:
    """The spec's one-line description, else the record's mutation squashed onto one line."""
    if spec and spec.get("description"):
        return spec["description"]
    return " ".join(record["mutation"].split())


def build_entry(record: dict[str, Any], spec: dict[str, Any] | None) -> dict[str, Any]:
    """One index entry for a case."""
    observed = record["observed"]
    runs = record_runs(record)
    eval_run = next((run for run in runs if run["run_id"] == record["run_id"]), runs[0])
    log_files = [record["log_file"]] if record["log_file"] else []
    log_files += [run["log_file"] for run in runs if run["log_file"] and run["log_file"] not in log_files]
    observed_failing = failing_jobs(eval_run.get("jobs"))
    return {
        "id": record["id"],
        "category": record["category"],
        "target_file": record["target_file"],
        "mutation_summary": mutation_summary(record, spec),
        "expected_failing_job": record["expected_failing_job"],
        "observed_failing_job": observed_failing[0] if observed_failing and len(observed_failing) == 1 else None,
        "failing_step": observed.get("failing_step"),
        "failing_step_inferred": observed.get("failing_step_inferred"),
        "job_conclusion": observed.get("job_conclusion"),
        "runs": len(runs),
        "outcomes": "".join(run_outcome(run, record) for run in runs),
        "expected_failing_tests": sorted(record["expected_failing_tests"]),
        "observed_failing_tests": log_failing_tests(record["log_file"]),
        "eval_run_id": eval_run["run_id"],
        "eval_commit": eval_run["commit"],
        "eval_commit_is_injection": eval_run["commit"] == record["commit"],
        "injection_commit": record["commit"],
        "log_files": log_files,
        "duration_s": observed.get("duration_s"),
    } | (holdout_entry_fields(record) if record["id"] in V11_IDS else {})


def holdout_entry_fields(record: dict[str, Any]) -> dict[str, Any]:
    """v1.1 only: family, rerun marker, decoy flag and the extra runs, appended to the entry."""
    holdout = record.get("holdout") or {}
    return {
        "family": holdout.get("family"),
        "same_commit_rerun": holdout.get("same_commit_rerun"),
        "decoy": holdout.get("decoy"),
        "mode": holdout.get("mode"),
        "attempts": holdout.get("attempts"),
        "extra_runs": [
            {key: run.get(key) for key in ("role", "attempt", "run_id", "commit", "outcome", "log_file")}
            for run in holdout.get("extra_runs", [])
        ],
    }


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #


def audit(
    records: dict[str, dict[str, Any]],
    specs: dict[str, dict[str, Any]],
    entries: list[dict[str, Any]],
    categories: list[str],
) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)`` for the whole lab."""
    errors: list[str] = []
    warnings: list[str] = []

    ids = sorted(case for case in records if case not in V11_IDS)
    if ids != V10_IDS:
        missing = sorted(set(V10_IDS) - set(ids))
        extra = sorted(set(ids) - set(V10_IDS))
        errors.append(f"record ids are not exactly inj-001..inj-060: missing {missing}, unexpected {extra}")
    for spec_id in sorted(set(specs) - set(records) - set(V11_IDS)):
        errors.append(f"spec {spec_id} has no record")

    counts = {category: 0 for category in categories}
    for record in records.values():
        if record["id"] in V11_IDS:
            continue
        if record["category"] not in counts:
            errors.append(f"{record['id']}: category {record['category']!r} is not in manifest.json")
        else:
            counts[record["category"]] += 1
    for category, count in counts.items():
        if count != CASES_PER_CATEGORY:
            errors.append(f"category {category} has {count} cases, expected {CASES_PER_CATEGORY}")

    referenced: set[str] = set()
    for entry in entries:
        if entry["id"] in V11_IDS:
            referenced.update(entry["log_files"])
            continue
        case = entry["id"]
        record = records[case]
        spec = specs.get(case)

        if spec is None:
            warnings.append(f"{case}: no spec in injections/specs/; category not cross-checked")
        elif spec["category"] != record["category"]:
            errors.append(f"{case}: record category {record['category']!r} differs from spec {spec['category']!r}")

        for log_file in entry["log_files"]:
            referenced.add(log_file)
            path = REPO_ROOT / log_file
            if not path.is_file():
                errors.append(f"{case}: log file {log_file} is missing")
            elif path.stat().st_size == 0:
                errors.append(f"{case}: log file {log_file} is empty")

        for run in record_runs(record):
            missing = missing_snapshot(run["run_id"])
            if missing:
                errors.append(f"{case}: run {run['run_id']} has no API snapshot (missing or empty: {', '.join(missing)})")

        for key in ("failing_step", "job_conclusion"):
            if entry[key] is None:
                errors.append(f"{case}: {key} is null")

        expected_job = entry["expected_failing_job"]
        observed_jobs = failing_jobs(next(run for run in record_runs(record)
                                          if run["run_id"] == entry["eval_run_id"]).get("jobs"))
        if observed_jobs is None:
            errors.append(f"{case}: observed failing job not recorded (record has no observed.jobs); "
                          f"expected {expected_job!r}")
        elif observed_jobs != [expected_job]:
            errors.append(f"{case}: observed failing jobs {observed_jobs} differ from expected [{expected_job!r}]")

        outcomes = entry["outcomes"]
        if record["category"] == FLAKY:
            if "F" not in outcomes or "P" not in outcomes:
                errors.append(f"{case}: flaky case needs at least one F and one P, outcomes {outcomes!r}")
        elif entry["runs"] != 1 or outcomes != "F":
            errors.append(f"{case}: non-flaky case needs runs=1 and outcome 'F', "
                          f"got runs={entry['runs']} outcomes {outcomes!r}")

        expected_tests = entry["expected_failing_tests"]
        if expected_tests:
            for index, run in enumerate(record_runs(record), start=1):
                if run_outcome(run, record) != "F" and run["run_id"] != entry["eval_run_id"]:
                    continue
                observed_tests = log_failing_tests(run["log_file"])
                if observed_tests != expected_tests:
                    errors.append(f"{case}: run {index} ({run['run_id']}) failing tests {observed_tests} "
                                  f"differ from expected {expected_tests}")

    holdout_errors, holdout_warnings, holdout_logs = audit_holdout(records, specs, entries, categories)
    errors += holdout_errors
    warnings += holdout_warnings
    referenced |= holdout_logs

    for path in sorted(LOGS_DIR.glob("*.log")):
        log_file = rel(path)
        if log_file not in referenced and log_file not in UNLABELLED_EXTRAS:
            warnings.append(f"log file {log_file} is not referenced by any record")
    for log_file, extra in UNLABELLED_EXTRAS.items():
        if not (REPO_ROOT / log_file).is_file():
            errors.append(f"unlabelled extra {log_file} is missing")
        missing = missing_snapshot(extra["run_id"])
        if missing:
            errors.append(f"unlabelled extra run {extra['run_id']} has no API snapshot "
                          f"(missing or empty: {', '.join(missing)})")

    return errors, warnings


# --------------------------------------------------------------------------- #
# v1.1 holdout audit (holdout_plan.md sections 3 and 5)
# --------------------------------------------------------------------------- #


def planned_mode(spec: dict[str, Any]) -> str:
    """The mode ``holdout_runner.case_mode`` gives this spec (the tests keep the two in step)."""
    rerun = spec.get("same_commit_rerun")
    if spec["category"] == FLAKY:
        return "flaky_rerun" if rerun == "pass" else "flaky"
    if rerun == "fail_same":
        return "fail_same"
    return "decoy" if spec["id"] in DECOY_CASES else "single"


def snapshot_run(run_id: str) -> dict[str, Any] | None:
    path = API_DIR / str(run_id) / "run.json"
    return load_json(path) if path.is_file() and path.stat().st_size else None


def failed_job_count(run_id: str) -> int | None:
    """Jobs of a snapshotted run that neither succeeded nor were skipped (the replay maps a saved log to it)."""
    path = API_DIR / str(run_id) / "jobs.json"
    if not path.is_file() or not path.stat().st_size:
        return None
    return sum(1 for job in load_json(path)["jobs"] if job["conclusion"] not in ("success", "skipped", None))


def v11_listing() -> list[dict[str, Any]] | None:
    """Runs on main from ``api/runs-main-v1.1.json``, or None if it has not been fetched."""
    if not V11_LISTING_PATH.is_file():
        return None
    return [run for page in load_json(V11_LISTING_PATH)["pages"] for run in page["workflow_runs"]]


def holdout_marks(record: dict[str, Any], listing: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Section 3's audit marks for one case: extra baseline runs, run-number gaps and earlier same-commit runs.

    Extra baseline runs: runs on the parent created before the eval run, minus the first (the previous case's
    revert run). Run-number gaps: runs numbered between that first parent run and the eval run that the replay
    does not show (neither the baseline, the latest green parent run, nor a run of the eval commit).
    Without the listing, extra baseline runs come from the record's parent runs and gaps are None.
    """
    holdout = record["holdout"]
    parent, eval_sha = holdout["parent_commit"], holdout["injection_commit_full"]
    eval_run = snapshot_run(record["run_id"])
    marks: dict[str, Any] = {"source": "listing" if listing is not None else "record"}
    if eval_run is None:
        return marks | {"extra_baseline_runs": None, "run_number_gaps": None, "earlier_same_commit_runs": None}
    eval_created = eval_run["created_at"]
    if listing is None:
        parents = [run for run in holdout["extra_runs"] if run["role"] == "parent" and run["created_at"] < eval_created]
        earlier = [run for run in record["observed"]["runs"] if run["run_id"] != record["run_id"]]
        return marks | {"extra_baseline_runs": len(parents), "run_number_gaps": None,
                        "earlier_same_commit_runs": len(earlier)}
    on_parent = sorted((run for run in listing if run["head_sha"] == parent and run["created_at"] < eval_created),
                       key=lambda run: run["run_number"])
    earlier = [run for run in listing
               if run["head_sha"] == eval_sha and run["created_at"] < eval_created and str(run["id"]) != record["run_id"]]
    if not on_parent:
        return marks | {"extra_baseline_runs": None, "run_number_gaps": None, "earlier_same_commit_runs": len(earlier)}
    green = [run for run in on_parent if run["conclusion"] == "success"]
    baseline = max(green, key=lambda run: (run["created_at"], run["id"]))["id"] if green else None
    first, last = on_parent[0]["run_number"], eval_run["run_number"]
    gaps = sum(1 for run in listing
               if first < run["run_number"] < last and run["id"] != baseline and run["head_sha"] != eval_sha)
    return marks | {"extra_baseline_runs": len(on_parent) - 1, "run_number_gaps": gaps,
                    "earlier_same_commit_runs": len(earlier)}


def audit_holdout(
    records: dict[str, dict[str, Any]],
    specs: dict[str, dict[str, Any]],
    entries: list[dict[str, Any]],
    categories: list[str],
) -> tuple[list[str], list[str], set[str]]:
    """``(errors, warnings, log files referenced)`` for the v1.1 cases."""
    errors: list[str] = []
    warnings: list[str] = []
    referenced: set[str] = set()
    recorded = [case for case in V11_IDS if case in records]
    pending = [case for case in V11_IDS if case in specs and case not in records]
    if pending:
        warnings.append(f"v1.1 holdout: {len(recorded)}/30 recorded, {len(pending)} pending "
                        f"(specs without a record yet: {pending[0]}..{pending[-1]})")
    for case in sorted(set(V11_IDS) - set(specs)):
        errors.append(f"v1.1 spec {case} is missing")
    if len(recorded) == len(V11_IDS):
        for category in categories:
            count = sum(1 for case in recorded if records[case]["category"] == category)
            if count != V11_CASES_PER_CATEGORY:
                errors.append(f"v1.1 category {category} has {count} cases, expected {V11_CASES_PER_CATEGORY}")
    listing = v11_listing()
    if recorded and listing is None:
        warnings.append(f"v1.1: {rel(V11_LISTING_PATH)} is not fetched yet, so run-number gaps and the "
                        "listing cross-checks are not available")

    by_id = {entry["id"]: entry for entry in entries}
    for case in recorded:
        record, spec, entry = records[case], specs.get(case), by_id[case]
        holdout = record.get("holdout")
        if spec is None:
            errors.append(f"{case}: no spec in injections/specs/")
            continue
        if holdout is None:
            errors.append(f"{case}: v1.1 record has no 'holdout' section")
            continue
        if spec["category"] != record["category"]:
            errors.append(f"{case}: record category {record['category']!r} differs from spec {spec['category']!r}")
        mode = planned_mode(spec)
        if holdout.get("mode") != mode:
            errors.append(f"{case}: recorded mode {holdout.get('mode')!r}, planned {mode!r}")
        for key in ("family", "branch", "commit_message", "same_commit_rerun"):
            if holdout.get(key) != spec.get(key):
                errors.append(f"{case}: holdout.{key} {holdout.get(key)!r} differs from the spec {spec.get(key)!r}")
        if holdout.get("decoy") != (case in DECOY_CASES):
            errors.append(f"{case}: holdout.decoy is {holdout.get('decoy')!r}; the decoy draw says {case in DECOY_CASES}")
        if not entry["eval_commit_is_injection"]:
            errors.append(f"{case}: the eval commit is not the injection commit")
        if not holdout["injection_commit_full"].startswith(record["commit"]):
            errors.append(f"{case}: holdout.injection_commit_full does not match the record commit")
        for key in ("failing_step", "job_conclusion"):
            if entry[key] is None:
                errors.append(f"{case}: {key} is null")
        if entry["observed_failing_job"] != record["expected_failing_job"]:
            errors.append(f"{case}: observed failing job {entry['observed_failing_job']!r} differs from expected "
                          f"{record['expected_failing_job']!r}")

        runs = record["observed"]["runs"]
        planned = {"single": "F", "decoy": "F", "flaky": "F", "fail_same": "FF", "flaky_rerun": "PF"}[mode]
        if entry["outcomes"] != planned:
            errors.append(f"{case}: mode {mode} needs outcomes {planned!r} in observed.runs, got {entry['outcomes']!r}")
        if runs[-1]["run_id"] != record["run_id"]:
            errors.append(f"{case}: the eval run must be the last run in observed.runs")
        if len(runs) == 2:
            first, second = snapshot_run(runs[0]["run_id"]), snapshot_run(runs[1]["run_id"])
            if runs[0]["commit"] != runs[1]["commit"]:
                errors.append(f"{case}: runs A and B are on different commits")
            if first and second and not first["created_at"] < second["created_at"]:
                errors.append(f"{case}: run A was not created before the eval run")

        extras = holdout.get("extra_runs", [])
        roles = [run["role"] for run in extras]
        if mode == "decoy" and roles.count("decoy") != 1:
            errors.append(f"{case}: a decoy case needs exactly one decoy attempt, has {roles.count('decoy')}")
        if mode != "decoy" and "decoy" in roles:
            errors.append(f"{case}: has a decoy attempt but is not a decoy case")
        if spec["category"] != FLAKY and ("discarded" in roles or "verification" in roles):
            errors.append(f"{case}: discarded and verification runs belong to flaky cases only")
        if spec["category"] == FLAKY and "P" not in entry["outcomes"] and not any(
                run["role"] in ("discarded", "verification") and run["outcome"] == "P" for run in extras):
            errors.append(f"{case}: flaky case has no green run of the same code")

        for run in runs:
            missing = missing_snapshot(run["run_id"])
            if missing:
                errors.append(f"{case}: run {run['run_id']} has no API snapshot (missing or empty: {', '.join(missing)})")
            elif run["log_file"] and failed_job_count(run["run_id"]) != 1:
                errors.append(f"{case}: run {run['run_id']} has a saved log but not exactly one failed job")
        for log_file in entry["log_files"]:
            path = REPO_ROOT / log_file
            if not path.is_file() or path.stat().st_size == 0:
                errors.append(f"{case}: log file {log_file} is missing or empty")
        expected_tests = entry["expected_failing_tests"]
        if expected_tests:
            for index, run in enumerate(runs, start=1):
                observed_tests = log_failing_tests(run["log_file"])
                if run_outcome(run, record) == "F" and observed_tests != expected_tests:
                    errors.append(f"{case}: run {index} ({run['run_id']}) failing tests {observed_tests} "
                                  f"differ from expected {expected_tests}")

        for run in extras:
            label = f"{case}: {run['role']} run {run['run_id']}"
            missing = missing_snapshot(run["run_id"])
            if missing:
                errors.append(f"{label} has no API snapshot (missing or empty: {', '.join(missing)})")
            if run["role"] == "parent" and run["outcome"] != "P":
                errors.append(f"{label} is not green")
            if run["role"] == "decoy" and run["outcome"] != "F":
                errors.append(f"{label} did not fail as the spec expects")
            if run["outcome"] == "X":
                errors.append(f"{label} failed some other way")
            if run["log_file"]:
                referenced.add(run["log_file"])
                match = EXTRA_LOG_NAME.fullmatch(Path(run["log_file"]).name)
                if not match or match[1] != case or match[2] != run["role"] or match[4] != run["run_id"]:
                    errors.append(f"{label}: log name {run['log_file']} does not follow "
                                  "extra-<id>-<role><k>-run<run_id>.log")
                path = REPO_ROOT / run["log_file"]
                if not path.is_file() or path.stat().st_size == 0:
                    errors.append(f"{label}: log file {run['log_file']} is missing or empty")
                elif not missing and failed_job_count(run["run_id"]) != 1:
                    errors.append(f"{label} has a saved log but not exactly one failed job")
                if run["role"] == "decoy" and expected_tests and log_failing_tests(run["log_file"]) != expected_tests:
                    errors.append(f"{label}: failing tests differ from expected {expected_tests}")
            elif run["outcome"] != "P":
                errors.append(f"{label} failed but its log was not saved")

        earlier = holdout_marks(record, listing)["earlier_same_commit_runs"]
        wanted = 1 if mode in ("fail_same", "flaky_rerun") else 0
        if earlier is not None and earlier != wanted:
            errors.append(f"{case}: {earlier} earlier run(s) of the eval commit before the eval run; planned {wanted}")
    return errors, warnings, referenced


def holdout_summary(records: dict[str, dict[str, Any]], categories: list[str]) -> dict[str, Any]:
    """Per-case and per-category audit marks for index.json and INDEX.md (reported, not required to be zero)."""
    listing = v11_listing()
    cases = {}
    for case in V11_IDS:
        record = records.get(case)
        if record is None or "holdout" not in record:
            continue
        holdout = record["holdout"]
        marks = holdout_marks(record, listing)
        cases[case] = {
            "category": record["category"],
            "mode": holdout["mode"],
            "planned_rerun": holdout["same_commit_rerun"],
            "attempts": holdout["attempts"],
            "eval_run_id": record["run_id"],
            "earlier_same_commit_runs": marks["earlier_same_commit_runs"],
            "extra_baseline_runs": marks["extra_baseline_runs"],
            "run_number_gaps": marks["run_number_gaps"],
            "marks_source": marks["source"],
            "extra_runs": [
                {"role": run["role"], "run_id": run["run_id"], "outcome": run["outcome"],
                 "snapshot": not missing_snapshot(run["run_id"]), "log_file": run["log_file"]}
                for run in holdout.get("extra_runs", [])
            ],
        }

    def total(group: list[dict[str, Any]], key: str) -> int | None:
        values = [case[key] for case in group]
        return None if any(value is None for value in values) else sum(values)

    per_category = {}
    for category in categories:
        group = [c for c in cases.values() if c["category"] == category]
        others = [c for c in group if not c["planned_rerun"]]
        reruns = [c for c in group if c["planned_rerun"]]
        per_category[category] = {
            "cases": len(group),
            "extra_baseline_runs": total(others, "extra_baseline_runs"),
            "run_number_gaps": total(others, "run_number_gaps"),
            "planned_rerun_cases": len(reruns),
            "planned_rerun_extra_baseline_runs": total(reruns, "extra_baseline_runs"),
            "planned_rerun_run_number_gaps": total(reruns, "run_number_gaps"),
        }
    return {"listing": rel(V11_LISTING_PATH) if listing is not None else None, "recorded": len(cases),
            "per_category": per_category, "cases": cases}


def render_holdout_markdown(entries: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    """The v1.1 section of INDEX.md, appended after the v1.0 content."""
    lines = [
        f"## v1.1 holdout ({summary['recorded']} of 30 recorded)",
        "",
        "Rerun: *fails same* or *passes* marks the 12 cases with a planned earlier run of the same commit. "
        "Extra baseline runs and run-number gaps are the marks that discarded attempts leave (holdout_plan.md "
        "section 3); they are reported, not required to be zero. In the category table the rerun cases are "
        "counted separately. *n/a*: needs the v1.1 listing.",
        "",
        "| ID | Category | Family | Rerun | Mode | Outcomes | Extra runs | Extra baseline runs | Run-number gaps "
        "| Eval run | Eval commit | Failing step |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def shown(value: Any) -> str:
        return "n/a" if value is None else str(value)

    for e in entries:
        case = summary["cases"].get(e["id"], {})
        rerun = {"fail_same": "fails same", "pass": "passes"}.get(e["same_commit_rerun"], "")
        extras = ", ".join(f"{run['role']} {run['outcome']}" for run in e["extra_runs"]) or "none"
        lines.append(
            f"| {e['id']} | {e['category']} | {md(e['family'])} | {rerun} | {e['mode']} | `{e['outcomes']}` "
            f"| {md(extras)} | {shown(case.get('extra_baseline_runs'))} | {shown(case.get('run_number_gaps'))} "
            f"| {e['eval_run_id']} | `{e['eval_commit']}` | {md(e['failing_step'])} |"
        )
    lines += ["", "| Category | Cases | Extra baseline runs | Run-number gaps | Rerun cases "
              "| Their extra baseline runs | Their gaps |", "|---|---|---|---|---|---|---|"]
    for category, row in summary["per_category"].items():
        lines.append(f"| {category} | {row['cases']} | {shown(row['extra_baseline_runs'])} "
                     f"| {shown(row['run_number_gaps'])} | {row['planned_rerun_cases']} "
                     f"| {shown(row['planned_rerun_extra_baseline_runs'])} "
                     f"| {shown(row['planned_rerun_run_number_gaps'])} |")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #


def md(text: Any) -> str:
    """Make a value safe for a Markdown table cell."""
    return " ".join(str(text).split()).replace("|", "\\|")


def short_tests(tests: list[str] | None) -> str:
    """Test function names only, one per line in the cell."""
    if tests is None:
        return "no pytest summary"
    if not tests:
        return "none"
    return "<br>".join(f"`{md(test.split('::')[-1])}`" for test in tests)


def render_markdown(entries: list[dict[str, Any]], categories: dict[str, str]) -> str:
    """Render INDEX.md."""
    lines = [
        "# Injection index",
        "",
        "Generated by `injections/build_index.py` from the records, specs and logs; do not edit by hand.",
        "",
        f"{len(entries)} labelled cases in {len(categories)} categories. "
        "Outcomes per CI run: **F** failed as labelled, **P** fully green, **X** failed some other way, "
        "**?** not recorded. The eval run is the first F run; *(rerun)* marks an eval commit that is "
        "an empty rerun commit rather than the injection commit. Steps marked *(inferred)* were "
        "cancelled by a job timeout without failing.",
        "",
    ]
    for category, description in categories.items():
        group = [entry for entry in entries if entry["category"] == category]
        lines += [
            f"## {category} ({len(group)})",
            "",
            f"{md(description)}",
            "",
            "| ID | Target file | Mutation | Job | Failing step | Conclusion | Outcomes "
            "| Expected failing tests | Observed failing tests | Eval run | Eval commit | Duration | Logs |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for e in group:
            step = md(e["failing_step"]) + (" *(inferred)*" if e["failing_step_inferred"] else "")
            commit = f"`{e['eval_commit']}`" + ("" if e["eval_commit_is_injection"] else " *(rerun)*")
            logs = "<br>".join(f"`{Path(log).name}`" for log in e["log_files"])
            lines.append(
                f"| {e['id']} | `{md(e['target_file'])}` | {md(e['mutation_summary'])} "
                f"| {md(e['expected_failing_job'])} | {step} | {md(e['job_conclusion'])} "
                f"| `{e['outcomes']}` | {short_tests(e['expected_failing_tests'])} "
                f"| {short_tests(e['observed_failing_tests'])} | {e['eval_run_id']} | {commit} "
                f"| {e['duration_s']} s | {logs} |"
            )
        lines.append("")
    lines += ["## Unlabelled extras", ""]
    for log_file, extra in UNLABELLED_EXTRAS.items():
        lines.append(f"- `{log_file}`: {md(extra['note'])}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    """Build the index files, run the audit, and return the exit code."""
    manifest = load_json(MANIFEST_PATH)
    categories: dict[str, str] = manifest["categories"]
    records = {}
    for path in sorted(INJECTIONS_DIR.glob("inj-*.json")):
        record = load_json(path)
        if record["id"] != path.stem:
            print(f"ERROR: {rel(path)} has id {record['id']!r}", file=sys.stderr)
            return 1
        records[record["id"]] = record
    specs = {}
    spec_paths = {}
    for path in sorted(SPECS_DIR.glob("inj-*.json")):
        spec = load_json(path)
        specs[spec["id"]] = spec
        spec_paths[spec["id"]] = rel(path)

    category_rank = {category: rank for rank, category in enumerate(categories)}
    entries = [build_entry(records[case], specs.get(case)) for case in sorted(records)]
    entries.sort(key=lambda e: (e["id"] in V11_IDS, category_rank.get(e["category"], len(category_rank)), e["id"]))
    v10_entries = [e for e in entries if e["id"] not in V11_IDS]
    v11_entries = [e for e in entries if e["id"] in V11_IDS]

    index = {
        "generated_by": "injections/build_index.py",
        "cases": entries,
        "unlabelled_extras": [{"log_file": log_file, **extra} for log_file, extra in UNLABELLED_EXTRAS.items()],
    }
    # v1.1 additions; absent while no v1.1 record exists, so the v1.0 build stays byte-identical.
    summary = holdout_summary(records, list(categories)) if v11_entries else None
    if summary is not None:
        index["unlabelled_extras"] += [
            {"log_file": run["log_file"], "related_case": case, "run_id": run["run_id"], "role": run["role"],
             "note": f"{run['role']} run of {case} (v1.1), outside the case's replay scope. Unlabelled."}
            for case in V11_IDS if case in records
            for run in records[case].get("holdout", {}).get("extra_runs", []) if run["log_file"]
        ]
        index["holdout_audit"] = summary
    manifest["injections"] = [
        {
            "id": case,
            "category": records[case]["category"],
            "record": f"injections/{case}.json",
            "spec": spec_paths.get(case),
            "logs": next(e["log_files"] for e in entries if e["id"] == case),
        }
        for case in sorted(records, key=lambda case: (case in V11_IDS, case))
    ]
    markdown = render_markdown(v10_entries, categories)
    if summary is not None:
        markdown += "\n" + render_holdout_markdown(v11_entries, summary)
    INDEX_JSON_PATH.write_text(dump_json(index), encoding="utf-8")
    INDEX_MD_PATH.write_text(markdown, encoding="utf-8")
    MANIFEST_PATH.write_text(dump_json(manifest), encoding="utf-8")
    print(f"wrote {rel(INDEX_JSON_PATH)}, {rel(INDEX_MD_PATH)}, {rel(MANIFEST_PATH)} ({len(entries)} cases)")

    errors, warnings = audit(records, specs, entries, list(categories))
    for message in errors:
        print(f"ERROR: {message}")
    for message in warnings:
        print(f"WARNING: {message}")
    print(f"audit: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
