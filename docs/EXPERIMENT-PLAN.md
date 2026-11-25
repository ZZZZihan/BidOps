# Experiments and gates (our engineering criteria, NOT organizer scoring)

## Gate P0 — source and scaffold readiness

- A) At least 6 real, traceable source URLs; link, project ID and notice publication date stored in seed CSV. **A URL is not a successfully fetched record.**
- B) Verify site access/robots/terms; report `allowed`, `denied`, `unknown`. Only run HTTP fetch with compliance confirmed; no circumvention.
- C) Offline deterministic importer + parser against representative lawful fixtures. Verify manifest SHA256 on repeated imports and stable deduplication.
- D) Fresh checkout: `uv sync`, `uv run pytest`, `uv run ruff check .`, migrations, backend health check; frontend build/test where scaffolded. Report actual outputs and environment.
- E) Failure tests: timeout, 403, 404, duplicate URL, duplicate bytes, broken charset, unknown amount, empty due date, amendment handling as `review_required`.
- **If network access fails, make offline test PASS but source collection FAIL/PENDING, not fake green.**

## Gate P1 — data quality before harness (later human-led review)

- At least **30 lawfully obtained real notices**, spanning 3 notice categories and >=2 layout variants, at least 10 archived/historical and 10 future-at-capture where available. These are internal sampling targets, not competition rules.
- Randomly select 20+ independent gold records; manual review original text evidence for project ID, title, buyer, budget unit, deadline, notice type and eligibility excerpts.
- For each field compute exact match/precision/recall or numeric normalized equality as appropriate, coverage, abstention, and error taxonomy; publish denominators not only rates.
- Proposed minimum: project code & link provenance 100%; critical deadline and budget unit >=95% precision among nonabstained, >=85% coverage; all extracted eligibility conclusions evidence-cited with human review.
- Time reference/timezone is explicit. Corrected/cancelled notices must never silently appear as currently open.
- If targets unmet, iterate on parser and corpus instead of starting harness. A quality threshold is **an internal decision gate** only.

## P2 / harness tests (not to implement now)

- Fixed evaluation prompts with known ground truth; unsupported claims and wrong evidence URLs counted as errors.
- Hard timeout / bounded budget / retries / no credential leakage / approval gate / idempotency in tests.
- Report actual recall and coverage, not vague claims like 'agent accuracy 98%'.

## Competition's product completion is DIFFERENT

The republished notice demands a runnable MVP, project PPT, official form and technical document. It does **not** demand model training, a benchmark score, GPU run, public GitHub or a fixed number of collected notices. Our numeric quality criteria are self-imposed to defend the product in demonstrations.

## 企业业务评测实现补充（2026-10-23）

B01 新增独立 schema、隔离／抽样／人工对照校验及可执行评分器。
本文件原 P1 门槛继续有效，新增门槛和分母定义见
[企业评测协议 v1](ENTERPRISE-EVALUATION.md) 与 `evals/enterprise-v1/protocol.json`。
旧六公告、模型预标注和工程夹具均不能作为独立验收样本；本轮离线校准不产生 Agent 成绩。
