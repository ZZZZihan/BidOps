"""Local browser server: real persistence/ACL/runtime, signed OIDC and FunctionModel.

UNIT clauses and answers are deliberately authored engineering fixtures. Passing
this fixture says nothing about semantic extraction accuracy or a real IdP/model.
No production authentication or authorization bypass is installed.
"""

import json
import os
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

import httpx2
import uvicorn
from bidops.agent_demo.runtime import DemoSettings
from bidops.api import create_app
from bidops.config import Settings
from bidops.qualification.config import ScreeningSettings
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

# Reuse the existing protocol and immutable notice/attachment test builders,
# rather than duplicating their signing, PKCE or database persistence logic.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_enterprise as oidc  # noqa: E402
from test_notice_attachments import attachment  # noqa: E402
from test_notice_versions import persist  # noqa: E402

models.ALLOW_MODEL_REQUESTS = False
data = Path(os.environ["BIDOPS_SCREENING_BROWSER_DATA"])
origin = os.environ["BIDOPS_SCREENING_BROWSER_ORIGIN"]
oidc.ORIGIN = origin
calls = []


class BrowserProvider(oidc.ProtocolProvider):
    def handle(self, request):
        if request.url.path.endswith("/token"):
            code = json.loads(parse_qs(request.content.decode())["code"][0])
            self.subject, self.roles = code["sub"], code["roles"]
            self.nonce, self.challenge = code["nonce"], code["challenge"]
        return super().handle(request)


def expression(text, start, operator="atom", operands=None):
    return {
        "start": start,
        "end": start + len(text),
        "operator": operator,
        "text": text,
        "operands": operands or [],
    }


def extraction(prompt):
    segments = []
    for segment in prompt["segments"]:
        conditions = []
        for match in re.finditer(r"^UNIT_[A-E][^\n]*", segment["text"], re.M):
            source = match.group()
            start = segment["start"] + match.start()
            logic = expression(source, start)
            if source.startswith("UNIT_E"):
                base, exception = "要求 E", "豁免 UNIT_EXCEPTION"
                logic = expression(
                    source,
                    start,
                    "unless",
                    [
                        expression(base, start + source.index(base)),
                        expression(exception, start + source.index(exception)),
                    ],
                )
            conditions.append(
                {
                    "start": start,
                    "end": start + len(source),
                    "hard_requirement": True,
                    "expression": logic,
                    "scope_kind": "all",
                    "validity": {"kind": "not_required"},
                }
            )
        segments.append(
            {
                "segment_id": segment["segment_id"],
                "disposition": "requirement" if conditions else "not_requirement",
                "conditions": conditions,
            }
        )
    return {"document_id": prompt["document"]["document_id"], "segments": segments}


def assessment(prompt):
    condition = prompt["condition"]
    evidence = [ref for fact in prompt["profile"]["facts"] for ref in fact["evidence"]]
    assert len(evidence) >= 2
    text = condition["original_text"]

    def judgment(node, status, refs=None, **extra):
        return {
            "node_id": node["node_id"],
            "status": status,
            "reason": "UNIT deterministic engineering answer; not a semantic score",
            "company_evidence": refs or [],
            **extra,
        }

    node = condition["expression"]
    if text.startswith("UNIT_E"):
        values = [
            judgment(node["operands"][0], "information_insufficient", missing_items=["UNIT_E"]),
            judgment(node["operands"][1], "satisfied", [evidence[1]]),
        ]
    elif text.startswith("UNIT_D"):
        values = [
            judgment(
                node,
                "evidence_conflict",
                evidence[:2],
                conflicting_evidence_ids=[ref["evidence_id"] for ref in evidence[:2]],
            )
        ]
    elif text.startswith("UNIT_C"):
        values = [judgment(node, "information_insufficient", missing_items=["UNIT_C 缺少证明"])]
    else:
        status = "satisfied" if text.startswith("UNIT_A") else "not_satisfied"
        values = [judgment(node, status, [evidence[0 if status == "satisfied" else 1]])]
    return {"condition_id": condition["condition_id"], "judgments": values}


