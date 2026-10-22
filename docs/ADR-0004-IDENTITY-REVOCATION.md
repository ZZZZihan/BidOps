# ADR-0004：Keycloak 撤销事件与有界身份快照

状态：B03 本地协议实现；真实 IdP、真实 HTTPS 和账号验收仍为 B10 BLOCKED。

## 问题与决定

原企业登录只在登录回调时读取角色，本地会话可持续一小时。仅删除浏览器 Cookie
无法传递 IdP 的会话撤销，也无法让账号停用或角色变更在五分钟内可靠阻止下一次访问。

继续使用现有 Authlib OIDC Authorization Code + PKCE + nonce + 单次 state 登录链路，
增加明确限于 **Keycloak OIDC Back-Channel Logout、RS256 签名、非加密 Logout Token**
的适配。签名解码使用已锁定 Authlib 1.9.1 本身使用的 `joserfc`，不实现密码、JWT
密码学或第二套 OIDC 登录框架。不将其他 IdP、旧 Keycloak Java Adapter 的 Admin URL
私有格式、Front-Channel Logout、JWE 或其他签名算法宣称为兼容。

Back-Channel Logout 只表示会话注销，不保证每次用户停用或角色变更都会产生事件。
因此它是提前撤销通道，**240 秒身份/角色快照期限才是没有事件时的强制上界**。
此上界描述下一次受保护请求或共享授权检查；已经发送的正文不能被追溯撤回，运行中任务
的工具执行与结果释放须按 B05/B06 契约重新检查授权。

## Keycloak 部署契约

- Confidential OIDC Client，启用 Standard Flow；保留现有 client secret、PKCE S256 和
  精确的 `https://<origin>/api/auth/callback` redirect URI 配置。
- `BIDOPS_ENTERPRISE_IDENTITY_PROVIDER=keycloak` 是当前唯一支持值；
  `BIDOPS_ENTERPRISE_IDENTITY_SNAPSHOT_SECONDS` 默认 240，允许 30–240，不能提高上限。
  原 `SESSION_SECONDS` 仅限制 Cookie/应用登录最长寿命，不能延长可信快照。
- 配置可信的 `bidops_roles` ID Token Claim mapper，值为字符串数组；只接纳应用已定义的
  viewer、analyst、auditor、admin。角色来自经过 Authlib 验证的 ID Token，不从请求体、
  Header、未验签 UserInfo 或客户端传入的对象上下文授予。
- Front channel logout 关闭；Backchannel logout URL 设置为
  `https://<origin>/api/auth/backchannel-logout`；建议开启 Backchannel logout session
  required，使 ID Token/Logout Token 包含 `sid`。也支持标准的 `iss + sub` 全主体注销。
- OP 必须能够通过 HTTPS 到达该 URL。使用 realm discovery/JWKS，Issuer 与配置精确匹配。
  仅明确允许的 localhost/127.0.0.1 测试配置允许 HTTP。
- 登录请求始终带 `prompt=login`、`max_age=0`，快照过期后需重新通过 Keycloak 登录；
  该重新登录签发的 ID Token 必须反映当前可用账号及角色 mapper 结果。不能仅延长旧本地
  会话，也不保存 provider access/refresh/ID token 供浏览器使用。

生产上线仍须在选定 Keycloak 版本、角色 mapper、用户停用、密钥轮换和网络路径上完成
B10 实测。本 ADR 与模拟测试不证明真实管理台配置已经存在。

## 持久状态与兼容迁移

`006_identity_refresh.sql` 在 `005` 之后执行，新增以下内部表；005 不依赖它们的外键。

| 表 | 用途与字段契约 |
| --- | --- |
| `enterprise_subjects` | `owner_id` 主键；`issuer`、`subject` 唯一组合；`status` 为 active/disabled；`roles_json` 为可信当前角色；`roles_checked_at`、`fresh_until`、`updated_at` 为 Unix 秒 |
| `enterprise_login_identities` | 以原 `token_hash` 关联登录，保存 issuer/subject/sid、`authenticated_at` 和该浏览器独立的 `fresh_until`；原登录删除时级联删除 |
| `enterprise_logout_receipts` | issuer + SHA256(jti) 唯一收据；保存签发/接收时间，跨进程持久去重，不存原 JWT |
| `enterprise_identity_revocations` | issuer + sub 或 sid 的单调撤销水位；sid 可附 sub 限定，避免组合不匹配污染其他主体；乱序事件只取最大签发时间 |

