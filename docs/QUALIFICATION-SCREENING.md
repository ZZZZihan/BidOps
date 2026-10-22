# 候选筛选与逐条资格核对

B04 在企业模式中提供固定候选批次和保存的资格核对表。默认关闭，候选筛选不调用模型；
资格核对通过 Pydantic AI（PAI）的抽取、核验两个阶段生成建议。结果始终为
`human_review_state: "unreviewed"`。候选命中、执行完成、条件“满足”和人工批准是不同状态，
本功能没有批准资格、提交投标或代表人工签字的接口。

本文说明当前代码的使用及运维边界，不提供真实业务质量结论。当前证明仅来自离线工程样例、
协议测试，以及真实本地 HTTP / SQLite / ACL 配合离线 `FunctionModel` 的集成；
未执行的验收不计作通过，具体命令、版本和结果以本次验收报告为准。

## 默认关闭与身份边界

`ScreeningSettings` 的环境变量前缀为 `BIDOPS_SCREENING_`。以下默认值同时阻止普通部署
意外发出 B04 外部模型请求：

```dotenv
BIDOPS_SCREENING_ENABLED=false
BIDOPS_SCREENING_LIVE_CALLS_ENABLED=false
BIDOPS_SCREENING_LEDGER_VERIFIED=false
```

`DEEPSEEK_API_KEY` 沿用现有服务端凭据名称；不要把密钥写进前端、示例、日志或仓库。
本文不提供密钥占位值，也不要求开启真实调用。`enabled` 仅控制 B04 功能；旧 Agent
入口有自己的配置，关闭 B04 不等于关闭所有模型入口。

企业模式未开启时 `/api/screening/*` 返回 404。企业模式开启但 B04 关闭时，已认证用户
仍可读取 `/api/screening/status`，其他业务入口返回 `screening_disabled`（503）。
状态响应包含 `enabled / configured / model / live_calls_enabled`。`configured` 可以来自
测试注入的离线模型，也可以来自本地真实模型配置，不是外部连通性、费用核对成功或模型
质量的证明；恢复屏障在付费预留事务中另行核验。

所有入口复用 Authlib OIDC 和当前主体校验；主体必须 active、可信角色快照未过期。
创建候选批次和清单要求 `agent:use`，读取状态、批次、历史和结果要求 `catalog:read`；
修改请求还受既有同源及 CSRF 检查约束。身份撤销、240 秒内的角色快照边界和受支持的
Keycloak Back-Channel Logout 范围见 [ADR-0004](ADR-0004-IDENTITY-REVOCATION.md)。

私有画像及每份来源材料必须在请求当下有 `read` 权限；身份、来源版本、哈希与派生来源闭包
在工具使用、模型请求前后、持久化和结果释放前重查。已保存结果还限定原创建者读取；
管理员没有隐含正文读取权，其他用户不能仅凭 `run_id` 获取结果。
本地停用、撤权、到期或删除不能靠旧 Cookie、旧上下文或固定 manifest 绕过。
浏览器在 401/403/404 时清除私有结果和所选私有输入，不把拒绝后的旧正文继续显示为可用。

## 固定候选批次与完整分页

`POST /api/screening/candidates` 接受：

```text
profile_revision: {object_id, version_id, sha256}
filters: {keyword?, min_budget_yuan?, max_budget_yuan?}
as_of?: 带时区的时间
```

`profile_revision` 必须来自有权读取的画像固定版本详情，不能只带对象 ID 或客户端自行
拼出的哈希。`keyword` 最多 120 字符，预算上下界非负且下界不能大于上界；金额单位为元，
服务端使用 Decimal。调用方可用十进制字符串表达金额，避免 JavaScript 浮点数损失精度。
省略 `as_of` 时使用本次服务端 UTC 时间。

服务端在一个读事务中枚举截至 `as_of` 已可用且已观察的完整公告版本集合，按项目/采购包
形成单元，不先截取目录第一页。无法关联项目、缺标题/预算、未识别采购包、版本先后关系
不明确等单元仍保留，并给出 `missing_fields` 和 `reasons`。批次包含：

- `candidate`：符合明确筛选条件，尚未核对资格。
- `excluded`：已有明确字段不符合用户筛选条件，保留排除理由。
- `needs_verification`：存在缺失或歧义，需要核实；缺失预算不按零处理。

批次保存完整集合、固定画像、筛选条件、公共来源版本和 `manifest_sha256`。
创建响应为第一页，返回 `batch_id / as_of / profile_revision / filters / counts / total /
offset / limit / has_more / truncated / items / qualification_checked / source_state`；
`qualification_checked` 始终为 `false`。这些筛选条件不等于企业资格规则。

