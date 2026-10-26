"""Run the notice-QA offline contract suite and save a non-scoring receipt."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from bidops.agent_demo.prompts import prompt_identity
from bidops.agent_demo.runtime import DemoSettings, demo_model_settings, demo_usage_limits

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evals/llm-pilot-v1.json"
TEST_TARGETS = [
    "tests/test_agent_demo.py",
    "tests/test_demo_evaluation.py",
    "tests/test_agent_demo_provenance.py",
    "tests/test_workflow_demo.py::test_screening_admission_counts_existing_qa_failure_and_success",
    "tests/test_llm_pilot.py",
]
FROZEN_INPUTS = ("evals/cases-v1.json", "evals/reference-v1.json")
# The existing backend CI does not build frontend assets; this separate launcher check
# already skips in that environment. No pilot test may be skipped.
OPTIONAL_FRONTEND_CHECK = (
    "tests.test_workflow_demo_launcher::"
    "test_actual_preview_cli_starts_without_external_identity_or_new_fee"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contract_snapshot(root: Path = ROOT) -> dict:
    # Read class defaults only: never load an env file, credential or live database.
    return {
        "prompt": prompt_identity(),
        "model": {
            "provider": "deepseek",
            "configured_model": DemoSettings.model_fields["model_name"].default,
            "version_policy": "provider_alias_not_immutable_version",
            "settings": demo_model_settings(),
            "usage_limits": asdict(demo_usage_limits()),
            "default_run_timeout_seconds": DemoSettings.model_fields["max_run_seconds"].default,
        },
        "frozen_evaluation_inputs": {name: sha(root / name) for name in FROZEN_INPUTS},
    }


def contract_errors(manifest: dict, snapshot: dict) -> list[str]:
    errors = []
    if manifest.get("schema_version") != 1:
        errors.append("schema_version_mismatch")
    if manifest.get("test_targets") != TEST_TARGETS:
        errors.append("test_targets_mismatch")
    expected = manifest.get("contract", {})
    for key, value in snapshot.items():
        if expected.get(key) != value:
            errors.append(f"{key}_mismatch")
    return errors


def test_results(path: Path) -> dict:
    """Never turn missing XML, collection errors or skipped cases into a passing suite."""
    result = {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "cases": []}
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        result["report_error"] = "missing_or_invalid_test_report"
        return result
    for case in root.iter("testcase"):
        state = next(
            (
                name
                for tag, name in [
                    ("failure", "failed"),
                    ("error", "errors"),
                    ("skipped", "skipped"),
                ]
                if case.find(tag) is not None
            ),
            "passed",
        )
        result["total"] += 1
        result[state] += 1
        result["cases"].append(
            {"id": f"{case.get('classname', '')}::{case.get('name', '')}", "state": state}
        )
    if not result["total"]:
        result["report_error"] = "no_tests_collected"
    return result


def suite_passed(returncode: int | None, results: dict, *, full_suite: bool = False) -> bool:
    allowed_skipped = sum(
        case["state"] == "skipped" and full_suite and case["id"] == OPTIONAL_FRONTEND_CHECK
        for case in results["cases"]
    )
    return (
        returncode == 0
        and results["total"] > 0
        and results["passed"] > 0
        and results["passed"] + allowed_skipped == results["total"]
        and not results.get("report_error")
    )


def pilot_case_ids(results: dict) -> list[str]:
    prefixes = []
    for target in TEST_TARGETS:
        path, _, name = target.partition("::")
        prefixes.append(path.removesuffix(".py").replace("/", ".") + "::" + name)
    return sorted(
        case["id"]
        for case in results["cases"]
        if any(case["id"].startswith(prefix) for prefix in prefixes)
    )


def source_identity(root: Path = ROOT) -> dict:
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, timeout=5
    ).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=root, text=True, timeout=5
    ).strip()
    paths = set((root / "backend/bidops/agent_demo").glob("*.py"))
    paths.update(root / name.split("::")[0] for name in TEST_TARGETS)
    paths.update(
        root / name
        for name in (
            "tests/conftest.py",
            "scripts/llm_pilot_check.py",
            "uv.lock",
            "requirements/durable.lock",
            "requirements/attachments.lock",
        )
    )
    return {
        "code_sha": revision,
        "working_tree_dirty": bool(dirty),
        "file_sha256": {str(p.relative_to(root)): sha(p) for p in sorted(paths)},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="New receipt file; never overwrite"
    )
    parser.add_argument("--full-suite", action="store_true", help="Run all backend tests for CI")
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output = args.output.open("x", encoding="utf-8")
    except FileExistsError:
        parser.error("Output already exists; preserve it and choose a new receipt path.")
    receipt = {
        "schema_version": 1,
        "execution_kind": "offline_contract",
        "actual_provider_calls": 0,
        "agent_score": None,
        "business_acceptance": "BLOCKED",
        "started_at": datetime.now(UTC).isoformat(),
        "gate_passed": False,
        "limitations": [
            "Synthetic UNIT inputs and scripted models check server contracts, not model quality.",
            "The 96 notice-derived scenarios are fingerprinted, not executed or rescored here.",
            "No real-user dataset, paid model evaluation or immutable provider model is verified.",
            "No production, migration, human or business acceptance is granted.",
        ],
    }
    with output:
        try:
            manifest = json.loads(MANIFEST.read_text())
            snapshot = contract_snapshot()
            receipt.update(
                {
                    "manifest_sha256": sha(MANIFEST),
                    "contract": snapshot,
                    "contract_errors": contract_errors(manifest, snapshot),
                    "source": source_identity(),
                    "test_targets": TEST_TARGETS,
                }
            )
            # Always run the full fixed suite even if the prompt/config fingerprint drifted.
            with tempfile.TemporaryDirectory(prefix="bidops-llm-pilot-") as temp:
                xml_path = Path(temp) / "results.xml"
                targets = ["tests"] if args.full_suite else TEST_TARGETS
                command = [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "-o",
                    "addopts=",
                    *targets,
                    f"--junitxml={xml_path}",
                ]
                receipt["test_command"] = [
                    "python",
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "-o",
                    "addopts=",
                    *targets,
                    "--junitxml=<temporary-report>",
                ]
                receipt["suite_scope"] = "full_backend" if args.full_suite else "notice_qa_pilot"
                try:
                    process = subprocess.run(
                        command,
                        cwd=ROOT,
                        # Existing hosted full runs take about 22 minutes. Leave three
                        # minutes of the 30-minute CI job for setup and receipt upload.
                        timeout=27 * 60 if args.full_suite else 120,
                        check=False,
                        env={**os.environ, "PYTEST_ADDOPTS": "", "BIDOPS_NETWORK_ENABLED": "false"},
                    )
                    receipt["test_exit_code"] = process.returncode
                except subprocess.TimeoutExpired:
                    receipt["test_exit_code"] = None
                    receipt["runner_error"] = "test_timeout"
                results = test_results(xml_path)
                receipt["tests"] = results
                receipt["pilot_case_set_matches"] = pilot_case_ids(results) == manifest.get(
                    "expected_pilot_case_ids"
                )
                receipt["gate_passed"] = (
                    not receipt["contract_errors"]
                    and receipt["pilot_case_set_matches"]
                    and suite_passed(receipt["test_exit_code"], results, full_suite=args.full_suite)
                )
        except Exception as exc:
            # Error text can contain paths or source data; keep only a bounded type name.
            receipt["runner_error"] = type(exc).__name__
        finally:
            receipt["completed_at"] = datetime.now(UTC).isoformat()
            output.write(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "gate_passed": receipt["gate_passed"],
                "contract_errors": receipt.get("contract_errors", []),
                "pilot_case_set_matches": receipt.get("pilot_case_set_matches", False),
                "tests": {
                    key: value for key, value in receipt.get("tests", {}).items() if key != "cases"
                },
                "receipt": str(args.output),
                "agent_score": None,
            },
            ensure_ascii=False,
        )
    )
    return 0 if receipt["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
