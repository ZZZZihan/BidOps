# 真实企业试用的准备与证据核验

B10 提供离线环境检查、六项验收输入合同和私有证据完整性核验。工具不会登录 IdP、探测
目标域名、调用模型、触发来源采集、恢复任务或发送告警；也不签署人工决定或批准部署。
真实 IdP、HTTPS、目标机器、经授权企业材料、独立用户和当前完整费用账本未提供时，
相应验收保持 **BLOCKED，发布判定为 NO-GO**。

验收条目与原始门槛以 [ENTERPRISE-PR-PLAN](ENTERPRISE-PR-PLAN.md#13-b10--真实身份系统与企业试用验收)
为准。身份机制见 [ADR-0004](ADR-0004-IDENTITY-REVOCATION.md)，运行和恢复分别见
[DURABLE-RUNS](DURABLE-RUNS.md)、[HUMAN-REVIEW](HUMAN-REVIEW.md) 与
[AUTHORIZED-UPDATES](AUTHORIZED-UPDATES.md)。这些工程文档和本机协议测试均不替代真实环境验收。

## 安装和离线检查

沿用冻结主锁及带哈希的 DBOS、附件解析 overlay，不修改 `pyproject.toml` 或 `uv.lock`：

```bash
uv sync --frozen --python 3.12
uv pip install --python .venv/bin/python --require-hashes \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements/durable.lock
uv pip install --python .venv/bin/python --require-hashes \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements/attachments.lock
```

之后使用 `uv run --frozen --no-sync ...`，避免主锁同步移除 overlay。依赖已在本机可信缓存
时可分别加 `--offline`；离线缓存缺失应如实报告，不能换用未锁版本。

在已授权的目标机上，由部署者准备真实企业配置，再执行：

```bash
uv run --frozen --no-sync bidops-enterprise --env-file private/enterprise.env check-config
uv run --frozen --no-sync python scripts/enterprise_readiness.py --env-file private/enterprise.env
```

环境文件必须只有所有者可访问，例如权限 `600`；不要把文件或秘密配置放进 Git。
readiness 脚本只有明确传入 `--env-file` 才读取该文件，不自动读取 `private/deepseek.env`。
已导出的环境变量优先于文件，和 enterprise CLI 的 `override=False` 一致。部署所需的
秘密可通过受控环境注入，本文不提供示例密钥，也不要求把供应商密钥写入企业配置文件。

检查器复用现有 Settings 校验，另检查真实 HTTPS 格式、非 loopback/保留示例域名、
Keycloak、最长 240 秒身份快照、单 worker 部署声明、SQLite 位于数据目录内，以及
Authlib/DBOS/pypdf 的已锁版本。它记录当前代码 SHA、三个实际锁文件哈希；在 Git checkout
中还检查脏树与 `BIDOPS_CODE_SHA` 是否匹配 HEAD。容器没有 Git 时，部署者必须提供真实
的构建 SHA 并独立核对镜像来源。

输出只含固定检查码、布尔开关、依赖版本及公开代码/锁哈希。它不输出 URL、文件路径、
secret 值、secret 的哈希或验证异常原文，也不创建数据库或执行迁移。
`configuration_ready=true` 只表示本机声明满足这组静态检查，不能证明 HTTPS 证书、
IdP 配置、密钥有效性或随机性、账号独立性、来源许可、告警送达和账本完整性。

完整业务流程的筛选、持久任务、人工复核开关默认都为 false。缺少这些能力会在准备检查中
显示缺口。模型执行还需现有独立 gate：`BIDOPS_SCREENING_LIVE_CALLS_ENABLED`、
`BIDOPS_SCREENING_LEDGER_VERIFIED` 和有效供应商配置；检查器不会替部署者开启它们。
只有在获得真实调用授权、人工核对**最新完整累计账本**后才能配置相应 gate。恢复 token
只表示已对账的部署声明，不会补回丢失费用，也不消除未知调用或重复计费风险。

## 先冻结候选版本，再准备证据包

所有命令在仓库根目录执行。以下目录是项目已经忽略的私有目录；不要把真实材料和录屏
复制到公开 `reports/`。输出目录由操作者选择新的名字，保留以前的证据，不覆盖旧记录。

```bash
umask 077
mkdir -p private/uat-evidence
uv run --frozen --no-sync python scripts/enterprise_readiness.py --schema \
  > private/uat-schema.json
uv run --frozen --no-sync python scripts/enterprise_readiness.py --template \
  > private/uat-input.json
```

`--template` 读取当前 HEAD、三份锁和验收计划的真实哈希，生成六条 `NOT_RUN` 记录，
没有账号、材料、证据或通过声明。它不能证明工作树已冻结。应在准备验收的干净候选版本
上生成并保存；代码、锁或计划变化后，旧证据保留为历史，针对新候选重新登记和执行。
仓库中的 `config/uat.example.json` 也只有未运行记录，所含 SHA 是示例创建时的真实基线，
不能直接当作以后版本的验收输入。

生产镜像沿用实际构建依据，例如由部署者在干净 checkout 执行：

```bash
docker build --build-arg BIDOPS_CODE_SHA="$(git rev-parse HEAD)" -t bidops-enterprise:candidate .
```

镜像保留 `BIDOPS_LOCK_PATH=/app/uv.lock`、
`BIDOPS_DURABLE_LOCK_PATH=/app/requirements/durable.lock` 和
`BIDOPS_ATTACHMENT_LOCK_PATH=/app/requirements/attachments.lock`。不要用任意 SHA 掩盖
未提交的代码，或删除实际安装依据后只留下声明。此构建命令不是启动、发布或生产配置授权。

## 六条记录必须提供什么

共同字段为 `ac_id`、`environment_kind`、`deployment_id`、`code_sha`、操作者/复核者别名、
含时区的开始/结束时间、`artifact_ids` 与 `claimed_outcome`。别名用于与受控的真实人员
登记交叉核对，工具不能验证他们的真实身份。记录内的所有观察时间必须落在该记录时间窗，
结束时间不能明显晚于检查时钟。开始前应核对目标机、IdP 与记录端的 UTC 时间。

| 验收项 | 输入与实际操作证据 |
| --- | --- |
| **B10-01 环境** | `inventory` 登记真实 origin/issuer/client、精确 callback/backchannel URI、Keycloak 版本、`bidops_roles` 映射、目标机和运维责任者、单机单 worker 与时钟、至少两名独立实际操作者及不同主体哈希、授权画像/材料/新公告固定 VersionRef、来源许可引用、告警接收端别名与预登记 RPO/RTO；本条附 `configuration_artifact_id`。不填秘密或原 sub。 |
| **B10-02 登录与隔离** | `sessions` 逐账号记录真实登录、重新 SSO、退出及原会话重放拒绝、Cookie 属性和 CSRF 负向；`access_checks` 记录两账号双向跨材料/会话/任务的 404、匿名 401、角色拒绝 403 与实际状态及时间。原始录屏和脱敏日志保存在私有证据目录，不能使用签名 OIDC 夹具替代。 |
| **B10-03 撤权** | `changes` 同时包含真实 `disable` 和 `role_change`，记录 IdP 事件、保留的旧浏览器会话、排队和等待审核任务 ID，以及受保护请求/新任务/工具/结果释放的拒绝时间。每个拒绝时间与事件差必须在 0–300 秒；另外记录 IdP 失联时快照到期拒绝和时钟边界。 |
| **B10-04 企业链路** | `flow` 绑定授权固定画像、材料和新公告版本，以及 batch/run/case/新补料 successor run、受控导出、独立用户复核记录；逐项核对双方证据、明确 assignment 和来源权限、补料新 run 全重算、Agent 原结果不改写。独立 reviewer 必须具备 reviewer 角色，不能由开发者代签。 |
| **B10-05 目标机演练** | `recovery` 绑定实际任务和中断/重启/恢复时间、同 key 同 ID/异输入 409、原生步骤与前后模型调用账本、停止 worker 的 v4 备份和恢复证据、restore epoch、旧会话拒绝、全部来源/job/review 隔离及显式重新授权、旧审批无效、最新完整账本核对、真实告警发出和收件时间；RPO/RTO 不得超过预登记目标，且目标本身不得宽于 86,400/1,800 秒。故障告警必须在本次中断之后、300 秒内生成，随后实际收件，不能引用故障前的消息；收件耗时如实记录。RTO 包含授权核验和业务恢复，不能只填 HTTP 响应时间。 |
| **B10-06 发布复核** | `prerequisites` 必须逐一包含当前计划里 B01–B09 的每个 AC，状态与证据引用不得漏掉失败项；附具名发布复核、试用规模/责任边界和 `human_claimed_decision`。任一前置 FAIL/BLOCKED/NOT_RUN 即不完整。E2 企业试用与生产 E3 分开，E3 需另一套规模及运维验收，本工具不接受借本包宣称 E3。 |

`VersionRef` 必须使用服务端实际返回的 `object_id`、`version_id`、`sha256`，不能仅登记
可变的对象 ID。企业主体使用稳定绑定的 SHA256 摘要和私有人员登记证据，不提供原始 token、
IdP 会话 ID、完整个人信息或密码。`actual_human`、`independent` 等字段都是需要独立核对
的人工声明，不能仅因值为 true 就认为身份已验收。

当前支持的是 Keycloak 的 RS256 Back-Channel Logout。角色变更或停用不保证发送 logout
事件；240 秒快照期限才是无事件情况下下一次授权检查的强制上界。B10 的 300 秒目标还
要用真实事件和拒绝时间验证。登录重认证使用 `prompt=login`、`max_age=0`；不能通过
延长旧 Cookie、轮询或假造本地 Principal 来演示撤权通过。退出重放、后台来源权限和结果
释放都应在实际环境验证，已发给浏览器的正文无法追溯撤回。

人工复核继续要求“当前 reviewer 角色 + 显式 assignment + 全部来源可读”，admin 不会
自动获得批准权。Agent 结果的 `human_review_state` 仍为 `unreviewed`，人工事件投影独立。
补料必须新 run 全重算并明确接入新 revision；close 是内部跟进结束，不等于原生 job 已完成。
应分别保留 delivery pending/已消费、失败和旧版本失效的证据，不能用页面文字掩盖队列状态。

## 私有证据和核验命令

`artifacts` 为每份文件登记唯一 ID、相对路径、SHA256、字节数和 media type。只允许私有
目录中的普通文件：根目录无 group/other 权限，文件无 group/other 权限，不允许绝对路径、
`..`、远程 URL 或任何路径组件的软链接。单文件上限 50,000,000 字节，总登记上限
200,000,000 字节；输入 JSON 上限 2,000,000 字节。录屏较大时应按一次操作拆成有界片段，
保留所有片段和完整索引，不能只选通过的一段。文件在核验时保持不变。

每条记录要把自己及嵌套对象引用的全部 artifact ID 列入 `artifact_ids`。环境登记中的
人员声明、来源许可也必须对应核验通过的文件。核验只读文件并计算哈希，不解析或打印
材料、JWT、录屏内容；独立复核者仍需在受控环境逐一打开和核对。录制前禁用浏览器对密码
及 provider token 的展示，日志遵守既有不记录 Cookie/Authorization/OAuth code 的基线。

```bash
uv run --frozen --no-sync python scripts/enterprise_readiness.py \
  --env-file private/enterprise.env \
  --bundle private/uat-input.json --evidence-root private/uat-evidence \
  > private/uat-summary.json
```

退出码 `2` 表示配置/输入/证据存在缺口，`0` 表示所选检查完成：schema/template 命令的 0
只表示成功生成；单独环境检查的 0 只表示静态配置齐备；bundle 检查的 0 最多表示
`READY_FOR_INDEPENDENT_REVIEW`。**任何一个退出码都不代表业务 PASS 或部署授权。**

核验输出区分操作者提供的 `claimed_outcome` 和工具状态。`EVIDENCE_COMPLETE` 仅表示
格式、必要声明、时间边界及引用字节齐全；工具无法证明文件内容真实、人员独立、签名有效
或消息确已送达。即使六项齐全，也只输出 `release_decision=NOT_AUTHORIZED_BY_TOOL`、
`business_acceptance=NOT_VERIFIED_BY_TOOL`，仍要由独立发布复核作结论。有缺口则输出
`release_decision=NO_GO`。模拟/历史记录不能满足真实环境类别，不能用修改标签代替真实操作。

## 恢复、回退和现存阻塞

备份恢复先停止唯一 worker，使用现有 CLI，不对运行中的 DBOS 系统库做不成对复制：

```bash
uv run --frozen --no-sync bidops-enterprise --env-file private/enterprise.env backup \
  --output private/uat-stopped-backup --retention-days 7
uv run --frozen --no-sync bidops-enterprise --env-file private/enterprise.env restore \
  --input private/uat-stopped-backup --output private/uat-restored-independent
```

输出目录必须尚不存在。切换恢复目录前重新核对数据目录/数据库指向和恢复清单。
v4 保留业务数据库与 DBOS 系统库、固定原文/附件/公司私有闭包；恢复撤销登录、更新 epoch，
隔离来源对象、任务和复核。新 SSO 不恢复旧授权或旧审批。先逐一核对来源权限，再按当前
API 对 job 和 case 显式重新授权；job 重新授权同时验证并放开指定结果，转为 interrupted
后仍需显式 resume。case 去隔离也不会复活旧人工决定。恢复 token 与累计费用核对流程
沿用 B04/B05 文档，不因为本 UAT 工具新增任何费用 token。

回退优先保留当前数据库、证据和全部人工/费用历史，关闭新功能及外部执行；如确需旧代码，
在停止写入后切换到经过核验的升级前备份和匹配镜像。不要反向删迁移表或清空新结果，
不要让旧检查点在代码、输入设置或依赖变化后冒充新运行。UAT 本身只增加离线文件和工具，
没有新增生产迁移，也不修改身份、授权或执行架构。

当前可交付的是离线准备检查和合成证据包的工程正负测试。真实来源许可和更新周期、旧真实
数据迁移、独立冻结数据/金标、真实企业材料、真实 Keycloak/HTTPS 与独立用户、目标机恢复/
告警、最新供应商完整费用账本和具名发布复核仍须按相应 AC 提供，缺失项保持 BLOCKED。
旧 `scripts/enterprise_live.py` 是显式调用模型的历史 provider/persistence 探针，代码构造
Principal 且声明 `oidc_live_test=false`，不能作为 B10 的真实登录、人工验收或新独立数据成绩。

本工具的定向工程检查命令为：

```bash
uv run --frozen --no-sync pytest -q tests/test_uat_environment.py tests/test_uat_evidence.py tests/test_uat_cli.py
.venv/bin/ruff check backend/bidops/uat scripts/enterprise_readiness.py tests/test_uat_*.py
.venv/bin/ruff format --check backend/bidops/uat scripts/enterprise_readiness.py tests/test_uat_*.py
```

测试中的账号、证据和时间均为明确合成夹具，所有真实模型请求禁止；测试通过不补齐任何
真实环境或业务门槛。最终候选版本的实际执行结果与整合报告由主流程另行记录。
