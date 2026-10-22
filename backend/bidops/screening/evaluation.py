"""Count-based enterprise evaluation against a frozen, independently reviewed oracle.

Predictions remain raw JSON: invalid schema output must not disappear from quality
denominators or conceal a dangerous 'satisfied' claim. Evidence support comes from
the oracle, never from a model's self-reported confidence or support flag.
"""

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import ConfigDict, Field, StrictBool, ValidationError, model_validator
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from bidops.models import StrictModel
from bidops.screening.schemas import (
    AssessmentInputs,
    Condition,
    EvidenceRef,
    ExecutionStatus,
    NonBlank,
    QualificationStatus,
    ScreeningAssessment,
)

SCORER_VERSION = "enterprise-scorer-v1"
DEFINITE = {"satisfied", "not_satisfied", "not_applicable"}
P1_FIELDS = (
    "project_code",
    "source_url",
    "title",
    "buyer_name",
    "budget",
    "budget_unit",
    "bid_deadline_at",
    "notice_kind",
    "eligibility_excerpts",
)


class EvaluationModel(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, revalidate_instances="always")


class EvidenceRecord(EvaluationModel):
    reference: EvidenceRef
    source_text: str
    readable: StrictBool
    current: StrictBool

    @model_validator(mode="after")
    def text_integrity(self):
        ref = self.reference
        if hashlib.sha256(self.source_text.encode()).hexdigest() != ref.text_sha256:
            raise ValueError("oracle_text_hash_mismatch")
        if not 0 <= ref.start < ref.end <= len(self.source_text):
            raise ValueError("oracle_evidence_out_of_bounds")
        if self.source_text[ref.start : ref.end] != ref.quote:
            raise ValueError("oracle_quote_mismatch")
        return self


class GoldCondition(EvaluationModel):
    condition: Condition
    status: QualificationStatus
    # Alternative individually sufficient evidence bundles, independently reviewed.
    support_bundles: list[list[NonBlank]] = Field(default_factory=list)
    missing_items: list[NonBlank] = Field(default_factory=list)


class GoldTask(EvaluationModel):
    task_id: NonBlank
    inputs: AssessmentInputs
    package_id: NonBlank
    candidate_required: StrictBool
    terminated: StrictBool = False
    scope: Literal["current", "historical"] = "current"
    conditions: list[GoldCondition] = Field(min_length=1)
    fields: dict[str, Any]

    @model_validator(mode="after")
    def oracle_shape(self):
        ids = [c.condition.condition_id for c in self.conditions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_gold_condition")
        if set(self.fields) != set(P1_FIELDS):
            raise ValueError("oracle_requires_all_p1_fields")
        for name, value in self.fields.items():
            if value is not None and not same_field(name, value, value):
                raise ValueError(f"invalid_gold_field:{name}")
        for row in self.conditions:
            if row.status != "information_insufficient" and not row.support_bundles:
                raise ValueError("definite_gold_requires_reviewed_evidence_support")
            if row.status == "information_insufficient" and not row.missing_items:
                raise ValueError("insufficient_gold_requires_missing_items")
        return self


def metric(
    numerator: int,
    denominator: int,
    threshold: float | None = None,
    *,
    maximum=False,
    failure_ids=(),
) -> dict:
    value = numerator / denominator if denominator else None
    status = "BLOCKED" if value is None else "PASS"
    if value is not None and threshold is not None:
        if (value > threshold) if maximum else (value < threshold):
            status = "FAIL"
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": value,
        "status": status,
        "failure_ids": sorted(set(failure_ids)),
    }


