"""B01 executable integration: immutable fixture pins and no Agent acceptance claims."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from bidops.screening import offline_eval

SOURCE = Path(__file__).parents[1]


@pytest.fixture
def frozen_root(tmp_path, monkeypatch):
    for filename in offline_eval.REQUIRED_FROZEN_PATHS:
        target = tmp_path / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / filename, target)
    monkeypatch.setattr(
        offline_eval, "_git", lambda root, *args: "a" * 40 if args[0] == "rev-parse" else ""
    )
    monkeypatch.setattr(offline_eval, "RUNTIME_ROOT", tmp_path)
    return tmp_path


def freeze(root, *, exclude=(), overrides=None):
    manifest = {
        "protocol_id": offline_eval.PROTOCOL_ID,
        "protocol_version": offline_eval.PROTOCOL_VERSION,
        "frozen_at": "2020-01-01T00:00:00Z",
        "files": [
            {"path": name, "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}
            for name in sorted(offline_eval.REQUIRED_FROZEN_PATHS - set(exclude))
        ],
    }
    manifest.update(overrides or {})
    data = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    (root / "freeze.json").write_bytes(data)
    return (
        hashlib.sha256(data).hexdigest(),
        hashlib.sha256((root / offline_eval.FIXTURE_PATH).read_bytes()).hexdigest(),
    )


def run(root, pins):
    return offline_eval.run_fixture(root, Path("freeze.json"), *pins)


def test_frozen_fixture_runs_framework_and_remains_business_blocked(frozen_root):
    result = run(frozen_root, freeze(frozen_root))
    assert result["status"] == "PASS"
    assert result["pydantic_evals"]["cases"] == 5
    assert result["pydantic_evals"]["failures"] == 0
    assert result["dataset_gate"]["status"] == "BLOCKED"
    assert result["dataset_gate"]["counts"]["real_notices"] == 0
    assert result["human_trials"]["status"] == "BLOCKED"
    assert result["agent_score"] is None
    assert result["business_acceptance"] == "BLOCKED"
    assert result["versions"]["actual_model_id"] is None
    assert result["versions"]["model_calls"] == 0
    assert result["versions"]["estimated_cost_cny"] == "0"
    assert result["source_tree_dirty"] is False


def test_frozen_root_must_match_actual_imported_runtime(frozen_root, monkeypatch):
    monkeypatch.setattr(offline_eval, "RUNTIME_ROOT", SOURCE)
    with pytest.raises(ValueError, match="match_executed_code"):
        run(frozen_root, freeze(frozen_root))


def test_installed_runtime_versions_must_match_frozen_lock(frozen_root, monkeypatch):
    monkeypatch.setattr(offline_eval, "version", lambda name: "0.0.0")
    with pytest.raises(ValueError, match="dependency_version_mismatch"):
        run(frozen_root, freeze(frozen_root))


@pytest.mark.parametrize("path", sorted(offline_eval.REQUIRED_FROZEN_PATHS))
def test_frozen_closure_cannot_omit_critical_file(frozen_root, path):
    result = run(frozen_root, freeze(frozen_root, exclude=[path]))
    assert result["status"] == "FAIL"
    assert result["issues"][0]["code"] == "incomplete_or_wrong_protocol_freeze"


@pytest.mark.parametrize("change", ["file", "manifest", "input_pin", "future_freeze"])
def test_cli_rejects_changed_artifact_or_pin(frozen_root, change):
    pins = freeze(frozen_root)
    if change == "file":
        (frozen_root / offline_eval.FIXTURE_PATH).write_text("{}")
    elif change == "manifest":
        (frozen_root / "freeze.json").write_text("{}")
    elif change == "input_pin":
        pins = (pins[0], "b" * 64)
    else:
        pins = freeze(frozen_root, overrides={"frozen_at": "2999-01-01T00:00:00Z"})
    assert run(frozen_root, pins)["status"] == "FAIL"


@pytest.mark.parametrize("change", ["purpose", "real_record", "human", "version"])
def test_even_re_pinned_fixture_cannot_claim_real_execution(frozen_root, change):
    path = frozen_root / (
        offline_eval.PROTOCOL_PATH if change == "version" else offline_eval.FIXTURE_PATH
    )
    obj = json.loads(path.read_text())
    if change == "purpose":
        obj["purpose"] = "independent_acceptance"
    elif change == "real_record":
        obj["dataset_records"][0]["provenance_kind"] = "real"
    elif change == "human":
        obj["contributors"] = [{"person_id": "claimed-human"}]
    else:
        obj["scorer_version"] = "another-scorer"
    path.write_text(json.dumps(obj))
    with pytest.raises(ValueError):
        run(frozen_root, freeze(frozen_root))


def test_cli_writes_sanitized_report_once_and_never_overwrites(frozen_root):
    pins = freeze(frozen_root)
    output = frozen_root / "reports/result.json"
    argv = [
        "--root",
        str(frozen_root),
        "--freeze",
        "freeze.json",
        "--expected-freeze-sha256",
        pins[0],
        "--expected-input-sha256",
        pins[1],
        "--output",
        str(output),
    ]
    assert offline_eval.main(argv) == 0
    raw = output.read_text()
    assert "UNIT synthetic material" not in raw
    with pytest.raises(FileExistsError):
        offline_eval.main(argv)
    assert output.read_text() == raw


def test_cli_failed_scoring_retains_failed_report(frozen_root):
    path = frozen_root / offline_eval.FIXTURE_PATH
    obj = json.loads(path.read_text())
    obj["observed_tasks"][0]["state"] = "timed_out"
    path.write_text(json.dumps(obj))
    pins = freeze(frozen_root)
    output = frozen_root / "failure.json"
    assert (
        offline_eval.main(
            [
                "--root",
                str(frozen_root),
                "--freeze",
                "freeze.json",
                "--expected-freeze-sha256",
                pins[0],
                "--expected-input-sha256",
                pins[1],
                "--output",
                str(output),
            ]
        )
        == 2
    )
    report = json.loads(output.read_text())
    assert report["scorer"]["metrics"]["quality"]["denominator"] == 5
    assert report["scorer"]["counts"]["task_state:timed_out"] == 1


@pytest.mark.parametrize("invalid", [None, {}, {"task_id": []}])
def test_extra_invalid_raw_outputs_cannot_drop_registered_denominators(frozen_root, invalid):
    path = frozen_root / offline_eval.FIXTURE_PATH
    obj = json.loads(path.read_text())
    obj["observed_tasks"].append(invalid)
    path.write_text(json.dumps(obj))
    report = run(frozen_root, freeze(frozen_root))
    assert report["status"] == "FAIL"
    assert report["pydantic_evals"]["cases"] == 5
    assert report["scorer"]["metrics"]["quality"]["denominator"] == 5
    assert len(report["scorer"]["protocol_errors"]) == 1


def test_protocol_thresholds_remain_original_plan(frozen_root):
    protocol = json.loads((frozen_root / offline_eval.PROTOCOL_PATH).read_text())
    assert protocol["thresholds"] == {
        "p1_project_code_and_source_recall": 1.0,
        "p1_deadline_and_budget_unit_precision": 0.95,
        "p1_deadline_and_budget_unit_coverage": 0.85,
        "candidate_recall": 0.95,
        "hard_condition_recall": 0.95,
        "definite_error_rate_max": 0.05,
        "decidable_coverage": 0.85,
        "citation_resolution": 1.0,
        "evidence_support": 0.98,
        "serious_errors_max": 0,
        "undisclosed_stale_references_max": 0,
        "reviewed_quality_change_min": 0,
        "median_paired_time_saving_min": 0.20,
    }
    pins = freeze(frozen_root)
    protocol["minimum_data"]["real_notices"] = 6
    (frozen_root / offline_eval.PROTOCOL_PATH).write_text(json.dumps(protocol))
    assert run(frozen_root, pins)["status"] == "FAIL"
