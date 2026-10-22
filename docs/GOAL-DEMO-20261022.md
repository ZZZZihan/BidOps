# BidOps Agent Demo goal — 2026-10-22

The user explicitly requested a working DeepSeek-powered Agent Demo, framework research
before implementation, a comprehensive test set, actual evaluation and a score. This new
authorization expands beyond the earlier P0-only task. It does not retroactively pass P1
or authorize new crawling, bidding, login/CA access, production deployment or registration.

## Goal and acceptance targets (targets, not measured results)

Deliver a local React/FastAPI workbench where a single framework-managed agent can search
the six real captured notices, read fields and original evidence, compare budgets/deadlines
and packages, handle follow-up questions, and abstain on missing/current-status/eligibility
claims. Every displayed procurement fact resolves to stored data and evidence. A typed
response renderer must not allow the model to invent numeric values, URLs or quotes.

- Use DeepSeek's official API, model `deepseek-flash`, through Pydantic AI.
- Delegate the model/tool loop, output validation/retries and usage limits to the framework.
- Use Pydantic Evals for the dataset runner and deterministic task evaluators.
- Preserve the current six-snapshot corpus and record its manifest SHA256.
- Target at least 80 distinct evaluation cases covering facts, search, comparison, missing
  data, unsafe instructions and multi-turn context. Separate development and held-out cases.
- Score target: >=85/100 on the declared demo corpus; zero credential disclosure, external
  writes or fabricated source references. This is not a production-quality or P1 gold score.
- Add offline integration/error-path tests and browser workflow checks. Report all failures,
  skipped/unexecuted cases, latency, token usage and estimated cost separately from scores.
- Limit development/evaluation spend to a conservative estimated CNY 10 total, using peak
  uncached token prices as the estimate. Stop at the limit or unrecoverable provider errors.
- Keep the key in ignored local `private/deepseek.env` mode 0600; never publish the key,
  raw notices, full private traces or contact details. No external telemetry is enabled.

## Delivery

Runnable source, setup/run commands, a frozen test manifest, evaluation JSON/Markdown,
recorded actual model/version, safe tool-call traces in the UI, and GitHub commit/CI links.
Keep the prior P0 reports as historical evidence. The final result must distinguish demo
success from remaining source-policy and independent-data-validation work.
