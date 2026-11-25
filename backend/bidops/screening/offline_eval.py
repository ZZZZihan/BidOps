"""Calibrate B01 on explicitly synthetic records, without providers or service access."""

import argparse
import asyncio
import hashlib
import json
import subprocess
import tomllib
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from pydantic_evals import Case, Dataset

from bidops.screening.eval_protocol import records_digest, validate_dataset_gate
from bidops.screening.evaluation import (
    SCORER_VERSION,
    GoldTask,
    ScreeningEvaluator,
    score_screening,
)
from bidops.screening.freeze import FreezeManifest, RunVersions, verify_freeze

PROTOCOL_ID = "bidops-enterprise-evaluation"
PROTOCOL_VERSION = "1"
RUNTIME_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = "evals/enterprise-v1/engineering-fixture.json"
PROTOCOL_PATH = "evals/enterprise-v1/protocol.json"
REQUIRED_FROZEN_PATHS = frozenset(
    {
        "backend/bidops/screening/schemas.py",
        "backend/bidops/screening/eval_protocol.py",
        "backend/bidops/screening/evaluation.py",
        "backend/bidops/screening/freeze.py",
        "backend/bidops/screening/offline_eval.py",
        "scripts/screening_eval.py",
        "backend/bidops/models.py",
        "backend/bidops/__init__.py",
        "backend/bidops/screening/__init__.py",
        "pyproject.toml",
        "uv.lock",
        FIXTURE_PATH,
        PROTOCOL_PATH,
    }
)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _json_bytes(raw):
    return json.loads(raw, object_pairs_hook=_unique_object)


