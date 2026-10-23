# 原生队列当前文档与报告归因独立复核

结论：暂存 DURABLE-RUNS.md 新增的 Jobs 默认关闭、计算默认1且上限2、原生计算队列与同值 Semaphore、父／子显式恢复队列、Jobs enabled 时同步409和页面 fail-closed 描述，与干净代码 `69560429745417e34113d0877d75a62d7eb8f37b` 一致。没有发现这些新增描述需要撤回。

本次只读核对文档和代码，未运行报告生成器、测试、负载或 CI，未修改仓库。6956042 完整负载及后续修复提交 CI 没有在本次核对中确认完成，不预判它们通过。下列行号对应本次读取的报告模板快照；协调者正在更新 work 文件，已修正项可据最终文件关闭。

## 与生产代码一致的部分

- `backend/bidops/jobs/config.py:7–9`：enabled=false、max_active_jobs=100、compute_concurrency=1，范围1–2。
- `backend/bidops/jobs/runtime.py:106–110,383–410,462–516`：实际 Semaphore 与计算队列 worker/global 均用 compute_concurrency；父队列受 max_active_jobs 限制。审核等待发生在计算子返回之后，不占实际计算名额。
- `backend/bidops/jobs/runtime.py:653–675`：恢复明确指定计算子队列和父队列，不依赖 SDK 默认内部队列。
- `backend/bidops/qualification/api.py:59–67`：身份及 agent:use 检查后，按 jobs.settings.enabled 拒绝同步，缺模型或 worker 未就绪不会回退。候选／历史未被该 gate 改写。
- `frontend/src/Screening.tsx:74–109,122`：只有明确 enabled=false 才开放同步；状态未知或请求失败不恢复同步；enabled=true 引导持久任务。
- `backend/bidops/jobs/runtime.py:38–63,131–153,252–277`：应用版本实际绑定后端全部 Python 字节、主锁和 durable/attachments 两个 overlay 锁；旧活动任务遇版本差异会取消并中断。固定任务设置来自 ScreeningSettings，不包含 JobsSettings.compute_concurrency。

## 发布前需修正的证据归因

1. **矩阵摘要把695实跑挂在后续修复 SHA 下。**

   `work/build_native_reports.py:73–79` 将 B09-09/10 顶层 code_sha 统一设为后续修复 SHA；`98–101` 对 B09-07 同样处理，并写“完成版本切换、回退和新完整本机复验”。JSON 的复合 code_sha_role 与 input_version 有解释，但 CSV 只输出一个 code_sha，Markdown 摘要不显示证据角色，读者容易认为四阶段升级和完整负载在后续修复提交上重跑。

   B09-07 摘要建议替换为：“6956042 上执行原生队列完整本机负载及 215fca7→6956042 升级／旧版配套备份回退；后续修复提交的 CI 与采集调度回归独立记录，未宣称在该修复提交上完整重跑负载或升级探针。”B09-10 摘要补：“恢复测试为提交前但后端／锁字节对应6956042；四阶段版本切换是 clean215fca7→clean6956042；后续修复 SHA 仅承载最终整合 CI。”

   结构上可分列 final_integration_ci_code_sha、full_load_code_sha、upgrade_old_code_sha、upgrade_new_code_sha、recovery_backend_application_version。至少应让 CSV／Markdown summary 保留这些角色，不能只依赖 JSON 的解释字段。

2. **后续修复 UAT 与 Actions tree 同一性须有新版本自己的记录。**

   模板 `225` 写“固定6956042及后续修复clean代码的UAT…分别实际退出0/2/2”，但本次读到的 clean-code-uat-cli.json 只记录 code_sha=6956042。`117,223` 写当前修复代码与 Actions checkout tree 完全一致；本次先读到的 code-ci-tree-equality.json 也只证明6956042与原 CI checkout 的 tree 相等，随后该文件在协调者更新中不再位于原位置。不能据这些旧记录替新提交作证明。

   最小修正：最终新证据就绪前只写“clean6956042 的 UAT 模板／空环境／空证据退出0/2/2”；后续修复保持待记录。最终实际执行后，以两份各带完整 code_sha 的记录呈现，保留原695记录。新 tree proof 应带最终代码 SHA、实际 checkout SHA 和 tree，生成时核对 proof.code_sha==fixed_ci.code_sha 且 content_equal=true。原695证明或新 CI 的总体 PASS 标签都不能代替该核对。