def _money(value: Any) -> tuple[Decimal, str]:
    if not isinstance(value, dict) or set(value) != {"amount", "currency", "unit"}:
        raise ValueError("money_requires_amount_currency_unit")
    if not isinstance(value["amount"], str) or value["currency"] != "CNY":
        raise ValueError("money_requires_decimal_string_and_currency")
    amount = Decimal(value["amount"])
    if not amount.is_finite() or amount < 0 or value["unit"] not in {"元", "万元"}:
        raise ValueError("invalid_money")
    parts = amount.as_tuple()
    normalized = Decimal(
        (parts.sign, parts.digits, parts.exponent + (4 if value["unit"] == "万元" else 0))
    )
    return normalized, value["currency"]


def same_field(name: str, expected: Any, actual: Any) -> bool:
    """Known-field equality. Unknown labels are handled separately from accuracy."""
    if expected is None or actual is None:
        return False
    try:
        if name == "budget":
            return _money(expected) == _money(actual)
        if name == "bid_deadline_at":
            if not isinstance(expected, str) or not isinstance(actual, str):
                return False
            left, right = datetime.fromisoformat(expected), datetime.fromisoformat(actual)
            return left.utcoffset() is not None and right.utcoffset() is not None and left == right
        if name == "eligibility_excerpts":
            return (
                isinstance(actual, list)
                and isinstance(expected, list)
                and all(isinstance(x, str) and x.strip() for x in actual + expected)
                and sorted(actual) == sorted(expected)
            )
        if name == "budget_unit":
            return actual in ("元", "万元") and actual == expected
        return isinstance(actual, str) and bool(actual.strip()) and actual == expected
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        return False


def score_p1_fields(gold: list[GoldTask], predictions: dict[str, dict]) -> dict:
    fields = {}
    for name in P1_FIELDS:
        count = Counter()
        failed = []
        for task in gold:
            prediction = predictions.get(task.task_id, {})
            completed = prediction.get("state") == "completed"
            values = prediction.get("fields", {})
            actual = values.get(name) if completed and isinstance(values, dict) else None
            expected = task.fields[name]
            count["registered"] += 1
            count["known"] += expected is not None
            count["unknown"] += expected is None
            count["explicit"] += actual is not None
            count["covered_known"] += expected is not None and actual is not None
            count["abstained"] += actual is None
            count["correct_unknown"] += expected is None and actual is None and completed
            correct = same_field(name, expected, actual)
            count["correct"] += correct
            if actual is not None and not correct:
                count["incorrect"] += 1
                failed.append(task.task_id)
            if expected is not None and not correct:
                failed.append(task.task_id)
        critical = name in {"bid_deadline_at", "budget_unit"}
        provenance = name in {"project_code", "source_url"}
        fields[name] = {
            "counts": dict(count),
            "precision": metric(
                count["correct"],
                count["explicit"],
                1 if provenance else (0.95 if critical else None),
                failure_ids=failed,
            ),
            "recall": metric(
                count["correct"], count["known"], 1 if provenance else None, failure_ids=failed
            ),
            "coverage": metric(
                count["covered_known"],
                count["known"],
                0.85 if critical else None,
                failure_ids=failed,
            ),
            "abstention": metric(count["abstained"], count["registered"]),
            "unknown_accuracy": metric(count["correct_unknown"], count["unknown"]),
        }
    return fields


def _index(records: list[EvidenceRecord]) -> dict[str, EvidenceRecord]:
    index = {record.reference.evidence_id: record for record in records}
    if len(index) != len(records):
        raise ValueError("duplicate_oracle_evidence_id")
    return index


def _valid_reference(raw: Any, index: dict[str, EvidenceRecord]) -> EvidenceRecord | None:
    try:
        ref = EvidenceRef.model_validate(raw)
    except (ValidationError, TypeError):
        return None
    record = index.get(ref.evidence_id)
    if record is None or not record.readable or ref != record.reference:
        return None
    return record


def _dict_list(value: Any) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _condition_equal(expected: Condition, actual: dict | None) -> bool:
    try:
        return Condition.model_validate(actual) == expected
    except (ValidationError, TypeError):
        return False


