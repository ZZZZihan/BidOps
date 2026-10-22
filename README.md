# BidOps | Evidence-first Tender Intelligence

> Status: **Project charter frozen (2026-10-22)**. Data ingestion, product, Agent and submission are **NOT implemented**.

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

This is the competition-oriented *governance and planning repository*, not a finished implementation. Files in `data/` are local only except seed URL references.
