"""Inject a known bug, let GitHub Actions fail, and record the labelled run.

Usage (PowerShell, from the repository root)::

    $env:GITHUB_TOKEN = "<token with actions:read and contents:write>"
    python injections/run_injection.py injections/specs/inj-002.json --dry-run
    python injections/run_injection.py injections/specs/inj-002.json
    python injections/run_injection.py injections/specs/inj-003.json --runs 6

The script applies a single find/replace mutation described by a spec file,
commits and pushes it, waits for the CI run on that commit, checks that the
observed failure matches the expected label, saves the failing job's log to
``injections/logs/``, writes ``injections/{id}.json`` in the same shape as
``inj-001.json``, and reverts the injection.

With ``--runs N`` the same code is run N times: after the injection commit,
N-1 empty commits re-trigger CI, and the injection is reverted only after the
last run. Non-flaky specs must match on every run; flaky specs must produce a
mix of expected failures and fully green runs.

A wrong label is worse than a missing one: any mismatch between expected and
observed aborts without writing a record. If the abort happens after the
injection was pushed, the exact commands to restore ``main`` are printed.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
INJECTIONS_DIR = REPO_ROOT / "injections"
LOGS_DIR = INJECTIONS_DIR / "logs"
BRANCH = "main"
REMOTE = "origin"
API_ROOT = "https://api.github.com"

POLL_INTERVAL_S = 20
POLL_TIMEOUT_S = 15 * 60
RUN_CREATION_GRACE_S = 60
DEFAULT_WAIT_FOR_GREEN_MIN = 15
RETRY_DELAYS_S = (5, 10, 20, 40, 80, 120, 120, 120)

FLAKY = "flaky"
FAILED_CONCLUSIONS = {"failure", "cancelled", "timed_out"}
REQUIRED_SPEC_KEYS: dict[str, type] = {
    "id": str,
    "category": str,
    "target_file": str,
    "find": str,
    "replace": str,
    "expected_failing_tests": list,
    "expected_failing_job": str,
    "expected_skipped_jobs": list,
    "notes": str,
}
PROTECTED_PREFIXES = ("eval/results/", ".git/")


class Abort(Exception):
    """Raised to stop the run with a message; handled once in ``main``."""


@dataclass
class RecoveryState:
    """Tracks what has reached the remote so an abort can say how to recover."""

    injection_sha: str | None = None
    injection_pushed: bool = False
    revert_committed: bool = False
    revert_pushed: bool = False

    def instructions(self) -> list[str]:
        """Return the git commands needed to restore ``main`` to green.

        Empty re-trigger commits pushed after the injection touch no files,
        so reverting the injection commit alone restores the code.
        """
        if not self.injection_pushed or self.revert_pushed:
            return []
        if self.revert_committed:
            return [f"git push {REMOTE} {BRANCH}"]
        return [
            f"git checkout {BRANCH}",
            f"git pull --ff-only {REMOTE} {BRANCH}",
            f"git revert --no-edit {self.injection_sha}",
            f"git push {REMOTE} {BRANCH}",
        ]


@dataclass
class Spec:
    """A validated injection spec."""

    id: str
    category: str
    target_file: str
    find: str
    replace: str
    expected_failing_tests: list[str]
    expected_failing_job: str
    expected_skipped_jobs: list[str]
    notes: str
    description: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def mutation(self) -> str:
        """Human-readable summary of the mutation, as in ``inj-001.json``."""
        return f"{self.find} -> {self.replace}"

    @property
    def commit_message(self) -> str:
        """Commit subject for the injection commit."""
        return f"inject: {self.id} {self.description or self.mutation}"


@dataclass
class RunResult:
    """What one CI run did, reduced to what matching and the record need."""

    index: int
    sha: str
    run_id: str
    run_url: str
    conclusion: str
    job_id: str | None
    failing_keys: list[str]
    failing_tests: set[str]
    pytest_ran: bool
    observed: dict[str, Any]
    failing_log: bytes | None
    problems: list[str] = field(default_factory=list)

    @property
    def matches(self) -> bool:
        """True if this run failed exactly as the spec expects."""
        return not self.problems

    @property
    def green(self) -> bool:
        """True if the run and every one of its jobs succeeded."""
        jobs = self.observed["jobs"]
        return self.conclusion == "success" and bool(jobs) and all(c == "success" for c in jobs.values())

    @property
    def outcome(self) -> str:
        """One-letter outcome: F expected failure, P green, X anything else."""
        return "F" if self.matches else "P" if self.green else "X"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def log(message: str) -> None:
    """Print a progress line to stderr so stdout stays for the summary."""
    print(f"[run_injection] {message}", file=sys.stderr, flush=True)


def git(*args: str, check: bool = True) -> str:
    """Run a git command in the repo root and return stripped stdout.

    Raises:
        Abort: If ``check`` is set and git exits non-zero.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if check and result.returncode != 0:
        raise Abort(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def is_git_ignored(path: Path) -> bool:
    """Return True if git would ignore ``path`` (which need not exist)."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", "--", path.relative_to(REPO_ROOT).as_posix()],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    if result.returncode not in (0, 1):
        raise Abort(f"git check-ignore failed for {path}")
    return result.returncode == 0


def parse_timestamp(value: str) -> datetime:
    """Parse a GitHub API ISO-8601 timestamp."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def rel(path: Path) -> str:
    """Return ``path`` relative to the repo root with forward slashes."""
    return path.relative_to(REPO_ROOT).as_posix()


def load_spec(path: Path) -> Spec:
    """Load and validate a spec file.

    Raises:
        Abort: If the file is unreadable, a key is missing or mistyped, or
            the target is outside the repo or in a protected location.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Abort(f"cannot read spec {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise Abort(f"spec {path} must be a JSON object")

    for key, expected_type in REQUIRED_SPEC_KEYS.items():
        if key not in data:
            raise Abort(f"spec is missing required key {key!r}")
        if not isinstance(data[key], expected_type):
            raise Abort(f"spec key {key!r} must be {expected_type.__name__}")
    for key in ("expected_failing_tests", "expected_skipped_jobs"):
        if not all(isinstance(item, str) for item in data[key]):
            raise Abort(f"spec key {key!r} must be a list of strings")
    if not re.fullmatch(r"inj-\d{3,}", data["id"]):
        raise Abort(f"spec id {data['id']!r} must look like 'inj-002'")
    if not data["find"]:
        raise Abort("spec 'find' must not be empty")
    if data["find"] == data["replace"]:
        raise Abort("spec 'find' and 'replace' are identical")

    manifest = json.loads((INJECTIONS_DIR / "manifest.json").read_text(encoding="utf-8"))
    if data["category"] not in manifest["categories"]:
        raise Abort(
            f"category {data['category']!r} is not one of {sorted(manifest['categories'])}"
        )

    target = (REPO_ROOT / data["target_file"]).resolve()
    if not target.is_relative_to(REPO_ROOT):
        raise Abort(f"target_file {data['target_file']!r} is outside the repository")
    relative = target.relative_to(REPO_ROOT).as_posix()
    if relative.startswith(PROTECTED_PREFIXES):
        raise Abort(f"target_file {relative!r} is in a protected location")

    description = data.get("description", "")
    if not isinstance(description, str):
        raise Abort("spec key 'description' must be str")

    return Spec(
        id=data["id"],
        category=data["category"],
        target_file=relative,
        find=data["find"],
        replace=data["replace"],
        expected_failing_tests=data["expected_failing_tests"],
        expected_failing_job=data["expected_failing_job"],
        expected_skipped_jobs=data["expected_skipped_jobs"],
        notes=data["notes"],
        description=description,
        raw=data,
    )


def repo_slug() -> str:
    """Return ``owner/name`` parsed from the ``origin`` remote URL.

    Raises:
        Abort: If the remote is not a recognisable GitHub URL.
    """
    url = git("remote", "get-url", REMOTE)
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if not match:
        raise Abort(f"remote {REMOTE!r} is not a GitHub URL")
    return f"{match.group(1)}/{match.group(2)}"


def workflow_jobs(workflow_path: Path) -> dict[str, str]:
    """Map display name -> job key for the jobs declared in a workflow file.

    GitHub's API reports jobs by display name ("Import & route smoke tests")
    while specs and records use job keys ("smoke"). This is a deliberately
    narrow parser for the flat ``jobs:`` layout used in this repo; it aborts
    rather than guessing if the layout is not what it expects.

    Raises:
        Abort: If no jobs are found or two jobs share a display name.
    """
    lines = workflow_path.read_text(encoding="utf-8").splitlines()
    mapping: dict[str, str] = {}
    in_jobs = False
    current_key: str | None = None
    current_name: str | None = None

    def flush() -> None:
        if current_key is None:
            return
        display = current_name or current_key
        if display in mapping:
            raise Abort(f"two jobs share the display name {display!r} in {workflow_path}")
        mapping[display] = current_key

    for line in lines:
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if line and not line[0].isspace() and not line.startswith("#"):
            break
        key_match = re.match(r"^  ([A-Za-z_][\w-]*):\s*$", line)
        if key_match:
            flush()
            current_key, current_name = key_match.group(1), None
            continue
        name_match = re.match(r"^    name:\s*(.+?)\s*$", line)
        if name_match and current_key is not None:
            current_name = name_match.group(1).strip("'\"")
    flush()

    if not mapping:
        raise Abort(f"no jobs found in {workflow_path}")
    return mapping


def log_path(spec: Spec, index: int, representative: bool) -> Path:
    """Path of a saved job log: ``{id}.log`` for the representative run, else ``{id}.run{k}.log``."""
    return LOGS_DIR / (f"{spec.id}.log" if representative else f"{spec.id}.run{index}.log")


# --------------------------------------------------------------------------- #
# GitHub API
# --------------------------------------------------------------------------- #


class GitHub:
    """Minimal GitHub Actions client. The token lives only in the session headers."""

    def __init__(self, token: str, slug: str) -> None:
        """Create a session authenticated with ``token`` for repo ``slug``."""
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        self._base = f"{API_ROOT}/repos/{slug}"

    def _get(self, path: str, params: dict[str, Any] | None = None) -> requests.Response:
        """GET ``path`` under the repo, aborting on transport or HTTP errors.

        Connection errors and timeouts are retried after each delay in
        ``RETRY_DELAYS_S``; HTTP error statuses are never retried.
        Error messages carry the status code and path only, never headers.
        """
        url = f"{self._base}{path}"
        for attempt, delay in enumerate((*RETRY_DELAYS_S, None), start=1):
            try:
                response = self._session.get(url, params=params, timeout=60)
                break
            except (requests.ConnectionError, requests.Timeout) as exc:
                if delay is None:
                    raise Abort(
                        f"GitHub request to {path} failed after {attempt} attempts: {type(exc).__name__}"
                    ) from exc
                log(
                    f"GitHub request to {path} failed ({type(exc).__name__}); "
                    f"retry {attempt}/{len(RETRY_DELAYS_S)} in {delay}s"
                )
                time.sleep(delay)
            except requests.RequestException as exc:
                raise Abort(f"GitHub request to {path} failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise Abort(f"GitHub API {path} returned HTTP {response.status_code}")
        return response

    def runs_for_sha(self, workflow_file: str, sha: str) -> list[dict[str, Any]]:
        """Return push-triggered runs of ``workflow_file`` for commit ``sha``."""
        response = self._get(
            f"/actions/workflows/{workflow_file}/runs",
            params={"head_sha": sha, "event": "push", "per_page": 20},
        )
        runs = response.json()["workflow_runs"]
        return [run for run in runs if run["head_sha"] == sha]

    def run(self, run_id: int) -> dict[str, Any]:
        """Return a single workflow run."""
        return self._get(f"/actions/runs/{run_id}").json()

    def jobs(self, run_id: int) -> list[dict[str, Any]]:
        """Return the jobs of the latest attempt of a run."""
        return self._get(f"/actions/runs/{run_id}/jobs", params={"per_page": 100}).json()["jobs"]

    def job_log(self, job_id: int) -> bytes:
        """Return the raw bytes of a job's log, exactly as GitHub serves it.

        The endpoint redirects to blob storage; ``requests`` drops the
        Authorization header on the cross-host redirect, which is intended.
        """
        return self._get(f"/actions/jobs/{job_id}/logs").content


def latest_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the most recently created run, or None."""
    return max(runs, key=lambda run: (run["created_at"], run["id"]), default=None)


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #


def check_local_state() -> str:
    """Clean tracked files, on main, in sync with origin. Returns the HEAD SHA.

    Untracked files are ignored: only ``target_file`` is ever staged, and the
    spec and a previous run's record are typically untracked.

    Raises:
        Abort: If any of those conditions fails.
    """
    dirty = git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise Abort(f"working tree is not clean:\n{dirty}")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != BRANCH:
        raise Abort(f"must be on {BRANCH!r}, currently on {branch!r}")
    git("fetch", "--quiet", REMOTE, BRANCH)
    head = git("rev-parse", "HEAD")
    remote_head = git("rev-parse", f"{REMOTE}/{BRANCH}")
    if head != remote_head:
        raise Abort(f"HEAD {head[:7]} differs from {REMOTE}/{BRANCH} {remote_head[:7]}; push or pull first")
    return head


def check_preconditions(spec: Spec, gh: GitHub, workflow_file: str, wait_for_green_s: int) -> str:
    """Step 1: outputs are free, tree is clean and in sync, HEAD is green.

    If HEAD's run is still in progress (typically the revert pushed by the
    previous injection), wait up to ``wait_for_green_s`` for it to finish
    rather than aborting. A completed run that is not a success aborts.

    Returns:
        The HEAD commit SHA.

    Raises:
        Abort: If any precondition fails.
    """
    record_path = INJECTIONS_DIR / f"{spec.id}.json"
    if record_path.exists():
        raise Abort(f"{rel(record_path)} already exists; refusing to overwrite a record")
    existing_logs = sorted(LOGS_DIR.glob(f"{spec.id}.*log"))
    if existing_logs:
        raise Abort(f"logs for {spec.id} already exist: {[rel(p) for p in existing_logs]}")
    if is_git_ignored(log_path(spec, 1, representative=True)):
        raise Abort(f"{rel(LOGS_DIR)} is git-ignored; saved logs would never be committed. Fix .gitignore first")

    head = check_local_state()
    run = latest_run(gh.runs_for_sha(workflow_file, head))
    if run is None or run["status"] != "completed":
        if wait_for_green_s <= 0:
            state = "has no CI run" if run is None else f"has CI run {run['id']} still {run['status']}"
            raise Abort(f"HEAD {head[:7]} {state}; not waiting (--wait-for-green 0)")
        log(f"HEAD {head[:7]} CI not finished; waiting up to {wait_for_green_s // 60} min for it")
        run = wait_for_run(gh, workflow_file, head, wait_for_green_s)
        if check_local_state() != head:
            raise Abort(f"{REMOTE}/{BRANCH} moved while waiting for HEAD's CI; re-run the script")
    if run["conclusion"] != "success":
        raise Abort(f"HEAD {head[:7]} is not green: run {run['id']} concluded {run['conclusion']!r}")
    log(f"HEAD {head[:7]} is clean, in sync and green (run {run['id']})")
    return head


def apply_mutation(spec: Spec, *, write: bool) -> str:
    """Steps 2-3: check ``find`` occurs once, replace it, preserve encoding and EOLs.

    The file is read as bytes and decoded without newline translation, so
    existing line endings survive untouched. If the file uses CRLF and the
    spec's strings use bare LF, the spec strings are converted to match.

    Args:
        spec: The injection spec.
        write: If False, compute the change but leave the file untouched.

    Returns:
        A unified diff of the change.

    Raises:
        Abort: On a missing file, undecodable content, or a match count
            other than exactly one.
    """
    path = REPO_ROOT / spec.target_file
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Abort(f"cannot read {spec.target_file}: {exc}") from exc

    encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
    try:
        original = raw.decode(encoding)
    except UnicodeDecodeError as exc:
        raise Abort(f"{spec.target_file} is not valid UTF-8: {exc}") from exc

    find, replace = spec.find, spec.replace
    if "\r\n" in original and "\n" in find and "\r\n" not in find:
        find = find.replace("\n", "\r\n")
        replace = replace.replace("\n", "\r\n")

    count = original.count(find)
    if count != 1:
        raise Abort(f"'find' occurs {count} times in {spec.target_file}; expected exactly 1")

    mutated = original.replace(find, replace, 1)
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            mutated.splitlines(keepends=True),
            fromfile=f"a/{spec.target_file}",
            tofile=f"b/{spec.target_file}",
        )
    )

    if write:
        path.write_bytes(mutated.encode(encoding))
        if path.read_bytes() != mutated.encode(encoding):
            raise Abort(f"write-back verification failed for {spec.target_file}")
        log(f"mutated {spec.target_file} ({encoding}, {'CRLF' if chr(13) in original else 'LF'})")
    return diff


