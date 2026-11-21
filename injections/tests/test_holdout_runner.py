"""The v1.1 holdout runner: every mode, every abort, recovery from every force-pushed state, and v1.0 untouched."""

from __future__ import annotations

import argparse
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import build_index
import holdout_runner as hr
import run_injection as ri
from conftest import LAB, Lab

UTC = timezone.utc
PLAN_RUN_ORDER = [  # holdout_plan.md section 4, read column by column
    "inj-090", "inj-062", "inj-072", "inj-089", "inj-085", "inj-071", "inj-061", "inj-088", "inj-069", "inj-076",
    "inj-063", "inj-087", "inj-066", "inj-068", "inj-080", "inj-086", "inj-078", "inj-082", "inj-081", "inj-064",
    "inj-074", "inj-065", "inj-083", "inj-067", "inj-084", "inj-070", "inj-073", "inj-077", "inj-079", "inj-075",
]


def args(spec: str, **overrides: object) -> argparse.Namespace:
    values = {"spec": spec, "dry_run": False, "workflow": "ci.yml", "runs": None, "simulate": None,
              "wait_for_green": 15}
    values.update(overrides)
    return argparse.Namespace(**values)


def assert_restored(lab: Lab, parent: str) -> None:
    """main (remote and local) has the parent's tree, the working tree is clean, the branch is gone."""
    assert lab.tree(lab.remote_main(), lab.bare) == lab.tree(parent)
    assert lab.git("rev-parse", "HEAD") == lab.remote_main()
    assert lab.git("rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert lab.git("status", "--porcelain", "--untracked-files=no") == ""
    assert not [b for b in lab.git("branch", "--list", "wip-*").split() if b != "*"]


def remote_branches(lab: Lab) -> list[str]:
    return lab.git("ls-remote", "--heads", "origin").split()[1::2]


# --------------------------------------------------------------------------- #
# Draws and spec checks
# --------------------------------------------------------------------------- #


def test_run_order_and_decoy_draw_reproduce_from_the_seed() -> None:
    assert hr.run_order() == PLAN_RUN_ORDER
    specs = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (LAB / "injections" / "specs").glob("inj-*.json")}
    r = random.Random(hr.SEED + 4)
    drawn = set()
    for category in hr.DECOY_CATEGORIES:
        pool = [case for case in sorted(specs) if hr.is_holdout(case) and specs[case]["category"] == category
                and specs[case]["same_commit_rerun"] is None]
        assert len(pool) == 3
        drawn |= set(r.sample(pool, 2))
    assert drawn == hr.DECOY_CASES


def test_every_holdout_spec_passes_the_v11_checks_and_build_index_agrees_on_the_mode() -> None:
    modes = {}
    for case in PLAN_RUN_ORDER:
        spec = ri.load_spec(LAB / "injections" / "specs" / f"{case}.json")
        hr.check_holdout_spec(spec)
        modes[case] = hr.case_mode(spec)
        assert build_index.planned_mode(spec.raw) == modes[case]
    counts = {mode: sum(1 for m in modes.values() if m == mode) for mode in set(modes.values())}
    assert counts == {"fail_same": 10, "flaky_rerun": 2, "flaky": 3, "decoy": 10, "single": 5}


def test_runs_option_is_refused_for_holdout_ids(lab: Lab) -> None:
    with pytest.raises(ri.Abort, match="--runs .* not used for v1.1"):
        ri.run(args(str(lab.root / "injections/specs/inj-063.json"), runs=1), ri.RecoveryState())


# --------------------------------------------------------------------------- #
# wait_for_run
# --------------------------------------------------------------------------- #


class ListingOnly:
    def __init__(self, runs: list[dict]) -> None:
        self.runs = runs

    def runs_for_sha(self, workflow: str, sha: str) -> list[dict]:
        return [r for r in self.runs if r["head_sha"] == sha]

    def run(self, run_id: int) -> dict:
        return next(r for r in self.runs if r["id"] == run_id)


def completed(run_id: int, created: str) -> dict:
    return {"id": run_id, "head_sha": "abc", "created_at": created, "status": "completed", "conclusion": "failure",
            "html_url": f"u/{run_id}"}


