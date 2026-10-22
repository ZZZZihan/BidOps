# BidOps PRD v0.1 — procurement qualification evidence workbench

**Version:** 0.1 (2026-10-22); **owner:** individual participant. No user interviews yet; customer pain points are hypotheses requiring validation.

## Target user and job-to-be-done

A small IT/industrial service provider wants to identify realistic public-sector procurement opportunities and understand deadlines and hard eligibility requirements without reading every announcement manually. The system must **cite primary text** and surface uncertainty, not act as a lawyer or procurement agent.

## Key workflow (MVP in P3)

1. User records organization *test profile* (industry, capabilities, size, certifications), marked synthetic until independently verified.
2. User imports real public notice URL or selects an indexed notice from sources with confirmed access permission.
3. System obtains immutable source snapshot (or user provides a lawfully held document) with `retrieved_at`, hash and URL.
4. It extracts purchaser, project identifier, budget with unit/currency, package, announcement date, application/upload deadline, procurement category, SME policy, key requirements, status, and URLs to supporting text.
5. User sees qualification matrix: **pass / fail / unknown / review required** with evidence and no hidden assumptions.
6. Reviewer signs off eligibility notes, creates an internal checklist; never submits a bid automatically.

## Must-haves for P0: infra + factual ingest; NO AGENT

- backend health / sources / notices API shape, versioned migrations (SQLite local).
- CLI `source inspect`, `import-urls`, `parse`, `export-manifest`; dry-run and offline fixture.
- Simple frontend skeleton showing health, imported entries and source detail placeholder (no real agent chat).
- audit logs, deterministic duplicate handling (source URL and content SHA256), error codes.
- source registry and allowlist; offline mode works without credentials or LLM.

## After P1 data gate: harness boundary

- Controlled `search_notices`, `read_source`, `extract_requirements`, `compare_profile`, `build_checklist` tools.
- Read-only default; human approval on irreversible writes; citation trace for all claims.
- Tool timeouts, retry budget, idempotency, budget/logs, reproducible agent evals.

## Explicitly out of scope

Auto bid submission, authenticated/CA-gated portals, bypassing CAPTCHA, agent self-directed web crawling, automatic legal or financial advice, fabricated customer metrics, unsupervised eligibility declarations, paid third-party APIs, live production business accounts.

## Internal product success hypothesis (not contest score)

For a small manually reviewed gold set, extract key fields precisely with evidence, correctly abstain when missing; reduce time taken to assemble an eligible/review/unknown checklist versus manual-only baseline, while showing errors. This is an experiment to conduct, **not an already demonstrated result**.
