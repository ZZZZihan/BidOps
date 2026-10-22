# 企业内部部署与运维

部署边界是 **Linux、单组织、一个 ASGI 进程、单机 SQLite 持久卷**。实现和本地工程检查
不等于目标环境、真实身份、容量或业务验收。容量输入与运行命令见
[容量与恢复](CAPACITY-RECOVERY.md)，保守架构裁决见
[ADR-0007](ADR-0007-CAPACITY-RECOVERY.md)。未完成的 B 类门槛继续为 BLOCKED。

资格核对、持久任务、人工审核和授权更新分别由独立开关控制，默认关闭；启用其中一个不会
自动启用真实模型或其他功能。详见 [资格核对](QUALIFICATION-SCREENING.md)、
[持久任务](DURABLE-RUNS.md)、[人工审核](HUMAN-REVIEW.md)。

## 身份、角色和私有对象

当前撤销适配明确限定为 **Keycloak OIDC、RS256、非加密 Back-Channel Logout Token**，
不泛称所有 OIDC IdP 已兼容。创建 confidential client，启用 Authorization Code 和
PKCE S256，禁用 implicit/password grant。Redirect URI 精确设置为
`https://<origin>/api/auth/callback`，Scope 为 `openid profile`，issuer 与配置一致。
在经过签名验证的 ID token 中配置字符串数组 `bidops_roles`，只映射所需应用角色。

| 角色 | 权限 |
| --- | --- |
| viewer | `catalog:read` |
| analyst | `catalog:read`、`agent:use` |
| reviewer | `catalog:read`、`review:decide` |
| auditor | `catalog:read`、`audit:read` |
| admin | `catalog:read`、`agent:use`、`audit:read`、`source:manage` |

多角色按权限并集生效。admin 没有隐含 `review:decide`；需要审核的管理员也须取得 reviewer
角色。角色不替代私有材料、画像、结果的对象 ACL。审核还要求有效人类登录、显式 case
assignment 和全部来源权限；assignment 不授予材料权限。管理员不能借角色读取他人正文。

`BIDOPS_ENTERPRISE_SESSION_SECONDS` 默认 3600 秒，控制应用登录最长寿命；
`BIDOPS_ENTERPRISE_IDENTITY_SNAPSHOT_SECONDS` 默认 240 秒，允许 30–240 秒，控制身份和
角色可信期限。两者同时校验，不能用一小时会话延长四分钟快照。每次受保护请求及共享对象
授权均重查主体 active、当前角色、快照和来源权限；快照过期必须重新 SSO，不顺延缓存。

Keycloak Backchannel logout URL 设置为 `https://<origin>/api/auth/backchannel-logout`，
建议启用 session required。退出按钮撤销本地登录；Back-Channel Logout 可提前撤销，
无事件时仍受 240 秒期限约束。真实 Keycloak、角色 mapper、停用和撤销路径须在 B10 实测。
完整协议与乱序处理见 [ADR-0004](ADR-0004-IDENTITY-REVOCATION.md)。

## 安装和本机启动

在仓库根目录使用 Python 3.12。主锁与两个带哈希的 overlay 都是运行版本的一部分：

```bash
uv sync --frozen --python 3.12
uv pip install --python .venv/bin/python --require-hashes \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  -r requirements/durable.lock -r requirements/attachments.lock
npm ci --prefix frontend
npm run build --prefix frontend
mkdir -p private
chmod 700 private
cp config/enterprise.env.example private/enterprise.env
chmod 600 private/enterprise.env
```

DBOS 和 pypdf overlay 不重写 `pyproject.toml` 或 `uv.lock`。再次执行 `uv sync` 后须重新
加装两个 overlay；后续命令用 `.venv/bin/...` 或 `uv run --frozen --no-sync ...`，避免同步
移除 overlay。pypdf 的严格解析、隔离进程、页定位和资源边界见
[ADR-0006](ADR-0006-ATTACHMENT-PARSING.md)，不支持 OCR 或加密 PDF 解密。

填写 `private/enterprise.env` 的 HTTPS origin、issuer、client secret 和随机 cookie secret。
DeepSeek Key 只能放在忽略的私有文件或部署注入环境，不能进入前端、日志、示例或版本库。

```bash
.venv/bin/bidops-enterprise --env-file private/enterprise.env check-config
.venv/bin/bidops migrate
# 仅导入已有合法来源包；没有数据时不制造公告
.venv/bin/bidops import-bundle --input data/artifacts/p0-capture-20261022
.venv/bin/bidops parse
.venv/bin/bidops-enterprise --env-file private/enterprise.env serve \
  --host 127.0.0.1 --port 8765
```

`--env-file` 是顶层参数，放在子命令前；进程环境优先于文件。`bidops` 数据命令须使用与服务
一致的 `BIDOPS_DATA_DIR` 和 `BIDOPS_DATABASE_URL`。`check-config` 只校验本地配置，
不证明 IdP、模型、来源网络、费用账本或业务效果已经验收。enterprise serve 拒绝降级为匿名 Demo。

