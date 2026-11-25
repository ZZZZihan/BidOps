# 原生双计算队列：独立只读技术复核

结论：**no blocking findings**。在本次限定的三文件增量及其直接依赖中，未发现导致恢复绕过计算上限、取消释放未知费用、同步入口额外受理第三计算，或破坏 v4 双库备份闭包的实质问题。此结论是代码技术复核；不代替完整负载、目标环境容量、真实业务或人工验收。

复核固定提交：`69560429745417e34113d0877d75a62d7eb8f37b`。工作树为 `$NEW_WORKTREE`；开始及写出证据前均只读确认 HEAD 和 clean 状态。比较基线：`215fca737e0e466a9781cd020b6b9d3b89e4b43f`。

本人未实现下列生产变更，因此对此增量的复核独立于实现者。本人实现的三项恢复测试属于自测，不能因此自称已独立复核这些测试；测试已另交 Agent 复核。请求的子 Agent 配置为 GPT-6.1 Sol / ultra，实际运行模型与推理档位不具备独立核验途径。

## 实际范围与方法

本次只读检查了：

- `backend/bidops/jobs/config.py`、`backend/bidops/jobs/runtime.py`、`backend/bidops/qualification/api.py` 的完整增量及调用上下文。
- 共享预算的 `qualification/model_budget.py`、持久模型回执的 `jobs/model.py`、v4 配对摘要的 `jobs/backup.py`、单 worker 文件锁，以及 ADR-0008、B09-09/10 的合同文字。
- 实际安装的 DBOS SDK SQLite 写事务、显式 resume 的默认队列、启动恢复保留队列逻辑；没有用当前官网的行为替换锁定 SDK 源码。
- `test_jobs_compute_queue.py`、`test_jobs_sync_admission.py` 的场景和断言，作为覆盖设计核查。本次不重复运行这些测试，也不把他人报告的结果列作本人的新实跑。

本次未启动应用、模型、外部网络或另一轮测试，没有读取或修改正在运行的容量目录，没有写入仓库文件。唯一写入为主会话 `work/` 下本说明和 `native-compute-recovery-evidence.json`。

## 恢复、取消和计算边界

1. **默认行为和范围受控。** `jobs/config.py:9` 的 `compute_concurrency` 默认 1，验证范围 1–2；既有 `max_active_jobs` 未被混同为计算并发。`runtime.py:110` 的物理 Semaphore 和 `runtime.py:506` 注册的原生计算队列使用同一配置值。父任务队列仍按原有活动任务上限受理。

2. **初次 child 提交与显式恢复都受原生队列约束。** `runtime.py:468` 保留固定 child ID，并通过 `enqueue_workflow_async(CALCULATION_QUEUE, ...)` 建立父子关系。`runtime.py:671` 恢复未完成 child 时明确指定计算队列，`runtime.py:675` 恢复父任务时明确指定父队列。锁定 SDK 的 `_sys_db.py:1283` 在未提供队列时会转向内部队列，所以这两处显式参数实际修复了恢复绕过限额的路径。启动重排的 SDK 实现 `_sys_db.py:4743` 使用 `COALESCE(existing_queue, recovery_queue)`，已有 child 的计算队列能够保留。

3. **待审不占计算名额；取消回执不等于在途计算已退出。** `runtime.py:386` 的 Semaphore 只覆盖 calculation 协程；`runtime.py:472` 之后的父任务等待和审核接收在该范围外。`runtime.py:639` 先持久写取消请求，再调用框架取消父子任务；一旦框架释放其 pending 名额，尚未实际返回的模型请求仍持有物理 Semaphore，直到协程退出。因此保留这道限制有必要，不能仅依赖 native CANCELLED 状态。执行守卫和取消状态本身的既有拒绝逻辑未被本次变更绕开。

4. **旧代码检查点不会被新代码冒充执行。** `runtime.py:150` 对持久 `application_version` 做精确比较；启动时在 `DBOS.launch` 前对全部活动任务检查、取消并中断不符合当前代码/权限/输入约束的工作流（`runtime.py:262`）。显式 `resume` 先进入 `resumable`，后者先调用 `require_execution`。新队列不意味着旧 application version 可以直接续跑。本次未独立重做主 Agent 负责的真实新旧代码升级和回滚探针。

5. **同版 2→1 的更改需要停机重启。** 队列注册会更新框架配置，进程启动时的 Semaphore 也取新值；即使 SDK 启动恢复先看到旧队列配置，进程计算仍受新的物理 Semaphore 限制。没有运行中热改对象或把当前配置伪写进旧任务的必要。此处是静态判断，不扩张为热更新支持声明。

## 共享费用和上下文

