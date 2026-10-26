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


def _agent(model, output_type, instructions, settings, *, output_retries=1, **options):
    return Agent(
        model,
        deps_type=Dependencies,
        output_type=ToolOutput(output_type),
        instructions=instructions,
        retries={"tools": 1, "output": output_retries},
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
    agent = _agent(
        model, ExtractionDraft, EXTRACTION_INSTRUCTIONS, settings, output_retries=3, **options
    )

    @agent.tool
    def locate_notice_quotes(ctx: RunContext[Dependencies], quotes: list[str]) -> dict:
        """精确定位本次公告段落中的原文，批量返回绝对字符位置；重复匹配须结合上下文选择。"""
        deps = ctx.deps
        deps.recheck()
        if (
            not 1 <= len(quotes) <= 32
            or any(not isinstance(quote, str) or not quote.strip() for quote in quotes)
            or sum(len(quote) for quote in quotes) > 4000
        ):
            raise ModelRetry("每次提供1至32条非空原文，总长度不超过4000字符。")
        segment = deps.segment
        results = []
        for quote in quotes:
            matches, count, offset = [], 0, 0
            while (position := segment.text.find(quote, offset)) != -1:
                count += 1
                if len(matches) < 20:
                    matches.append(
                        {
                            "start": segment.start + position,
                            "end": segment.start + position + len(quote),
                        }
                    )
                offset = position + 1
            results.append(
                {
                    "quote": quote,
                    "matches": matches,
                    "match_count": count,
                    "truncated": count > len(matches),
                }
            )
        deps.recheck()
        return {
            "document_id": deps.document.document_id,
            "segment_id": segment.segment_id,
            "results": results,
        }

    @agent.output_validator
    def validate(ctx: RunContext[Dependencies], draft: ExtractionDraft):
        deps = ctx.deps
        deps.recheck()
        if draft.document_id != deps.document.document_id or [
            s.segment_id for s in draft.segments
        ] != [deps.segment.segment_id]:
            raise ModelRetry(
                "必须完整且只覆盖服务端本次指定的文档段落：segments 只能包含当前 segment_id "
                "的一条记录，不能为同一段分别重复提交 requirement/not_requirement/unparsed。"
            )
        try:
            bind_inventory(
                [deps.document],
                [draft],
                lambda doc, start, end: deps.adapter.make_evidence(deps.bundle, doc, start, end),
                max_segment_chars=settings.max_segment_chars,
                package_ids=deps.bundle.manifest.target_package_ids,
            )
        except CompanyError:
            raise ModelRetry("条件跨度、原文、采购包或引用结构未通过服务端核验。") from None
        except ValueError as exc:
            # Only publish fixed diagnostics, never arbitrary exception contents.
            hints = {
                "expression_text_anchor_mismatch": (
                    "expression_text_anchor_mismatch：某个 expression.text 与其跨度原文不一致。"
                    "请把根节点和全部 operands 子节点的 text 分别交给 locate_notice_quotes，"
                    "逐一使用返回的位置，不能从父节点位置自行计数。"
                ),
                "expression_span_outside_condition": (
                    "expression_span_outside_condition：子节点必须包含于父节点，"
                    "根节点必须包含于条件跨度；请重新定位相关原文。"
                ),
                "condition_span_outside_segment": (
                    "condition_span_outside_segment：条件只能引用本次给定段落内的原文。"
                ),
                "draft_package_not_in_inputs": (
                    "draft_package_not_in_inputs：采购包只能使用本次输入的标识；"
                    "未知范围请保留 unknown。"
                ),
            }
            raise ModelRetry(
                hints.get(str(exc), "条件跨度、原文、采购包或引用结构未通过服务端核验。")
            ) from None
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
        """读取已授权的固定材料。首次仅传版本号，不猜测end；按返回total/end继续分页。"""
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
            raise ModelRetry(
                f"材料范围无效：这份已授权材料共 {len(normalized)} 字符，"
                "必须 0 <= start < end <= 总长度，单页最多4000字符。"
                "首次读取请只传 material_version_id，省略 start/end，"
                "由工具返回实际总长度与下一页信息；不要估算 end。"
            )
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
        atoms = [
            node.node_id for node in deps.condition.expression.nodes() if node.operator == "atom"
        ]
        supplied = [judgment.node_id for judgment in draft.judgments]
        if len(supplied) != len(atoms) or set(supplied) != set(atoms):
            raise ModelRetry(
                "judgments 必须恰好覆盖下列 atom 节点一次，不得提交 all/any/not/unless 父节点："
                + ", ".join(atoms)
                + "。父节点结论由服务器计算；company_evidence 必须原样复制已返回的 citation。"
            )
        if any(
            judgment.status in {"satisfied", "not_satisfied", "not_applicable"}
            and not judgment.company_evidence
            for judgment in draft.judgments
        ):
            raise ModelRetry(
                "definite_judgment_requires_company_evidence：缺少企业证据，不能提交"
                " satisfied/not_satisfied/not_applicable。不要编造或引用无关材料。"
                "应保留 information_insufficient 并列出需要核实的材料或适用性问题。"
                "若原条款只是说明无额外要求，须提示人工确认该条是否构成实质资格条件。"
            )
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
                "判断必须使用真实授权证据与固定有效期；company_evidence 中每个引用应完整原样复制"
                "工具返回的 citation 或画像 evidence，不能保留旧ID却改写"
                "quote、start/end、哈希或有效期。"
                "没有对应依据时，scope_evidence_ids/validity_evidence_ids 留空；缺证明不能判满足。"
            ) from None
        deps.recheck()
        return draft

    return agent