反向代理在同一 HTTPS origin 提供 UI/API，正确转发 Host，内部 8765 端口不直接对外暴露。
普通企业 JSON/form 写请求上限为 16,384 bytes；材料上传、材料更新和授权导入由精确路由
单独鉴权及流式限额，不能对全部路由统一设置 16 KiB 而阻断合法上传，也不能把上传豁免
推广到其他写请求。代理响应超时应按路由设置；异步任务受理与轮询不应等待完整模型计算，
完整候选构建和受控导出另有成本。不要记录 OAuth query、Cookie、Authorization 或正文。

仅受信代理可设置 forwarded headers。后端固定一个 Uvicorn worker，第二个同库企业进程
会拒绝启动。不要用 NFS、多个容器副本、第二个 DBOS worker 或删除锁文件来扩容。
开发 HTTP 仅允许显式 `BIDOPS_ENTERPRISE_ALLOW_INSECURE_LOOPBACK=true` 且为
localhost/127.0.0.1；企业部署仍要求 HTTPS。

## 容器与运行限额

```bash
docker compose -f compose.enterprise.yaml build
docker compose -f compose.enterprise.yaml up -d
```

容器使用 UID 10001、只读根目录和无额外 capabilities，数据保存在 named volume。新卷为空，
`/api/ready` 应返回 503。导入包须有合法来源并仅调整该包的容器所有权：

```bash
docker compose -f compose.enterprise.yaml cp data/artifacts/p0-capture-20261022 app:/app/data/capture
docker compose -f compose.enterprise.yaml exec --user 0 app chown -R 10001:10001 /app/data/capture
docker compose -f compose.enterprise.yaml exec app bidops import-bundle --input /app/data/capture
docker compose -f compose.enterprise.yaml exec app bidops parse
```

持久资格计算默认同时运行 **1 份**；`BIDOPS_JOBS_COMPUTE_CONCURRENCY` 只接受 `1` 或 `2`。
配置 `2` 会启用 DBOS 原生计算子队列的两份计算上限，仍须单 ASGI 进程、SingleWorker 独占锁
及固定 DBOS executor。`BIDOPS_JOBS_MAX_ACTIVE_JOBS=100` 是业务活动任务和父工作流上限，
包含等待审核，不能解释为模型并发。Jobs 启用时旧同步 `/api/screening/checklists` 明确返回
409 `screening_use_durable_jobs`，前端引导到持久任务；worker 未就绪也不回退同步计算。
候选查询和历史读取不受此迁移影响。Jobs 关闭时仍可使用原同步接口。

原 capacity-v1 的实测计算1/目标2仍为 FAIL。新的配置和执行证据见
[ADR-0008](ADR-0008-NATIVE-COMPUTE-QUEUE.md)；本机离线双计算不等于目标环境或真实
DeepSeek 吞吐验收。生产部署与多 worker 扩容仍须相应授权、实测和基础设施变更。

资格核对默认限额为每次最多 12 个模型请求、90 秒运行时间、120,000 输入 bytes、6,000 输出
tokens；真实调用和账本确认分别受 `BIDOPS_SCREENING_LIVE_CALLS_ENABLED`、
`BIDOPS_SCREENING_LEDGER_VERIFIED` 控制。每用户企业请求默认 20 次/分钟。历史 Demo 的
两条请求槽和 60 秒默认值属于旧 Demo 路径，不代表持久资格计算能力。

共享成本上限保持 10 元；它是保守预留和估算控制，不能充当供应商账单硬上限。未知费用保留
预留，不能按零费用释放。同一 owner、幂等 key 和相同输入重放读取原任务；不同输入返回 409。
任务取消、中断或恢复仍须核验当前授权和固定版本，不保证供应商计费 exactly-once。

候选专用存储上限为 50,000,000 bytes，普通结果及原始模型输出仍受原 10,000,000-byte 通道
约束。复核页默认 20、最多 100 个候选，完整导出上限 60,000,000 bytes；超限明确 413，不截断。
这些是 UTF-8 序列化后的十进制 bytes，不是 MiB。索引、页面语义及回退见
[容量与恢复](CAPACITY-RECOVERY.md)。

## 健康、指标与本地告警

- `/api/health` 是不含用户数据的公开基础存活检查。
- `/api/ready` 校验本地配置、Agent 开关和公告存在；不调用 IdP、模型或来源网络。
  启用依赖监测后还要求依赖健康及观察未过期。
- `/api/audit` 和 `/api/metrics` 要求 `audit:read`。指标及请求日志使用有界路由/组件标签，
  不携带私有正文、Cookie、查询参数或 provider token。自动抓取仍需独立服务身份方案，
  不能把人工浏览器 cookie 当长期采集凭据。
- `BIDOPS_OPERATIONS_ENABLED` 默认 **false**。打开后每 30 秒观察业务库、工作流库、
  worker、磁盘、队列、来源失败和维护状态；默认观察有效期 120 秒。观察过期使 ready 失败。
  `/api/operations/health` 还要求企业模式、监测开关和 `audit:read`；关闭时返回 404。