def test_wait_for_run_ignores_runs_already_seen(lab: Lab) -> None:
    a = completed(1, "2026-12-01T10:00:00Z")
    gh = ListingOnly([a])
    assert ri.wait_for_run(gh, "ci.yml", "abc")["id"] == 1  # v1.0 behaviour: the latest run
    with pytest.raises(ri.Abort, match="no CI run was created"):
        ri.wait_for_run(gh, "ci.yml", "abc", exclude=frozenset({1}))  # run B never appears
    gh.runs.append(completed(2, "2026-12-01T10:09:00Z"))
    assert ri.wait_for_run(gh, "ci.yml", "abc", exclude=frozenset({1}))["id"] == 2


# --------------------------------------------------------------------------- #
# Modes, happy paths
# --------------------------------------------------------------------------- #


def test_single_case_commits_on_its_branch_and_reverts(lab: Lab) -> None:
    case, state = lab.case("inj-063", ["F"])
    parent = lab.git("rev-parse", "HEAD")
    assert case.run(0) == 0
    record = json.loads((lab.root / "injections/inj-063.json").read_text(encoding="utf-8"))
    injection = record["holdout"]["injection_commit_full"]
    assert lab.git("log", "-1", "--format=%s", injection) == "Update api/main.py"
    assert lab.git("log", "-1", "--format=%s", "origin/main") == 'Revert "Update api/main.py"'
    assert [run["commit"] for run in record["observed"]["runs"]] == [injection[:7]]
    assert record["holdout"]["extra_runs"] == []
    assert remote_branches(lab) == ["refs/heads/main"]
    assert_restored(lab, parent)


def test_decoy_records_the_decoy_attempt_and_triages_the_recreated_commit(lab: Lab) -> None:
    case, state = lab.case("inj-090", ["F", "F"])
    parent = lab.git("rev-parse", "HEAD")
    assert case.run(0) == 0
    record = json.loads((lab.root / "injections/inj-090.json").read_text(encoding="utf-8"))
    holdout = record["holdout"]
    decoy, parent_run = holdout["extra_runs"]
    assert (decoy["role"], decoy["outcome"], parent_run["role"], parent_run["outcome"]) == ("decoy", "F", "parent", "P")
    x, x2 = decoy["commit_full"], holdout["injection_commit_full"]
    assert x != x2 and lab.tree(x) == lab.tree(x2)
    assert lab.git("rev-parse", f"{x}~1") == lab.git("rev-parse", f"{x2}~1") == parent
    assert lab.git("merge-base", "--is-ancestor", x2, "origin/main", check=False) == ""
    assert x not in lab.git("rev-list", "origin/main").split()
    assert record["commit"] == x2[:7] and record["observed"]["runs"][0]["commit"] == x2[:7]
    assert decoy["log_file"] == f"injections/logs/extra-inj-090-decoy1-run{decoy['run_id']}.log"
    assert (lab.root / decoy["log_file"]).read_bytes()
    assert_restored(lab, parent)


def test_fail_same_records_runs_a_and_b_on_one_commit(lab: Lab) -> None:
    case, state = lab.case("inj-062", ["F", "F"], polls=2)
    parent = lab.git("rev-parse", "HEAD")
    assert case.run(0) == 0
    record = json.loads((lab.root / "injections/inj-062.json").read_text(encoding="utf-8"))
    a, b = record["observed"]["runs"]
    assert a["commit"] == b["commit"] and a["run_id"] != b["run_id"]
    assert record["run_id"] == b["run_id"] and record["log_file"] == "injections/logs/inj-062.log"
    assert a["log_file"] == "injections/logs/inj-062.run1.log"
    assert [run["role"] for run in record["holdout"]["extra_runs"]] == ["parent"]
    sigs = record["holdout"]["same_commit_signatures"]
    assert sigs["run_a"] == sigs["run_b"] and sigs["run_a"]
    assert lab.gh.pushes == [(lab.gh.runs[int(a["run_id"])]["head_sha"], "injection"), (parent, "parent"),
                             (lab.gh.runs[int(a["run_id"])]["head_sha"], "injection"),
                             (lab.remote_main(), "revert")]
    assert_restored(lab, parent)


