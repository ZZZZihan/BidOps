"""Pins/budget/privacy failures use real files; no independent data or provider."""

import copy
import hashlib
import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
from bidops.acceptance import runner
from bidops.acceptance.collection import collect_observed
from bidops.acceptance.evaluation import canonical_sha256
from bidops.acceptance.run_manifest import (
    CumulativeLedger,
    RunManifest,
    check_environment,
    pinned_json,
    reconcile_ledger,
    runtime_closure,
)
from bidops.screening.eval_protocol import records_digest
from test_acceptance_collection import batch_for, register, run
from test_company_access import ALICE
from test_qualification_service import service as service
from test_qualification_sources import domain as domain
from test_qualification_sources import sources as sources

STAMP = "2026-10-22T12:00:00Z"
FINISHED = datetime(2026, 10, 22, 12, tzinfo=UTC)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def save(path, value):
    raw = json.dumps(value, ensure_ascii=False).encode()
    path.write_bytes(raw)
    return digest(raw)


def ledger():
    return {
        "schema_version": "cumulative-model-cost-v1",
        "scope": "all_bidops_development_and_evaluation",
        "currency": "CNY",
        "limit_cny": "10",
        "opening_spent_cny": "4",
        "opening_reserved_cny": "1",
        "opening_record_sha256": "a" * 64,
        "reconciled_at": STAMP,
        "complete_through": STAMP,
        "independently_reconciled_by": "declared-human-not-authenticated",
        "entries": [
            {
                "entry_id": "entry-1",
                "execution_id": "job-1",
                "occurred_at": STAMP,
                "model_calls": 2,
                "input_tokens": 100,
                "output_tokens": 20,
                "estimated_cost_cny": "0.2",
                "reserved_cost_cny": "0.8",
                "unknown_calls": 1,
                "provider_record_sha256": None,
            }
        ],
    }


def execution():
    return [
        {
            "execution_id": "job-1",
            "usage": {
                "model_calls": 2,
                "input_tokens": 100,
                "output_tokens": 20,
                "estimated_cost_cny": "0.2",
                "reserved_cost_cny": "0.8",
                "unknown_calls": 1,
            },
        }
    ]


def manifest(**changes):
    data = dict(
        schema_version="independent-evaluation-v1",
        run_id="unit-scorer-only",
        purpose="engineering_fixture",
        frozen_at=STAMP,
        execution_started_at=STAMP,
        execution_finished_at=STAMP,
        evaluation_code_sha="a" * 40,
        candidate_code_sha="b" * 40,
        candidate_implementation_sha256="b" * 64,
        lock_sha256="c" * 64,
        runtime_closure_sha256="d" * 64,
        input_sha256="e" * 64,
        collection_sha256=None,
        ledger_sha256=None,
        registration_sha256=None,
        prompt_version="qualification-v1",
        prompt_sha256="f" * 64,
        rule_version="qualification-logic-v1",
        parameters_sha256=None,
    )
    return RunManifest.model_validate(data | changes)


@pytest.mark.parametrize(
    "raw", [b'{"same":1,"same":2}', b'{"v":NaN}', b'{"v":Infinity}', b"not-json"]
)
def test_pinned_json_rejects_ambiguous_or_invalid_input_without_echo(tmp_path, raw):
    path = tmp_path / "private.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="^pinned_artifact_unavailable_or_invalid$"):
        pinned_json(path, digest(raw))


def test_pin_mismatch_precedes_parsing(tmp_path):
    path = tmp_path / "private.json"
    path.write_text("SECRET malformed")
    with pytest.raises(ValueError) as error:
        pinned_json(path, "0" * 64)
    assert "SECRET" not in str(error.value)