- 默认磁盘门槛 268,435,456 bytes，积压门槛 80，最老排队 120 秒；这些是依赖异常触发值，
  不是容量验收硬件要求或队列准入上限。阈值配置前缀均为 `BIDOPS_OPERATIONS_`。

进程内部监测不能发现自身整个进程死亡。独立 watchdog 仅轮询 ready，在本地 JSONL 和
stdout 输出 firing/resolved，**没有配置或发送企业外部告警**。示例只连接本机已运行服务：

```bash
.venv/bin/python scripts/operations_watch.py \
  --origin http://127.0.0.1:8765 --duration-seconds 300 --interval-seconds 30 \
  --output work/operations-watch-new.jsonl
```

输出文件必须不存在；观察时间在检查结束后记录。每次 I/O 有 5 秒超时，分块间还有 10 秒
elapsed 检查，不能宣称严格 10 秒 wall-clock 强杀。该进程同样位于本机，不能证明主机死亡
时的外部送达。B09-05 必须另有真实接收端和发送授权；缺失时为 BLOCKED。

## 一致备份、恢复和回滚

**先停止服务及 worker，再备份。** 备份与服务共享独占锁；不能在服务仍运行时复制两个
SQLite 文件或绕过锁。输出包含敏感正文，必须放在受控目录并单独管理备份留存：

```bash
.venv/bin/bidops-enterprise --env-file private/enterprise.env backup \
  --output data/artifacts/backup-YYYYMMDD --retention-days 7
.venv/bin/bidops-enterprise --env-file private/enterprise.env restore \
  --input data/artifacts/backup-YYYYMMDD --output data/artifacts/restore-YYYYMMDD
```

v4 bundle 通过 SQLite backup API 复制业务库和 `private/jobs/dbos.sqlite3`，核验私有文件、
公告/附件固定字节与解析 receipt、任务/结果/账本映射和许可调度代际闭包。不能只替换某个
数据库并重算单文件 SHA；typed 关联也须匹配。新空任务库可无系统库，包含持久任务的旧
v1–v3 备份不能据此省略必需 DBOS 库。路径越界、丢失、篡改、未知工作流或关联损坏均拒绝。

恢复到新目录，不覆盖原实例。恢复会撤销登录和 OAuth state、过期身份快照、移除旧对象
授权并隔离私有对象/结果/任务。原生任务不可自动恢复执行，采集 run/schedule 也隔离且原生
schedule 暂停。人工 case 使用新的 review epoch、清空 assignment、冻结旧 outbox；历史
决定和 hash 保留，但不能重新成为当前批准。job reauthorize 不授予来源权限，也不解除 review
隔离。应先对账当前授权，再按各领域显式重新授权、分配审核和决定是否恢复任务。

所有 restore 都生成新的共享预算对账屏障，保留已用、未知和预留费用。部署人员对账后才可
配置本次 `BIDOPS_SCREENING_LEDGER_RESTORE_TOKEN` 与旧 Demo 对应的
`BIDOPS_DEMO_BUDGET_RESTORE_TOKEN`；旧 token 不可复用。确认屏障不意味着找回备份后
丢失的供应商费用。无法确认最新授权或成本时，保持私有访问/真实调用关闭。

切换时将 `BIDOPS_DATA_DIR` 和 `BIDOPS_DATABASE_URL` 同时指向恢复目录及其中的
`bidops.sqlite3`，检查固定版本、页证据、全部历史、账本、隔离和撤权结果。RTO 必须计入
上述授权核验和业务可用，不能仅计 `/api/health` 首次成功。目标 RPO≤24 小时、RTO≤30 分钟
仍需目标环境的完整演练，当前保持 B09-06 BLOCKED。

升级前备份；回退用已验证镜像和升级前 typed bundle，停写后切换，不做破坏性反向迁移。
012 索引可保留，不需要 DROP；旧镜像的 10 MB 通道和未分页复核不能替代新增 50 MB 批次/
60 MB 导出能力，不能仅降镜像就声称兼容这些新增记录。

会话历史默认保存 30 天，启动和定期维护执行清理。人工运行 prune 也须先停服务取得锁：

```bash
.venv/bin/bidops-enterprise --env-file private/enterprise.env prune
```

来源到期删除其私有正文及依赖正文、冻结写回，并清理对应 DBOS 历史；事件 metadata/hash
和 usage/账本继续保留。case 联合来源到期清掉 case 的事件正文，不扩大删除本不依赖新增
材料的旧 job。SQLite 删除不保证安全擦除旧页；宿主机、秘密配置和备份的磁盘加密、权限及
生命周期由企业管理。当前没有应用层静态加密。

## 检查与验收边界

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest
npm run build --prefix frontend
npm run test:e2e --prefix frontend
```

这些命令不触发实际 IdP、来源或模型验收。`scripts/enterprise_live.py --execute` 会调用真实
模型，只有另有明确授权和预算时才可执行，不属于默认检查。CI、合成数据、真实本机 HTTP/
DBOS、目标环境负载、企业接收告警和具名人工验收分别记录。正式容量命令与失败留存规则
见 [CAPACITY-RECOVERY](CAPACITY-RECOVERY.md)，不能用 UNIT smoke 或全量单测替代。
