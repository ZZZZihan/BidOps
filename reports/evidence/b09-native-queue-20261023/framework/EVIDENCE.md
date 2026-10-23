# DBOS 3.3.0 原生队列 / SQLite 隔离可行性实验

实验支持保留 SQLite 的技术可行性：单进程内使用 DBOS 原生父队列与 calculation 子队列，计算队列的 worker/global 并发均设为 2，六个场景均通过。三个计算任务中实际只有两个重叠，另一个在原生队列中等待；三个父任务停在审核等待时，第四个计算任务仍可开始。本证据不把原 B09 容量 FAIL 改为 PASS，也不证明真实模型或目标部署已达到容量指标。

执行环境为 DBOS 3.3.0 / Python 3.12.12 / SQLite 3.50.4，SQLite journal_mode 实际为 delete。每个场景独立 SQLite 系统库，同一时刻仅有本实验一个 worker 进程；没有应用数据库、真实身份、材料或模型。worker 启用 socket 连接拒绝、最小子进程环境并关闭 OTLP/admin server，模型与真实网络调用均为 0、费用为 ¥0。没有编辑或提交任何仓库。

实际完整执行时间（UTC）：2026-10-22T22:47:29.375318 至 2026-10-22T22:48:27.170562。先前 baseline run-01 也通过，原件保留；run-02 没有覆盖 run-01。

| 场景 | 状态 | 实际计算峰值 / 原生 PENDING 峰值 | 两个及以上计算重叠秒 | 关键观察 |
|---|---|---|---|---|
| baseline | PASS | 2 / 2 | 1.400051500 | 第三个子任务原生 ENQUEUED；三个父任务待审时第四个子计算启动 |
| cancel_default | PASS | 2 / 2 | 3.998443834 | 取消回执后实际 step 又运行 3.980592834 秒；父子最终均 CANCELLED |
| cancel_preemptible | PASS | 2 / 2 | 3.971509792 | 取消回执后 0.973171625 秒 coroutine 被取消；父子未复活 |
| restart_active | PASS | 2 / 2 | 4.016978250 | SIGKILL 时未完成的 a/c 各重做一次（总调用 2），已完成 prepare 各仅 1 次 |
| restart_cancelled | PASS | 2 / 2 | 4.022827292 | 已取消 a 重启后不再执行；未完成 b 总调用 2，费用未知窗口不能据此释放 |
| restart_waiting | PASS | 2 / 2 | 1.400454000 | 三个已完成子计算均只执行 1 次；待审重启无新增计算，随后审核可完成 |

原生接口为 DBOS.register_queue、DBOS.enqueue_workflow_async、DBOS.recv_async、DBOS.cancel_workflow_async(cancel_children=True) 与 DBOS.step；没有实现自制队列、worker 调度器或重试引擎。父队列名 probe_parent_v1 的 worker/global 均为 16，计算队列 probe_calculation_v1 的 worker/global 均为 2，polling_interval_sec=0.025。SQLite queues 表的 concurrency 列代表 global 上限，worker_concurrency 为每 worker 上限。workflow_status 的 workflow_uuid/status/queue_name/executor_id/application_version/parent_workflow_id 与 operation_outputs.child_workflow_id 核对了真正的父子关系。baseline 有 4 个唯一子关系，其余场景各 3 个；operation_outputs 中 enqueue/get_result 可重复记录同一关系，未把这些行计为独立子任务。

复跑前将 B10_VENV_PYTHON 指向包含锁定 DBOS 3.3.0 的解释器，并选择不存在的新输出目录：

```sh
"$B10_VENV_PYTHON" work/native-queue-probe/run_probe.redacted.py --output work/native-queue-probe/run-03
```

本次实际运行的是 run_probe.py（解释器绝对路径固定于本机），命令参数为 --output work/native-queue-probe/run-02，未指定 --cases，执行了全部六项。run_probe.redacted.py 是公开路径投影，唯一行为差异为从 B10_VENV_PYTHON 取解释器；该投影本身未另行执行，不能替代原执行脚本 hash。worker.py 与 run_probe.py 的实际 SHA256 已记录于原 summary 和 safe-result。分析命令为同一解释器执行 work/native-queue-probe/analyze_probe.py，仅只读既有 SQLite/事件，不重跑任务。

需要保留的边界：默认非 preemptible step 的取消不是立即停止，不能只看原生 CANCELLED 就假定物理执行已经结束；preemptible 的本次约一秒延迟也不是外部供应商请求一定中止的保证。SIGKILL 后未完成 step 会重放，外部请求未知成本和持久尝试上限仍须保守保留。父任务仍占用有限的父队列 16 个槽，只是不占 calculation 的 2 个槽。两个上限在同一 worker 同时设为 2，本次没有独立证明多 worker/global 仲裁、跨机器故障切换或双进程重叠恢复。asyncio.sleep 模拟协作式 I/O 等待，不代表 CPU 吞吐、真实供应商容量或 SQLite 写冲突负载。未断言 FIFO 顺序。

初始接口核查中 Queue.enqueue_workflow 与私有 handle 导入的错误假设已被锁定 SDK 拒绝，原检查失败摘要保留在 sdk-interfaces.json；实际实验改用公开接口。这不是产品测试通过记录，也不推出必须迁移 PostgreSQL 或无需迁移的结论。业务接入后仍需原冻结负载与取消/恢复/预算整体验收。

公开证据仅限 evidence-index.json 的 allowlist。原生数据库、完整采样和 worker 日志在各 run 目录保留作本机复查，不递归公开；原 controller 本机路径也不进入公开白名单。所有任务子进程均已结束，SIGKILL 场景为旧进程退出 -9 后再启动新进程，其他 worker 正常退出 0。
