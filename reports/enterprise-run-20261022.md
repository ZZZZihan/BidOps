# BidOps 企业应用 E1 工程基线验收 — 2026-10-22

## 结论与目标

用户新增目标：实现企业级 Agent App。本次交付单企业内部部署的工程基线：企业身份接入、
角色权限、私有持久会话、请求防重、审计、限流、备份恢复及容器运维。

**企业生产验收尚未通过。** 真实企业 SSO、目标 HTTPS 环境、两名独立用户验收、数据质量
门槛及实际业务负载评审仍未完成。原 P1/P2/P3 比赛阶段没有被自动宣布通过。

- 远端：https://github.com/ZZZZihan/BidOps ，公开仓库，main。
- 企业目标：[Issue #13](https://github.com/ZZZZihan/BidOps/issues/13)。
- E1 交付：[Issue #14](https://github.com/ZZZZihan/BidOps/issues/14)。
- 实现 Commit / CI：推送后补充不可变引用；本段目前不声称远端 CI 已通过。
- 本次基于 `ebf1e6eb7342400c9d2f0de8282964dd0afaf484` 的已完成 Demo；历史报告保留。

## 实际架构

React / TypeScript / Vite → FastAPI 后端 → Authlib OIDC、Pydantic AI + DeepSeek、SQLite。
Pydantic Evals 保留已有评测协议。Authlib 1.9.1、limits 5.8.0、prometheus-client 0.26.0
均使用官方文档与成熟框架能力；没有自行开发 Agent 循环、密码认证框架或工作流引擎。

OIDC 使用 confidential client / code / PKCE S256 / RS256。服务端检查 issuer、audience、
nonce、state、签名与过期；仅接受明确的应用角色。登录 cookie HttpOnly / SameSite=Lax，
HTTPS 配置下 Secure；模型和 OAuth 凭证不进入浏览器。每个写请求校验同源和 CSRF。

会话绑定 issuer/sub 派生的 owner_id。请求按 owner/idempotency key 事务认领，持久保存
响应和框架消息；重启后可读取/续聊。同键不同输入拒绝，未知中断不自动重跑或标记成功。
审计表通过 trigger 拒绝修改/删除，日志和指标只记录路由模板等安全元数据。

部署为 Linux 单 worker、一个组织共享公告库。启动锁阻止同一数据库第二个企业工作进程；
容器为 UID 10001、只读根目录、持久数据卷。默认两条活跃 Agent、每用户 20 请求/分钟、
每会话 8 轮、60 秒运行限制、30 天会话留存。该配置不是多租户 SaaS、高可用集群或 SLA。

## 实际执行与结果

| 命令 / 检查 | 本地实测 |
|---|---|
| `uv run ruff check .` | 通过 |
| `uv run ruff format --check .` | 通过 |
| `uv run pytest -q` | 189 passed；原 141 项基础上增加 48 项企业能力测试 |
| `npm run build --prefix frontend` | 通过 |
| `npm run test:e2e --prefix frontend` | 12 passed；原 5 项基础上增加 7 项企业界面场景 |
| `uv run python scripts/enterprise_live.py --execute --run-id enterprise-live-20261022` | 5/5 集成检查通过，3 个真实模型轮次、6 次模型请求 |
| SQLite 备份恢复 | 单元测试及真实六公告集成运行均执行；数据库完整性与原文 SHA256 验证通过 |
| Docker 镜像构建/启动 | 当前执行环境没有 Docker；通过 GitHub Actions 的 container job 验证，结果待回填 |
| 企业真实 SSO / 目标域名部署 | 未执行；没有企业 IdP、client、域名及目标机器参数 |

认证测试通过 HTTPX2 MockTransport 交换 OIDC 协议，使用生成的 RSA 密钥真实签名、校验 JWT，
覆盖错误 issuer/audience/nonce/签名、过期、未知角色、state 重放/跨浏览器和 CSRF。它是协议
集成测试，不是一次真实 Keycloak/企业 IdP 账号登录。

其他测试覆盖跨用户读写拒绝、API 别名认证、重启恢复、重复请求零新增模型调用、并发重复、
取消/中断状态、限流/预算、审计不可修改与正文脱敏、备份损坏/路径穿越、单 worker 限制。
浏览器测试覆盖 SSO 入口、失败不降级、角色界面、历史恢复、原 key 重取与移动端审计。

第一次新增测试运行曾有两项断言失败：Authlib 会清除一次未绑定浏览器的 state（测试错误地
期待继续复用）；FastAPI 指标使用不含 `/api` 的规范路由模板（测试期待带前缀）。修正断言
以符合实际安全语义后，完整套件通过。没有把最初失败的运行记为已通过。

## 真实模型与样本

[原始汇总 JSON](enterprise-live-20261022.json)记录：

- 实际返回模型：`deepseek-flash`，Pydantic AI 2.59.0。
- 5 项检查：真实预算、关闭/重建服务后的防重、重启后的指代追问、精确差额、保存三轮历史。
- 输入 21,004 tokens，输出 401 tokens；3 轮 / 6 次模型请求。
- 总联调耗时 14.013 秒；样本很小，不据此发布 P95 或并发 SLA。
- 本次按原定高峰未缓存价估算 **¥0.045216**，不是实际账单。
- 已合并进开发成本账本：完成请求估算 **¥2.170878**，含历史失败预留保守合计
  **¥2.420878**，低于原定 ¥10 上限。未收集、发布 API Key。

仍使用此前真实抓取的 6 份公告 / 15 个采购包；本次没有新增采集，也没有生成政府样本。
在隔离数据库副本中完成实测，原始公告未改写。恢复测试会撤销复制库中的登录/state。
原始正文、私有问答轨迹、登录记录及备份不进入公开仓库。

本次没有重新包装一个“企业 Agent 总分”。原 Demo 的 96 场景 / 105 条提示、首次留出集
98.67/100（71/72）和修复后回归 72/72 是历史事实；本次 5 项集成通过不是新的留出集成绩。
提示词 SHA256 仍为 `85ed48558a3a1d728567b45c39e8db34c7398061b316051fb427c47433a54bd9`。

## 尚未解决与下一验收门槛

1. 企业 SSO issuer、client secret、应用角色、HTTPS 域名和目标机器尚未提供；协议测试
   不能替代两个真实账号的访问隔离、停用/退出和故障恢复验收。
2. 默认登录有效期一小时；当前没有 IdP back-channel logout 或即时账户撤销。
   `/api/metrics` 需要审计身份，企业自动抓取还需服务身份/认证代理方案。
3. SQLite 单进程、内存频率窗口、同组织共享公告、磁盘级数据保护是当前部署边界；
   未完成多实例、PostgreSQL、企业负载/容量/可用性、安全评审或生产 RPO/RTO。
4. 审计 UPDATE/DELETE 被应用数据库约束禁止，但没有防数据库管理员篡改的外部审计存储。
5. 只有六份公告，持续采集许可与独立代表性人工金标仍未就绪，不能宣布 P1 数据门槛通过。

下一步按 GOAL.md 的 E2 连接真实身份与部署环境，补齐独立数据验收；正式生产发布需 E3 评审。
