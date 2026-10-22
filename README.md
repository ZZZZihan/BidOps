# BidOps | Evidence-first Tender Intelligence

> Status: **P0 engineering implementation available; source/data acceptance BLOCKED (2026-10-22)**. Zero real notice snapshots. P1/P2/P3 have not started. See [actual run report](reports/p0-run-20261022.md).

An AI-native procurement notice intelligence workbench for small and medium-sized businesses. Start with authentic Chinese government procurement notices; build a lawful, auditable data pipeline; validate data quality; only then build the Agent harness.

## Competition

- Event: [“单人成军·智创高新”首届 OPC 创新应用挑战赛](https://mp.weixin.qq.com/s/mxhQ2aq_9IYueMWHKMMTmw)
- Track: **AiCoding 硬核组 → AI 智能体开发**
- Published collection period: 2026-10-19 to **2026-11-19** (China Standard Time; no precise cutoff hour confirmed).
- Selection: 2026-11-20 to 2026-11-26; roadshow: 2026-11-27.
- This repository **does not constitute a competition entry**. Registration/form submission must be confirmed via official channel.

Read **[contest rules](docs/competition/OPC-2026-RULES.md)** and [organizer questions](docs/competition/OPEN-QUESTIONS.md) before starting development.

## Principle: data → experiments → harness → product

1. **P0 / foundation:** verify permitted sources, curate seeds, implement deterministic fetch/parse/storage with provenance, set up API/UI scaffolds, tests and CI. **Do not build an Agent in P0.**
2. **P1 / data:** collect a representative legal sample, manually review the gold set, calculate errors, repair parsers and freeze a reproducible dataset manifest.
3. **P2 / harness:** tool adapters, task state, retrieval, evidence links, abstention, human confirmation, audit logs and evaluations. This step only starts after the data gate passes.
4. **P3 / delivery:** working MVP, demo, project deck, disclosure of AI-assisted development and competition submission artifacts.

## V0 scope and product boundary

MVP: notice discovery → source-grounded field extraction → company profile vs eligibility checklist → human review → actionable project card. **No automatic bid submission, login bypass, legal eligibility guarantees or autonomous external writes**.

Sources are public notices, but **publicly viewable is not a blanket license for bulk scraping or redistribution**. Raw HTML/DOCX/PDF, session tokens and participant PII are excluded from the public repo by default. Legal access, site terms and robots rules are verified before collection. Logged-in/CA-required files are out of scope for unattended harvesting.

## Developer handoff

- **[CODEX P0 instruction](docs/CODEX-HANDOFF-P0.md)** (paste verbatim into Codex)
- [PRD](docs/PRD-v0.1.md) | [architecture](docs/ARCHITECTURE.md) | [source catalog](docs/DATA-SOURCES.md) | [data contract](docs/DATA-CONTRACT.md) | [experiment plan](docs/EXPERIMENT-PLAN.md)
- [Kanban](TASKS.md) | [Evidence register](docs/EVIDENCE-REGISTER.md)

This is a local P0 prototype, not a completed competition product. Raw and processed data stay local; only seed references and the generated data schema are public.


## Run locally

Prerequisites: Python 3.12+, uv, Node.js 22.12+ and npm. No paid service or API key.
Run all backend commands from the repository root. Settings read exported environment
variables; `.env.example` is documentation and is not automatically loaded.

```bash
uv sync --frozen
uv run bidops migrate
uv run bidops source inspect
uv run bidops import-urls --csv data/seeds/seed_urls.csv
uv run bidops stats
uv run uvicorn bidops.api:app --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5173. The initial notice list is **empty**. Registering the six
seed URLs does not download or parse them. API: `/health`, `/sources`, `/notices`,
`/notices/{id}`; OpenAPI is at http://127.0.0.1:8000/docs.

## Import a lawfully held file

Use an actual original URL from a registered official host and your own lawful HTML/text
file. The following paths are operator inputs, not supplied notice data:

```bash
uv run bidops import-file --url "$NOTICE_URL" --file "$NOTICE_FILE" --content-type text/html --attest-lawful
uv run bidops parse
uv run bidops export-manifest --output data/artifacts/local-backup --include-raw
uv run bidops import-bundle --input data/artifacts/local-backup
```

Manual import records `access_method=manual_import`, never an HTTP download. Missing
fields stay null. Explicit `--encoding gb18030` is supported. Export bundles and raw
files remain local and must not be committed. `parse` verifies raw integrity first.

## Optional network path (currently blocked)

```bash
uv run bidops fetch --url "$NOTICE_URL"
```

This is a dry run. `BIDOPS_NETWORK_ENABLED=true` **alone is insufficient**. Collection
also requires `--execute`, exact URL membership and reviewed robots/terms permission in
`config/sources.json`. All current sources are `unknown`. Do not flip statuses merely
to make a test pass. `scripts/audit_sources.py --execute-metadata-audit` inspects only
public access-rule metadata; it never grants permission or harvests notices.

## Verification

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python scripts/p0_pilot.py
npm ci --prefix frontend
npm run build --prefix frontend
```

The pilot reports blocked URL references truthfully; its process exit code only indicates
that the diagnostic completed, **not** that source collection passed. Automated tests use
isolated syntax fragments/protocol mocks, not fabricated demo announcements. Read
[implementation limits](docs/P0-IMPLEMENTATION.md), [test scope](tests/README.md), and
[run report](reports/p0-run-20261022.md) before deciding whether P1 can start.
