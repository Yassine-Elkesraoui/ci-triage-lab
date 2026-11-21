"""Run one v1.1 holdout case (inj-061..inj-090) as ``holdout_plan.md`` sections 3 and 5 describe.

Called by ``run_injection.py`` for spec ids above 060; v1.0 ids never reach this module, so every v1.0
code path stays as it was. Compared with v1.0:

- The injection is committed on the spec's ``branch`` with the spec's ``commit_message``
  (``Update <target_file>``) and pushed as ``<branch>:main``. The branch never reaches the remote.
- Five modes, chosen from the spec and the decoy draw:

  ``single``      push X, judge the run (as v1.0 with one run).
  ``decoy``       push X and judge it (the decoy attempt, recorded but out of scope), force main back to the
                  parent P, recreate X as X' (same tree and message, new committer date), push X', judge.
  ``fail_same``   push X (run A), force back to P and wait for its green run, push X again (run B). A and B
                  must both fail as the spec says, and fail the same way (same step, tests and error line).
  ``flaky_rerun`` as ``fail_same``, but A must pass and B must fail. A failing A, or a passing B, discards the
                  commit: force back, recreate, start again (at most ``MAX_ATTEMPTS`` commits).
  ``flaky``       push X; a passing run discards it (force back, recreate, push) until a run fails as
                  expected. If no discarded attempt passed, verification runs follow (force back, push the
                  same X again) until one passes. They are created after the triaged run, so the replay never
                  shows them.

- Every push records the run ids already seen for its sha, and ``wait_for_run`` ignores them, so a re-push
  never mistakes an earlier run (A, or P's original run) for the new one.
- Every run that is not in ``observed.runs`` (decoy, discarded, parent and verification runs) is listed in
  the record under ``holdout.extra_runs``; its failing log is saved as
  ``logs/extra-<id>-<role><k>-run<run_id>.log``, the naming the replay already understands.
- inj-070 has a clock gate: run A is refused from 10:50 UTC, and run B is never pushed before 11:00 UTC (the
  harness waits for 11:00). The gate is valid only while New Zealand is on daylight time.

``--dry-run`` drives the same code with a simulated GitHub and git commands that are printed instead of run:
nothing is pushed, committed, written or fetched, and no API request is made. ``--simulate`` sets the
simulated outcomes of the injection pushes (for example ``P,F``).
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import run_injection as ri
from run_injection import FLAKY, Abort, RecoveryState, RunResult, Spec, compare, latest_run, log, observe, wait_for_run

UTC = timezone.utc
V11_FIRST, V11_LAST = 61, 90
SEED = 20261120
MAX_ATTEMPTS = 10
MAX_VERIFICATION_RUNS = 10
BRANCH_PATTERN = re.compile(r"wip-[0-9a-f]{6}")
RERUN_VALUES = ("fail_same", "pass", None)

# holdout_plan.md section 3: random.Random(SEED + 4), r.sample(pool, 2) per category from the cases without a
# planned rerun. tests/test_holdout_runner.py recomputes the draw and checks it against this set.
DECOY_CASES = frozenset({
    "inj-061", "inj-064", "inj-072", "inj-075", "inj-079",
    "inj-080", "inj-083", "inj-084", "inj-089", "inj-090",
})
DECOY_CATEGORIES = ("real_regression", "dependency", "environment", "timeout", "build")


def run_order() -> list[str]:
    """holdout_plan.md section 4: the 30 ids in numeric order, shuffled with ``random.Random(SEED)``."""
    order = [f"inj-{n:03d}" for n in range(V11_FIRST, V11_LAST + 1)]
    random.Random(SEED).shuffle(order)
    return order


def is_holdout(spec_id: str) -> bool:
    """True for the v1.1 ids, inj-061 to inj-090."""
    return V11_FIRST <= int(spec_id.split("-")[1]) <= V11_LAST


# --------------------------------------------------------------------------- #
# inj-070 clock gate
# --------------------------------------------------------------------------- #

GATED_CASE = "inj-070"
GATE_A_LATEST = (10, 50)  # run A is refused from 10:50 UTC
GATE_B_EARLIEST = (11, 0)  # run B is never pushed before 11:00 UTC
# The gate assumes New Zealand daylight time (UTC+13), when the Auckland date runs ahead of the UTC date from
# 11:00 UTC. NZDT runs from 2026-09-27 02:00 NZST (2026-09-26 14:00 UTC) to 2027-04-04 03:00 NZDT
# (2027-04-03 14:00 UTC). Outside that window the boundary is 12:00 UTC and the gate must be re-derived.
GATE_VALID_FROM = datetime(2026, 9, 26, 14, 0, tzinfo=UTC)
GATE_VALID_UNTIL = datetime(2027, 4, 3, 14, 0, tzinfo=UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _check_gate_valid(now: datetime) -> None:
    if not GATE_VALID_FROM <= now < GATE_VALID_UNTIL:
        raise Abort(f"{GATED_CASE} gate: {now:%Y-%m-%d %H:%M} UTC is outside NZ daylight time "
                    f"({GATE_VALID_FROM:%Y-%m-%d} to {GATE_VALID_UNTIL:%Y-%m-%d}); the 11:00 UTC boundary "
                    "no longer holds. Re-derive the gate before running this case")


def gate_run_a(now: datetime) -> None:
    """Refuse run A of inj-070 from 10:50 UTC, so its pytest step runs while the dates still agree."""
    _check_gate_valid(now)
    if (now.hour, now.minute) >= GATE_A_LATEST:
        raise Abort(f"{GATED_CASE} gate: run A must be pushed before {GATE_A_LATEST[0]:02d}:{GATE_A_LATEST[1]:02d} "
                    f"UTC and it is {now:%H:%M} UTC; run this case between 00:00 and 10:49 UTC")


def gate_run_b_wait(now: datetime) -> timedelta:
    """How long to wait before run B of inj-070 may be pushed (zero from 11:00 UTC)."""
    _check_gate_valid(now)
    earliest = now.replace(hour=GATE_B_EARLIEST[0], minute=GATE_B_EARLIEST[1], second=0, microsecond=0)
    return max(earliest - now, timedelta(0))


# --------------------------------------------------------------------------- #
# Failure signature ("fails the same way")
# --------------------------------------------------------------------------- #

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_TIMESTAMP = re.compile(r"^﻿?\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z ?")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_LONG_ID = re.compile(r"\b(?=[0-9a-z]*\d)[0-9a-z]{16,}\b")
_DIGITS = re.compile(r"\d+")


def _normalise(line: str) -> str:
    line = _UUID.sub("<uuid>", line)
    line = _LONG_ID.sub("<id>", line)
    return _DIGITS.sub("#", line).strip()


def error_signature(log_bytes: bytes | None) -> list[str]:
    """The last content line before the first ``##[error]``, then every ``##[error]`` line, normalised.

    Timestamps, ANSI colours, UUIDs (temp script names), long generated ids (docker refs, shas) and every digit
    run (durations, counts) are masked, so two runs of the same failure give the same signature and two
    different failures in the same step usually do not. Empty if the log has no ``##[error]`` line.
    """
    if not log_bytes:
        return []
    lines = [_TIMESTAMP.sub("", _ANSI.sub("", line)).rstrip()
             for line in log_bytes.decode("utf-8", errors="replace").splitlines()]
    first = next((i for i, line in enumerate(lines) if line.startswith("##[error]")), None)
    if first is None:
        return []
    before = next((line for line in reversed(lines[:first]) if line.strip() and not line.startswith("##[")), "")
    return [_normalise(before)] + [_normalise(line) for line in lines if line.startswith("##[error]")]


def same_failure_problems(a: RunResult, b: RunResult) -> list[str]:
    """Differences between two runs that must fail the same way (section 3)."""
    problems = []
    if a.failing_keys != b.failing_keys:
        problems.append(f"failing jobs differ: A {a.failing_keys}, B {b.failing_keys}")
    if a.observed["failing_step"] != b.observed["failing_step"]:
        problems.append(f"failing step differs: A {a.observed['failing_step']!r}, B {b.observed['failing_step']!r}")
    if a.failing_tests != b.failing_tests:
        problems.append(f"failing tests differ: A {sorted(a.failing_tests)}, B {sorted(b.failing_tests)}")
    sig_a, sig_b = error_signature(a.failing_log), error_signature(b.failing_log)
    if not sig_a or not sig_b:
        problems.append("a failing log has no ##[error] line, so the error line cannot be compared")
    elif sig_a != sig_b:
        problems.append(f"error line differs: A {sig_a}, B {sig_b}")
    return problems


# --------------------------------------------------------------------------- #
# Spec checks and mode
# --------------------------------------------------------------------------- #


def case_mode(spec: Spec) -> str:
    """``single``, ``decoy``, ``fail_same``, ``flaky_rerun`` or ``flaky`` (see the module docstring)."""
    rerun = spec.raw.get("same_commit_rerun")
    if spec.category == FLAKY:
        if rerun == "fail_same":
            raise Abort(f"{spec.id}: a flaky case's earlier run must pass, not fail_same")
        return "flaky_rerun" if rerun == "pass" else "flaky"
    if rerun == "pass":
        raise Abort(f"{spec.id}: only flaky cases have a passing earlier run")
    if rerun == "fail_same":
        if spec.id in DECOY_CASES:
            raise Abort(f"{spec.id}: a rerun case cannot also be a decoy")
        return "fail_same"
    return "decoy" if spec.id in DECOY_CASES else "single"


def check_holdout_spec(spec: Spec) -> None:
    """The v1.1 keys that ``load_spec`` ignores: present, well-formed and neutral."""
    raw = spec.raw
    for key in ("family", "commit_message", "branch", "same_commit_rerun"):
        if key not in raw:
            raise Abort(f"{spec.id}: v1.1 spec is missing {key!r}")
    if not isinstance(raw["family"], str) or not raw["family"]:
        raise Abort(f"{spec.id}: 'family' must be a non-empty string")
    if raw["commit_message"] != f"Update {spec.target_file}":
        raise Abort(f"{spec.id}: commit_message must be 'Update {spec.target_file}', got {raw['commit_message']!r}")
    if not isinstance(raw["branch"], str) or not BRANCH_PATTERN.fullmatch(raw["branch"]):
        raise Abort(f"{spec.id}: branch {raw['branch']!r} must look like wip-0a1b2c")
    if raw["same_commit_rerun"] not in RERUN_VALUES:
        raise Abort(f"{spec.id}: same_commit_rerun must be one of {RERUN_VALUES}")
    case_mode(spec)


def case_specific_checks(spec: Spec, actions: "GitActions") -> None:
    """Checks the plan asks for before particular cases."""
    if spec.id == "inj-079":
        entry = actions.read("ls-files", "-s", "--", "stats.py")
        if not entry.startswith("100644 "):
            raise Abort(f"inj-079 needs stats.py tracked as 100644 (not executable); git ls-files -s shows {entry!r}")
        log("inj-079: stats.py is tracked as 100644")


# --------------------------------------------------------------------------- #
# Git actions: real, and printed for --dry-run
# --------------------------------------------------------------------------- #


class GitActions:
    """Every git command the holdout runner issues. Reads always run; writes run here and print in the dry run."""

    def __init__(self, spec: Spec) -> None:
        self.spec = spec
        self.branch: str = spec.raw["branch"]
        self.push_listeners: list[Callable[[str, str], None]] = []

    # -- plumbing -------------------------------------------------------------

    def _git(self, *args: str, env: dict[str, str] | None = None) -> str:
        """Run a git command that changes something (overridden by the dry run)."""
        result = subprocess.run(["git", *args], cwd=ri.REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
                                env=None if env is None else {**os.environ, **env})
        if result.returncode != 0:
            raise Abort(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
        return result.stdout.strip()

    def read(self, *args: str) -> str:
        """Run a read-only git command (also in the dry run)."""
        return ri.git(*args)

    def _notify(self, sha: str, kind: str) -> None:
        for listener in self.push_listeners:
            listener(sha, kind)

    # -- steps ----------------------------------------------------------------

    def fetch(self) -> None:
        self._git("fetch", "--quiet", ri.REMOTE, ri.BRANCH)

    def start_branch(self) -> None:
        self._git("checkout", "--quiet", "-b", self.branch)

    def commit_injection(self) -> str:
        """Apply the mutation, commit only the target file with the spec's message. Returns the sha."""
        ri.apply_mutation(self.spec, write=True)
        self._git("add", "--", self.spec.target_file)
        staged = self.read("diff", "--cached", "--name-only").splitlines()
        if staged != [self.spec.target_file]:
            raise Abort(f"unexpected staged files: {staged}")
        self._git("commit", "--quiet", "-m", self.spec.raw["commit_message"])
        return self.read("rev-parse", "HEAD")

    def recreate(self, sha: str) -> str:
        """Same tree, parent, message and author as ``sha``, new committer date: a new sha (section 3)."""
        if self.read("rev-parse", "HEAD") != sha:
            raise Abort(f"cannot recreate {sha[:7]}: it is not the tip of {self.branch}")
        previous = int(self.read("log", "-1", "--format=%ct", sha))
        epoch = max(int(datetime.now(UTC).timestamp()), previous + 1)
        self._git("commit", "--amend", "--no-edit", "--quiet", env={"GIT_COMMITTER_DATE": f"@{epoch} +0000"})
        new = self.read("rev-parse", "HEAD")
        if new == sha or self.read("rev-parse", f"{new}^{{tree}}") != self.read("rev-parse", f"{sha}^{{tree}}") \
                or self.read("rev-parse", f"{new}~1") != self.read("rev-parse", f"{sha}~1"):
            raise Abort(f"recreating {sha[:7]} did not give a new commit with the same tree and parent")
        return new

    def push_branch(self, sha: str, kind: str = "injection") -> None:
        """``git push origin <branch>:main``; ``sha`` must be the branch tip (a fast-forward of main)."""
        self._git("push", "--quiet", ri.REMOTE, f"{self.branch}:{ri.BRANCH}")
        self._notify(sha, kind)

    def force_main(self, sha: str, expected_remote: str) -> None:
        """Move main back to ``sha``, but only if it is still at ``expected_remote``."""
        self._git("push", "--quiet", f"--force-with-lease=refs/heads/{ri.BRANCH}:{expected_remote}",
                  ri.REMOTE, f"{sha}:refs/heads/{ri.BRANCH}")
        self._notify(sha, "parent")

    def revert(self, sha: str) -> str:
        self._git("revert", "--no-edit", sha)
        return self.read("rev-parse", "HEAD")

    def finish(self) -> None:
        """Back on main at the pushed revert, with the working branch deleted."""
        self._git("checkout", "--quiet", ri.BRANCH)
        self._git("pull", "--quiet", "--ff-only", ri.REMOTE, ri.BRANCH)
        self._git("branch", "--quiet", "-D", self.branch)

    def write_outputs(self, write: Callable[[], None], paths: list[str]) -> None:
        write()


