"""Fixed-batch screening and saved, explicitly unreviewed qualification runs.

The loops enumerate domain inputs. Pydantic AI handles model/tool iterations;
durable scheduling and human decisions remain separate subsequent PRs.
"""

import asyncio
import hashlib
import os
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.usage import UsageLimits

from bidops.company.authorization import CompanyError, transaction
from bidops.notice_data.schemas import ProjectStatus
from bidops.qualification.candidates import build_batch, page_batch
from bidops.qualification.config import ScreeningSettings
from bidops.qualification.contracts import ExtractionDraft, UnresolvedClause
from bidops.qualification.logic import (
    aggregate_recommendation,
    applicability,
    assess_condition,
    build_assessment_group,
    conditions_for_group,
)
from bidops.qualification.model_budget import GuardedModel
from bidops.qualification.prompts import (
    ASSESSMENT_INSTRUCTIONS,
    EXTRACTION_INSTRUCTIONS,
    PROMPT_VERSION,
    RULES_VERSION,
)
from bidops.qualification.repository import QualificationRepository
from bidops.qualification.requirements import bind_inventory, segment_documents
from bidops.qualification.runtime import Dependencies, assessment_agent, extraction_agent
from bidops.qualification.sources import SourceAdapter, canonical_json, digest
from bidops.screening.schemas import AssessmentInputs, ConditionAssessment


def implementation_version():
    root = Path(__file__).resolve().parents[3]
    revision = os.environ.get("BIDOPS_CODE_SHA")
    if not revision:
        try:
            revision = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            ).strip()
        except (OSError, subprocess.SubprocessError):
            revision = None
    revision = revision if revision and re.fullmatch(r"[a-f0-9]{40}", revision) else None
    files = sorted(Path(__file__).parent.glob("*.py"))
    source_hash = digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
    lock = Path(os.environ.get("BIDOPS_LOCK_PATH", str(root / "uv.lock")))
    try:
        lock_hash = hashlib.sha256(lock.read_bytes()).hexdigest()
    except OSError:
        lock_hash = None
    return {
        "code_sha": revision,
        "implementation_sha256": source_hash,
        "lock_sha256": lock_hash,
    }


