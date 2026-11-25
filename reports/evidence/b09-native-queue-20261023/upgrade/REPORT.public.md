# 原生计算队列升级与回退：发布用工程证据

本机离线工程探针通过了旧版启动保护、原版配套备份回退以及身份、对象、任务和费用屏障检查。四个独立执行阶段均退出 0。它没有验证真实企业材料、真实 SSO、人审、外部模型、目标机容量或业务恢复时间。

本探针直接调用 Python 领域服务与仓库函数，**没有发送 HTTP 请求，也没有执行 ASGI／HTTP 路由验收**。下文的 401、404、409 均为捕获的 `CompanyError.status_code` 属性，不是实际网络响应。

## 版本、入口与命令投影

- 旧版及回退代码：`215fca737e0e466a9781cd020b6b9d3b89e4b43f`；真实应用版本 `8bd8c06b2b1d080abcc02cac9f11fc5de5567ea1b76cd5e1501d8ebaeec8a397`。
- 新版代码：`69560429745417e34113d0877d75a62d7eb8f37b`；真实应用版本 `744c15f446d6f4218bd8063a6ac8963472174c7416077f81c65636aa597ab6aa`。两个版本执行时均观察到干净工作树，没有伪造应用版本。
- 依赖：DBOS 3.3.0、Pydantic AI Slim 2.59.0、SQLAlchemy 2.1.6。各进程使用对应版本已有的 `.venv/bin/python` 和显式 `PYTHONPATH`。
- 入口为 `JobsService`、`JobsRepository`、company authorization 及 budget-check 函数。进程持有实际 SingleWorker 文件锁；停 worker、释放锁后才做 v4 配套备份。HTTP transport 与 socket connect 均拒绝，`models.ALLOW_MODEL_REQUESTS=False`，仅允许本地 FunctionModel 合成输出。

命令中的路径只作脱敏投影，定义如下：`$OLD_WORKTREE` 为旧代码的隔离 checkout，`$NEW_WORKTREE` 为新代码的隔离 checkout，`$PROBE_DIR` 为本次新建的会话探针目录。它们不暴露本机账户或目录。这些命令记录实际调用形式；私有原脚本、输入、数据库和日志不随公开文件发布，因此这份投影不是独立可运行的复现包。

```text
PYTHONPATH="$OLD_WORKTREE/backend:$OLD_WORKTREE/tests" "$OLD_WORKTREE/.venv/bin/python" "$PROBE_DIR/probe.py" old
PYTHONPATH="$NEW_WORKTREE/backend:$NEW_WORKTREE/tests" "$NEW_WORKTREE/.venv/bin/python" "$PROBE_DIR/probe.py" upgrade
PYTHONPATH="$OLD_WORKTREE/backend:$OLD_WORKTREE/tests" "$OLD_WORKTREE/.venv/bin/python" "$PROBE_DIR/probe.py" rollback
PYTHONPATH="$OLD_WORKTREE/backend:$OLD_WORKTREE/tests" "$OLD_WORKTREE/.venv/bin/python" "$PROBE_DIR/probe.py" rollback-native
```

完整的命令投影、UTC 起止、退出码、执行脚本及 stdout/stderr 摘要保存在 `safe-summary.public.json`。执行时清除了供应商、BIDOPS、DBOS、OTEL 环境前缀，显式禁用网络且不写字节码；未公开环境值。命令耗时不是业务 RTO。

## 实际观察

1. 旧版真实 DBOS 任务进入 `waiting_review`，原生父 `PENDING`、计算子 `SUCCESS`，保存 9 条离线模型调用收据，人工状态 `unreviewed`。其他 owner 的服务读取抛 `CompanyError(code=job_not_found, status_code=404)`。
2. 停机后生成 v4 配套备份，共 6 个文件，闭包包含业务库、DBOS 系统库和所需内容。新版从独立停机副本启动，将旧任务置为 `interrupted / job_application_version_changed`，原生父为 `CANCELLED`、计算子仍为 `SUCCESS`。旧固定历史可读、结果版本不变；显式恢复执行抛 `CompanyError(code=job_application_version_changed, status_code=409)`；新增模型调用为 0。
3. 旧版使用原配套备份恢复到新目录后，2 个私有对象、1 份结果、1 个任务隔离，主体新鲜度清零、原授权去除。旧身份服务读取抛 `CompanyError(code=current_identity_required, status_code=401)`；仅刷新合成身份投影后仍因隔离抛 `job_not_found / 404`；源未授权时任务重授权抛 `company_object_not_found / 404`。这些均是进程内异常观察。
4. 显式重新授权源、再由 owner 重新授权任务，仅进入 `interrupted / job_restored_needs_resume`，没有自动执行。显式旧版恢复复用原检查点，累计仍 9 次调用、新增 0 次，结果版本不变；未调用 `review_fixture` 伪造批准，人工状态始终 `unreviewed`。
5. 第一次回退的业务状态已回到 `waiting_review`，但停机时原生父尚为 `ENQUEUED`。第二次从原备份恢复到另一个新目录，补强确认原生父真正进入 `PENDING`、子仍为 `SUCCESS`，新增调用仍为 0。两个观察分别保留，没有把第一次写成已经 `PENDING`。旧版父队列观察为 `_dbos_internal_queue`，不将其归作新版原生计算队列的验收。
6. 恢复后无 acknowledgement 的费用准入在离线恢复前后均抛 `BudgetReviewRequired`。只在内存检查匹配分支，没有配置付费运行确认值、没有声称账本已被人工核对或丢失费用已补回；这是零新调用的离线检查点恢复。
7. SQLite 只读快照显示完整性检查为 `ok`，原结果元数据及 9 条 request/response/usage 收据未改变，DBOS operation 计数仍为 24。原旧版数据与原配套备份在升级及两次恢复后逐文件摘要相同。

## 证据范围与保留

公开白名单仅为本文件、`safe-summary.public.json` 和 `public-evidence-index.json`。发布索引提供公开文件摘要及保留原件摘要，未复制数据库、raw/private 目录、原始模型输出、恢复确认值、运行对象 ID 或完整本机日志。原件及旧索引保留不覆盖；旧文件中曾使用的“可公开”标签由本次明确白名单取代。

首次脚本导入错误发生在创建数据前，原始错误保留，不混入四个成功阶段。第一次回退的两份同名快照被第二次覆盖后，已按第二次阶段正确归档；第一次原事件、命令与输出仍保留，不把第二次快照归作第一次。原始资料的这些限制不会因发布投影被抹除。

本探针使用明确标注的合成领域身份投影，不能替代真实 SSO。新版配置计算槽为 2，但单任务探针不能证明两个计算并发或容量通过。回退仅证明原旧代码加原配套备份的路径，没有声称旧版可执行新版创建的任务。实际企业 RPO/RTO、材料许可、业务人审、账本及目标机验收仍未验证。