class DryRunActions(GitActions):
    """Prints every git command and file write instead of doing it. Created shas are placeholders."""

    def __init__(self, spec: Spec, echo: Callable[[str], None]) -> None:
        super().__init__(spec)
        self.echo = echo
        self._commits = 0
        self._tips: dict[str, str] = {}

    def _git(self, *args: str, env: dict[str, str] | None = None) -> str:
        prefix = "".join(f"{key}='{value}' " for key, value in (env or {}).items())
        self.echo(f"[git]   {prefix}git {' '.join(_quote(a) for a in args)}")
        return ""

    def commit_injection(self) -> str:
        diff = ri.apply_mutation(self.spec, write=False)
        self.echo(f"[edit]  {self.spec.target_file} (diff below, not written)")
        for line in diff.rstrip("\n").splitlines():
            self.echo(f"        {line}")
        self._git("add", "--", self.spec.target_file)
        self._git("commit", "--quiet", "-m", self.spec.raw["commit_message"])
        return self._placeholder()

    def recreate(self, sha: str) -> str:
        self._git("commit", "--amend", "--no-edit", "--quiet",
                  env={"GIT_COMMITTER_DATE": f"<now, at least {sha}'s committer date + 1 s>"})
        new = self._placeholder()
        self.echo(f"[note]  {new}: same tree, parent, message and author as {sha}; new committer date")
        return new

    def revert(self, sha: str) -> str:
        self._git("revert", "--no-edit", sha)
        return "<revert>"

    def write_outputs(self, write: Callable[[], None], paths: list[str]) -> None:
        for path in paths:
            self.echo(f"[write] {path} (not written)")

    def _placeholder(self) -> str:
        self._commits += 1
        return f"<X{self._commits}>"


