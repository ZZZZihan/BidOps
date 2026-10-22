"""Caller-pinned B08 artifacts, code closure and cumulative cost reconciliation.

These checks establish consistency of retained records. They cannot authenticate
human declarations, a provider invoice, or a deliberately omitted historical run.
No network client or execution service is constructed by this module.
"""

import hashlib
import json
import subprocess
import tomllib
from datetime import UTC, datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StringConstraints, TypeAdapter, model_validator

from bidops.acceptance.evaluation import AuditModel, canonical_sha256
from bidops.screening.freeze import CodeSha, Count, Digest, Identifier, verify_freeze
from bidops.screening.offline_eval import REQUIRED_FROZEN_PATHS

FREEZE_PIN = "22a2adf96f07b9a3e9c1493dae2cebbe2499b6e05db21f2b52855686c2afffea"
ROOT = Path(__file__).resolve().parents[3]
MAX_ARTIFACT_BYTES = 50_000_000
# Twelve decimal places exceed current provider price precision. Bounded decimal
# inputs prevent context rounding at the strict ten-yuan boundary.
Cost = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9]{1,8}(?:\.[0-9]{1,12})?$")]


def money(value):
    return Decimal(TypeAdapter(Cost).validate_python(value))


class RunManifest(AuditModel):
    schema_version: Literal["independent-evaluation-v1"]
    run_id: Identifier
    purpose: Literal["engineering_fixture", "independent_acceptance"]
    frozen_at: AwareDatetime
    execution_started_at: AwareDatetime
    execution_finished_at: AwareDatetime
    evaluation_code_sha: CodeSha
    candidate_code_sha: CodeSha
    candidate_implementation_sha256: Digest
    lock_sha256: Digest
    runtime_closure_sha256: Digest
    input_sha256: Digest
    collection_sha256: Digest | None
    ledger_sha256: Digest | None
    registration_sha256: Digest | None
    prompt_version: Identifier
    prompt_sha256: Digest
    rule_version: Identifier
    parameters_sha256: Digest | None

    @model_validator(mode="after")
    def ordered(self):
        if not self.frozen_at <= self.execution_started_at <= self.execution_finished_at:
            raise ValueError("run_times_not_ordered")
        return self


class LedgerEntry(AuditModel):
    entry_id: Identifier
    execution_id: Identifier | None
    occurred_at: AwareDatetime
    model_calls: Count
    input_tokens: Count
    output_tokens: Count
    estimated_cost_cny: Cost
    # Unknown/in-flight requests retain their complete reservation in this field.
    reserved_cost_cny: Cost
    unknown_calls: Count
    provider_record_sha256: Digest | None


class CumulativeLedger(AuditModel):
    schema_version: Literal["cumulative-model-cost-v1"]
    scope: Literal["all_bidops_development_and_evaluation"]
    currency: Literal["CNY"]
    limit_cny: Literal["10"]
    opening_spent_cny: Cost
    opening_reserved_cny: Cost
    opening_record_sha256: Digest
    reconciled_at: AwareDatetime
    complete_through: AwareDatetime
    independently_reconciled_by: Identifier
    entries: list[LedgerEntry] = Field(max_length=100_000)

    @model_validator(mode="after")
    def unique(self):
        if len({r.entry_id for r in self.entries}) != len(self.entries):
            raise ValueError("duplicate_ledger_entry")
        if self.complete_through > self.reconciled_at or any(
            r.occurred_at > self.complete_through for r in self.entries
        ):
            raise ValueError("inconsistent_ledger_time")
        if any(r.unknown_calls > r.model_calls for r in self.entries):
            raise ValueError("invalid_unknown_call_count")
        return self


def pinned_json(path, expected_sha256):
    """Read once, pin bytes, reject duplicate keys/NaN; exceptions reveal no input."""
    try:
        path = Path(path)
        with path.open("rb") as stream:
            raw = stream.read(MAX_ARTIFACT_BYTES + 1)
        if len(raw) > MAX_ARTIFACT_BYTES or hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError

        def unique(pairs):
            out = {}
            for key, value in pairs:
                if key in out:
                    raise ValueError
                out[key] = value
            return out

        def invalid(_):
            raise ValueError

        return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    except (OSError, ValueError, TypeError, RecursionError):
        raise ValueError("pinned_artifact_unavailable_or_invalid") from None


def git(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL, timeout=10
    ).strip()


