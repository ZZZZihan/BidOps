# SQLite 原生计算队列：独立技术复核

复核结论：在下述范围内，未发现需要修改生产代码的阻塞。当前固定代码是 `69560429745417e34113d0877d75a62d7eb8f37b`，PR 为 https://github.com/ZZZZihan/BidOps/pull/26 。本结论支持继续对该代码执行统一整合验证，不等于完整容量通过、企业业务验收通过、已部署或业务 RTO 达标。

本记录由未参与本次生产增量实现的子 Agent 整理。请求配置为 GPT-6.1 Sol / ultra；本会话未提供足以独立核验实际模型与推理档位的信息，故只记录请求配置。没有创建下级 Agent。

## 1. 版本及执行归因

- 只读检查时，目标工作树 HEAD 为上述完整 SHA，`git status --porcelain=v1` 无输出。
- 比较基线为 `215fca737e0e466a9781cd020b6b9d3b89e4b43f`。本次阅读该基线到固定 SHA 的生产增量，以及新恢复测试和升级实验原件。未修改仓库、Git、运行服务或负载进程。
- 独立实际执行的 **16 项定向测试发生在提交前 WIP**：当时 HEAD 仍为 `215fca737e0e466a9781cd020b6b9d3b89e4b43f`，叠加本次实现。实际结果为 `16 passed in 38.90s`，退出 0。当前固定 SHA 的增量随后逐项静态核对；这不改变原测试的版本归因，不能表述为“在干净 6956042 上独立执行了 16 项”。
- 新增三项恢复测试是本次**独立代码/断言审阅**，未重复运行。实现 Agent 的执行成绩由其原日志承担，本记录不把他人执行列作自己的实跑。
- 独立 DBOS 六场景框架实验是另一个已实际完成的隔离实验，使用单独脚本和 SQLite，不是 BidOps 全业务测试。其证据位于 `work/native-queue-probe/`。
- 本次未重复全后端、浏览器、完整容量或跨版本恢复实验。完整容量仍由主 Agent 的统一运行提供结论。

16 项测试的实际命令如下。`$REPO` 指本次 native-compute 工作树，`$SESSION` 指本会话目录；仅路径投影为别名，原命令在工具执行记录中保留。

```sh
cd "$REPO"
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider   --basetemp="$SESSION/work/native-queue-probe/review-test-tmp"   tests/test_jobs_compute_queue.py tests/test_jobs_sync_admission.py
```

该 pytest stdout 在工具会话中观察到；没有另存一份可称为“原始 pytest 日志”的独立文件。本复核记录是事后总结，不能替代原始执行记录。

## 2. 生产增量与具体行为

| 文件位置（固定代码） | 复核行为与结论 |
| --- | --- |
| `backend/bidops/jobs/config.py:9` | `compute_concurrency` 默认 1，仅允许 1 或 2。没有因配置新增而默认开放两个计算槽或任意并发。 |
| `backend/bidops/jobs/runtime.py:106-110,383-412` | 原生队列负责可恢复准入；本进程 `asyncio.Semaphore` 额外包住整个 calculation 生命周期，防止原生取消释放队列槽、但旧外呼仍在返回时实际计算数超过上限。`ACTIVE_MODEL` 用 token 在 `finally` 中恢复，不将并发任务 guard 存到共享可变全局实例。 |
| `backend/bidops/jobs/runtime.py:462-487,505-517` | 父 workflow 原生 enqueue 固定 ID 的 calculation 子 workflow；父等待子结果、再等待人审。calculation 队列的 worker/global concurrency 都为配置值；父队列另有 `max_active_jobs` 上限。父等待审核释放计算槽，但仍占有限父队列容量，不声称无限审核等待。 |
| `backend/bidops/jobs/runtime.py:143-158,639-679` | 执行仍核当前功能、模型配置、身份/来源及固定应用版本/设置。取消先持久化业务停止意图，再级联 native cancel；SDK 失败保留可重试停止意图。恢复子任务显式指定计算队列，恢复父任务显式指定父队列，避免 SDK 默认内部队列丢失计算准入。没有放开旧 application_version 自动运行。 |
| `backend/bidops/qualification/api.py:59-67` | 当前身份、权限和 CSRF 检查先于持久任务准入。jobs 已启用时同步 checklist 入口返回拒绝，引导持久任务；worker 未启动、模型未配置也不会回退到同步调用。jobs 明确关闭时保留原有离线同步行为。 |
| `frontend/src/Screening.tsx:69-109,122` 与 `frontend/src/App.tsx:41-44` | 状态未知或读取失败不能当作 jobs 关闭；只有明确 `enabled=false` 才开放同步表单。401/403/404 清私有状态；服务端拒绝持久准入时跳转相关入口，不自动重新提交。导航接到已有持久任务页面。此项是静态审阅，非新增独立浏览器实跑。 |
| `backend/bidops/operations_acceptance/load.py:389-397,464-469,553-561` | 接纳固定 manifest 的 1/2 计算槽，初始化与实际任务服务均使用同一值；没有把目标 2 改成 1，也未放宽其他容量阈值。新旧 manifest 原件分存。 |
| `backend/bidops/uat/evidence.py:16-22` | 最低 AC 集更新到 73 项、上游 67 项；旧 gate 保留，未知 B10 项仍须升级合同。没有通过删除既有门槛使 readiness 变绿。 |

