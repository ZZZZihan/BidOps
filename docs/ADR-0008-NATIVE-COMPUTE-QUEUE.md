# ADR-0008：单进程内使用 DBOS 原生计算子队列

日期：2026-10-23。范围：B09 后续独立基础设施 PR，新增 B09-09/10；依赖 B10 的整合版本。
工程验收由实际运行报告决定；目标机器、真实模型与企业验收仍须各自证据。

## 依据与选择

原完整容量实测在 `836ccbfad779c4b6e52dd1802dd0877989dbe774` 上观察到计算峰值1、
目标2及零双任务重叠。原始容量 manifest、4,200条操作和失败报告保持不变。
瓶颈是 `JobsService` 内显式计算 Semaphore=1；更换硬件不能解除这个代码约束。

锁定 DBOS 3.3.0 的 `register_queue` 支持每 executor 和全局 workflow 并发限制；
SQLite 后端以 IMMEDIATE 事务和 busy timeout 串行短写，不要求整条异步 workflow 串行。
原生 `enqueue_workflow_async` 保留父子关系，父任务可独立等待审核。首个隔离实验实际观察
两计算重叠、第三任务 ENQUEUED，并在三个父任务待审时执行第四计算。
这支持先验证单进程内的原生计算队列，尚不支持多 worker 或生产容量结论。

因此保留业务 SQLite、私有 DBOS SQLite、固定 executor、单 ASGI 进程和 SingleWorker 锁；
不新增数据库服务。PostgreSQL 仍是生产、多实例和 HA 的后续候选。ADR-0005 的历史
“所有容量扩大先 PG”在本项有界单进程工程范围内修订；ADR-0007 的历史失败不修改。

## 执行与兼容契约

- `BIDOPS_JOBS_COMPUTE_CONCURRENCY` 默认1，只接受1或2。父队列继续受
  `max_active_jobs` 限制；新计算队列 `bidops_qualification_calculations_v1` 的
  worker/global concurrency 均取该值。父任务待审不占计算名额。
- 计算协程仍持有同值 Semaphore，直至实际退出。已持久取消的 provider 请求可能还在
  返回；不能把框架状态 CANCELLED 当作该进程已没有在途调用。两道限制各自有意义，
  排队、持久化与恢复继续完全由 DBOS 负责。
- 初次提交用原生子队列；人工恢复时显式指定计算/父队列。SDK 3.3.0 的 `resume_workflow`
  若省略 `queue_name` 会把任务放到内部队列，不能依赖其保留旧队列。
- Jobs 启用时同步 `POST /api/screening/checklists` 在身份/权限检查后返回409
  `screening_use_durable_jobs`。未就绪和缺 provider 不触发同步回退。页面读取 enabled
  状态并引导持久任务；状态未知时暂停同步提交，候选和历史保持可用。Jobs 关闭保留原接口。
- 每个计算有独立 `Execution`、`DurableModel`、ContextVar、步骤指纹和唯一结果；共享费用
  仍以现有业务事务原子预留，取消/重启不释放已确认或未知费用。没有新增真实模型授权。
- 不修改数据库 schema、业务 ID、已有队列记录、原始正文或 v4 typed backup 格式。
  新队列由框架注册。原有停机双库备份、来源闭包、retention、权限与费用屏障继续适用。
- 默认关闭的历史 Demo 有独立资源策略，不纳入本次资格计算容量声明；本轮负载明确停用
  Demo 和增量采集。若部署同时启用其他耗资源路径，必须重新冻结组合负载并测量。

## 切换与回退

1. 停止新提交，保留当前完整代码/依赖版本，停止 worker/ASGI；由现有独占锁覆盖 v4
   配套业务库、DBOS库、原始/私有文件备份，并验证备份能恢复。不能只复制业务库。
2. 在同一数据副本上启动新版本，默认计算仍为1；如需复验2，显式写配置。新代码完整
   application_version 与旧版不同；旧未完成任务必须中断并提示版本变化，不能改数据库
   版本号或用新实现冒充旧检查点。历史记录保留。需要继续旧任务时使用对应旧代码和
   受控数据副本，经过当前权限/费用核对；也可由获授权用户创建明确的新任务。
3. 同版配置2回到1时先停止 worker，修改配置后重启，由原生队列更新持久配置；不热改
   Python信号量或运行中的调度状态。物理计算限额启动即生效。待审历史无需重写。
4. 回退到旧代码前停写，保存升级后全部历史，再使用对应旧代码与升级前配套备份恢复到
   新隔离目录。不得仅回退二进制后强行执行新版队列记录，也不能删除新历史使回退成立。
   恢复后的权限、旧会话、review epoch、任务隔离、账本核对屏障均保留；最新授权未知时
   私有访问关闭，未知费用不重置。该过程不是生产发布授权，也不自动通过业务 RTO。

本地新旧版本切换、真实两计算 SIGKILL、原生恢复队列、费用竞争、v4恢复以及浏览器结果
分别记入后续 B09 报告；任一必需项未完成时相应 C 保持 NOT_RUN/BLOCKED/FAIL。

## 容量复验与框架来源

新 [冻结档位](../evals/capacity-native-queue-v2/README.md) 仅更新标识与实际配置并发2。
原规模、2计算目标、预热、稳态、每客户端、请求比例、超时与错误率门槛逐字段保持。
完整新运行使用干净提交及新目录；失败、超时、未完成任务全部保留。开发机完整规模负载
只能证明本地工程行为；B09-02/07 的目标环境裁决继续保持独立。

本轮查阅官方资料并以实际安装版本源代码核对接口，不以当前官网替换锁定版本事实：

- [DBOS 数据库连接](https://docs.dbos.dev/python/tutorials/database-connection)：SQLite/PG支持范围；生产推荐PG。
- [DBOS 配置](https://docs.dbos.dev/python/reference/configuration)：系统数据库与 application version。
- [DBOS 队列](https://docs.dbos.dev/python/tutorials/queue-tutorial)：持久队列与并发控制。
- [DBOS CLI](https://docs.dbos.dev/python/reference/cli)：`dbos migrate` 是系统 schema 初始化/更新，不能误称为 SQLite 到 PG 的历史迁移。

锁定 SDK 的 `_dbos.py`、`_queue.py`、`_sys_db_sqlite.py` 与 `_sys_db.py` 分别用于核对
队列注册/子提交、SQLite短写、取消/显式resume和启动恢复保留队列。没有修改依赖锁。
