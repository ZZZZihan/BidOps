# BidOps 开发接续说明 — 2026-11-25

本次范围是按用户授权整合现有 PR #15–#32，形成可以从 GitHub 主分支继续开发的统一代码基础。公告问答、业务流程、浅蓝工作台、MyGo 桌面入口和提示词追踪一并保留。实际合并记录以各 PR 的 GitHub 状态为准，验证以当前提交的 Actions 为准。

此前报告中的“代码合并 BLOCKED”记录当时的交付决定及未取得的真实输入。本次用户明确要求先合并用于继续开发；这不表示那些输入已经取得，也不表示业务或生产验收通过。旧报告、失败记录、验收阈值和原始分支历史保留。

## 已有代码能力

- 公告档案、预算/采购包等字段查询、原文引用和多轮 AI 问答。
- 企业画像和证明材料的版本、对象权限、撤权及留存控制。
- 按关键词和预算建立固定候选批次，再对公告与企业证据逐条进行资格核对；缺证明、冲突及未知状态显式保留。
- DBOS 持久任务、进度、取消、中断恢复和费用预留。
- 逐项人工复核、补材料后续任务、独立审核事件和结构化审核包导出。
- 经许可的更新/导入及附件解析入口；现有来源登记并未因此获得持续采集许可。
- 浅蓝淡白浏览器工作台，以及加载同源本机服务的 MyGo 桌面窗口。
- 公告问答的独立提示词版本、运行审计和固定 91 项离线回归试点。

主要目录：`backend/bidops/`、`frontend/`、`desktop/`、`scripts/`、`tests/`。Pydantic AI 负责模型循环，DBOS 负责持久任务，Authlib 负责 OIDC；当前部署边界仍是单组织、单机与 SQLite。

## 换平台后的准备

```bash
git clone https://github.com/ZZZZihan/BidOps.git
cd BidOps
git switch main
uv sync --frozen --python 3.12
uv pip install --python .venv/bin/python --require-hashes \
  -r requirements/durable.lock -r requirements/attachments.lock
npm ci --prefix frontend
npm run build --prefix frontend
```

需要 Python 3.12、uv、Node.js 22.12+。桌面源码验证另需 `desktop/go.mod` 指定的 Go 工具链；原生窗口构建还需对应操作系统的构建环境。

主锁 `uv.lock`、两个带哈希的 overlay lock，以及 B01 冻结 pin 沿用现有设计。仅运行 `uv sync` 不会安装完整的 DBOS/附件 overlay；不要为方便安装而自动重写锁文件或冻结清单。

公开仓库不包含真实公告原文、企业材料、业务数据库、历史会话、费用账本和密钥。新 clone 只取得代码和公开验证材料。若在新平台继续已有真实任务，必须另行使用匹配的完整私有数据目录与版本，不能用新建空库替代原账本或历史。

有完整既有数据目录时，按 [本机完整业务流程](LOCAL-WORKFLOW-DEMO.md) 或 [本机公告问答](LOCAL-LIVE-DEMO.md) 启动；企业部署按 [运维说明](ENTERPRISE-OPERATIONS.md)。MyGo 入口见 [desktop/README.md](../desktop/README.md)，它需要已经启动的本机后端。

## 适用的离线验证

```bash
uv run --frozen --no-sync ruff check .
uv run --frozen --no-sync ruff format --check .
uv run --frozen --no-sync python scripts/llm_pilot_check.py \
  --full-suite --output /absolute/path/to/new-llm-pilot-report.json
uv run --frozen --no-sync python scripts/screening_eval.py \
  --expected-freeze-sha256 22a2adf96f07b9a3e9c1493dae2cebbe2499b6e05db21f2b52855686c2afffea \
  --expected-input-sha256 eda43a451ae978251de4be003f1c062d1a4d3e93db9249c9c5458966628f44fb \
  --output /absolute/path/to/new-b01-calibration.json
npm run test:e2e --prefix frontend
cd desktop
go mod verify
CGO_ENABLED=0 go test -count=1 ./...
CGO_ENABLED=0 go vet ./...
```

报告路径须替换为尚不存在的输出文件。浏览器测试需要安装 Playwright Chromium，或使用环境已提供的 Chrome 并设置 `BIDOPS_BROWSER_CHANNEL=chrome`。容器打包检查由现有 CI 执行；CI 还在临时环境执行迁移和 P0 诊断。离线替身、冻结评分器校准和工程恢复不能作为真实模型质量、人工节时或企业试用的结论。

## 尚未完成的业务与数据工作

1. **真实旧资产恢复（B02-06 / E01）**：原业务库、原三轮持久历史、实际引用的 raw/附件/材料闭包、匹配备份与旧运行版本仍缺完整验证；六公告原文包不能替代历史业务库。
2. **完整费用核对（E06）**：历史完整账本与供应商对账仍未确认。保留失败/未知预留和恢复后的预算复核屏障，不从旧估算推断当前余额。
3. **独立业务效果**：已保存的六公告和虚构企业资料用于演示/回归；代表性新项目、独立人工金标、审核人及耗时对照仍需准备。历史 98.67 分不是资格核对的新成绩。
4. **真实企业与目标环境**：真实企业材料、至少两名独立用户、企业 IdP/HTTPS、撤权传播、目标机器负载和完整恢复仍需验收。
5. **持续更新**：来源权限、最新更正/终止/完整附件和实际周期覆盖仍需核实，不能把历史快照显示为当前可投项目。

原逐项标准继续见 [企业 PR 计划](ENTERPRISE-PR-PLAN.md) 和 [历史验收矩阵](../reports/enterprise-current-acceptance-20261023.md)。后续变更应按其实际范围验证；发现新问题就记录具体证据，不将历史状态批量改为通过。

## 给下一开发平台的起始说明

> 请从 ZZZZihan/BidOps 的最新 main 开始，先读 README.md 与 docs/DEVELOPMENT-HANDOFF.md，再核查实际 Git 状态。代码已覆盖采购公告问答、企业材料、候选筛选、资格证据核对、持久任务、人工复核、审核包导出、Web UI、MyGo 桌面和提示词追踪。先确认我接下来要做的具体功能，沿用现有结构与锁文件。真实旧库恢复、完整费用对账、独立效果评测与真实企业验收仍未完成；不要用工程测试或新空库替代这些证据。涉及真实模型、持续采集、部署或发布时，再核对当次授权与实际数据条件。