当前固定代码的关键 Git blob（用于和提交前所读增量对照）：

```text
jobs/config.py          29ddda3a741991241683208cedd1e62f66deb466
jobs/runtime.py         f656558139c88b9485153246303bd2df24609cb7
qualification/api.py    5ee0cb91f1022bf662e55250a1705693cab002f2
operations_acceptance/load.py fb6b9cccab1973bd964693ad769405c0f68f7906
uat/evidence.py          457930c467d63c948899eb8512f88b7837520720
frontend/src/App.tsx     c205bed603fd4736d6dcf43ca80e74ecf7e8b1b8
frontend/src/Screening.tsx 6695bd19617ce1faf8cb12500dc7d2c9e313f827
```

16 项用例覆盖了：原生 2 槽和审核等待释放计算、取消中的实际外呼仍占物理槽、授权恢复回原计算队列、不支持的并发配置、jobs 启用但未启动/缺模型时拒绝同步回退、jobs 关闭的原路径、身份/权限/CSRF 的拒绝顺序、候选与已存同步历史仍可读。参数化计数包含在实际 16 项中。

已指出的文档问题只有旧、新容量 manifest SHA 标注混淆；固定代码中已经更正。旧 manifest SHA 为 `bd82c7ab050a53610742515d4af082140f6fce5593b203cabaa4e847ffb4a170`；新 manifest SHA 为 `8d232aa52fdc5b60bbdf32c258aaa964585b3faf2e5b4b50f7d75349a00c043c`。没有因此修改运行时代码。

## 3. 三项新增恢复测试及 fixture 的独立审阅

审阅文件：`tests/test_jobs_compute_recovery.py` 和 `tests/fixtures/jobs_compute_worker.py`。未发现通过放宽费用、恢复隔离或输出唯一性断言取得通过的情况。

### 3.1 两在途、一排队后的 SIGKILL

`test_jobs_compute_recovery.py:89-159` 和 worker `:154-166,200-215` 实际设计为三个独立进程：第一次创建任务并在两次模型调用在途、原生子状态恰为 `2 PENDING + 1 ENQUEUED` 后自发 SIGKILL；第二次仅从既存 run ID 恢复；第三次重读已保存结果。第二、三进程没有重新提交旧任务。

测试要求 3 个业务 run 与 3 个独立 result，run ID 与 result ID 不混用，最终均 waiting_review；29 条 attempt/ledger 中恰为 **2 unknown + 27 response_received**。崩溃前两条 ledger 整行逐字段保持不变，原 reserved_cny 仍大于 0，已确认合成费用精确为 `0.00756`。每个 run 的 9 条确认收据和后续判断正文均检查自身 run 标记，拒其他任务标记，覆盖并发上下文串扰。第三次重读要求无新调用，业务 jobs/attempts/results/ledger 快照不变。

worker 的网络入口明确拒绝，`models.ALLOW_MODEL_REQUESTS=False`，仅构造本地 FunctionModel。模型入口在 await 前后核相同 guard 与 workflow，事件落盘并 fsync。fixture 显式采用每任务 20 次/120 秒界限以完成故障实验，且通过测试实例注入 synthetic shared-ledger 路径；这不是把正式容量的 12 次/90 秒参数换成更宽配置。账本内是合成计费记录，实际外部调用和实际花费为 0。

### 3.2 两个原生计算竞争唯一预算

`test_jobs_compute_recovery.py:162-178` 和 worker `:118-123,174-199` 将预算精确设为一次 reservation。竞争失败者必须在胜者外呼尚未返回时预算拒绝。胜者随后无收据失败，原 attempt=unknown、ledger=unconfirmed、reservation 精确保留；第三个新任务仍须预算拒绝。断言为 3 jobs / 1 attempt / 1 ledger / 1 provider invocation，两个 budget_exhausted 和一个 model_failed，没有因取消、未知响应或后续任务而释放费用。