保留 `enterprise_logins` 原六列，原备份夹具及读写约定无需修改。旧登录不回填可信身份
快照；升级后必须重新 SSO。迁移不把历史 session 当作当前 IdP 身份证据。

SSO 成功将当前主体角色和快照期限更新到数据库；期限是 `min(本地接收时间, iat) + TTL`，
不能用过旧 ID Token 延长。更新已有主体绝不把 disabled 改回 active。已停用主体即使取得
新签名 ID Token 也不能建立应用登录。owner 仍由 issuer/sub 稳定派生，不接受外部 owner。

`roles_checked_at` 保存已验签的 ID Token `iat`。更旧签发的延迟回调不能覆盖新快照。
相同秒内如角色不同，协议没有足够信息判断先后，因此持久清空该主体角色与快照、撤销
全部登录后拒绝回调；同秒再次签发不能恢复，只有严格更新的 iat 可以重新建立快照。
该拒绝在撤销事务提交后返回，不能因抛出异常回滚撤销。新的空角色快照也立即撤销授权。

每次 HTTP 身份解析都同时检查应用登录有效期、该浏览器快照、主体 active 状态和主体
快照。角色取登录角色与当前主体角色的交集，因此其他浏览器重新认证不会给旧 Cookie
增加角色。共享对象授权服务每次通过 owner 查询主体 active、fresh_until 及当前角色，
不能相信旧任务上下文里的角色。

## 回调验证、重放和乱序

端点只接收 POST `application/x-www-form-urlencoded`，恰好一个 `logout_token`，
请求体最多 16 KiB。普通浏览器登出仍需原有 Origin/CSRF；唯一此 OP 回调使用协议签名
认证，不要求 Cookie、Origin 或 CSRF。

1. 复用 Authlib 的可信 discovery、JWKS transport 与 10 秒请求超时；发现未知 `kid`
   后强制刷新 JWKS 一次。仅从已验证 discovery 的 HTTPS JWKS URL 取密钥，不信任
   JWT Header 中的 `jku`、`jwk` 或 `x5u`。
2. 用 `joserfc` 验证 RS256 签名；拒绝 none、HS256、错误签名和非支持的显式 token type。
3. 检查精确 issuer、包含当前 client ID 的 aud、整数 iat，iat 最多早于接收 300 秒或
   晚于接收 30 秒。若存在 exp，必须为未来且晚于 iat 的整数。
4. 检查非空 jti、至少一个非空字符串 sub/sid、指定 backchannel-logout events 对象，
   且绝不能存在 nonce。未知业务 Claim 不参与授权；仅支持上述明确协议格式。
5. 在同一数据库事务中持久插入 jti 收据、推进撤销水位、清除匹配主体的快照并删除其
   全部本地浏览器登录。收到 sid 时按 issuer/sid 以及可选 sub 匹配，再保守注销该主体
   所有本地会话，使非 HTTP 上下文也立即失效；这比只关闭一个浏览器更保守。
6. 对重复 jti 返回 200 且不重复处理；合法但已不存在的会话返回 200。合法乱序事件只
   撤销和推进最大水位，不写 active、不授角色。错误签名/Claim 返回 400；发现或 JWKS
   不可用返回 503，不改变现有可信记录；快照期限不因失联顺延。

已撤销 sid 不能用同一 OP 会话重新建立本地登录；需新的 OP 会话。sub 撤销后，iat 不晚于
水位的旧 ID Token 不能重建会话。新的真实登录可以更新身份快照，但不能恢复本地停用
状态，也不能授予材料对象 ACL。

收据与撤销水位当前持久保留，不自动按业务历史清理。它们位于私有数据库，不进入普通
日志；日志只记录固定事件名，不记录 JWT、原 jti、sid、provider token 或 Header。

## 失联、恢复与接线义务

受保护请求不依赖临时 IdP 在线检查：在最后一次可信快照范围内可用，到期即 401，不能
用缓存继续访问。`/api/auth/login` 发现失联返回 503；回调换码/验证失败清除当前浏览器
本地登录。单个网络请求超时不会延长 240 秒上界。

恢复旧备份后，原 restore 流程删除登录。公司授权层还必须启用恢复隔离、按独立撤权依据
对账或重新授权，不能仅凭一次新 SSO 自动恢复备份内 ACL；身份恢复与对象授权恢复是
两个不同决定。本适配不声称单独解决 B03-06 的旧 ACL 恢复问题。