def _quote(arg: str) -> str:
    return f"'{arg}'" if (" " in arg or not arg) else arg


# --------------------------------------------------------------------------- #
# Simulated GitHub (dry run and tests)
# --------------------------------------------------------------------------- #

SMOKE_STEPS = ["Set up job", "Run actions/checkout@v5", "Run actions/setup-python@v6", "Install dependencies",
               "App imports on a clean machine", "Run smoke tests", "Complete job"]
DOCKER_STEPS = ["Set up job", "Run actions/checkout@v5", "Build image", "Complete job"]
ALL_TESTS = 13


class SimulatedGitHub:
    """Stands in for ``run_injection.GitHub``: every push that reaches main starts one run.

    Outcomes of injection pushes come from ``script`` in push order: ``F`` fails as the spec expects, ``F2``
    fails as expected but with a different error line, ``P`` is green, ``X`` fails some other way and
    ``none`` starts no run at all. Pushes of the parent take their outcomes from ``parent_script`` (default
    green); reverts are always green. ``echo`` prints each API request as the real client would send it.
    """

    def __init__(self, spec: Spec, names_to_keys: dict[str, str], script: list[str], *,
                 parent_script: list[str] | None = None, echo: Callable[[str], None] | None = None,
                 slug: str = "owner/repo", start: datetime = datetime(2026, 12, 1, 9, 0, tzinfo=UTC),
                 polls_in_progress: int = 0) -> None:
        self.spec = spec
        self.keys = names_to_keys
        self.script = list(script)
        self.parent_script = list(parent_script or [])
        self.echo = echo
        self.slug = slug
        self.clock = start
        self.polls_in_progress = polls_in_progress
        self.runs: dict[int, dict[str, Any]] = {}
        self.jobs_by_run: dict[int, list[dict[str, Any]]] = {}
        self.logs: dict[int, bytes] = {}
        self.outcomes: dict[int, str] = {}
        self._pending: dict[int, int] = {}
        self._next_run, self._next_job, self._run_number = 9_000_001, 70_000_001, 500
        self.pushes: list[tuple[str, str]] = []

    # -- runs appear on push --------------------------------------------------

    def seed_run(self, sha: str, outcome: str = "P") -> int:
        """A completed run that already exists before the case starts (HEAD's own run)."""
        run_id = self._create(sha, outcome)
        self._pending[run_id] = 0
        return run_id

    def on_push(self, sha: str, kind: str) -> None:
        self.pushes.append((sha, kind))
        if kind == "injection":
            if not self.script:
                raise AssertionError(f"simulated outcomes ran out at push {len(self.pushes)} ({sha})")
            outcome = self.script.pop(0)
        elif kind == "parent":
            outcome = self.parent_script.pop(0) if self.parent_script else "P"
        else:
            outcome = "P"
        if outcome != "none":
            self._create(sha, outcome)

    def _create(self, sha: str, outcome: str) -> int:
        run_id, self._next_run = self._next_run, self._next_run + 1
        self._run_number += 1
        created = self.clock
        self.clock += timedelta(minutes=4)
        jobs = self._jobs_for(run_id, outcome, created)
        conclusion = "success" if all(job["conclusion"] in ("success", "skipped") for job in jobs) else "failure"
        self.runs[run_id] = {
            "id": run_id, "name": "CI", "head_branch": ri.BRANCH, "head_sha": sha, "event": "push",
            "run_number": self._run_number, "run_attempt": 1, "status": "completed", "conclusion": conclusion,
            "created_at": _iso(created), "updated_at": _iso(created + timedelta(minutes=3)),
            "run_started_at": _iso(created), "html_url": f"https://github.com/{self.slug}/actions/runs/{run_id}",
        }
        self.jobs_by_run[run_id] = jobs
        self.outcomes[run_id] = outcome
        self._pending[run_id] = self.polls_in_progress
        return run_id

    def _jobs_for(self, run_id: int, outcome: str, created: datetime) -> list[dict[str, Any]]:
        spec = self.spec
        failing = {"F": spec.expected_failing_job, "F2": spec.expected_failing_job, "X": "smoke"}.get(outcome)
        jobs = []
        for display, key in self.keys.items():
            job_id, self._next_job = self._next_job, self._next_job + 1
            if failing is None:
                conclusion = "success"
            elif key == failing:
                conclusion = "failure"
            elif outcome in ("F", "F2") and key in spec.expected_skipped_jobs:
                conclusion = "skipped"
            elif outcome == "X" and key != "smoke":
                conclusion = "skipped"
            else:
                conclusion = "success"
            names = SMOKE_STEPS if key == "smoke" else DOCKER_STEPS
            failing_step = "Run smoke tests" if key == "smoke" else "Build image"
            steps = []
            for number, name in enumerate(names, start=1):
                if conclusion == "skipped":
                    step_conclusion = "skipped"
                elif conclusion == "failure" and name == failing_step:
                    step_conclusion = "failure"
                elif conclusion == "failure" and names.index(name) > names.index(failing_step) and name != "Complete job":
                    step_conclusion = "skipped"
                else:
                    step_conclusion = "success"
                steps.append({"name": name, "status": "completed", "conclusion": step_conclusion, "number": number})
            jobs.append({
                "id": job_id, "run_id": run_id, "name": display, "status": "completed", "conclusion": conclusion,
                "started_at": None if conclusion == "skipped" else _iso(created + timedelta(seconds=10)),
                "completed_at": None if conclusion == "skipped" else _iso(created + timedelta(minutes=3)),
                "steps": steps,
            })
            self.logs[job_id] = self._log_for(key, conclusion, outcome)
        return jobs

    def _log_for(self, key: str, conclusion: str, outcome: str) -> bytes:
        stamp = "2026-12-01T09:00:00.0000000Z "
        if conclusion == "skipped":
            return b""
        tests = sorted(self.spec.expected_failing_tests) if outcome in ("F", "F2") else []
        if outcome == "X" and key == "smoke":
            tests = ["tests/test_app.py::test_simulated_unexpected_failure"]
        lines = []
        if key == "smoke":
            if conclusion == "success":
                lines.append(f"{'=' * 20} {ALL_TESTS} passed in 9.41s {'=' * 20}")
            elif tests:
                lines += [f"FAILED {test} - AssertionError" for test in tests]
                lines.append(f"{'=' * 10} {len(tests)} failed, {ALL_TESTS - len(tests)} passed in 9.62s {'=' * 10}")
            else:
                lines.append(f"Simulated failure of {self.spec.id}: {self.spec.description}")
        elif conclusion != "success":
            lines.append(f"ERROR: simulated build failure of {self.spec.id}")
        if conclusion == "failure":
            if outcome == "F2":
                lines.append("A different error line than the first run")
            lines.append("##[error]Process completed with exit code 1.")
        return "".join(f"{stamp}{line}\n" for line in lines).encode("utf-8")

    # -- the client interface -------------------------------------------------

    def _say(self, request: str) -> None:
        if self.echo:
            self.echo(f"[api]   GET /repos/{self.slug}{request}")

    def runs_for_sha(self, workflow_file: str, sha: str) -> list[dict[str, Any]]:
        self._say(f"/actions/workflows/{workflow_file}/runs?head_sha={sha}&event=push&per_page=20")
        return [self._view(run_id) for run_id in sorted(self.runs, reverse=True) if self.runs[run_id]["head_sha"] == sha]

    def run(self, run_id: int) -> dict[str, Any]:
        self._say(f"/actions/runs/{run_id}")
        if self._pending.get(run_id, 0) > 0:
            self._pending[run_id] -= 1
        return self._view(run_id)

    def jobs(self, run_id: int) -> list[dict[str, Any]]:
        self._say(f"/actions/runs/{run_id}/jobs?per_page=100")
        return deepcopy(self.jobs_by_run[run_id])

    def job_log(self, job_id: int) -> bytes:
        self._say(f"/actions/jobs/{job_id}/logs")
        return self.logs[job_id]

    def _view(self, run_id: int) -> dict[str, Any]:
        run = deepcopy(self.runs[run_id])
        if self._pending.get(run_id, 0) > 0:
            run.update(status="in_progress", conclusion=None)
        return run

    # -- snapshot endpoints (for snapshot_api.py in tests) ---------------------

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if match := re.fullmatch(r"/actions/runs/(\d+)", path):
            return deepcopy(self.runs[int(match[1])])
        if match := re.fullmatch(r"/actions/runs/(\d+)/jobs", path):
            jobs = deepcopy(self.jobs_by_run[int(match[1])])
            return {"total_count": len(jobs), "jobs": jobs}
        if path == "/actions/runs":
            listed = [deepcopy(self.runs[r]) for r in sorted(self.runs, reverse=True)]
            page, per_page = (params or {}).get("page", 1), (params or {}).get("per_page", 100)
            return {"total_count": len(listed), "workflow_runs": listed[(page - 1) * per_page: page * per_page]}
        raise AssertionError(f"unexpected simulated GET {path}")


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# The case
# --------------------------------------------------------------------------- #