### 3.3 原生 2 槽的 v4 配套恢复

`test_jobs_compute_recovery.py:181-257` 直接使用实际 v4 backup/restore，并在备份的 SQLite 中读取 calculation 队列 `concurrency=2, worker_concurrency=2` 和三个子任务映射。恢复后要求三任务全部 quarantine，Alice/Bob/admin 均不能直接读取；刷新合成主体并逐源重授权仍不能顺带释放任务。缺少/错误预算确认值下，新任务必须 budget_review_required，provider 调用计数为 0，旧 ledger 完整不变，原任务继续隔离。

该测试证明的是隔离、账本保守性和队列映射，不验证真实 SSO、真实费用对账、正确 ack 后的付费调用或完整业务恢复。没有把领域 ToolContext 伪称真实登录，也没有发送人工批准。

## 4. 独立框架实验对 SQLite 的证据

本子 Agent 在 `work/native-queue-probe/` 独立实际运行过六个隔离场景，固定 DBOS **3.3.0**、Python **3.12.12**、SQLite **3.50.4**，SQLite journal_mode 为 delete。父队列 worker/global=16，计算子队列 worker/global=2。只使用 SDK 的 enqueue、child workflow、get_result、recv、cancel 与恢复；没有自制队列。每次至多一个任务专属 worker 进程，旧进程退出后才启动下一代。

六场景均通过：baseline、cancel_default、cancel_preemptible、restart_active、restart_cancelled、restart_waiting。实测物理计算峰值和 native PENDING 计算峰值均为 2；基线两个计算重叠 `1.4000515s`，第三个原生排队；三个父等待审核时第四个计算可启动。原生父子关系同时查 `parent_workflow_id` 和 operation child ID。

取消不是立即终止外部动作：默认步骤在 cancel ack 后 **3.980592834s** 才返回，preemptible 步骤约 **0.973171625s** 后取消。该事实支持生产代码保留本地物理 semaphore，不能只靠 native CANCELLED 就复用所有实际资源。进程重启会重放未完成计算，已完成 prepare/等待审核检查点不重做；取消任务不复活。unknown reservation 仍须业务账本保守处理，不能从框架恢复推出外呼 exactly-once。

该实验反驳了“单进程 SQLite 原生队列不能配置 2 个计算槽，因此必须先迁 PG”的绝对判断；它没有证明多进程 global enforcement、CPU 吞吐、SQLite 争用、split-brain 或正式容量达标。

证据入口和当前摘要：

| 文件 | SHA256 |
| --- | --- |
| `work/native-queue-probe/evidence-index.json` | `db13b86b0ca72c503fb19990368a5424103a32ec11552c4bf9cb51621f91bbd7` |
| `work/native-queue-probe/safe-result.json` | `b861ab417ae2724e9987761eb2317e28ce4b37714a0d66c1ee37154f52e481c6` |
| 实际执行 `worker.py` | `58286f5a462c61dcb33ecb83946b4d3866514f8c185faf295b74281e38105151` |
| 实际执行 `run_probe.py`（本地原件） | `0cb0d6fb2b53b0ce3b9c775bf81e9a01c820388c6c7bda97f9d27146e6de9d7a` |

按该索引的明确 16 成员白名单取安全产物；原始数据库、完整日志及含本机路径的实际 controller 脚本不在公开白名单。`run_probe.redacted.py` 是路径投影，未另行执行。首次 baseline 和 SDK 入口探索中的失败记录均保留，没有只保留最好的一次。

## 5. 升级与原配套回退证据的独立核对

审阅 `work/native-queue-upgrade/REPORT.md`、`safe-summary.json`、原始脚本/命令/事件/版本/快照/索引，并通过 SQLite `mode=ro` 检查四个实例。未重新运行探针。

旧代码为 `215fca737e0e466a9781cd020b6b9d3b89e4b43f`，应用版本为 `8bd8c06b2b1d080abcc02cac9f11fc5de5567ea1b76cd5e1501d8ebaeec8a397`；新代码为本记录的固定 SHA，应用版本为 `744c15f446d6f4218bd8063a6ac8963472174c7416077f81c65636aa597ab6aa`。四阶段版本记录的 99 个 backend Python 文件摘要逐个对照各自 Git 提交内容相符；旧版两次回退使用旧代码，未通过伪造应用版本绕过检查。

支持的窄结论是：

