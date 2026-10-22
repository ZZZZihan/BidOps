# 持久资格核对任务运行与恢复

B05 将 B04 资格核对放入 DBOS 持久工作流，保存稳定任务 ID、固定输入版本、执行检查点、
取消状态和每次模型尝试的费用记录。它默认关闭，当前只支持单机、单进程、单 worker 的
受控工程试用。任务进入 `completed` 不表示供应商资质合格，也不表示完成了人工审核。

后端选择和框架恢复边界见 [ADR-0005](ADR-0005-DURABLE-RUNS.md)；条件抽取、五态判断、
双边证据和模型准入沿用 [资格核对说明](QUALIFICATION-SCREENING.md)。来源对象的权限、
版本与留存见 [企业画像与材料](COMPANY-ACCESS.md)，身份撤销边界见
[ADR-0004](ADR-0004-IDENTITY-REVOCATION.md)。这些机制共同生效，固定检查点不能替代当前授权。

## 安装与运行边界

主项目的 `pyproject.toml`、`uv.lock` 保持冻结。DBOS 依赖使用独立的
[requirements/durable.in](../requirements/durable.in) 和带下载哈希的
[requirements/durable.lock](../requirements/durable.lock) 加装到项目环境。在仓库根目录执行：

```bash
uv sync --frozen --python 3.12
uv pip install --python .venv/bin/python --require-hashes \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  -r requirements/durable.lock
```

随后运行应用、CLI 或检查时使用 `uv run --frozen --no-sync ...`。普通同步可能移除主锁之外
的 overlay；若重建或再次同步基础环境，应重新按上述带哈希命令加装。不要用只包含 overlay
的 `pip sync` 裁剪完整项目环境，也不要为安装 DBOS 重写冻结主锁。

默认配置如下，均不启用真实模型调用：

```dotenv
BIDOPS_JOBS_ENABLED=false
BIDOPS_JOBS_MAX_ACTIVE_JOBS=100
BIDOPS_SCREENING_ENABLED=false
BIDOPS_SCREENING_LIVE_CALLS_ENABLED=false
BIDOPS_SCREENING_LEDGER_VERIFIED=false
```

持久执行需要企业模式、资格核对和任务功能均已配置，且满足 B04 的模型与预算准入。
`BIDOPS_JOBS_MAX_ACTIVE_JOBS` 范围为 1–100，默认 100。该值限制包括排队、计算、等待审核、
中断和取消处理中任务在内的全局活动任务数量；容量已满返回 `429 job_capacity_exhausted`。
它也是原生父工作流的队列并发上限。等待审核的父任务可占用这些等待槽位，但计算子工作流
另有一个并发槽，**同时只进行一份资格计算**，不能据此推导出 100 路模型并发。

服务持有业务库旁的独占运行锁，`bidops-enterprise serve` 固定使用一个 Uvicorn worker。
DBOS 使用固定 executor `bidops-single-worker`。不要对同一业务库或状态目录启动第二个服务、
外部 DBOS worker 或多个容器副本；也不要通过删除锁文件绕过独占检查。当前 SQLite 方案没有
验证多主机、HA 或 PostgreSQL 部署能力，相关扩容需要独立设计与恢复验证。

按已有企业配置启动或检查：

```bash
uv run --frozen --no-sync bidops-enterprise --env-file private/enterprise.env check-config
uv run --frozen --no-sync bidops-enterprise --env-file private/enterprise.env serve \
  --host 127.0.0.1 --port 8765
```

`--env-file` 是顶层参数，放在子命令前；已有进程环境变量优先于文件。`check-config` 通过只说明
企业设置可解析，不证明真实 IdP、模型连接、账单、任务恢复或资质效果已经验收。

## API、身份与状态

任务路由仅位于 `/api/jobs`，不提供省略 `/api` 的别名。所有路由都要求企业身份；服务器从
已验证会话确定主体，不接收请求体或自报请求头中的 owner、角色或审批人。读取要求
`catalog:read`，创建和状态操作要求 `agent:use`。当前主体的停用、身份可信期限、全部来源
权限和留存每次重新检查；owner 身份本身不能绕过来源撤权，其他管理员也不能直接读取他人任务。

