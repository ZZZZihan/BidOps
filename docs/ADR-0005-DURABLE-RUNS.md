# ADR-0005：采用 DBOS 承载持久任务与框架恢复

日期：2026-10-23。状态：接受，限单机、单 worker 的受控试用；生产高可用方案尚未验收。

## 决策与范围

B05 采用 **DBOS 3.3.0 + Pydantic AI 2.59.0 的 `DBOSDurability` capability**。首轮运行边界为一台主机、一个 DBOS worker、一个固定 executor，以及独立私有 SQLite 框架状态库。现有业务 SQLite 继续保存任务身份、输入版本、授权、费用与唯一结果。队列、步骤记录、调度和恢复由成熟框架提供，业务表不承担通用任务领取、重试和回放职责。

本 ADR 关闭 B05-01 的后端选择和最小恢复实验。探针只使用进程内 `TestModel` 合成内容，真实模型调用、付费请求和采购来源请求均为零。它不证明真实模型效果、人工验收、所有宕机计费窗口、业务取消或整套备份恢复已经完成；后者分别属于 B05-02 至 B05-07。

## 候选和选择依据

| 候选 | 与冻结依赖的兼容性 | 数据库及进程要求 | 已有实际证据 | 本轮决定 |
| --- | --- | --- | --- | --- |
| DBOS 3.3.0 | Pydantic AI slim 2.59.0 元数据声明 `dbos>=2.10.0`；DBOS 要求 SQLAlchemy asyncio>=2.0.43，兼容冻结的 2.1.6 | 嵌入 Python 进程；支持 SQLite 和 PostgreSQL；进程内只有一个 DBOS singleton | 精确带哈希闭包安装、官方 capability 导入、真实 SIGKILL 后自动恢复及完成后重启读取 | 选择，符合当前受控单机试用边界 |
| Temporal SDK 1.34.0 | 原实验安装并核对 Pydantic AI 2.59.0 的 `temporalio>=1.27.0`、集成类导入 | Python worker 另需连接 Temporal Service；正式自托管还需服务及存储运维 | 仅 SDK 安装、元数据和集成 API 核查；没有启动 Temporal Service，没有恢复实验 | 暂不选择；没有证据称其恢复能力失败 |

比较依据保存在 [原实验说明](../reports/evidence/b05-backend-selection/historical-experiment.txt) 与 [原环境兼容记录](../reports/evidence/b05-backend-selection/historical-compatibility.json)。原比较环境同时安装了两家 SDK；最终 DBOS-only 实验及交付锁均不需要 Temporal。文档核查没有代替实际数据库和 worker 实验。

DBOS 官方把 SQLite 定位为开发、测试用途，并建议生产使用 PostgreSQL；因此这里的 SQLite 是有意限定的工程试用选择，不能据此承诺生产容量、多 worker 或高可用。该提示也保留在原始 worker 输出中。

## 精确依赖与复现

[requirements/durable.in](../requirements/durable.in) 是独立 DBOS overlay，[durable.lock](../requirements/durable.lock) 包含 28 个精确版本及全部下载 SHA256。它保留项目原有 OpenAI 依赖，不替换主项目环境；20 个共享包与冻结 `uv.lock` 完全同版本，新增 8 个包为 DBOS、croniter、greenlet、psycopg、psycopg-binary、python-dateutil、six 和 tzdata。

主锁 SHA256 为 `92ceebd17f2e98b0075eac8f13c9108b2686635e1ece5aa4d5f172de10289b27`。原安装已实际执行 `--require-hashes`，见 [安装输出](../reports/evidence/b05-backend-selection/historical-install.txt)。迁移仅调整依赖锁的来源注释，版本和 hash 集合保持不变；最终探针还会逐包核对已安装版本、overlay 与主锁的共享版本，不一致直接失败。没有修改 `pyproject.toml` 或主锁。

已有项目虚拟环境的复现方式如下。`install` 的语义是加装精确 overlay；不要用 `pip sync` 将项目环境裁剪成 28 包。需要新建隔离的探针环境时可只安装此锁；正常应用部署仍须先安装项目冻结依赖。