@dataclass
class ExtraRun:
    """A run of the case that is not in ``observed.runs``."""

    role: str  # decoy | discarded | parent | verification
    result: RunResult
    created_at: str
    attempt: int
    log_file: str | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "attempt": self.attempt,
            "run_id": self.result.run_id,
            "commit": self.result.sha[:7],
            "commit_full": self.result.sha,
            "created_at": self.created_at,
            "conclusion": self.result.conclusion,
            "outcome": self.result.outcome,
            "jobs": self.result.observed["jobs"],
            "failed": self.result.observed["failed"],
            "passed": self.result.observed["passed"],
            "log_file": self.log_file,
        }


class HoldoutCase:
    """One v1.1 case from preconditions to the pushed revert."""

    def __init__(self, spec: Spec, gh: Any, actions: GitActions, names_to_keys: dict[str, str], workflow: str,
                 state: RecoveryState, *, clock: Callable[[], datetime] = utc_now,
                 sleep: Callable[[float], None] | None = None, dry_run: bool = False,
                 echo: Callable[[str], None] = print) -> None:
        self.spec = spec
        self.gh = gh
        self.actions = actions
        self.keys = names_to_keys
        self.workflow = workflow
        self.state = state
        self.clock = clock
        self.sleep = sleep or ri.time.sleep
        self.dry_run = dry_run
        self.echo = echo
        self.mode = case_mode(spec)
        self.extras: list[ExtraRun] = []
        self.parent = ""
        self.index = 0
        self.attempt = 1
        self.signatures: dict[str, list[str]] = {}

    # -- small steps ----------------------------------------------------------

    def _step(self, text: str) -> None:
        if self.dry_run:
            self.echo(f"[step]  {text}")
        else:
            log(text)

    def push_and_observe(self, sha: str, *, label: str) -> tuple[RunResult, dict[str, Any]]:
        """Push ``sha`` (the branch tip) to main and observe the new run, ignoring runs seen before the push."""
        seen = frozenset(run["id"] for run in self.gh.runs_for_sha(self.workflow, sha))
        self._step(f"{label}: push {sha[:7] if not sha.startswith('<') else sha} to main"
                   + (f" (ignoring earlier runs {sorted(seen)})" if seen else ""))
        self.actions.push_branch(sha)
        self.state.remote_main = sha
        try:
            run = wait_for_run(self.gh, self.workflow, sha, exclude=seen)
        except Abort as exc:
            raise Abort(f"{label}: {exc}") from exc
        self.index += 1
        result = observe(self.gh, run, self.keys, self.index, sha)
        result.problems = compare(self.spec, result)
        self._step(f"{label}: run {result.run_id} outcome {result.outcome}")
        return result, run

    def force_back(self, *, label: str) -> None:
        """Force main back to the parent and wait for the new, green run on it (section 3, step 2)."""
        expected_remote = self.state.remote_main
        seen = frozenset(run["id"] for run in self.gh.runs_for_sha(self.workflow, self.parent))
        self._step(f"{label}: force main back to the parent {self.parent[:7]}")
        self.actions.force_main(self.parent, expected_remote)
        self.state.remote_main = self.parent
        try:
            run = wait_for_run(self.gh, self.workflow, self.parent, exclude=seen)
        except Abort as exc:
            raise Abort(f"{label}: no new run on the parent after the force-back: {exc}") from exc
        result = observe(self.gh, run, self.keys, 0, self.parent)
        result.problems = compare(self.spec, result)
        self.extras.append(ExtraRun("parent", result, run["created_at"], self.attempt))
        if not result.green:
            raise Abort(f"{label}: the run on the parent ({result.run_id}) is not green: {result.observed['jobs']}; "
                        "main is at the parent, which is not passing")
        self._step(f"{label}: parent run {result.run_id} is green")

    def extra(self, role: str, result: RunResult, run: dict[str, Any]) -> None:
        self.extras.append(ExtraRun(role, result, run["created_at"], self.attempt))

    def recreate(self, sha: str) -> str:
        self.attempt += 1
        if self.attempt > MAX_ATTEMPTS:
            raise Abort(f"{self.spec.id}: no usable commit in {MAX_ATTEMPTS} attempts (cap reached)")
        new = self.actions.recreate(sha)
        self.state.injection_sha = new
        return new

    def gate_a(self) -> None:
        if self.spec.id != GATED_CASE:
            return
        now = self.clock()
        if self.dry_run:
            try:
                gate_run_a(now)
                self.echo(f"[gate]  run A allowed now ({now:%H:%M} UTC, before 10:50)")
            except Abort as exc:
                self.echo(f"[gate]  WOULD REFUSE NOW: {exc}")
            return
        gate_run_a(now)

    def gate_b(self) -> None:
        if self.spec.id != GATED_CASE:
            return
        wait = gate_run_b_wait(self.clock())
        if self.dry_run:
            self.echo("[gate]  run B is not pushed before 11:00 UTC; the harness waits for 11:00 if needed")
            return
        if wait > timedelta(0):
            log(f"{GATED_CASE} gate: waiting {wait} for 11:00 UTC before run B")
            self.sleep(wait.total_seconds())
            if gate_run_b_wait(self.clock()) > timedelta(0):
                raise Abort(f"{GATED_CASE} gate: still before 11:00 UTC after waiting")

    def require(self, result: RunResult, what: str) -> None:
        if not result.matches:
            detail = "\n  - ".join(result.problems)
            raise Abort(f"{what} (run {result.run_id}) does not fail as the spec expects:\n  - {detail}\n"
                        "No record or logs were written.")

    # -- modes ----------------------------------------------------------------

    def run_single(self, x: str) -> tuple[list[RunResult], RunResult]:
        result, _ = self.push_and_observe(x, label="triaged run")
        self.require(result, "the triaged run")
        return [result], result

    def run_decoy(self, x: str) -> tuple[list[RunResult], RunResult]:
        decoy, run = self.push_and_observe(x, label="decoy attempt")
        self.require(decoy, "the decoy attempt")
        self.extra("decoy", decoy, run)
        self.force_back(label="decoy")
        x2 = self.recreate(x)
        result, _ = self.push_and_observe(x2, label="triaged run")
        self.require(result, "the triaged run")
        return [result], result

    def run_fail_same(self, x: str) -> tuple[list[RunResult], RunResult]:
        a, _ = self.push_and_observe(x, label="run A")
        self.require(a, "run A")
        self.force_back(label="rerun")
        b, _ = self.push_and_observe(x, label="run B")
        self.require(b, "run B")
        problems = same_failure_problems(a, b)
        self.signatures = {"run_a": error_signature(a.failing_log), "run_b": error_signature(b.failing_log)}
        if problems:
            detail = "\n  - ".join(problems)
            raise Abort(f"runs A ({a.run_id}) and B ({b.run_id}) do not fail the same way:\n  - {detail}\n"
                        "No record or logs were written.")
        return [a, b], b

    def run_flaky_rerun(self, x: str) -> tuple[list[RunResult], RunResult]:
        while True:
            self.gate_a()
            a, run_a = self.push_and_observe(x, label=f"attempt {self.attempt}, run A")
            if a.outcome == "X":
                self.require(a, "run A")
            if a.outcome == "F":
                self.extra("discarded", a, run_a)
                self.force_back(label=f"attempt {self.attempt} discarded (run A failed)")
                x = self.recreate(x)
                continue
            self.force_back(label=f"attempt {self.attempt}, rerun")
            self.gate_b()
            b, run_b = self.push_and_observe(x, label=f"attempt {self.attempt}, run B")
            if b.outcome == "F":
                return [a, b], b
            if b.outcome == "X":
                self.require(b, "run B")
            self.extra("discarded", a, run_a)
            self.extra("discarded", b, run_b)
            self.force_back(label=f"attempt {self.attempt} discarded (run B passed)")
            x = self.recreate(x)

    def run_flaky(self, x: str) -> tuple[list[RunResult], RunResult]:
        while True:
            result, run = self.push_and_observe(x, label=f"attempt {self.attempt}")
            if result.outcome == "F":
                break
            if result.outcome == "X":
                self.require(result, f"attempt {self.attempt}")
            self.extra("discarded", result, run)
            self.force_back(label=f"attempt {self.attempt} discarded (passed)")
            x = self.recreate(x)
        if not any(extra.role == "discarded" and extra.result.green for extra in self.extras):
            self.verify_green(x)
        return [result], result

    def verify_green(self, x: str) -> None:
        """Verification runs after the cutoff until one passes (section 3)."""
        for number in range(1, MAX_VERIFICATION_RUNS + 1):
            self.force_back(label=f"verification {number}")
            result, run = self.push_and_observe(x, label=f"verification run {number}")
            if result.outcome == "X":
                self.require(result, f"verification run {number}")
            self.extra("verification", result, run)
            if result.green:
                return
        raise Abort(f"no verification run passed in {MAX_VERIFICATION_RUNS}; the flaky label has no green run. "
                    "No record or logs were written.")

    # -- whole case -----------------------------------------------------------

    def preconditions(self, wait_for_green_s: int) -> str:
        spec = self.spec
        record_path = ri.INJECTIONS_DIR / f"{spec.id}.json"
        if record_path.exists():
            raise Abort(f"{ri.rel(record_path)} already exists; refusing to overwrite a record")
        existing = sorted(ri.LOGS_DIR.glob(f"{spec.id}.*log")) + sorted(ri.LOGS_DIR.glob(f"extra-{spec.id}-*.log"))
        if existing:
            raise Abort(f"logs for {spec.id} already exist: {[ri.rel(p) for p in existing]}")
        if ri.is_git_ignored(ri.log_path(spec, 1, representative=True)):
            raise Abort(f"{ri.rel(ri.LOGS_DIR)} is git-ignored; saved logs would never be committed")
        dirty = self.actions.read("status", "--porcelain", "--untracked-files=no")
        if dirty:
            raise Abort(f"working tree is not clean:\n{dirty}")
        current = self.actions.read("rev-parse", "--abbrev-ref", "HEAD")
        if current != ri.BRANCH:
            raise Abort(f"must be on {ri.BRANCH!r}, currently on {current!r}")
        if self.actions.read("branch", "--list", self.actions.branch):
            raise Abort(f"local branch {self.actions.branch} already exists; delete it or finish the earlier attempt")
        self.actions.fetch()
        head = self.actions.read("rev-parse", "HEAD")
        remote_head = self.actions.read("rev-parse", f"{ri.REMOTE}/{ri.BRANCH}")
        if head != remote_head:
            raise Abort(f"HEAD {head[:7]} differs from {ri.REMOTE}/{ri.BRANCH} {remote_head[:7]}; push or pull first")
        case_specific_checks(spec, self.actions)
        run = latest_run(self.gh.runs_for_sha(self.workflow, head))
        if run is None or run["status"] != "completed":
            if wait_for_green_s <= 0:
                raise Abort(f"HEAD {head[:7]} has no completed CI run; not waiting (--wait-for-green 0)")
            run = wait_for_run(self.gh, self.workflow, head, wait_for_green_s)
        if run["conclusion"] != "success":
            raise Abort(f"HEAD {head[:7]} is not green: run {run['id']} concluded {run['conclusion']!r}")
        self._step(f"HEAD {head[:7]} is clean, in sync and green (run {run['id']}); mode {self.mode}")
        return head

    def run(self, wait_for_green_s: int) -> int:
        self.parent = self.preconditions(wait_for_green_s)
        state = self.state
        state.branch, state.parent_sha, state.target_file = self.actions.branch, self.parent, self.spec.target_file
        self.actions.start_branch()
        state.on_branch = True
        x = self.actions.commit_injection()
        state.injection_sha = x
        self._step(f"committed {x[:7] if not x.startswith('<') else x} on {self.actions.branch}: "
                   f"{self.spec.raw['commit_message']!r}")

        flows = {"single": self.run_single, "decoy": self.run_decoy, "fail_same": self.run_fail_same,
                 "flaky_rerun": self.run_flaky_rerun, "flaky": self.run_flaky}
        results, triaged = flows[self.mode](x)
        problems = judge_holdout(self.spec, self.mode, results, triaged, self.extras)
        if problems:
            raise Abort("observed runs do not support the expected label:\n  - " + "\n  - ".join(problems)
                        + "\nNo record or logs were written.")
        self.write(results, triaged)

        final = state.injection_sha
        revert = self.actions.revert(final)
        state.revert_committed = True
        self._step(f"revert {final[:7] if not final.startswith('<') else final} and push it")
        self.actions.push_branch(revert, kind="revert")
        state.revert_pushed = True
        state.remote_main = revert
        self.actions.finish()
        state.on_branch = False
        outcomes = "".join(r.outcome for r in results)
        extras = ",".join(f"{e.role}:{e.result.outcome}" for e in self.extras) or "none"
        self.echo(f"{self.spec.id} {self.spec.category} mode={self.mode} outcomes={outcomes} "
                  f"extras={extras} attempts={self.attempt} match=Y" + (" (DRY RUN: nothing was done)" if self.dry_run else ""))
        return 0

    def write(self, results: list[RunResult], triaged: RunResult) -> None:
        spec = self.spec
        counters: dict[str, int] = {}
        for extra in self.extras:
            counters[extra.role] = counters.get(extra.role, 0) + 1
            if extra.result.failing_log is not None:
                extra.log_file = ri.rel(ri.LOGS_DIR / f"extra-{spec.id}-{extra.role}{counters[extra.role]}"
                                                      f"-run{extra.result.run_id}.log")
        paths = [ri.rel(ri.log_path(spec, r.index, representative=r is triaged)) for r in results if r.failing_log]
        paths += [e.log_file for e in self.extras if e.log_file]
        paths.append(ri.rel(ri.INJECTIONS_DIR / f"{spec.id}.json"))

        def write_all() -> None:
            logs = ri.save_logs(spec, results, triaged)
            for extra in self.extras:
                if extra.log_file:
                    (ri.REPO_ROOT / extra.log_file).write_bytes(extra.result.failing_log)
            path = ri.write_record(spec, results, triaged, logs)
            record = json.loads(path.read_text(encoding="utf-8"))
            record["holdout"] = {
                "version": "1.1",
                "family": spec.raw["family"],
                "mode": self.mode,
                "branch": spec.raw["branch"],
                "commit_message": spec.raw["commit_message"],
                "same_commit_rerun": spec.raw["same_commit_rerun"],
                "decoy": spec.id in DECOY_CASES,
                "parent_commit": self.parent,
                "injection_commit_full": self.state.injection_sha,
                "attempts": self.attempt,
                "same_commit_signatures": self.signatures or None,
                "extra_runs": [extra.as_record() for extra in self.extras],
            }
            path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        self.actions.write_outputs(write_all, paths)


