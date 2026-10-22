# Instructions to Codex — phase P0 only

You are the **implementation owner for BidOps P0**, not the designer of a multi-agent system. Work inside `ZZZZihan/BidOps` or the provided local checkout and honor `README.md`, `docs/competition/OPC-2026-RULES.md`, `docs/DATA-SOURCES.md`, `docs/DATA-CONTRACT.md`, `docs/EXPERIMENT-PLAN.md`, and `docs/ARCHITECTURE.md`.

## Preflight — create/publish repository only if not already present

- Intended GitHub repository `ZZZZihan/BidOps`, **public**, branch `main`.
- If the remote does not yet exist, check `gh auth status`; if necessary ask user to complete interactive `gh auth login`. No token in code/chat/logs.
- Preserve files and existing Git history. Do not overwrite an existing repo or force push.
- **If starting from the provided ZIP, it contains project files but no `.git` directory**. Unzip it, enter `BidOps/`, run `git init -b main && git add . && git commit -m "chore: bootstrap BidOps"` before publishing (configure your Git identity if Git asks).
- If remote absent, run `gh repo create ZZZZihan/BidOps --public --source=. --remote=origin --push`, or connect and push without destructively rewriting existing content.
- Create GitHub Issues according to `TASKS.md` if authenticated; milestone optional. If auth missing, report blocking issue and continue local implementation.

## P0 deliverables

1. **Source legality/access audit**: Check robots.txt, usage rules, HTTP status and normal page accessibility for `data/seeds/seed_urls.csv`. Record decisions; avoid broad scraping, CAPTCHA bypass, 403 bypass or gated CA downloads. Offline/manual import path required.
2. **Data collection code** (NOT data mining): Typed CLI, finite allowlisted URL inputs only, conservative serial rate limit, timeout, retry with bounded exponential backoff, user-agent, no undocumented endpoint. Default network disabled. Store raw snapshots only locally and SHA256 manifests with UTC fetch instant. On denied/unknown access, refuse collection and log reason.
3. **Data parsing/storage**: deterministic parser for accessible public notice HTML / lawful human-provided text; minimum fields project code, notice type, title, buyer, raw/normalized budget, currency, publication/bid cutoffs, SME condition snippets, raw evidence offsets, source URL. Mark unknown and missing attachment explicitly. SQLite migration, dedup and notice versioning; retain amendments rather than clobber originals.
4. **App framework only**: `uv` Python 3.12+, FastAPI API with `/health` `/sources` `/notices` `/notices/{id}`, React/TS/Vite skeleton with a data table and detail evidence link, typed API client; simple local environment. No production auth until requirements confirmed, but safe localhost defaults.
5. **Tests and CI**: offline stable fixtures and unit/integration tests for parsing, budget unit conversion (`万元`), CST timestamps, duplicate entries, amend/cancel warnings, network errors, 403 and empty fields; `pytest` + `ruff`; Vite TS check/build and GitHub Actions on PRs.
6. **Operational docs**: exact setup commands, a reproducible pilot run script and `reports/p0-run-YYYYMMDD.md` with PASS/FAIL/PENDING and actual invocation logs. Do not claim internet fetch success if blocked.

## Constraints and sequencing

- **Do not build LLM pipelines, RAG, vector DB, LangGraph, crew, tool-calling harness, autonomous search or agent loops in P0.** These are blocked until P1 labels and metrics are reviewed.
- Do not purchase data or spend on cloud services; target $0 cash for P0.
- No invented tender notices, timestamps, external/customer claims, evaluation metrics or AI-written percentage.
- Do not commit real raw HTML/PDF/DOCX, credentials, personal data or private company documents. `.gitignore` must protect those paths.
- Use real seed URLs that already exist; URLs are metadata, not claims that the page was successfully downloaded.
- Distinguish procurement notice deadline vs competition submission deadline.

## P0 exit report — mandatory

Create a short report with: git remote/branch/commit, environment, source access audit table, actual data counts (allow 0), fixture counts, tested CLI commands with exit status, tests results, HTTP/backend + frontend screenshots if available, unresolved blocks and **go/no-go recommendation for P1**. Do not mark P0 done if acceptance conditions in `docs/EXPERIMENT-PLAN.md` did not pass. Open issues for blockers.

Stop after P0 and return to human review. **Do not proceed to P1 data labeling, P2 harness, P3 presentation or competition registration without a new explicit instruction.**