```bash
uv pip install --python .venv/bin/python --require-hashes \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  -r requirements/durable.lock
.venv/bin/python scripts/durable_probe/probe.py
# 或指定尚不存在的目录；若目录已存在，拒绝覆盖：
.venv/bin/python scripts/durable_probe/probe.py --output /tmp/bidops-b05-probe-new
```

依赖下载是显式安装动作；探针本身不会安装依赖或访问网络。未传 `--output` 时使用系统新临时目录，目录权限为 0700、进程 umask 为 077。SQLite、日志和运行中虚拟环境均不进入提交。探针拒绝覆盖已有输出目录；使用原脚本和同一环境即可新建另一份独立证据。

## 最终脚本的实际恢复结果

[probe.py](../scripts/durable_probe/probe.py) 启动真实子进程运行 [worker.py](../scripts/durable_probe/worker.py)。Agent、稳定名称和工作流在 `DBOS.launch()` 前注册，模型请求由官方 capability 包装为框架步骤，等待和结果函数使用 `@DBOS.step()`。

1. 第一个进程使用 `SetWorkflowID` 和 `DBOS.start_workflow` 提交。控制器同时观察下一等待步骤已经进入，并读取真实 DBOS `operation_outputs`，确认前一模型步骤有已持久输出、无错误，随后发送 SIGKILL。
2. 控制器释放合成等待条件。第二个进程只调用 `DBOS.launch()`，再 `DBOS.retrieve_workflow(...).get_result()`；没有二次提交 workflow。恢复由 DBOS 自己完成。
3. 第三个新进程读取已完成任务。控制器核对两个结果相同，并核对真实事件计数和最终数据库状态。所有关键检查使用显式异常，在 `python -O` 下也不会被关闭。

迁移后的脚本于 2026-10-23 02:14:09 至 02:14:21（UTC+8）实际执行通过，基线 HEAD 为 `3f04b045f0d029dadb4b882a293f31776400cc30`，`source_tree_dirty=true`；当时的交付文件 SHA 单独记录，不能把基线 HEAD 表述为包含这些未提交文件的最终提交。

| 观测项 | 真实次数或值 |
| --- | ---: |
| 第一模型步骤 `TestModel.request` | 1 |
| 第二模型步骤 `TestModel.request` | 1 |
| 被打断且未持久输出的等待步骤 | 2 |
| 最终结果步骤 | 1 |
| 第一个进程退出码 | -9（SIGKILL） |
| DBOS SQLite schema version | 123 |
| 真实模型、付费、采购来源请求 | 0 |

本次 `application_version` 是 worker 文件 SHA256：`1c00cd0a46907fedac684a20083f3983b216d911896282d644a3fa520fbd110d`；控制器 SHA256 为 `fe8f4e8d439f20e12c476a5cf08abf940b29df8ad9b997c28d4240e1aa3be4c9`。完整版本、主锁和 overlay SHA、时间、每个调用的 PID、检查点、数据库摘要及原始进程输出见 [迁移后结果](../reports/evidence/b05-backend-selection/migrated-run/report.json)。[证据索引](../reports/evidence/b05-backend-selection/evidence-index.json) 标记原始字节与脱敏字节 SHA。

原 DBOS-only 实验的 worker SHA 为 `bc42b06b6dbdd085c2f61509bbd0541d6fddb695e42e9e367eb616d7b0519b36`，原结果独立保留在 [historical-run](../reports/evidence/b05-backend-selection/historical-run/report.json)。两次结果的脚本版本不能混用。迁移只替换本机路径标记，不改调用数、时间、PID 或退出状态；原数据库仍留在隔离实验目录供本机复核，交付证据中只有脱敏 JSON/文本。

## 必须保留的运行约束

