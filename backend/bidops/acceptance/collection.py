"""Read saved service results for every registered task, without executing a model.

Registrations contain input/result identities, never gold labels or predictions.
Returned records are private artifacts and require the same retention/export care
as their source results. Collection success is not independent business acceptance.
"""

from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from bidops.company.authorization import CompanyError, ToolContext, transaction
from bidops.qualification.sources import digest
from bidops.screening.eval_protocol import Identifier
from bidops.screening.schemas import VersionRef


class TaskRegistration(BaseModel):
    """A task's fixed target, chosen before inspecting its saved predictions.

    The outer runner freezes these identities and preserves first-run history.
    This read-only adapter cannot prove when a registration was written.
    """

    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    task_id: Identifier
    batch_id: Identifier
    unit_id: Identifier
    notice_revision: VersionRef
    package_id: Identifier | None
    execution_kind: Literal["screening", "job", "not_submitted"]
    execution_id: Identifier | None = None
    expected_result_revision: Identifier | None = None

    @model_validator(mode="after")
    def execution_identity(self):
        if self.execution_kind == "not_submitted":
            if self.execution_id is not None or self.expected_result_revision is not None:
                raise ValueError("unsubmitted_task_has_execution")
        elif self.execution_id is None:
            raise ValueError("submitted_task_requires_execution_id")
        return self


def _fields(version):
    """P1 notice fields, from its immutable parser payload, not the gold or LLM.

    Money.amount is already yuan in Notice. The scorer expects amount in the
    stated unit, so normalize budget.unit to yuan and report source budget_unit
    separately. Unknown notice totals stay unknown; package totals are not added.
    """
    notice = version.notice
    money = notice.budget
    return {
        "project_code": notice.project_code,
        "source_url": notice.source.source_url,
        "title": notice.title,
        "buyer_name": notice.buyer_name,
        "budget": {"amount": money.amount, "currency": money.currency, "unit": "元"}
        if money is not None
        else None,
        "budget_unit": money.unit if money is not None else None,
        "bid_deadline_at": notice.bid_deadline_at,
        "notice_kind": notice.notice_kind,
        "eligibility_excerpts": [item.text for item in notice.eligibility_conditions] or None,
    }


def _batch(screening, context, batch_id):
    # The public service verifies the entire fixed public closure, not just its
    # first page. The authorized repository then provides all saved units.
    page = screening.candidate_page(context, batch_id, offset=0, limit=100)
    record = screening.repository.get(context, batch_id)
    result = record["result"]
    units = result["items"]
    if page["total"] != len(units) or len({u["unit_id"] for u in units}) != len(units):
        raise CompanyError("acceptance_batch_integrity_failed", 409)
    return {**record, "source_state": page["source_state"]}


def _fixed_notice(screening, registration, batch, unit):
    expected = registration.notice_revision.model_dump(mode="json")
    if expected not in unit["notice_revisions"] or unit["package_id"] != registration.package_id:
        raise CompanyError("acceptance_registration_target_mismatch", 422)
    try:
        screening.sources.notices.read_text(registration.notice_revision.version_id)
        version = screening.sources.notices.get_version(registration.notice_revision.version_id)
    except (ValueError, OSError):
        raise CompanyError("acceptance_notice_unavailable", 409) from None
    public = next((r for r in batch["result"]["public_versions"] if r["version"] == expected), None)
    if (
        version.version_ref != registration.notice_revision
        or public is None
        or version.payload_sha256 != public["payload_sha256"]
        or version.text_sha256 != public["text_sha256"]
        or version.project_id != unit["project_id"]
    ):
        raise CompanyError("acceptance_notice_binding_failed", 409)
    return version


def _execution(screening, context, registration, jobs):
    if registration.execution_kind == "not_submitted":
        return None, None, None
    if registration.execution_kind == "job":
        if jobs is None:
            raise CompanyError("acceptance_jobs_reader_required", 422)
        job = jobs.read(context, registration.execution_id)
        result_id = job["result_id"]
    else:
        job, result_id = None, registration.execution_id
    if result_id is None:
        return job, None, None
    # read() enforces kind/feature gates, get() exposes saved raw output records
    # only after owner, full-source, quarantine and body-hash verification.
    result = screening.read(context, result_id)
    saved = screening.repository.get(context, result_id)
    if registration.expected_result_revision is not None and (
        result["result_revision"] != registration.expected_result_revision
    ):
        raise CompanyError("acceptance_result_revision_mismatch", 409)
    return job, result, saved


def _prediction(registration, unit, fields, job, result, source_state):
    assessment, selection_error = {}, None
    if result is not None:
        matches = [
            group["assessment"]
            for group in result["groups"]
            if group["primary_notice_revision"]
            == registration.notice_revision.model_dump(mode="json")
            and group["assessment"].get("target_package_id") == registration.package_id
        ]
        if len(matches) == 1:
            assessment = matches[0]
        else:
            selection_error = "registered_assessment_missing" if not matches else "ambiguous_group"
    state = result.get("state", "missing") if result is not None else "missing"
    if job is not None and job["status"] not in {"completed", "waiting_review"}:
        state = job["status"]
    # A completed computation with no registered group is still an incomplete
    # task. Keep the entire original output in records for independent inspection.
    if state == "completed" and selection_error:
        state = "failed"
    summaries = result.get("package_summaries", []) if result is not None else []
    positive = any(
        summary.get("target_package_id") == registration.package_id
        and summary.get("recommendation") == "review_recommended"
        for summary in summaries
    )
    return {
        "task_id": registration.task_id,
        "state": state,
        "candidate": unit["disposition"] in {"candidate", "needs_verification"},
        "fields": deepcopy(fields),
        "assessment": deepcopy(assessment),
        "positive_recommendation": positive,
        "stale_disclosed": not source_state["current"],
    }, selection_error