| 接口 | 当前行为 |
| --- | --- |
| `GET /api/jobs/status` | 返回 `enabled`、`configured`、`backend: "dbos"`、`live_calls_enabled` 等状态。默认关闭时不启动模型。 |
| `POST /api/jobs/runs` | 必需 `Idempotency-Key`，接收 B04 `ChecklistRequest`，返回 HTTP 202 和稳定任务概要。 |
| `GET /api/jobs/runs?offset=0&limit=20` | 当前主体可读历史分页，返回 `items`、`total`、`offset`、`limit`；`limit` 为 1–100。按当前全部来源权限过滤后分页。 |
| `GET /api/jobs/runs/{run_id}` | 返回固定任务、`manifest`、版本依据、用量和完整 `result` 或 null；不重新计算。 |
| `POST /api/jobs/runs/{run_id}/cancel` | 先持久记录取消意图，再取消原生工作流及子工作流。 |
| `POST /api/jobs/runs/{run_id}/resume` | 仅中断且服务端允许的任务可显式恢复；重新检查当前权限、输入、代码、设置、预算和原生检查点。 |
| `POST /api/jobs/runs/{run_id}/reauthorize` | 恢复后 owner 对隔离任务显式再授权；仅处理指定 job 与其校验通过的结果的可见性。 |

创建请求包含 `profile_revision`（`object_id`、`version_id`、`sha256`）、`project_id`、
`target_package_ids`、`additional_material_revisions` 和可选带时区 `as_of`，与 B04 清单请求相同。
包和附加材料列表各最多 100 项。输入固定到实际版本及哈希；请求中没有“默认满足”、跳过审核
或客户端指定结果字段。202 只确认任务被接收。

同一主体下，幂等键为 1–128 个字母、数字、下划线或连字符。同键同输入返回同一任务，
重试时 `created=false`；同键不同输入返回 `409 job_idempotency_conflict`。未显式传入的
`as_of` 在首次创建时固定，重试不会刷新时间。响应丢失时保留原键和原请求重试或查询历史，
不要为不确定的同一次提交换新键。不同主体的同名键互相隔离。

所有 POST 需要现有企业会话的 CSRF token 和合法 Origin。状态操作请求体为
`{"expected_state_version": 当前整数版本}`；过期版本返回 `409 job_state_conflict`，前端应刷新
并让用户重新决定，不能盲目循环提交。任务业务状态版本用于并发状态修改；费用回执可能在
同一状态版本内更新，客户端也要按详情请求顺序防止旧轮询覆盖新用量。

| 状态 | 中文含义与操作边界 |
| --- | --- |
| `queued` | 已接收，等待计算。 |
| `running` | 正在执行固定输入的计算。 |
| `waiting_review` | 计算结果已保存，等待后续 B06 人审接入；页面不提供跳过审核或批准按钮。 |
| `cancel_requested` | 取消意图已持久化，尚待原生后端确认；后续执行检查会拒绝继续。 |
| `cancelled` | 取消终态，不能恢复或复活。 |
| `failed` | 执行失败，可能保留部分清单、原始输出和费用。 |
| `timed_out` | 执行超时，不能把已产生的费用或部分输出当作不存在。 |
| `interrupted` | 因权限、恢复隔离解除或可恢复中断而暂停；只有 `allowed_actions` 包含 `resume` 时才可恢复。 |
| `completed` | 工作流已完成；结果的人审状态仍需独立判断，不代表资格通过。 |

`allowed_actions` 由后端当前条件产生。前端不能仅凭状态文字自行开放操作。取消时若 DBOS
暂时不可用，接口可返回 503，但业务状态已是 `cancel_requested`；读取仍会给出 `cancel` 操作，
可按最新 `state_version` 显式重试。取消并不承诺撤回供应商已接收的请求或退款。

缺少模型配置或实时预算准入时，保持任务和资格核对功能开启的服务仍可作为历史读取器启动，
不会注入替代模型或启动执行 worker。已授权、未过期的历史可读，`configured=false`、
`allowed_actions=[]`，需要 worker 的创建、取消或恢复返回 503。若关闭任务功能本身，执行
和历史接口均不作为可用任务服务；不能把关闭模型与关闭整个功能混为一谈。

界面遇到 401、403 或 404 会清空任务私有内容、固定输入、引用和历史缓存。读取、刷新和
页面重载只读取保存的 run；不会增加模型调用。保存在页面中的旧结果也不构成继续访问的授权。

## 固定输入、检查点与版本升级

业务库保存任务身份、输入 manifest、设置快照、唯一结果引用、费用尝试和权限状态；DBOS
状态库保存父/子工作流及步骤输出。业务 `run_id` 与结果 `result_id` 不同，结果修订在任务
创建时固定。恢复会校验结果与任务的 manifest、版本、哈希和关系，不能给旧任务接入另一份结果。

