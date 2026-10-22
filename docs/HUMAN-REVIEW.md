# 逐项人工复核、补材料与持久继续

B06 在固定候选批次和等待审核的资格核对任务上建立复核案件，保存有身份、时间、理由、
结果版本及证据依据的人工事件。人工决定与 Agent 原判断分别保存；原结果中的
`human_review_state` 仍为 `unreviewed`，不会因为人工确认或案件关闭而改写原始模型输出。
关闭表示内部是否继续跟进的复核结束，不是正式资质认定，也没有外部投标、采购提交或付款动作。

当前证明限于离线工程样例。实际浏览器走生产 HTTP、SQLite、对象 ACL、DBOS 和 Pydantic AI
接线，但外部 OIDC 传输和 UNIT 模型回答由夹具提供，不能当作真实 IdP、真实材料、人工业务
验收或生产准入通过。B02、B08、B10 的未完成门槛见本文末尾。

相关运行约束沿用 [持久任务说明](DURABLE-RUNS.md) 和
[ADR-0005](ADR-0005-DURABLE-RUNS.md)；五态判断和双边证据见
[资格核对说明](QUALIFICATION-SCREENING.md)，身份撤销见
[ADR-0004](ADR-0004-IDENTITY-REVOCATION.md)，对象授权见
[企业画像与材料](COMPANY-ACCESS.md)。

## 默认关闭与当前身份

唯一新增的功能开关为：

```dotenv
BIDOPS_REVIEW_ENABLED=false
```

在已配置的企业部署中，需要使用复核功能时，由运维在受控私有运行配置中明确开启此开关，
再启动服务；这不会开启真实模型调用或绕过 B04/B05 的预算、模型准入。没有企业身份配置时，
复核 API 返回 404。企业身份有效但功能关闭时，状态入口仍可读，其余复核入口拒绝操作。
页面仅对企业已登录身份显示“人工复核”；关闭状态不会继续读取私有案件或提交计算。

`GET /api/reviews/status` 返回 `enabled`、`configured`、`worker_available`。当前实现的
`configured` 随复核开关表示本地复核服务已接入，不证明外部 IdP 或模型已验收。
`worker_available=false` 时，按权限读取已有案件、保存人工决定和关闭案件仍可用；已经就绪的
新结果也可显式接入。创建补材料计算必须另有可用的 Jobs worker。保存人工事件后若 worker
不能消费，页面会保留配送待处理提示，不能据此宣称关联任务已经完成。

提交人工决定必须同时满足三项条件：

1. 当前登录具有显式 `reviewer` 角色，即 `catalog:read` 与 `review:decide` 权限。
   需要兼任 owner 管理时可同时具有 `analyst`。`admin` 本身没有 `review:decide`。
2. 当前主体由案件 owner 明确列入 assignment。owner 若要亲自逐项决定，也必须满足此条件；
   创建案件不会默认把 owner 变成审核人。
3. 当前主体可读取案件的全部私有来源闭包，包括固定候选批次、所有历史修订和已关联补材料
   任务所需的画像及材料。只获某个新材料或当前页的权限不够，assignment 也不授予材料 ACL。

owner 创建、调整 assignment、创建 successor、接入修订、关闭和再授权案件还要求
`agent:use`；非 owner 即使是管理员，也不能代替该 owner 管理案件。列表先按当前权限过滤，
再分页；未授权详情不释放正文。当前身份、角色快照、本地停用状态和对象授权都由服务端重查。

真实使用沿用 Authlib 的 OIDC 登录：浏览器访问 `/api/auth/login`，经已配置 IdP 的回调建立
会话，`/api/auth/me` 提供当前主体编号和 CSRF token。不要把测试签名、客户端填写的
`actor_id`、模型工具参数或一个已知主体 hash 当成人工登录凭据。人工边界要求服务端当前登录，
普通 `ToolContext` 不能替代它。可信身份快照到期后需要重新 SSO；离线协议模拟没有验收真实
IdP 的停用、角色变更传播或 HTTPS Cookie 行为。