def commit_and_push(spec: Spec, state: RecoveryState) -> str:
    """Steps 4-5: commit the target file only, push, and return the new SHA.

    Raises:
        Abort: If git fails. If the commit succeeded but the push did not,
            the local commit is left for the user to inspect or reset.
    """
    git("add", "--", spec.target_file)
    staged = git("diff", "--cached", "--name-only").splitlines()
    if staged != [spec.target_file]:
        raise Abort(f"unexpected staged files: {staged}")
    git("commit", "--quiet", "-m", spec.commit_message)
    sha = git("rev-parse", "HEAD")
    state.injection_sha = sha
    try:
        git("push", "--quiet", REMOTE, BRANCH)
    except Abort as exc:
        raise Abort(
            f"{exc}\nThe injection commit {sha[:7]} exists locally but was NOT pushed. "
            f"Undo it with: git reset --hard {sha}~1"
        ) from exc
    state.injection_pushed = True
    log(f"pushed injection commit {sha[:7]}: {spec.commit_message}")
    return sha


def push_rerun_commit(spec: Spec, index: int, total: int) -> str:
    """Push an empty commit to re-trigger CI on the injected code. Returns its SHA."""
    git("commit", "--quiet", "--allow-empty", "-m", f"inject: {spec.id} rerun {index}/{total}")
    sha = git("rev-parse", "HEAD")
    git("push", "--quiet", REMOTE, BRANCH)
    log(f"pushed empty rerun commit {sha[:7]} ({index}/{total})")
    return sha


