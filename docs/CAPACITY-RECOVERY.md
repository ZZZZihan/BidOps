# 容量与完整恢复验收

本文规定 B09 的可复现工程运行和验收边界。单组织、单 ASGI worker 和 SQLite 约束保持；
默认计算1，可显式配置原生队列计算2。原 capacity-v1 的实测1/目标2仍为 FAIL；新档位不覆盖
旧结果。目标环境负载、真实告警、完整业务恢复和数据库扩容裁决仍按 B 类门槛独立判断。
新计算队列的配置、兼容与回退见 [ADR-0008](ADR-0008-NATIVE-COMPUTE-QUEUE.md)。

## 固定输入

原正式输入是 [manifest.json](../evals/capacity-v1/manifest.json) 原始文件字节，SHA256 为：

```text
bd82c7ab050a53610742515d4af082140f6fce5593b203cabaa4e847ffb4a170
```

文件重排、缩进或参数改变也必须重新冻结，不能沿用旧 pin。缺字段、错误单位、档位不足、
空 trace 或测量未执行均不能 PASS。完整 manifest 校验通过与容量测量通过是两个判断。

| 项目 | 冻结值 |
| --- | --- |
| 企业 / 账号 / 活跃 client | 1 / 20 / 10；client-01…10 各绑定不同账号 |
| 公告内容版本 / 采购包 | 10,000 / 50,000 |
| 公告固定正文 | 每版本 4,096 bytes，共 40,960,000 bytes |
| 企业材料 | 200 份，每份 16,384 bytes，共 3,276,800 bytes |
| 公告附件 | 1,000 份，每份 16,384 bytes，共 16,384,000 bytes |
| 目标硬件 | 4 logical CPU、8,589,934,592-byte RAM、21,474,836,480-byte 可用本地磁盘 |
| 预热 / 稳态 / 最长 drain | 300 / 1,800 / 900 秒 |
| 逻辑业务到达率 | 2 次/秒，偏差 ±5%；60 秒 bucket；到达迟滞≤1,000 ms |
| 业务操作比例 | read/search 78%、task status 18%、submit task 2%、review 2% |
| client 思考时间 / 下限 | 5,000 ms；每个 client 稳态至少 180 次 |
| 预登记操作 | 共 4,200 次，其中稳态 3,600 次；均分时每 client 稳态 360 次 |
| 测量 HTTP | 10,000 ms timeout；0 次自动重试 |
| 资源采样 | 每 1,000 ms 一次，最大缺口 2,000 ms |
| 活跃计算目标 / 实际 | 2 / 1；所需重叠至少 30,000 ms |
| ASGI worker / 活跃业务任务上限 | 1 / 100 |
| JSON / 材料上传 / 材料正文 | 16,384 bytes / 2,000,000 bytes / 200,000 字符 |
| 普通公告页面 | 100 条 |
| 资格调用限额 | 12 requests、90 秒、120,000 input bytes、6,000 output tokens |
| 离线模型等待 | 每 job 合计固定 30,000 ms，偏差≤1,500 ms；真实模型/来源调用均为 0 |
| 延迟 / 错误 | 非模型 API P95≤1,000 ms；任务受理 P95≤1,000 ms；错误率严格小于 1% |
| 恢复目标 | RPO≤86,400 秒；RTO≤1,800 秒，含授权核验和业务可用 |

bytes 使用精确字节，RAM 的 8 GiB 与磁盘的 20 GiB 不是十进制 GB。档案/材料的 hash、
实际数量、单件大小和总量在运行前后核验，不能只接受调用方自报计数。

## 工程 harness 与计数

[capacity_probe.py](../scripts/capacity_probe.py) 启动真实本机 FastAPI HTTP、SQLite、
对象 ACL、签名登录、DBOS 任务及人工审核仓储，装载上述完整规模；模型仅使用合成答案的
FunctionModel，两次等待各 15 秒组成每 job 的 30 秒等待。真实外部模型、真实采购下载和
实际告警发送都不属于此命令。脚本只允许 loopback 网络，不接收 provider 凭据。

20 个账户经合成 IdP transport 的真实 RS256/PKCE 协议建立应用登录，10 个 client 使用独立
cookie。它证明本机协议与实现路径，不能替代企业真实账号/Keycloak。当前测量固定为
`local_engineering`；开发机 Apple M2 Ultra、64 GiB RAM 不能替代上表的目标硬件。须记载
其他测试、构建或进程争用 CPU、磁盘和内存的情况，不声称独占目标机。

