"""B08 aggregation of supplied records using the unchanged, frozen B01 contracts.

This module checks declared consistency. It cannot authenticate people, permission,
source bytes, an undisclosed rerun, or a real provider call. The runner must verify
external pins and execution/ledger records; this helper never grants business
acceptance, even when all locally assessable gates pass.
"""

import hashlib
import json
from collections import Counter, defaultdict
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from pydantic_evals import Case, Dataset

from bidops.screening.eval_protocol import (
    AnnotationRecord,
    Contributor,
    DatasetRecord,
    Digest,
    Identifier,
    SamplingPlan,
    TrialPlan,
    records_digest,
    score_paired_trials,
    validate_annotations,
    validate_dataset_gate,
    validate_split,
)
from bidops.screening.evaluation import (
    P1_FIELDS,
    EvidenceRecord,
    GoldTask,
    ScreeningEvaluator,
    same_field,
    score_screening,
)

Status = Literal["PASS", "FAIL", "BLOCKED"]
BUSINESS_ACS = ("B08-01", "B08-02", "B08-04", "B08-05", "B08-06")
LIMITATIONS = [
    "Supplied consistency checks do not authenticate human identity, permission or source bytes.",
    "Undisclosed runs or answer access cannot be detected from declarations alone.",
    "External file pins, execution versions and cumulative ledger need runner checks.",
    "Engineering fixtures cannot establish independent gold, Agent quality or human efficiency.",
]


class AuditModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, revalidate_instances="always")


class TaskBinding(AuditModel):
    task_id: Identifier
    record_id: Identifier


class BlindRun(AuditModel):
    run_id: Identifier
    role: Literal["blind", "regression"]
    dataset_records: list[DatasetRecord] = Field(min_length=1)
    dataset_sha256: Digest
    result_sha256: Digest
    frozen_at: AwareDatetime
    started_at: AwareDatetime
    finished_at: AwareDatetime
    answer_accessed_at: AwareDatetime | None
    answers_used_for_tuning: bool = Field(strict=True)
    outcome: Status


class BlindHistory(AuditModel):
    current_run_id: Identifier
    runs: list[BlindRun] = Field(min_length=1)


class EvaluationBundle(AuditModel):
    purpose: Literal["engineering_fixture", "independent_acceptance"]
    dataset_records: list[DatasetRecord]
    sampling: SamplingPlan
    annotations: list[AnnotationRecord]
    contributors: list[Contributor]
    gold_tasks: list[GoldTask]
    # Raw outputs must reach the frozen safety scorer, even if structurally invalid.
    observed_tasks: list[Any]
    evidence: list[EvidenceRecord]
    task_bindings: list[TaskBinding] = Field(default_factory=list)
    trial_plan: TrialPlan | None = None
    trial_observations: list[Any] = Field(default_factory=list)
    blind_history: BlindHistory | None = None