继续使用 `GET /api/screening/candidates/{batch_id}?offset=20&limit=20` 读取同一批次。
默认 `offset=0, limit=20`，`limit` 范围 1–100；一直读取到 `has_more=false` 才覆盖全批。
`total` 和 `counts` 属于全批，`items` 属于当前页。`truncated=true` 表示这一页不是全批，
包括非首页；它不表示后端丢掉了其余单元。后续导入、观察或修改资料不改变旧批次的分页结果。
界面把保存批次的固定画像版本、哈希和筛选条件与当前编辑输入分开显示。

## 两阶段核对、五态与证据

`POST /api/screening/checklists` 接受：

```text
profile_revision: {object_id, version_id, sha256}
project_id: 非空项目 ID
target_package_ids: []                 # 可省略，最多 100 个
additional_material_revisions: []      # 可省略，最多 100 个固定 VersionRef
as_of?: 带时区的时间
```

目标采购包必须属于固定来源闭包且不能重复；空列表核对闭包中全部已识别采购包，未识别包时
保留未明确的适用范围。输入 manifest 固定画像、材料、公告及附件版本、哈希、观察时点、
解析/规范化版本、项目状态及缺失附件，不假定“最新一条公告”自动取代全部历史条款。

1. **抽取阶段**：逐段提交公告/可读附件的固定文本与字符范围，PAI 输出结构化条件草案。
   服务端绑定稳定条件 ID、逻辑树、采购包适用范围和原文引用，记录非条件及未解析段落；
   没有“最多前三条条件”的截断规则。
2. **核验阶段**：按目标采购包和条件核验固定画像及授权材料。PAI 输出逐原子判断，
   服务端校验证据后按 AND/OR、否定、例外逻辑归并；未知不能经否定变成已证明。

公告和材料中的指令均是来源数据，不能改变工具权限或人工审核状态。当前工具
`read_company_material(material_version_id, start=0, end=None)` 仅能读取本次 manifest
已授权材料。单页最多 4,000 个规范化文本字符，返回 `offset/end/total/has_more/truncated`
及固定引用；后续范围可继续分页。范围是字符偏移，不是字节偏移或未经实现的 PDF 页码。

条件状态沿用 B01：

| 状态 | 含义 |
| --- | --- |
| `satisfied` | 在给定范围和时点，有可校验依据支持满足 |
| `not_satisfied` | 有可校验依据支持不满足 |
| `information_insufficient` | 证明、日期、范围、解析或执行信息不足 |
| `evidence_conflict` | 证据之间存在需要处理的冲突 |
| `not_applicable` | 有适用范围或例外依据支持不适用 |

明确判断需要公告侧依据和企业事实/材料侧依据；引用绑定固定版本、哈希、规范化字符范围
与原文。服务端拒绝伪造、越界、版本错误、无权或到期引用。引用确实存在、原文一致只证明
结构和来源可核查，不证明模型对语义的解释正确；材料缺失、自报字段及未知有效期不能直接
判满足。冲突、未知和范围不明保留其状态，不能默认为通过。

响应包含 `run_id / result_revision / state / inventory / groups / applicability /
package_summaries / recommendation / human_review_state / error_code / missing_items`，
以及 `manifest / provenance / source_state`。`groups` 按公告主版本和采购包保存 B01
assessment；`inventory` 保留条件、文档覆盖、未解析项和缺失附件。建议枚举为
`review_recommended / needs_materials / resolve_conflict / do_not_pursue / status_unknown /
not_evaluated`，均不是批准结果。硬性不符、未知、冲突或已知终止不能自动输出资格通过。

## 资源边界、失败与部分完成

下列环境变量均使用 `BIDOPS_SCREENING_` 前缀，限制由服务端配置而非请求体控制：

| 配置后缀 | 默认值 | 可配置边界及作用 |
| --- | --- | --- |
| `MAX_REQUESTS` | 12 | 1–30，两个阶段合计的模型请求次数，含验证重试和工具后续请求 |
| `MAX_SECONDS` | 90 | 大于 0、最多 300 秒，抽取及核验的整体执行预算 |
| `MAX_SEGMENT_CHARS` | 4,000 | 100–8,000 个字符，公告/附件分段目标上限 |
| `MAX_REQUEST_BYTES` | 120,000 | 10,000–200,000 字节，完整框架消息和请求参数的序列化大小 |
| `MAX_OUTPUT_TOKENS` | 6,000 | 100–8,000，每次模型输出上限 |
| `BUDGET_CNY` | 10 | 大于 0、最多 10 元，沿用共享累计账本进行保守准入 |
| `INPUT_CNY_PER_MILLION` | 2 | 不低于 2，配置的每百万输入 token 估算费率 |
| `OUTPUT_CNY_PER_MILLION` | 8 | 不低于 8，配置的每百万输出 token 估算费率 |