2 次/秒与 4,200 条指 **逻辑业务操作**。一次 review 还包含依赖 GET；定期 SSO 刷新包含
login/callback/me 等控制 HTTP。逻辑操作计时包含其必要前置工作，HTTP 总量另记在
`http-responses.jsonl` 和 `http-accounting.json`，不能把 4,200 条称为全部 HTTP。
`auth-control.jsonl` 保留认证开销。响应只记录状态、hash 和有界路径，不记录 cookie、
token、私有正文或回调 query。

完整候选冻结属于测量前准备：`POST /api/screening/candidates` 单独允许 300 秒客户端
等待，真实耗时和结果写入 `setup-operations.jsonl`，明确
`included_in_timed_capacity_metrics=false`。这不是提高稳态 10 秒 timeout，也不把准备耗时
作为达标的 P95 样本。准备失败立即留存 `FAILED_BEFORE_WARMUP`，不能复用该半准备库
或缩为小候选批次继续正式测量。300 秒不是普遍 API SLO。

## 从干净版本启动

由集成负责人先提交全部实现、文档和定向修复，再在该 clean SHA 启动；运行期间不改代码。
安装基础环境及两个精确 overlay（重新同步后须重新安装 overlay）：

```bash
uv sync --frozen --python 3.12
uv pip install --python .venv/bin/python --require-hashes \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  -r requirements/durable.lock -r requirements/attachments.lock
```

在仓库根目录执行下面整段。路径每次必须新建；同一 SHA 重试时把末尾 `-01` 改为新序号。
保留旧输出和日志，不能覆盖失败。

```bash
if test -n "$(git status --porcelain)"; then
  printf '%s\n' '拒绝开始：工作树尚未冻结为干净提交。'
  exit 2
fi
capacity_revision="$(git rev-parse --short=12 HEAD)"
capacity_output="work/capacity-local-${capacity_revision}-01"
mkdir -p work
if test -e "$capacity_output" || test -e "${capacity_output}.log"; then
  printf '%s\n' '拒绝覆盖已有容量证据；请选择新序号。'
  exit 2
fi
.venv/bin/python scripts/capacity_probe.py \
  --output "$capacity_output" \
  --manifest evals/capacity-v1/manifest.json \
  --manifest-sha256 bd82c7ab050a53610742515d4af082140f6fce5593b203cabaa4e847ffb4a170 \
  > "${capacity_output}.log" 2>&1
capacity_exit=$?
printf 'capacity_exit=%s output=%s\n' "$capacity_exit" "$capacity_output"
```

正式命令没有 `--unit-smoke`。通常需装载准备时间、35 分钟窗口及最多 15 分钟 drain，
不能承诺固定完成于 35 分钟。当前一份计算槽可能使后续 job 排队，须保留实际积压、超时
和错误。脚本退出会回收所启动的 worker，不清理历史输出。

完整工程运行的退出码 1 表示容量 FAIL，2 表示其他未通过/受阻情形；非预期异常也会非零
退出并留下安全 `failure.json`。UNIT 模式可因协议完成而退出 0，仍不是正式容量 PASS。
不要以 shell exit 0 或 trace 文件存在替代 `report.json` 中的逐项判定。

## 原始证据与判定

受控输出目录含私有数据库和正文，只能按内部证据处理，不提交 Git 或公开附件。至少保留：

- `input-pin.json`、固定 manifest、`plan.json`、`schedule.json`、`clock.json`：代码与输入
  指纹、完整预登记到达及不可后选的窗口。
- `requests.jsonl`、`http-responses.jsonl`、`http-accounting.json`、`auth-control.jsonl`：
  每条业务操作及额外 HTTP、错误和认证成本；重试不得抹去原失败。
- `resources.jsonl`、`worker-events.jsonl`、`worker.log`、`incomplete-jobs.jsonl`：实际进程
  RSS/CPU、计算起止、模型等待和未完成任务；缺失样本不是零占用或零错误。
- `page-inventory.json`、`trace.json`、`report.json`：固定页面 ID、数据前后完整性、原始
  测量输入和逐项结果。早期失败保留 `failure.json`；中止保留原因和进程退出信息。
- `setup-operations.jsonl`：完整候选准备的独立 deadline、耗时和结果，不混入稳态指标。