API 集成：现有 `auth_router()` 已包含端点，无需另挂 Router。如应用将来增加全局身份
或 CSRF 中间件，只对 **POST `/api/auth/backchannel-logout`** 精确豁免，继续执行协议
验证和 16 KiB 上限；不能豁免 `/api/auth/*` 或其他方法。Operations 现有 16 KiB 上限
适用，不能将 token/原路径参数写入监控标签或请求日志。

## 本地证据与剩余验收

`tests/test_identity_revocation.py` 使用真实 RSA 签名、Authlib OIDC 登录与 HTTPX2
MockTransport 提供 discovery/JWKS/token；测试覆盖正常/伪造/错误字段、jti 重放与服务
重建、乱序水位、JWKS 轮换/不可用、主体启停、角色交集、无事件时精确 240 秒到期和
浏览器 Cookie 恢复；还覆盖延迟旧角色回调、同秒冲突持久撤销及严格更新 iat 才能恢复。
身份新增 39 项，与现有企业协议/备份一起运行 87 项通过，原断言保持不变。

B03-02 页面接线使用实际公司 API：画像版本、材料上传/下载、摘录与事实来源、留存期、
资格结果及对象授权均由服务端判定。Vite 开发和预览代理保留 `/api` 前缀，使这些路径与
认证路径确实到达既有后端路由。`frontend/tests/company.spec.ts` 的 6 项浏览器测试启动
真实 FastAPI/SQLite/ACL 与 Vite 代理，仅外部 OIDC transport 使用真实签名协议模拟；
覆盖匿名拒绝、版本冲突、中文材料、缺证据/有效期未知不满足、下载、检索及跨账号授予/
撤销。另验证 403 拒绝后清除全部私有缓存、撤权后列表仍返回 200 时固定对象和引用来源
的重新授权核查，以及跨分页保留仍有权限的固定版本。画像详情与历史读取返回对象当前
留存期限，页面不依赖列表分页推断；画像和材料未修改期限的更新分别省略该字段或请求头，
保留服务端精确时间。跨页读取历史、旧版本更新拒绝以及画像/材料保留期限后保存新版本
有实际后端浏览器回归。
全套前端 18 项通过（其中另 12 项
为已有界面 fixture 测试），构建通过，390px 页面已做渲染截图检查且无横向溢出。
浏览器夹具显式关闭 Agent 并禁止真实模型调用；页面验证不等于真实 IdP 或生产部署验收。

这些是可控协议端证据，不是真实 Keycloak、真实 HTTPS、人工账号角色验收、真实队列
运行中撤权或生产部署证据。B05/B06 负责运行/恢复/结果释放的再次授权；B10 负责真实
issuer/client/mapper/HTTPS 与账号验收。未配置这些环境时保持 BLOCKED。

## 官方依据与锁定版本核对

- [OIDC Back-Channel Logout 1.0 最终版](https://openid.net/specs/openid-connect-backchannel-1_0-final.html)：
  标准表单、签名、iss/aud/iat/sub/sid/events/nonce 与重放/响应语义。
- [OIDC Back-Channel Logout 1.0](https://openid.net/specs/openid-connect-backchannel-1_0.html)：
  较新文本还要求 exp；本 Keycloak 兼容配置接纳最终版无 exp token，若发送 exp 必须验证。
- [Keycloak Server Administration Guide — OIDC Logout](https://www.keycloak.org/docs/latest/server_admin/index.html#_oidc_clients)：
  Backchannel logout URL、session required、与旧 Admin URL 私有格式的区别。
- [Authlib Starlette OAuth Client](https://docs.authlib.org/en/v1.6.5/client/starlette.html)：
  OAuth registry、`authorize_redirect`、`authorize_access_token` 与自动 `userinfo` 解析。
  最新文档入口访问返回 404，故该页面仅作既有 API 的官方说明；实际行为以本地锁定的
  **Authlib 1.9.1** 源码及签名协议测试核对：`AsyncOpenIDMixin.fetch_jwk_set(force=True)`、
  `parse_id_token` 使用 `joserfc`/`KeySet`，`StarletteOAuth2App.authorize_access_token`
  自动校验 nonce/ID Token，未假设旧文档即为 1.9.1 的完整兼容证明。