def collect_observed(screening, context, registrations, *, jobs=None):
    """Collect the full registered denominator using existing read-only services.

    Any identity/ACL/not-found error rejects the whole collection; 404 does not
    reveal whether a private result exists. A not_submitted registration produces
    a missing output, including excluded units. It never fabricates an assessment
    or completed state to improve frozen scorer candidate recall. Jobs waiting for
    human review retain the saved computation state and a separate job status.

    No gold is accepted, no model/client is constructed, and no workflow is
    submitted, resumed, cancelled or reviewed. ToolContext cannot impersonate the
    HumanContext required by B06: human review projections must be independently
    exported through that existing authenticated service and kept separate.
    """
    if type(context) is not ToolContext:
        raise CompanyError("acceptance_authenticated_context_required", 401)
    auth = screening.sources.company.auth
    auth.require_subject(context)
    try:
        tasks = [TaskRegistration.model_validate(row) for row in registrations]
    except (ValidationError, TypeError):
        raise CompanyError("acceptance_invalid_registration", 422) from None
    if len({task.task_id for task in tasks}) != len(tasks):
        raise CompanyError("acceptance_duplicate_registered_task", 422)
    batches, results, executions, records, observed = {}, set(), {}, [], []
    execution_cache = {}
    job_ids = set()
    for task in tasks:
        if task.batch_id not in batches:
            batches[task.batch_id] = _batch(screening, context, task.batch_id)
        batch = batches[task.batch_id]
        unit = next((u for u in batch["result"]["items"] if u["unit_id"] == task.unit_id), None)
        if unit is None:
            raise CompanyError("acceptance_registered_unit_missing", 422)
        version = _fixed_notice(screening, task, batch, unit)
        fields = _fields(version)
        key = (task.execution_kind, task.execution_id)
        if key not in execution_cache:
            execution_cache[key] = _execution(screening, context, task, jobs)
        job, result, saved = execution_cache[key]
        if (
            task.expected_result_revision is not None
            and result is not None
            and (result["result_revision"] != task.expected_result_revision)
        ):
            raise CompanyError("acceptance_result_revision_mismatch", 409)
        if job is not None:
            job_ids.add(task.execution_id)
        if saved is not None:
            results.add(saved["result_id"])
            if saved["manifest"]["profile_revision"] != batch["manifest"]["profile_revision"]:
                raise CompanyError("acceptance_profile_binding_mismatch", 409)
        reasons = set(batch["source_state"]["stale_reasons"])
        for source in (job, result):
            if source is not None:
                reasons.update(source["source_state"]["stale_reasons"])
        source_state = {"current": not reasons, "stale_reasons": sorted(reasons)}
        prediction, selection_error = _prediction(task, unit, fields, job, result, source_state)
        raw = saved["raw_model_outputs"] if saved is not None else []
        provenance = result["provenance"] if result is not None else {}
        usage = job["usage"] if job is not None else provenance
        execution = {k: v for k, v in job.items() if k != "result"} if job else None
        record = {
            "registration": task.model_dump(mode="json"),
            "candidate": deepcopy(unit),
            "fields": fields,
            "notice_payload_sha256": version.payload_sha256,
            "batch": {
                "batch_id": task.batch_id,
                "manifest_sha256": batch["result"]["manifest_sha256"],
                "manifest": deepcopy(batch["manifest"]),
                "filters": deepcopy(batch["result"]["filters"]),
                "counts": deepcopy(batch["result"]["counts"]),
                "total": len(batch["result"]["items"]),
                "source_state": deepcopy(batch["source_state"]),
            },
            "execution": deepcopy(execution),
            "agent_result": deepcopy(result),
            "source_state": source_state,
            "provenance": deepcopy(provenance),
            "usage": deepcopy(usage),
            "raw_model_outputs_sha256": digest(raw),
            "raw_model_output_hashes": [digest(value) for value in raw],
            "assessment_selection_error": selection_error,
        }
        records.append(record)
        observed.append(prediction)
        if task.execution_id is not None:
            key = (task.execution_kind, task.execution_id)
            executions[key] = {
                "kind": task.execution_kind,
                "execution_id": task.execution_id,
                "usage": deepcopy(usage),
                "provenance": deepcopy(provenance),
                "raw_model_outputs_sha256": digest(raw),
            }
    # A long multi-task collection must not release earlier private bodies after
    # their grants are withdrawn while a later task is being assembled.
    with transaction(screening.repository.db) as conn:
        auth.require_subject(context, conn)
        for result_id in set(batches) | results:
            screening.repository._authorize(context, result_id, conn=conn)
        for job_id in job_ids:
            jobs.repository._authorize(context, job_id, conn)
    return {
        "observed_tasks": observed,
        "records": records,
        "human_review_projections": [],
        "metadata": {
            "collector_version": "saved-services-v1",
            "collected_at": datetime.now(UTC).isoformat(),
            "registered": len(tasks),
            "states": dict(Counter(row["state"] for row in observed)),
            "registration_sha256": digest([task.model_dump(mode="json") for task in tasks]),
            "observed_tasks_sha256": digest(observed),
            "records_sha256": digest(records),
            "executions": list(executions.values()),
            "private_artifact": True,
            "business_acceptance": "BLOCKED",
            "limits": [
                "Collection cannot prove pre-registration, blind sampling or independent gold.",
                "Missing/excluded executions stay missing; frozen recall requires completed tasks.",
                "Usage repeats per task; executions is deduplicated. Do not sum per-task costs.",
                "Human review needs its own HumanContext export; Agent results stay unchanged.",
                "No provider call, workflow mutation or human approval is performed by collection.",
            ],
        },
    }