所有预登记到达都在分母内。missing、重复 ID、非法事件、失败、429、5xx、超时和晚到均
保留，不从分母剔除；没有结束时间的请求不能靠成功样本计算出通过的 P95。错误率严格
`<1%`，不是 `≤1%`。操作比例、client 多样性、每人最少次数、到达率和资源档位单独检查，
不能由单 client 或短时间峰值冒充十个活跃 client。

模型等待、排队等待、实际计算、HTTP 受理和非模型接口延迟分列。计算并发只在固定稳态
统计，并交叉核验 job interval 与资源采样；预热偶发达到目标不能填补稳态缺口。
OOM、进程退出、死锁、数据遗漏、重复/缺失页面、静默截断和不完整任务不能被排除为异常
样本。终止正式测量时保留目标操作与实际完成的差额。

本机离线工程结果可以揭示瓶颈或验证失败关闭，不能把 `local_engineering` 改名为目标验收。
当前 2/1 差距即使其他指标通过仍保留 FAIL；目标环境、真实身份、真实送达及恢复证据缺失
仍保留 BLOCKED。B08 独立业务质量门槛和 B10 人工验收不由容量分数替代。

## 完整候选、复核页面与回退

全量候选按固定公共版本快照构建，不能通过只存前 500 条或缩减采购包绕过规模。原实现
按 version 查包缺少匹配索引，且完整候选超过普通 10 MB 通道。修复使用 additive
[012 索引](../backend/bidops/migrations/012_package_versions_index.sql)
`nd_package_versions(version_id, package_id)`，并提供只接受严格完整候选形状、计数、hash
及真实公共来源/包件闭包的内部专用保存方法，上限 50,000,000 bytes。普通 save/raw-output
通道保持 10,000,000 bytes；调用方不能仅设置 kind 来获得大正文通道。

人工 case 详情候选按 `candidate_offset`、`candidate_limit` 分页，默认 20、最多 100；
返回全量计数和原 manifest hash，并声明页面不是完整记录。翻页每次执行当前授权，页面
不能用于重新计算完整批次 hash。完整历史由 export 提供；60,000,000-byte 之外返回 413，
不静默删事件/采购包。分页减少传输量，不意味着服务端读取/完整性校验免费，其真实成本
仍应体现在容量测量。

索引迁移和重复迁移保持旧 ID、原始字节和证据。旧 typed backup 恢复后按迁移 ledger 应用
新增索引。回退先停服务，使用升级前的配套镜像和 typed bundle；不 DROP 索引、不删除
大批次或历史迁就旧限额。旧镜像未验证新增 50 MB 批次及分页/60 MB 导出，不能把降镜像
当作兼容恢复。B09-08 是实际装载失败后新增标准，不降低原目标。

## 完整恢复和真实告警

命令与授权/账本屏障见 [企业运维](ENTERPRISE-OPERATIONS.md)。完整演练须在备份之后
新增业务记录、停用账号、撤销和删除材料，再模拟实例及本地存储丢失，恢复业务库、DBOS
系统库和全部引用私有文件。不能只重启同一目录冒充丢失本地存储。

RPO 从故障/丢失时点与实际可恢复业务记录界限计算；RTO 从故障时点直到依赖可用、最新
授权核验完成、预算屏障对账及获准业务路径可用。仅启动进程、health 200 或旧会话能登录
不构成恢复完成。旧会话/链接/事件/assignment 均须负测；最新 ACL 无法确认时保持隔离，
旧人工决定不得因旧备份复活，已用/未知费用不能释放。

故障到本地告警须用因果时间戳和完整观察记录证明，进程内监测与独立 watchdog 分开测。
watchdog 只生成本地日志，没有真实外部接收端；B09-05 需要另外明确授权和实际收件证据。
当前脚本不发送告警，也不自动部署。目标 RPO/RTO、真实接收和数据库扩容结论在相应实际
证据完成前一律 BLOCKED；保留 SQLite、单 worker 和原有成本/并发限制。

## 原生计算队列复验档位

新增 `evals/capacity-native-queue-v2/manifest.json`，只改变档位标识和
`configured_compute_concurrency: 1 → 2`；数据、硬件、负载、目标并发2、延迟、错误率、
30秒模型等待、300秒预热与1800秒稳态保持逐字段相同。运行仍使用上面的完整命令，
将 `--manifest` 和对应原始 SHA 替换为 [新冻结说明](../evals/capacity-native-queue-v2/README.md)。
新运行必须使用新目录、干净完整 SHA，不得重用或删除 v1 失败证据。
