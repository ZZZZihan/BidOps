# B02-06 原云端采集包取回与副本预检 — 2026-10-23

**已取得原六公告包，原 manifest 与六份 raw 全部匹配；真实旧库、三轮历史、最新完整账本仍未取得。**
这是对同日本机查找的新增证据，不覆盖初查时的缺件、失败或工具测试记录。
B02-06、E06、G-01/G-03/G-08 继续 BLOCKED，73 项矩阵不变。

## 1. 取得方式与证据边界

用户确认原采集与企业联调在 ChatGPT 云端运行。只读查阅原采集会话与对应云端执行记录，
找到同一 ZIP 的两次原消息下载引用，以及既有 Library 文件注册记录。
通过原会话现有下载按钮取得文件，没有发送新消息、运行模型、重新采集或重建历史。
原消息标识、云端私有路径和文件注册标识仅保存在受控本地记录，不放入公开仓库。

浏览器下载事件等待曾超时；之后实际文件已落地，因此以文件字节核验为取得依据，
不把页面按钮状态或历史链接单独当作交付成功。下载目录中的两份同名/重名 ZIP
字节相同；不能据文件 mtime 推断此前整段搜索期间都存在。

本次 ZIP：`BidOps-P0-samples-20261022.zip`，**52,663 bytes**，10 个条目，ZIP CRC 检查通过。
新观测 ZIP SHA256：

```text
3999b73e550ea0f0a410d86d0b11f58f070c63c350b68f5124188358e0a83aa3
```

该整体哈希是本次封存指纹，历史没有公开同一 ZIP 的整体哈希。
身份核验依据仍是交接中固定的原 manifest SHA256 及六份 raw 身份/哈希/大小。

实际条目：README.txt、capture-events.json、manifest.json、notices.jsonl、六份 raw。
没有 SQLite、enterprise_sessions/enterprise_turns、responses.json 或费用账本。
六 raw 合计 **156,552 bytes**。本轮只验原文身份，不重新解析 notices.jsonl 来制造旧库。

## 2. 实际副本预检

下载原件保持原样；复制为只读封存 ZIP，检查归档路径/符号链接边界，再解到独立受控副本。
在 PR #27 的 `9c8dbd17c92d6d87a274552206c8229869758c94` checkout 内执行既有入口：

```bash
python3 scripts/legacy_preflight.py --capture "$bidops_capture_copy"
```

真实命令的绝对路径、UTC 和 stdout 留在受控记录；公开投影见
[capture-verification.json](evidence/b02-06-cloud-capture-20261023/capture-verification.json)。

- Python 3.12.12，SQLite 3.50.4；现有脚本与 expected 内容未修改。
- 实际 exit **0**，stderr 为空，`preflight_status=CHECKS_OK_NOT_ACCEPTANCE`。
- manifest SHA256 为 `1f9578666a4297ee75b181c0b629d123516feb757bc4902f67ea89059e57cf54`，与原指纹一致。
- 六份 raw 的 source identity、SHA256 和 byte_size 全部匹配固定 expected。
- `b02_06_status=NOT_EVALUATED`、`e06_status=NOT_EVALUATED`。
- 没有向数据库导入数据、运行 Store/stats/parse/migrate 或启动服务。

这是**真实原文包的有限身份预检**，与之前的合成工具 unittest 分列。
本次没有重复执行未改动的 14 项工具测试；其上一轮结果不冒充本轮新执行。
新提交只修改说明/证据，CI 结果在 PR 中按 SHA 单列，不继承旧提交的通过结论。

## 3. 原运行归属补充

只读云端执行记录支持以下更精确的边界：

- 原 ZIP 创建及文件注册成功记录为 10 条目、52,663 bytes，与本次取得的包一致。
- 三轮企业问答命令执行时存在未提交实现；企业实现 `6a41a067` 是其后归档提交，
  不能只凭相同 schema/锁将其称为实际 clean 运行 SHA。
- 原记录另有从 restored 库按 responses.json 中的三条 run_id 手工回并 agent_runs
  到默认源库的成功命令；没有会话表回并证据。默认源库仍不能替代三轮 restored 历史库。
- 这些是原运行记录，不是当前云端文件仍存在、数据库一致或最新费用完整的证明。

## 4. 更新后的精确缺件与门槛

| 输入 | 当前状态 | 下一次需提供/检查 |
| --- | --- | --- |
| 原六公告 ZIP、manifest、六 raw 及包内辅助记录 | **已取得，身份预检通过** | 保留原字节与封存指纹；不据此判断业务准确性或迁移通过 |
| `data/artifacts/enterprise-live-20261022/restored/bidops.sqlite3` 与 sidecars | **未取得** | 原环境一致副本、UTC 截止及实际运行归属；不对原库构造 Store |
| `restored/raw/` 与外层 `responses.json` | **未取得历史配套副本** | 原包 raw 已找回，但仍需按真实旧库引用验证闭包；不能假定两者天然一致 |
| `enterprise-live-20261022/backup/` 及匹配旧版本材料 | **未取得** | 区分运行前备份与含三轮历史的升级前快照；记录 WIP/锁/配置归属 |
| 默认或覆盖后的业务库 `agent_runs` 与必要运行副本 | **未取得，最新完整截止未知** | 可先独立执行 ledger-only；之后跨副本核对、供应商对账与备份后费用补齐 |
| 真实库引用的附件/企业材料、适用时 DBOS/v4 闭包 | **尚不能判断适用范围** | 仅按真实引用补齐；不为原 0 附件批次制造文件，不给旧 001–003 强加 DBOS |

旧库预检与 ledger-only 未执行真实输入路径：没有取得一致库，不能传 `--snapshot-confirmed`。
收到真实一致副本后沿用 [既有入口与迁移/回退说明](../docs/LEGACY-ASSET-VERIFICATION.md)，
历史要求仍为 `--min-history 3`。

| 门槛 | 裁决 | 原因 |
| --- | --- | --- |
| B02-06 | BLOCKED | 原文包已取得；真实旧库两次迁移、原生历史/引用和旧服务回退仍未执行 |
| E06 | BLOCKED | 无最新完整账本与跨副本/供应商核对，不推算剩余预算 |
| G-01 | BLOCKED | B02-06 仍是唯一工程阻塞 |
| G-03 | BLOCKED | 没有真实历史迁移与恢复证据 |
| G-08 | BLOCKED | 没有匹配旧代码/备份的 health、公告与历史 HTTP smoke |

代码合并和业务验收分别 BLOCKED；PR 继续 Draft，不合并或部署。
真实模型调用 0、来源重新采集 0、真实数据库写入 0；未释放 unknown/预留或确认费用恢复。
下一步集中取得原云端 restored 历史库的一致副本，最新账本可独立先行。
