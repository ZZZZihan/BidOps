"""Offline B01 dataset, independent-annotation and paired-review contracts.

These checks audit supplied records; they cannot authenticate a person's identity,
source permission or blindness declaration. Engineering fixtures never count as real
independent data. No model, storage, network or workflow runtime is used here.
"""

import hashlib
import json
import random
import statistics
from collections import Counter, defaultdict
from datetime import UTC
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import AwareDatetime, ConfigDict, Field, StringConstraints, ValidationError

from bidops.models import StrictModel

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Risk = Literal[
    "hard_disqualification",
    "missing_qualification_evidence",
    "conflicting_evidence",
    "qualification_amendment",
    "terminated",
    "attachment_dependency",
]
RISKS = tuple(Risk.__args__)
Purpose = Literal["engineering_fixture", "independent_acceptance"]


class ProtocolModel(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, revalidate_instances="always")


class DatasetRecord(ProtocolModel):
    record_id: Identifier
    split: Literal["dev", "regression", "acceptance"]
    family_id: Identifier
    notice_id: Identifier
    source_url: Identifier
    raw_sha256: Digest
    text_sha256: Digest
    project_code: Identifier | None = None
    version_chain_id: Identifier | None = None
    related_notice_ids: list[Identifier] = Field(default_factory=list)
    # Package/attachment IDs are stable, globally scoped identities, not local labels.
    package_ids: list[Identifier] = Field(default_factory=list)
    attachment_ids: list[Identifier] = Field(default_factory=list)
    attachment_sha256s: list[Digest] = Field(default_factory=list)
    provenance_kind: Literal["real", "engineering_fixture", "legacy_demo"] = "engineering_fixture"
    source_permission: Literal["allowed", "denied", "unknown"] = "unknown"
    notice_category: Identifier | None = None
    layout: Identifier | None = None
    captured_at: AwareDatetime | None = None
    deadline_at: AwareDatetime | None = None
    risk_types: list[Risk] = Field(default_factory=list)


class LegacyIdentity(ProtocolModel):
    project_code: Identifier | None = None
    source_url: Identifier | None = None
    raw_sha256: Digest | None = None


# Source-reviewed historical demo corpus, never independent acceptance data.
LEGACY_NOTICES = (
    LegacyIdentity(
        project_code="TGPC-2026-A-0384",
        source_url="https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261016_27510682.htm",
        raw_sha256="48adb3f1e4a7495a0d7edb9689716589ea1d963009cedbaca8ad052674f75c7b",
    ),
    LegacyIdentity(
        project_code="TGPC-2026-D-1131",
        source_url="https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261019_27520096.htm",
        raw_sha256="583b8ccd74f955d114704ec2b6bf497d69776f7d71f1e24822b1ba39ff9aec0d",
    ),
    LegacyIdentity(
        project_code="TJBH-2026-D-0170",
        source_url="https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261019_27520100.htm",
        raw_sha256="27b3d5a8a6c28411af9f9ca3fa8a2811f50dd5c6c836cf81ad3ec1765d86477c",
    ),
    LegacyIdentity(
        project_code="TGPC-2026-A-0343",
        source_url="https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261016_27510616.htm",
        raw_sha256="792f9037ffaf37ba0b52585ccd2e067a51c43c483c66afa0377b2d91a5d500ca",
    ),
    LegacyIdentity(
        project_code="PYGP-2026-A-0826",
        source_url="https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261018_27513837.htm",
        raw_sha256="cdd130977fb2a9e27eaafaf3b68dfb99a4f8f897d9b75c2822c2ad03c5231fcf",
    ),
    LegacyIdentity(
        project_code="TGPC-2026-A-0324",
        source_url="https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202609/t20260918_27358274.htm",
        raw_sha256="0d050a9ffe5e6ee634dccb80ac87b8da443bd931cf41b9eb9f746a09c54a43a9",
    ),
)


class Contributor(ProtocolModel):
    person_id: Identifier
    actual_human: bool = False
    independent: bool = False
    developer_or_tuner: bool = False
    attestation_ref: Identifier | None = None