def wait_for_run(gh: GitHub, workflow_file: str, sha: str, timeout_s: int = POLL_TIMEOUT_S) -> dict[str, Any]:
    """Step 6: poll until the run for ``sha`` completes.

    A missing run is normal for the first ``RUN_CREATION_GRACE_S`` seconds.

    Raises:
        Abort: If no run appears within the grace period, or the run does
            not complete within ``timeout_s``.
    """
    start = time.monotonic()
    run_id: int | None = None
    while True:
        elapsed = time.monotonic() - start
        if run_id is None:
            run = latest_run(gh.runs_for_sha(workflow_file, sha))
            if run is None:
                if elapsed > RUN_CREATION_GRACE_S:
                    raise Abort(f"no CI run was created for {sha[:7]} within {RUN_CREATION_GRACE_S}s")
                log(f"run not created yet ({elapsed:.0f}s)")
            else:
                run_id = run["id"]
                log(f"found run {run_id}: {run['html_url']}")
        if run_id is not None:
            run = gh.run(run_id)
            if run["status"] == "completed":
                log(f"run {run_id} completed: {run['conclusion']}")
                return run
            log(f"run {run_id} {run['status']} ({elapsed:.0f}s)")
        if elapsed > timeout_s:
            raise Abort(f"run for {sha[:7]} did not complete within {timeout_s // 60} minutes")
        time.sleep(POLL_INTERVAL_S)