def test_flaky_discards_a_passing_attempt(lab: Lab) -> None:
    case, state = lab.case("inj-069", ["P", "F"])
    parent = lab.git("rev-parse", "HEAD")
    assert case.run(0) == 0
    record = json.loads((lab.root / "injections/inj-069.json").read_text(encoding="utf-8"))
    roles = [(run["role"], run["outcome"]) for run in record["holdout"]["extra_runs"]]
    assert roles == [("discarded", "P"), ("parent", "P")]
    discarded = record["holdout"]["extra_runs"][0]["commit_full"]
    assert discarded != record["holdout"]["injection_commit_full"]
    assert discarded not in lab.git("rev-list", "origin/main").split()
    assert len(record["observed"]["runs"]) == 1 and record["holdout"]["attempts"] == 2
    assert_restored(lab, parent)


def test_flaky_failing_at_once_gets_verification_runs_after_the_cutoff(lab: Lab) -> None:
    case, state = lab.case("inj-069", ["F", "F", "P"])
    parent = lab.git("rev-parse", "HEAD")
    assert case.run(0) == 0
    record = json.loads((lab.root / "injections/inj-069.json").read_text(encoding="utf-8"))
    extras = record["holdout"]["extra_runs"]
    assert [(run["role"], run["outcome"]) for run in extras] == [
        ("parent", "P"), ("verification", "F"), ("parent", "P"), ("verification", "P")]
    triaged = lab.gh.runs[int(record["run_id"])]
    for run in extras:
        assert lab.gh.runs[int(run["run_id"])]["created_at"] > triaged["created_at"]
    assert {run["commit_full"] for run in extras if run["role"] == "verification"} == {triaged["head_sha"]}
    assert extras[1]["log_file"] == f"injections/logs/extra-inj-069-verification1-run{extras[1]['run_id']}.log"
    assert_restored(lab, parent)


def test_flaky_rerun_discards_a_commit_whose_run_a_failed(lab: Lab) -> None:
    case, state = lab.case("inj-068", ["F", "P", "F"])
    parent = lab.git("rev-parse", "HEAD")
    assert case.run(0) == 0
    record = json.loads((lab.root / "injections/inj-068.json").read_text(encoding="utf-8"))
    a, b = record["observed"]["runs"]
    assert (a["conclusion"], b["conclusion"]) == ("success", "failure") and a["commit"] == b["commit"]
    assert [(r["role"], r["outcome"]) for r in record["holdout"]["extra_runs"]] == [
        ("discarded", "F"), ("parent", "P"), ("parent", "P")]
    assert_restored(lab, parent)


def test_inj070_gate_waits_for_1100_before_run_b(lab: Lab) -> None:
    lab.time.now_value = datetime(2026, 12, 1, 10, 35, tzinfo=UTC)
    case, state = lab.case("inj-070", ["P", "F"])
    assert case.run(0) == 0
    assert lab.time.slept and lab.time.now() >= datetime(2026, 12, 1, 11, 0, tzinfo=UTC)


def test_gate_rules() -> None:
    hr.gate_run_a(datetime(2026, 12, 1, 10, 49, tzinfo=UTC))
    with pytest.raises(ri.Abort, match="before 10:50"):
        hr.gate_run_a(datetime(2026, 12, 1, 10, 50, tzinfo=UTC))
    assert hr.gate_run_b_wait(datetime(2026, 12, 1, 10, 52, tzinfo=UTC)).total_seconds() == 8 * 60
    assert hr.gate_run_b_wait(datetime(2026, 12, 1, 11, 0, tzinfo=UTC)).total_seconds() == 0
    with pytest.raises(ri.Abort, match="outside NZ daylight time"):
        hr.gate_run_a(datetime(2027, 4, 5, 9, 0, tzinfo=UTC))