def runtime_closure(root):
    """All executable Python plus lock overlays; additions cannot evade the pin."""
    root = Path(root).resolve()
    files = set((root / "backend" / "bidops").rglob("*.py"))
    files.update((root / "scripts").rglob("*.py"))
    files.update((root / "requirements").glob("*.lock"))
    files.update(root / name for name in ("uv.lock", "pyproject.toml"))
    result = {}
    for path in sorted(files):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("runtime_symlink_not_supported")
        result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def check_environment(manifest, root=ROOT):
    root = Path(root).resolve()
    issues = []
    if root != ROOT:
        raise ValueError("runtime_root_mismatch")
    freeze = verify_freeze(root, "evals/enterprise-v1/freeze.json", FREEZE_PIN)
    if freeze["status"] != "PASS":
        issues.append("b01_freeze_invalid")
    else:
        frozen = json.loads((root / "evals/enterprise-v1/freeze.json").read_bytes())
        if not REQUIRED_FROZEN_PATHS <= {r["path"] for r in frozen["files"]}:
            issues.append("b01_freeze_incomplete")
    if manifest.execution_finished_at > datetime.now(UTC):
        issues.append("execution_finished_in_future")
    if git(root, "rev-parse", "HEAD") != manifest.evaluation_code_sha:
        issues.append("evaluation_commit_mismatch")
    # A retained historical candidate may precede the audit code. Every runtime
    # file present in that commit must still have its exact committed contents;
    # new audit modules do not rewrite the evaluated implementation.
    git(root, "cat-file", "-e", manifest.candidate_code_sha + "^{commit}")
    candidate_files = git(
        root,
        "ls-tree",
        "-r",
        manifest.candidate_code_sha,
        "backend/bidops",
        "scripts",
        "requirements",
        "uv.lock",
        "pyproject.toml",
    )
    candidate_count = 0
    for line in candidate_files.splitlines():
        metadata, name = line.split("\t", 1)
        if not (name.endswith((".py", ".lock")) or name == "pyproject.toml"):
            continue
        mode, kind, blob = metadata.split()
        path = root / name
        candidate_count += 1
        if mode == "120000" or kind != "blob" or path.is_symlink() or not path.is_file():
            issues.append("candidate_runtime_file_unavailable")
            continue
        raw = path.read_bytes()
        current_blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if current_blob != blob:
            issues.append("candidate_runtime_file_changed")
    if not candidate_count:
        issues.append("candidate_runtime_empty")
    if git(root, "status", "--porcelain", "--untracked-files=normal"):
        issues.append("uncommitted_evaluation_tree")
    if canonical_sha256(runtime_closure(root)) != manifest.runtime_closure_sha256:
        issues.append("runtime_closure_mismatch")
    if hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest() != manifest.lock_sha256:
        issues.append("lock_mismatch")
    from bidops.qualification.prompts import (
        ASSESSMENT_INSTRUCTIONS,
        EXTRACTION_INSTRUCTIONS,
        PROMPT_VERSION,
        RULES_VERSION,
    )
    from bidops.qualification.service import implementation_version
    from bidops.qualification.sources import digest

    implementation = implementation_version()
    if implementation["implementation_sha256"] != manifest.candidate_implementation_sha256:
        issues.append("candidate_implementation_mismatch")
    if digest([EXTRACTION_INSTRUCTIONS, ASSESSMENT_INSTRUCTIONS]) != manifest.prompt_sha256:
        issues.append("prompt_hash_mismatch")
    if manifest.prompt_version != PROMPT_VERSION or manifest.rule_version != RULES_VERSION:
        issues.append("prompt_or_rule_version_mismatch")
    locked = {
        p["name"]: p["version"] for p in tomllib.loads((root / "uv.lock").read_text())["package"]
    }
    dependencies = {
        name: version(name) for name in ("pydantic", "pydantic-evals", "pydantic-ai-slim")
    }
    if any(locked.get(name) != value for name, value in dependencies.items()):
        issues.append("runtime_dependency_mismatch")
    return {
        "status": "FAIL" if issues else "PASS",
        "issues": [{"code": code, "status": "FAIL"} for code in issues],
        "freeze": freeze,
        "dependencies": dependencies,
        "runtime_files": len(runtime_closure(root)),
        "candidate_runtime_files_verified": candidate_count,
    }


def reconcile_ledger(payload, executions, finished_at):
    """Deduplicated execution receipts reconcile to the whole cumulative budget."""
    if payload is None:
        return {
            "status": "BLOCKED",
            "issues": [{"code": "cumulative_ledger_missing", "status": "BLOCKED"}],
        }
    ledger = CumulativeLedger.model_validate(payload)
    issues = []
    if ledger.complete_through < finished_at:
        issues.append("ledger_does_not_cover_run")
    if ledger.reconciled_at > datetime.now(UTC):
        issues.append("ledger_reconciled_in_future")
    ids = [item["execution_id"] for item in executions]
    if len(set(ids)) != len(ids):
        issues.append("duplicate_execution_receipt")
    spent = Decimal(ledger.opening_spent_cny) + sum(
        (Decimal(row.estimated_cost_cny) for row in ledger.entries), Decimal(0)
    )
    reserved = Decimal(ledger.opening_reserved_cny) + sum(
        (Decimal(row.reserved_cost_cny) for row in ledger.entries), Decimal(0)
    )
    if spent + reserved > Decimal("10"):
        issues.append("cumulative_budget_exceeded")
    for item in executions:
        usage = item["usage"]
        rows = [row for row in ledger.entries if row.execution_id == item["execution_id"]]
        for name in ("model_calls", "input_tokens", "output_tokens"):
            if sum(getattr(row, name) for row in rows) != usage.get(name):
                issues.append(f"ledger_{name}_mismatch")
        if usage.get("model_calls", 0) and not rows:
            issues.append("execution_missing_from_ledger")
        for name in ("estimated_cost_cny", "reserved_cost_cny"):
            expected = Decimal(str(usage.get(name, "0")))
            actual = sum((Decimal(getattr(row, name)) for row in rows), Decimal(0))
            if actual < expected:
                issues.append(f"ledger_{name}_underreported")
        if sum(row.unknown_calls for row in rows) < usage.get("unknown_calls", 0):
            issues.append("unknown_calls_underreported")
    if any(row.unknown_calls and Decimal(row.reserved_cost_cny) <= 0 for row in ledger.entries):
        issues.append("unknown_cost_released")
    return {
        "status": "FAIL" if issues else "PASS",
        "issues": [{"code": code, "status": "FAIL"} for code in sorted(set(issues))],
        "cumulative_spent_cny": str(spent),
        "cumulative_reserved_cny": str(reserved),
        "remaining_after_reservations_cny": str(Decimal("10") - spent - reserved),
        "balance_as_of": ledger.complete_through.isoformat(),
        "reconciled_at": ledger.reconciled_at.isoformat(),
        "latest_ledger_verified": False,
        "new_model_calls_authorized": False,
        "entries": len(ledger.entries),
        "authentication": "External identity and provider records still require verification.",
    }
