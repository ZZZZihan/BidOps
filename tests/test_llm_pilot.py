"""Offline receipt gates must retain failures and reject silent input drift."""

import copy
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "llm_pilot_check", ROOT / "scripts/llm_pilot_check.py"
)
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)


def test_frozen_pilot_contract():
    manifest = json.loads(pilot.MANIFEST.read_text())
    assert pilot.contract_errors(manifest, pilot.contract_snapshot()) == []


@pytest.mark.parametrize("key", ["prompt", "model", "frozen_evaluation_inputs"])
def test_prompt_model_or_dataset_drift_fails_contract(key):
    snapshot = pilot.contract_snapshot()
    manifest = {
        "schema_version": 1,
        "test_targets": pilot.TEST_TARGETS,
        "contract": copy.deepcopy(snapshot),
    }
    manifest["contract"][key] = {"changed": True}
    assert pilot.contract_errors(manifest, snapshot) == [f"{key}_mismatch"]


def test_removing_test_target_is_rejected():
    snapshot = pilot.contract_snapshot()
    manifest = {"schema_version": 1, "test_targets": pilot.TEST_TARGETS[:-1], "contract": snapshot}
    assert pilot.contract_errors(manifest, snapshot) == ["test_targets_mismatch"]


def test_selected_pilot_cases_exclude_unrelated_full_suite_tests():
    selected = [
        "tests.test_agent_demo::test_search_boundaries[unit]",
        "tests.test_workflow_demo::test_screening_admission_counts_existing_qa_failure_and_success",
    ]
    unrelated = ["tests.test_enterprise::test_other", "tests.test_workflow_demo::test_other"]
    result = {"cases": [{"id": name, "state": "passed"} for name in selected + unrelated]}
    assert pilot.pilot_case_ids(result) == sorted(selected)


@pytest.mark.parametrize(
    "body,passed",
    [
        ("<testsuite/>", False),
        ("invalid XML", False),
        ('<testsuite><testcase name="unit"/></testsuite>', True),
        ('<testsuite><testcase name="unit"><failure/></testcase></testsuite>', False),
        ('<testsuite><testcase name="unit"><error/></testcase></testsuite>', False),
        ('<testsuite><testcase name="unit"><skipped/></testcase></testsuite>', False),
    ],
)
def test_receipt_does_not_drop_failed_skipped_or_empty_cases(tmp_path, body, passed):
    path = tmp_path / "results.xml"
    path.write_text(body)
    result = pilot.test_results(path)
    assert pilot.suite_passed(0, result) is passed
    assert not pilot.suite_passed(1, result)


def test_missing_test_report_fails(tmp_path):
    result = pilot.test_results(tmp_path / "absent.xml")
    assert result["report_error"] == "missing_or_invalid_test_report"
    assert not pilot.suite_passed(0, result)


def test_full_backend_only_allows_existing_optional_frontend_skip(tmp_path):
    module, name = pilot.OPTIONAL_FRONTEND_CHECK.split("::")
    path = tmp_path / "results.xml"
    path.write_text(
        f'<testsuite><testcase name="passed"/><testcase classname="{module}" name="{name}">'
        "<skipped/></testcase></testsuite>"
    )
    result = pilot.test_results(path)
    assert pilot.suite_passed(0, result, full_suite=True)
    assert not pilot.suite_passed(0, result)
    result["cases"][1]["id"] = "tests.test_agent_demo::pilot_check"
    assert not pilot.suite_passed(0, result, full_suite=True)


def test_receipt_cannot_overwrite_previous_evidence(tmp_path):
    path = tmp_path / "receipt.json"
    path.write_text("preserve failed evidence")
    with pytest.raises(SystemExit):
        pilot.main(["--output", str(path)])
    assert path.read_text() == "preserve failed evidence"


def test_missing_manifest_still_saves_failure_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot, "MANIFEST", tmp_path / "missing.json")
    path = tmp_path / "receipt.json"
    assert pilot.main(["--output", str(path)]) == 1
    report = json.loads(path.read_text())
    assert not report["gate_passed"] and report["runner_error"] == "FileNotFoundError"
    assert report["agent_score"] is None and report["actual_provider_calls"] == 0


@pytest.mark.parametrize("outcome", ["failure", "timeout", "contract_drift", "missing_cases"])
def test_failed_run_always_saves_non_scoring_receipt(tmp_path, monkeypatch, outcome):
    manifest = {
        "schema_version": 1,
        "test_targets": pilot.TEST_TARGETS,
        "contract": pilot.contract_snapshot(),
        "expected_pilot_case_ids": ["tests.test_agent_demo::unit"],
    }
    if outcome == "contract_drift":
        manifest["contract"]["prompt"] = {"version": "unreviewed-change"}
    elif outcome == "missing_cases":
        manifest["expected_pilot_case_ids"].append("tests.test_agent_demo::missing")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(pilot, "MANIFEST", path)
    monkeypatch.setattr(
        pilot, "source_identity", lambda: {"code_sha": "unit", "working_tree_dirty": False}
    )
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["env"]["PYTEST_ADDOPTS"] == ""
        assert "addopts=" in command
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, 120)
        xml = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml=")))
        failed = "<failure/>" if outcome == "failure" else ""
        xml.write_text(
            '<testsuite><testcase classname="tests.test_agent_demo" name="unit">'
            f"{failed}</testcase></testsuite>"
        )
        return SimpleNamespace(returncode=1 if outcome == "failure" else 0)

    monkeypatch.setattr(pilot.subprocess, "run", run)
    receipt = tmp_path / "receipt.json"
    assert pilot.main(["--output", str(receipt)]) == 1
    report = json.loads(receipt.read_text())
    assert not report["gate_passed"]
    assert report["execution_kind"] == "offline_contract" and report["agent_score"] is None
    assert len(calls) == 1  # Drift must not skip the regression suite.
    assert all(target in calls[0] for target in pilot.TEST_TARGETS)
    if outcome == "failure":
        assert report["tests"]["failed"] == 1
        assert report["pilot_case_set_matches"]
    elif outcome == "timeout":
        assert report["runner_error"] == "test_timeout"
    elif outcome == "contract_drift":
        assert report["contract_errors"] == ["prompt_mismatch"]
        assert report["pilot_case_set_matches"]
    else:
        assert not report["pilot_case_set_matches"]