- **恢复和计费。** 已写入框架检查点的模型步骤可回放结果；尚未完成、或供应商已经返回但检查点尚未写入的步骤仍可能重跑。等待步骤实际执行两次直接证明这一边界。费用预留、已确认和 unknown 必须分开保留，宕机不能把未确认请求归零，也不能宣称所有外部请求或费用恰好一次。供应商重试与框架重试须分别限制，默认不给付费模型步骤叠加自动重试。
- **队列和结果。** 采用框架原生持久队列，并限制单 worker、单并发；同主体幂等键与输入 SHA 的匹配、跨主体隔离、取消终态和唯一结果由业务事务约束。取消后框架 resume 不得恢复业务执行或发布第二份结果。
- **授权和输入。** 固定输入版本不等于永久授权。恢复准入、新模型请求、真实工具调用、结果提交和读取都必须实时重查 B03 权限及期限；已完成步骤里缓存的旧授权 PASS 不能作为恢复后的凭据。撤权、取消和人审事件的业务语义需要后续实际接线测试。
- **工具和重放。** `DBOSDurability` 包装模型请求和 MCP 通信；普通 Python 自定义工具不会自动被包装，有 I/O 时须显式做成框架步骤。workflow 协调代码保持确定性，真实时钟、随机数、数据库与外部 I/O 留在步骤内部。普通 `agent.run()` 在 workflow 外不提供持久保证。
- **私有状态。** DBOS 默认序列化可能包含私有 prompt、引用、工具输出和模型返回；状态库、WAL、日志、备份与子工作流属于全部来源 ACL 和留存闭包。不得只清业务任务行，或恢复用户可控的不可信 pickle 数据库。清理、隔离、再授权必须覆盖框架历史。
- **升级和部署。** 一个 SQLite 状态目录只允许一个固定 executor；以进程监督和独占运行锁阻止第二 worker。代码和步骤配置改变时必须显式处理 `application_version`；旧版本 pending workflow 需要保留对应代码或采用框架正式升级机制，禁止把新代码标成旧版本。探针以 worker SHA 标版本仅用于本实验，业务部署版本还需绑定完整代码、依赖与配置。
- **备份和恢复。** 业务库、框架状态库、blob/text 和账本没有跨库天然原子快照。须在同一停止写入、停止 worker 的边界生成配套 manifest，校验全部引用，错配拒绝恢复；恢复沿用全部来源隔离与预算对账屏障。不把在线随手复制两个 SQLite 文件称为一致快照。

## 升级路径与尚未执行项

后续修订：[ADR-0008](ADR-0008-NATIVE-COMPUTE-QUEUE.md) 以原生队列和实际恢复实验，
单独评估同一单进程内的两个计算任务。以下历史 PG 前置不再涵盖这个有界单进程工程档位；
生产、多主机、多 worker 和 HA 的 PostgreSQL 评审要求继续保留。原 B05 证据不重写。


生产上线、多主机、多 worker、HA 或容量扩大前，先把 DBOS 状态库迁移到 PostgreSQL，在对应部署上重新执行 SIGKILL、并发、版本升级、取消、预算不确定窗口及全存储备份恢复测试。该步骤不要求业务 SQLite 同步迁移，但仍需一致停写和配套 manifest。**本次没有部署或测试 PostgreSQL，也没有启动或测试 Temporal 恢复。** 若未来选择 Temporal，应新增基于真实 Service/worker 的同等级故障证据，再变更此决策。

B05-02..07 的业务任务状态、全故障点、并发幂等、取消、审核暂停/恢复、撤权、留存与备份恢复均不能由这一个探针代验。当前证据是本地框架工程证据，既不是业务效果评测，也不是用户人工验收。

## 官方资料与历史快照

以下资料是原实验已经抓取并保留的官网快照；本次迁移未发出新的网络请求，不声称官网内容在迁移时再次核实。快照与来源 URL 一起保存在 [official](../reports/evidence/b05-backend-selection/official/)。原 `official-dbos-commands.md` 实际为 404，已明确排除出选型依据。

- [Pydantic AI DBOS 集成](https://ai.pydantic.dev/durable_execution/dbos/)：capability、模型和工具包装、步骤和序列化边界。
- [Pydantic AI Temporal 集成](https://ai.pydantic.dev/durable_execution/temporal/)：SDK、workflow、activity、Service 与 worker。
- [DBOS configuration](https://docs.dbos.dev/python/reference/configuration)：SQLite/PostgreSQL、executor、application version 与 singleton。
- [DBOS workflow recovery](https://docs.dbos.dev/production/workflow-recovery)：启动恢复与 executor 归属。
- [DBOS workflows](https://docs.dbos.dev/python/tutorials/workflow-tutorial)：已完成步骤重放、未完成步骤重执行与确定性。
- [Temporal self-hosting](https://docs.temporal.io/self-hosted-guide)：本地开发服务与正式自托管要求。
