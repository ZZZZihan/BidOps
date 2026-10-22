# B02 公告版本、证据与分页实际验收

实现 PR：[#17](https://github.com/ZZZZihan/BidOps/pull/17)，依赖 [B01 #16](https://github.com/ZZZZihan/BidOps/pull/16)，均未合并。
执行日期：2026-10-23（Asia/Shanghai）；日志使用 UTC。

**代码合并：BLOCKED。** B02-01～05 工程验证通过；B02-06 指定的真实旧六公告备份未提供，
不能用六条合成记录替代，因此 PR 保持 Draft。
**业务验收：BLOCKED。** 没有真实新公告效果、持续来源许可、独立金标或企业用户验收。

## 被测版本与执行者

- Base：`069e4b495828caca4c64447062c030a0f313aada`。
- 完整实现提交：`72c9525784ae18df743b49fec5049fc7a224615f`。
- 被测 tree：`860e17bd07402b968da9d34d4d6305f0854386b9`；运行前后工作树干净。
- 环境：macOS、Python 3.12.12、Pydantic AI 2.59.0、Authlib 1.9.1、Node 26.11.0、uv 0.10.0。
  CI 使用仓库已有 Python 3.12 / Node 22 / uv 0.12.23 配置，CI 结果单独从当前 HEAD 检查。
- 主 Agent：集成、SQL 查询、旧问答与 API、附件备份、完整验证；本次用户允许 max。
- `b01_contract_audit`：公告领域、时点状态、引用、附件及 90 项领域自检。
- `b01_eval_audit`：独立领域/集成复核、112 项定向检查及原失败复现。
- `b03_identity_adapter`：独立问答/查询/界面复核；末页绕过问题修复后实际重跑 64 项定向
  后端与 14 项浏览器检查。三个子 Agent 均显式请求 GPT-6.1 Sol / ultra；无法独立证明
  provider 的实际运行档位。技术复核不是用户人工验收。

输入均为测试代码明确编写的 `UNIT-*` 工程片段、507 条分页记录和签名 OIDC 协议夹具。
领域时钟固定为测试中的带时区时刻，API 当前时间另有可控时钟边界用例。
不包含真实企业材料或新独立采购样本；前置企业权限使用测试 analyst/viewer/admin 身份。

## 实际执行

完整命令、开始/结束时间、退出码见 [verification.json](evidence/b02-20261023/verification.json)。
本次提交的原始结果：

| 检查 | 实际结果 | 日志 |
| --- | --- | --- |
| `.venv/bin/pytest -q` | 685 passed，退出 0 | [后端](evidence/b02-20261023/backend.txt) |
| `.venv/bin/ruff check .` | All checks passed | [lint](evidence/b02-20261023/lint.txt) |
| `.venv/bin/ruff format --check .` | 102 files already formatted | [format](evidence/b02-20261023/format.txt) |
| B01 固定 pin 离线入口 | PASS；Agent score null；业务 BLOCKED | [机器结果](evidence/b02-20261023/frozen-scorer.json) |
| `npm run build`（frontend） | TypeScript 与 Vite 构建成功 | [构建](evidence/b02-20261023/frontend-build.txt) |
| `BIDOPS_BROWSER_CHANNEL=chrome npm run test:e2e`（frontend） | 14 passed | [浏览器](evidence/b02-20261023/frontend-browser.txt) |

定向重放命令（完整套件已包含这些用例）：

```sh
.venv/bin/pytest -q tests/test_notice_versions.py tests/test_notice_evidence.py \
  tests/test_notice_status.py tests/test_notice_attachments.py \
  tests/test_catalog_pagination.py tests/test_notice_integration.py \
  tests/test_agent_demo.py tests/test_enterprise_backup.py tests/test_storage_api_cli.py
```

B01 冻结 SHA `22a2adf96f07b9a3e9c1493dae2cebbe2499b6e05db21f2b52855686c2afffea`，
工程输入 SHA `eda43a451ae978251de4be003f1c062d1a4d3e93db9249c9c5458966628f44fb`，
画像 SHA `ff951fa725c2ec5b1b981c1cc46c85493a4354903b4d0a69d92d54d6c0312202`。
18 个冻结文件、业务 schema、锁文件及评分器均未改动。

## 逐项验收

以下各行共用上述代码 SHA、输入类别、执行者与时间范围；测试函数保留具体版本、时钟、
前置权限、预期断言和失败输入，链接与日志可复查。

| AC-ID | 输入、操作与预期 | 实际观察与证据 | 状态 |
| --- | --- | --- | --- |
| B02-01 C | 原公告、两次更正、终止、多个包和重复导入；稳定标识、不可覆盖、歧义待审核 | [versions](../tests/test_notice_versions.py) 与 [integration](../tests/test_notice_integration.py)：重复保持原快照，重新解析新增不可变解释，旧 payload/引用保持；不唯一关联为 pending_review | PASS |
| B02-02 C | 公开 HTML/文本及已支持附件；篡改字节、哈希、偏移、定位版本均应拒绝 | [evidence](../tests/test_notice_evidence.py) 与附件/API用例通过；原始字节、规范化文本、版本、精确范围和逐字引用同时核验，旧解释仍可解析 | PASS |
| B02-03 C | as_of、晚到更正、未来解释、终止、缺关联及陈旧来源；未来截止不直接开放 | [status](../tests/test_notice_status.py)：旧更正48小时而原页刚刷新仍 unknown/stale_observation/172800秒；已知终止优先；当前Store默认来源不完整，仍 unknown | PASS |
| B02-04 C | 可读、未下载、失败、加密、扫描未识别、未支持；解析成功后删除/损坏blob | [attachments](../tests/test_notice_attachments.py)：状态逐份保留；历史parsed不覆写，动态unavailable/corrupt进入缺失清单，项目变unknown；PDF/OCR诊断仅外部metadata，不冒充解析 | PASS |
| B02-05 C | 507条记录，目标在501以后；全库筛选计数、8种排序方向组合、末页多版本 | [pagination](../tests/test_catalog_pagination.py)：507条全页无重漏、目标可查、计数169；Decimal极近值排序正确；101个同项目版本末页强行results被拒，review_required保留总数/截断；14浏览器包含归档分页和末页展示 | PASS |
| B02-06 C | 指定真实旧六公告备份迁移、历史会话证据及回退旧服务 | 工程替身6行迁移后仍6版本，历史payload/会话相等，重复回填created=0/existing=6；恢复旧备份保持6公告/1历史会话且无004。附件闭包恢复及篡改拒绝通过。但真实旧库/raw/历史会话未收到，未运行指定真实输入或旧服务现场回退 | BLOCKED |

## 共同门槛

| ID | 实际依据 | 状态 |
| --- | --- | --- |
| G-01 | 新增行为映射B02-01～06；C项B02-06仍BLOCKED，不宣称全C通过 | BLOCKED |
| G-02 | 当前实现提交685后端、lint/format、构建、14浏览器及冻结入口通过；原断言保留 | PASS |
| G-03 | 新增004可重复；合成旧记录/会话、附件闭包及恢复通过；指定历史真实库输入缺失 | BLOCKED |
| G-04 | 新API两种别名均受catalog:read与既有CSRF保护；引用失败受控，不回显路径/正文；公开输入仅工程夹具 | PASS |
| G-05 | 无新增第三方组件；复用SQLAlchemy/SQLite、原规范化与FastAPI/Pydantic，依赖锁未变化 | PASS |
| G-06 | 模型/采购来源请求0，新增¥0；无自动采集；历史预算不重置 | PASS |
| G-07 | 日志绑定72c9525完整SHA、干净树、冻结输入及真实BLOCKED；后续文档提交的CI单列 | PASS |
| G-08 | 已验证工程旧备份内容可恢复且未破坏版本；真实旧服务及指定真实库回退因同一输入缺口未运行 | BLOCKED |

## 已修复的实际失败与边界

独立复核曾发现：刷新原页替旧更正续期、附件blob丢失仍开放、读取101版本末页绕过复核、
界面隐藏读取截断。对应实现与回归已补齐；两名独立复核 Agent 重跑原失败输入确认关闭。

动态offset分页只承诺不变集合稳定；固定批次交给B04输入manifest。查询目前扫描全表，
507条不代表B09生产容量。仅支持文本/HTML，不包含PDF或OCR能力。重新解析不是来源刷新。
当前来源闭包默认不完整，不能将“未来截止”宣传成实时可投。

升级/回退步骤见 [NOTICE-VERSIONS](../docs/NOTICE-VERSIONS.md)。旧版本、raw、历史会话和
新附件全部保留；回退必须使用独立目录和完整备份，不执行破坏性降级。

真实旧六公告的数据库、raw和历史会话受控路径仍待用户提供。用户回复“好”没有提供路径，
因此该输入缺口仍在。累计¥10预算沿用，最新费用账本亦待提供；本轮实际模型标识null、
参数{}、请求/tokens/费用全部0，未执行真实模型或新来源请求。

下一步继续B03集成与B04，不等待本Draft合并。B02未合并、未部署；M1/M2/M3未通过。