运行版本绑定后端代码、主锁和 durable overlay 哈希；影响计算的设置也单独固定。当前代码或
依赖版本不匹配时拒绝执行，返回 `job_application_version_changed`；固定设置改变时返回
`job_settings_changed`。来源版本、生命周期或其他执行依据变化会触发 `job_inputs_changed`。
公告的新旧来源另通过 `source_state` 标记；单独的 `public_sources_stale` 可保留为旧来源提示，
不能把读取旧结果描述为重新核对了最新公告。

升级、改画像、材料或计算设置后，保留旧任务与结果，使用新输入修订和新的幂等键另建任务。
不要改写旧 manifest、把新代码冒充旧 `application_version`，或重置旧结果修订。
已完成且仍有权限、未过期的历史可以保留读取。若原生检查点已成为无法恢复的 SUCCESS/ERROR
终态，接口返回 `job_checkpoint_not_resumable`；另建任务，不把无效的 SDK resume 当作恢复成功。

执行阶段沿用 B04 的两阶段 Pydantic AI 抽取/核验、请求次数、段长、输入尺寸、输出和时限约束。
恢复检查点会复用已保存步骤；尚未有可信回执的步骤仍受剩余次数和预算限制。部分失败不能清空
已保存证据、unknown 用量或费用，也不能把“未核验”“需补材料”改成满足条件。

## 故障窗口与费用不确定性

DBOS 已持久化的模型步骤可回放；业务侧还持久保存每次尝试与模型响应回执，避免只因框架
检查点稍晚写入就重复发送已确认的请求。回执与请求指纹绑定，覆盖工作流/步骤、输入、工具
参数、模型与设置。结果提交使用固定身份并验证相同内容，不在恢复时发布第二份业务结果。

当前工程故障注入位置如下。它们属于进程内测试能力，没有公开 HTTP 故障注入接口：

| 窗口 | 中断时已知什么 | 恢复边界 |
| --- | --- | --- |
| `before_model` | 预留和尝试已记录，尚未进入该次模型调用。 | 重启后不能仅凭缺少回执推定供应商费用为零；旧尝试保留不确定性。 |
| `response_before_receipt` | 模型已返回，业务响应回执尚未提交。 | 可能需要再次请求，可能重复计费；旧预留继续占用预算。 |
| `receipt_before_accounting` | 响应回执已保存，费用归集尚未完成。 | 复用回执并补齐归集，不因缺少框架检查点直接重发。 |
| `accounting_before_checkpoint` | 业务回执及费用已保存，框架步骤检查点尚未完成。 | 按同一指纹回放已知响应，保留原费用。 |
| `before_result_commit` | 计算已完成，唯一业务结果尚未提交。 | 复用完成步骤，重新进行权限和结果提交校验。 |
| `after_result_commit` | 唯一业务结果已持久化，父工作流尚未完成。 | 校验并复用原结果与修订，不生成第二份结果。 |

`model_calls` 统计已记录尝试，包含未确认的请求，不能直接当成供应商成功调用数。
`confirmed_calls`、`unknown_calls`、`estimated_cost_cny`、`reserved_cost_cny`、
`orphan_reservations`、`usage_complete` 应分别查看。没有回执的 started 尝试在恢复时转为
unknown，原预留不释放；若允许再次尝试，会产生新的预留，并可能再次收费。孤立账本预留
也不能因找不到对应尝试而归零。unknown 尝试仍计入请求次数上限，未结算预留继续占用共享预算。

缺失或无效的 token 用量、未结算预留等会使 `usage_complete=false`；模型标识是否完整另由
`model_identity_complete` 表示。任何这些缺口都不能解释为“实际免费”。
estimated 和 reserved 是系统保守核算，不是供应商账单硬上限；不承诺外部请求、供应商计费或
任何宕机窗口的恰好一次。取消、超时和撤权也无法收回已经在途的外部请求。不要通过删尝试、
清账本、释放 unknown 预留或换幂等键掩盖重复计费风险。

## 停止 worker 后生成 v4 备份

业务库、DBOS 状态库和私有文件没有跨库天然原子快照。先用当前部署的进程管理方式停止整个
企业服务及 worker，确认没有其他写入者，再运行备份。备份 CLI 获取同一独占运行锁，活跃
服务持锁时会拒绝。不要在线分别复制两个 SQLite 文件，也不要只备份业务任务表。

以下 `/controlled/...` 是运维选择的受控路径，目标目录必须尚不存在：