def _git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def run_fixture(root: Path, manifest_path: Path, manifest_pin: str, input_pin: str) -> dict:
    """Verify a fixed artifact closure and score UNIT output, never execute an Agent.

    B08 must bind independent gold, actual model/provider records and real human
    provenance separately. This function always leaves business acceptance blocked.
    """
    root = root.resolve()
    if root != RUNTIME_ROOT:
        raise ValueError("freeze_root_must_match_executed_code")
    started_at = datetime.now(UTC)
    frozen = verify_freeze(root, manifest_path, manifest_pin)
    if frozen["status"] != "PASS":
        return {
            "status": "FAIL",
            "freeze": frozen,
            "agent_score": None,
            "business_acceptance": "BLOCKED",
        }
    path = manifest_path if manifest_path.is_absolute() else root / manifest_path
    manifest = FreezeManifest.model_validate(_json_bytes(path.read_bytes()))
    entries = {entry.path: entry.sha256 for entry in manifest.files}
    missing = sorted(REQUIRED_FROZEN_PATHS - entries.keys())
    if (
        missing
        or manifest.protocol_id != PROTOCOL_ID
        or manifest.protocol_version != PROTOCOL_VERSION
        or manifest.frozen_at > started_at
    ):
        return {
            "status": "FAIL",
            "freeze": frozen,
            "agent_score": None,
            "business_acceptance": "BLOCKED",
            "issues": [{"code": "incomplete_or_wrong_protocol_freeze", "ids": missing}],
        }
    raw = (root / FIXTURE_PATH).read_bytes()
    data_sha = hashlib.sha256(raw).hexdigest()
    if data_sha != input_pin or entries[FIXTURE_PATH] != data_sha:
        return {
            "status": "FAIL",
            "freeze": frozen,
            "agent_score": None,
            "business_acceptance": "BLOCKED",
            "issues": [{"code": "input_pin_mismatch", "ids": [FIXTURE_PATH]}],
        }
    payload = _json_bytes(raw)
    if (
        payload.get("purpose") != "engineering_fixture"
        or any(
            r.get("provenance_kind") != "engineering_fixture" for r in payload["dataset_records"]
        )
        or payload.get("annotations")
        or payload.get("contributors")
    ):
        raise ValueError("offline_calibration_requires_explicit_engineering_fixture")
    protocol = _json_bytes((root / PROTOCOL_PATH).read_bytes())
    if protocol["scorer_version"] != SCORER_VERSION:
        raise ValueError("protocol_scorer_version_mismatch")
    dependencies = {
        name: version(name) for name in ("pydantic", "pydantic-evals", "pydantic-ai-slim")
    }
    locked = {
        p["name"]: p["version"] for p in tomllib.loads((root / "uv.lock").read_text())["package"]
    }
    if any(locked.get(name) != installed for name, installed in dependencies.items()):
        raise ValueError("runtime_dependency_version_mismatch")
    tasks = [GoldTask.model_validate(task) for task in payload["gold_tasks"]]
    profiles = [t.inputs.profile_revision.model_dump(mode="json") for t in tasks]
    profile_sha = hashlib.sha256(json.dumps(profiles, sort_keys=True).encode()).hexdigest()
    versions = RunVersions(
        code_sha=_git(root, "rev-parse", "HEAD"),
        data_sha256=data_sha,
        profile_sha256=profile_sha,
        prompt_version="not_invoked:scorer_fixture",
        scorer_version=SCORER_VERSION,
        actual_model_id=None,
        execution_kind="scorer_fixture",
        model_calls=0,
        input_tokens=0,
        output_tokens=0,
        estimated_cost_cny="0",
        parameters={},
    )
    scored = score_screening(tasks, payload["observed_tasks"], payload["evidence"])
    by_id = {}
    registered_ids = {task.task_id for task in tasks}
    for observed in payload["observed_tasks"]:
        if (
            isinstance(observed, dict)
            and isinstance(observed.get("task_id"), str)
            and (observed["task_id"] in registered_ids)
        ):
            by_id.setdefault(observed["task_id"], observed)
    dataset = Dataset(
        name="B01 synthetic scorer calibration",
        cases=[Case(name=t.task_id, inputs=t) for t in tasks],
        evaluators=[ScreeningEvaluator(evidence=payload["evidence"])],
    )
    framework = asyncio.run(
        dataset.evaluate(
            lambda task: by_id.get(task.task_id, {"task_id": task.task_id, "state": "missing"}),
            progress=False,
        )
    )
    assertions = {
        case.name: {name: value.value for name, value in case.assertions.items()}
        for case in framework.cases
    }
    framework_pass = not framework.failures and all(
        all(values.values()) for values in assertions.values()
    )
    data_gate = validate_dataset_gate(
        payload["dataset_records"],
        {
            "purpose": "engineering_fixture",
            "seed": protocol["sampling_seed"],
            "candidate_pool_sha256": records_digest([]),
            "selected_record_ids": [],
        },
    )
    # A second verification detects files changed while scoring. Public reports
    # contain IDs and counts, not copies of raw notice/company text.
    end_freeze = verify_freeze(root, manifest_path, manifest_pin)
    status = (
        "PASS"
        if (scored["status"] == "PASS" and framework_pass and end_freeze["status"] == "PASS")
        else "FAIL"
    )
    return {
        "status": status,
        "purpose": "engineering_fixture",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "versions": versions.model_dump(mode="json"),
        "source_tree_dirty": bool(_git(root, "status", "--porcelain")),
        "dependencies": dependencies,
        "freeze": end_freeze,
        "scorer": scored,
        "pydantic_evals": {
            "cases": len(framework.cases),
            "failures": len(framework.failures),
            "assertions": assertions,
        },
        "dataset_gate": data_gate,
        "human_trials": {
            "status": "BLOCKED",
            "registered": 0,
            "reason": "No actual independent human observations supplied.",
        },
        "agent_score": None,
        "business_acceptance": "BLOCKED",
        "limits": [
            "Synthetic oracle agreement calibrates the scorer, not an Agent.",
            "B08 real data/gold/model/human evidence and B10 acceptance remain required.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--freeze", type=Path, default=Path("evals/enterprise-v1/freeze.json"))
    parser.add_argument("--expected-freeze-sha256", required=True)
    parser.add_argument("--expected-input-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_fixture(
            args.root, args.freeze, args.expected_freeze_sha256, args.expected_input_sha256
        )
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        # Avoid emitting input content, validation excerpts, or local secret paths.
        result = {
            "status": "FAIL",
            "issues": [{"code": "invalid_fixture_or_environment"}],
            "agent_score": None,
            "business_acceptance": "BLOCKED",
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {"status": result["status"], "agent_score": None, "business_acceptance": "BLOCKED"}
        )
    )
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
