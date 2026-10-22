# Phase-gated architecture (ADR-0001)

The historical phase plan below remains the data/competition gate. Later explicit user
requests authorized the [Demo](ADR-0002-AGENT-DEMO.md) and then the
[enterprise internal-app baseline](ADR-0003-ENTERPRISE.md). See [GOAL.md](../GOAL.md) for
the current engineering goal and the separate pending production/data acceptance gates.

```text
P0 (no LLM):
  curated source registry + consent/access checks
       └──> allowlisted public-URL fetch OR manual import
            └──> immutable local raw snapshots + SHA256 manifest
                 └──> deterministic HTML/text parse
                      └──> SQLite normalized notices, field provenance
                           ├──> FastAPI read-only endpoints
                           ├──> React/Vite lightweight table shell
                           └──> parser tests and metrics artifacts

P1:
  human gold review + temporal status reconciliation + quality gate

P2 (LATER, gated):
  bounded Agent orchestration/harness (search/read/compare/report)
       ├──> evidence resolver / abstention
       ├──> tool budget / timeouts / retries
       ├──> audit trace and test replay
       └──> human confirmation before external write
```

## Stack decision

- Python **3.12+** with `uv` and `pytest`/`ruff` (accessible, fast setup).
- FastAPI + Pydantic v2, SQLAlchemy 2 + SQLite (local MVP; optional Postgres migration later).
- `httpx` with explicit timeouts; `BeautifulSoup`/`lxml` for HTML; document parsers only after access clearance.
- React + TypeScript + Vite frontend shell (no full application at P0).
- GitHub Actions CI: lint, tests, offline parser fixtures, build.
- No LangGraph, RAG vector DB, Redis, LLM call, workflow backend or extra deployment services in P0. Choose harness only after data semantics stabilize.

## State semantics

Records distinguish **source observation time** from **business effective date**. Do not assume an old announcement is open today. New corrigenda/termination/awards may supersede original; represent uncertainty/status provenance explicitly.

Each normalized field has `value`, `source_snapshot_id`, `evidence_locator` (text offset or structurally located segment), and `confidence_type` (`rule`, `manual`, `unknown`). Do not use model-generated confidence as empirical probability.

## Public repository safety

Raw files, scraped HTML, downloaded attachments, provider credentials, application identity and company profiles are ignored by git. Seed notices store only public original links and public project IDs. Document source/terms checking in PRs. No automatic external writes.