class AnnotationRecord(ProtocolModel):
    record_id: Identifier
    family_id: Identifier
    input_sha256: Digest
    source_sha256: Digest
    gold_sha256: Digest
    quality_unit_ids: list[Identifier] = Field(min_length=1)
    purpose: Literal["engineering_fixture", "real_independent"] = "engineering_fixture"
    source_available: bool = False
    source_refs: list[Identifier] = Field(default_factory=list)
    annotator_id: Identifier | None = None
    reviewer_id: Identifier | None = None
    annotated_at: AwareDatetime | None = None
    reviewed_at: AwareDatetime | None = None
    critical: bool = True
    model_preannotated: bool = False
    disagreement: bool = False
    arbitrator_id: Identifier | None = None
    arbitrated_at: AwareDatetime | None = None
    arbitration_resolution: Identifier | None = None


class SamplingPlan(ProtocolModel):
    purpose: Purpose = "engineering_fixture"
    method: Literal["stratified_family_random_v1"] = "stratified_family_random_v1"
    seed: int
    candidate_pool_sha256: Digest
    selected_record_ids: list[Identifier]
    # The original P1 future-at-capture target is conditional on availability.
    future_availability_exception_ref: Identifier | None = None
    future_available_count: int | None = Field(default=None, ge=0)
    future_availability_evidence_sha256: Digest | None = None


class TrialAssignment(ProtocolModel):
    task_id: Identifier
    pair_id: Identifier
    family_id: Identifier
    mode: Literal["manual", "assisted"]
    operator_id: Identifier
    input_sha256: Digest
    gold_record_id: Identifier
    gold_sha256: Digest
    deliverable_sha256: Digest
    quality_unit_ids: list[Identifier] = Field(min_length=1)
    time_limit_seconds: float = Field(gt=0)
    order: int = Field(ge=1)
    blind_to_gold: bool = False
    previously_performed: bool = True


class TrialPlan(ProtocolModel):
    purpose: Purpose = "engineering_fixture"
    frozen_at: AwareDatetime
    randomization_seed: int
    allocation_method: Literal["randomized_balanced"] = "randomized_balanced"
    allocation_record_ref: Identifier
    start_rule: Identifier
    stop_rule: Identifier
    assignments: list[TrialAssignment]


class TrialObservation(ProtocolModel):
    task_id: Identifier
    status: Literal["completed", "failed", "timed_out", "cancelled", "interrupted"]
    started_at: AwareDatetime
    stopped_at: AwareDatetime
    active_seconds: float = Field(ge=0)
    waiting_seconds: float = Field(ge=0)
    correct_unit_ids: list[Identifier] = Field(default_factory=list)
    severe_error_ids: list[Identifier] = Field(default_factory=list)
    failure_reason: Identifier | None = None


def _issue(issues, code, ids=(), status="FAIL", **details):
    issues.append({"code": code, "ids": sorted(set(ids)), "status": status, **details})


def _report(issues, counts=None, **extra):
    status = "FAIL" if any(i["status"] == "FAIL" for i in issues) else "PASS"
    if status == "PASS" and issues:
        status = "BLOCKED"
    return {
        "status": status,
        "issues": issues,
        "failure_ids": sorted({item for i in issues for item in i["ids"]}),
        "counts": counts or {},
        **extra,
    }


def _models(values, model, issues):
    parsed = []
    for index, value in enumerate(values):
        try:
            parsed.append(model.model_validate(value))
        except ValidationError as exc:
            identifier = (
                value.get("record_id", value.get("task_id", f"index:{index}"))
                if (isinstance(value, dict))
                else f"index:{index}"
            )
            _issue(
                issues,
                "invalid_record",
                [str(identifier)],
                model=model.__name__,
                errors=[
                    {"field": ".".join(map(str, error["loc"])), "type": error["type"]}
                    for error in exc.errors()
                ],
            )
    return parsed


def _duplicates(values, key, issues):
    duplicates = {
        identifier for identifier, count in Counter(key(v) for v in values).items() if count > 1
    }
    if duplicates:
        _issue(issues, "duplicate_ids", duplicates)
    return duplicates


