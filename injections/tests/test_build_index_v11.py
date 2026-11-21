"""build_index.py and snapshot_api.py for v1.1: the v1.0 build is byte-identical, and a full simulated holdout
(all 30 cases through the real harness on a temporary repository) passes the v1.1 audit."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import build_index
import holdout_runner as hr
import runs_listing
import snapshot_api
from conftest import LAB, Lab
from test_holdout_runner import PLAN_RUN_ORDER

OUTPUTS = ("index.json", "INDEX.md", "manifest.json")
FLAKY_SCRIPTS = {  # simulated outcomes of the injection pushes, including discards and verification runs
    "inj-066": ["P", "F"],
    "inj-067": ["F", "F", "P"],
    "inj-068": ["F", "P", "F"],
    "inj-069": ["P", "P", "F"],
    "inj-070": ["P", "F"],
}


def committed(name: str) -> bytes:
    return subprocess.run(["git", "show", f"HEAD:injections/{name}"], cwd=LAB, capture_output=True, check=True).stdout


def point_build_index_at(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    injections = root / "injections"
    for name, value in {
        "REPO_ROOT": root, "INJECTIONS_DIR": injections, "SPECS_DIR": injections / "specs",
        "LOGS_DIR": injections / "logs", "API_DIR": injections / "api", "MANIFEST_PATH": injections / "manifest.json",
        "INDEX_JSON_PATH": injections / "index.json", "INDEX_MD_PATH": injections / "INDEX.md",
        "RUNS_MAIN_PATH": injections / "api" / "runs-main.json",
    }.items():
        monkeypatch.setattr(build_index, name, value)


def test_v10_rebuild_is_byte_identical(lab: Lab, monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    point_build_index_at(monkeypatch, lab.root)
    assert build_index.main() == 0
    out = capsys.readouterr().out
    assert "audit: 0 error(s), 1 warning(s)" in out and "v1.1 holdout: 0/30 recorded, 30 pending" in out
    for name in OUTPUTS:
        built = (lab.root / "injections" / name).read_bytes().replace(b"\r\n", b"\n")
        assert built == committed(name), name


@pytest.fixture
def holdout_lab(lab: Lab, monkeypatch: pytest.MonkeyPatch) -> Lab:
    """All 30 cases run in plan order through the harness, then snapshotted from the simulated GitHub."""
    for case_id in PLAN_RUN_ORDER:
        mode = hr.case_mode(lab.spec(case_id))
        script = FLAKY_SCRIPTS.get(case_id) or hr.DEFAULT_SCRIPTS[mode]
        if case_id == "inj-070":
            lab.time.now_value = lab.time.now_value.replace(hour=10, minute=30)
        case, _ = lab.case(case_id, script)
        assert case.run(0) == 0, case_id
        lab.time.now_value = lab.time.now_value.replace(hour=9, minute=0)
    monkeypatch.setattr(snapshot_api, "INJECTIONS_DIR", lab.root / "injections")
    monkeypatch.setattr(snapshot_api, "API_DIR", lab.root / "injections" / "api")
    monkeypatch.setattr(snapshot_api, "REPO_ROOT", lab.root)
    assert snapshot_api.main(gh=lab.gh) == 0
    point_build_index_at(monkeypatch, lab.root)
    return lab


def test_full_simulated_holdout_passes_the_v11_audit(holdout_lab: Lab, capsys: pytest.CaptureFixture[str]) -> None:
    lab = holdout_lab
    listing = json.loads((lab.root / "injections/api/runs-main.json").read_text(encoding="utf-8"))
    assert runs_listing.is_appended(listing) and runs_listing.v10_problems(listing) == []
    assert runs_listing.v10_bytes(listing) == committed("api/runs-main.json")
    assert len(listing["pages"]) == 3 and listing["v1_1_append"]["runs_appended"] == len(lab.gh.runs)
    assert build_index.main() == 0
    out = capsys.readouterr().out
    assert "audit: 0 error(s), 0 warning(s)" in out, out
    index = json.loads((lab.root / "injections/index.json").read_text(encoding="utf-8"))

    # v1.0 content is unchanged with v1.1 present
    old_index = json.loads(committed("index.json"))
    assert index["cases"][:60] == old_index["cases"]
    assert index["unlabelled_extras"][:1] == old_index["unlabelled_extras"]
    markdown = (lab.root / "injections/INDEX.md").read_bytes().replace(b"\r\n", b"\n")
    assert markdown.startswith(committed("INDEX.md"))
    manifest = json.loads((lab.root / "injections/manifest.json").read_text(encoding="utf-8"))
    assert manifest["injections"][:60] == json.loads(committed("manifest.json"))["injections"]

    audit = index["holdout_audit"]
    cases = audit["cases"]
    assert audit["recorded"] == 30 and audit["listing"] == "injections/api/runs-main.json (appended v1.1 page)"
    reruns = [c for c, v in cases.items() if v["planned_rerun"]]
    assert len(reruns) == 12 and all(cases[c]["earlier_same_commit_runs"] == 1 for c in reruns)
    assert all(v["earlier_same_commit_runs"] == 0 for c, v in cases.items() if c not in reruns)
    assert all(entry["eval_commit_is_injection"] for entry in index["cases"][60:])
    for case in hr.DECOY_CASES:
        assert (cases[case]["extra_baseline_runs"], cases[case]["run_number_gaps"]) == (1, 1), case
    assert (cases["inj-062"]["extra_baseline_runs"], cases["inj-062"]["run_number_gaps"]) == (1, 0)
    assert (cases["inj-069"]["extra_baseline_runs"], cases["inj-069"]["run_number_gaps"]) == (2, 3)
    assert (cases["inj-067"]["extra_baseline_runs"], cases["inj-067"]["run_number_gaps"]) == (0, 0)
    for category, row in audit["per_category"].items():
        assert row["cases"] == 5
        assert row["extra_baseline_runs"] > 0 and row["run_number_gaps"] > 0, category
    every_extra = [run for v in cases.values() for run in v["extra_runs"]]
    assert every_extra and all(run["snapshot"] for run in every_extra)
    assert {run["role"] for run in every_extra} == {"decoy", "discarded", "parent", "verification"}


def test_audit_flags_a_missing_extra_run_snapshot(holdout_lab: Lab, capsys: pytest.CaptureFixture[str]) -> None:
    record = json.loads((holdout_lab.root / "injections/inj-090.json").read_text(encoding="utf-8"))
    decoy = record["holdout"]["extra_runs"][0]
    (holdout_lab.root / f"injections/api/{decoy['run_id']}/jobs.json").unlink()
    assert build_index.main() == 1
    assert f"inj-090: decoy run {decoy['run_id']} has no API snapshot" in capsys.readouterr().out


def test_audit_flags_an_extra_earlier_run_of_the_eval_commit(holdout_lab: Lab,
                                                             capsys: pytest.CaptureFixture[str]) -> None:
    path = holdout_lab.root / "injections/api/runs-main.json"
    listing = json.loads(path.read_text(encoding="utf-8"))
    record = json.loads((holdout_lab.root / "injections/inj-063.json").read_text(encoding="utf-8"))
    eval_run = next(run for page in listing["pages"] for run in page["workflow_runs"] if str(run["id"]) == record["run_id"])
    listing["pages"][-1]["workflow_runs"].append(eval_run | {"id": 1, "created_at": "2000-01-01T00:00:00Z"})
    path.write_text(json.dumps(listing), encoding="utf-8")
    assert build_index.main() == 1
    assert "inj-063: 1 earlier run(s) of the eval commit before the eval run; planned 0" in capsys.readouterr().out


def test_snapshot_api_does_not_fetch_a_partial_v11_listing(lab: Lab, monkeypatch: pytest.MonkeyPatch,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    case, _ = lab.case("inj-063", ["F"])
    assert case.run(0) == 0
    monkeypatch.setattr(snapshot_api, "INJECTIONS_DIR", lab.root / "injections")
    monkeypatch.setattr(snapshot_api, "API_DIR", lab.root / "injections" / "api")
    monkeypatch.setattr(snapshot_api, "REPO_ROOT", lab.root)
    before = (lab.root / "injections/api/runs-main.json").read_bytes()
    assert snapshot_api.main(gh=lab.gh) == 0
    assert "v1.1 runs not appended yet (1/30 v1.1 records" in capsys.readouterr().out
    assert (lab.root / "injections/api/runs-main.json").read_bytes() == before


def test_lab_check_flags_a_changed_v10_entry(lab: Lab, monkeypatch: pytest.MonkeyPatch,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    path = lab.root / "injections/api/runs-main.json"
    listing = json.loads(path.read_text(encoding="utf-8"))
    listing["pages"][1]["workflow_runs"][0]["conclusion"] = "failure"
    path.write_text(runs_listing.dumps(listing), encoding="utf-8")
    point_build_index_at(monkeypatch, lab.root)
    assert build_index.main() == 1
    assert "the v1.0 part of injections/api/runs-main.json changed" in capsys.readouterr().out


def test_append_refuses_to_shadow_or_change_v10(lab: Lab) -> None:
    listing = json.loads(committed("api/runs-main.json"))
    assert runs_listing.v10_problems(listing) == []
    old_run = listing["pages"][0]["workflow_runs"][0]
    new_run = dict(old_run, id=1, run_number=99999)
    fetched = [{"total_count": 2, "workflow_runs": [new_run, dict(old_run, conclusion="failure")]}]
    result = runs_listing.append_v11(listing, fetched, "2027-01-01T00:00:00Z")
    assert result["pages"][2]["workflow_runs"] == [new_run]  # the re-fetched v1.0 run is not stored
    assert result["v1_1_append"]["v1_0_runs_refetched_not_stored"] == 1
    assert runs_listing.v10_bytes(result) == committed("api/runs-main.json")
    with pytest.raises(ValueError, match="already appended"):
        runs_listing.append_v11(result, fetched, "2027-01-01T00:00:00Z")
    shadowed = dict(result, pages=result["pages"][:2] + [{"total_count": 1, "workflow_runs": [old_run]}])
    assert any("override v1.0 replay data" in p for p in runs_listing.v10_problems(shadowed))