def parse_pytest(log_text: str) -> tuple[dict[str, int], set[str]] | None:
    """Extract pytest outcome counts and failing test ids from a job log.

    Returns:
        ``(counts, failing_ids)`` or None if the log has no pytest summary.
        ``failing_ids`` includes both FAILED and ERROR entries from the
        short test summary.
    """
    summaries = re.findall(r"=+ ([^=\n]*?\d+ \w+[^=\n]*?) in [\d.]+s(?: \([^)]*\))? =+", log_text)
    if not summaries:
        return None
    counts: dict[str, int] = {}
    for number, outcome in re.findall(r"(\d+) (\w+)", summaries[-1]):
        outcome = {"errors": "error", "warning": "warnings"}.get(outcome, outcome)
        counts[outcome] = int(number)
    failing = set(re.findall(r"(?:^|\s)(?:FAILED|ERROR) (tests/[^\s]+)", log_text, flags=re.MULTILINE))
    return counts, failing


def observe(gh: GitHub, run: dict[str, Any], names_to_keys: dict[str, str], index: int, sha: str) -> RunResult:
    """Step 7: collect job conclusions, the failing step, pytest results and the failing log.

    Raises:
        Abort: If a job in the run is not declared in the workflow file.
    """
    jobs = gh.jobs(run["id"])
    conclusions: dict[str, str] = {}
    by_key: dict[str, dict[str, Any]] = {}
    for job in jobs:
        key = names_to_keys.get(job["name"])
        if key is None:
            raise Abort(f"job {job['name']!r} is not declared in the workflow file")
        conclusions[key] = job["conclusion"]
        by_key[key] = job

    failing_keys = [key for key, conclusion in conclusions.items() if conclusion in FAILED_CONCLUSIONS]
    failing_job = by_key[failing_keys[0]] if len(failing_keys) == 1 else None

    failing_step: str | None = None
    duration_s: int | None = None
    if failing_job is not None:
        failing_step = next(
            (step["name"] for step in failing_job["steps"] if step["conclusion"] in FAILED_CONCLUSIONS),
            None,
        )
        if failing_job["started_at"] and failing_job["completed_at"]:
            duration_s = round(
                (parse_timestamp(failing_job["completed_at"]) - parse_timestamp(failing_job["started_at"])).total_seconds()
            )

    # Failing job's log first (it is kept as evidence); fall back to other jobs
    # that ran, so a docker-only failure still records the pytest counts.
    failing_log = gh.job_log(failing_job["id"]) if failing_job is not None else None
    pytest_result = parse_pytest(failing_log.decode("utf-8", errors="replace")) if failing_log else None
    if pytest_result is None:
        for job in jobs:
            if job is failing_job or job["conclusion"] == "skipped":
                continue
            pytest_result = parse_pytest(gh.job_log(job["id"]).decode("utf-8", errors="replace"))
            if pytest_result is not None:
                break
    counts, failing_tests = pytest_result if pytest_result else ({}, set())

    return RunResult(
        index=index,
        sha=sha,
        run_id=str(run["id"]),
        run_url=run["html_url"],
        conclusion=run["conclusion"],
        job_id=str(failing_job["id"]) if failing_job else None,
        failing_keys=failing_keys,
        failing_tests=failing_tests,
        pytest_ran=pytest_result is not None,
        failing_log=failing_log,
        observed={
            "failed": counts.get("failed", 0) if pytest_result else None,
            "passed": counts.get("passed", 0) if pytest_result else None,
            "errors": counts.get("error", 0) if pytest_result else None,
            "failing_step": failing_step,
            "duration_s": duration_s,
            "jobs": conclusions,
        },
    )


