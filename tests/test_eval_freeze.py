"""Authored artifacts verify freeze mechanics, never real-model acceptance."""

import copy
import hashlib
import json

import pytest
from bidops.screening.freeze import (
    FreezeManifest,
    FrozenFile,
    RunVersions,
    verify_freeze,
    verify_run_versions,
)
from pydantic import ValidationError


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def codes(report: dict) -> set[str]:
    return {issue["code"] for issue in report["issues"]}


@pytest.fixture
def frozen(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "gold.json").write_bytes(b'{"gold":"fixture-only"}\n')
    (tmp_path / "scorer.py").write_bytes(b"# unit fixture\n")
    payload = {
        "protocol_id": "b01-engineering-test",
        "protocol_version": "1",
        "frozen_at": "2026-01-01T00:00:00+08:00",
        "files": [
            {"path": str(path.relative_to(tmp_path)), "sha256": digest(path.read_bytes())}
            for path in (tmp_path / "data" / "gold.json", tmp_path / "scorer.py")
        ],
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path, manifest, payload, digest(manifest.read_bytes())


def write_manifest(frozen, payload):
    _, manifest, _, _ = frozen
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    # Only this test author pins newly authored fixtures. Runtime does not repin.
    return digest(manifest.read_bytes())


def versions(**updates):
    return {
        "code_sha": "a" * 40,
        "data_sha256": "b" * 64,
        "profile_sha256": "c" * 64,
        "prompt_version": "prompt-v1",
        "scorer_version": "scorer-v1",
        "actual_model_id": None,
        "execution_kind": "scorer_fixture",
        "model_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_cny": "0.0000",
        "parameters": {},
        **updates,
    }


def model_versions(**updates):
    return versions(
        **{
            "actual_model_id": "provider-returned-model-20260101",
            "execution_kind": "recorded_model_output",
            "model_calls": 2,
            "input_tokens": 120,
            "output_tokens": 80,
            "estimated_cost_cny": "0.0012",
            "parameters": {"temperature": 0, "max_output_tokens": 512, "thinking": "disabled"},
            **updates,
        }
    )


def test_pinned_manifest_checks_all_files_without_writing(frozen):
    root, manifest, payload, pin = frozen
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    report = verify_freeze(root, "manifest.json", pin)
    assert report["status"] == "PASS"
    assert report["counts"] == {"expected_files": 2, "verified_files": 2}
    assert report["manifest_sha256"] == pin
    assert report["protocol_id"] == payload["protocol_id"]
    assert report["frozen_at"] == payload["frozen_at"]
    assert verify_freeze(root, manifest, pin)["status"] == "PASS"
    assert before == {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_modified_manifest_fails_even_with_updated_file_hashes(frozen):
    root, manifest, payload, pin = frozen
    (root / "scorer.py").write_bytes(b"# changed scorer\n")
    payload["files"][1]["sha256"] = digest((root / "scorer.py").read_bytes())
    write_manifest(frozen, payload)
    report = verify_freeze(root, manifest, pin)
    assert codes(report) == {"manifest_sha256_mismatch"}
    assert report["counts"]["expected_files"] == 0


def test_even_whitespace_only_manifest_change_breaks_external_pin(frozen):
    root, manifest, _, pin = frozen
    manifest.write_bytes(manifest.read_bytes() + b"\n")
    assert codes(verify_freeze(root, manifest, pin)) == {"manifest_sha256_mismatch"}


@pytest.mark.parametrize("pin", [None, "", "a" * 63, "A" * 64, 1])
def test_external_pin_is_required_and_strict(frozen, pin):
    root, manifest, _, _ = frozen
    assert codes(verify_freeze(root, manifest, pin)) == {"invalid_manifest_pin"}


@pytest.mark.parametrize("change", ["modified", "deleted", "directory"])
def test_file_tampering_missing_and_nonfile_fail_with_path_id(frozen, change):
    root, manifest, _, pin = frozen
    target = root / "data" / "gold.json"
    if change == "modified":
        target.write_bytes(b"replacement")
    else:
        target.unlink()
        if change == "directory":
            target.mkdir()
    report = verify_freeze(root, manifest, pin)
    expected = "frozen_file_sha256_mismatch" if change == "modified" else "frozen_file_missing"
    assert codes(report) == {expected}
    assert report["failure_ids"] == ["data/gold.json"]
    assert report["counts"]["verified_files"] == 1


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "/tmp/escape",
        "data/../scorer.py",
        "./scorer.py",
        "data//gold.json",
        "C:/escape",
        "data\\gold.json",
        ".",
        " a",
    ],
)
def test_noncanonical_and_escaping_declared_paths_fail(frozen, path):
    root, manifest, payload, _ = frozen
    payload["files"][0]["path"] = path
    pin = write_manifest(frozen, payload)
    assert codes(verify_freeze(root, manifest, pin)) == {"invalid_manifest"}


