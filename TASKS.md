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
- [ ] B02 — [PR #17](https://github.com/ZZZZihan/BidOps/pull/17), Draft: B02-01–05 engineering PASS; B02-06 real historical backup BLOCKED. Implementation `72c9525784ae18df743b49fec5049fc7a224615f`: 685 backend / 14 browser tests. [Evidence](reports/b02-notice-versions-20261023.md).
- [x] B03 — [PR #18](https://github.com/ZZZZihan/BidOps/pull/18), Draft stacked on B02: B03-01–08 engineering PASS; 791 backend / 20 browser on `88d1f30d592678daa586c67818100dafff83f8d0`. [Evidence](reports/b03-company-access-20261023.md). Main merge remains blocked on B02-06; real company/IdP/HTTPS acceptance BLOCKED.
- [x] B04 — [Draft PR #19](https://github.com/ZZZZihan/BidOps/pull/19), stacked on B03 #18: B04-01–08 engineering PASS; 1101 backend / 39 browser tests on `3f04b045f0d029dadb4b882a293f31776400cc30`. [Evidence](reports/b04-qualification-screening-20261023.md). Code delivered, not merged/deployed; prerequisite B02-06 and real business acceptance BLOCKED.
- [x] B05 — [Draft PR #20](https://github.com/ZZZZihan/BidOps/pull/20), stacked on B04 #19: B05-01–07 engineering PASS; 1316 backend / 75 browser tests on clean `509e8007218c306d0e0639d96317537676570a0b`, seven SIGKILL/restart/read cases with 21 separate processes. [Evidence](reports/b05-durable-runs-20261023.md). No main merge/deploy; B02-06 and real business acceptance BLOCKED; review fixtures are not actual human approval.
- [x] B06 — [Draft PR #21](https://github.com/ZZZZihan/BidOps/pull/21), stacked on B05 #20: B06-01–07 engineering PASS; clean code `487649aec8260ce494d7a4cf1f41bae2c4ec1f26`, 1429 backend / 105 browser tests, four SIGKILL windows with 12 processes. Chrome updater teardown required scoped cleanup, recorded in [evidence](reports/b06-human-review-20261023.md). Chain B02-06 and real business acceptance remain BLOCKED.
- [ ] B07 — Authorized incremental updates and attachment ingestion (B07-01 through B07-07).
- [ ] B08 — Held-out evaluation and real human time comparison (B08-01 through B08-07).
- [ ] B09 — Declared capacity, alerts and complete recovery drills (B09-01 through B09-07).
- [ ] B10 — Real IdP/HTTPS/user business acceptance and release verdict (B10-01 through B10-06).

Each implementation PR must also satisfy G-01 through G-08 and attach per-item evidence.
Existing P1/E2/E3 checkboxes remain open. Synthetic tests, code merge and unavailable external inputs
must not be represented as independent business acceptance.


B06 CI修复补充：原Head backend/frontend实际FAIL已保留；修复代码`a0be11276de97b1dbf55fc87b96a9767e96c626a`完整1430后端PASS、Ruff197/冻结PASS，两项真实浏览器复验PASS；新CI frontend/container PASS，backend待结果。详见B06报告CI复验节，业务与前置B02-06仍BLOCKED。

B08已交付 [Draft PR #23](https://github.com/ZZZZihan/BidOps/pull/23)，基于B07 #22：授权完整结果收集、原始投影/用量/代码闭包核验、冻结独立协议及累计费用核对。干净代码`b42f05269e13be278d220a1307d79ee4b17aad6b`完整1851后端PASS，121项新增；真实CLI工程校准PASS，业务BLOCKED、agent_score=null。B08-03/07工程PASS，其他五项真实评测BLOCKED。见[B08逐项证据](reports/b08-independent-evaluation-20261023.md)。前置B02-06、真实资料/金标/人审计时/完整账本仍缺，B09/B10继续，无合并部署。
