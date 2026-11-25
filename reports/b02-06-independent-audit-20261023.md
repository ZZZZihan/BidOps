# B02-06 / E06 独立资产核验 — 2026-10-23

## 结论

**真实历史迁移验收仍为 BLOCKED。**
本轮定位了旧代码、六公告指纹、原交付包名称及真实三轮会话的具体落点，
并提供不导入应用的独立预检入口。没有取得真实旧库或原文，没有执行真实迁移或回退。

B02-06、G-01/G-03/G-08 和 E06 的缺口保持不变。
本报告不修改 73 项矩阵，不新增 AC-ID，不把工具检查或合成测试改记为历史迁移通过。
真实企业验收、依赖链合并和生产发布也没有因此获得放行。

## 审阅范围与版本

- 固定基线：PR #26，0ff9413cff5e3cb13b85697e5a0c9181e4b86658。
- 读取范围：完整当前目录、可达提交、关键历史目录、运行文档、旧集成脚本、
  001–003 schema、公告版本迁移、恢复与费用记录代码。
- 当前目录树返回 857 项，truncated=false；可达提交列表为 79 项，第二页为空。
- 本次查到 main 为 cfcd7c8a331f26952ac6ad0493d92c1bd623d9d0。
- 新检查器的本地工程结果见下文，文件 SHA256 将其绑定到本 PR 代码；
  本报告中的旧运行结果仅用于定位资产，不冒充本轮真实资产复验。