def compare(spec: Spec, result: RunResult) -> list[str]:
    """Return every difference between the spec's expected failure and one run."""
    problems: list[str] = []
    conclusions: dict[str, str] = result.observed["jobs"]

    if spec.expected_failing_job not in conclusions:
        problems.append(f"expected failing job {spec.expected_failing_job!r} not in run {sorted(conclusions)}")
    if result.failing_keys != [spec.expected_failing_job]:
        problems.append(f"failing jobs: expected [{spec.expected_failing_job!r}], observed {result.failing_keys}")

    observed_skipped = sorted(key for key, c in conclusions.items() if c == "skipped")
    if observed_skipped != sorted(spec.expected_skipped_jobs):
        problems.append(f"skipped jobs: expected {sorted(spec.expected_skipped_jobs)}, observed {observed_skipped}")

    expected_ok = set(conclusions) - {spec.expected_failing_job} - set(spec.expected_skipped_jobs)
    for key in sorted(expected_ok):
        if conclusions[key] != "success":
            problems.append(f"job {key!r} expected success, observed {conclusions[key]!r}")

    expected_tests = set(spec.expected_failing_tests)
    if expected_tests and not result.pytest_ran:
        problems.append("expected failing tests but no pytest summary was found in any job log")
    if expected_tests != result.failing_tests:
        missing = sorted(expected_tests - result.failing_tests)
        extra = sorted(result.failing_tests - expected_tests)
        problems.append(f"failing tests differ: missing {missing}, unexpected {extra}")
    if result.observed["failing_step"] is None and result.failing_keys:
        problems.append("could not identify the failing step")
    return problems