def respond(messages, info):
    prompt = next(
        json.loads(part.content)
        for message in reversed(messages)
        for part in reversed(message.parts)
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    )
    assert prompt["stage"] in {"extraction", "assessment"}
    calls.append({"stage": prompt["stage"], "index": len(calls) + 1})
    (data / "calls.json").write_text(json.dumps(calls))
    if prompt["stage"] == "extraction":
        assert "text" not in prompt["document"]
        answer = extraction(prompt)
    else:
        if prompt["profile"]["display_name"] == "UNIT_FORCE_FAILURE":
            raise RuntimeError("UNIT deliberate offline provider failure")
        # Exercise one real authorized material tool round-trip per UNIT_A.
        tool_return = next(
            (
                part
                for message in messages
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_name == "read_company_material"
            ),
            None,
        )
        if prompt["condition"]["original_text"].startswith("UNIT_A") and tool_return is None:
            ref = prompt["profile"]["facts"][0]["evidence"][0]
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "read_company_material",
                        {
                            "material_version_id": ref["version"]["version_id"],
                            "start": ref["start"],
                            "end": ref["end"],
                        },
                    )
                ],
                model_name="offline-screening-browser",
                usage=RequestUsage(input_tokens=100, output_tokens=50),
            )
        if tool_return is not None:
            returned = tool_return.content
            assert returned["text"] == returned["citation"]["quote"]
            assert returned["offset"] == returned["citation"]["start"]
            assert returned["total"] >= returned["end"]
        answer = assessment(prompt)
    return ModelResponse(
        parts=[ToolCallPart(info.output_tools[0].name, answer)],
        model_name="offline-screening-browser",
        usage=RequestUsage(input_tokens=100, output_tokens=50),
    )


settings = Settings(
    data_dir=data,
    database_url="sqlite:///" + str(data / "browser.sqlite3"),
    source_registry=Path("config/sources.json"),
    network_enabled=False,
)
enterprise = oidc.enterprise(allow_insecure_loopback=True, requests_per_minute=120, _env_file=None)
app = create_app(
    settings,
    DemoSettings(enabled=False, _env_file=None),
    enterprise,
    screening_settings=ScreeningSettings(enabled=True, live_calls_enabled=False, _env_file=None),
)
original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def lifespan(app):
    async with original_lifespan(app):
        provider = BrowserProvider()
        app.state.identity.oauth.enterprise.client_kwargs["transport"] = httpx2.MockTransport(
            provider.handle
        )
        app.state.screening.model_override = FunctionModel(
            respond, model_name="offline-screening-browser"
        )
        body = (
            "项目名称：UNIT_SCREENING_PROJECT\n项目编号：UNIT-SCREENING\n"
            "采购人名称：UNIT采购中心\n公告类型：招标公告\n"
            "投标截止时间：2099年11月30日10:00\n第1包\n预算金额：100元\n"
            "UNIT_A 要求证明 A。\nUNIT_B 要求证明 B。\n"
            "UNIT_C 要求证明 C。\nUNIT_D 要求解决证明冲突。\n"
        )
        notice = persist(app.state.store.notice_data, body)
        record = app.state.store.notice_data.record_attachment(
            attachment(notice), "UNIT_E 条款：要求 E，已列明豁免 UNIT_EXCEPTION。".encode()
        )
        persist(
            app.state.store.notice_data,
            body.replace("UNIT_SCREENING_PROJECT", "OTHER_PROJECT").replace(
                "UNIT-SCREENING", "UNIT-OTHER"
            ),
            path="other",
        )
        persist(
            app.state.store.notice_data,
            body.replace("UNIT_SCREENING_PROJECT", "UNIT_UNKNOWN_PROJECT")
            .replace("UNIT-SCREENING", "UNIT-UNKNOWN")
            .replace("预算金额：100元\n", ""),
            path="unknown",
        )
        (data / "seed.json").write_text(
            json.dumps(
                {
                    "project_id": notice.project_id,
                    "notice_revision": notice.version_ref.model_dump(mode="json"),
                    "attachment_record_id": record.record_id,
                }
            )
        )
        (data / "calls.json").write_text("[]")
        yield


app.router.lifespan_context = lifespan
if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ["BIDOPS_SCREENING_BROWSER_PORT"]),
        log_level="warning",
    )