class ScreeningService:
    def __init__(self, company_service, notice_repository, settings=None, model=None):
        self.settings = settings or ScreeningSettings()
        self.sources = SourceAdapter(company_service, notice_repository)
        self.repository = QualificationRepository(company_service.db, self.sources)
        self.model_override = model
        self._live_model = None
        self.slots = asyncio.Semaphore(1)

    def status(self):
        offline = isinstance(self.model_override, (FunctionModel, TestModel))
        return {
            "enabled": self.settings.enabled,
            "configured": bool(
                offline
                or (
                    self.settings.api_key
                    and self.settings.live_calls_enabled
                    and self.settings.ledger_verified
                )
            ),
            "model": self.model_override.model_name if offline else self.settings.model_name,
            "live_calls_enabled": self.settings.live_calls_enabled
            and self.settings.ledger_verified,
        }

    def _enabled(self):
        if not self.settings.enabled:
            raise CompanyError("screening_disabled", 503)

    def candidates(self, context, profile_revision, filters, as_of=None):
        self._enabled()
        bundle = self.sources.resolve_profile(context, profile_revision, as_of or datetime.now(UTC))
        self.sources.recheck(context, bundle)
        result = build_batch(self.sources, bundle, filters)
        self.sources.recheck(context, bundle)
        record = self.repository.save(
            context,
            bundle.manifest,
            result,
            provenance={
                **implementation_version(),
                "model_calls": 0,
                "estimated_cost_cny": "0",
                "rule_version": RULES_VERSION,
            },
        )
        return self.candidate_page(context, record["result_id"])

    def candidate_page(self, context, batch_id, offset=0, limit=20):
        self._enabled()
        record = self.repository.get(context, batch_id)
        result = record["result"]
        if result["kind"] != "candidate_batch":
            raise CompanyError("screening_batch_not_found")
        saved = result["public_versions"]
        current = self.sources.notices.db.rows(
            "SELECT version_id FROM nd_versions ORDER BY version_id"
        )
        reasons = list(record["source_state"]["stale_reasons"])
        if {row["version"]["version_id"] for row in saved} != {r["version_id"] for r in current}:
            reasons.append("public_catalog_changed")
        for value in saved:
            try:
                version, body = self.sources.notices.read_text(value["version"]["version_id"])
                if (
                    version.model_dump(mode="json") != value["version"]
                    or hashlib.sha256(body.encode()).hexdigest() != value["text_sha256"]
                ):
                    raise ValueError("fixed_public_input_changed")
            except ValueError:
                reasons.append("public_source_unavailable")
                break
        record["source_state"] = {"current": not reasons, "stale_reasons": sorted(set(reasons))}
        # Sources/owner are rechecked after potentially long public corpus verification.
        with transaction(self.repository.db) as conn:
            self.repository._authorize(context, batch_id, conn=conn)
        return page_batch(record, offset, limit)

    def _model(self):
        if isinstance(self.model_override, (FunctionModel, TestModel)):
            return self.model_override, True
        if self.model_override is not None:
            raise CompanyError("screening_unapproved_model_override", 503)
        if not self.status()["configured"]:
            raise CompanyError("screening_model_unavailable", 503)
        if self._live_model is None:
            self._live_model = OpenAIChatModel(
                self.settings.model_name,
                provider=DeepSeekProvider(api_key=self.settings.api_key.get_secret_value()),
            )
        return self._live_model, False

    def read(self, context, run_id):
        self._enabled()
        record = self.repository.get(context, run_id)
        if record["result"]["kind"] != "checklist":
            raise CompanyError("screening_run_not_found")
        return {
            **record["result"],
            "run_id": run_id,
            "manifest": record["manifest"],
            "provenance": record["provenance"],
            "source_state": record["source_state"],
        }

    async def checklist(
        self,
        context,
        profile_revision,
        project_id,
        target_package_ids=(),
        additional_material_revisions=(),
        as_of=None,
    ):
        self._enabled()
        self.sources.company.auth.require_creator(context)
        bundle = self.sources.resolve(
            context,
            profile_revision,
            project_id,
            as_of or datetime.now(UTC),
            additional_material_revisions,
        )
        if len(set(target_package_ids)) != len(target_package_ids) or not set(
            target_package_ids
        ) <= set(bundle.manifest.target_package_ids):
            raise CompanyError("screening_package_outside_closure", 422)
        targets = list(target_package_ids) or bundle.manifest.target_package_ids or [None]
        self.sources.recheck(context, bundle)
        model, offline = self._model()
        if self.slots.locked():
            raise CompanyError("screening_busy", 429)
        async with self.slots:
            return await self._check(context, bundle, targets, model, offline)

    async def _check(self, context, bundle, targets, model, offline, *, execution=None):
        run_id = execution.result_id if execution else uuid.uuid4().hex
        revision = execution.revision if execution else uuid.uuid4().hex
        guard = (
            execution.guard
            if execution
            else GuardedModel(
                model,
                db=self.repository.db,
                settings=self.settings,
                adapter=self.sources,
                context=context,
                bundle=bundle,
                run_id=run_id,
                offline=offline,
            )
        )
        extract, judge = (
            execution.agents
            if execution
            else (
                extraction_agent(guard, self.settings),
                assessment_agent(guard, self.settings),
            )
        )
        drafts, judgments, evaluated = {}, [], {}
        state, error = "completed", None

        def inventory():
            result = bind_inventory(
                bundle.documents,
                list(drafts.values()),
                lambda doc, start, end: self.sources.make_evidence(bundle, doc, start, end),
                missing_attachment_ids=bundle.manifest.missing_attachment_ids,
                max_segment_chars=self.settings.max_segment_chars,
                package_ids=bundle.manifest.target_package_ids,
            )
            if len(bundle.manifest.notice_revisions) > 1:
                result.unresolved.append(
                    UnresolvedClause(
                        document_id=bundle.documents[0].document_id,
                        reason="revision_precedence_requires_review",
                    )
                )
            return result

        documents = {doc.document_id: doc for doc in bundle.documents}
        try:
            async with asyncio.timeout(None if execution else self.settings.max_seconds):
                segments = segment_documents(
                    bundle.documents, max_segment_chars=self.settings.max_segment_chars
                )
                for index, segment in enumerate(segments):
                    if execution:
                        await execution.before_step("extraction", index, len(segments))
                    if not segment.processable:
                        # Binder explicitly records this segment as unparsed.
                        continue
                    document = documents[segment.document_id]
                    deps = Dependencies(
                        self.sources,
                        context,
                        bundle,
                        document=document,
                        segment=segment,
                        execution_check=execution.recheck if execution else None,
                    )
                    payload = {
                        "stage": "extraction",
                        "document": document.model_dump(mode="json", exclude={"text"}),
                        "segments": [segment.model_dump(mode="json")],
                        "package_ids": bundle.manifest.target_package_ids,
                        "as_of": bundle.manifest.as_of.isoformat(),
                        "revision_precedence_unresolved": len(bundle.manifest.notice_revisions) > 1,
                    }
                    result = await extract.run(
                        canonical_json(payload),
                        deps=deps,
                        usage_limits=UsageLimits(request_limit=3, tool_calls_limit=6),
                    )
                    previous = drafts.get(document.document_id)
                    drafts[document.document_id] = ExtractionDraft(
                        document_id=document.document_id,
                        segments=[
                            *(previous.segments if previous else []),
                            *result.output.segments,
                        ],
                    )
                extracted = inventory()
                total = sum(
                    applicability(c, p).disposition != "out_of_scope"
                    for p in targets
                    for c in extracted.conditions
                )
                assessed = 0
                for package in targets:
                    for condition in extracted.conditions:
                        if applicability(condition, package).disposition == "out_of_scope":
                            continue
                        if execution:
                            await execution.before_step("assessment", assessed, total)
                        deps = Dependencies(
                            self.sources,
                            context,
                            bundle,
                            condition=condition,
                            package_id=package,
                            execution_check=execution.recheck if execution else None,
                        )
                        payload = {
                            "stage": "assessment",
                            "condition": condition.model_dump(mode="json"),
                            "target_package_id": package,
                            "profile": bundle.profile,
                            "material_versions": [
                                r.model_dump(mode="json")
                                for r in bundle.manifest.material_revisions
                            ],
                            "as_of": bundle.manifest.as_of.isoformat(),
                        }
                        result = await judge.run(
                            canonical_json(payload),
                            deps=deps,
                            usage_limits=UsageLimits(request_limit=4, tool_calls_limit=6),
                        )
                        judgments.append(
                            {"target_package_id": package, **result.output.model_dump(mode="json")}
                        )
                        evaluated[(package, condition.condition_id)] = assess_condition(
                            condition,
                            result.output.judgments,
                            target_package_id=package,
                            as_of=bundle.manifest.as_of,
                        )
                        assessed += 1
        except TimeoutError:
            state, error = "timed_out", "screening_timeout"
        except CompanyError as exc:
            # A revoked source cannot be re-released as a partial result.
            self.sources.recheck(context, bundle)
            state = "timed_out" if exc.detail == "screening_timeout" else "failed"
            error = exc.detail
        except (AgentRunError, ValueError):
            state, error = "failed", "screening_model_output_invalid"
        except Exception:
            # Do not expose provider exception messages containing private prompts.
            state, error = "failed", "screening_model_failed"
        extracted = inventory()
        groups, summaries, scope = [], [], []
        for package in targets:
            assessments = []
            for condition in extracted.conditions:
                application = applicability(condition, package)
                scope.append(application.model_dump(mode="json"))
                if application.disposition == "out_of_scope":
                    continue
                assessment = evaluated.get((package, condition.condition_id))
                if assessment is None:
                    assessment = ConditionAssessment(
                        condition_id=condition.condition_id,
                        status="information_insufficient",
                        reason="assessment_not_completed",
                        notice_evidence=condition.notice_evidence,
                        company_evidence=[],
                        missing_items=["assessment_not_completed"],
                    )
                assessments.append(assessment)
            for primary in bundle.manifest.notice_revisions:
                conditions = conditions_for_group(extracted, primary, package)
                keys = {condition.condition_id for condition in conditions}
                group = build_assessment_group(
                    AssessmentInputs(
                        project_family_id=bundle.manifest.project_id,
                        project_id=bundle.manifest.project_id,
                        notice_revision=primary,
                        profile_revision=bundle.manifest.profile_revision,
                        material_revisions=bundle.manifest.material_revisions,
                        attachment_revisions=bundle.manifest.attachment_revisions,
                        as_of=bundle.manifest.as_of,
                    ),
                    conditions,
                    [a for a in assessments if a.condition_id in keys],
                    target_package_id=package,
                    assessment_id=digest([run_id, primary.key(), package]),
                    result_revision=revision,
                )
                if group:
                    groups.append(group.model_dump(mode="json"))
            summary = aggregate_recommendation(
                extracted,
                assessments,
                project_status=ProjectStatus.model_validate(bundle.manifest.project_status),
                target_package_id=package,
                execution_complete=state == "completed",
            )
            summaries.append({"target_package_id": package, **summary.model_dump(mode="json")})
        recommendations = {s["recommendation"] for s in summaries}
        overall = recommendations.pop() if len(recommendations) == 1 else "not_evaluated"
        result = {
            "kind": "checklist",
            "run_id": run_id,
            "result_revision": revision,
            "state": state,
            "inventory": extracted.model_dump(mode="json"),
            "groups": groups,
            "applicability": scope,
            "node_judgments": judgments,
            "package_summaries": summaries,
            "recommendation": overall,
            "human_review_state": "unreviewed",
            "error_code": error,
            "missing_items": sorted(
                {m for s in summaries for m in s["missing_items"]}
                | set(extracted.missing_attachment_ids)
                | {u.reason for u in extracted.unresolved}
            ),
        }
        provenance = {
            **implementation_version(),
            **guard.provenance(),
            "prompt_version": PROMPT_VERSION,
            "rule_version": RULES_VERSION,
            "prompt_sha256": digest([EXTRACTION_INSTRUCTIONS, ASSESSMENT_INSTRUCTIONS]),
            "parameters": {
                "temperature": 0,
                "max_tokens": self.settings.max_output_tokens,
                "max_requests": self.settings.max_requests,
                "max_retries": 0,
                "max_seconds": self.settings.max_seconds,
                "max_segment_chars": self.settings.max_segment_chars,
                "max_request_bytes": self.settings.max_request_bytes,
                "budget_cny": str(self.settings.budget_cny),
                "input_cny_per_million": str(self.settings.input_cny_per_million),
                "output_cny_per_million": str(self.settings.output_cny_per_million),
            },
        }
        self.sources.recheck(context, bundle)
        if execution:
            execution.recheck()
            return await execution.persist(result, provenance, guard.outputs)
        self.repository.save(
            context,
            bundle.manifest,
            result,
            raw_model_outputs=guard.outputs,
            provenance=provenance,
            result_id=run_id,
        )
        return self.read(context, run_id)

    async def close(self):
        if self._live_model:
            await self._live_model.__aexit__(None, None, None)
