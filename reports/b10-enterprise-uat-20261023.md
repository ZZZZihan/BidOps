# B10 企业试用准备：工程验收与真实业务缺口（2026-10-23）

本报告对应已完成工程验证的干净代码 **`88dee00a36adf64ec20bb2918ead48f5a388c520`**，tree `a45142d6b4878853f297a8ea717e6fad70879d6d`。该版本已合入身份测试收集时钟修复和上游文档／证据；相对前候选 `a0cf14a…`，生产 backend／scripts／三锁字节均未变化。**本机最终2133 PASS／602.81秒，Linux CI2133 PASS／1042.83秒；当前CI三个job全部success，frontend152通过、container通过。本机lint／256文件格式／冻结评分器四命令均exit0且clean，三CLI实际0／2／2、65上游、六BLOCKED／NO_GO。** [版本身份](evidence/b10-20261023/final-verified-code-identity.json)、[最终工程状态](evidence/b10-20261023/final-engineering-status.json)。

最新 `88dee00…` 三 CLI 由root实际执行：**65 项 B01–B09 前置、六条 B10 全部 BLOCKED、NO_GO、0 模型／网络调用**。原 `a0cf14a…`／`b136…`／`427…` CLI继续按各自SHA保留，不覆盖或重贴新标签。真实企业验收和发布仍未通过。
当前计划是原69项加实施新增B04-08、B09-08，共 **71项专项AC，其中65项属于B01–B09**。工具必须保留至少这71个已登记编号；未来新增合法上游编号同样必需，当前不支持的新增B10编号则必须先升级六项证据合同。旧 `427a7ed…` CLI 的63项漏掉了B04-08、B09-08，是已修复的历史门槛缺陷，**不是完整或充分的前置合同**。

B10-01依原C项“清单具备或明确BLOCKED”的工程标准，结合当前最终本机和Linux CI检查，**工程C已关闭为PASS**；B10-06的离线汇总机制工程PASS，但原计划 **B10-06仍为B类型，真实发布复核BLOCKED**。B10-02–05缺真实环境证据，同样全部BLOCKED。`EVIDENCE_COMPLETE/READY_FOR_INDEPENDENT_REVIEW`只说明声明及引用字节齐备，不认证人员、许可、独立验收、收件或发布授权。

前候选 `a0cf14a…` 已完成实际完整检查：**2129 passed／621.28 秒，lint／256 文件 format／B01 冻结评分器均 exit0，执行后树干净**；UTC `2026-10-22T21:35:52.970001+00:00` 至 `21:46:18.413638+00:00`。该成绩作为旧 SHA 的历史保留；当前88dee已有另一次本机完整结果，见下表，不复用此历史成绩。更早 `b136…` 本机为 **2128 PASS／1 FAIL／619.67 秒**，同版 Linux CI 为 **2127 PASS／2 FAIL／990.74 秒**，两者分别保留；原 `427…` 的2039全量、89独立定向和不完整63前置输出也不覆盖。报告 Agent 本轮只读核对并投影已有证据，没有重跑测试、修改仓库或发起外部请求。

## 当前版本、输入、权限与命令