def _url(value):
    parts = urlsplit(value)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def _tokens(record):
    tokens = {
        ("family", record.family_id),
        ("notice", record.notice_id),
        ("url", _url(record.source_url)),
        ("content", record.raw_sha256),
        ("content", record.text_sha256),
    }
    if record.project_code:
        tokens.add(("project", "".join(record.project_code.upper().split())))
    if record.version_chain_id:
        tokens.add(("version_chain", record.version_chain_id))
    for prefix, values in [
        ("notice", record.related_notice_ids),
        ("package", record.package_ids),
        ("attachment", record.attachment_ids),
        ("content", record.attachment_sha256s),
    ]:
        tokens.update((prefix, value) for value in values)
    return tokens


def validate_split(records, legacy=None):
    """Reject cross-split connected families, identities and exact content duplicates."""
    issues = []
    rows = _models(records, DatasetRecord, issues)
    _duplicates(rows, lambda r: r.record_id, issues)
    legacy_rows = list(LEGACY_NOTICES) + _models(legacy or (), LegacyIdentity, issues)
    legacy_tokens = set()
    for row in legacy_rows:
        if row.project_code:
            legacy_tokens.add(("project", "".join(row.project_code.upper().split())))
        if row.source_url:
            legacy_tokens.add(("url", _url(row.source_url)))
        if row.raw_sha256:
            legacy_tokens.add(("content", row.raw_sha256))
        if not any((row.project_code, row.source_url, row.raw_sha256)):
            _issue(issues, "empty_legacy_identity")
    # Connected components catch aliases linked through intermediate versions/attachments.
    parent = list(range(len(rows)))

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    owners = {}
    tokens_by_record = []
    for index, row in enumerate(rows):
        tokens = _tokens(row)
        tokens_by_record.append(tokens)
        for token in tokens:
            if token in owners:
                parent[root(index)] = root(owners[token])
            else:
                owners[token] = index
    components = defaultdict(list)
    for index in range(len(rows)):
        components[root(index)].append(index)
    legacy_acceptance = []
    for indexes in components.values():
        members = [rows[i] for i in indexes]
        acceptance = [r.record_id for r in members if r.split == "acceptance"]
        development = [r.record_id for r in members if r.split != "acceptance"]
        if acceptance and development:
            _issue(issues, "split_leakage", acceptance + development)
        if acceptance and len({r.family_id for r in members}) > 1:
            _issue(issues, "connected_families_not_independent", [r.record_id for r in members])
        known_legacy = any(tokens_by_record[i] & legacy_tokens for i in indexes) or any(
            r.provenance_kind == "legacy_demo" for r in members
        )
        if acceptance and known_legacy:
            legacy_acceptance.extend(acceptance)
            _issue(issues, "legacy_acceptance", acceptance)
    return _report(
        issues,
        {
            "records": len(rows),
            "components": len(components),
            "legacy_acceptance": len(legacy_acceptance),
        },
        manifest_sha256=records_digest(rows),
    )


def records_digest(records):
    """Hash canonical metadata; raw/source bytes must separately match their hashes."""
    rows = [DatasetRecord.model_validate(r) for r in records]
    payload = [r.model_dump(mode="json") for r in sorted(rows, key=lambda r: r.record_id)]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _real_pool(rows):
    return [
        r
        for r in rows
        if r.split == "acceptance"
        and r.provenance_kind == "real"
        and r.source_permission == "allowed"
    ]


def select_gold_record_ids(records, seed, count=20):
    """Versioned, deterministic stratified random selection of one notice per family."""
    rows = _real_pool([DatasetRecord.model_validate(r) for r in records])
    by_family = defaultdict(list)
    for row in sorted(rows, key=lambda r: r.record_id):
        by_family[row.family_id].append(row)
    rng = random.Random(seed)
    representatives = {family: rng.choice(by_family[family]) for family in sorted(by_family)}
    selected = set()
    for risk in RISKS:
        covered = sum(risk in representatives[f].risk_types for f in selected)
        available = sorted(
            f for f in by_family if f not in selected and risk in representatives[f].risk_types
        )
        selected.update(rng.sample(available, min(max(0, 3 - covered), len(available))))
    remaining = sorted(set(by_family) - selected)
    selected.update(rng.sample(remaining, min(max(0, count - len(selected)), len(remaining))))
    return sorted(representatives[family].record_id for family in sorted(selected))