def judge_holdout(spec: Spec, mode: str, results: list[RunResult], triaged: RunResult,
                  extras: list[ExtraRun]) -> list[str]:
    """Final check of everything recorded for a v1.1 case (the flows already enforce it run by run)."""
    problems = []
    if not triaged.matches:
        problems.append(f"triaged run {triaged.run_id} does not fail as expected: {triaged.problems}")
    if mode == "fail_same" and [r.outcome for r in results] != ["F", "F"]:
        problems.append(f"fail_same needs runs A and B both F, got {[r.outcome for r in results]}")
    if mode == "flaky_rerun" and [r.outcome for r in results] != ["P", "F"]:
        problems.append(f"flaky rerun needs run A green and run B F, got {[r.outcome for r in results]}")
    if mode in ("fail_same", "flaky_rerun") and len({r.sha for r in results}) != 1:
        problems.append("runs A and B are not on the same commit")
    if mode in ("single", "decoy", "flaky") and len(results) != 1:
        problems.append(f"{mode} records exactly one run in observed.runs, got {len(results)}")
    if spec.category == FLAKY:
        green = [r for r in results if r.outcome == "P"] + [e.result for e in extras
                                                            if e.role in ("discarded", "verification") and e.result.green]
        if not green:
            problems.append("flaky label needs a green run of the same code; none observed")
    if mode == "decoy" and [e.role for e in extras if e.role == "decoy"] != ["decoy"]:
        problems.append("decoy case needs exactly one decoy attempt")
    for extra in extras:
        if extra.role == "decoy" and not extra.result.matches:
            problems.append(f"decoy run {extra.result.run_id} does not fail as expected")
        if extra.role == "parent" and not extra.result.green:
            problems.append(f"parent run {extra.result.run_id} is not green")
        if extra.result.outcome == "X" and extra.role != "parent":
            problems.append(f"{extra.role} run {extra.result.run_id} failed some other way")
    return problems


