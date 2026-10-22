# Project board — dated 2026-10-22

## Enterprise application goal (new explicit authorization)

- [ ] [Enterprise goal #13](https://github.com/ZZZZihan/BidOps/issues/13): complete E1 / E2 / E3 gates in [GOAL.md](GOAL.md).
- [x] Framework research; Authlib OIDC + PKCE and backend RBAC, no anonymous fallback.
- [x] Owner-scoped persistent conversations, request idempotency, failure preservation and rate limits.
- [x] Audit UI / append-only application audit / bounded metric labels / retention / backup-restore tooling.
- [x] Offline auth/security/isolation tests and real DeepSeek persistence integration.
- [x] [E1 #14](https://github.com/ZZZZihan/BidOps/issues/14) Engineering baseline: [implementation](https://github.com/ZZZZihan/BidOps/commit/6a41a067b08f67b5d9c46bd68f5b3acc0101bf80), 189 backend / 12 UI tests and container smoke verified directly from [CI job logs](https://github.com/ZZZZihan/BidOps/actions/runs/39795653783). GitHub summary status may lag during its reported incident; enterprise business acceptance remains below.
- [ ] Real company SSO / HTTPS / two-user acceptance and target-machine operations drill.
- [ ] Independent representative data quality gate; production workload/security requirements and release review.

Engineering test results and unexecuted work are separated in the enterprise run report.
This goal does not authorize competition submission or automatically pass P1.

## Separately authorized Agent Demo (does not pass P1/P2 gates)

- [x] [Demo-01](https://github.com/ZZZZihan/BidOps/issues/12) Research frameworks; use Pydantic AI + official DeepSeek API, read-only tools and evidence UI.
- [x] Freeze 96 scenarios / 105 prompts with Pydantic Evals; run 24 development and 72 held-out cases. First held-out score: 98.67/100, 71/72 passed.
- [x] Preserve the failed count-limit case and report a separate post-fix regression run.
- [x] Publish [final run report](reports/demo-run-20261022.md), verified source commit and passing CI; human demo acceptance pending.

This scope was explicitly requested after P0. The original independent-data and competition
gates below remain pending; the Demo does not start those phases automatically.

## P0: engineering and six real samples delivered; human acceptance pending
- [ ] [P0-01](https://github.com/ZZZZihan/BidOps/issues/1) Obtain organizer answers on eligibility, registration, IP and submission route (user action needed).
- [ ] [P0-02](https://github.com/ZZZZihan/BidOps/issues/2) Audit actual public tender pages, site ToS/robots, user-agent/access limitations; no bypass.
- [x] [P0-03](https://github.com/ZZZZihan/BidOps/issues/3) Initialize `uv` Python 3.12 backend + FastAPI health endpoint, SQLite migration, React/Vite TypeScript skeleton and CI.
- [x] [P0-04](https://github.com/ZZZZihan/BidOps/issues/4) Design immutable raw-data snapshots, manifests, URL dedupe and source evidence chain.
- [x] [P0-05](https://github.com/ZZZZihan/BidOps/issues/5) Build manual URL importer and optional *allowlisted, rate-limited* public HTML fetcher with dry-run default.
- [x] [P0-06](https://github.com/ZZZZihan/BidOps/issues/6) Deterministic parser for at least 6 seeded notices and explicit unsupported cases; never infer unknown fields.
- [x] [P0-07](https://github.com/ZZZZihan/BidOps/issues/7) Implement audit/event logs and error retry/timeout tests.
- [x] [P0-08](https://github.com/ZZZZihan/BidOps/issues/8) Produce real run report with results, provenance and limitations (not aspirational PASS).

## P1: data verification (next phase; do not start automatically)
- [ ] Label >=30 notice samples from truly permitted sources and their source snapshots.
- [ ] Establish field-level gold labels and errors; status, currency, date normalizations.
- [ ] Manual review of ambiguous qualification statements, missing files, notices amended/cancelled.
- [ ] Generate metrics report and assess gate for harness.

## P2: agent harness (BLOCKED on P1 pass)
- [ ] Design read-only notice search and evidence tools, controlled state machine, bounded execution, abstention, human review.
- [ ] Run fixed benchmark for evidence correctness and no unsupported eligibility decisions.

## P3: competition submission (BLOCKED on P2 and official eligibility verification)
- [ ] Applicant registration / official form
- [ ] MVP demo link or executable package + rehearsed offline fallback
- [ ] Deck: background, design, AI dev process, screenshots, grounded use case
- [ ] Technical document: AI-assisted code share **method**, tools, feature logic, legal sources
- [ ] Verify upload/QR process and retain confirmation


## Current acceptance limits (2026-10-22)

- #1 remains a user/organizer action. No registration performed.
- #2 the user-requested six-URL access check returned 6 HTTP 200 notice bodies, no failures. Recurring collection/reuse permission remains unconfirmed; source registry is still unknown.
- #6 parser p0.2 processed all six actual bodies and 15 procurement packages. Offline replay verified 90 evidence spans, six duplicate imports and a stable manifest. This is not P1 gold-label evaluation.
- #8 the earlier zero-capture report is retained; reports/p0-collection-20261022.md records the follow-up. P0 awaits human access/acceptance review.
- P1 backlog: #9; P2 backlog: #10; P3 backlog: #11. None has started.


## Next business loop: individually accepted implementation PRs

The user requested PR decomposition with an acceptance criterion for every task.
[Full plan and acceptance matrix](docs/ENTERPRISE-PR-PLAN.md).
B01–B10 below are planning IDs. Implementation progress and actual PR numbers are recorded separately.

- [x] B01 — Screening contracts and independent evaluation protocol (B01-01 through B01-07): [implementation PR #16](https://github.com/ZZZZihan/BidOps/pull/16), 571 backend tests on `4d22667acc4001b57d3dfca248a7cb78d5636587`; [per-item evidence](reports/b01-contracts-eval-20261023.md). Code delivered for review, not merged; P1/B08 real acceptance remains BLOCKED.
- [ ] B02 — Versioned notices, packages, evidence and complete pagination (B02-01 through B02-06).
- [ ] B03 — Company profiles, document access, IdP revocation and retention (B03-01 through B03-08).
- [ ] B04 — Candidate screening and evidence-backed qualification checklist (B04-01 through B04-07).
- [ ] B05 — Durable runs, cancellation, checkpoints and recovery (B05-01 through B05-07).
- [ ] B06 — Human decisions, material requests and resumable review (B06-01 through B06-07).
- [ ] B07 — Authorized incremental updates and attachment ingestion (B07-01 through B07-07).
- [ ] B08 — Held-out evaluation and real human time comparison (B08-01 through B08-07).
- [ ] B09 — Declared capacity, alerts and complete recovery drills (B09-01 through B09-07).
- [ ] B10 — Real IdP/HTTPS/user business acceptance and release verdict (B10-01 through B10-06).

Each implementation PR must also satisfy G-01 through G-08 and attach per-item evidence.
Existing P1/E2/E3 checkboxes remain open. Synthetic tests, code merge and unavailable external inputs
must not be represented as independent business acceptance.

