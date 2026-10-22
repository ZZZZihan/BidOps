# ADR-0003：企业内部部署基线

日期：2026-10-22。授权来自新增企业 Agent App 目标，详见 GOAL.md。

## 框架调查与决定

| 能力 | 采用 | 理由与边界 |
|---|---|---|
| Agent / 评测 | 已有 Pydantic AI / Pydantic Evals 2.59.0 | 保持已评测的工具、类型与证据契约；不重写编排循环 |
| 企业身份 | Authlib 1.9.1 + Starlette SessionMiddleware | 框架处理 OIDC、JWT、state、nonce、PKCE；应用映射角色、保存本地会话 |
| IdP | 已有企业 OIDC；可自托管 Keycloak | 不创建自制密码库；尚无企业 IdP 参数，不能声称实际 SSO 已验收 |
| 会话历史 | Pydantic AI ModelMessagesTypeAdapter + SQLite | 使用框架消息序列化，只加载服务端历史；不接受客户端注入历史 |
| 限流 / 指标 | limits 5.8.0 / prometheus-client 0.26.0 | 使用框架计数器与指标，不建设自定义限流或遥测平台 |
| 部署 | 非 root 容器 + 持久卷 + 企业 HTTPS 反向代理 | 单企业单进程；启动文件锁阻止同库第二个应用工作进程 |

编码前查阅的官方来源：

- https://docs.authlib.org/en/stable/oauth2/client/web/starlette.html
- https://docs.authlib.org/en/latest/oauth2/client/web/
- https://pydantic.dev/docs/ai/core-concepts/message-history/
- https://www.keycloak.org/server/containers
- https://limits.readthedocs.io/en/stable/quickstart.html
- https://prometheus.github.io/client_python/instrumenting/histogram/

Authlib 自动校验 ID token 与 nonce；本项目额外固定配置中的 issuer，限定 RS256，校验
回调目标与元数据端点，并对真实签名协议做负向测试。Keycloak 官方明确 start-dev 不用于
生产，因此不把默认密码的开发 IdP 放进生产启动配置。本次提供可连接企业 IdP 的适配器。

## 请求与持久化

1. 浏览器从同源 `/api/auth/me` 获取本地身份和 CSRF token；OAuth token 不传给 JavaScript。
2. Authlib 执行 code + PKCE 和 ID token 验证。一次性 state 存数据库，签名 HttpOnly cookie
   只含绑定标记/随机应用会话。登录记录保存 opaque token 的 SHA256，默认一小时有效。
3. 每个业务端点检查权限。会话表与历史查询携带由 issuer/sub 派生的 owner_id；公告库
   是一个组织共用的公开资料库，不宣称租户数据隔离。
4. 请求须携带 Idempotency-Key；数据库事务按 owner/key 认领请求，按会话禁止同时写入。
   完成结果与框架消息落库后可复用；崩溃时未知执行状态不自动重试，保留成本预留。
5. Pydantic AI 仍负责模型、工具、输出重试。请求存储只是 API 适配层，不是另一套 Agent 框架。

## 审计、隐私与故障

- `enterprise_audit` 通过 SQLite trigger 拒绝 UPDATE/DELETE。它不能抵抗数据库管理员的
  修改；需要该保证的企业必须外接受控审计存储。
- 会话正文、证据片段、登录名称在私有数据库中保存。默认 30 天，启动时和每小时清理；
  审计与计费摘要继续保留。备份由运维另设留存，源数据不随聊天清理。
- 退出撤销本地会话，不代表退出 IdP。IdP 禁用账户/变更角色不即时推送到本地；默认最多
  一小时后重新认证，需要即时撤销的企业在 E2 接入 back-channel logout / introspection。
- 本机 SQLite 用于有界的内部试用。单进程最多 2 个活跃 Agent、每人默认 20 请求/分钟、
  每会话 8 轮、最多 50 个未清理会话；不是高可用架构或承诺的并发 SLA。
- 数据质量、身份接入和实际负载仍是独立上线门槛；不使用旧 Demo 分数推断企业准备度。