# --------------------------------------------------------------------------- #
# Entry point from run_injection.run
# --------------------------------------------------------------------------- #

DEFAULT_SCRIPTS = {
    "single": ["F"],
    "decoy": ["F", "F"],
    "fail_same": ["F", "F"],
    "flaky_rerun": ["P", "F"],
    "flaky": ["P", "F"],
}


def run_holdout(spec: Spec, args: Any, state: RecoveryState) -> int:
    """Run (or, with ``--dry-run``, print) one v1.1 case."""
    check_holdout_spec(spec)
    if args.wait_for_green < 0:
        raise Abort("--wait-for-green must be >= 0")
    workflow_path = ri.REPO_ROOT / ".github" / "workflows" / args.workflow
    names_to_keys = ri.workflow_jobs(workflow_path)
    mode = case_mode(spec)

    if args.dry_run:
        script = [s.strip() for s in args.simulate.split(",")] if args.simulate else DEFAULT_SCRIPTS[mode]
        echo = print
        order = run_order()
        echo(f"DRY RUN {spec.id} [{spec.category}] mode={mode}, slot {order.index(spec.id) + 1}/30 of the run order; "
             f"branch {spec.raw['branch']}; simulated injection outcomes {','.join(script)}")
        echo("Nothing below is executed: [git] commands are printed, [api] requests go to a simulated GitHub, "
             "[write] files are not written. Reads of the local repository ([step] checks) are real.")
        try:
            slug = ri.repo_slug()
        except Abort:
            slug = "owner/repo"
        gh = SimulatedGitHub(spec, names_to_keys, script, echo=echo, slug=slug)
        actions: GitActions = DryRunActions(spec, echo)
        actions.push_listeners.append(gh.on_push)
        head = ri.git("rev-parse", "HEAD")
        gh.seed_run(head)
        dry_state = RecoveryState()
        case = HoldoutCase(spec, gh, actions, names_to_keys, args.workflow, dry_state, dry_run=True, echo=echo)
        return case.run(args.wait_for_green * 60)

    if args.simulate:
        raise Abort("--simulate is only for --dry-run")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise Abort("GITHUB_TOKEN environment variable is not set")
    gh = ri.GitHub(token, ri.repo_slug())
    case = HoldoutCase(spec, gh, GitActions(spec), names_to_keys, args.workflow, state)
    return case.run(args.wait_for_green * 60)