所有写接口要求同一浏览器会话、与配置公开 origin 完全相符的 `Origin` 和
`X-CSRF-Token`。请求体不接受 actor、角色或认证来源字段，额外字段拒绝。
人工事件的身份、时间和 `server_authenticated` 来源由服务端填入，模型不能给自己签署人审。
B05 的 `review_fixture` 仍只是工程函数，不暴露 HTTP 或模型工具入口；已有 B06 案件的任务
拒绝以它配送测试决定。测试事件不能替代本节要求的登录、assignment 和来源授权。

## 页面操作顺序

1. **准备固定输入。** 在“企业画像与材料”保存画像和可引用的证明材料；不能把自报、缺失或
   冲突材料标成已证明。按最新权限分别给审核人授予全部所需来源的读取权限，记录
   `/api/auth/me` 返回的 `user.subject_id` 用于 assignment。不要用显示姓名代替主体编号。
2. **取得候选与等待任务。** 在“候选与资格核对”保存固定候选批次，保留其 `batch_id`；
   在“持久核对任务”创建使用固定 `VersionRef` 的任务，等到 `waiting_review`。
   原始候选不是资格通过，任务计算结束也不是人工批准。候选批次保存完整候选、排除及待核实
   行；读取后续分页不重新筛选或调用模型。
3. **由 owner 建案。** 打开“人工复核”，填入等待任务编号、固定候选批次编号和明确的审核人
   主体编号，再点击“创建复核案件”。服务端校验 owner、任务状态、结果和批次绑定，返回
   完整案件；页面只在收到成功应答后显示保存成功。assignment 本身不会执行材料授权。
4. **逐项核对。** 展开公告要求和企业证明，核对对象、固定版本、原文及文本 SHA-256、
   字符区间、逐字引用、适用包、有效期、否定或例外。原判断完整保留“满足／不满足／信息不足／
   证据冲突／不适用”五态及缺失项。每条人工决定初始为空，分别选择确认原判断、驳回原判断
   或要求补材料，并填写该条独立理由；没有默认批准和全选批准按钮。
5. **处理并发和更正。** 两个审核人基于同一 `review_version` 提交时，过期写入返回 409。
   页面保留理由草稿并要求显式刷新，不自动以新版本重试。重新核对最新事件后才能再次提交。
   修改已有决定时使用“追加更正该条理由与决定”，写新的理由和事件；原理由、原审核人及原
   时间保留，更正须指向该条最新事件，不能覆盖历史行。
6. **补材料后另建计算。** 对需要补材料的条目先保存 `request_materials` 事件，再在企业材料
   页面实际上传新材料或更新画像。回到案件选择对应触发事件、已保存的新画像版本及新增证明
   版本，填写项目、采购包和重算理由，创建关联新任务。请求用实际对象／版本／hash，不上传
   自填“已满足”结果。新 job 和案件 successor 链接在同一业务事务中建立，不改旧 manifest。
7. **显式接入新结果。** 等关联任务计算就绪，填其编号和接入理由，再点击“显式接入已完成的
   新修订”。新任务执行完整资格核对，接入后所有新条目重新未审，旧批准不自动继承。历史
   修订仍可选择，展示当时原判断、证据与事件；不能在历史修订写入新决定。若 worker 暂停，
   先恢复既有运行条件再等其就绪，不能用旧结果冒充重算完成。
8. **明确关闭与导出。** 当前来源仍有效、全部条目的最新人工决定均为有效确认或驳回、没有
   待补材料项时，owner 填关闭理由并明确关闭。随后按需要导出受控 JSON 审核包。关注案件
   页面配送提示和“持久核对任务”的实际状态；关闭应答不代替 DBOS 消费和原生完成检查。

“读取已有复核”支持案件编号和分页历史。显式刷新会重查已打开案件及新计算已选来源；
列表返回空的 200 不会被当成已打开案件仍有权限的证明。401／403／404 拒绝或功能被关闭
时，页面清除已加载案件、证据、材料选择及理由草稿，并使并发旧响应失效。409 保留草稿。
页面不把私有正文或理由写入 `localStorage`。跨刷新、退出或关闭页面后不要依赖未提交草稿。

## API、版本和幂等

复核接口只挂在 `/api/reviews`，没有另一个无 `/api` 的复核别名。以下路径均相对于该前缀：

