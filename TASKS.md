# Project board — dated 2026-10-22

## P0: engineering implementation delivered for review; source/data acceptance BLOCKED
- [ ] [P0-01](https://github.com/ZZZZihan/BidOps/issues/1) Obtain organizer answers on eligibility, registration, IP and submission route (user action needed).
- [ ] [P0-02](https://github.com/ZZZZihan/BidOps/issues/2) Audit actual public tender pages, site ToS/robots, user-agent/access limitations; no bypass.
- [x] [P0-03](https://github.com/ZZZZihan/BidOps/issues/3) Initialize `uv` Python 3.12 backend + FastAPI health endpoint, SQLite migration, React/Vite TypeScript skeleton and CI.
- [x] [P0-04](https://github.com/ZZZZihan/BidOps/issues/4) Design immutable raw-data snapshots, manifests, URL dedupe and source evidence chain.
- [x] [P0-05](https://github.com/ZZZZihan/BidOps/issues/5) Build manual URL importer and optional *allowlisted, rate-limited* public HTML fetcher with dry-run default.
- [ ] [P0-06](https://github.com/ZZZZihan/BidOps/issues/6) Deterministic parser for at least 6 seeded notices and explicit unsupported cases; never infer unknown fields.
- [x] [P0-07](https://github.com/ZZZZihan/BidOps/issues/7) Implement audit/event logs and error retry/timeout tests.
- [ ] [P0-08](https://github.com/ZZZZihan/BidOps/issues/8) Produce real run report with results, provenance and limitations (not aspirational PASS).

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
- #2 audit executed, but all three collection-permission decisions remain unknown.
- #6 parser prototype and isolated tests exist; zero of six real notice bodies verified.
- #8 actual report is being finalized; P0 is not an overall PASS.
- P1 backlog: #9; P2 backlog: #10; P3 backlog: #11. None has started.
