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

Per-run outcomes are derived from each run's job conclusions with the same
rule as ``run_injection.compare()`` at job level: F = only the expected job
failed and the expected jobs were skipped, P = the run and every job
succeeded, X = anything else, ? = the record has no job conclusions.
Failing tests are re-parsed from the saved logs with
``run_injection.parse_pytest()``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from run_injection import FAILED_CONCLUSIONS, FLAKY, parse_pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INJECTIONS_DIR = REPO_ROOT / "injections"
SPECS_DIR = INJECTIONS_DIR / "specs"
LOGS_DIR = INJECTIONS_DIR / "logs"
MANIFEST_PATH = INJECTIONS_DIR / "manifest.json"
INDEX_JSON_PATH = INJECTIONS_DIR / "index.json"
INDEX_MD_PATH = INJECTIONS_DIR / "INDEX.md"

EXPECTED_IDS = [f"inj-{n:03d}" for n in range(1, 61)]
CASES_PER_CATEGORY = 10

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

    ids = sorted(records)
    if ids != EXPECTED_IDS:
        missing = sorted(set(EXPECTED_IDS) - set(ids))
        extra = sorted(set(ids) - set(EXPECTED_IDS))
        errors.append(f"record ids are not exactly inj-001..inj-060: missing {missing}, unexpected {extra}")
    for spec_id in sorted(set(specs) - set(records)):
        errors.append(f"spec {spec_id} has no record")

    counts = {category: 0 for category in categories}
    for record in records.values():
        if record["category"] not in counts:
            errors.append(f"{record['id']}: category {record['category']!r} is not in manifest.json")
        else:
            counts[record["category"]] += 1
    for category, count in counts.items():
        if count != CASES_PER_CATEGORY:
            errors.append(f"category {category} has {count} cases, expected {CASES_PER_CATEGORY}")

    referenced: set[str] = set()
    for entry in entries:
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

    for path in sorted(LOGS_DIR.glob("*.log")):
        log_file = rel(path)
        if log_file not in referenced and log_file not in UNLABELLED_EXTRAS:
            warnings.append(f"log file {log_file} is not referenced by any record")
    for log_file in UNLABELLED_EXTRAS:
        if not (REPO_ROOT / log_file).is_file():
            errors.append(f"unlabelled extra {log_file} is missing")

    return errors, warnings


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
    entries.sort(key=lambda e: (category_rank.get(e["category"], len(category_rank)), e["id"]))

    index = {
        "generated_by": "injections/build_index.py",
        "cases": entries,
        "unlabelled_extras": [{"log_file": log_file, **extra} for log_file, extra in UNLABELLED_EXTRAS.items()],
    }
    manifest["injections"] = [
        {
            "id": case,
            "category": records[case]["category"],
            "record": f"injections/{case}.json",
            "spec": spec_paths.get(case),
            "logs": next(e["log_files"] for e in entries if e["id"] == case),
        }
        for case in sorted(records)
    ]
    INDEX_JSON_PATH.write_text(dump_json(index), encoding="utf-8")
    INDEX_MD_PATH.write_text(render_markdown(entries, categories), encoding="utf-8")
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
