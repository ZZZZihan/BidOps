# 企业内部部署与运维

交付范围：Linux / 单组织 / 一个 ASGI 进程 / SQLite 持久卷。它可作为企业内部试用基线；
真实身份系统接入和目标机器验收仍需完成。成熟 Agent 与认证框架选型见 ADR-0003。

B05 的可选持久任务默认关闭。启用、精确 overlay、原生恢复、私有框架库及 v4 配套备份步骤见
[DURABLE-RUNS](DURABLE-RUNS.md)。装好 overlay 后使用下面的 `--frozen --no-sync` 命令，
避免普通 `uv run` 自动同步主锁而移除 DBOS；重新执行 `uv sync` 后须重新安装 overlay。

## 身份系统接入

可以使用现有符合 OIDC 的企业 IdP，或由企业自行部署 Keycloak。创建 confidential client：

- Client ID：`bidops`，启用 Authorization Code，启用 PKCE S256；禁用 implicit/password grant。
- Redirect URI：精确填写 `https://<你的域名>/api/auth/callback`，不要通配。
- Scope：`openid profile`。签名算法 RS256；发现文档的 issuer 必须和配置完全一致。
- 在 **ID token** 中增加字符串数组 claim `bidops_roles`。只将需要的应用角色映射进去；
  不要直接把全局管理员组等同于本应用角色。没有已知角色的账户被拒绝。

| 角色 | 公告档案 | 使用 Agent / 自己的历史 | 审计 / 指标 |
|---|---|---|---|
| viewer | 是 | 否 | 否 |
| analyst | 是 | 是 | 否 |
| auditor | 是 | 否 | 是 |
| admin | 是 | 是 | 是 |

权限在服务端强制执行。审计 API 不提供其他用户的提问正文。角色在登录时读取，默认登录
一小时有效。退出按钮撤销 BidOps 本地登录；若 IdP 仍有登录，下一次 SSO 可能自动认证。
需要禁用账号即刻退出、全局单点退出时，应在企业试用阶段接入 IdP 的撤销机制。

## 本机启动（连接企业 IdP）

```bash
uv sync --frozen
uv pip install --python .venv/bin/python --require-hashes \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements/durable.lock
npm ci --prefix frontend
npm run build --prefix frontend
mkdir -p private
chmod 700 private
cp config/enterprise.env.example private/enterprise.env
chmod 600 private/enterprise.env
```

填写 `private/enterprise.env` 中的 HTTPS 域名、issuer、client secret 与随机 cookie secret。
生成 cookie secret 的命令在示例文件中。DeepSeek Key 继续使用忽略的 `private/deepseek.env`
或注入环境变量；不要进入前端、配置示例或版本库。

```bash
uv run --frozen --no-sync bidops-enterprise check-config
uv run --frozen --no-sync bidops migrate
# 首次部署需要放置已有的合法采集包；缺少包不会虚构数据
uv run --frozen --no-sync bidops import-bundle --input data/artifacts/p0-capture-20261022
uv run --frozen --no-sync bidops parse
uv run --frozen --no-sync bidops-enterprise serve
```

`check-config` 只验证本地配置，**不代表 IdP 或 DeepSeek 已认证成功**。enterprise serve
拒绝静默降级为匿名 Demo。已有个人本地 Demo 仍可用 `uv run --frozen --no-sync bidops-demo` 在 loopback 运行。

反向代理须在配置的同一 HTTPS 域名提供 UI/API，转发正确 Host；不得暴露内部 8765 端口。
设置请求体上限 16 KiB、上游超时至少 70 秒；不要记录 OAuth 回调 query string、Cookie 或
Authorization。只有受信代理可以设置 forwarded headers。后端为单 worker，第二个同库
企业进程会拒绝启动。不要用 NFS、多个容器副本或绕过文件锁来扩容。

开发用 HTTP 只允许显式 `BIDOPS_ENTERPRISE_ALLOW_INSECURE_LOOPBACK=true` 且 URL 指向
localhost/127.0.0.1；默认配置强制 HTTPS。这个选项不用于企业部署。

## 容器

```bash
docker compose -f compose.enterprise.yaml build
docker compose -f compose.enterprise.yaml up -d
```

容器使用 UID 10001、只读根目录、无额外 capabilities，数据保存在 named volume 中。
新卷没有数据；空库的 `/api/ready` 正确返回 503。把合法采集包复制到卷并导入：

