# 经许可的更新、手工导入与附件证据

B07 为明确登记的来源提供受限 HTTP 更新、带责任声明的手工导入，以及 DBOS 计划调度。
它将公告、附件、失败记录、固定版本和游标接入既有公告历史。功能默认关闭，网络获取也独立
默认关闭。一次任务执行完成只表示本次工程流程结束，不表示来源许可已经核实、资料一定完整，
更不表示企业资质通过或人工审核完成。

来源版本与历史引用见 [公告数据计划](ENTERPRISE-PR-PLAN.md#5-b02--公告版本采购包与证据数据层)，
持久执行和停止备份见 [持久任务运行说明](DURABLE-RUNS.md)，PDF 设计、资源边界与兼容策略见
[ADR-0006](ADR-0006-ATTACHMENT-PARSING.md)。本说明中的受控工程夹具不构成真实来源许可、
真实身份环境或采购业务准确率验收。

## 安装、默认值与启动

主项目 `pyproject.toml` 与 `uv.lock` 保持冻结。DBOS 和 PDF core 分别使用带下载哈希的
独立 overlay；在仓库根目录执行：

```bash
uv sync --frozen --python 3.12
uv pip install --python .venv/bin/python --require-hashes \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  -r requirements/durable.lock
uv pip install --python .venv/bin/python --require-hashes \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  -r requirements/attachments.lock
```

安装后使用 `uv run --frozen --no-sync ...`，避免普通同步移除主锁之外的依赖。PDF overlay
锁定 `pypdf==6.21.2` core，不安装 OCR、密码解密或额外外部命令。不要通过改主锁或安装未锁版本
解决运行版本不匹配。

| 设置 | 默认值 | 含义 |
| --- | --- | --- |
| `BIDOPS_INGESTION_ENABLED` | `false` | 关闭导入、更新和计划入口。 |
| `BIDOPS_NETWORK_ENABLED` | `false` | 独立关闭外部 HTTP；手工导入不要求开启网络。 |
| `BIDOPS_INGESTION_MAX_ACTIVE_RUNS` | `10` | 来源任务容量，独立于等待人工审核的资格任务。 |
| `BIDOPS_INGESTION_MAX_SCHEDULES` | `20` | 可登记的计划上限。 |
| `BIDOPS_INGESTION_FRESHNESS_SECONDS` | `86400` | 来源最近成功记录的新鲜度窗口。 |
| `BIDOPS_MAX_BYTES` | `2000000` | 每个公告原文或附件的字节上限；最高允许配置为 10000000。 |
| `BIDOPS_SOURCE_REGISTRY` | `config/sources.json` | 维护者管理的来源与许可登记。 |

企业模式、当前 SSO 身份、CSRF 和单机单 worker 约束沿用企业部署。功能开关、网络开关、有效
来源策略和身份权限分别校验，不能互相替代。即使资格筛选、持久资格任务和人工复核功能关闭，
来源任务也可使用同一进程内的 DBOS 引擎。不要为并行采集启动多个企业 worker 或第二个写入实例。

生产 Docker 构建必须提供实际完整提交 SHA：`docker build --build-arg BIDOPS_CODE_SHA=...`。
省略号表示部署者提供的实际值，不是可照抄的 SHA。镜像保留 `BIDOPS_LOCK_PATH=/app/uv.lock`、
`BIDOPS_DURABLE_LOCK_PATH=/app/requirements/durable.lock` 和
`BIDOPS_ATTACHMENT_LOCK_PATH=/app/requirements/attachments.lock`，用于固定运行依据。
输入设置、代码或依赖改变后保留旧历史，创建新任务；不要让旧检查点冒充新版本执行。

## 登记来源与许可

页面只显示配置缺口，不提供修改许可策略的表单。维护者在受控配置中登记来源的唯一 `id`、
`host`、首页、有限 `allowed_urls`、robots/terms 状态及许可依据。网络更新还必须有完整的
`policy`：

- `revision`、`status`、`evidence_sha256`、`evidence_reference`、`reviewed_by`、`reviewed_at`；
- 带明确时区的 `valid_from`、`valid_until`，当前时间必须在允许窗口内；
- `acquisition_modes`：`manual_import`、`manual_fetch`、`scheduled_fetch` 的明确子集；
- 有限 `url_scope`，每个地址都须属于该来源的 `allowed_urls`；
- `rate_limit_seconds`、`max_attempts`、`max_requests`、`max_bytes`、`total_timeout_seconds`。

许可证据哈希只验证记录完整性，不能证明内容真实或自动取得使用权。robots 允许、HTTP 200、
历史成功和管理员身份都不等于当前许可。unknown、denied、过期、缺少依据或方式不符时，网络
获取拒绝。策略变化后旧任务不能继续按旧许可执行；需要在当前许可下明确提交新任务。

URL 必须是登记 host 上的明确 HTTPS 地址，不接受凭据、查询参数、片段、模糊编码、路径穿越
或任意跳转；服务器按精确登记范围复核。当前实现拒绝重定向，不把跳转目标自动加入允许范围。
这里的 host/path 检查不应解释为额外 DNS 解析隔离或完整网络沙箱承诺。没有通用站内爬虫、
自动链接展开或已接入的 JSON feed 连接器；公开的支持方式只有 `manual_import` 和 `http_fetch`。

手工接收允许对尚无网络许可的登记来源，要求上传者明确确认自己有权提供原文和附件，并填写
责任理由；这不会授予 HTTP 获取许可。存在明确 policy 时，其禁止、方式和期限同样受校验。
未登记 URL、客户端伪造的“许可允许”或省略责任声明不能绕过服务端策略。

## 页面操作

企业登录后打开“授权更新与导入”。默认关闭时页面只确认状态，不读取私有任务或自动提交。
所有当前登录者可以查看净化后的来源摘要；新增、导入和计划管理需要 `source:manage`，目前由
明确管理员角色获得。任务列表、详情和停止由服务端检查所有者或当前管理员；降为 viewer 的
原所有者仍可停止自己的任务，但不能再新增采集。

来源卡分别显示最近尝试、最近成功、距成功时间、新鲜度、已持久游标、计数和错误。后续尝试
失败不会刷新最近成功时间；没有成功记录应显示未知，不能将失败或没有更新包装成已核实完整。

手工导入一个公告及多个附件：

1. 选择已经配置的来源，填写该来源允许的公告 HTTPS URL，选择 HTML 或 TXT 公告原文。
2. 选择明确文本编码，可同时上传多个附件。PDF 作为附件；如来源明确给出预期页数，再填写该
   页数，不凭猜测补值。附件来源 URL 可选，填写后仍必须在来源允许范围内。
3. 填写责任理由，主动勾选有权提供原文和附件的声明。声明默认不勾选。
4. “已核对公告和所需附件完整”也是默认不勾选的独立责任者声明。任一附件失败、未识别或不支持
   时，服务端仍强制保留不完整状态。自动 HTTP 更新不会因请求成功自动声明完整。
5. 可填写本页成功提交后的游标，再提交。页面只有收到服务端确认才显示受理的任务编号；202
   是受理，不是执行成功。查看最终状态、失败计数、页回执和固定公告/附件版本。

“提交 HTTP 更新”只处理填写的有限公告 URL 和附件 URL，附件成功与否由实际下载和解析决定。
“保存或暂停计划”复用该明确 URL 范围，要求标准五字段 cron、理由和服务端当前状态版本。
首次版本为 0，计划默认暂停；更新/暂停必须使用返回的当前版本。cron 最低间隔一分钟，仍须
遵守来源更严格的速率与请求上限。暂停保留定义和审计记录，不删除历史或回滚已持久页。

相同输入的网络失败重试沿用同一幂等键；修改输入后生成新键。“开始另一项提交”才明确允许相同
输入另起任务。应答未确认时先查询原任务，避免反复点击新建。409 表示状态或命令冲突，页面
保留草稿并要求重新读取后决定，不自动覆盖。自动轮询和手动刷新只读已保存状态，不重新下载。
401/403/404 会清除私有任务、URL、文件选择、责任说明和计划草稿；浏览器不使用 localStorage
保存私有输入。

## HTTP 合同与边界

所有路径在 `/api/ingestion` 下，企业身份是前提。写请求使用真实会话及 `X-CSRF-Token`，
并满足 Origin 校验。actor、获取时间、许可和成功状态只由服务端决定，额外 JSON 字段拒绝。

| 请求 | 用途 |
| --- | --- |
| `GET /status` | 返回 enabled、configured、worker_available、backend、支持方式与大小上限。 |
| `GET /sources` | 所有当前登录者可读的来源许可、新鲜度和计数摘要。 |
| `GET /runs?offset=0&limit=20` | 当前可访问任务的稳定分页与全量 total；limit 为 1–100。 |
| `GET /runs/{run_id}` | 保存状态、计数、失败、游标及已持久页回执。 |
| `POST /runs` | 有限 `source_id`、`idempotency_key`、`items` 明确 URL 批次，返回 202。 |
| `POST /imports` | 有界手工包、责任声明和页面，返回 202。 |
| `POST /runs/{run_id}/cancel` | `{expected_state_version}`；停止以保存状态为准。 |
| `GET /schedules` | 需要来源管理权限的计划定义列表，包含固定 URL。 |
| `POST /schedules` | 明确 schedule_id、当前版本、cron、enabled、理由和固定 URL 范围。 |

手工包顶层是 `source_id`、`idempotency_key`、`rights_attestation:true`、
`responsibility_reason`、`pages`。每页包含 `items` 与可空 `cursor_after`，每个公告包含
`source_url`、`content_type`、`encoding`、严格 base64 的 `body_base64`、默认 false 的
`source_complete` 及有限附件。附件提供 bytes，或明确 `failed:true` 和安全 `error_code`，
不能同时提供两者，也不能由客户端自称 parsed。失败声明是接收记录，不是成功证据。

完整手工 JSON 上限为 10 MiB（10485760 bytes），最多 50 页、每页最多 100 公告、总计最多
500 公告、每个公告最多 50 附件；URL 批次最多 100 公告。单 blob 再受 `BIDOPS_MAX_BYTES`
限制，base64 严格解码且不接受换行或无效字符。`POST /imports` 在读取 stream 前先完成身份
和 CSRF 校验，逐 chunk 限制总大小，再校验 schema；错误不回显原始私有 JSON/base64。
只有此精确路径获得较大流式请求豁免，其他管理 JSON 仍受既有 16 KiB 限制，过大的 URL 清单应
拆成明确小批次，不能期待 schema 的条数上限取消 HTTP 字节上限。

获取遵守配置与 policy 中更严格的大小、速率、尝试和时间限制；policy 的重试最多三次，速率
至少五秒。403、429、超时、异常类型、超限和不允许重定向均保留为可查失败，不通过换 host、
绕过限制或无界重试完成。取消先记录请求，再停止后续操作；已经完成的外部请求无法撤回。

## 原子页、固定证据与失败状态

一页公告、其附件、观察记录、审计、回执和游标在同一业务事务提交。解析在事务外完成，不能
在数据库写锁中等待不可信 PDF。重复页核对已存指纹并复用回执；事务失败不发布部分可见页，
游标不提前前移。失败附件可以作为明确失败记录随页提交，使资料缺口可见，但不会令来源完整。
内容不变、没有新增版本和本次失败分别计数；重复导入不能覆盖原文字节或重解释旧引用。

状态包括 queued、running、cancel_requested、cancelled、completed、partial、failed、
interrupted。部分完成应检查失败记录和固定页回执，不能只看总任务是否结束。新代码、设置或
来源策略不兼容时保留历史而拒绝旧任务继续执行。GET 不恢复、重抓或重新解析。

HTML/纯文本沿用固定规范化文本与字符 offset。PDF 使用受限独立子进程提取，保存实际解析器
版本、选项/限额、raw/text/receipt SHA 和完整 page map；引用页码由固定 offset 映射派生，
不能相信调用方填写的页码。旧引用读取验证旧 bytes 和 receipt，不调用当前安装的新解析器。

有 `catalog:read` 的当前身份可通过 `GET /notice-versions/{version_id}/attachments` 读取附件
状态，使用 `GET /notice-attachments/{record_id}/evidence?start=...&end=...` 生成固定引用。
随后 `POST /notice-evidence/location` 接受完整原 `EvidenceRef`，同样要求 CSRF，返回
`{evidence,location}`。PDF 的 location 包含 `kind:pdf_pages`、实际页号和固定
`parser_receipt_sha256`；字符区间跨页时返回所有相关页。改写 quote、版本或 offset 的引用
拒绝，不用当前解析器重新生成历史定位。上述路径同样位于 `/api` 前缀下。

PDF 默认限制为 10,000,000 原始 bytes、200 页、单解码流 8,000,000 bytes、每页 100,000
字符、全文 500,000 字符、20 秒、256 MiB 子进程 RSS；实际接收仍受更低的单 blob 上限。
同一 OS 用户的解析并发为 1，忙碌明确失败。超时/资源超限会 kill 并 wait 回收。macOS 使用
RSS watchdog，存在监测间隔和瞬时超额，不是内核地址空间硬隔离；详见 ADR 的实测边界。

加密 PDF 不尝试解密，扫描或空白页没有 OCR，损坏、缺页、解码超限和不支持格式均保持失败或
未识别。预期页数只有来源已知时才能检查；没有可信预期页数不能发现原文件已经缺少物理页。
`parsed` 只说明受支持文字提取成功，不保证表格顺序、图像文字或采购条件的语义完整性。

## 迁移、私有输入与恢复

`010_authorized_ingestion.sql` 新增许可修订、任务私有输入、执行状态、页回执、请求/失败、计划
版本和调度触发记录；`011_attachment_receipts.sql` 保存固定 PDF receipt。迁移通过现有 ledger
按顺序执行，旧公告和 HTML/plain 附件的版本 ID/引用算法保持兼容。不要删除迁移表或修改旧
raw/hash/receipt 来让新代码启动。

提交原始包、责任理由和执行检查点属于私有输入，不能放入静态 Web、普通日志、Git 或公共
调试接口。运行数据、DBOS 状态、WAL、私有 raw 与备份一起保护。按来源资料的实际许可和
留存要求管理，不把“已经有公告展示”解释为上传包可以公开。

备份前停止整个服务与 worker，确认没有其他写入实例。目标目录须尚不存在，使用既有 CLI：

```bash
uv run --frozen --no-sync bidops-enterprise --env-file private/enterprise.env backup \
  --output /controlled/b07-stopped-backup --retention-days 7
uv run --frozen --no-sync bidops-enterprise --env-file private/enterprise.env restore \
  --input /controlled/b07-stopped-backup --output /controlled/b07-restored-independent
```

`/controlled/...` 是运维选择的独立受控路径。备份期限默认 7 天，可选 1–30 天。存在 DBOS
持久任务时使用 v4 绑定业务库、状态库和文件闭包，不能在线分别复制数据库，也不能将别处的
状态库混入恢复。恢复保持旧身份失效、私有对象隔离、来源执行与计划暂停；重新登录不能自动
恢复旧许可、旧 grants 或旧人工决定。当前来源策略和实际权限必须重新核对；新环境不要立即
自动启用网络或计划。

如需回退，先停 worker 并保存包含新表、新结果和全部原始文件的停止快照，再在独立副本上
验证旧版本读取能力。旧版本不能读取的新表与 receipt 保留在新版本快照中；不要降级后对原
业务库删表、清理 DBOS 状态或覆盖历史。恢复费用/审核隔离仍沿用 B05/B06，不因来源任务不
调用模型而取消其他持久任务的费用核对屏障。

## 工程证明与待完成验收

本次只允许合成 UNIT 来源、程序生成 PDF、离线签名 OIDC 协议模拟和受控 HTTP 故障注入；
没有真实网络采集、外部模型调用或真实业务批准。可执行的定向检查入口为：

```bash
uv run --frozen --no-sync pytest -q tests/test_ingestion_api.py
cd frontend
npm run build
npx playwright test tests/ingestion.spec.ts tests/ingestion-real.spec.ts
```

命令入口与实际执行结果应在本轮验收报告中分别记录，本说明不将未执行命令写成通过数量。
浏览器真实 HTTP 夹具使用生产应用、SQLite/ACL/DBOS 和受限 PDF 解析，只将外部 IdP transport
替换为签名协议模拟；“真实 HTTP”不能缩写为真实 IdP 或真实来源验收。

B07-07 要求真实许可范围内至少三轮、覆盖至少 24 小时的增量运行，以及一次真实中断恢复；
当前尚无这些真实运行与许可证据，保持 BLOCKED。B02 的真实公告/附件迁移与引用样本、B08 的
独立金标质量及人工耗时对照、B10 的真实 HTTPS/IdP/账号/材料与用户验收也不由这些工程测试
补齐。没有新增更正或附件时必须如实报告覆盖不足，不能用合成内容填满真实验收数量。
