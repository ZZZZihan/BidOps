"""Two bounded Pydantic AI agents for the specific qualification business steps."""

from dataclasses import dataclass

from pydantic import Field
from pydantic_ai import Agent, ModelRetry, RunContext, ToolOutput
from pydantic_ai.models.openai import OpenAIChatModelSettings

from bidops.company.authorization import CompanyError
from bidops.qualification.contracts import ExtractionDraft, NodeJudgment, QualificationModel
from bidops.qualification.logic import assess_condition
from bidops.qualification.prompts import ASSESSMENT_INSTRUCTIONS, EXTRACTION_INSTRUCTIONS
from bidops.qualification.requirements import bind_inventory


class JudgmentDraft(QualificationModel):
    condition_id: str
    judgments: list[NodeJudgment] = Field(min_length=1)


@dataclass
class Dependencies:
    adapter: object
    context: object
    bundle: object
    document: object | None = None
    segment: object | None = None
    condition: object | None = None
    package_id: str | None = None
    execution_check: object | None = None

    def recheck(self):
        if self.execution_check is not None:
            self.execution_check()
        self.adapter.recheck(self.context, self.bundle)


def _agent(model, output_type, instructions, settings, **options):
    return Agent(
        model,
        deps_type=Dependencies,
        output_type=ToolOutput(output_type),
        instructions=instructions,
        retries=1,
        tool_timeout=5,
        **options,
        model_settings=OpenAIChatModelSettings(
            thinking=False,
            extra_body={"thinking": {"type": "disabled"}},
            temperature=0,
            max_tokens=settings.max_output_tokens,
            max_retries=0,
            parallel_tool_calls=False,
            request_timeout=min(30, settings.max_seconds),
        ),
    )


def extraction_agent(model, settings, **options):
    agent = _agent(model, ExtractionDraft, EXTRACTION_INSTRUCTIONS, settings, **options)

    @agent.output_validator
    def validate(ctx: RunContext[Dependencies], draft: ExtractionDraft):
        deps = ctx.deps
        deps.recheck()
        if draft.document_id != deps.document.document_id or [
            s.segment_id for s in draft.segments
        ] != [deps.segment.segment_id]:
            raise ModelRetry("必须完整且只覆盖服务端本次指定的文档段落。")
        try:
            bind_inventory(
                [deps.document],
                [draft],
                lambda doc, start, end: deps.adapter.make_evidence(deps.bundle, doc, start, end),
                max_segment_chars=settings.max_segment_chars,
                package_ids=deps.bundle.manifest.target_package_ids,
            )
        except (ValueError, CompanyError):
            raise ModelRetry("条件跨度、原文、采购包或引用结构未通过服务端核验。") from None
        return draft

    return agent


def assessment_agent(model, settings, **options):
    agent = _agent(model, JudgmentDraft, ASSESSMENT_INSTRUCTIONS, settings, **options)

    @agent.tool
    def read_company_material(
        ctx: RunContext[Dependencies],
        material_version_id: str,
        start: int = 0,
        end: int | None = None,
    ) -> dict:
        """读取本任务已授权且固定版本的企业材料段落；范围以字符计，可继续分页。"""
        deps = ctx.deps
        deps.recheck()
        ref = next(
            (
                r
                for r in deps.bundle.manifest.material_revisions
                if r.version_id == material_version_id
            ),
            None,
        )
        if ref is None:
            raise ModelRetry("材料版本不在本任务授权输入内。")
        normalized = deps.bundle.material_texts[material_version_id]
        end = min(len(normalized), start + 4000) if end is None else end
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(normalized)
            or end - start > 4000
        ):
            raise ModelRetry("材料范围无效；单页最多4000字符。")
        citation = deps.adapter.company.materials.citation(
            deps.context, ref.object_id, ref.version_id, start, end
        )
        # Preserve server-bound validity assertions on identical fixed profile spans.
        for fact in deps.bundle.profile["facts"]:
            for original in fact["evidence"]:
                if original["evidence_id"] == citation["evidence_id"]:
                    citation = original
        deps.adapter.resolve_evidence(deps.context, deps.bundle, citation)
        deps.recheck()
        return {
            "text": normalized[start:end],
            "offset": start,
            "end": end,
            "total": len(normalized),
            "has_more": end < len(normalized),
            "truncated": start > 0 or end < len(normalized),
            "citation": citation,
        }

    @agent.output_validator
    def validate(ctx: RunContext[Dependencies], draft: JudgmentDraft):
        deps = ctx.deps
        deps.recheck()
        if draft.condition_id != deps.condition.condition_id:
            raise ModelRetry("只能判断本次指定条件。")
        try:
            for judgment in draft.judgments:
                for ref in judgment.company_evidence:
                    deps.adapter.resolve_evidence(deps.context, deps.bundle, ref)
            assess_condition(
                deps.condition,
                draft.judgments,
                target_package_id=deps.package_id,
                as_of=deps.bundle.manifest.as_of,
            )
        except (ValueError, CompanyError):
            raise ModelRetry(
                "判断必须覆盖所有原子条件，使用真实授权证据与固定有效期；缺证明不能判满足。"
            ) from None
        deps.recheck()
        return draft

    return agent