def validate_annotations(records, contributors):
    """Check independent-human declarations and original-evidence/review/arbitration records."""
    issues = []
    rows = _models(records, AnnotationRecord, issues)
    people = _models(contributors, Contributor, issues)
    duplicate_people = _duplicates(people, lambda p: p.person_id, issues)
    duplicate_records = _duplicates(rows, lambda r: r.record_id, issues)
    roster = {p.person_id: p for p in people if p.person_id not in duplicate_people}
    valid_ids, independent_ids = [], []
    for row in rows:
        before = len(issues)
        ids = [row.record_id]
        if row.record_id in duplicate_records:
            continue
        if not row.source_available or not row.source_refs:
            _issue(issues, "missing_original_evidence", ids)
        if row.model_preannotated:
            _issue(issues, "model_preannotation_not_independent", ids)
        if not row.annotated_at:
            _issue(issues, "missing_annotation_time", ids)
        required = [("annotator", row.annotator_id)]
        if row.critical or row.purpose == "real_independent":
            required.append(("reviewer", row.reviewer_id))
            if not row.reviewed_at:
                _issue(issues, "missing_review_time", ids)
        if row.disagreement:
            required.append(("arbitrator", row.arbitrator_id))
            if not row.arbitrated_at or not row.arbitration_resolution:
                _issue(issues, "unresolved_disagreement", ids)
        if len(row.quality_unit_ids) != len(set(row.quality_unit_ids)):
            _issue(issues, "duplicate_annotation_quality_units", ids)
        identities = [person for _, person in required if person]
        if len(set(identities)) != len(identities):
            _issue(issues, "annotation_roles_not_independent", ids)
        for role, person_id in required:
            person = roster.get(person_id)
            if (
                not person
                or not person.actual_human
                or not person.independent
                or (person.developer_or_tuner or not person.attestation_ref)
            ):
                _issue(issues, "nonindependent_annotation_person", ids, role=role)
        if row.annotated_at and row.reviewed_at and row.reviewed_at < row.annotated_at:
            _issue(issues, "review_before_annotation", ids)
        preceding_time = row.reviewed_at or row.annotated_at
        if row.arbitrated_at and preceding_time and row.arbitrated_at < preceding_time:
            _issue(issues, "arbitration_before_review", ids)
        if len(issues) == before:
            valid_ids.append(row.record_id)
            if row.purpose == "real_independent":
                independent_ids.append(row.record_id)
    if not rows:
        _issue(issues, "missing_annotations", status="BLOCKED")
    return _report(
        issues,
        {
            "registered": len(rows),
            "valid": len(valid_ids),
            "independent_gold": len(independent_ids),
        },
        valid_ids=sorted(valid_ids),
        independent_gold_ids=sorted(independent_ids),
        evidence_level="supplied_records_not_identity_authentication",
    )