| 方法与路径 | 行为与关键请求字段 |
| --- | --- |
| `GET /status` | 当前功能与 worker 状态 |
| `GET /cases?offset=0&limit=20` | 当前可见案件分页；offset 非负，limit 1–100，返回 items/total/offset/limit |
| `POST /cases` | 201；`run_id,candidate_batch_id,idempotency_key,reviewer_ids` |
| `GET /cases/{case_id}` | 固定案件完整投影，不重跑模型 |
| `POST /cases/{case_id}/assignments` | `expected_version,reviewer_ids,idempotency_key,reason` |
| `POST /cases/{case_id}/decisions` | 逐项命令，结构见下文 |
| `POST /cases/{case_id}/successors` | 202；`expected_version,revision_id,trigger_event_id,request,idempotency_key,reason` |
| `POST /cases/{case_id}/revisions` | 接入已关联且就绪的新任务；`run_id,expected_version,idempotency_key,reason` |
| `POST /cases/{case_id}/close` | `expected_version,idempotency_key,reason` |
| `POST /cases/{case_id}/reauthorize` | 恢复后 owner 明确再授权；`expected_version,idempotency_key,reason` |
| `GET /cases/{case_id}/export` | 完整 JSON 审核包，每次重查全部来源权限 |

除 create 为 201、successor 为 202 外，写入成功返回 200；写响应均是完整案件。
`expected_version` 是非负整数，必须取该案件最新 `review_version`；不是原生任务的
`state_version`。逐项目标由 `revision_id + assessment_id + condition_id` 共同确定，不能仅按
条件编号跨采购包写入。命令还绑定固定 `expected_result_revision`。

逐项请求结构如下，尖括号说明均应替换成实际服务端返回值；不是可用的夹具身份或默认凭据：

```json
{
  "revision_id": "<当前案件修订>",
  "assessment_id": "<该采购包 assessment>",
  "condition_id": "<该条条件>",
  "expected_version": 1,
  "command": {
    "decision": "request_materials",
    "expected_result_revision": "<固定结果修订>",
    "reason": "<本条具体待补材料及理由>",
    "idempotency_key": "<本次明确操作的唯一键>"
  },
  "supersedes_event_id": null
}
```

更正时 `supersedes_event_id` 必须是该条当前最新事件；初次提交为 null。
同一操作重试保持相同幂等键和完整请求，服务端按已存事件去重；同键不同内容拒绝。
页面对未确认的网络应答不显示成功，在当前页面内重试同一操作保留原键。刷新后先读案件
确认是否已保存，不因为没有看到成功提示就重复签署。人工事件与 outbox 同事务提交；这不
构成对外部投递或模型收费“恰好一次”的承诺。

successor 的 `request` 沿用 `ChecklistRequest`：`profile_revision`、`project_id`、
`target_package_ids`、`additional_material_revisions`，以及可选的带时区 `as_of`。
每份版本引用含 `object_id,version_id,sha256`。`allowed_actions` 是服务端当前能力，页面按
`assign/decide/successor/attach/close/export/reauthorize` 显示或禁用操作；客户端不能以修改
按钮状态获得权限。

## 配送、重启与来源失效

人工事件先持久保存，然后通过 outbox 向现有 DBOS workflow 配送事件 ID，未增加另一套任务
执行引擎。每次人工写入后立即尝试配送；开启复核的企业 API 进程另有每 30 秒的重试循环。
worker 缺失时事件仍留在业务库；同版本重启并恢复当前身份、权限和 worker 条件后，待处理
事件可继续由重试循环配送。不是每 30 秒必然消费成功，也不保证固定完成时限。

补材料后，新 job 与 successor 关联成功的同一事务会冻结旧 run 尚未消费的决定事件，
保留它们的正文、身份和原始回执；旧决定不会被反复投递或计为新修订的待处理事项。
用于取消旧 run 的 `superseded` 通知仍可消费。其他暂时无法投递的事件按已尝试次数轮转，
防止早期永久失败占满每轮额度，使后续有效事件有机会继续。

详情 `delivery.pending_count` 计数的是尚未 `consumed_at` 的非冻结事件；`delivered_at` 只说明
发送已经确认，不能替代消费。`last_error_code` 和 `frozen_count` 保留失败或冻结信息。
worker 消费前重查事件、case、epoch、revision、审核人当前有界主体快照、assignment、角色及
全部来源权限；登录时有权不代表无限期可消费。身份过期需重新 SSO，撤权或停用不通过重启
绕过。重试不会重新授予权限，恢复隔离冻结的旧事件也不会自动重发。