```bash
docker compose -f compose.enterprise.yaml cp data/artifacts/p0-capture-20261022 app:/app/data/capture
# 只调整这份导入包的所有权，不改原始宿主机文件
docker compose -f compose.enterprise.yaml exec --user 0 app chown -R 10001:10001 /app/data/capture
docker compose -f compose.enterprise.yaml exec app bidops import-bundle --input /app/data/capture
docker compose -f compose.enterprise.yaml exec app bidops parse
```

环境文件通过运行配置注入，不进入镜像。Docker 管理员和数据库管理员属于信任边界；备份、
数据库、宿主机及秘密配置由企业磁盘加密、访问控制和密钥管理保护，本基线没有实现应用层加密。

## 健康、审计与成本

- `/api/health`：本地存活与数据库可用；公开，无用户数据。
- `/api/ready`：本地配置、Agent 开关和已导入公告存在。它不主动消耗模型额度，不探测
  IdP/模型网络，不声称真实上游可用。
- `/api/audit`：auditor/admin 可分页读取事件；表禁止更新和删除。
- `/api/metrics`：auditor/admin 可读 Prometheus 格式请求计数、状态与延迟直方图。
  自动采集尚需企业认证代理或服务身份方案，不能把人工 cookie 当长期抓取凭据。
- 结构化请求日志含随机 request_id、路由模板、状态码和耗时，不含正文或回调 query。
  框架/模型用量保存在 agent_runs，企业事件可通过 run_id 关联。
- 默认两条活跃模型请求、每用户 20 请求/分钟、60 秒运行超时，沿用保守成本上限 10 元。
  该估算不是供应商账单硬限额；失败或未知运行保留预留额，运维需对账后再调整上限。

同一个用户、Idempotency-Key 和相同输入重复提交时只读已保存结果。输入不一致返回 409。
中断请求不自动重跑；先查看历史/审计再发起新的请求，避免掩盖重复计费。限流为单进程内存
状态，重启后重置；成本、请求防重和会话状态持久化。不要宣称 exactly-once 的供应商计费保证。

## 备份、恢复、回滚

```bash
# 输出目录必须尚不存在。包含私有会话、身份摘要和原文，仅存于受控位置
uv run --frozen --no-sync bidops-enterprise backup --output data/artifacts/backup-YYYYMMDD
uv run --frozen --no-sync bidops-enterprise restore --input data/artifacts/backup-YYYYMMDD --output data/artifacts/restore-YYYYMMDD
```

备份用 SQLite online backup API 创建一致数据库，再复制其引用的不可变原文并逐个核验 SHA256。
恢复验证 manifest、数据库完整性、所有引用的原文，拒绝路径穿越/校验失败/覆盖现有目录。
恢复时撤销所有应用登录和未完成的 OAuth state；保留会话与费用审计。切换恢复库前停止应用，
将 `BIDOPS_DATA_DIR` 指向恢复目录、`BIDOPS_DATABASE_URL` 指向其中的 bidops.sqlite3；启动后
重新登录、核对公告数量/证据/历史与费用。不得把恢复目录中的模型历史提交 GitHub。

升级前备份；回滚时使用已验证镜像和升级前备份，停止写入后再切换目录。不要执行反向破坏性
迁移。备份恢复的实际耗时不等于已承诺 RPO/RTO，企业需在目标数据规模下演练。

会话默认保存 30 天，启动及每小时清理过期会话和登录。可手动运行：

```bash
uv run --frozen --no-sync bidops-enterprise prune
```

审计/费用表继续保留，备份需另设留存；SQLite 删除不保证擦除磁盘旧页。管理员需采用企业
磁盘与备份生命周期控制。没有静默外传或托管遥测。

## 验收命令

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
npm run build --prefix frontend
npm run test:e2e --prefix frontend
# 三次真实模型提问 + 重启/防重；写入独立私有数据库，不覆盖历史报告
uv run python scripts/enterprise_live.py --execute --run-id enterprise-check-unique-id
```

CI 在隔离测试中检查真正 RSA 签名的 OIDC 协议，并构建/启动非 root 企业容器；它不包含
真实企业用户、SSO 登录或 DeepSeek 凭证。实际执行及限制见 reports/enterprise-run-20261022.md。
