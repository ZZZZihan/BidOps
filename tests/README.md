# Engineering tests, not procurement samples

Test inputs are self-authored, minimal syntax fragments and HTTP protocol doubles.
`UNIT-*` identifiers and test text exist only in temporary test databases. They are not
government announcements, lawful real-source fixtures, gold annotations, demo data, or
evidence that any seed URL was collected. No fixtures are loaded by the app.

Tests cover money, date precision/timezone, missing and conflicting fields, quotes and
offsets, packages, version preservation, byte-level and URL dedupe, manifests, integrity
failures, API/CLI behavior, permission gates, redirects, HTTP errors and bounded retries.
Real fixture coverage is currently zero; that acceptance gap is tracked in Issue #6.

Agent Demo tests additionally exercise the Pydantic AI loop using FunctionModel and an
in-memory DeepSeek HTTP transport: source and quote tampering, Decimal bounds, missing
values, immutable versions, unknown tools, malformed outputs, credential-like input,
provider 401/402/403/429/500, timeout, limits and API origin/validation. Real HTTP transports
and live model requests are forbidden by the autouse fixture. Evaluator tests intentionally
corrupt answers to check that the scorer rejects them. None of these mocks count as a live
Agent score. Browser protocol tests cover missing credentials, empty corpus, errors,
HTML escaping, keyboard submission and responsive layout without any provider call.

The separately opt-in scripts/demo_eval.py runs the frozen real-corpus cases through
DeepSeek and Pydantic Evals. See docs/DEMO-EVALUATION.md for denominators and limitations.