def test_duplicate_declared_paths_fail(frozen):
    root, manifest, payload, _ = frozen
    payload["files"].append(copy.deepcopy(payload["files"][0]))
    pin = write_manifest(frozen, payload)
    report = verify_freeze(root, manifest, pin)
    assert codes(report) == {"duplicate_frozen_path"}
    assert report["failure_ids"] == ["data/gold.json"]


def test_external_file_symlink_is_rejected_even_with_matching_hash(frozen, tmp_path):
    root, manifest, payload, _ = frozen
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_bytes(b"outside-boundary")
    (root / "outside-link").symlink_to(outside)
    payload["files"] = [{"path": "outside-link", "sha256": digest(outside.read_bytes())}]
    pin = write_manifest(frozen, payload)
    assert codes(verify_freeze(root, manifest, pin)) == {"frozen_path_outside_root"}


def test_external_directory_symlink_cannot_hide_escape(frozen, tmp_path):
    root, manifest, payload, _ = frozen
    outside = tmp_path.parent / f"{tmp_path.name}-outside-dir"
    outside.mkdir()
    (outside / "gold.json").write_bytes(b"outside-boundary")
    (root / "external-dir").symlink_to(outside, target_is_directory=True)
    payload["files"] = [{"path": "external-dir/gold.json", "sha256": digest(b"outside-boundary")}]
    pin = write_manifest(frozen, payload)
    assert codes(verify_freeze(root, manifest, pin)) == {"frozen_path_outside_root"}


def test_internal_symlink_allowed_but_alias_duplicate_rejected(frozen):
    root, manifest, payload, _ = frozen
    (root / "alias.py").symlink_to(root / "scorer.py")
    payload["files"] = [{"path": "alias.py", "sha256": payload["files"][1]["sha256"]}]
    pin = write_manifest(frozen, payload)
    assert verify_freeze(root, manifest, pin)["status"] == "PASS"
    payload["files"].append({"path": "scorer.py", "sha256": payload["files"][0]["sha256"]})
    pin = write_manifest(frozen, payload)
    assert codes(verify_freeze(root, manifest, pin)) == {"duplicate_frozen_target"}


def test_manifest_cannot_be_outside_root_or_symlink_outside(frozen, tmp_path):
    root, manifest, _, pin = frozen
    outside = tmp_path.parent / f"{tmp_path.name}-external-manifest.json"
    outside.write_bytes(manifest.read_bytes())
    assert codes(verify_freeze(root, outside, pin)) == {"manifest_path_outside_root"}
    alias = root / "external-manifest.json"
    alias.symlink_to(outside)
    assert codes(verify_freeze(root, alias, pin)) == {"manifest_path_outside_root"}


def test_missing_manifest_and_invalid_root_are_reported(frozen):
    root, manifest, _, pin = frozen
    assert codes(verify_freeze(root, "missing.json", pin)) == {"manifest_unreadable"}
    assert codes(verify_freeze(root / "absent", manifest, pin)) == {"invalid_freeze_root"}
    assert codes(verify_freeze(root / "scorer.py", manifest, pin)) == {"invalid_freeze_root"}


@pytest.mark.parametrize("content", [b"not-json", b"\xff", b'{"files":[],"files":[]}', b"[]"])
def test_invalid_and_duplicate_key_manifest_json_fail(frozen, content):
    root, manifest, _, _ = frozen
    manifest.write_bytes(content)
    assert codes(verify_freeze(root, manifest, digest(content))) == {"invalid_manifest"}


@pytest.mark.parametrize("field", ["protocol_id", "protocol_version", "frozen_at", "files"])
def test_manifest_required_fields_cannot_be_omitted(frozen, field):
    root, manifest, payload, _ = frozen
    del payload[field]
    pin = write_manifest(frozen, payload)
    assert verify_freeze(root, manifest, pin)["status"] == "FAIL"


def test_strict_manifest_schema_and_timezone(frozen):
    _, _, payload, _ = frozen
    FreezeManifest.model_validate(payload)
    with pytest.raises(ValidationError):
        FreezeManifest.model_validate({**payload, "frozen_at": "2026-01-01T00:00:00"})
    with pytest.raises(ValidationError):
        FreezeManifest.model_validate({**payload, "self_hash": "a" * 64})
    with pytest.raises(ValidationError):
        FreezeManifest.model_validate({**payload, "files": []})
    with pytest.raises(ValidationError):
        FrozenFile(path="data/gold.json", sha256="a" * 63)


def test_fixture_and_recorded_run_metadata_verify():
    assert verify_run_versions(versions(), versions())["status"] == "PASS"
    report = verify_run_versions(RunVersions(**model_versions()), model_versions())
    assert report["status"] == "PASS"
    assert report["counts"]["compared_fields"] == 12
    # Monetary values stay exact decimal strings without requiring identical scale.
    assert verify_run_versions(versions(), versions(estimated_cost_cny="0"))["status"] == "PASS"


