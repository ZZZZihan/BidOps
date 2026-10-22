# ADR-0002: Framework-first DeepSeek demo

Decision date: 2026-10-22. Scope is authorized by the user's new Demo request; see GOAL.md.

## Research before implementation

| Candidate | Official capability checked | Decision |
|---|---|---|
| Pydantic AI 2.59.0 | Native DeepSeek provider, typed tools/output, dependency injection, retry and usage limits | Selected; fits the existing Pydantic/FastAPI data contract |
| LangChain create_agent / LangGraph | Prebuilt agent loop, tools and middleware; LangGraph stateful orchestration | Viable alternative, adds a second abstraction set for this small typed application |
| Pydantic Evals 2.59.0 | Dataset/Case/evaluators and local reports | Selected; no paid evaluation or telemetry service required |

Primary sources inspected:

- https://pydantic.dev/docs/ai/models/deepseek/
- https://pydantic.dev/docs/ai/evals/evals/
- https://docs.langchain.com/oss/python/langchain/overview
- https://api-docs.deepseek.com/guides/tool_calls/
- https://api-docs.deepseek.com/quick_start/pricing/
- https://api-docs.deepseek.com/zh-cn/quick_start/pricing/

The user's credential was checked against the official GET /models endpoint: HTTP 200,
available IDs deepseek-flash and deepseek-v4-pro. No credential value is recorded here.
The official pricing page maps deepseek-flash to DeepSeek-V4.1-Flash-1017. Pydantic's
provider documentation recommends disabling thinking for reliable tool-based structured
output. In two development preflights, `thinking=False` alone did not disable thinking for
the newly introduced `deepseek-flash` alias in Pydantic AI 2.59.0; DeepSeek returned HTTP 400
because thinking and forced structured-output tool choice conflicted. The verified setting
is `OpenAIChatModelSettings(thinking=False, extra_body={"thinking": {"type": "disabled"}})`.
This configures the framework's official provider, without writing a chat-completions loop.
The lockfile records the versions actually installed. Runtime responses will record the
model identifier actually returned, since aliases may change.

## Assembly and boundaries

Pydantic AI Agent + DeepSeekProvider calls bounded application tools over existing SQLite
snapshots. The model chooses tools and result references; typed output validation checks
those references. Application code resolves facts, original spans, URLs and comparison
arithmetic from the source store. The UI renders these validated results and a tool trace.
This is a constrained procurement assistant, not an unrestricted free-text agent.

Application code owns domain queries, immutable evidence lookup, rendering, API/session
adapters and scoring rubrics. Pydantic AI owns the agent loop, schema handling, tool
dispatch, model retries and run budgets. Pydantic Evals owns case execution and reports.
No custom agent orchestration framework, planner loop or multi-agent architecture is built.

Only structured notice fields and necessary public evidence snippets are sent to DeepSeek.
The toolset has no shell, file reader, arbitrary SQL, web fetch, credential or bidding action.
No fresh government-site requests are needed. Unknown fields and current status stay unknown;
a future stored deadline alone does not prove an opportunity is currently open.

## Cost and evaluation

Use conservative peak, cache-miss Flash rates from the dated official Chinese price page:
CNY 2 / million input tokens and CNY 8 / million output tokens. Estimates are not invoices;
off-peak/cache discounts may lower charges. Bound each run's calls/tokens and the evaluation
batch's estimated spend. Real provider runs, offline protocol mocks and browser checks are
reported separately. Frozen expected values come from reviewed stored source evidence,
not from calling the implementation under test to manufacture an oracle.