def test_error_signature_masks_noise_but_not_the_message() -> None:
    a = (b"2026-12-01T10:00:00.1234567Z /home/runner/work/_temp/0b2c3d4e-1111-2222-3333-444455556666.sh: line 1: "
         b"SMOKE_TEST_PATHS: SMOKE_TEST_PATHS is not set\n2026-12-01T10:00:00.2Z ##[error]Process completed with exit code 1.\n")
    b = a.replace(b"0b2c3d4e-1111", b"99999999-aaaa").replace(b"10:00:00", b"11:42:07")
    c = a.replace(b"SMOKE_TEST_PATHS is not set", b"something else")
    assert hr.error_signature(a) == hr.error_signature(b) != hr.error_signature(c)
    assert hr.error_signature(b"no error line\n") == []


# --------------------------------------------------------------------------- #
# Aborts and recovery from every state
# --------------------------------------------------------------------------- #

RECOVERY_CASES = [
    # (case, script, parent_script, abort message, where main is left)
    ("inj-062", ["F", "none"], None, "run B: no CI run was created", "injection"),
    ("inj-062", ["F", "F2"], None, "do not fail the same way", "injection"),
    ("inj-062", ["F"], ["none"], "no new run on the parent", "parent"),
    ("inj-062", ["F"], ["X"], "is not green", "parent"),
    ("inj-062", ["X"], None, "run A .* does not fail as the spec expects", "injection"),
    ("inj-090", ["F"], ["none"], "decoy: no new run on the parent", "parent"),
    ("inj-090", ["P"], None, "decoy attempt .* does not fail", "injection"),
    ("inj-069", ["P"] * hr.MAX_ATTEMPTS, None, "no usable commit in 10 attempts", "parent"),
    ("inj-069", ["F", "X"], None, "verification run 1 .* does not fail", "injection"),
    ("inj-069", ["F"] + ["F"] * hr.MAX_VERIFICATION_RUNS, None, "no verification run passed", "injection"),
    ("inj-068", ["P", "P"] * hr.MAX_ATTEMPTS, None, "no usable commit in 10 attempts", "parent"),
]


@pytest.mark.parametrize("case_id,script,parent_script,message,left_at", RECOVERY_CASES,
                         ids=[f"{c[0]}-{c[3][:28]}" for c in RECOVERY_CASES])
def test_abort_and_recovery(lab: Lab, case_id: str, script: list[str], parent_script: list[str] | None,
                            message: str, left_at: str) -> None:
    case, state = lab.case(case_id, script, parent_script=parent_script)
    parent = lab.git("rev-parse", "HEAD")
    with pytest.raises(ri.Abort, match=message):
        case.run(0)
    assert not (lab.root / f"injections/{case_id}.json").exists()
    assert not list((lab.root / "injections/logs").glob(f"*{case_id}*"))
    if left_at == "parent":
        assert lab.remote_main() == parent and not state.main_broken()
        assert "no revert is needed" in state.holdout_headline()
    else:
        assert lab.remote_main() == state.injection_sha and state.main_broken()
        assert "BROKEN" in state.holdout_headline()
    commands = state.instructions()
    assert any("revert" in c for c in commands) == (left_at == "injection")
    lab.run_instructions(commands)
    assert_restored(lab, parent)


def test_recovery_when_the_revert_push_fails(lab: Lab) -> None:
    case, state = lab.case("inj-063", ["F"])
    parent = lab.git("rev-parse", "HEAD")
    push = case.actions.push_branch

    def failing_revert_push(sha: str, kind: str = "injection") -> None:
        if kind == "revert":
            raise ri.Abort("simulated push failure")
        push(sha, kind)

    case.actions.push_branch = failing_revert_push
    with pytest.raises(ri.Abort, match="simulated push failure"):
        case.run(0)
    assert state.revert_committed and not state.revert_pushed and state.main_broken()
    commands = state.instructions()
    assert f"git push origin {state.branch}:main" in commands and not any("git revert" in c for c in commands)
    lab.run_instructions(commands)
    assert_restored(lab, parent)


def test_recovery_when_the_gate_refuses_after_the_local_commit(lab: Lab) -> None:
    lab.time.now_value = datetime(2026, 12, 1, 10, 55, tzinfo=UTC)
    case, state = lab.case("inj-070", ["P", "F"])
    parent = lab.git("rev-parse", "HEAD")
    with pytest.raises(ri.Abort, match="before 10:50"):
        case.run(0)
    assert lab.remote_main() == parent and lab.gh.pushes == []
    lab.run_instructions(state.instructions())
    assert_restored(lab, parent)


