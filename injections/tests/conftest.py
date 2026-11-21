"""Fixtures for the lab-tooling tests (not the app's tests: CI runs ``pytest tests`` only).

Run from the repository root::

    python -m pytest injections/tests -q

Nothing here touches the network or the real repository: each test works in a temporary git repository
cloned from a temporary bare "origin", built from ``git archive HEAD`` of this lab, with GitHub simulated by
``holdout_runner.SimulatedGitHub``.
"""

from __future__ import annotations

import io
import shlex
import subprocess
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

INJECTIONS = Path(__file__).resolve().parents[1]
LAB = INJECTIONS.parent
sys.path[:0] = [str(INJECTIONS), str(INJECTIONS / "maintenance")]
sys.dont_write_bytecode = True

import build_index  # noqa: E402
import holdout_runner as hr  # noqa: E402
import run_injection as ri  # noqa: E402

ARCHIVED = [".github", ".gitignore", ".dockerignore", "Dockerfile", "api", "tests", "requirements.txt", "pytest.ini",
            "stats.py", "injections"]


def git(cwd: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed in {cwd}:\n{result.stderr}")
    return result.stdout.strip()


def configure(repo: Path) -> None:
    git(repo, "config", "user.name", "Lab Test")
    git(repo, "config", "user.email", "lab-test@example.invalid")
    git(repo, "config", "core.autocrlf", "false")


@pytest.fixture(scope="session")
def origin(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A bare repository holding one commit: the lab at HEAD (code, specs, v1.0 records, logs, API snapshots)."""
    root = tmp_path_factory.mktemp("lab-origin")
    template = root / "template"
    template.mkdir()
    git(template, "init", "--quiet", "-b", "main")
    configure(template)
    archive = subprocess.run(["git", "-c", "core.autocrlf=false", "archive", "--format=tar", "HEAD", *ARCHIVED],
                             cwd=LAB, capture_output=True, check=True).stdout
    tarfile.open(fileobj=io.BytesIO(archive)).extractall(template)
    git(template, "add", "-A")
    git(template, "commit", "--quiet", "-m", "lab baseline")
    bare = root / "origin.git"
    git(root, "clone", "--quiet", "--bare", str(template), str(bare))
    return bare


class FakeTime:
    """Replaces ``run_injection.time`` and the runner's clock: sleeping advances both instantly."""

    def __init__(self, start: datetime) -> None:
        self.now_value = start
        self.mono = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.mono

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.mono += seconds
        self.now_value += timedelta(seconds=seconds)

    def now(self) -> datetime:
        return self.now_value


class Lab:
    """A clone of ``origin`` that the harness modules are pointed at."""

    def __init__(self, root: Path, bare: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root, self.bare = root, bare
        self.monkeypatch = monkeypatch
        self.time = FakeTime(datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc))
        monkeypatch.setattr(ri, "REPO_ROOT", root)
        monkeypatch.setattr(ri, "INJECTIONS_DIR", root / "injections")
        monkeypatch.setattr(ri, "LOGS_DIR", root / "injections" / "logs")
        monkeypatch.setattr(ri, "time", self.time)
        self.names_to_keys = ri.workflow_jobs(root / ".github" / "workflows" / "ci.yml")
        self.gh: hr.SimulatedGitHub | None = None

    def git(self, *args: str, check: bool = True) -> str:
        return git(self.root, *args, check=check)

    def spec(self, case: str) -> ri.Spec:
        return ri.load_spec(self.root / "injections" / "specs" / f"{case}.json")

    def case(self, case_id: str, script: list[str], *, parent_script: list[str] | None = None,
             polls: int = 0) -> tuple[hr.HoldoutCase, ri.RecoveryState]:
        """A HoldoutCase on real git, with one SimulatedGitHub kept across cases (so run ids and numbers grow)."""
        spec = self.spec(case_id)
        if self.gh is None:
            self.gh = hr.SimulatedGitHub(spec, self.names_to_keys, [], polls_in_progress=polls)
            self.gh.seed_run(self.git("rev-parse", "HEAD"))
        self.gh.spec, self.gh.script = spec, list(script)
        self.gh.parent_script = list(parent_script or [])
        self.gh.polls_in_progress = polls
        actions = hr.GitActions(spec)
        actions.push_listeners.append(self.gh.on_push)
        state = ri.RecoveryState()
        case = hr.HoldoutCase(spec, self.gh, actions, self.names_to_keys, "ci.yml", state,
                              clock=self.time.now, sleep=self.time.sleep, echo=lambda line: None)
        return case, state

    def remote_main(self) -> str:
        return git(self.bare, "rev-parse", "refs/heads/main")

    def tree(self, ref: str, repo: Path | None = None) -> str:
        return git(repo or self.root, "rev-parse", f"{ref}^{{tree}}")

    def run_instructions(self, commands: list[str]) -> None:
        """Execute recovery commands exactly as printed; a ``# must print X`` comment is checked."""
        for command in commands:
            line, _, comment = command.partition("#")
            output = git(self.root, *shlex.split(line)[1:])
            if "must print" in comment:
                expected = comment.split("must print", 1)[1].split(";")[0].strip()
                assert output == expected, f"{line.strip()} printed {output}, expected {expected}"


@pytest.fixture
def lab(origin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Lab:
    bare = tmp_path / "origin.git"  # per test, so one test's pushes never reach another
    git(tmp_path, "clone", "--quiet", "--bare", str(origin), str(bare))
    work = tmp_path / "work"
    git(tmp_path, "clone", "--quiet", "-c", "core.autocrlf=false", str(bare), str(work))
    configure(work)
    return Lab(work, bare, monkeypatch)
