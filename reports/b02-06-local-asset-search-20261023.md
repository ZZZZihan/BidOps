# B02-06 / E06 本机实查与缺件交接 — 2026-10-23

本次继续 PR #27，没有新建同内容 PR、重建历史或修改应用实现。
**未取得可归属原运行的真实采集包、旧业务库、三轮历史或最新完整账本。**
B02-06、E06、G-01/G-03/G-08 保持 BLOCKED；73 项矩阵仍为
57 C PASS、1 C BLOCKED、15 B BLOCKED。

本文是新增的本机查找/复验记录，原独立报告、原始工具测试和既有失败记录保持原样。
公开 JSON 见 [followup-verification.json](evidence/b02-06-followup-20261023/followup-verification.json)。
私有搜索清单包含实际绝对路径，仅随本次本地交付保存，不提交 GitHub。

## 1. 版本与当前工程状态

开始时重新读取 GitHub PR 与 CI：

- [PR #26](https://github.com/ZZZZihan/BidOps/pull/26)：open、Draft、未合并，
  head `0ff9413cff5e3cb13b85697e5a0c9181e4b86658`。
- [PR #27](https://github.com/ZZZZihan/BidOps/pull/27)：open、Draft、未合并，
  head `3c42b493d0be401cde5adbea5a59ff740fb49257`，base 为上述 #26。
- 两者与交接 SHA 一致，无新增远端差异需要接续审阅。
- 原项目检出 `069e4b495828caca4c64447062c030a0f313aada`，初始 tracked/untracked
  status 均无输出；为 #27 新建独立工作树，不切换/重置既有工作树。
- [交接提交 CI 39887750240](https://github.com/ZZZZihan/BidOps/actions/runs/39887750240)
  实际检出 `4f17989b594dced5664d805fcfc5ae2d9ddce29c`；Git 实算其 tree 与
  `3c42b493` 同为 `8c2b63dd582ed47ca52353cad9c052ea7c89c089`。

记录本次初查时 container/frontend 已 success，frontend 日志实际为
**159 passed / 2.9m**；backend 当时仍在 pytest，不能按旧 #26 的 2175 项填入。
最终 CI 和本次纯证据提交的 CI 另在 PR 描述及本地交付中记录，不能把此时快照当最终结果。

独立虚拟环境按仓库 `uv sync --frozen --python 3.12` 安装，单次使用清华索引，未改全局配置或锁。
本机实际 Python **3.12.12**、SQLite **3.50.4**、uv **0.10.0**、Ruff **0.17.1**。
这与原报告的 Python 3.12.14 / SQLite 3.53.1 分开记录。

| 本次实际执行 | 结果 | 证据含义 |
|---|---|---|
| `python3 -m unittest discover -s tests -p test_legacy_preflight.py -v`，PATH 首位为该工作树 `.venv/bin` | 14 PASS，exit 0 | 临时 TOOL-ONLY 工具测试 |
| `uv run --frozen --no-sync ruff check scripts/legacy_preflight.py tests/test_legacy_preflight.py` | exit 0 | 锁定 Ruff 静态检查 |
| `uv run --frozen --no-sync ruff format --check scripts/legacy_preflight.py tests/test_legacy_preflight.py` | exit 0，2 files already formatted | 格式检查 |
| capture、full/history、ledger-only 三个入口指向原项目已确认不存在的路径 | 均 exit 2 / BLOCKED，stderr 为空 | 缺件拒绝；未打开数据库、未声明一致快照 |
| 未参与实现 Agent 的代码/历史归属/公开指纹复核 | 未发现明确阻断工具局部交付的缺陷 | 静态技术复核；不是另一次真实迁移或人工业务验收 |

本轮没有重跑旧 7 项独立探针，不与 14 项相加。本地未重跑全套应用/浏览器测试。
脚本、测试、expected 与交接提交哈希一致；无新增工具、测试或依赖。

## 2. 本次实际查找范围

以下名称是公开摘要使用的根目录别名，精确绝对路径与 argv 留在本地交付：

| 根目录别名 | 实际范围 | 结果 |
|---|---|---|
| ORIGINAL | 本机 Projects/BidOps | 原采集、历史恢复目录及默认业务库/raw 未定位 |
| WORKTREES | 本机 Projects/BidOps-worktrees | 同上；存在工程测试/恢复/容量资产 |
| DELIVERIES | 本机 Documents/Codex | 同上；发现两份 20261023 工程交接 ZIP |
| DOWNLOADS、DESKTOP | 本机下载、桌面目录 | 未定位原 ZIP 或目标目录 |
| SHARED | 本机普通文件共享目录 | 未定位原 ZIP 或目标目录；未做同步/其他设备检查 |
| BACKUPS、ROUTER_BACKUP | 本机明确备份目录和 iStoreOS 迁移备份目录 | 未定位目标；前者仅有 .DS_Store |

实际执行 `rg --files --hidden -uu`，排除 `.git`、`node_modules`、`__pycache__`、`.venv*`，
枚举 **356,526 个文件路径**，exit 0、stderr 空。按精确 ZIP basename 和 capture/live
路径段匹配，结果 0。另用不跟随符号链接的目录遍历覆盖空目录，遍历 134,594 个目录，
目标命名目录 0、错误 0；8,067 个目录符号链接未跟随。
15 个 repo/worktree 根目录的 105 个默认路径及 sidecar 存在性检查均为不存在。
这些是特定时间窗的路径计数，不是全盘不存在证明。
另一次同范围枚举 356,531 个文件路径，以六个原 raw SHA 精确文件名或 SHA 加扩展名匹配，
六项均为 0；只比较文件名，没有对其他文件做正文哈希扫描。

搜索得到 1,619 个 `bidops.sqlite3` 路径：1,609 位于 pytest/test 路径；
另外 10 个来自两组既有工程演练，其配套报告/脚本明确声明合成夹具或 FunctionModel。
它们不能当作原六公告库、真实三轮历史或最新真实费用。
放宽到 BidOps 路径中的 SQLite/DB/sidecar 共 1,719 条，其中 1,688 条位于测试路径；
剩余 31 条仅登记大小、mtime、sidecar。包含容量测试 journal，保持原状。
当次 `lsof` 未观察到可见占用，**不据此确认停写或一致性**，未打开这些数据库。

两份交接 ZIP 只读取中央目录，分别 22 / 91 条，原 capture/live/raw/SQLite/responses
目标条目均为 0。没有解压任意其他存档或扫描数据库正文。

配置排查仅提取 `BIDOPS_DATABASE_URL` / `BIDOPS_DATA_DIR` 两个路径项：当前 shell 均未设置；
原项目和 worktrees 排除测试/依赖目录后仅发现 15 份 `.env.example`，没有实际 env 文件。
示例默认路径不是历史运行覆盖关系的证据；未加载凭据或输出环境全集。

GitHub 当前 releases 返回空列表，仓库级 Actions artifacts 列表 `total_count=0`；
39887750240 的 artifacts 也为空。仅说明查询时可访问产物为空，不证明历史从未生成或保管方没有副本。
GitHub 插件读取 PR/job/log/artifacts；插件未提供 releases/仓库级 artifact 清单入口的部分，
使用既有 `gh api` 只读查询并分页。

未覆盖：外接卷、远端/云端执行环境、网盘账户、聊天附件历史、目录符号链接目标、
排除目录与任意其他名称归档的内部内容。未找到不能外推到这些位置。

## 3. 精确缺件清单与取得后的入口

下表的“未取得”均指本次上述限定范围内；旧代码和公开指纹可获取，真实原件不可据此推定。
命令 A/B/C 定义在本节之后，完整副本实验继续使用既有 runbook，不新建另一套工具。

| 必需文件/对象 | 已观察到的路径线索与查找结果 | 当前状态 | 保管方需提供及一致性要求 | 收到后验证/受影响门槛 |
|---|---|---|---|---|
| 原六公告 manifest 和六份 raw | `BidOps-P0-samples-20261022.zip`；`data/artifacts/p0-capture-20261022/manifest.json`、`raw/<sha256>`；8 根目录未定位 | 仅有公开指纹 | 原 ZIP 或原目录；保留实际 notices.jsonl/访问说明，不重导入或重新下载代替原件。manifest SHA 应为 `1f9578666a4297ee75b181c0b629d123516feb757bc4902f67ea89059e57cf54`；六 raw 身份/大小按固定 expected | A 验原字节。只得到该包仍不能关闭 B02-06/G-03/G-08 |
| 三轮历史业务 SQLite、raw、原 responses | `data/artifacts/enterprise-live-20261022/restored/bidops.sqlite3`、`restored/raw/`、外层 `responses.json`；目标目录/默认库均未定位 | 未取得 | 停写 UTC、源位置/运行归属、主库及现存 sidecars、文件哈希清单；至少三条真实 completed turns 的 `enterprise_sessions` / `enterprise_turns`、response_json/messages_json 和关联。responses 不能替代数据库关系 | B；再用原生类型/实际旧定位器核验、两次迁移。影响 B02-06/G-01/G-03 |
| 升级前配套备份及实际旧代码归属 | `enterprise-live-20261022/backup/`；6a41a067 与 069e4b4 Git 对象/锁可读，原备份和真实运行配置未取得 | 代码可获取，资产归属缺失 | 匹配旧运行 SHA、WIP、锁文件、Python、两个路径覆盖及备份截止。原运行前 backup 不保证有后续三轮历史；应保留三轮库的升级前完整副本 | 固定旧代码和配套备份，在另一个新目录恢复并执行 health/公告/历史 HTTP smoke。影响 B02-06/G-08 |
| 最新完整业务账本和必要其他副本 | 默认 `data/processed/bidops.sqlite3` 及真实运行副本的 `agent_runs`；可确认来源者为工程夹具，其他候选仅登记路径/metadata，未取得可归属真实运行的库 | 未取得，最新截止未知 | 最新完整实例的一致快照、UTC 截止、运行/副本清单及包含/分叉/回并关系；agent_runs 完成/失败/重试/unknown/孤立预留与适用尝试记录 | C 可先执行；之后跨副本去重/查漏，不能由单份旧快照金额推余额。影响 E06 |
| 供应商对账与备份后费用区间 | 历史公开运行摘要可读，不含当前完整费用或供应商账单；本轮没有访问供应商账户 | 未取得 | 对应账号/时间窗的受控账单或导出、应用外请求、备份后遗漏区间说明；不要求提供 API key | 与实际 agent_runs/attempts 对账并检查恢复后保守费用屏障。影响 E06；不得清零或释放未知预留 |
| 数据库实际引用的附件/企业材料 | 旧 capture 原批附件请求为 0；当前缺真实数据库，无法列出后续实际引用 | 条件未能判断 | 仅按真实行引用提供 raw/attachments/private company 文件及授权/留存信息；不为最初 0 附件批次制造文件 | B 限定闭包加实际引用复验。缺引用原文时仍不能通过 B02-06 |
| 持久任务/采集实例的 DBOS 配套闭包 | 较新库可能引用 `private/jobs/dbos.sqlite3` 及 v4 backup manifest；所见系统库位于工程演练/容量测试路径，未取得可归属真实运行的配套闭包 | 条件未能判断 | 仅当真实库有对应持久状态时，提供同一停写时点业务库/系统库/sidecars/文件与 v4 关联清单 | 副本上官方 v4 验证与恢复；B02-06/G-03/G-08 适用时需要。001–003 旧实例无 DBOS 不凭空要求 |

在 PR checkout 内，用实际受控副本替换变量，仅确认一致后才加 `--snapshot-confirmed`：

```bash
# A：原采集包解出的受控副本
python3 scripts/legacy_preflight.py --capture "$bidops_capture_copy"
# B：真实历史一致副本，已知三轮要求保持不变
python3 scripts/legacy_preflight.py --database "$bidops_legacy_db_copy" \
  --data-dir "$bidops_legacy_data_copy" --snapshot-confirmed --min-history 3
# C：可单独先做，不等 A/B 或 IdP/金标
python3 scripts/legacy_preflight.py --database "$bidops_latest_ledger_copy" \
  --snapshot-confirmed --ledger-only
```

本次缺件探针没有使用 `--snapshot-confirmed`，因为没有一致副本可供确认。
上面三条是收到输入后的命令，不冒充本次真实运行。
exit 0 仍只是 `CHECKS_OK_NOT_ACCEPTANCE`；B02/E06 字段为 `NOT_EVALUATED`。
真实 JSON 保留受控位置；公开提交只能使用显式白名单摘要。

## 4. 历史归属、不同缺件组合与最终门槛

本次直接复读固定 `6a41a067` 旧脚本，确认 restored 中执行三轮，finally 只回并
agent_runs。它到 cfcd7c8 的 Git diff 仅 TASKS/报告两文档；6a 与 069 的 uv.lock
SHA 都为 `92ceebd17f2e98b0075eac8f13c9108b2686635e1ece5aa4d5f172de10289b27`。
相同 schema/锁仍不能证明实际运行 SHA，必须结合原运行记录和配置选择回退版本。

- **只有原文包**：可验六份字节身份，仍无历史迁移/费用结论。
- **只有旧库**：可在一致副本上清点账本/结构；缺 raw、真实历史或实际代码归属的门槛继续阻塞。
- **有会话但缺原文**：可核对原生类型及关系，但不能完成 quote/区间/原文复验。
- **旧账本存在但最新截止未知**：可报告该快照占用，E06、累计余额和费用恢复确认仍不能通过。
- **本次实际情况**：没有取得可归属真实运行的原文包或业务库；仅有公开元数据、代码与工程夹具。

| 门槛 | 裁决 | 当前理由与必须满足的通过条件 |
|---|---|---|
| B02-06 C | BLOCKED | 原件/历史未取得；须完成真实旧库首次与重复迁移、数量/逻辑内容/原文哈希、原生历史和旧定位器引用、另目录旧服务恢复 |
| E06 | BLOCKED | 真实账本未取得，最新完整截止/跨副本/供应商核对未完成；快照汇总与恢复 token 不足以关闭 |
| G-01 | BLOCKED | B02-06 仍为唯一 C 阻塞；清单及工具测试齐备不能解除 |
| G-03 | BLOCKED | 未执行真实旧数据/历史重复迁移与恢复，不用合成记录代替 |
| G-08 | BLOCKED | 未执行匹配旧代码和配套备份的旧服务 HTTP smoke；须保留升级后新历史、费用和预期登录撤销边界 |

G-02 本轮局部复验 PASS；完整 CI 单列。G-04 公开摘要白名单、G-05 无新增框架依赖、
G-06 真实模型/来源调用 0、G-07 版本和证据分列均保持本 PR 局部边界，不扩大到真实企业验收。
没有原库打开、应用启动、迁移、回退、重新导入、parse、费用释放/重置或恢复确认。
真实模型调用 0、来源重新采集 0、新增真实费用 0；当前累计余额未知。

**代码合并：BLOCKED。业务验收：BLOCKED。** 不合并、不部署，PR #27 继续 Draft。
下一项最值得投入的工作是定位 **2026-10-22 原执行环境或原交付附件**，优先取得
enterprise-live/restored 的一致快照和对应原文；最新账本可独立先交付，不必等待其他环境条件。

两个子 Agent 分别完成限定范围资产定位、未参与实现的技术复核；均显式请求
`gpt-6.1-sol / ultra`。请求参数可见，但未独立核验底层供应商实际运行配置；技术复核不替代人工业务验收。