上述费率是当前代码的保守估算配置，不是本文对供应商未来价格或实际账单的承诺。
框架请求大小包含提示、历史工具返回和工具 schema，不仅是用户输入；超限在外部请求前拒绝。
普通 HTTP JSON 请求仍受企业层 16 KiB 上限约束，不能把 `MAX_REQUEST_BYTES` 当作 HTTP
上传限额。单份保存结果连同 manifest、provenance、raw 输出最多 10,000,000 字节。

分段覆盖全部字符并保留整行；超过上限的单行整段标记不可处理，进入未解析项，不能截掉尾部
再声称完整覆盖。每次抽取限制 3 个模型请求和 6 次工具调用，每次条件核验限制 4 个模型请求
和 6 次工具调用；PAI 验证重试上限为 1。SDK 网络自动重试为 0，单次请求超时最多 30 秒，
工具超时 5 秒；这些局部限制仍受全局次数和时间限制。原生工具和流式模型入口未开放。

当前单进程只允许一个新清单执行，并发新清单返回 `screening_busy`（429），没有持久队列。
进入执行循环后捕获的整体超时保存为 `state="timed_out"`；模型、输出或预算等失败保存为
`state="failed"`，通过 `error_code` 表达原因。已完成的条件及覆盖前缀保留，未完成判断补为
`information_insufficient / assessment_not_completed`，未解析和缺失项继续可见。
`state="completed"` 仅表示这次执行正常结束，仍可能有未知、冲突和未解析条款。

功能未开启、模型未配置、初始来源鉴权或并发准入失败会直接拒绝，不保证创建结果。
创建接口的 HTTP 201 也可能携带已保存的 failed/timed_out 结果，调用方必须检查 `state`。
来源撤权时不释放含私有内容的部分结果；最终保存或完整性检查失败也不能保证有可读取结果。
执行时间预算结束后仍需组装、持久化及鉴权，因此它不是 HTTP 必定在同一秒返回的硬截止。
反向代理超时应覆盖部署的 `MAX_SECONDS` 并为存储及返回留余量，不能直接沿用旧演示入口的
较短超时。进程崩溃后的持久续跑、取消和业务幂等任务属于 B05，不在 B04 中冒充实现。

## 保存读取与旧来源提示

`GET /api/screening/runs/{run_id}` 读取已保存结果，不重新抽取、核验或计费。
`GET /api/screening/runs?offset=0&limit=20` 分页列出当前用户仍有权读取的历史，包含
`candidate_batch` 和 `checklist` 两类；响应为 `items / total / offset / limit`。
该历史列表没有 `has_more` 字段，应按 `total` 继续分页，单页上限同为 100。
再次 POST 会创建新批次或新清单，可能发生新调用；不要用重发 POST 代替刷新已保存结果。

旧结果保存其输入闭包和提示/规则版本，不被当前画像、公告、材料或模型配置改写。
`source_state.current=false` 和 `stale_reasons` 提示画像/材料 head 改变、公告版本或观察
更新、附件集合改变、公共来源不新鲜等情况。候选批次还可提示公共目录改变或固定公共来源
不可用。stale 提醒用户这是旧依据，不会悄悄改成新结论；固定清单来源损坏会拒绝读取，
私有来源失权、到期或删除也必须拒绝，不能只贴 stale 标签后继续泄露。

`provenance` 保存 `code_sha / implementation_sha256 / lock_sha256 / prompt_version /
prompt_sha256 / rule_version / requested_model / actual_models / model_calls / input_tokens /
output_tokens / estimated_cost_cny / reserved_cost_cny / usage_complete /
model_identity_complete / offline / parameters`。请求模型名与实际响应模型名分开记录，
缺失运行依据不编造值，未知用量不写成已确认零费用。

## 私有 raw 输出、留存与备份

迁移 `007_qualification_screening.sql` 新增不可变结果元数据、正文和来源关联表，以及
可更新的隔离/清理生命周期表。每次生成追加新结果，不改写 B01 合同或旧结果。
SQLite 正文保存固定 manifest、result、provenance 和已收到的原始模型响应
`raw_model_outputs`，并用正文 SHA256 校验完整性。尚未收到的响应无法被保存，不能宣称
超时或断网时已经保留全部上游输出。API 的普通结果读取不返回 raw 输出，没有公共 raw 导出。