普通逐项决定不把任务改为 completed。有效 `close` 事件消费后才把关联业务任务标为
`completed`，原生 workflow 随后持久完成；若消费前后进程退出，按检查点及已存消费记录恢复。
因此案件 `status=closed`、事件 `delivered_at`、`consumed_at` 和 Jobs／原生完成是不同观察点。
页面显示“人工事件已保存，仍待消费”时，应刷新确认，不重复创建关闭事件冒充完成。

画像、材料、公告、附件版本或必要来源观察发生变化时，`source_state.current=false` 并列出
`stale_reasons`。`latest_decision` 是历史最新动作，不等于当前有效批准；还须看 `effective`
和 `ineffective_reasons`。来源变化、新修订、待接入重算和恢复 epoch 都可能令决定失效。
历史原判断与证据保持固定，但不能继续把它展示为当前有效；确认最新依据需新计算和重新审核。
已终止或来源不完整的项目不能因过去有确认事件而自动变为可投。

## 完整导出与私有留存

导出为 `bidops_review_v1` JSON，包含完整固定候选与排除理由、所有修订的 manifest、原始五态
判断、双方引用及版本、运行依据、全部人工事件和 successor 链。顶层
`agent_judgments_preserved=true`、`automatic_bidding=false`，`current` 明示当前来源状态。
它不是一张只保留“通过”行的结果表，也不是正式供应商资格证明。

服务端生成导出前后重查权限，历史来源被撤权、到期、删除或缺失时，不能跳过无权部分导出
剩余“完整包”。JSON 上限为 10,000,000 字节，超出返回 413，不静默截断。响应带
`Cache-Control: no-store`；下载得到的本地文件仍含私有材料和理由，应置于受控位置并按期限
处理。HTTP 缓存头不会追踪或销毁用户已下载的副本。

复核私有正文纳入来源留存闭包。任一所需私有来源到期、被删除或正文已清理时，案件拒绝读取；
维护清理删除可清除的事件理由正文、移除 assignment 并冻结 outbox，保留不可变的审计元数据、
hash、版本关联和必要 tombstone。仅撤销某个人的 ACL 会立即阻止其读取／消费，不等于删除
所有人的历史记录。已清理正文不能通过恢复、重登或案件再授权复活。

## 009 迁移、备份和恢复后的新审核 epoch

[009_human_review.sql](../backend/bidops/migrations/009_human_review.sql) 在 008 持久任务迁移
之后新增 cases、可变状态、修订、assignment、事件及正文、outbox、来源与 successor 表。
历史事件、修订和关联由数据库触发器保护，业务更正使用追加事件。正常 `Store` 初始化按迁移
文件名顺序应用；也可用原有 `bidops migrate`，不要删除 `schema_migrations` 记录强制重跑。