def test_private_report_never_replaces_failed_first_result_or_symlink(tmp_path):
    path = tmp_path / "first.json"
    runner.write_private_report(path, {"status": "FAIL"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        runner.write_private_report(path, {"status": "PASS"})
    alias = tmp_path / "alias.json"
    alias.symlink_to(path)
    with pytest.raises(FileExistsError):
        runner.write_private_report(alias, {"status": "PASS"})
    assert json.loads(path.read_text()) == {"status": "FAIL"}


def test_full_cumulative_budget_keeps_unknown_and_historical_reservations():
    result = reconcile_ledger(ledger(), execution(), FINISHED)
    assert result["status"] == "PASS"
    assert result["cumulative_spent_cny"] == "4.2"
    assert result["cumulative_reserved_cny"] == "1.8"
    assert result["remaining_after_reservations_cny"] == "4.0"
    assert reconcile_ledger(None, execution(), FINISHED)["status"] == "BLOCKED"


@pytest.mark.parametrize(
    "case,code",
    [
        ("over_budget", "cumulative_budget_exceeded"),
        ("release", "unknown_cost_released"),
        ("calls", "ledger_model_calls_mismatch"),
        ("tokens", "ledger_input_tokens_mismatch"),
        ("missing", "execution_missing_from_ledger"),
        ("old", "ledger_does_not_cover_run"),
        ("duplicates", "duplicate_execution_receipt"),
        ("unknown", "unknown_calls_underreported"),
    ],
)
def test_budget_failures_cannot_be_hidden(case, code):
    value, runs = ledger(), execution()
    if case == "over_budget":
        value["opening_spent_cny"] = "9"
    if case == "release":
        value["entries"][0]["reserved_cost_cny"] = "0"
    if case == "calls":
        value["entries"][0]["model_calls"] = 3
    if case == "tokens":
        value["entries"][0]["input_tokens"] = 99
    if case == "missing":
        value["entries"] = []
    if case == "old":
        value["complete_through"] = "2026-10-22T11:59:59Z"
        value["entries"][0]["occurred_at"] = value["complete_through"]
    if case == "duplicates":
        runs *= 2
    if case == "unknown":
        value["entries"][0]["unknown_calls"] = 0
    result = reconcile_ledger(value, runs, FINISHED)
    assert result["status"] == "FAIL"
    assert code in {r["code"] for r in result["issues"]}


@pytest.mark.parametrize(
    "change",
    [
        {"opening_spent_cny": "-1"},
        {"opening_spent_cny": "10.0000000000000000000000000001"},
        {"opening_reserved_cny": "NaN"},
        {"limit_cny": "20"},
        {"scope": "current_run_only"},
        {"complete_through": "2026-10-23T12:00:00Z"},
    ],
)
def test_invalid_ledgers_cannot_reset_budget(change):
    with pytest.raises(ValueError):
        CumulativeLedger.model_validate(ledger() | change)


def test_runtime_closure_detects_added_code_and_rejects_symlink(tmp_path):
    (tmp_path / "backend/bidops").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "uv.lock").write_text("unit")
    (tmp_path / "pyproject.toml").write_text("unit")
    before = canonical_sha256(runtime_closure(tmp_path))
    (tmp_path / "scripts/new.py").write_text("print('unit')")
    assert canonical_sha256(runtime_closure(tmp_path)) != before
    (tmp_path / "scripts/link.py").symlink_to(tmp_path / "scripts/new.py")
    with pytest.raises(ValueError, match="runtime_symlink"):
        runtime_closure(tmp_path)


def test_foreign_checkout_cannot_validate_executed_code(tmp_path):
    with pytest.raises(ValueError, match="runtime_root_mismatch"):
        check_environment(manifest(), tmp_path)


def test_collection_mismatch_is_not_scored_as_saved_output():
    registration = {
        "task_id": "t",
        "batch_id": "b",
        "unit_id": "u",
        "notice_revision": {"object_id": "n", "version_id": "v", "sha256": "a" * 64},
        "package_id": None,
        "execution_kind": "not_submitted",
        "execution_id": None,
        "expected_result_revision": None,
    }
    records = [
        {
            "registration": registration,
            "candidate": {
                "unit_id": "u",
                "package_id": None,
                "notice_revisions": [registration["notice_revision"]],
                "disposition": "candidate",
            },
            "fields": {},
            "execution": None,
            "agent_result": None,
            "source_state": {"current": True},
            "provenance": {},
            "usage": {},
            "assessment_selection_error": None,
        }
    ]
    observed = [
        {
            "task_id": "t",
            "state": "missing",
            "candidate": True,
            "fields": {},
            "assessment": {},
            "positive_recommendation": False,
            "stale_disclosed": False,
        }
    ]
    reg_hash = canonical_sha256([registration])
    data = {
        "records": records,
        "observed_tasks": observed,
        "metadata": {
            "registered": 1,
            "states": {"missing": 1},
            "registration_sha256": reg_hash,
            "records_sha256": canonical_sha256(records),
            "observed_tasks_sha256": canonical_sha256(observed),
            "executions": [],
        },
    }
    bundle = {"gold_tasks": [{"task_id": "t"}], "observed_tasks": observed}
    config = manifest(registration_sha256=reg_hash)
    assert runner.check_collection(data, bundle, config)["status"] == "PASS"
    changed = copy.deepcopy(bundle)
    changed["observed_tasks"][0]["state"] = "completed"
    result = runner.check_collection(data, changed, config)
    assert result["status"] == "FAIL"
    assert "scored_output_not_saved_collection" in {i["code"] for i in result["issues"]}
    # Rehashing invented predictions cannot change an unsubmitted execution into
    # a completed original Agent output, even with mutually agreeing file pins.
    data["observed_tasks"] = changed["observed_tasks"]
    data["metadata"]["observed_tasks_sha256"] = canonical_sha256(data["observed_tasks"])
    data["metadata"]["states"] = {"completed": 1}
    result = runner.check_collection(data, changed, config)
    assert result["status"] == "FAIL"
    assert "observed_not_original_record_projection" in {i["code"] for i in result["issues"]}


def test_pinned_fixture_cli_reports_score_but_blocks_all_business(tmp_path, monkeypatch, capsys):
    bundle = json.loads(
        (Path(__file__).parents[1] / "evals/enterprise-v1/engineering-fixture.json").read_text()
    )
    bundle.pop("fixture_label")
    bundle.update(
        sampling={
            "purpose": "engineering_fixture",
            "seed": 127,
            "candidate_pool_sha256": records_digest([]),
            "selected_record_ids": [],
        },
        annotations=[],
        contributors=[],
    )
    inputs, config, output = (
        tmp_path / name for name in ("input.json", "manifest.json", "report.json")
    )
    input_pin = save(inputs, bundle)
    config_pin = save(config, manifest(input_sha256=input_pin).model_dump(mode="json"))
    # Real environment/clean-checkout boundary is separately checked above and by
    # the root's committed-version calibration. This test isolates CLI file gates.
    monkeypatch.setattr(runner, "check_environment", lambda _: {"status": "PASS"})
    argv = [
        "--manifest",
        str(config),
        "--expected-manifest-sha256",
        config_pin,
        "--input",
        str(inputs),
        "--output",
        str(output),
    ]
    assert runner.main(argv) == 0
    report = json.loads(output.read_text())
    assert report["evaluation"]["sections"]["screening"]["status"] == "PASS"
    assert report["cumulative_budget"]["status"] == "BLOCKED"
    assert report["business_acceptance"] == "BLOCKED"
    assert all(report["ac_status"][key] == "BLOCKED" for key in runner.BUSINESS_ACS)
    assert report["real_calls_made_by_this_audit"] == 0
    assert report["agent_score"] is None
    assert "source_text" not in capsys.readouterr().out
    assert runner.main(argv) == 2  # first report cannot be overwritten
    inputs.write_text("PRIVATE_DATA")
    argv[-1] = str(tmp_path / "failed.json")
    assert runner.main(argv) == 2
    assert "PRIVATE_DATA" not in capsys.readouterr().out
    assert json.loads((tmp_path / "failed.json").read_text())["status"] == "FAIL"


def test_collected_usage_cannot_override_original_saved_result(service):
    screening = service[0]
    result = run(service)
    gathered = collect_observed(
        screening, ALICE, [register(service, batch_for(service), result=result)]
    )
    provenance = gathered["records"][0]["provenance"]
    config = manifest(
        candidate_code_sha=provenance["code_sha"],
        candidate_implementation_sha256=provenance["implementation_sha256"],
        lock_sha256=provenance["lock_sha256"],
        prompt_version=provenance["prompt_version"],
        prompt_sha256=provenance["prompt_sha256"],
        parameters_sha256=canonical_sha256(provenance["parameters"]),
        registration_sha256=gathered["metadata"]["registration_sha256"],
    )
    bundle = {
        "gold_tasks": [{"task_id": gathered["observed_tasks"][0]["task_id"]}],
        "observed_tasks": gathered["observed_tasks"],
    }
    assert runner.check_collection(gathered, bundle, config)["status"] == "PASS"
    assert provenance["model_calls"] == 5
    for usage in (gathered["records"][0]["usage"], gathered["metadata"]["executions"][0]["usage"]):
        usage["model_calls"] = 0
        usage["input_tokens"] = 0
        usage["output_tokens"] = 0
    gathered["metadata"]["records_sha256"] = canonical_sha256(gathered["records"])
    audited = runner.check_collection(gathered, bundle, config)
    assert audited["status"] == "FAIL"
    assert "original_screening_receipt_mismatch" in {i["code"] for i in audited["issues"]}