def validate_dataset_gate(records, sampling, annotations=(), contributors=()):
    """Preserve P1 minima and independent-acceptance risk coverage; fixtures cannot fill gaps."""
    issues = []
    rows = _models(records, DatasetRecord, issues)
    try:
        plan = SamplingPlan.model_validate(sampling)
    except ValidationError:
        _issue(issues, "invalid_sampling_plan")
        return _report(issues)
    split_report = validate_split(rows)
    issues.extend(split_report["issues"])
    annotation_report = validate_annotations(annotations, contributors)
    issues.extend(annotation_report["issues"])
    gold = {r.record_id: r for r in _models(annotations, AnnotationRecord, [])}
    real = [r for r in rows if r.provenance_kind == "real" and r.source_permission == "allowed"]
    # Exact repeated source content counts once, even if record/notice IDs were changed.
    unique = {r.raw_sha256: r for r in real}
    categories = {r.notice_category for r in unique.values() if r.notice_category}
    layouts = {r.layout for r in unique.values() if r.layout}
    historical = [
        r
        for r in unique.values()
        if r.deadline_at and r.captured_at and r.deadline_at <= r.captured_at
    ]
    future = [
        r
        for r in unique.values()
        if r.deadline_at and r.captured_at and r.deadline_at > r.captured_at
    ]
    pool = _real_pool(rows)
    if records_digest(pool) != plan.candidate_pool_sha256:
        _issue(issues, "candidate_pool_hash_mismatch")
    if len(plan.selected_record_ids) != len(set(plan.selected_record_ids)):
        _issue(issues, "duplicate_selected_records", plan.selected_record_ids)
    expected_selection = select_gold_record_ids(rows, plan.seed, len(plan.selected_record_ids))
    if sorted(plan.selected_record_ids) != expected_selection:
        _issue(issues, "sampling_not_reproducible", plan.selected_record_ids)
    index = {r.record_id: r for r in pool}
    selected = []
    for record_id in plan.selected_record_ids:
        row, annotation = index.get(record_id), gold.get(record_id)
        if row is None:
            _issue(issues, "selected_record_not_in_real_pool", [record_id])
        elif record_id not in annotation_report["independent_gold_ids"]:
            _issue(issues, "missing_independent_gold", [record_id], status="BLOCKED")
        elif annotation.source_sha256 != row.raw_sha256 or annotation.family_id != row.family_id:
            _issue(issues, "gold_source_identity_mismatch", [record_id])
        else:
            selected.append(row)
    coverage = {
        risk: sorted({r.family_id for r in selected if risk in r.risk_types}) for risk in RISKS
    }
    counts = {
        "real_notices": len(unique),
        "notice_categories": len(categories),
        "layouts": len(layouts),
        "historical": len(historical),
        "future": len(future),
        "selected_gold_records": len(selected),
        "selected_gold_families": len({r.family_id for r in selected}),
        "risk_families": {risk: len(families) for risk, families in coverage.items()},
    }
    for name, minimum in [
        ("real_notices", 30),
        ("notice_categories", 3),
        ("layouts", 2),
        ("historical", 10),
        ("selected_gold_records", 20),
        ("selected_gold_families", 20),
    ]:
        if counts[name] < minimum:
            _issue(
                issues,
                "insufficient_" + name,
                status="BLOCKED",
                observed=counts[name],
                required=minimum,
            )
    if len(future) < 10 and not (
        plan.future_availability_exception_ref
        and plan.future_available_count == len(future)
        and plan.future_availability_evidence_sha256
    ):
        _issue(issues, "insufficient_future", status="BLOCKED", observed=len(future), required=10)
    for risk, families in coverage.items():
        if len(families) < 3:
            _issue(
                issues,
                "missing_risk_coverage",
                status="BLOCKED",
                risk=risk,
                observed=len(families),
                required=3,
            )
    if plan.purpose != "independent_acceptance":
        _issue(issues, "engineering_fixture_not_real_acceptance", status="BLOCKED")
    return _report(
        issues,
        counts,
        risk_family_ids=coverage,
        purpose=plan.purpose,
        candidate_pool_sha256=records_digest(pool),
        future_availability_exception_ref=plan.future_availability_exception_ref,
    )