def test_print_recovery_for_holdout_names_the_state(lab: Lab, capsys: pytest.CaptureFixture[str]) -> None:
    state = ri.RecoveryState(branch="wip-91d930", parent_sha="p" * 40, injection_sha="x" * 40,
                             remote_main="x" * 40, on_branch=True)
    ri.print_recovery(state)
    err = capsys.readouterr().err
    assert "main is BROKEN" in err and f"git revert --no-edit {'x' * 40}" in err and "git branch -D wip-91d930" in err


# --------------------------------------------------------------------------- #
# v1.0 paths are unchanged
# --------------------------------------------------------------------------- #


def test_v10_recovery_instructions_are_unchanged() -> None:
    assert ri.RecoveryState().instructions() == []
    pushed = ri.RecoveryState(injection_sha="abc", injection_pushed=True)
    assert pushed.instructions() == ["git checkout main", "git pull --ff-only origin main",
                                     "git revert --no-edit abc", "git push origin main"]
    pushed.revert_committed = True
    assert pushed.instructions() == ["git push origin main"]
    pushed.revert_pushed = True
    assert pushed.instructions() == []


def test_v10_spec_keeps_its_commit_message_and_cli_path(lab: Lab, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = lab.spec("inj-002")
    assert spec.commit_message == f"inject: inj-002 {spec.description}"
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(ri.Abort, match="GITHUB_TOKEN"):  # v1.0 path reached, as before
        ri.run(args(str(lab.root / "injections/specs/inj-002.json"), runs=None), ri.RecoveryState())
    with pytest.raises(ri.Abort, match="--simulate is only for v1.1"):
        ri.run(args(str(lab.root / "injections/specs/inj-002.json"), simulate="F"), ri.RecoveryState())


# --------------------------------------------------------------------------- #
# Dry run
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case_id,simulate", [("inj-090", None), ("inj-062", None), ("inj-069", None),
                                              ("inj-069", "F,P"), ("inj-070", None), ("inj-079", None)])
def test_dry_run_changes_nothing(lab: Lab, capsys: pytest.CaptureFixture[str], case_id: str,
                                 simulate: str | None) -> None:
    before = (lab.git("rev-parse", "HEAD"), lab.git("branch", "--list"), lab.git("status", "--porcelain"),
              lab.remote_main(), sorted(p.name for p in (lab.root / "injections").rglob("*")))
    assert ri.run(args(str(lab.root / f"injections/specs/{case_id}.json"), dry_run=True, simulate=simulate),
                  ri.RecoveryState()) == 0
    out = capsys.readouterr().out
    after = (lab.git("rev-parse", "HEAD"), lab.git("branch", "--list"), lab.git("status", "--porcelain"),
             lab.remote_main(), sorted(p.name for p in (lab.root / "injections").rglob("*")))
    assert before == after
    assert "match=Y (DRY RUN: nothing was done)" in out
    assert re.search(r"\[git\]   git push --quiet origin wip-[0-9a-f]{6}:main", out)
    assert "[api]   GET /repos/" in out


def test_dry_run_of_inj079_checks_the_stats_py_mode(lab: Lab, capsys: pytest.CaptureFixture[str]) -> None:
    lab.git("update-index", "--chmod=+x", "stats.py")
    lab.git("commit", "--quiet", "-m", "make stats.py executable")
    lab.git("push", "--quiet", "origin", "main")
    with pytest.raises(ri.Abort, match="inj-079 needs stats.py tracked as 100644"):
        ri.run(args(str(lab.root / "injections/specs/inj-079.json"), dry_run=True), ri.RecoveryState())


# --------------------------------------------------------------------------- #
# The app's CI never collects these tests
# --------------------------------------------------------------------------- #


def test_app_ci_collects_only_the_app_tests() -> None:
    ini = (LAB / "pytest.ini").read_text(encoding="utf-8")
    assert re.search(r"^testpaths = tests$", ini, flags=re.MULTILINE)
    workflow = (LAB / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert re.findall(r"python -m pytest (\S+)", workflow) == ["tests"]
    here = Path(__file__).resolve()
    assert (LAB / "tests") not in here.parents