共享费用代码在本次增量中保持原样。`qualification/model_budget.py:43` 在同一 `BEGIN IMMEDIATE` 事务内执行恢复费用确认屏障、累计费用计算、限额判断和新预留写入；没有先在事务外读取余额、再分别入库的并发窗口。累计口径在 `model_budget.py:51` 将未完成行继续按预留额计入。

`jobs/model.py:104` 要求每次请求位于该 run 的 calculation 工作流和原生 step 中；指纹同时绑定工作流/step/消息/参数，回执按 run 读取。未知 attempt 不被当作可复用响应；新的请求需要新的原子预留（`jobs/model.py:158`、`:166`）。provider 异常后保留 `unknown` attempt 与 `unconfirmed` 账本行（`:176`）。取消或重启不会自行扣掉这些费用。

每次 calculation 创建独立 `Execution` 和 guard，并以 `ACTIVE_MODEL.set` / `reset` 配对（`runtime.py:398`、`:400`、`:412`）。两个 calculation 的异步上下文不依靠可变全局当前 run ID。这一设计与本人的实际跨 await 和持久回执隔离测试相符。

## HTTP 与备份边界

同步入口 `qualification/api.py:61` 先执行身份、权限及主体检查；之后只要 Jobs **enabled** 就返回明确 409，判断不依赖 worker 是否已启动或 provider 是否配置。这样 worker 故障时也不会悄悄回退同步路径、在另一事件循环中额外受理第三条资格计算。Jobs 关闭时仍走原有接口。当前代码中的生产 checklist 调用方只有该路由；候选与历史路由未被这项门禁误封。现有 HTTP 测试分别覆盖 enabled/unstarted、缺模型、关闭 Jobs、权限、CSRF 和历史兼容。

SQLite、SingleWorker、业务 ID、固定 root/child workflow 名称以及 v4 格式均未变化。`jobs/backup.py:747` 对系统库所有非绑定/非 SQLite 内部表计算摘要，因此新增 `queues` 行被自然纳入配对快照，不依靠仅列 workflow 名称的摘要。原有 root/child 映射、固定 application version、两库绑定及隔离恢复不因新增队列被放宽。不需要为了新增队列本身升级备份格式；停机和独占锁的前提仍成立，不能把本次修改解释为在线一致备份支持。

本人三项恢复测试已实际观察新队列配置进入真实 v4 bundle，恢复后三个旧任务隔离、身份与来源重授权不自动释放旧任务、缺失或错误费用确认令牌无法新增调用/账本。原始库和模型正文没有被复制到安全摘要。

## 与新 AC 的对应关系和实际证据限制

- **B09-09 C：** 三文件增量直接实现默认 1/可选 2、原生 child 队列、同值物理限制和同步入口 409。本人只读核查了第三任务、取消未返回、待审释放、恢复指定队列及真实 HTTP 负测的代码与断言；这些其他测试的实际运行及浏览器结论由其执行者负责。
- **B09-10 C：** 本人实际三项测试证明两个在途 calculation 的真实 SIGKILL、原生队列恢复、上下文/固定结果隔离、共享预算竞争及未知费用保留，以及 v4 备份恢复权限/费用屏障。真实旧版本升级和回退仍由主 Agent 独立探针补齐。本说明不单独将整项 AC 标为全部完成。
- **完整容量与业务：** 新完整容量仍由主 Agent 执行；本人的恢复测试输入是工程合成夹具，不是 10,000 公告/50,000 包，也不是目标环境。原 compute1 容量失败记录不能被这些测试替代或覆盖。没有真实模型授权、真实业务样本、人类审阅或部署验收。

最终三项命令的实际结果为 `3 passed in 39.83s`、退出 0；本地 FunctionModel 调用 59，真实 provider 调用 0、真实费用 0。该命令发生在提交前的工作副本上，不能表述成 clean SHA 后重跑。写安全证据时重新计算的完整 backend+锁摘要与全部子进程记录的 application version 一致，两个测试源文件也保持最终测试时 hash。具体归因、历史测试脚本错误和原始受控路径见相邻 JSON。

当前固定生产源文件 SHA256：

| 文件 | SHA256 |
| --- | --- |
| `backend/bidops/jobs/runtime.py` | `e7856d269c9b237efb217a927ee826af5a749f2be11cc451ae4ef54933932f54` |
| `backend/bidops/jobs/config.py` | `75a2deab2b102ef9c0e21b35c476846c14366d6796e7fda5a85f7e7951173b4a` |
| `backend/bidops/qualification/api.py` | `fd4a79935d349abac45718a626482dd9d699b02a23d96893b836028898bfe46e` |

**最终判定：限定生产增量 no blocking findings；未新增生产行为或修改仓库。**