def validate_trial_plan(plan, annotations=(), contributors=()):
    """Validate frozen paired assignments, common inputs/quality units and blind operators."""
    issues = []
    try:
        plan = TrialPlan.model_validate(plan)
    except ValidationError:
        _issue(issues, "invalid_trial_plan")
        return _report(issues)
    people = _models(contributors, Contributor, issues)
    duplicate_people = _duplicates(people, lambda p: p.person_id, issues)
    roster = {p.person_id: p for p in people if p.person_id not in duplicate_people}
    annotation_rows = _models(annotations, AnnotationRecord, issues)
    annotation_report = validate_annotations(annotation_rows, people)
    eligible_gold_ids = annotation_report[
        "independent_gold_ids" if plan.purpose == "independent_acceptance" else "valid_ids"
    ]
    gold_index = {a.record_id: a for a in annotation_rows if a.record_id in eligible_gold_ids}
    if plan.purpose == "independent_acceptance":
        issues.extend(annotation_report["issues"])
        gold_families = {
            a.family_id
            for a in annotation_rows
            if a.record_id in annotation_report["independent_gold_ids"]
        }
        missing_families = {a.family_id for a in plan.assignments} - gold_families
        if missing_families:
            _issue(issues, "trial_missing_independent_gold", missing_families, status="BLOCKED")
    annotation_people = defaultdict(set)
    for row in annotation_rows:
        annotation_people[row.family_id].update(
            p for p in (row.annotator_id, row.reviewer_id, row.arbitrator_id) if p
        )
    _duplicates(plan.assignments, lambda a: a.task_id, issues)
    pairs, operators = defaultdict(list), defaultdict(list)
    for assignment in plan.assignments:
        pairs[assignment.pair_id].append(assignment)
        operators[assignment.operator_id].append(assignment)
        person = roster.get(assignment.operator_id)
        if (
            not person
            or not person.actual_human
            or not person.independent
            or (person.developer_or_tuner or not person.attestation_ref)
        ):
            _issue(issues, "nonindependent_trial_operator", [assignment.task_id])
        if assignment.operator_id in annotation_people[assignment.family_id]:
            _issue(issues, "operator_contributed_gold", [assignment.task_id])
        if not assignment.blind_to_gold or assignment.previously_performed:
            _issue(issues, "operator_not_blind", [assignment.task_id])
        if len(assignment.quality_unit_ids) != len(set(assignment.quality_unit_ids)):
            _issue(issues, "duplicate_quality_units", [assignment.task_id])
        gold = gold_index.get(assignment.gold_record_id)
        if gold is None:
            _issue(issues, "trial_missing_gold_record", [assignment.task_id], status="BLOCKED")
        elif (
            assignment.family_id,
            assignment.input_sha256,
            assignment.gold_sha256,
            sorted(assignment.quality_unit_ids),
        ) != (gold.family_id, gold.input_sha256, gold.gold_sha256, sorted(gold.quality_unit_ids)):
            _issue(issues, "trial_gold_binding_mismatch", [assignment.task_id])
    for pair_id, pair in pairs.items():
        if len(pair) != 2 or {a.mode for a in pair} != {"manual", "assisted"}:
            _issue(issues, "incomplete_mode_pair", [pair_id])
            continue
        left, right = pair
        if left.operator_id == right.operator_id:
            _issue(issues, "same_operator_both_modes", [pair_id])
        if (
            left.family_id,
            left.input_sha256,
            left.deliverable_sha256,
            sorted(left.quality_unit_ids),
            left.gold_record_id,
            left.gold_sha256,
            left.time_limit_seconds,
        ) != (
            right.family_id,
            right.input_sha256,
            right.deliverable_sha256,
            sorted(right.quality_unit_ids),
            right.gold_record_id,
            right.gold_sha256,
            right.time_limit_seconds,
        ):
            _issue(issues, "unequal_trial_inputs_or_deliverables", [pair_id])
    first_modes = set()
    for operator, assignments in operators.items():
        ordered = sorted(assignments, key=lambda a: a.order)
        families = Counter(a.family_id for a in assignments)
        inputs = Counter(a.input_sha256 for a in assignments)
        repeated = [
            a.task_id
            for a in assignments
            if families[a.family_id] > 1 or inputs[a.input_sha256] > 1
        ]
        if repeated:
            _issue(issues, "operator_repeats_family_or_input", repeated)
        first_modes.add(ordered[0].mode)
        modes = Counter(a.mode for a in ordered)
        if abs(modes["manual"] - modes["assisted"]) > 1 or len({a.order for a in ordered}) != len(
            ordered
        ):
            _issue(issues, "unbalanced_operator_order", [operator])
        # Also balance the early/late halves; block an all-manual then all-assisted schedule.
        for half in (ordered[: len(ordered) // 2], ordered[len(ordered) // 2 :]):
            counts = Counter(a.mode for a in half)
            if abs(counts["manual"] - counts["assisted"]) > 1:
                _issue(issues, "unbalanced_operator_order", [operator])
                break
    if operators and first_modes != {"manual", "assisted"}:
        _issue(issues, "unbalanced_first_mode", operators)
    families = {a.family_id for a in plan.assignments}
    if len(families) < 10:
        _issue(
            issues,
            "insufficient_trial_families",
            status="BLOCKED",
            observed=len(families),
            required=10,
        )
    if len(operators) < 2:
        _issue(
            issues,
            "insufficient_trial_operators",
            status="BLOCKED",
            observed=len(operators),
            required=2,
        )
    return _report(
        issues,
        {
            "assignments": len(plan.assignments),
            "pairs": len(pairs),
            "families": len(families),
            "operators": len(operators),
        },
        purpose=plan.purpose,
    )


def score_paired_trials(plan, observations, annotations=(), contributors=()):
    """Keep all preregistered quality units; failed/missing/late tasks cannot earn time PASS."""
    plan_report = validate_trial_plan(plan, annotations, contributors)
    issues = list(plan_report["issues"])
    try:
        plan = TrialPlan.model_validate(plan)
    except ValidationError:
        return _report(issues)
    rows = _models(observations, TrialObservation, issues)
    duplicates = _duplicates(rows, lambda r: r.task_id, issues)
    observed = {r.task_id: r for r in rows if r.task_id not in duplicates}
    registered = {a.task_id for a in plan.assignments}
    by_operator = defaultdict(list)
    for assignment in plan.assignments:
        if assignment.task_id in observed:
            by_operator[assignment.operator_id].append((assignment, observed[assignment.task_id]))
    for operator, attempts in by_operator.items():
        attempts.sort(key=lambda pair: pair[0].order)
        if any(
            left[1].stopped_at > right[1].started_at
            for left, right in zip(attempts, attempts[1:], strict=False)
        ):
            _issue(issues, "trial_order_or_overlap", [operator])
    for row in rows:
        if row.task_id not in registered:
            _issue(issues, "unregistered_observation", [row.task_id])
    observation_integrity_failed = any(issue["status"] == "FAIL" for issue in issues)
    totals = {mode: {"numerator": 0, "denominator": 0} for mode in ("manual", "assisted")}
    task_rows, pairs = [], defaultdict(dict)
    failed = []
    severe_ids = []
    for assignment in plan.assignments:
        observation = observed.get(assignment.task_id)
        denominator = len(set(assignment.quality_unit_ids))
        totals[assignment.mode]["denominator"] += denominator
        elapsed, correct, status = None, 0, "missing"
        if observation:
            elapsed = (
                observation.stopped_at.astimezone(UTC) - observation.started_at.astimezone(UTC)
            ).total_seconds()
            status = observation.status
            valid = True
            if status != "completed" and not observation.failure_reason:
                _issue(issues, "missing_trial_failure_reason", [assignment.task_id])
            if observation.started_at < plan.frozen_at or elapsed <= 0:
                _issue(issues, "invalid_or_prefreeze_trial_time", [assignment.task_id])
                valid = False
            if observation.active_seconds + observation.waiting_seconds > elapsed + 1e-6:
                _issue(issues, "activity_exceeds_wall_time", [assignment.task_id])
                valid = False
            if set(observation.correct_unit_ids) - set(assignment.quality_unit_ids) or len(
                set(observation.correct_unit_ids)
            ) != len(observation.correct_unit_ids):
                _issue(issues, "invalid_scored_quality_units", [assignment.task_id])
                valid = False
            if elapsed > assignment.time_limit_seconds:
                status = "timed_out"
            if not valid:
                status = "invalid"
            severe_ids.extend(observation.severe_error_ids)
            if status == "completed":
                correct = len(observation.correct_unit_ids)
        if status != "completed":
            failed.append(assignment.task_id)
            _issue(issues, "unfinished_trial", [assignment.task_id], observed_status=status)
        totals[assignment.mode]["numerator"] += correct
        result = {
            "task_id": assignment.task_id,
            "pair_id": assignment.pair_id,
            "family_id": assignment.family_id,
            "mode": assignment.mode,
            "operator_id": assignment.operator_id,
            "status": status,
            "numerator": correct,
            "denominator": denominator,
            "elapsed_seconds": elapsed,
            "started_at": observation.started_at.isoformat() if observation else None,
            "stopped_at": observation.stopped_at.isoformat() if observation else None,
            "time_limit_seconds": assignment.time_limit_seconds,
            "active_seconds": observation.active_seconds if observation else None,
            "waiting_seconds": observation.waiting_seconds if observation else None,
            "failure_reason": (
                observation.failure_reason
                or (
                    "time_limit_exceeded"
                    if elapsed > assignment.time_limit_seconds
                    else f"reported_{status}_without_reason"
                    if status != "completed"
                    else None
                )
            )
            if observation
            else "missing_result",
        }
        task_rows.append(result)
        pairs[assignment.pair_id][assignment.mode] = result
    for total in totals.values():
        total["value"] = total["numerator"] / total["denominator"] if total["denominator"] else None
    savings = []
    paired = []
    for pair_id, pair in sorted(pairs.items()):
        manual, assisted = pair.get("manual"), pair.get("assisted")
        saving = None
        if manual and assisted and all(r["status"] == "completed" for r in pair.values()):
            saving = Decimal(1) - Decimal(str(assisted["elapsed_seconds"])) / Decimal(
                str(manual["elapsed_seconds"])
            )
            savings.append(saving)
        paired.append({"pair_id": pair_id, "saving": float(saving) if saving is not None else None})
    median = statistics.median(savings) if savings else None
    quality_status = "PASS"
    if severe_ids:
        _issue(issues, "severe_postreview_errors", severe_ids)
        quality_status = "FAIL"
    if any(total["value"] is None for total in totals.values()):
        quality_status = "BLOCKED"
    elif totals["assisted"]["value"] < totals["manual"]["value"]:
        _issue(issues, "postreview_quality_decreased")
        quality_status = "FAIL"
    threshold = Decimal("0.2")
    time_status = (
        "FAIL" if failed else ("PASS" if median is not None and median >= threshold else "FAIL")
    )
    if not failed and median is not None and median < threshold:
        _issue(issues, "insufficient_time_saving", observed=float(median), required=0.2)
    if quality_status == "FAIL":
        time_status = "FAIL"
    if observation_integrity_failed:
        time_status = "FAIL"
    if plan_report["status"] == "FAIL":
        quality_status = time_status = "FAIL"
    elif plan_report["status"] == "BLOCKED":
        if quality_status == "PASS":
            quality_status = "BLOCKED"
        if time_status == "PASS":
            time_status = "BLOCKED"
    if not plan.assignments:
        quality_status = time_status = "BLOCKED"
    if plan.purpose != "independent_acceptance":
        _issue(issues, "engineering_fixture_not_real_acceptance", status="BLOCKED")
        if quality_status != "FAIL":
            quality_status = "BLOCKED"
        if time_status != "FAIL":
            time_status = "BLOCKED"
    return _report(
        issues,
        {
            "registered": len(plan.assignments),
            "observed": len(rows),
            "completed": len(plan.assignments) - len(failed),
            "failed_or_missing": len(failed),
            "registered_pairs": len(pairs),
            "completed_pairs": len(savings),
        },
        purpose=plan.purpose,
        quality=totals,
        quality_status=quality_status,
        time_status=time_status,
        median_saving=float(median) if median is not None and not failed else None,
        completed_pair_median_saving=float(median) if median is not None else None,
        pairs=paired,
        tasks=task_rows,
        failed_task_ids=sorted(failed),
        severe_error_ids=sorted(set(severe_ids)),
    )
