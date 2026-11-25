# CI 未 await 协程警告：有界只读复核

结论：**现有证据未证明实质业务阻塞，但真实警告仍未查明或修复，不能写成“零警告”或“已确认无害”。** 锁定 DBOS 3.3.0 存在一条与该警告相容的出队派发/关闭竞态路径；本次没有 allocation traceback、实际异常文本或新复现，不能断言它就是此次根因，也不能确认警告发生在 teardown。

CI 运行 `39874101082` 的后端日志记录 **2175 passed、1 warning、802.13s**。候选为 `76ce23720c14e9e66e583e4cc15ad95356f5e519`，GitHub 实际执行的 PR 合并树为 `0189e8154aa1f910018af7c9c383802670b19a51`。本次核查的 runtime、目标测试、durable lock 三个文件在这两个提交中的字节一致。三 job 全 PASS 是主 Agent 提供的执行记录；本复核只读取已保存后端日志，没有重新查询 GitHub。

警告关联 `tests/test_jobs_runtime.py::test_revoked_review_wait_replays_native_checkpoints_after_authorized_resume[request_materials-waiting_review]`，类型为 `RuntimeWarning`，消息是 `coroutine 'execute_dequeued_workflow.<locals>.start_workflow_task' was never awaited`。发出位置是 `dbos/_queue.py:690` 的异常记录语句。日志没有实际异常 `e`、协程分配堆栈或发生阶段；pytest 摘要输出时间不是已知的警告发生时间。日志中的 actions Node.js 弃用提示与这一条 pytest RuntimeWarning 分开。

该用例确实验证了：材料撤权后业务 interrupted、原生根工作流 CANCELLED；恢复授权前禁止 resume；重新授权后可恢复到 waiting_review；保存 usage.model_calls 仍为 9、result_id 不变，原 Agent 判断仍 unreviewed；旧版本重复 resume 被拒绝。用例通过说明这些断言没有失败，不证明所有后台派发线程均已清空。request_materials 分支本来就继续 `recv_async` 等待下一审核事件，并不要求根工作流终结。

可直接从锁定源码确认的路径：

1. `dbos/_core.py:1338–1355` 在异步出队时先构造 `start_workflow_task()`，再交给 `submit_coroutine`。该协程名与警告完全一致；`_queue.py:690` 是捕获派发异常的记录点，不是创建点。
2. `dbos/_queue.py:704–744` 在循环开始和 polling wait 检查 stop event，claim 到 dispatch 之间没有另一次 stop 检查。队列 worker 已进入本轮派发时，单纯设置 stop event 不会立刻打断它。
3. 应用 `backend/bidops/jobs/runtime.py:701–704` 直接调用 `DBOS.destroy(destroy_registry=True)`。SDK 默认工作流完成等待为 0 秒；`dbos/_dbos.py:912–946` 先置 stop events、停止背景事件循环，之后才 join 背景线程。队列管理线程在 `_queue.py:906–918` 进一步 join 各队列 worker。
4. `dbos/_event_loop.py:39–78` 取消并 gather 事件循环任务后停止/关闭循环；`:100–122` 对无循环和本循环线程调用有显式 `coro.close()`，但 `run_coroutine_threadsafe` 的异常路径没有相同清理。本机 Python 3.12.12 的 `asyncio/tasks.py:931–952`、`base_events.py:543–545,842–852` 表明向已关闭循环提交会同步抛异常，不会接管或关闭传入协程。CI 使用 Python 3.12.3，本次没有逐字节核查其标准库。

因此，一个具体但**未实证归因**的解释是：出队线程已开始 claim/dispatch，另一线程关闭循环；前者随后构造协程并提交失败，未 await/close 的协程在异常处理路径被释放并发出警告。现有静态源码允许这种时序。它尚不能证明本次就是该时序，也不能证明警告只可能发生在 teardown。第一次 destroy 正常完成后 SDK 全局实例已清空，fixture 里的第二次 destroy 本身也不足以证明根因。

若该时序发生，claim 与真正启动之间可能中断。当前日志没有受影响工作流 ID 或原生行的最终快照，因而既不能证明丢任务，也不能保证每一个被 claim 的任务都已恢复。**本次“未建立阻塞”的依据是缺少实际行为损坏证据，而不是单凭 PASS 忽略警告。** 若后续发现未恢复的 claim、持续派发失败、关闭后线程泄漏，或权限/预算/取消/恢复断言失败，应重新按实质问题处理。

本次仅完成日志与源码审阅，未重跑测试、负载或退出复现；未改变仓库、依赖、框架或 warning 过滤。保留警告和原日志，后续若再次发生，再针对同一关闭窗口收集 tracemalloc allocation traceback、实际异常文本和线程/原生状态。没有仅为消除警告升级框架的证据依据，也没有证据支持修改当前业务语义。

完整相对路径、行号、文件 SHA256、证据层级和限制见同目录 `native-ci-warning-review.json`。本机 SDK 与锁和 CI 安装行均为 DBOS 3.3.0，但没有独立取得 CI wheel 文件逐字节核对。请求模型为 GPT-6.1 Sol / ultra，实际分配不能独立核验。公开报告不含本机绝对路径、业务标识、凭据或原始业务正文。