def judge(spec: Spec, results: list[RunResult]) -> list[str]:
    """Decide whether the whole set of runs supports the spec's label.

    Non-flaky categories must fail as expected on every run. A flaky label
    needs a non-uniform mix: at least one expected failure and at least one
    fully green run, and nothing else (a run failing some other way means the
    injection does something other than what the spec claims).
    """
    for result in results:
        result.problems = compare(spec, result)

    if spec.category != FLAKY:
        return [f"run {r.index} ({r.run_id}): {p}" for r in results for p in r.problems]

    problems: list[str] = []
    for result in results:
        if result.outcome == "X":
            problems.extend(f"run {result.index} ({result.run_id}) neither expected failure nor green: {p}"
                            for p in result.problems)
    outcomes = {result.outcome for result in results}
    if "F" not in outcomes:
        problems.append("flaky label needs at least one expected failure; none observed")
    if "P" not in outcomes:
        problems.append("flaky label needs at least one green run; none observed")
    return problems


def save_logs(spec: Spec, results: list[RunResult], representative: RunResult) -> dict[int, str]:
    """Write the failing job log of every failing run. Returns run index -> repo-relative path."""
    LOGS_DIR.mkdir(exist_ok=True)
    saved: dict[int, str] = {}
    for result in results:
        if result.failing_log is None:
            continue
        path = log_path(spec, result.index, representative=result is representative)
        path.write_bytes(result.failing_log)
        saved[result.index] = rel(path)
        log(f"saved {rel(path)} ({len(result.failing_log)} bytes)")
    return saved