raw、引用和结果可能含企业私有原文，受同一所有者及全部来源当前权限限制。
任一依赖私有来源删除、到期或进入清理后，立即拒绝读取，并在启动、每小时维护或手动
`bidops-enterprise prune` 中删除派生结果正文，连同 raw、manifest 和 provenance 中的
私有内容一并清除，仅留必要元数据、哈希、关联及清理标记。即使源文件删除需重试，派生正文
也先清理。仅撤销授权会立即阻止读取，不等于已经物理删除全部历史副本。

数据库、宿主机管理员及备份管理员属于信任边界；本实现没有应用层加密。
SQLite 删除不保证擦除旧页，离线备份也不会被在线清理自动擦除，需依企业介质及备份
生命周期控制。不要把私有 raw 或备份放进公共公告导出、Git 或不受控日志。

企业备份需停止服务并取得相同 SingleWorker 文件锁。新备份为 v3，含数据库及其完整引用的
公告 raw、公告附件、私有材料 blob/文本；B04 私有结果与费用账本在数据库快照内。
恢复同时检查引用闭包和各文件哈希，拒绝缺失、多余、损坏和不安全路径。
默认恢复窗口 7 天，可设 1–30 天；过期包拒绝恢复。旧纯公共 v1 和 B02 无期限 v2 可兼容，
任何私有对象（含只有画像的数据库）都要求带期限的 v2 或 v3。

**每次恢复都执行隔离和费用核对屏障，包括没有旧费用表的公共旧备份。** 恢复先在临时目录
验证、撤销全部登录/OAuth state/旧可信身份快照、删除全部旧对象 ACL、隔离全部私有对象
及 B04 结果、清理已到期正文，再发布新目录。重新 SSO 不会恢复旧对象授权。
依据最新独立授权/删除记录，由管理员逐对象重新授权；无法核实时保持隔离。原结果所有者
随后可 POST `/api/screening/runs/{run_id}/reauthorize`，服务端重查全部固定来源和哈希后
仅恢复该结果可见性。此操作不授予来源权限、不重跑模型，也不改变 `unreviewed`；已清理的
正文不能通过此接口复活。

## 恢复后的费用核对标记

恢复返回 `budget_review_required=true` 和新生成的 `budget_restore_token`，每次恢复都会
轮换。旧备份里的标记、上次恢复的标记、缺失或错误标记不能通过当前屏障。
新旧模型入口检查同一屏障和共享 `agent_runs` 累计账本，配置名有区别：

| 入口 | 只在完成当前费用人工核对后配置的环境变量 |
| --- | --- |
| B04 资格核对 | `BIDOPS_SCREENING_LEDGER_RESTORE_TOKEN` |
| 原 Agent 演示入口 | `BIDOPS_DEMO_BUDGET_RESTORE_TOKEN` |

B04 还要求 `BIDOPS_SCREENING_LEDGER_VERIFIED`，默认 false。这些配置是运维确认，
不接受由业务请求体、模型或前端开启。恢复屏障拒绝时，B04 错误码为
`screening_budget_review_required`，旧入口为 `budget_review_required`。

**只有人工核对最新、完整的累计费用后，才能配置本次标记。** 核对必须覆盖供应商实际账单、
两个入口的已用及未知预留、备份之后到故障之间的调用；不能只检查恢复库中较早的账本。
如果区间用量丢失或仍不确定，继续关闭付费入口，先取得完整依据，不通过重置账本、调高
额度或填写标记绕过。标记只是确认屏障，不会补回丢失费用，也不会修改历史费用或释放预留。

模型每次真实请求前在事务中预留费用。已完成请求按估算已用计入，响应未知、用量缺失或
异常的请求保留保守预留；实际模型标识不确认时也停止继续运行。累计准入上限不超过配置的
10 元上限，依赖当前完整账本与保守估算，不能据此保证供应商实际账单的绝对硬上限。
离线 `FunctionModel/TestModel` 不发付费请求，也不能替代真实账单核对。

## 迁移、生产运行依据与非破坏回退

B04 依赖 B02 公告版本和 B03 公司/身份迁移，数据库迁移按文件名顺序执行。
以下是运维操作示例，不代表本文已在目标生产环境执行。先停止应用，保留受控、尚未到期的
完整升级前备份；输出目录必须未存在，命令中的路径由运维选择且不能位于公开目录。
`BIDOPS_DATA_DIR` 和 `BIDOPS_DATABASE_URL` 必须指向同一目标数据集。