```bash
uv run --frozen --no-sync bidops-enterprise --env-file private/enterprise.env backup \
  --output /controlled/b05-stopped-backup --retention-days 7
```

期限默认 7 天，允许 1–30 天。存在 DBOS 状态库时输出 v4 manifest；无状态库、无持久任务的
备份仍可输出 v3。业务库只要存在持久任务，就必须有成对的状态库，缺失时拒绝备份或恢复。
备份包括公告 raw、附件、企业材料 blob/text、业务结果和原始输出、费用账本以及 DBOS 私有
状态闭包。v4 将两库绑定到同一快照标识与映射摘要，核对父/子工作流、任务、输入、结果、
尝试和费用引用，拒绝缺件、额外引用或错配的双库组合。

DBOS 序列化输出可能包含画像、材料、提示词、工具结果和模型原文。状态库、WAL、日志、
临时文件和备份都按私有数据保护；当前应用使用受限目录及文件权限，不允许放入 Web 静态
目录或代码提交。备份 hash 和快照绑定用于完整性检查，不是来源真实性签名。只接收可信、
受控来源的备份；不能把任意用户上传的 DBOS 序列化数据库当成安全输入。

manifest 到期后恢复会拒绝。它不会自动删除运维已复制到其他位置的离线备份，副本期限和
销毁仍需由实际存储流程落实。

## 恢复隔离、费用核对与显式继续

恢复到新的独立目录，不覆盖运行目录：

```bash
uv run --frozen --no-sync bidops-enterprise --env-file private/enterprise.env restore \
  --input /controlled/b05-stopped-backup --output /controlled/b05-restored-independent
```

恢复先验证期限、文件 hash、文件闭包和双库绑定，再复制到 staging 目录复查实际字节，完成
安全变换后才发布目标目录。旧公共备份 v1、无私有对象的旧 v2 以及带正确期限的旧私有 v2/v3
沿用兼容规则；任何带持久任务的旧格式因没有配对 v4 状态库而拒绝。只有画像、没有文件的
私有备份也必须带有效期限，不能以“没有私有 blob”为由绕过期限。

恢复时分别处理以下屏障。完整费用核对是付费执行的前提；历史读取和再授权本身不会启动模型：

1. **切换独立数据配置并保持外部调用关闭。** 恢复目录包含 `bidops.sqlite3`、raw、附件与
   private 内容。核对 `BIDOPS_DATA_DIR` 和 `BIDOPS_DATABASE_URL` 同时指向这份独立副本，
   不要把恢复业务库与原 DBOS 目录混用。所有进程仍遵守单 worker 约束。
2. **重新建立当前身份与来源授权。** 旧登录、OAuth 状态和身份快照失效，必须重新 SSO；
   本地 disabled 主体仍不能登录。恢复会移除旧私有 grants，并隔离企业对象、资格结果和
   持久任务；原生父/子工作流被取消，不会启动后自动执行。管理员需按最新的独立授权、撤权
   和删除记录，显式恢复所需来源对象及 owner 的权限。备份里的旧授权不能作当前批准依据。
3. **先核对最新完整费用。** 恢复返回 `budget_review_required` 及新的 `budget_restore_token`，
   每次恢复轮换，不沿用旧 token。人工核对供应商账单、恢复点之后的丢失区间、共享账本、
   unknown 尝试和孤立预留；无法覆盖的费用区间保持付费准入关闭。
4. **owner 显式重新授权指定任务。** 当前 owner 身份和全部来源权限满足后，用最新 CAS
   版本调用 `POST /api/jobs/runs/{run_id}/reauthorize`。同一事务验证原结果的 manifest、hash、
   revision 及任务关系，并同时解除该 job 与其指定结果的隔离；验证失败全部回滚。
   不要求另发一条结果再授权请求，也不会批量放开其他结果或来源。
5. **再决定是否恢复执行。** 原 `queued`、`running`、`waiting_review` 在再授权后变为
   `interrupted`，错误码为 `job_restored_needs_resume`；原 `cancel_requested` 变为 `cancelled`，
   终态保持终态。再授权只恢复可见性，不调用模型、不恢复原生工作流、不签署人审。只有当前
   `allowed_actions` 允许时，owner 才能按新 CAS 版本显式请求 `resume`。

预算屏障的环境变量沿用现有名称：

| 消费入口 | 恢复核对 token 配置 |
| --- | --- |
| B04 资格核对与 B05 持久任务 | `BIDOPS_SCREENING_LEDGER_RESTORE_TOKEN` |
| 原 Agent 演示入口 | `BIDOPS_DEMO_BUDGET_RESTORE_TOKEN` |