def canonical_sha256(value: Any) -> str:
    """Canonical JSON hash; the outer runner separately pins original file bytes."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def gold_binding(tasks: list[GoldTask | dict]) -> dict:
    """One annotation binds all package tasks of its record, not one favorable task.

    Gold hashes cover sorted complete GoldTask JSON. Input hashes cover sorted
    task_id/inputs/package_id JSON. Common human quality units are task:condition.
    """
    rows = sorted((GoldTask.model_validate(t) for t in tasks), key=lambda t: t.task_id)
    return {
        "gold_sha256": canonical_sha256([t.model_dump(mode="json") for t in rows]),
        "input_sha256": canonical_sha256(
            [
                {
                    "task_id": t.task_id,
                    "inputs": t.inputs.model_dump(mode="json"),
                    "package_id": t.package_id,
                }
                for t in rows
            ]
        ),
        "quality_unit_ids": sorted(
            f"{t.task_id}:{c.condition.condition_id}" for t in rows for c in t.conditions
        ),
    }


def _issue(issues, code, ids=(), status="FAIL", **details):
    issues.append({"code": code, "ids": sorted(set(ids)), "status": status, **details})


def _status(statuses):
    values = list(statuses)
    return (
        "FAIL" if "FAIL" in values else "BLOCKED" if not values or "BLOCKED" in values else "PASS"
    )


def _section(issues, **details):
    return {
        "status": _status(i["status"] for i in issues) if issues else "PASS",
        "issues": issues,
        **details,
    }


def _duplicates(values):
    return sorted(value for value, count in Counter(values).items() if count > 1)


def _bindings(bundle):
    issues, grouped = [], defaultdict(list)
    dataset = {row.record_id: row for row in bundle.dataset_records}
    annotations = {row.record_id: row for row in bundle.annotations}
    task_index = {task.task_id: task for task in bundle.gold_tasks}
    for task_id in _duplicates(b.task_id for b in bundle.task_bindings):
        _issue(issues, "duplicate_task_binding", [task_id])
    for binding in bundle.task_bindings:
        task, record = task_index.get(binding.task_id), dataset.get(binding.record_id)
        if task is None or record is None:
            _issue(issues, "unknown_task_or_record_binding", [binding.task_id, binding.record_id])
            continue
        grouped[binding.record_id].append(task)
        if task.inputs.project_family_id != record.family_id:
            _issue(issues, "task_family_binding_mismatch", [task.task_id])
        if task.inputs.notice_revision.sha256 != record.raw_sha256:
            _issue(issues, "task_source_binding_mismatch", [task.task_id])
        primary_texts = {
            ref.text_sha256
            for condition in task.conditions
            for ref in condition.condition.notice_evidence
            if ref.object_kind == "notice" and ref.version == task.inputs.notice_revision
        }
        if primary_texts and primary_texts != {record.text_sha256}:
            _issue(issues, "task_text_binding_mismatch", [task.task_id])
        if not {ref.sha256 for ref in task.inputs.attachment_revisions} <= set(
            record.attachment_sha256s
        ):
            _issue(issues, "task_attachment_binding_mismatch", [task.task_id])
        if (
            task.fields["source_url"] != record.source_url
            or task.fields["project_code"] != record.project_code
        ):
            _issue(issues, "task_p1_identity_binding_mismatch", [task.task_id])
        deadline = task.fields["bid_deadline_at"]
        if (record.deadline_at is None) != (deadline is None) or (
            record.deadline_at is not None
            and not same_field("bid_deadline_at", deadline, record.deadline_at.isoformat())
        ):
            _issue(issues, "task_deadline_binding_mismatch", [task.task_id])
        if task.fields["notice_kind"] != record.notice_category:
            _issue(issues, "task_category_binding_mismatch", [task.task_id])
    missing = set(task_index) - {b.task_id for b in bundle.task_bindings}
    if missing:
        _issue(issues, "missing_task_binding", missing, status="BLOCKED")
    selected = set(bundle.sampling.selected_record_ids)
    if selected - grouped.keys():
        _issue(issues, "selected_gold_not_scored", selected - grouped.keys(), status="BLOCKED")
    if set(grouped) - selected:
        _issue(issues, "scored_record_not_selected", set(grouped) - selected)
    summaries = []
    for record_id, tasks in sorted(grouped.items()):
        annotation = annotations.get(record_id)
        binding = gold_binding(tasks)
        summaries.append(
            {"record_id": record_id, "task_ids": sorted(t.task_id for t in tasks), **binding}
        )
        if annotation is None:
            _issue(issues, "missing_bound_annotation", [record_id], status="BLOCKED")
            continue
        if (
            annotation.gold_sha256 != binding["gold_sha256"]
            or annotation.input_sha256 != binding["input_sha256"]
            or sorted(annotation.quality_unit_ids) != binding["quality_unit_ids"]
        ):
            _issue(issues, "annotation_scoring_binding_mismatch", [record_id])
        record = dataset[record_id]
        if record.package_ids and set(record.package_ids) != {t.package_id for t in tasks}:
            _issue(issues, "incomplete_record_package_tasks", [record_id])
        if (
            annotation.family_id != record.family_id
            or annotation.source_sha256 != record.raw_sha256
        ):
            _issue(issues, "annotation_source_binding_mismatch", [record_id])
    if not bundle.gold_tasks:
        _issue(issues, "no_registered_tasks", status="BLOCKED")
    return _section(
        issues,
        records=summaries,
        registered_tasks=len(bundle.gold_tasks),
        counts={
            "records": len(grouped),
            "families": len({t.inputs.project_family_id for t in bundle.gold_tasks}),
            "packages": len(
                {(t.inputs.project_family_id, t.package_id) for t in bundle.gold_tasks}
            ),
            "company_project_pairs": len(
                {
                    (t.inputs.profile_revision.key(), t.inputs.project_family_id)
                    for t in bundle.gold_tasks
                }
            ),
        },
    )


def _registration(bundle):
    issues = []
    for task_id in _duplicates(t.task_id for t in bundle.gold_tasks):
        _issue(issues, "duplicate_registered_task", [task_id])
    indexed, retries = {}, Counter()
    registered = {t.task_id for t in bundle.gold_tasks}
    for position, row in enumerate(bundle.observed_tasks):
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("task_id"), str)
            or row["task_id"] not in registered
        ):
            _issue(issues, "unregistered_output", [f"output:{position}"])
            continue
        task_id = row["task_id"]
        retries[task_id] += 1
        indexed.setdefault(task_id, row)
    rows = [
        {
            "task_id": task.task_id,
            "state": indexed.get(task.task_id, {}).get("state", "missing"),
            "attempts": retries[task.task_id],
        }
        for task in bundle.gold_tasks
    ]
    for task_id, count in retries.items():
        if count > 1:
            _issue(issues, "duplicate_output", [task_id])
    if not rows:
        _issue(issues, "no_registered_tasks", status="BLOCKED")
    return _section(
        issues, registered=len(rows), observed=len(bundle.observed_tasks), tasks=rows
    ), indexed


def _framework(tasks, outputs, evidence):
    dataset = Dataset(
        name="B08 supplied output audit",
        cases=[Case(name=t.task_id, inputs=t) for t in tasks],
        evaluators=[ScreeningEvaluator(evidence=evidence)],
    )
    report = dataset.evaluate_sync(
        lambda task: outputs.get(task.task_id, {"task_id": task.task_id, "state": "missing"}),
        progress=False,
    )
    assertions = {
        case.name: {name: value.value for name, value in case.assertions.items()}
        for case in report.cases
    }
    return {
        "status": "FAIL" if report.failures else "PASS" if tasks else "BLOCKED",
        "cases": len(report.cases),
        "registered": len(tasks),
        "failures": len(report.failures),
        "assertions": assertions,
        "issues": [],
    }


def _history(bundle, screening):
    issues = []
    history = bundle.blind_history
    if history is None:
        return _section(
            [{"code": "missing_blind_history", "ids": [], "status": "BLOCKED"}], runs=[]
        )
    for run_id in _duplicates(r.run_id for r in history.runs):
        _issue(issues, "duplicate_history_run", [run_id])
    runs = sorted(history.runs, key=lambda r: (r.started_at, r.run_id))
    blind = [r for r in runs if r.role == "blind"]
    for left, right in zip(blind, blind[1:], strict=False):
        if left.finished_at > right.started_at:
            _issue(issues, "overlapping_blind_runs", [left.run_id, right.run_id])
    current = next((r for r in runs if r.run_id == history.current_run_id), None)
    if current is None or not blind or current.run_id != blind[-1].run_id:
        _issue(issues, "current_run_not_latest_blind", [history.current_run_id])
    summaries = []
    for run in runs:
        if run.role == "blind" and not any(r.split == "acceptance" for r in run.dataset_records):
            _issue(issues, "blind_history_has_no_acceptance_records", [run.run_id])
        if records_digest(run.dataset_records) != run.dataset_sha256:
            _issue(issues, "history_dataset_hash_mismatch", [run.run_id])
        if not run.frozen_at <= run.started_at <= run.finished_at:
            _issue(issues, "history_time_order", [run.run_id])
        if (
            run.role == "blind"
            and run.answer_accessed_at is not None
            and run.answer_accessed_at < run.finished_at
        ):
            _issue(issues, "answers_seen_before_blind_finish", [run.run_id])
        if run.answers_used_for_tuning and run.answer_accessed_at is None:
            _issue(issues, "missing_tuning_answer_access_time", [run.run_id])
        summaries.append(
            {
                "run_id": run.run_id,
                "role": run.role,
                "outcome": run.outcome,
                "dataset_sha256": run.dataset_sha256,
                "result_sha256": run.result_sha256,
                "answers_used_for_tuning": run.answers_used_for_tuning,
            }
        )
    if current is not None:
        if current.dataset_sha256 != records_digest(bundle.dataset_records):
            _issue(issues, "current_dataset_hash_mismatch", [current.run_id])
        if current.result_sha256 != canonical_sha256(bundle.observed_tasks):
            _issue(issues, "current_result_hash_mismatch", [current.run_id])
        if current.outcome != screening["status"]:
            _issue(issues, "current_outcome_mismatch", [current.run_id])
        if current.outcome != "PASS":
            _issue(issues, "current_blind_not_passed", [current.run_id], status=current.outcome)
        if current.answers_used_for_tuning:
            _issue(issues, "fresh_blind_required_after_tuning", [current.run_id], status="BLOCKED")
        previous = [r for r in blind if r.started_at < current.started_at]
        for prior in previous:
            if prior.finished_at > current.frozen_at:
                _issue(
                    issues, "retest_not_frozen_after_prior_finish", [prior.run_id, current.run_id]
                )
            if prior.answers_used_for_tuning:
                if prior.answer_accessed_at and prior.answer_accessed_at > current.frozen_at:
                    _issue(
                        issues, "retest_frozen_before_tuning_access", [prior.run_id, current.run_id]
                    )
                if not any(
                    r.role == "regression"
                    and r.dataset_sha256 == prior.dataset_sha256
                    and prior.answer_accessed_at is not None
                    and r.started_at >= prior.answer_accessed_at
                    for r in runs
                ):
                    _issue(
                        issues,
                        "tuned_blind_not_retained_as_regression",
                        [prior.run_id],
                        status="BLOCKED",
                    )
            # The old blind's entire supplied corpus has been exposed. Historical
            # split labels cannot remove any of its family/version/content tokens
            # from the next blind's leakage check.
            separated = []
            for prefix, records, split in (
                ("prior", prior.dataset_records, "regression"),
                ("current", current.dataset_records, "acceptance"),
            ):
                separated.extend(
                    r.model_copy(update={"record_id": f"{prefix}:{r.record_id}", "split": split})
                    for r in records
                    if prefix == "prior" or r.split == "acceptance"
                )
            split_report = validate_split(separated)
            if split_report["status"] != "PASS":
                _issue(
                    issues,
                    "reused_blind_corpus",
                    [prior.run_id, current.run_id],
                    split_issue_codes=sorted({i["code"] for i in split_report["issues"]}),
                )
    return _section(
        issues,
        runs=summaries,
        current_run_id=history.current_run_id,
        path="first_test" if len(blind) == 1 else "new_independent_retest",
    )


def _invalid(payload, errors):
    raw = payload.get("gold_tasks", []) if isinstance(payload, dict) else []
    raw = raw if isinstance(raw, list) else []
    registration = {
        "status": "FAIL",
        "registered": len(raw),
        "tasks": [
            {
                "task_id": t["task_id"]
                if isinstance(t, dict) and isinstance(t.get("task_id"), str)
                else f"index:{i}",
                "state": "unscored_invalid_bundle",
            }
            for i, t in enumerate(raw)
        ],
    }
    return {
        "status": "FAIL",
        "sections": {"registration": registration},
        "issues": [
            {
                "section": "bundle",
                "code": "invalid_bundle",
                "status": "FAIL",
                "ids": [],
                "errors": errors,
            }
        ],
        "ac_status": {f"B08-0{i}": "FAIL" if i in (3, 7) else "BLOCKED" for i in range(1, 8)},
        "business_acceptance": "BLOCKED",
        "agent_score": None,
        "limitations": LIMITATIONS,
    }


def audit_evaluation(payload: dict) -> dict:
    """Audit a controlled JSON bundle without I/O or changes to frozen thresholds.

    Failed/missing/invalid model outputs always reach the registered denominators.
    Invalid oracle/registry input blocks scoring instead of silently dropping rows.
    All B gates are BLOCKED for engineering fixtures. Even real declarations need
    the outer runner's authenticated evidence/pin/version/budget gates.
    """
    try:
        # Reject non-JSON and non-finite values before comparisons or framework use.
        canonical_sha256(payload)
        bundle = EvaluationBundle.model_validate(payload)
    except (ValidationError, ValueError, TypeError, OverflowError, RecursionError) as exc:
        errors = (
            [{"field": ".".join(map(str, e["loc"])), "type": e["type"]} for e in exc.errors()]
            if isinstance(exc, ValidationError)
            else [{"type": type(exc).__name__}]
        )
        return _invalid(payload, errors)
    registration, indexed = _registration(bundle)
    sections = {
        "registration": registration,
        "dataset": validate_dataset_gate(
            bundle.dataset_records, bundle.sampling, bundle.annotations, bundle.contributors
        ),
        "annotations": validate_annotations(bundle.annotations, bundle.contributors),
        "bindings": _bindings(bundle),
    }
    if bundle.sampling.purpose != bundle.purpose:
        _issue(sections["bindings"]["issues"], "sampling_purpose_mismatch")
        sections["bindings"]["status"] = "FAIL"
    try:
        sections["screening"] = score_screening(
            bundle.gold_tasks, bundle.observed_tasks, bundle.evidence
        )
        sections["pydantic_evals"] = _framework(bundle.gold_tasks, indexed, bundle.evidence)
    except (ValueError, TypeError, ValidationError, OverflowError):
        # Malformed gold is not a zero-task successful evaluation.
        sections["screening"] = _section(
            [{"code": "invalid_scoring_oracle", "ids": [], "status": "FAIL"}],
            counts={"registered_tasks": len(bundle.gold_tasks)},
            p1_fields={name: {"status": "BLOCKED"} for name in P1_FIELDS},
        )
        sections["pydantic_evals"] = _section(
            [{"code": "oracle_prevents_framework_scoring", "ids": [], "status": "BLOCKED"}],
            cases=0,
            registered=len(bundle.gold_tasks),
            failures=0,
        )
    if bundle.trial_plan is None:
        sections["human_trials"] = _section(
            [{"code": "missing_human_trial_plan", "ids": [], "status": "BLOCKED"}],
            counts={"registered": 0, "observed": len(bundle.trial_observations)},
        )
        if bundle.trial_observations:
            _issue(sections["human_trials"]["issues"], "observations_without_registered_plan")
            sections["human_trials"]["status"] = "FAIL"
    else:
        sections["human_trials"] = score_paired_trials(
            bundle.trial_plan, bundle.trial_observations, bundle.annotations, bundle.contributors
        )
        ids = {b.record_id for b in bundle.task_bindings}
        unrelated = {a.gold_record_id for a in bundle.trial_plan.assignments} - ids
        if unrelated or bundle.trial_plan.purpose != bundle.purpose:
            _issue(sections["human_trials"]["issues"], "trial_not_bound_to_evaluation", unrelated)
            sections["human_trials"]["status"] = "FAIL"
    sections["blind_history"] = _history(bundle, sections["screening"])
    evidence_gates = [sections[name]["status"] for name in ("dataset", "annotations", "bindings")]
    ac = {
        "B08-01": sections["dataset"]["status"],
        "B08-02": _status([sections["annotations"]["status"], sections["bindings"]["status"]]),
        "B08-03": _status([registration["status"], sections["pydantic_evals"]["status"]]),
        "B08-04": _status([*evidence_gates, sections["screening"]["status"]]),
        "B08-05": _status([*evidence_gates, sections["human_trials"]["status"]]),
        "B08-06": sections["blind_history"]["status"],
        "B08-07": "PASS",
    }
    if bundle.purpose == "engineering_fixture":
        for name in BUSINESS_ACS:
            ac[name] = "BLOCKED"
    issues = [
        {"section": name, **issue}
        for name, report in sections.items()
        for issue in report.get("issues", [])
    ]
    scored = sections["screening"]
    for name, measure in scored.get("metrics", {}).items():
        if measure["status"] == "FAIL":
            issues.append(
                {
                    "section": "screening",
                    "code": "metric_failed",
                    "status": "FAIL",
                    "ids": measure["failure_ids"],
                    "metric": name,
                }
            )
    for key in ("serious_errors", "version_errors", "protocol_errors"):
        if scored.get(key):
            issues.append(
                {"section": "screening", "code": key, "status": "FAIL", "ids": scored[key]}
            )
    statuses = [registration["status"], scored["status"], sections["pydantic_evals"]["status"]]
    if bundle.purpose == "independent_acceptance":
        statuses.extend(report["status"] for report in sections.values())
    elif any(report["status"] == "FAIL" for report in sections.values()):
        statuses.append("FAIL")
    return {
        "status": _status(statuses),
        "purpose": bundle.purpose,
        "sections": sections,
        "issues": issues,
        "ac_status": ac,
        "business_acceptance": "BLOCKED",
        "agent_score": None,
        "external_checks_required": [
            "freeze_and_file_pins",
            "source_permission_and_human_identity",
            "run_versions_and_model_usage",
            "complete_cumulative_cost_ledger",
        ],
        "limitations": LIMITATIONS,
    }