```sh
uv sync --frozen
uv run --frozen --no-sync bidops-enterprise --env-file private/enterprise.env backup \
  --output /controlled/b04-before-upgrade --retention-days 7
uv run --frozen --no-sync bidops migrate
uv run --frozen --no-sync bidops-enterprise --env-file private/enterprise.env check-config
```

普通 `bidops migrate` 从进程环境读取数据库配置，不自动读取 `private/enterprise.env`；
执行前须由部署工具将目标数据目录/数据库配置注入该进程。`Store` 初始化也会应用尚未记录的
迁移，`schema_migrations` 应含 `007_qualification_screening.sql`。不要删除迁移记录来重跑
已应用迁移。`check-config` 校验企业本地配置，不验证 IdP、B04 真实调用、恢复标记或账单。

生产镜像通常没有 `.git`。应从已核对的提交构建，并传入真实代码 SHA：

```sh
docker build --build-arg BIDOPS_CODE_SHA="$(git rev-parse HEAD)" \
  -t bidops-enterprise:local .
```

当前 Dockerfile 使用 `uv sync --frozen --no-dev --no-editable`，保留 `/app/uv.lock`，
并设置 `BIDOPS_LOCK_PATH=/app/uv.lock`。其他打包方式也要保留实际 frozen 安装使用的锁文件，
设置正确的绝对 `BIDOPS_LOCK_PATH`，让结果保存真实 `lock_sha256`。`BIDOPS_CODE_SHA` 要与
镜像代码一致；未提交文件不能仅凭 HEAD 宣称对应提交。运行时另算 qualification 源文件的
`implementation_sha256`，但它和环境变量都不替代构建来源的核验。缺失代码 SHA 或锁文件时
记录可能为 null，不能填假值冒充完整依据。

需要停止本功能时，优先在当前 B04 版本设 `BIDOPS_SCREENING_ENABLED=false` 和
`BIDOPS_SCREENING_LIVE_CALLS_ENABLED=false` 后重启，保留 007 表、新结果和账本；
当前版本仍负责私有派生结果的到期清理。这是功能回退，不执行反向破坏性迁移。

若必须切回旧代码，先停写并用当前 B04 代码再做一份完整升级后备份，保存新增结果、raw、
来源闭包和最新费用依据；备份仍遵守到期和访问政策，不意味着无限期保留私有内容。
然后把升级前备份恢复到全新独立目录，按恢复隔离与人工对账流程核验后再切换。
不要覆盖当前数据集、DROP 007 表、把旧账本覆盖新账本，或把新结果零散复制到旧库。
旧 B03 代码不认识 B04 派生正文的清理关系，不能继续服务含这些正文的原数据库并声称留存
机制完整。保留下来的升级后完整包应由相容 B04 版本按恢复流程读取；若备份区间费用不完整，
回退实例的付费入口继续关闭。

恢复和手动清理的命令同样使用 frozen 环境；恢复目的目录必须尚不存在，清理针对进程环境
和企业配置实际指向的数据集，运行前仍须停止服务：

```sh
uv run --frozen --no-sync bidops-enterprise --env-file private/enterprise.env restore \
  --input /controlled/b04-before-upgrade --output /controlled/b04-restored-independent
uv run --frozen --no-sync bidops-enterprise --env-file private/enterprise.env prune
```

## 当前验收限制

- 离线固定条件草案与 `FunctionModel` 只证明协议、资源边界、版本、权限、五态归并和失败
  路径的工程行为，不是模型语义准确率或新数据泛化成绩。
- 真实企业材料许可、企业事实/材料人工核验与最终业务验收仍为 **BLOCKED**。
- 最新完整真实费用账本及供应商账单核对未完成，真实模型调用验收仍为 **BLOCKED**；
  没有通过标记配置补齐未知费用的捷径。
- 真实企业 IdP、HTTPS、撤销事件与目标部署运行验收仍为 **BLOCKED**，签名协议夹具不替代
  真正 Keycloak/企业账号验收。
- 新真实公告上的抽取、资格判断、证据支持率和人工耗时对照属于 B08，当前质量准入仍为
  **BLOCKED**。B05 持久任务/取消/恢复及 B06 人工审核也不能由本功能的保存读取代替。

相关边界见 [企业画像、材料与授权](COMPANY-ACCESS.md)、
[公告版本与证据](NOTICE-VERSIONS.md) 和 [分阶段实施计划](ENTERPRISE-PR-PLAN.md)。