@pytest.mark.parametrize("field", list(RunVersions.model_fields))
def test_missing_version_fields_are_rejected(field):
    observed = versions()
    del observed[field]
    report = verify_run_versions(versions(), observed)
    assert report["status"] == "FAIL"
    assert codes(report) == {"invalid_run_versions"}
    assert f"observed.{field}" in report["failure_ids"]


@pytest.mark.parametrize(
    "updates",
    [
        {"actual_model_id": "requested-model"},
        {"model_calls": 1},
        {"input_tokens": 1},
        {"output_tokens": 1},
        {"estimated_cost_cny": "0.000000000000000001"},
        {"parameters": {"temperature": 0}},
        {"agent_score": 100},
    ],
)
def test_fixture_cannot_claim_model_calls_cost_parameters_or_agent_score(updates):
    with pytest.raises(ValidationError):
        RunVersions(**versions(**updates))


@pytest.mark.parametrize(
    "updates",
    [
        {"actual_model_id": None},
        {"model_calls": 0},
        {"requested_model_id": "alias"},
    ],
)
def test_recorded_output_requires_actual_returned_model_and_calls(updates):
    with pytest.raises(ValidationError):
        RunVersions(**model_versions(**updates))


@pytest.mark.parametrize(
    "field,new_value",
    [
        ("code_sha", "d" * 40),
        ("data_sha256", "e" * 64),
        ("profile_sha256", "f" * 64),
        ("prompt_version", "prompt-v2"),
        ("scorer_version", "scorer-v2"),
        ("actual_model_id", "different-returned-model"),
        ("model_calls", 3),
        ("input_tokens", 121),
        ("output_tokens", 81),
        ("estimated_cost_cny", "0.0013"),
        ("parameters", {"temperature": 0.1}),
    ],
)
def test_actual_model_parameters_versions_and_usage_must_match(field, new_value):
    report = verify_run_versions(model_versions(), model_versions(**{field: new_value}))
    assert codes(report) == {"run_version_mismatch"}
    assert report["failure_ids"] == [field]


def test_execution_kind_mismatch_fails_even_if_both_records_are_valid():
    report = verify_run_versions(versions(), model_versions())
    assert "execution_kind" in report["failure_ids"]


@pytest.mark.parametrize(
    "updates",
    [
        {"code_sha": "a" * 7},
        {"code_sha": "A" * 40},
        {"data_sha256": "x" * 64},
        {"profile_sha256": "c" * 63},
        {"prompt_version": " "},
        {"scorer_version": ""},
        {"model_calls": True},
        {"input_tokens": "0"},
        {"output_tokens": -1},
        {"estimated_cost_cny": 0},
        {"estimated_cost_cny": "NaN"},
        {"estimated_cost_cny": "Infinity"},
        {"estimated_cost_cny": "-1"},
        {"estimated_cost_cny": "1e-8"},
    ],
)
def test_versions_reject_abbreviated_hashes_coercions_and_inexact_costs(updates):
    with pytest.raises(ValidationError):
        RunVersions(**versions(**updates))


@pytest.mark.parametrize(
    "parameters",
    [
        {"api_key": "do-not-retain"},
        {"token": "do-not-retain"},
        {"authorization": "do-not-retain"},
        {"base_url": "https://unit.example"},
        {"metadata": {}},
        {"prompt": "original prompt"},
        {"temperature": float("nan")},
        {"temperature": float("inf")},
        {"temperature": True},
        {"temperature": "0.1"},
        {"temperature": {"nested": 0}},
        {"thinking": {"enabled": True}},
        {"thinking": "arbitrary secret"},
        {"response_format": {"type": "json_object"}},
        {"response_format": "arbitrary"},
        {"max_tokens": 0},
        {"max_output_tokens": 1.5},
        {"seed": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": -1},
    ],
)
def test_parameters_reject_secrets_unknown_fields_nested_values_and_nonfinite(parameters):
    with pytest.raises(ValidationError):
        RunVersions(**model_versions(parameters=parameters))


def test_known_scalar_parameters_and_decimal_money_are_preserved():
    parameters = {
        "temperature": 0.25,
        "max_tokens": 512,
        "max_output_tokens": 1024,
        "top_p": 0.9,
        "presence_penalty": 0,
        "frequency_penalty": -0.5,
        "seed": -1,
        "timeout_seconds": 30.5,
        "thinking": "enabled",
        "response_format": "json_object",
    }
    metadata = RunVersions(**model_versions(parameters=parameters, estimated_cost_cny="0.00000001"))
    assert metadata.parameters == parameters
    assert metadata.estimated_cost_cny == "0.00000001"


def test_mutated_model_instances_are_revalidated():
    expected = RunVersions(**versions())
    observed = RunVersions(**versions())
    observed.model_calls = 1
    assert codes(verify_run_versions(expected, observed)) == {"invalid_run_versions"}


def test_invalid_expectation_cannot_be_used_as_an_automatic_baseline():
    expected = versions()
    del expected["code_sha"]
    report = verify_run_versions(expected, versions())
    assert report["status"] == "FAIL"
    assert report["failure_ids"] == ["expected.code_sha"]