def write_record(spec: Spec, results: list[RunResult], representative: RunResult, logs: dict[int, str]) -> Path:
    """Step 8: write ``injections/{id}.json`` in the ``inj-001.json`` layout.

    Top-level ``run_id``/``job_id``/``observed`` describe the representative
    run (the first expected failure); ``observed.runs`` lists every run.
    """
    observed = dict(representative.observed)
    observed["runs"] = [
        {
            "run_id": result.run_id,
            "commit": result.sha[:7],
            "conclusion": result.conclusion,
            "jobs": result.observed["jobs"],
            "failed": result.observed["failed"],
            "passed": result.observed["passed"],
            "log_file": logs.get(result.index),
        }
        for result in results
    ]
    record = {
        "id": spec.id,
        "category": spec.category,
        "commit": results[0].sha[:7],
        "run_id": representative.run_id,
        "job_id": representative.job_id,
        "target_file": spec.target_file,
        "mutation": spec.mutation,
        "expected_failing_tests": spec.expected_failing_tests,
        "expected_failing_job": spec.expected_failing_job,
        "expected_skipped_jobs": spec.expected_skipped_jobs,
        "log_file": logs.get(representative.index),
        "observed": observed,
        "notes": spec.notes,
    }
    path = INJECTIONS_DIR / f"{spec.id}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"wrote {rel(path)}")
    return path