它们对应同一恢复库当前的屏障 token；不是分别生成两个 token，也没有新增
`BIDOPS_JOBS_*` 费用 token。B04/B05 仍要求 `BIDOPS_SCREENING_LEDGER_VERIFIED` 等原有准入，
默认不通过。**仅在人工核对最新完整费用后，才在受控私有运行配置中填入当前 token。**
token 是核对确认，不会补回丢失费用、重建供应商账单、清零账本或自动释放预留。不要把 token
写入仓库、共享报告或公开日志，也不要用其绕过缺失真实账本的阻塞。

再授权不能复活已经到期或删除的正文。恢复期间清理失败会中止恢复，不发布一个部分放开的
运行目录。恢复读取仍显示旧输入、原提示版本和来源 stale 信息，不会把旧结果标成最新核对。

## 私有留存、迁移与非破坏回退

[008_durable_jobs.sql](../backend/bidops/migrations/008_durable_jobs.sql) 在 B04 的 007 后新增
持久任务、固定输入、尝试及工程 review fixture 等表；正常 Store 初始化按迁移顺序应用。
升级前先生成并保留已停止服务的完整备份。保留 007 资格结果、008 任务与全部费用依据，
不做降级删表迁移。

留存闭包覆盖业务私有正文和 DBOS 的父/子工作流、步骤缓存、工具输出及提示原文。来源到期、
删除或撤权立即阻止读取与继续执行；维护流程清除因到期或删除而应移除的持久正文，撤权本身
不替代对象留存策略。启动、周期维护和以下离线命令执行留存清理；即使任务功能关闭，也不能
跳过既有任务的私有历史清理：

```bash
uv run --frozen --no-sync bidops-enterprise --env-file private/enterprise.env prune
```

该命令也使用独占运行锁，应停止服务后运行。清理保留费用元数据和必要 tombstone，不把历史
收费归零；原生清理失败保留失败状态供后续重试，不能因此恢复私有访问。

优先的功能回退是在当前版本关闭 `BIDOPS_JOBS_ENABLED` 并保持外部调用关闭，保留数据库、
私有状态、账本和当前清理能力。若必须回退旧代码，应先停止服务并保存包含升级后新增结果的
完整受控备份，再将升级前备份恢复到另一独立目录运行；升级后数据原样保留，不将单个表或
单个 DBOS 文件拼回旧环境。旧 B04 代码没有 B05 私有状态清理能力，不应直接接管现有 B05
状态目录。旧备份恢复后的真实费用区间也必须重新核对，不能因回退而重置预算。

## 打包依据与当前证明范围

Docker 镜像先装冻结基础依赖，再安装带哈希 overlay，保留两份锁文件。用已审阅的干净提交
构建时传入实际代码 SHA：

```bash
docker build --build-arg BIDOPS_CODE_SHA="$(git rev-parse HEAD)" \
  -t bidops-enterprise:local .
```

当前镜像设置 `BIDOPS_LOCK_PATH=/app/uv.lock` 和
`BIDOPS_DURABLE_LOCK_PATH=/app/requirements/durable.lock`。其他打包方式同样应保留实际安装
使用的锁字节，设置可读取的正确绝对路径，并提供真实 `BIDOPS_CODE_SHA`。HEAD 不包含未提交
修改，不能把有 WIP 的构建描述为该提交的精确产物。运行版本还绑定实际后端源文件和依赖锁，
升级时不能手工改成旧任务版本来绕过恢复检查。

B05 当前的实际 HTTP、浏览器、SQLite/DBOS 和进程恢复证明使用离线合成材料、签名 OIDC
协议夹具及显式注入的 `FunctionModel`/`TestModel`。这些证明验证工程路径、权限、持久化和
故障边界，不证明真实模型质量、真实供应商账单、生产 IdP/HTTPS 或真实资质材料已验收。
具体命令、提交、测试数量和故障证据以对应版本的独立报告为准，本说明不把开发中测试次数
当作冻结后的最终验收数字。

`review_fixture` 只供进程内工程测试，不暴露 HTTP 路由、模型工具或生产人审按钮。合成 confirm
事件不产生真实人工批准，保存的 B04 清单仍是 `human_review_state: unreviewed`。B06 的审核人
身份、签署、事件和正式继续流程尚待接入。真实资质材料、真实 IdP、完整实时费用账本及质量
评测仍是 BLOCKED；保持这些边界可见，不以页面可用或工程测试通过替代它们。