[固定基线](https://github.com/ZZZZihan/BidOps/tree/0ff9413cff5e3cb13b85697e5a0c9181e4b86658) ·
[既有矩阵](https://github.com/ZZZZihan/BidOps/blob/0ff9413cff5e3cb13b85697e5a0c9181e4b86658/reports/enterprise-current-acceptance-20261023.md)

## 1. 能取得什么，尚缺什么

| 对象 | 本轮独立观察 | 尚不能声称 |
|---|---|---|
| 旧代码与 schema | 015ce3c、6a41a067、069e4b4 等固定版本可读 | 拿到的未知旧库一定运行过该 SHA |
| 六公告身份 | 6 个 URL/snapshot_id/raw SHA、大小和采集时间可读 | 原始 HTML 已到手或哈希已重算 |
| 原交付 ZIP | 报告明确名为 BidOps-P0-samples-20261022.zip | ZIP 当前可下载、内含旧 SQLite/会话 |
| 真实三轮历史 | 固定脚本与汇总可定位 restored 库 | 三轮私有 JSON/数据库原件已验证 |
| 迁移、回退代码 | 入口与隔离恢复机制可追踪 | 真实旧库兼容或旧服务回退已通过 |
| 费用记录 | agent_runs 与跨副本费用回写路径可定位 | 最新累计余额、无遗漏或 unknown 可释放 |

当前 Git 的 data 目录只有 README、schema 与 seed。
PR26 可达历史中，data 路径只有两次提交变更：
647d32d 添加这三类文件；015ce3c 仅修改 schema。
检查的 P0、首次企业实现、B02 前基线目录也没有 raw、SQLite 或 capture 原件。
忽略规则与报告均明确将原文、数据库、附件及私有运行产物留在 Git 之外。

这些结论只覆盖已检查的 Git 引用和目录；
不能据此断言删除分支、原执行环境、备份或历史交付附件中不存在副本。

## 2. 新定位：历史在 restored 库，不一定在默认源库

旧 6a41a067 的 scripts/enterprise_live.py 先备份源库，
再恢复到 data/artifacts/enterprise-live-20261022/restored/bidops.sqlite3，
在这个恢复副本执行三轮真实模型问答，并另存 responses.json。

record_new_costs 只把 agent_runs 回合并到源库，未复制 enterprise_turns。
因此应优先找：

- data/artifacts/enterprise-live-20261022/restored/bidops.sqlite3。
- 同级 raw/ 与外层 responses.json。
- 其次是运行前 backup/ 与默认 data/processed/bidops.sqlite3、data/raw/。

只拿到六公告的默认库或运行前备份，不能自动满足“读取历史会话证据”。
该历史使用 integration-probe 身份，旧汇总 oidc_live_test=false；
它是实际发生的公告/模型应用历史，不是企业真实 SSO 或 UAT。

[旧脚本](https://github.com/ZZZZihan/BidOps/blob/6a41a067b08f67b5d9c46bd68f5b3acc0101bf80/scripts/enterprise_live.py#L22-L54) ·
[历史保存与回合并](https://github.com/ZZZZihan/BidOps/blob/6a41a067b08f67b5d9c46bd68f5b3acc0101bf80/scripts/enterprise_live.py#L129-L145) ·
[旧真实集成汇总](../reports/enterprise-live-20261022.json)

## 3. 采集身份与旧版本绑定

原采集 manifest SHA256：
1f9578666a4297ee75b181c0b629d123516feb757bc4902f67ea89059e57cf54。
公开记录为 6 份 raw、156,552 bytes、15 包，原批附件请求为 0。
本 PR 的 expected JSON 是这些已公开指纹的整理，不含原文，不创建历史数据库。

原 ZIP 被描述为 raw、manifest、notices.jsonl、访问记录与说明；
没有依据认为它还交付了持久会话和完整账本。
后续附件只按取得的真实旧库引用闭包补齐，不为最初 0 附件制造数据。

001 保存公告基础表；002 增加 agent_runs；003 增加企业会话/轮次/审计。
历史三轮对应实现为 6a41a067。该提交到 main cfcd7c8 只有两份后续文档提交，
改动仅 TASKS.md 和 reports/enterprise-run-20261022.md，运行代码相同。
069e4b4 是后续 B01、B02 前基线；实际旧运行证据匹配它时才能用于回退。

[原采集报告](../reports/p0-collection-20261022.md) ·
[逐样本指纹](../reports/p0-collection-verification-20261022.json) ·
[旧实现至 main 比较](https://github.com/ZZZZihan/BidOps/compare/6a41a067b08f67b5d9c46bd68f5b3acc0101bf80...cfcd7c8a331f26952ac6ad0493d92c1bd623d9d0)

## 4. Release / Actions 查找边界

Releases API 返回空列表。
抽查以下 7 个关键 workflow 的 artifact 列表均为空：

~~~text
39769121834 39772022878 39772319997 39783054474
39795653783 39822827370 39876635741
~~~

当前 ci.yml 没有 upload-artifact 步骤。
没有检查全部 106 个 run 的产物，不将这次抽查写成“CI 从未保存任何真实数据”。
可重做端点为 GET /repos/ZZZZihan/BidOps/actions/runs/{run_id}/artifacts；
目录和历史均使用固定 SHA 的 GitHub GET 查询，未写回远端状态。

## 5. 本 PR 提供的具体工作

- scripts/legacy_preflight.py：标准库只读 capture、旧库结构与独立账本预检。
- data/seeds/p0-capture-20261022.expected.json：公开六公告预期指纹。
- [操作说明](../docs/LEGACY-ASSET-VERIFICATION.md)：只读查找、WAL 一致副本、
  配置隔离、两次工作副本迁移和第二目录旧代码恢复。
- 工具成功仅为 CHECKS_OK_NOT_ACCEPTANCE，B02/E06 输出 NOT_EVALUATED；
  实际缺件或检查失败有不同退出状态。

脚本不调用 Store，不自动迁移、parse、恢复、写账或执行模型。
typed 消息、原规范化定位器逐字引用、DBOS v4 完整关联和旧服务 HTTP smoke
不由此工具认证，真实输入取得后仍须单独执行并保留证据。
工程测试的临时 SQLite/原子片段只是测试工具的拒绝路径，不是六公告迁移验收。

### 本 PR 工具验证

Python 3.12.14、SQLite 3.53.1；使用 uv.lock 固定的 Ruff 0.17.1。
实际执行 14 项 unittest、Ruff check 与 format --check，均退出 0。
缺原 capture 与缺最新账本的 CLI 检查均退出 2 / BLOCKED，符合缺件拒绝预期；
它们没有把底层真实验收改成 PASS。另一独立审阅者的 7 项定向检查通过，
包括畸形元数据、未知私有表/状态不回显、账本解耦及严格 JSON。
这些定向检查与 unittest 有覆盖重叠，不合计为 21 项独立验收。

脚本 SHA256：475ebf102e6406b1dd1b67c3665f8d60b430cda0bed8ffd25f5291072a6234f7。
预期元数据 SHA256：f722bf3269ce2c6b98a56a2f04a4ad87a2b7c1b07ff15f2f348c2ef75064d5ad。
完整命令、退出码、实际运行时戳、测试文件哈希和输出见
[verification.json](evidence/b02-06-preflight-20261023/verification.json)，
定向复查见 [independent-review.json](evidence/b02-06-preflight-20261023/independent-review.json)。
本地没有重跑完整应用/浏览器套件；本 PR 的远端 CI 状态另查。

## 6. 费用恢复依赖与下一步

E06 可以独立于 E01 执行 --ledger-only，不要求 raw、六公告或会话。
但它只描述给定副本：完成估算、非完成预留和占用，不证明最新完整截止。
供应商记录、开发/评测/失败/重试/unknown/orphan/跨副本费用仍需对账；
remaining_budget_cny 保持 null。恢复 token 只是屏障确认，不能找回漏记费用。

下一步优先由资产保管方找回原 ZIP 和 enterprise-live 的 restored 目录，
提供停写时点一致快照、raw/实际附件闭包、代码归属及完整账本的受控位置。
随后分别开展真实迁移与费用核对，不必等待 IdP、告警或独立金标全部齐备。
不可在原库运行 Store/stats/parse，也不可重新调用模型补造“历史”。

| 门槛 | 本轮裁决 |
|---|---|
| 资产路径、旧代码及公开指纹定位 | 已完成限定范围独立核验 |
| B02-06 真实旧库/历史/引用/回退 | BLOCKED，真实输入未取得 |
| G-01 / G-03 / G-08 | 沿用既有 BLOCKED，未解除依赖 |
| E06 最新完整账本与恢复对账 | BLOCKED，快照预检不是完整账本认证 |
| 真实企业验收与生产发布 | 保持原结论，不由此 PR 放行 |

本轮无真实模型请求、无重新采集、无真实数据库改动、无费用记录重置。