def revert_and_push(sha: str, state: RecoveryState) -> None:
    """Step 9: revert the injection commit and push the revert."""
    git("revert", "--no-edit", sha)
    state.revert_committed = True
    git("push", "--quiet", REMOTE, BRANCH)
    state.revert_pushed = True
    log(f"reverted {sha[:7]} as {git('rev-parse', '--short=7', 'HEAD')} and pushed")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def run(args: argparse.Namespace, state: RecoveryState) -> int:
    """Execute the injection pipeline. Returns the process exit code."""
    spec = load_spec(Path(args.spec))
    if args.runs < 1:
        raise Abort("--runs must be at least 1")
    if spec.category == FLAKY and args.runs < 2:
        raise Abort("a flaky label needs a mix of outcomes; use --runs 2 or more")
    if args.wait_for_green < 0:
        raise Abort("--wait-for-green must be >= 0")

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise Abort("GITHUB_TOKEN environment variable is not set")
    slug = repo_slug()
    gh = GitHub(token, slug)
    workflow_path = REPO_ROOT / ".github" / "workflows" / args.workflow
    names_to_keys = workflow_jobs(workflow_path)

    check_preconditions(spec, gh, args.workflow, args.wait_for_green * 60)

    if args.dry_run:
        diff = apply_mutation(spec, write=False)
        print(diff, end="" if diff.endswith("\n") else "\n")
        print(f"DRY RUN {spec.id} {spec.category}: would commit {spec.commit_message!r} "
              f"to {slug} and run CI {args.runs} time(s)")
        return 0

    apply_mutation(spec, write=True)
    injection_sha = commit_and_push(spec, state)

    results: list[RunResult] = []
    for index in range(1, args.runs + 1):
        sha = injection_sha if index == 1 else push_rerun_commit(spec, index, args.runs)
        completed = wait_for_run(gh, args.workflow, sha)
        results.append(observe(gh, completed, names_to_keys, index, sha))

    problems = judge(spec, results)
    outcomes = "".join(result.outcome for result in results)
    if problems:
        print(f"{spec.id} {spec.category} runs={args.runs} outcomes={outcomes} match=N", flush=True)
        detail = "\n  - ".join(problems)
        observed = json.dumps([{"run": r.run_url, **r.observed} for r in results], indent=2)
        raise Abort(
            f"observed runs do not support the expected label:\n  - {detail}\n"
            f"observed: {observed}\nNo record or logs were written."
        )

    representative = next(result for result in results if result.matches)
    logs = save_logs(spec, results, representative)
    write_record(spec, results, representative, logs)
    revert_and_push(injection_sha, state)
    print(f"{spec.id} {spec.category} runs={args.runs} outcomes={outcomes} match=Y")
    return 0


def main() -> int:
    """Parse arguments, run, and turn any abort into a loud, actionable exit."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", help="path to the injection spec JSON")
    parser.add_argument("--dry-run", action="store_true", help="check preconditions and show the diff; change nothing")
    parser.add_argument("--workflow", default="ci.yml", help="workflow file under .github/workflows (default: ci.yml)")
    parser.add_argument("--runs", type=int, default=1,
                        help="CI runs on the injected code, re-triggered by empty commits (default: 1; flaky needs >= 2)")
    parser.add_argument("--wait-for-green", type=int, default=DEFAULT_WAIT_FOR_GREEN_MIN, metavar="MINUTES",
                        help="wait this long for HEAD's in-progress CI run before starting; 0 = don't wait "
                             f"(default: {DEFAULT_WAIT_FOR_GREEN_MIN})")
    args = parser.parse_args()

    state = RecoveryState()
    try:
        return run(args, state)
    except (Abort, KeyboardInterrupt) as exc:
        reason = "interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc)
        print(f"\nABORT: {reason}", file=sys.stderr)
        print_recovery(state)
        return 1
    except Exception:
        # Unexpected failure (e.g. an API response shape we did not anticipate):
        # still say how to un-break main, then let the traceback surface.
        print_recovery(state)
        raise


def print_recovery(state: RecoveryState) -> None:
    """Print the commands to restore main, if the injection reached the remote."""
    commands = state.instructions()
    if not commands:
        return
    print(
        f"\n!!! Injection {state.injection_sha} is on {REMOTE}/{BRANCH} and main is BROKEN. "
        "Restore it with:\n",
        file=sys.stderr,
    )
    for command in commands:
        print(f"    {command}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