def _refs(row: dict) -> list:
    notice, company = row.get("notice_evidence", []), row.get("company_evidence", [])
    return notice + company if isinstance(notice, list) and isinstance(company, list) else [None]


def _is_definite(status: Any) -> bool:
    return isinstance(status, str) and status in DEFINITE


def score_screening(
    gold_tasks: list[GoldTask | dict],
    observed_tasks: list[dict],
    evidence: list[EvidenceRecord | dict],
) -> dict:
    """Score every pre-registered task, including missing/invalid/failed outputs.

    This reports engineering/recorded-output quality, not independent acceptance:
    corpus permissions, human provenance and blinded sampling are separate gates.
    """
    gold = [GoldTask.model_validate(task) for task in gold_tasks]
    index = _index([EvidenceRecord.model_validate(row) for row in evidence])
    ids = [task.task_id for task in gold]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate_registered_task")
    for task in gold:
        versions = {
            "notice": {task.inputs.notice_revision.key()},
            "attachment": {v.key() for v in task.inputs.attachment_revisions},
            "company_fact": {task.inputs.profile_revision.key()},
            "company_material": {v.key() for v in task.inputs.material_revisions},
        }
        for row in task.conditions:
            for ref in row.condition.notice_evidence:
                if _valid_reference(ref.model_dump(), index) is None:
                    raise ValueError("unresolved_gold_notice_evidence")
            for bundle in row.support_bundles:
                if not bundle or len(bundle) != len(set(bundle)) or not set(bundle) <= index.keys():
                    raise ValueError("unresolved_gold_support_bundle")
                if {index[key].reference.side for key in bundle} != {"notice", "company"}:
                    raise ValueError("gold_support_requires_both_sides")
                if not set(bundle) & {r.evidence_id for r in row.condition.notice_evidence}:
                    raise ValueError("gold_support_requires_condition_notice")
                for key in bundle:
                    ref = index[key].reference
                    if ref.version.key() not in versions[ref.object_kind]:
                        raise ValueError("gold_support_version_not_in_inputs")
    outputs = {}
    protocol_errors = []
    rejected_attempts = []
    for position, raw in enumerate(observed_tasks):
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("task_id"), str)
            or raw.get("task_id") not in ids
        ):
            protocol_errors.append(f"unregistered_output:{position}")
            continue
        task_id = raw["task_id"]
        if task_id in outputs:
            protocol_errors.append(f"duplicate_output:{task_id}")
            rejected_attempts.append(raw)
            continue  # Retain the first attempt; never cherry-pick a successful retry.
        outputs[task_id] = raw
    counts = Counter()
    failures: dict[str, list[str]] = {}
    rows = []
    expected_families, shown_families = set(), set()

    def fail(kind: str, identifier: str):
        failures.setdefault(kind, []).append(identifier)

    # Retries cannot replace the pre-registered first result, but dangerous claims
    # in those rejected attempts still belong in the safety audit.
    rejected_audit = []
    for attempt in rejected_attempts:
        task = next(t for t in gold if t.task_id == attempt["task_id"])
        audit = score_screening([task], [attempt], evidence)
        rejected_audit.append(
            {
                "task_id": task.task_id,
                "serious_errors": audit["serious_errors"],
                "version_errors": audit["version_errors"],
                "failure_ids": audit["failure_ids"],
            }
        )
        for kind, identifiers in audit["failure_ids"].items():
            if kind.startswith("hard_false_satisfied") or kind in {
                "hard_veto_bypassed",
                "terminated_recommended",
                "undisclosed_stale_citation",
            }:
                for identifier in identifiers:
                    fail(kind, identifier)

    for task in gold:
        pred = outputs.get(task.task_id, {})
        state = pred.get("state", "missing")
        state = (
            state
            if isinstance(state, str) and state in {*ExecutionStatus.__args__, "missing"}
            else "invalid"
        )
        counts["registered_tasks"] += 1
        counts[f"task_state:{state}"] += 1
        completed = state == "completed"
        raw = pred.get("assessment", {})
        raw = raw if isinstance(raw, dict) else {}
        valid = False
        if completed:
            try:
                parsed = ScreeningAssessment.model_validate(raw)
                valid = parsed.inputs == task.inputs and parsed.target_package_id == task.package_id
            except (ValidationError, TypeError):
                pass
        if not valid:
            fail("invalid_or_incomplete_task", task.task_id)
        counts["completed_tasks"] += completed
        counts["valid_tasks"] += valid
        candidate = completed and pred.get("candidate") is True
        counts["expected_candidates"] += task.candidate_required
        counts["recalled_candidates"] += task.candidate_required and candidate
        if task.candidate_required:
            expected_families.add(task.inputs.project_family_id)
            if candidate:
                shown_families.add(task.inputs.project_family_id)
            else:
                fail("candidate_missed", task.task_id)
        positive = pred.get("positive_recommendation") is True
        if task.terminated:
            counts["terminated_opportunities"] += 1
            if positive:
                fail("terminated_recommended", task.task_id)
        definition_rows = _dict_list(raw.get("conditions"))
        definitions = {
            r.get("condition_id"): r
            for r in definition_rows
            if isinstance(r.get("condition_id"), str)
        }
        judgments = _dict_list(raw.get("assessments"))
        judgment_map = {
            r.get("condition_id"): r for r in judgments if isinstance(r.get("condition_id"), str)
        }
        if len(judgment_map) != len(judgments):
            fail("duplicate_condition_output", task.task_id)
        task_wrong, task_definite = False, False
        expected_ids = {r.condition.condition_id for r in task.conditions}
        for position, referenced_row in enumerate(definition_rows + judgments):
            for ref in _refs(referenced_row):
                counts["citations"] += 1
                record = _valid_reference(ref, index)
                counts["resolvable_citations"] += record is not None
                condition_id = referenced_row.get("condition_id")
                condition_id = (
                    condition_id
                    if isinstance(condition_id, str) and (condition_id in expected_ids)
                    else f"unknown-condition-{position}"
                )
                identifier = f"{task.task_id}:{condition_id}"
                if record is None:
                    fail("invalid_citation", identifier)
                elif task.scope == "current" and not record.current:
                    counts["stale_citations"] += 1
                    if pred.get("stale_disclosed") is not True:
                        fail("undisclosed_stale_citation", identifier)
        for extra in judgments:
            extra_id = extra.get("condition_id")
            if (not isinstance(extra_id, str) or extra_id not in expected_ids) and _is_definite(
                extra.get("status")
            ):
                counts["definite_judgments"] += 1
                counts["wrong_definite_judgments"] += 1
                task_wrong = task_definite = True
                fail("unexpected_definite_condition", task.task_id)
        for row in task.conditions:
            condition = row.condition
            identifier = f"{task.task_id}:{condition.condition_id}"
            actual = judgment_map.get(condition.condition_id, {})
            status = actual.get("status")
            status = (
                status
                if isinstance(status, str) and (status in QualificationStatus.__args__)
                else None
            )
            counts["quality_units"] += 1
            definite = _is_definite(status)
            identified = _condition_equal(condition, definitions.get(condition.condition_id))
            if condition.hard_requirement and row.status != "not_applicable":
                counts["hard_conditions"] += 1
                counts["recalled_hard_conditions"] += completed and identified
                if not completed or not identified:
                    fail("hard_condition_missed_or_changed", identifier)
            if row.status in DEFINITE:
                counts["decidable_conditions"] += 1
                counts["covered_decidable_conditions"] += completed and definite
                if not completed or not definite:
                    fail("decidable_abstained", identifier)
            else:
                counts[f"{row.status}_gold"] += 1
                counts[f"{row.status}_covered"] += completed and status == row.status
                if not completed or status != row.status:
                    fail(f"{row.status}_missed", identifier)
            if condition.hard_requirement and row.status in {
                "not_satisfied",
                "information_insufficient",
                "evidence_conflict",
            }:
                counts[f"serious_opportunities:{row.status}"] += 1
                if any(
                    j.get("condition_id") == condition.condition_id
                    and j.get("status") == "satisfied"
                    for j in judgments
                ):
                    fail(f"hard_false_satisfied:{row.status}", identifier)
                if positive:
                    fail("hard_veto_bypassed", identifier)
            refs = _refs(actual)
            valid_records = [
                record for ref in refs if (record := _valid_reference(ref, index)) is not None
            ]
            supplied = {
                r.reference.evidence_id
                for r in valid_records
                if r.current or task.scope == "historical"
            }
            if status == "evidence_conflict":
                selected = actual.get("conflicting_evidence_ids", [])
                selected = (
                    {s for s in selected if isinstance(s, str)}
                    if isinstance(selected, list)
                    else set()
                )
                supplied &= selected | {
                    r.reference.evidence_id for r in valid_records if r.reference.side == "notice"
                }
            references_valid = bool(refs) and len(valid_records) == len(refs)
            supported = (
                status == row.status
                and bool(row.support_bundles)
                and any(set(bundle) <= supplied for bundle in row.support_bundles)
                and references_valid
            )
            if status == "information_insufficient":
                missing = actual.get("missing_items")
                supported = (
                    status == row.status
                    and references_valid
                    and isinstance(missing, list)
                    and all(isinstance(item, str) for item in missing)
                    and set(missing) == set(row.missing_items)
                    and {r.evidence_id for r in condition.notice_evidence} <= supplied
                )
            if status in {
                "satisfied",
                "not_satisfied",
                "not_applicable",
                "evidence_conflict",
                "information_insufficient",
            }:
                counts["evidence_judgments"] += 1
                counts["supported_judgments"] += supported
                if not supported:
                    fail("unsupported_evidence", identifier)
            if definite:
                task_definite = True
                counts["definite_judgments"] += 1
                wrong = status != row.status or not identified or not supported or not valid
                counts["wrong_definite_judgments"] += wrong
                task_wrong |= wrong
                if wrong:
                    fail("wrong_definite_judgment", identifier)
            correct = (
                valid and identified and status == row.status and references_valid and supported
            )
            counts["correct_quality_units"] += correct
            if not correct:
                fail("incorrect_quality_unit", identifier)
            rows.append(
                {
                    "task_id": task.task_id,
                    "condition_id": condition.condition_id,
                    "expected": row.status,
                    "observed": status,
                    "identified": identified,
                    "correct": correct,
                    "supported": supported,
                    "task_state": state,
                }
            )
        counts["definite_packages"] += task_definite
        counts["wrong_definite_packages"] += task_wrong

    measures = {
        "candidate_recall_packages": metric(
            counts["recalled_candidates"],
            counts["expected_candidates"],
            0.95,
            failure_ids=failures.get("candidate_missed", []),
        ),
        "candidate_recall_families": metric(len(shown_families), len(expected_families), 0.95),
        "hard_condition_recall": metric(
            counts["recalled_hard_conditions"],
            counts["hard_conditions"],
            0.95,
            failure_ids=failures.get("hard_condition_missed_or_changed", []),
        ),
        "definite_error_rate_conditions": metric(
            counts["wrong_definite_judgments"],
            counts["definite_judgments"],
            0.05,
            maximum=True,
            failure_ids=failures.get("wrong_definite_judgment", []),
        ),
        "definite_error_rate_packages": metric(
            counts["wrong_definite_packages"], counts["definite_packages"], 0.05, maximum=True
        ),
        "decidable_coverage": metric(
            counts["covered_decidable_conditions"],
            counts["decidable_conditions"],
            0.85,
            failure_ids=failures.get("decidable_abstained", []),
        ),
        "citation_resolution": metric(
            counts["resolvable_citations"],
            counts["citations"],
            1,
            failure_ids=failures.get("invalid_citation", []),
        ),
        "evidence_support": metric(
            counts["supported_judgments"],
            counts["evidence_judgments"],
            0.98,
            failure_ids=failures.get("unsupported_evidence", []),
        ),
        "quality": metric(
            counts["correct_quality_units"],
            counts["quality_units"],
            failure_ids=failures.get("incorrect_quality_unit", []),
        ),
        "completion": metric(
            counts["valid_tasks"],
            counts["registered_tasks"],
            failure_ids=failures.get("invalid_or_incomplete_task", []),
        ),
    }
    for status in ("information_insufficient", "evidence_conflict"):
        measures[f"{status}_coverage"] = metric(
            counts[f"{status}_covered"],
            counts[f"{status}_gold"],
            failure_ids=failures.get(f"{status}_missed", []),
        )
    for status in ("not_satisfied", "information_insufficient", "evidence_conflict"):
        failed = failures.get(f"hard_false_satisfied:{status}", [])
        measures[f"serious_false_satisfied:{status}"] = metric(
            len(set(failed)),
            counts[f"serious_opportunities:{status}"],
            0,
            maximum=True,
            failure_ids=failed,
        )
    measures["terminated_recommendation"] = metric(
        len(set(failures.get("terminated_recommended", []))),
        counts["terminated_opportunities"],
        0,
        maximum=True,
        failure_ids=failures.get("terminated_recommended", []),
    )
    severe = sorted(
        {
            identifier
            for kind, values in failures.items()
            if kind.startswith("hard_false_satisfied")
            or kind in {"hard_veto_bypassed", "terminated_recommended"}
            for identifier in values
        }
    )
    stale = sorted(set(failures.get("undisclosed_stale_citation", [])))
    p1 = score_p1_fields(gold, outputs)
    gate_statuses = [m["status"] for m in measures.values()]
    gate_statuses += [
        p1[name][which]["status"]
        for name, which in (
            ("project_code", "recall"),
            ("source_url", "recall"),
            ("project_code", "precision"),
            ("source_url", "precision"),
            ("bid_deadline_at", "precision"),
            ("bid_deadline_at", "coverage"),
            ("budget_unit", "precision"),
            ("budget_unit", "coverage"),
        )
    ]
    if severe or stale or protocol_errors or failures.get("invalid_or_incomplete_task"):
        gate_statuses.append("FAIL")
    status = (
        "FAIL" if "FAIL" in gate_statuses else ("BLOCKED" if "BLOCKED" in gate_statuses else "PASS")
    )
    return {
        "scorer_version": SCORER_VERSION,
        "status": status,
        "counts": dict(counts),
        "metrics": measures,
        "p1_fields": p1,
        "serious_errors": severe,
        "version_errors": stale,
        "failure_ids": failures,
        "protocol_errors": protocol_errors,
        "rejected_attempts": rejected_audit,
        "rows": rows,
        "business_acceptance": "NOT_ASSESSED",
        "limitations": [
            "Oracle support must come from independent review, not model labels.",
            "No source permission, human identity or blinded sampling is proven by this scorer.",
        ],
    }


@dataclass
class ScreeningEvaluator(Evaluator):
    """Pydantic Evals adapter; aggregation still includes every registered task."""

    evidence: list[EvidenceRecord] = field(repr=False)

    def evaluate(self, ctx: EvaluatorContext) -> dict[str, bool | float]:
        report = score_screening([ctx.inputs], [ctx.output], self.evidence)
        return {
            "no_serious_error": not report["serious_errors"],
            "no_version_error": not report["version_errors"],
            "valid_complete_output": report["counts"].get("valid_tasks", 0) == 1,
            "correct_quality_units": float(report["counts"].get("correct_quality_units", 0)),
        }