1. 旧版生成一个真实 DBOS waiting_review 任务，9 条本地模型调用收据，人工仍 unreviewed。停 worker 后生成 v4 原配套备份。
2. 新版从独立停机副本启动时，将旧任务置 interrupted / job_application_version_changed，原父 CANCELLED；旧固定结果仍可读、版本不变，显式 resume 仍版本拒绝，新增模型调用 0。
3. 原旧代码加原配套备份恢复到新目录后，主体新鲜度清零、对象/结果/任务均隔离。仅更新合成主体不释放任务；须依次显式源授权和任务重授权。缺费用 ack 继续拒绝。匹配 ack 只在内存检查 token 合同，不表示实际费用账本已对账。
4. 显式旧版恢复复用原检查点，业务回到 waiting_review；仍为同一结果版本、9 次既存调用、0 次新增调用、unreviewed，没有伪造人审。

**第一次与第二次回退不可合并。** 第一次记录的停机原父为 **ENQUEUED**，业务已 waiting_review；其同名两份快照后来被第二次覆盖，原事件/命令/stdout 仍在，但剩余原事件/stdout 没有足够内容独立重证首次 ENQUEUED。本次将其限定为保留的探针作者历史观察，不能视作首次原生 PENDING 的证据。第二次从原备份再恢复到另一个新目录，`rollback-native-events.json` 的明确 PENDING 事件及 `rollback-native-stopped-snapshot.json` 可重查；其父队列为旧版 `_dbos_internal_queue`，不是新版 calculation 队列验证。报告已经披露覆盖事实，没有把第二次快照归到第一次。

本次只读实际追加核对：四实例 SQLite integrity_check 全为 ok；每个实例 1 条结果正文的 SHA 与 metadata 相符；9 条实际响应正文、结果正文及全部 agent_runs 行与旧版完全相同。九条响应正文整体规范摘要为 `3c82459c97e0ecbbe18036818da68fd3350bab81f9a6fc98c4c79047d7b83ae9`。原旧数据和原备份目录逐文件摘要仍与 original-hashes.json 一致。原始 evidence-index 的 **32 个文件摘要全部相符**。

四阶段是直接 Python 领域入口，401/404/409 来自 CompanyError.status_code，**没有真实 HTTP / ASGI 请求**。四个短进程的持续时间不等于业务 RTO。恢复仍停在审核等待，并未完成真实身份、材料、费用对账和人工审核的业务恢复。该单任务探针也不覆盖旧版本 queued/inflight 升级、新版创建任务由旧代码执行、真实外呼或新 2 槽容量。

## 6. 公开投影与保留原件

复核时发现原 `safe-summary.json` 的 phase_commands 含本机绝对路径，且原字段名把进程内异常称作 HTTP status；已反馈主 Agent。主 Agent 另增公共投影，未覆盖原件。本次已独立核公共文件摘要、保留原件摘要及路径扫描：均一致，三个公共文件无 `local user-directory prefix`、`/home/`、`file://`。公共投影明确非 HTTP 验收、首次 ENQUEUED 与第二次 PENDING，并声明不是独立可运行复现包。

**升级探针仅发布以下三个成员**，原 `evidence-index.json` 的旧 safe_public_projection 标签不能作为当前发布白名单：

| 相对于 `work/native-queue-upgrade/` | 字节 | SHA256 |
| --- | ---: | --- |
| `REPORT.public.md` | 6177 | `702e4029132c213fd78c2a049dca3e45bd47cda18412b798535b6f006a266838` |
| `safe-summary.public.json` | 11238 | `6f879e29f2c6c40397495f96c5656943b67297f4a707798714d41010519abcce` |
| `public-evidence-index.json` | 3635 | `31d121713a68a60ebd6a6bc74937fc1ebe2b1b50cf9cdbb02119443b402f070d` |

原 REPORT / safe-summary / evidence-index / scripts / stdout / database 仍保留本地，不能因本记录提到其文件名或摘要就递归打包公开。公共索引不自嵌哈希，使用上表或外层发布清单固定。

## 7. 未覆盖项与交付边界

本次没有独立执行当前干净 SHA 的全后端/浏览器/容量测试，没有独立重新运行三项业务恢复测试或升级探针，没有远端 CI 核验，也没有真实企业 IdP、材料、模型、费用对账或人审。完整容量、硬件等价性及最终 G 状态只取主 Agent 对固定代码的统一证据，本记录不预先判定 PASS。73 项 AC 与 67 项上游计数亦不能代替每项业务验收。

可以交付的结论是：生产增量未见技术阻塞；16 项独立 WIP 定向测试有实际通过记录；三项恢复测试的断言与声称边界相符；独立框架实验支持单进程 SQLite 保留原生 2 槽的可行性；升级/原配套回退证据支持上述有限路径且保留失败、覆盖限制和两次原生状态差异。实际网络/模型调用与实际花费均为 0。
