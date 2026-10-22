# B07 经许可的增量更新与附件接收：实际验收记录

[实现 PR #22](https://github.com/ZZZZihan/BidOps/pull/22) 堆叠于 B06。B07-01～06 的专项工程行为已在
离线夹具中通过；B07-07 的真实许可、三轮 / 24 小时和真实中断恢复 **BLOCKED**。默认采集与网络
保持关闭，没有实际采购来源或模型请求。前置 B02-06 的指定真实旧库验收仍未完成，不能据此
合并整条依赖链、部署或宣称企业持续更新已经验收。

本机全量验证和 Linux CI 分别记录：固定代码本机 **1730 后端 / 148 浏览器通过且自然退出 0**；
同一 Head 的原 Linux CI 是 **1729 通过 / 1 失败**。失败是 64 MiB 地址空间限额先于 PDF 解码
限额触发时，测试错误地要求另一失败码；不能将失败记录改成通过。测试修复及新 CI 见末节。

## 被测版本与执行范围

- 完整代码 `5ac68753c22f46a3e3214e2f0c61a1ea8008808c`，tree
  `86e1457647f889e6150f5a0d9532f77aea444d12`。整合验证开始和结束时工作树干净；随后只补报告
  以及单独说明的测试修复。本报告不把旧代码的结果外推为修复后新提交已经完成全量验证。
- 主 Agent 实际执行时间：2026-10-22 20:43:08–20:59:41 UTC，即 2026-10-23
  04:43:08–04:59:41 Asia/Shanghai。命令、起止、退出码见
  [verification.json](evidence/b07-20261023/verification.json)。
- Python 3.12.12、Pydantic AI 2.59.0、DBOS 3.3.0、pypdf 6.21.2 core、Authlib 1.9.1。
  主锁 SHA256 `92ceebd17f2e98b0075eac8f13c9108b2686635e1ece5aa4d5f172de10289b27`；
  DBOS overlay `cc7fa6b4de2bd7dc10ca1745b56c5c1f0891edb649732843195a854e098ccd88`；
  附件 overlay `59fd6f618e0fa0414b0caad225096e12d5c88885ad4e0471299b667315a913ba`。
  主锁和 B01 的 18 个冻结文件未变，新增 pypdf 仅以独立 hashed overlay 安装。
- 实际运行 SQLite、DBOS、FastAPI、文件系统、独立 PDF 子进程及浏览器。外部 HTTP / IdP
  传输为 MockTransport 或协议夹具；真实采购 HTTP、模型调用和新增实际费用均为 0 / ¥0。
  历史累计费用账本缺口没有因此消失，不能把本次 ¥0 当作重置 ¥10 预算。
- 主 Agent 负责 ingestion service/runtime/schedules、API、存储原子接线和最终整合；三个子
  Agent 分别负责严格来源/下载/仓储及进程恢复、PDF/receipt/备份闭包、工作台与实际后端浏览器。
  子 Agent 均明确请求 GPT-6.1 Sol / ultra，主 Agent max 已获本次许可；配置请求不等于独立
  核验实际服务端档位。未参与对应实现的 Agent 交叉复核许可、原子性、解析、恢复及 UI。

## 完整执行结果

| 命令 / 被测入口 | 实际结果 | 已净化证据 |
| --- | --- | --- |
| `.venv/bin/pytest -q --basetemp <受控目录>/pytest` | 1730 passed in 552.40s，退出 0 | [backend](evidence/b07-20261023/backend.txt) |
| `.venv/bin/ruff check .` | All checks passed，退出 0 | [lint](evidence/b07-20261023/lint.txt) |
| `.venv/bin/ruff format --check .` | 221 files already formatted，退出 0 | [format](evidence/b07-20261023/format.txt) |
| `scripts/screening_eval.py`，使用 B01 外部预固定 manifest / input pin | PASS，agent_score=null、business_acceptance=BLOCKED，退出 0 | [冻结输出](evidence/b07-20261023/frozen-scorer.json)、[入口](evidence/b07-20261023/freeze.txt) |
| `npm run build` | TypeScript / Vite 成功，退出 0 | [build](evidence/b07-20261023/frontend-build.txt) |
| `BIDOPS_BROWSER_CHANNEL=chrome npm run test:e2e` | 148 passed (7.2m)，自然退出 0；无需清理 Chrome 派生进程收尾 | [browser](evidence/b07-20261023/frontend-browser.txt) |

## 逐项验收

每项共享上述完整 SHA、锁、执行时间及工程输入类别；测试源码定义具体输入、错误码和不变量，
全量日志证明执行。代码 C 结果与真实 B 结果分开，不将页面成功或 schema 自报当作许可证明。

| AC-ID | 输入、操作和预期 | 实际观察与证据 | 结果 |
| --- | --- | --- | --- |
| B07-01 C | allowed / denied / unknown、不同方式、期限、精确 URL 范围；手工责任声明与伪 actor | [source policy](../tests/test_source_policy.py)、[fetcher](../tests/test_fetcher.py)、[仓储](../tests/test_ingestion_repository.py)：legacy allowed 缺完整 policy 仍拒网络；原三来源保留 unknown；allowed_urls 与 policy scope 取交集；reviewed_at 未来、许可过期及策略变化均拒；手工 unknown 需登记来源、主动责任声明和服务端当前 admin；不会取得网络许可 | PASS |
| B07-02 C | 首次、重复、更正、终止、第二条写入故障、页提交前后 SIGKILL | [storage](../tests/test_ingestion_storage.py)、[repository](../tests/test_ingestion_repository.py)、[实际进程](../tests/test_ingestion_process_recovery.py)：notice/附件/观察/audit/receipt/cursor 同事务；第二条失败整页回滚；同页重放复用固定 observed_at 和 receipt，不新增观察；重复 bundle 不伪装新鲜；版本链接入 B02 | PASS |
| B07-03 C | HTML / text / 文本 PDF、扫描、加密、损坏、缺页、压缩膨胀、伪 MIME、超限 | [parser](../tests/test_attachment_parser.py)、[旧引用](../tests/test_notice_attachments.py)、[backup](../tests/test_ingestion_backup.py)、[ADR](../docs/ADR-0006-ATTACHMENT-PARSING.md)：成熟 pypdf core 在隔离子进程中提取；固定 raw/text/receipt/version/page map；HTML/text v1 身份不漂移；失败 / 不支持显式可见，扫描无 OCR；页定位由服务端 offset 映射，读旧引用不重新解析 | PASS |
| B07-04 C | 3xx / 401 / 403 / 404 / 429、5xx、超时、超限和连续失败；等待期间撤权 / 取消 | [fetcher](../tests/test_fetcher.py)、[runtime](../tests/test_ingestion_runtime.py)、[API](../tests/test_ingestion_api.py)：拒跳转及压缩响应；原始字节、尝试、请求总量和期限均有界；持久速率 slot 跨进程生效；等待后 GET 前及 commit 前重核当前策略；失败仅安全错误码和计数，不写原始 provider / 上传正文 | PASS |
| B07-05 C | 成功后下一次失败 / 超时，跨越 freshness window；读取和 UI 刷新 | [repository](../tests/test_ingestion_repository.py)、[实际 runtime](../tests/test_ingestion_runtime.py)、[真实浏览器](../frontend/tests/ingestion-real.spec.ts)：last_attempt 和 last_success 分离；失败不更新已核实观察；旧 source state 变 stale / unknown；GET 不抓取、不解析、不推进游标；空列表 200 后固定 detail 失权会清私有 UI | PASS |
| B07-06 C | 手工与计划同来源竞争、并发同 key、计划代际变化、停用 / 许可撤回、三进程重启 | [repository](../tests/test_ingestion_repository.py)、[runtime](../tests/test_ingestion_runtime.py)、[process](../tests/test_ingestion_process_recovery.py)、[backup](../tests/test_ingestion_backup.py)：原生 DBOS schedule / queue；同来源 active_run 原子锁；不同输入 409，同输入满容量仍重放；run 固定 schedule generation，暂停后旧代不继续；实际 enqueue 固定 app_version；恢复不重复页、任务或 tick；当前有权用户可查询 / 停止 | PASS |
| B07-07 B | 真实许可范围内三次、至少 24 小时、真实中断恢复 | 没有可核验的持续采集许可、真实运行 manifest 或 24 小时时间线；工程 MockTransport / SIGKILL 不能补足；未开始真实周期 | **BLOCKED** |

## 三个真实进程恢复窗口

[脱敏恢复汇总](evidence/b07-20261023/recovery-summary.json)仅公开固定 hash、数量、状态与
checkpoint 函数名。三个窗口各有 crash / recover / read-saved 三个独立 Python 进程，总计
9 个进程；同一 application_version
`7acc28b8d8a4f3e5abb3f994ebeb5d9fc29aea3083a0d9666573750d1e937250`。
首次进程真实 SIGKILL(-9)，第二进程使用原生 DBOS 恢复，第三读取不增加解析、下载或业务行。

| 杀进程窗口 | 实际恢复 |
| --- | --- |
| 页解析完成、业务提交前 | 1 run、2 公告 / 页 / receipt，cursor version=2；第一次未提交页重新 prepare，prepare 总 3 次；没有半页和重复逻辑记录 |
| 页业务提交后、SDK checkpoint 前 | 1 run、2 公告 / 页 / receipt，cursor version=2；原 receipt / 观察 / raw hash 保持；prepare 共 2 次，不重做已提交页 |
| schedule run 业务提交后、tick / enqueue 前 | 同一 generation 原生恢复为 1 run、1 tick、1 公告 / 页 / receipt；Mock HTTP 1 次、prepare 1 次；不会以新 native ID 另建任务 |

全部窗口第三次读取 JSON 与恢复 JSON 逐字节相同，页 checkpoint 有 output、无 error；原文
raw hash 匹配。真实来源 / 模型调用均 0。此处不是企业真实中断恢复或 24 小时更新验证。

## 共同门槛与回退

| ID | 本 PR 的具体证据与边界 | 当前状态 |
| --- | --- | --- |
| G-01 | B07-01～07 映射 policy/fetcher、原子 Store/ingestion 仓储、PDF、调度、API/UI 及恢复；业务缺口明确列出 | PASS |
| G-02 | 本机固定代码 1730/148、Ruff/build/freeze 均退出 0；原 Linux CI 1729/1 实际失败，测试修复后新完整 CI 尚待结果 | **BLOCKED：待修复提交复验** |
| G-03 | 010 / 011 增量重复迁移、B02 旧引用兼容、配对业务/DBOS 备份完整性、三 SIGKILL；恢复所有 ingestion run quarantine / schedule pause；指定真实旧库 B02-06 仍独立 BLOCKED | PASS（本 PR 工程范围） |
| G-04 | 当前身份 / source:manage / owner或admin / CSRF；读写重新授权；私有输入独立 body，不在 read/list/error 回显；限额先于解码与解析；公开归档仅显式净化白名单 | PASS |
| G-05 | 复用 DBOS 3.3 和 pypdf 6.21.2 core，无自制队列 / 爬虫 / 通用执行器；独立 hashed overlay、原生 RSS / RLIMIT_AS 及兼容边界见 ADR | PASS |
| G-06 | 离线 MockTransport / FunctionModel，0 真实采集 / 模型 / 新增费用；unknown / denied 不因历史 HTTP200 变 allowed；累计预算不重置 | PASS |
| G-07 | SHA/tree、起止和退出码、外部冻结 pin、9 进程同 appversion、公开证据 SHA256 索引；原失败 CI 不覆盖，修复后版本单列 | PASS |
| G-08 | 默认 ingestion/network 关闭；qualification/review 关闭仍可独立使用同一 DBOS 引擎；许可撤回后仅允许原定义暂停，不能借历史 policy 修改定义或重新启用；停止后备份、恢复全隔离；旧字节/审核/成本保留 | PASS |

回退优先关闭 `BIDOPS_INGESTION_ENABLED` 与 `BIDOPS_NETWORK_ENABLED`、暂停已有 native schedule，
并停止单 worker 后备份。保留已经形成的原文版本、receipt、审计及费用，不能 DROP 新表或恢复
旧授权来“回退”。需要切回旧程序时使用升级前验证备份和兼容镜像，在隔离目录核对，不将新
application_version 任务交给旧 worker 继续；实际操作见
[使用说明](../docs/AUTHORIZED-UPDATES.md)与[持久执行](../docs/DURABLE-RUNS.md)。

## 已知限制、CI 失败与后续复验

PDF 不支持 OCR、解密、任意 Office 或自动外链。macOS RSS watchdog 可能存在短时超额，Linux
地址空间限制可能先于 decoder limit 拒绝；均不能宣称通用不可信文件沙箱。下载总期限控制是否
释放收据；已开始的阻塞读取仍由当次 read timeout 结束，可能超过名义墙钟总限，不是硬实时。
不提供通用 feed 连接器、站内 crawler、DNS 隔离、分布式扩容或真实供应商吞吐结论。

[原 Linux backend CI](https://github.com/ZZZZihan/BidOps/actions/runs/39851394452/job/120078241548)
实际 checkout merge `815c5c29493259994d0edfeae8fc9e0261b99e71`，将本 Head 合入 B06
`e468a4a863f49f2e94c39c1e90bd66fd3ce84206`；1729 passed / 1 failed，922.88 秒，退出 1。
[失败摘录](evidence/b07-20261023/ci-failure.txt)及[状态 / 原日志 hash](evidence/b07-20261023/ci-status.json)
保留 `pdf_memory_limit != pdf_partial_pages` 的原始断言。

修复提交 `de96024711bbd0eaf40b580859bf871004ef2a66` 仅调整测试：保留 64 MiB 综合
fail-closed 断言，另以 512 MiB 内存独立验证原 8 MB decoder 限额；不改变生产 parser、
限额、主锁或 PDF overlay。parser 定向由 47 项增至 48 项，实现者实际 48 passed / 4.37 秒。
未参与此测试修复的 Agent 在新提交独立执行同一 parser 文件，实际 **48 passed / 4.57 秒**，
自然退出 0，见[定向复验](evidence/b07-20261023/parser-ci-fix.txt)。
这不是新提交全量 / 新 Linux CI 已通过；新 Head 全量待验，预计用例总数 1731 也不是实跑计数。
不能在这项共同门槛未关闭时宣称 PR 全门槛已经通过。

## 归档与未完成项

[evidence-index.json](evidence/b07-20261023/evidence-index.json)列出逐文件字节数、SHA256、
原根日志 hash 与净化方式。只复制八个已审阅根日志 / JSON，并投影恢复汇总及失败 CI 摘录；
不递归归档 pytest 目录。原始 SQLite / DBOS / WAL / SHM、backup、bootstrap、身份密钥、session、
CSRF、私有责任说明、上传 bytes/base64、序列化原生输入均排除。

尚待：修复提交及新 CI；前置 B02-06 真实旧库；真实来源许可与 B07-07 三轮 / 24 小时；真实
更正和附件覆盖、企业 IdP / HTTPS / 用户及目标环境验收。技术交叉复核不构成独立用户人审。