升级前停止企业 API 和 worker，并保存完整备份。继续沿用 frozen 基础环境及带哈希 DBOS
overlay，安装细节见 [持久任务说明](DURABLE-RUNS.md#安装与运行边界)。下列是运维操作步骤，
不是本文已经在目标生产环境执行的记录；`/controlled/...` 是运维选择的受控新目录，必须尚未存在：

```bash
uv run --frozen --no-sync bidops-enterprise --env-file private/enterprise.env backup \
  --output /controlled/b06-before-upgrade --retention-days 7
uv run --frozen --no-sync bidops migrate
uv run --frozen --no-sync bidops-enterprise --env-file private/enterprise.env check-config
```

`bidops migrate` 读取进程环境，不自动加载 `private/enterprise.env`，执行前应由部署工具注入
目标 `BIDOPS_DATA_DIR` 和 `BIDOPS_DATABASE_URL`，并确认两者指向同一数据集。
迁移后 `schema_migrations` 应有 `009_human_review.sql`；配置解析通过不代表 IdP 或业务验收。
不要用会同步删掉 overlay 的普通 `uv run` 运行这些命令。

包含持久任务的备份继续使用 v4，成对保留业务库、DBOS 状态库、公开 raw 与附件、私有材料、
派生结果、账本和复核闭包。复核 inventory 校验 case、candidate、修订／结果、事件正文与
hash、source、successor、outbox 和任务引用，不能只复制新表或漏掉旧历史依据。备份要求停止
服务并取得同一独占锁；默认有效期 7 天，可设 1–30 天。没有 DBOS 状态库且没有持久任务时
才可能输出 v3，不能把含持久任务的库拆成无配对状态库的旧格式恢复。

恢复到独立目录，不覆盖运行目录：

```bash
uv run --frozen --no-sync bidops-enterprise --env-file private/enterprise.env restore \
  --input /controlled/b06-before-upgrade --output /controlled/b06-restored-independent
```

除 B03–B05 的登录撤销、全部私有 grants 移除、对象／结果／任务隔离、原生任务取消和费用屏障
外，B06 还会对每个案件设置新的 `review_epoch`、递增 `review_version`、隔离案件、删除全部
assignment，并冻结旧 outbox。旧人工事件保留为历史，但 epoch 不符即不再是当前有效审批。
旧 cookie、备份里的 assignment 或旧审核事件不能恢复可见性、重新投递或复活批准。

恢复后的明确顺序如下：

1. 将数据目录与数据库一起切换到独立副本，保持外部模型调用关闭；重新 SSO，核对当前身份、
   本地停用状态及独立授权／撤权／删除记录。
2. 管理员按最新依据逐对象恢复所需来源及 owner 权限。全部历史依赖也须核对，不是只恢复
   当前新增材料。无法核实、已到期或已删除的内容继续保持不可读。
3. owner 明确恢复底层结果和任务可见性。固定候选批次是独立的 qualification result，使用
   `POST /api/screening/runs/{candidate_batch_id}/reauthorize`；路径中的参数在该接口代表结果 ID。
   每个关联 Jobs 任务使用 `POST /api/jobs/runs/{run_id}/reauthorize` 和最新
   `expected_state_version`。该 Jobs 操作在同一事务验证并解除指定 job 与其结果的隔离，不需
   再为同一结果重复授权；它不自动 resume、不签人审，也不放开其他结果。
4. owner 使用明确案件编号、恢复后的最新 `review_version`、唯一幂等键和理由调用
   `POST /api/reviews/cases/{case_id}/reauthorize`。满足全部来源及底层结果条件后，只解除该
   案件隔离；有未接入 successor 时为 `recomputing`，否则回到 `open`。此操作不恢复 assignment、
   不消费旧 outbox、不恢复旧批准、不自动继续计算。owner 必须重新明确 assignment，逐项重审。
5. 若需要继续可恢复的底层任务，由 owner 再按 Jobs 的当前 `allowed_actions` 和 CAS 明确
   `resume`。缺 worker、主体不新鲜、来源未授权、检查点不可恢复或应用版本不匹配都不能绕过。
   先读状态确认，案件再授权与任务执行是两项操作。

费用核对要求仍沿用 [B05 恢复屏障](DURABLE-RUNS.md#恢复隔离费用核对与显式继续)：只有人工核对
最新完整供应商账单、备份后丢失区间、共享已用与未知预留后，才可在私有配置填写本次恢复
token。B04/B05 使用 `BIDOPS_SCREENING_LEDGER_RESTORE_TOKEN`，旧演示入口使用
`BIDOPS_DEMO_BUDGET_RESTORE_TOKEN`；没有新的 review 费用 token。token 不会补回丢失账单、
清零费用或自动释放 unknown 预留。真实账本缺失时继续保持付费入口关闭。

## 应用版本变更和保留历史的回退

持久任务绑定代码实现、冻结主锁和 DBOS overlay 的 `application_version`，以及固定输入与
运行参数。升级更改实现或不兼容的输入设置后，不能改写已存版本、manifest 或 DBOS 历史让
旧 job 继续。创建复核时会检查 job 应用版本；配送与原生执行也重查当前兼容性。历史读取和
人工事件保存不等于旧任务可以执行。需要继续计算时，用当前版本建立新 run，保留旧 run、
结果、费用与审核历史，并按新的固定依据审核，不能把无效旧事件签成新批准。

优先的功能回退是在当前版本关闭 `BIDOPS_REVIEW_ENABLED`，保持外部调用关闭，保留 009
数据及当前留存清理能力。关闭复核入口会停止本功能的周期配送，不删除已经保存的事件；
以后重新启用仍要满足当前身份、权限、epoch 和版本条件。若要停止所有执行，应同时按 B05
流程停止 worker，不能把复核开关当作全局运行取消按钮。

关闭功能并重启后，已经关联正式案件的任务仍拒绝 B05 内部测试审核消息；历史测试消息
不能绕过人工关闭步骤。关闭事件被原生工作流消费时，会在最终写入事务前再次核对来源当前
版本，并检测预检期间的数据库变更。若画像、公告或材料已变化，保留原审核历史而拒绝完成
旧输入任务。服务停机时会等待正在执行的审核配送线程返回，再关闭 DBOS、数据库和单实例锁。

必须回退旧代码时，先停止服务并另存包含升级后新增案件、理由、结果、账本和成对 DBOS 库的
完整受控备份；保留这份新数据，再把升级前备份恢复到另一独立目录，由对应旧版本验证读取。
不做反向删表、不覆盖新目录、不拼接两库、不把 B05 旧代码直接接到现有 B06 目录继续处理，
因为旧代码没有 B06 的来源闭包清理和审核恢复屏障。旧备份恢复同样需要最新完整费用核对。

## 已执行的工程验证与未完成门槛

本次界面实现实际执行了 `npm run build`，并在独立 Playwright 配置下运行：

```text
npx playwright test reviews.spec.ts reviews-real.spec.ts --config <当次独立测试配置>
```

该配置使用本机 Chrome、独立 4186 预览端口，实际 API 夹具另分配独立端口；合跑结果为
**28 项协议测试 + 2 项实际浏览器集成测试，30 passed（49.4s）**。Python 夹具的
`.venv/bin/ruff check tests/fixtures/reviews_browser.py` 和 `git diff --check` 也已通过。
此数只统计这两个新增浏览器文件，不代表全仓、CI、故障恢复套件或业务总验收数量。

两项实际浏览器测试使用生产 `create_app`／ReviewService／JobsService、真实本机 SQLite、ACL
和 DBOS：标准签名协议登录、上传材料和 citation、保存画像、候选与 waiting job、建案、
第二审核人 CAS 冲突保草稿、追加更正、补材料新 job、显式接入后全部重审、刷新不增模型调用、
关闭、完整导出、画像新 head 使旧决定失效，以及撤权后列表空 200／固定详情 404 清私有页面。
所有就绪任务显式断言 `offline=true`、`unknown_calls=0`、估算及预留费用为 `"0"`。
28 项协议用例还覆盖默认关闭、角色与 assignment 区分、五态和双边证据、无默认全批准、
网络应答未确认的幂等重试、401／403／404 清理、分页、移动端、恢复元信息和配送待处理提示。

协议测试的应答由测试路由提供；实际集成测试只模拟外部 OIDC 传输及预先编写的 UNIT
FunctionModel 回答，并显式禁用外部模型请求。模拟签名正确不代表目标 IdP、真实账号或
HTTPS 环境已验收，人工事件中的测试账号也不是企业人员的业务签字。未访问真实采购来源、
未请求真实模型、未产生模型费用；未把合成五态或引用当成真实语义质量成绩。

尚未完成的门槛按 [企业 PR 计划](ENTERPRISE-PR-PLAN.md) 分列：

- **B02-06：BLOCKED。** 指定的真实旧六公告数据库、raw 和历史会话备份未提供；合成旧库迁移
  和回退不能替代指定真实输入验收，见 [B02 现有报告](../reports/b02-notice-versions-20261023.md)。
- **B08：BLOCKED。** 尚无所需许可的新独立真实项目族、独立人工金标／仲裁与人工耗时对照；
  不能从 UNIT 回答推导真实准确率、遗漏率、证据支持率或节时收益。
- **B10：BLOCKED。** 尚缺目标 HTTPS／真实 IdP／独立企业账号、经授权真实材料、最新完整费用
  账本及具名业务验收。没有验证真实 IdP 停用／角色变更时效或目标环境恢复，不宣称生产或
  人工业务 PASS，也不由开发者或模型代签企业审核与发布批准。

当前文档和离线测试不解除上述阻塞，不构成合并、部署或对外发布的授权。