| 项目 | 当前代码事实 |
| --- | --- |
| 代码／tree | `88dee00a36adf64ec20bb2918ead48f5a388c520`／`a45142d6b4878853f297a8ea717e6fad70879d6d`；只读观察树干净，提交时间 `2026-10-23T05:48:35+08:00`；[当前版本身份](evidence/b10-20261023/final-verified-code-identity.json) |
| 验收计划 | `docs/ENTERPRISE-PR-PLAN.md`，SHA256 `1bcfc395039fb2e61d794efcd6e5be57343efb03799358f60d98e274f961d399`；71总AC／65上游 |
| 主锁 | `uv.lock`：`92ceebd17f2e98b0075eac8f13c9108b2686635e1ece5aa4d5f172de10289b27` |
| durable／attachment overlay | `cc7fa6b4de2bd7dc10ca1745b56c5c1f0891edb649732843195a854e098ccd88`／`59fd6f618e0fa0414b0caad225096e12d5c88885ad4e0471299b667315a913ba` |
| 实际已装版本 | CLI只读检查为Authlib1.9.1、DBOS3.3.0、pypdf6.21.2，与三锁一致；B10未新增依赖 |
| B10自身diff | B10业务变化为uat模块、离线脚本、示例、文档和3份测试，另合入共享`test_storage_api_cli.py`迁移预期+012一行、`test_identity_revocation.py`时间构造／边界测试及上游文档证据；无生产迁移、身份／权限／runtime／API／前端／锁更改；[逐文件hash](evidence/b10-20261023/final-code-input-identity.json) |
| 当前实际输入 | 当前88dee实际生成六条`NOT_RUN/engineering_fixture`模板，绑定当前SHA，`deployment_id=not-configured`、`artifacts=[]`；无真实账号、材料或通过声明 |
| 环境与范围 | 显式空业务环境，不读取秘密env文件，不继承企业／provider配置；仅本机离线核查。没有目标HTTPS、IdP、企业材料、收件端或当前完整账本 |
| 费用／外呼 | 当前88dee CLI `network_probes=0`、`model_calls=0`；真实模型、来源、IdP、目标、告警发送0，本轮新增费用0。没有据此证明历史账本完整或授权新付费 |
| 实现／复核 | 实现`b03_identity_adapter`；独立技术复核`b01_eval_audit`、`b01_contract_audit`；当前88dee CLI／完整检查及CI取证`root`。这些是Agent标识，不是真实用户、人审或具名发布签名 |

最新root CLI在固定干净`88dee00a36adf64ec20bb2918ead48f5a388c520`执行，UTC `2026-10-22T21:49:21.819992+00:00`至`21:49:23.232398+00:00`（北京时间次日05:49）；三命令stderr均空，执行后树仍干净，不继承BIDOPS／DEEPSEEK／OPENAI业务配置。[完整命令／时间／退出码](evidence/b10-20261023/clock-cli-verification.json)仅把本机私有路径替换为别名，不更改实际参数和退出码。

