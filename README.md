# BidOps | Evidence-first Tender Intelligence

> Status: **User-authorized DeepSeek Agent Demo**, built on six real P0 snapshots and 15 procurement packages. The new request explicitly expands the earlier P0-only scope; see [GOAL.md](GOAL.md). The first held-out evaluation scored **98.67/100 (71/72 cases)**. This does not pass the independent P1 data gate or start competition submission. Recurring collection permission remains unconfirmed. [Demo report](reports/demo-run-20261022.md) · [P0 collection](reports/p0-collection-20261022.md).

An AI-native procurement notice intelligence workbench for small and medium-sized businesses. Start with authentic Chinese government procurement notices; build a lawful, auditable data pipeline; validate data quality; only then build the Agent harness.

## Run the Agent Demo

Python 3.12+, uv and Node 22.12+ are required. Run these commands from the repository root:

```bash
uv sync --frozen
npm ci --prefix frontend
npm run build --prefix frontend
uv run bidops migrate
# On a new checkout, place the separately supplied lawful capture bundle here first:
uv run bidops import-bundle --input data/artifacts/p0-capture-20261022
uv run bidops parse
```

Set `DEEPSEEK_API_KEY` in your environment, or in ignored `private/deepseek.env` (mode 0600).
The current workspace already has the user-provided credential configured; it is never
included in GitHub, browser JavaScript or reports. A new checkout needs its own local setup.
An optional terminal prompt keeps the key out of shell history:

```bash
mkdir -p private
chmod 700 private
read -rsp 'DeepSeek API Key: ' bidops_deepseek_key
printf '\n'
(umask 077; printf 'DEEPSEEK_API_KEY=%s\n' "$bidops_deepseek_key" > private/deepseek.env)
unset bidops_deepseek_key
uv run bidops-demo
```

Open **http://127.0.0.1:8765**. One local process serves the built UI and API. The assistant
searches the imported corpus, compares amounts/deadlines/packages, preserves multi-turn
references and displays official links, original evidence and tool traces. The archive tab
retains the original notice list. Without a key or real imported data, the UI explicitly
disables questions. Never enter a key into the chat box.

Try “哪些项目预算不超过120万元？” or “TGPC-2026-A-0324 有几个采购包？”
The framework is **Pydantic AI + native DeepSeekProvider**, with `deepseek-flash` and typed
output/tool validation; evaluation uses **Pydantic Evals**. See the [framework decision](docs/ADR-0002-AGENT-DEMO.md)
and [frozen evaluation protocol](docs/DEMO-EVALUATION.md). No handwritten agent loop or
paid hosting service is used. DeepSeek API calls consume the user's provider credits.

`BIDOPS_DEMO_ENABLED=false` disables model calls. `BIDOPS_DEMO_MAX_COST_CNY` defaults to 10;
the local ledger conservatively reserves cost for active/failed requests. This is an
estimate, not a provider billing cap. Network collection remains disabled independently.
Only necessary public notice fields/evidence are sent to DeepSeek. No telemetry is enabled.
Conversations live in memory (up to eight turns; two retained in model context); restarting
or switching workspaces clears the browser conversation. This is a localhost demo, not an
authenticated production service.

## Competition

- Event: [“单人成军·智创高新”首届 OPC 创新应用挑战赛](https://mp.weixin.qq.com/s/mxhQ2aq_9IYueMWHKMMTmw)
- Track: **AiCoding 硬核组 → AI 智能体开发**
- Published collection period: 2026-10-19 to **2026-11-19** (China Standard Time; no precise cutoff hour confirmed).
- Selection: 2026-11-20 to 2026-11-26; roadshow: 2026-11-27.
- This repository **does not constitute a competition entry**. Registration/form submission must be confirmed via official channel.

Read **[contest rules](docs/competition/OPC-2026-RULES.md)** and [organizer questions](docs/competition/OPEN-QUESTIONS.md) before starting development.

## Principle: data → experiments → harness → product

The original stage gates below remain unchanged. The separately authorized Demo is a
bounded prototype exception, not a declaration that these data/competition gates passed.

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

This is a local prototype, not a completed competition product. Raw and full parsed data stay private; seed references, schema, code and sanitized verification metadata are public.


## Run locally

The original P0 data/archive features require Python 3.12+, uv, Node.js 22.12+ and npm,
and can run without a paid service or API key. The separate Agent feature requires DeepSeek.
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

The 2026-10-22 user-requested access check separately read exactly the six seed notice
bodies with anonymous HTTPS GET, the declared project user agent, no redirects and
at least five seconds between requests. It did not enable this recurring collection
path or authorize attachment downloads. The private capture bundle records
`access_method=http_fetch`; it is not mislabeled as a human-provided file.

## Replay the private six-notice capture

After placing the separately supplied capture bundle at `data/artifacts/p0-capture-20261022`:

```bash
uv run bidops import-bundle --input data/artifacts/p0-capture-20261022
uv run python scripts/verify_collection.py --bundle data/artifacts/p0-capture-20261022 --output data/artifacts/replay-check.json
```

This is offline. The replay checks raw SHA256, duplicate imports, stable exports, evidence
offsets and API records. It does not measure field accuracy against independent gold labels.

## Verification

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python scripts/p0_pilot.py
npm ci --prefix frontend
npm run build --prefix frontend
npx --prefix frontend playwright install chromium --only-shell
npm run test:e2e --prefix frontend
```

The pilot reports blocked URL references truthfully; its process exit code only indicates
that the diagnostic completed, **not** that source collection passed. Automated tests use
isolated syntax fragments/protocol mocks, not fabricated demo announcements. Read
[implementation limits](docs/P0-IMPLEMENTATION.md), [test scope](tests/README.md), and
[run report](reports/p0-run-20261022.md) before deciding whether P1 can start.

Live evaluation is opt-in and never runs in CI:

```bash
uv run python scripts/demo_eval.py --split dev --run-id my-demo-dev --execute
# Uses two real DeepSeek conversation turns and checks the built browser UI:
bash scripts/check_demo_browser.sh --execute
```

Evaluation cases, expected values and scoring weights are public; full live responses stay
under ignored `data/artifacts/`. Each run records actual model ID, prompt/data hashes,
latency, tokens, conservative cost and every failed case. Replaying a previously evaluated
set is a regression check, not a new held-out evaluation.