3. **四阶段段落的“账本不变”和“仅来源授权”超出探针直接观察。**

   模板 `176–181` 已明确旧215／新695、第一次 ENQUEUED／第二次 PENDING 和领域异常不等于 HTTP，建议保留。但 `178` 的“原9条响应与账本不变”应改为：“原9条离线 request/response/usage 收据及原结果元数据不变，费用屏障仍要求确认；未验证真实账本。”这9条 FunctionModel 收据的 ledger_id 为空，不应与另一组合成费用预留竞争测试混作同一证据。

   `177` 的“仅来源授权均不能跳过…”建议按真实执行顺序改为：“旧身份读取拒绝；只刷新合成身份仍因任务隔离拒绝；源未重授权时任务重授权拒绝；随后显式源授权与任务重授权后仅变 interrupted，再显式 resume。”原探针未在“所有源已授权但未任务重授权”中间点单独发起读取负测，不宜列为额外已执行用例。

   `70` 的 old_completed_result_receipts_preserved 建议命名 old_waiting_review_job_receipts_preserved，值仍9，避免误读成旧任务／人审已完成。

4. **报告组装门槛应核对版本与完成性。**

   `21–32` 已要求最终 CI、原失败、修复记录及容量路径不变声明，是合适的防提前发布方向。建议再核对修复记录、UAT 和 tree proof 对应最终 SHA；负载 completion、safe-report、复算和 manifest/provenance 对应 LOAD_SHA；completion 确认本次负载已结束。command_exit_code 按实际保留；进程自然结束、命令退出码与 gate 状态分列，不能由“预期结束／通过”反推实际结果。评估失败导致非0退出时也应保留失败，而不是断言必须0来隐藏它。

   `39` 的 canonical_plan URL 采用最终 SHA，而 hash 读取仍固定在695的工作树文件。建议按最终 Git 对象字节计算，或先明确证明两份计划 hash 相同再绑定 URL，避免稍后文档变动造成 hash／URL错配。

## DURABLE-RUNS.md 中应澄清的现有文字

5. **区分部署并发与固定核对设置。**

   暂存文档 `131–140` 泛称“计算设置改变会 job_settings_changed、应新建任务”，容易把新增 BIDOPS_JOBS_COMPUTE_CONCURRENCY 也算入每任务绑定。生产 fixed_settings() 只固定 ScreeningSettings。ADR-0008:50–51 明确同版本并发2回1可停机调整，待审历史无需重写。

   建议改为：“任务固定资格核对 ScreeningSettings 快照，其变化会 job_settings_changed。BIDOPS_JOBS_COMPUTE_CONCURRENCY 是部署队列和实际计算上限，不在该任务快照内。同版本调整1／2须先停 worker 再重启，由原生队列更新限制；历史不重写。后端或依赖字节改变仍触发应用版本保护，不能借调整并发恢复版本不匹配任务。”这与当前生产和 ADR 均一致，不需要生产代码改动。

6. **当前安装与版本绑定实际是主锁加两个 overlay。**

   安装段仅安装 durable overlay，打包段称“两份锁”，版本段仅列主锁和 durable，遗漏 attachments。application_version() 实际读取 attachments.lock，Dockerfile:15–24 同时保留及安装 durable、attachments 两个 overlay。

   建议沿现有带哈希方式补：

   ```sh
   uv pip install --python .venv/bin/python --require-hashes \
     --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
     -r requirements/attachments.lock
   ```

   基础 sync 后两个 overlay 都应恢复；绑定描述改为后端实际字节、uv.lock、durable.lock、attachments.lock。打包段改为“三份锁”，补 BIDOPS_ATTACHMENT_LOCK_PATH。无需修改锁。

7. **B06 尚待接入及缺模型无 worker 的沿用文字需要限定。**

   状态表和 `300–303` 仍写“后续B06尚待接入”，但当前 api.py:91 创建 ReviewService，jobs runtime 已消费正式复核事件，HUMAN-REVIEW.md 也描述现有接线。建议改为：“正式人工流程见 HUMAN-REVIEW.md，须显式启用并满足 reviewer角色、assignment及全部来源权限；review_fixture仍仅工程测试。现有工程接线不等于真实人工业务验收。”真实 IdP、材料、账本、质量继续 BLOCKED。

   “缺模型不启动任何执行worker、allowed_actions=[]”只准确覆盖未启用来源 worker 的历史读取模式。JobsService.start() 可因 ingestion 独立启动共享DBOS，read() 的 cancel 动作看 started，而状态写入口还核对资格配置。建议文档限定“不启动资格计算；来源 worker 可独立运行，操作按当前服务端动作和准入结果”，不要对组合模式作过强承诺。本次只静态分析此组合，未执行探针，也不建议因此扩张当前冻结生产修复。

## 应保留的正确边界

- 模板 `137,160,173,176,180,223,235` 已区分基线、695负载、提交前字节对应、旧／新版升级、原CI失败与后续修复；尤其235明确新应用版本不同、未在新提交完整重跑容量，应保留。
- 旧836 actual1/target2 FAIL 仍应保留。真实目标机／供应商容量不被本机合成结果替代；B02-06及G01/G03/G08仍阻塞。
- 四阶段原私有证据与公开白名单分离；后续修复 CI 不改贴为旧升级进程或695负载的代码。

以上为待整合修正建议，不是对未完成运行的最终验收裁决。