| 当前88dee实际命令／本机最终结果 | 实际结果 | 安全证据 |
| --- | --- | --- |
| `.venv/bin/python scripts/enterprise_readiness.py --template` | exit0，仅表示生成成功；六NOT_RUN，绑定当前88dee SHA／三锁／计划hash；原a0／b136模板另存历史，不覆盖 | [clock-cli-template.json](evidence/b10-20261023/clock-cli-template.json) |
| `.venv/bin/python scripts/enterprise_readiness.py`，空业务环境 | **预期exit2**；configuration_ready=false，enterprise/screening/jobs/review/ingestion/live及ledger/provider gate全false，NO_GO | [clock-cli-empty-environment.json](evidence/b10-20261023/clock-cli-empty-environment.json) |
| `.venv/bin/python scripts/enterprise_readiness.py --bundle <CURRENT_TEMPLATE> --evidence-root <PRIVATE_EMPTY_EVIDENCE_ROOT>` | **预期exit2**；schema_valid=true、65上游、六BLOCKED、0 verified artifacts、INCOMPLETE／NO_GO／NOT_VERIFIED_BY_TOOL | [clock-cli-unexecuted-bundle.json](evidence/b10-20261023/clock-cli-unexecuted-bundle.json) |
| 历史a0完整后端、Ruff、格式、B01冻结评分器 | **2129 passed／621.28秒**、lint exit0、256文件格式 exit0、冻结工程PASS／agent_score=null／business BLOCKED；四命令均自然exit0、树干净 | [完整去路径命令／UTC](evidence/b10-20261023/clock-prior-a0-full-verification.json)、[摘要／源hash](evidence/b10-20261023/clock-prior-a0-full-summaries.json) |
| 当前88dee完整后端／lint／format／freeze | **2133 passed／602.81秒，四命令exit0，256文件格式，clean**；UTC `2026-10-22T21:48:39.756853+00:00`至`21:58:46.375013+00:00`。冻结18文件验证PASS、agent_score=null／business BLOCKED | [命令／UTC／exit](evidence/b10-20261023/current-full-verification.json)、[backend原始安全stdout](evidence/b10-20261023/current-backend.txt)、[lint](evidence/b10-20261023/current-lint.txt)、[format](evidence/b10-20261023/current-format.txt)、[冻结摘要](evidence/b10-20261023/current-frozen-scorer-summary.json) |
| 当前88dee Linux CI39860218830 | **三个job全部success**：backend **2133 passed／1042.83秒**，lint／256format／freeze／migrate／pilot通过；frontend **152 passed（2.1m）**；container通过。快照观察UTC `2026-10-22T22:07:32.841672+00:00`；各job ID和原日志hash保留 | [当前CI安全终结快照](evidence/b10-20261023/final-code-ci.json)、[backend job](https://github.com/ZZZZihan/BidOps/actions/runs/39860218830/job/120108697689)、[取证范围](evidence/b10-20261023/final-ci-source-provenance.json) |

原a0实际CLI仍保留为[历史命令／UTC](evidence/b10-20261023/corrected-cli-verification.json)，b136与427原件也未覆盖；不能将这些版本pin改写成88dee。

schema/template的exit0只表示成功生成；环境／bundle预期exit2是缺口检查的正确行为，不能写成产品失败。相反，任何exit0也不构成业务PASS。B10自身没有前端改动，本报告不声称单独重跑浏览器，不借其他PR浏览器结果冒充B10现场验收。原b136 Linux CI失败摘录与当前88dee三个job全部成功的终结快照分别保存。CI浏览器是本地／合成协议工程检查，不是企业现场双账户验收。

## 逐条 AC 与真实缺口

机器可读当前状态在[final-engineering-ac-status.json](evidence/b10-20261023/final-engineering-ac-status.json)。其中工程状态与真实证据核验状态分开：当前88dee严格CLI已实际对六项均BLOCKED，B10-01 C的“已明确列出缺口”工程PASS并不改变这个结果。

| AC／原类型 | 输入与验收方式 | 工程实现／实际核查 | 当前状态与剩余证据 |
| --- | --- | --- | --- |
| **B10-01 C** | 真实IdP issuer/client/roles、HTTPS、目标机、两独立账号、许可材料／新公告、责任者及收件端，逐项具备或明确BLOCKED | 当前88dee已完成CLI复用Settings，校验HTTPS格式／控制字符、Keycloak／≤240秒快照、单worker／SQLite路径、SHA／三锁及已装版本；空环境不输出secret、URL或本机路径，所有未配业务gate保持false | **C工程PASS，当前本机及Linux CI已关闭；真实环境BLOCKED**。清单明确缺失项；没有默认生产口令或开发身份绕过，不伪造真实材料和人员 |
| **B10-02 B** | 至少两真实独立账号在目标HTTPS登录、重SSO、退出／原会话重放、CSRF／Cookie、匿名／角色拒绝，双向跨材料／会话／任务隔离 | 合同和负测保留两个独立主体／会话、双向检查；缺方向、重复人员或开发者代签不能齐备。当前没有实际登录和浏览器操作 | **BLOCKED**。缺真实目标、人员、实际时刻及私有录屏／受控日志；Agent技术复核不代替使用者验收 |
| **B10-03 B** | 真实IdP disable与role_change，保留旧会话和queued／waiting任务；受保护入口、新任务、工具及结果释放拒绝；失联／时钟边界 | 合同同时要求两种变更，每个拒绝相对真实事件在0–300秒内；反向时间、缺检查、超界不完整。≤240秒配置只是机制声明 | **BLOCKED**。没有真实IdP事件与拒绝实测，不以协议夹具或配置值宣称真实撤权性能 |
| **B10-04 B** | 经授权真实画像／材料／新公告，candidate→checklist→补料successor→逐项review→受控export，另一个实际独立用户复核 | 合同绑定固定VersionRef、batch/run/case/successor/export及artifact；要求当前reviewer角色＋assignment＋全部来源可读，Agent原unreviewed与人工投影分开，补料全重算 | **BLOCKED**。缺真实来源许可、业务产物、实际角色核验及独立用户验收；人工声明不能自证身份和权限 |
| **B10-05 B** | 目标部署中断／重启、防重、停worker全闭包备份／恢复、当前授权／完整账本及真实告警；预登记RPO/RTO | 合同拒宽于RPO86400／RTO1800秒的目标，观测不得超预登记；interrupted≤sent≤received，故障后≤300秒生成，实际收件耗时如实记录；RTO包含授权核验和业务恢复 | **BLOCKED**。无目标机／实际收件／业务重开。B09本机工程RPO或RTO=null演练不能填补本条，也不证明最新授权／完整历史费用 |
| **B10-06 B** | 当前计划B01–B09 **65项逐AC**真实证据及具名独立发布复核；E2与E3分开 | 离线汇总机制**工程PASS**：至少当前71编号、B04-08／B09-08均必需；删行／破损不缩小分母，未来上游新增同样登记，新B10必须升级合同；原FAIL/BLOCKED/NOT_RUN保留。完整合成声明最高READY_FOR_INDEPENDENT_REVIEW，不输出批准 | **B类真实复核BLOCKED／NO_GO**。缺65项当前前置实证、真实E2环境／业务与具名发布复核；E3规模／运维／责任另验。没有将本条改成C或把工具结果当人审签名 |

## 共用 G-01～G-08

| G | 当前工程结论与边界 |
| --- | --- |
| **G-01** | 工程映射PASS：逐条保留六AC的原类型、输入、方式、证据、真实缺口；34条新增计划门槛回归与65项前置单列。B10-01 C按“明确BLOCKED清单”验收，B10-06保持B；真实B未伪改PASS |
| **G-02** | **PASS（本PR当前代码工程范围）**。88dee本机后端2133／602.81秒、lint／256format／冻结18文件评分器四命令exit0、clean；Linux CI39860218830三个job全部success，后端2133／1042.83秒、frontend152（2.1m）、container成功，并完成工作流的freeze／migrate／pilot。当前CLI0／2／2的预期缺口结果正确。a0历史和b136旧失败分版本保留；本项PASS不改变五条B类真实验收BLOCKED |
| **G-03** | B10自身无schema／migration／runtime／身份变更，有diff及逐文件hash；离线核验不创建DB／迁移。此前各模块迁移／恢复与本条无变更检查分开，真实恢复仍B10-05 BLOCKED |
| **G-04** | 当前范围是本机离线CLI，没有新HTTP／批准入口；私有文件mode／目录／有界hash、拒任何组件symlink／越界／URL／重复JSON／坏输入回显均有负测。公开档案严格显式白名单，无cookie／材料／DB／trace |
| **G-05** | B10无新依赖、三锁不变，复用已有Pydantic／Settings／Authlib／DBOS／pypdf；当前CLI核已装锁定版本，不引入crawler／队列／执行框架 |
| **G-06** | 当前CLI与历史技术检查均无真实provider／来源／IdP／目标／收件网络请求，新增费用0。未获得或暗示实际模型／采集／外发授权，不声称已有账本由本工具认证 |
| **G-07** | 当前`88dee00…`、完整历史`a0cf14a…`、失败`b136…`与历史`427…`、中间WIP123／83／1、原22／9／1红测和新增身份时钟红绿分列；旧63明确为缺陷。输入／计划／三锁／文件hash独立pin；当前本机／LinuxCI终结与旧partial快照分开，旧结果不冒充新运行 |
| **G-08** | 被动离线工具不修改生产、数据库／恢复／排队／审核，不自动启用功能；当前空环境业务与live gate全false。回退先保留证据、人工与费用历史，停写后切匹配备份／镜像，不删新历史；真实目标回退与业务RTO仍BLOCKED |

G-02现已绑定88dee本机与Linux CI终结记录，工程PASS；其余G结论仍限本PR列明范围，不代替上游真实证据。阶段交接汇总的55项C工程PASS不等于企业业务通过，也不代表尚缺真实旧库的B02-06 C已关闭；本PR五条B类验收及最终发布继续BLOCKED／NO_GO。b136本机与Linux两种失败、a0历史成绩和当前88dee成绩均保持原始范围。

## 共享迁移预期缺陷与分版本验证历史

主流程B09原候选`836ccbfad779c4b6e52dd1802dd0877989dbe774`完整后端已结束，**2005 passed／1 failed／605.60s，exit1**，UTC `2026-10-22T21:19:35.082227+00:00`至`21:29:43.968491+00:00`。唯一失败为`tests/test_storage_api_cli.py::test_migrations_idempotent`：实现已有additive012索引迁移，测试精确名称列表遗漏`012_package_versions_index.sql`。该真实失败不能改写为全量PASS，也不能因为是测试预期缺陷而从失败数删除。

B10原`b136…`全量现已实际结束，**2128 passed／1 failed／619.67s，exit1**，UTC `2026-10-22T21:24:48.567078+00:00`至`21:35:11.694796+00:00`；唯一失败同上。该场没有执行后续Ruff／format／冻结评分器，不把未跑写成通过。[b136失败摘要](evidence/b10-20261023/final-b136-full-failure.json)保留原日志hash。[安全失败／修复记录](evidence/b10-20261023/final-shared-migration-gap.json)只保存原日志hash、计数／时间／退出码及失败节点，不复制trace。

root已在独立验证分支提交测试修复 **`36efed31ae496f42474cd8389236668b05767499`**，仅对精确预期列表添加上述一行；静态diff确认原所有名称和完整相等断言保留，生产实现／锁未改。b03独立该项实际**1 passed／0.80s**，日志及UTC／exit元数据已核；执行时为836基线＋同一行WIP，不能描述为提交后全量或重新运行。[定向输出](evidence/b10-20261023/final-migration-independent-test.txt)、[去路径元数据](evidence/b10-20261023/final-migration-independent-verification.json)保留来源hash。随后B10迁移预期修复候选为**`a0cf14a5e909c71be45753a279acb4713203c204`**，与b136精确diff仍只有同一测试+012一行，原相等断言完整；生产实现／锁未改。该a0新整合full现已实际结束：2129 passed／621.28秒、lint／256文件格式／冻结评分器均exit0且树干净；详见上表。a0版本CLI亦实际执行，0／2／2、65前置、六BLOCKED／NO_GO；[原b136 CLI](evidence/b10-20261023/final-cli-verification.json)仍保留为另一版本历史，不覆盖。

## 当前本机／Linux CI终结与历史版本分开

88dee本机最终检查以root原始终结记录为准：pytest **2133 passed in602.81s**，控制进程UTC `21:48:39.756853`至backend结束`21:58:45.719351`；后续lint、format、freeze至`21:58:46.375013`全部exit0。pytest自身时长与控制进程墙钟不同，均按原字段保存；不把602.81秒写成四命令总墙钟。输出为合成工程／协议夹具，真实模型0、费用0；冻结工程PASS没有Agent业务分数。[终端验证](evidence/b10-20261023/current-full-verification.json)、[freeze原始stdout](evidence/b10-20261023/current-freeze.txt)。

另有先前a0的Linux CI39858527890已实际三个job全部success，后端 **2129 passed／630.84秒**。这是a0的远端历史，与其本机2129／621.28秒以及当前88dee本机2133／602.81秒分别记录，不把a0的CI通过归给88dee。[a0历史CI安全选录](evidence/b10-20261023/current-historical-a0-linux-ci.json)保留三job ID和原log hash。当前88dee CI39860218830现已三个job全部success，Linux后端2133／1042.83秒与本机2133／602.81秒分开列示。先前尚未终结的CI部分状态[current-ci-status.json历史快照](evidence/b10-20261023/current-ci-status.json)保留原字节；当前状态以[终结快照](evidence/b10-20261023/final-code-ci.json)为准。报告取证来自已有安全快照，未额外外部查询。

## Linux原失败与身份测试时钟修复（生产规则不变）

原 `b136ed58c689b30c98bf43a4eee2a1a134181899` 的 **Linux CI39857041113／job120097812664** 实际为 **2127 passed／2 failed／990.74秒，exit1**，不同于同SHA本机2128／1／619.67秒。两项是迁移预期漏012，以及 `test_invalid_signed_logout_claims_rejected_without_state_change[updates6]` 的 collection-time future-iat 输入在执行前已变为合法。只保存[安全失败摘录与原log hash](evidence/b10-20261023/clock-b136-linux-ci-failure.json)，不复制完整失败trace，不将旧CI改成通过。

原测试在collection取`T0+120`，而运行到它时可能已经过180秒，iat变成`now−60`。生产既有合法窗为`[now−300, now+30]`，此时返回200符合原合同；应修测试输入，不应让生产误拒合法签名。精确旧测试+180秒无sleep探针实际 **1 failed／18 passed／9.19秒**，红日志及diff只保留hash。修复将时间输入改为执行期生成，并在原真实签名登录fixture完成后，仅用独立SimpleNamespace冻结`identity_events.time`模块绑定；未改stdlib time、OIDC／session时钟或生产源码。

修复实现者保留原19组非法claims和原全部断言，新增−301／−300／+30／+31四个精确边界；执行文件全量 **43 passed／8.92秒**、同19项延迟探针 **19 passed／4.35秒**，Ruff／format通过。未参与实现者独立重新执行为 **43 passed／8.77秒**、延迟 **19 passed／4.48秒**，另用本地内存JWKS和真实RS256签名直接调用生产verifier的 **7个确定性案例全部符合预期**。19项与43项重叠，不相加；7例是直接verifier脚本，不冒称7个HTTP端到端或真实IdP用例。[红绿／边界摘要](evidence/b10-20261023/clock-test-history.json)、[实现命令／UTC／退出码](evidence/b10-20261023/clock-fix-verification.json)、[独立命令／UTC](evidence/b10-20261023/clock-independent-checks.json)、[原断言AST保留](evidence/b10-20261023/clock-independent-assertion-integrity.json)、[独立七例](evidence/b10-20261023/clock-independent-verifier-probe.json)。

实现及独立复核针对 `36efed3…` 基线上的精确未提交diff，SHA256 `21351b4946920f6bdc244dead18058ae1e9e504f63297e70e50ec2f04371d677`；不是当前88dee的整套重跑。root经 `db4f55dbf410acb4ef36c87738b0f95eddada5ed` 集成后，当前88dee的身份测试文件SHA与该已审修复相同。a0→88dee另包含GOAL／TASKS及B07／B08报告／证据更新；backend、scripts与主锁／两overlay无字节变化，300秒最大年龄／30秒未来宽限不变。[当前版本逐项依据](evidence/b10-20261023/final-verified-code-identity.json)。这些检查没有外部网络、真实模型、真实IdP或付费，不能填补B10-03真实停用／变更实测；当前CI的工程通过另由版本绑定的终结记录证明。

## 计划门槛修复、独立复核及历史失败

当前合同计数不是凭报告手填：root当前88dee实际CLI返回65，代码pin／计划hash与当前候选一致。有效计划逐节为B01=7、B02=6、B03=8、B04=8、B05=7、B06=7、B07=7、B08=7、B09=8。B10六项另列。实际修复新增 **34条永久回归，89→123**，没有降低原六项真实证据门槛。

| 阶段 | 原失败及后续真实结果 | 范围与版本限制 |
| --- | --- | --- |
| 原regex漏括号标注的B04-08及新增B09-08；AC格式／重复未严格拒绝 | 原新增例 **22 failed、49 deselected／0.80s**；逐行严格解析后 **112 passed／2.20s** | 合法括号保留ID；重复／畸形AC使整个计划不可用，普通非AC表不误判 |
| 可用空行＋B4-08、整行删除／去pipe避开必需集合 | 原 **9 failed、1 passed、73 deselected／0.67s**；加至少当前71编号后 **122 passed／2.52s** | 不能通过重写／删行缩到64READY。合法未来B09-09变成第66项上游，未登记NO_GO，补证据才可READY；不设置最大65 |
| 新B10-07超出当前六项合同 | 原 **1 failed、83 deselected／0.27s**；显式拒未支持B10编号后 **123 passed／2.51s** | 必须先升级合同，不能像上游扩展一样自动接受；不改变未来合法上游规则 |
| 未参与实现者独立复核 | contract **83 passed／1.70s**，末项新增独立 **1 passed／0.29s**；原no-pipe探针重跑均acceptance_plan_unavailable、六BLOCKED、INCOMPLETE／NO_GO，有效计划65 | [探针](evidence/b10-20261023/final-plan-independent-probe.json)、[83项](evidence/b10-20261023/final-plan-independent-tests.txt)、[末项](evidence/b10-20261023/final-plan-b10-extension-independent.txt)；这些为提交前WIP技术复核，来源hash留存，不相加为clean当前全量计数 |

[final-plan-parser-history.json](evidence/b10-20261023/final-plan-parser-history.json)保存各原始红／绿日志SHA256、字节数和终端摘要，**不复制完整traceback**。中间日志没有逐命令完整SHA／UTC时刻或完整命令时，明确记未记录，不编造。独立复核为技术行为，不是实际企业用户／具名发布签名。

原恢复／URL修复也继续保留：90,000／2,400秒宽目标、目标+1、超目标观测；故障前告警／逆序／300秒+1微秒；PUBLIC_URL／ISSUER的CR／LF／TAB／DEL／空白共12负测。恢复旧红6failed2passed、URL旧红12failed及69→77→89历史，不因新增计划修复被覆盖。[历史摘要](evidence/b10-20261023/historical-red-green.json)

### 固定旧 `427…` 结果仅作为历史

下列真实执行仍保留原文件字节，但旧合同遗漏当前前置AC，**测试通过不证明旧门槛充分**。当前版本已使用新的65项及本SHA本机／Linux CI完整工程验证结果，不能继续在顶层写“当前63”。

| 旧固定代码范围 | 历史实际结果／时间 | 证据 |
| --- | --- | --- |
| `427a7edb28e059488fd2eadcddc7352d83bca6fc`完整后端 | **2039 passed／586.23s，exit0**；UTC2026-10-22T20:54:42.452928至21:04:32.150760 | [旧backend](evidence/b10-20261023/integrated-backend.txt)、[旧命令元数据](evidence/b10-20261023/integrated-verification.json) |
| 旧全仓lint／format／冻结评分器 | exit0、249 files formatted；冻结工程PASS且agent_score=null、business BLOCKED，UTC21:04:32.151636至21:04:32.892228 | [旧lint](evidence/b10-20261023/integrated-lint.txt)、[旧format](evidence/b10-20261023/integrated-format.txt)、[旧评分摘要](evidence/b10-20261023/frozen-scorer-summary.json) |
| 旧独立三UAT定向／lint／format | **89 passed／2.57s**，8files格式、命令exit0；UTC21:13:45.798240至21:13:49.170611 | [旧89](evidence/b10-20261023/targeted-tests.txt)、[独立元数据](evidence/b10-20261023/independent-verification.json) |
| 旧schema／template／空环境／未运行包 | schema/template exit0；环境／未运行包预期exit2，六BLOCKED、NO_GO、**旧63不完整**；UTC21:13:44.362487至21:13:49.634726 | [旧schema](evidence/b10-20261023/schema.json)、[旧template](evidence/b10-20261023/template.json)、[旧63输出](evidence/b10-20261023/unexecuted-bundle.json) |

原公开索引hash及旧报告hash已留在[历史身份文件](evidence/b10-20261023/final-code-input-identity.json)，旧payload未被覆盖。原完整报告仅私有保留，当前报告以已验证的88dee代码为准，避免读者把旧63或2039误认成当前验证。

## 安全归档与复跑范围

公开归档范围为[evidence-index.json](evidence/b10-20261023/evidence-index.json)显式列出的成员及本报告，不包含递归工作目录。旧成员分别标明427、b136、a0历史及WIP；新`clock-*`／`current-*`成员记录当前88dee或其明确历史来源。原72个payload（含427、b136、a0、先前clock及当前CI部分状态快照）及对应旧索引／报告均保留，已有payload字节未改，旧索引／报告私有留档。旧`final-integrated-status.json`等只是创建当时的pending快照，当前状态以`final-engineering-status.json`及`final-code-ci.json`为准；先前clock和CI部分状态快照按创建时点保留。索引只是字节完整性清单，**不是外部签名、先验可信pin、人审或发布批准**。

允许字段仅版本／hash／计数／固定缺口码／因果UTC／必要命令及范围。当前CLI模板和输出经逐字段检查无秘密，原始trace只保留SHA及摘要。排除所有env／凭证／JWT／Cookie／Authorization／原sub／真实人员／bootstrap／pytest临时目录／SQLite／DBOS／备份／录屏／材料及模型正文，未提供的真实企业证据不能由公开fixture补齐。旧来源见[final-source-provenance.json](evidence/b10-20261023/final-source-provenance.json)，时钟修复投影见[clock-source-provenance.json](evidence/b10-20261023/clock-source-provenance.json)，当前CLI原始文件与固定命令／环境过滤脚本hash见[clock-cli-source-provenance.json](evidence/b10-20261023/clock-cli-source-provenance.json)；本机最终stdout／元数据与历史CI来源见[current-source-provenance.json](evidence/b10-20261023/current-source-provenance.json)，当前CI快照／原log hash和版本绑定工作流来源见[final-ci-source-provenance.json](evidence/b10-20261023/final-ci-source-provenance.json)。

以下为具备锁定依赖的干净代码上的离线复跑方式。使用新的私有目录且保留旧结果，环境／未运行包预期exit2；命令不能开启模型、采集、发送或发布：

```bash
umask 077
mkdir -p private/uat-offline-next/evidence
.venv/bin/python scripts/enterprise_readiness.py --schema > private/uat-offline-next/schema.json
.venv/bin/python scripts/enterprise_readiness.py --template > private/uat-offline-next/input.json
env -i PATH="$PATH" LANG=en_US.UTF-8 PYTHONPATH=backend ALLOW_MODEL_REQUESTS=false \
  .venv/bin/python scripts/enterprise_readiness.py > private/uat-offline-next/environment.json
env -i PATH="$PATH" LANG=en_US.UTF-8 PYTHONPATH=backend ALLOW_MODEL_REQUESTS=false \
  .venv/bin/python scripts/enterprise_readiness.py \
  --bundle private/uat-offline-next/input.json --evidence-root private/uat-offline-next/evidence \
  > private/uat-offline-next/summary.json
.venv/bin/python -m pytest -q tests/test_uat_environment.py tests/test_uat_evidence.py tests/test_uat_cli.py
.venv/bin/ruff check backend/bidops/uat scripts/enterprise_readiness.py tests/test_uat_environment.py tests/test_uat_evidence.py tests/test_uat_cli.py
.venv/bin/ruff format --check backend/bidops/uat scripts/enterprise_readiness.py tests/test_uat_environment.py tests/test_uat_evidence.py tests/test_uat_cli.py
```

真实UAT仍需有权操作者提供受控配置、最新完整账本、许可材料和真实独立人员，在目标环境另建私有证据按六AC执行。本报告不为这些真实性签名，也不因完整合成正例或技术Agent数量授权E2／E3发布。
