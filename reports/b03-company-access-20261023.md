# B03 企业画像、材料、授权与撤销实际验收

实现 [PR #18](https://github.com/ZZZZihan/BidOps/pull/18)，堆叠于 [B02 #17](https://github.com/ZZZZihan/BidOps/pull/17)，未合并、未部署。
执行日期 2026-10-23（Asia/Shanghai）；原始日志使用 UTC。

**B03 专项 C 门槛：PASS。代码进入 main 的条件：BLOCKED。** 本 PR 自身的工程行为通过，
但前置 B02-06 真实旧六公告迁移输入缺失，堆叠链仍不可宣布具备合并条件。
**业务验收：BLOCKED。** 没有真实企业材料、真实 IdP/HTTPS 或独立用户验收。
当前 PR HEAD 的远端 CI 状态在 PR 描述单列，不能将本机通过冒充 CI 通过。

## 被测对象、输入与执行者

- B02 base：`c3435402247d0cd66986a0bbd8854304c017f864`。
- B03 独立实现：`2f24256cb8af82ded31d14d62be1f3fd568bd240`，672 后端、18 浏览器通过。
- 与 B02 集成：`65f6697f1e518ef638bfd32c016762990e174ff1`，791 后端、20 浏览器本机通过。
- 最终验证代码：`88d1f30d592678daa586c67818100dafff83f8d0`；tree
  `276ee95928dcdbb67a473295e071713e83de2c16`；验证前后工作树干净。
- 最终执行：2026-10-22 17:40:47–17:41:25 UTC，主 Agent 执行。
  环境 Python 3.12.12、Pydantic AI 2.59.0、Authlib 1.9.1、Node 26.11.0、uv 0.10.0。
- 输入为合成企业画像、UTF-8/HTML 工程材料、真实 SQLite/本地文件，以及受控签名 OIDC
  传输夹具；没有真实企业私人资料。测试固定 analyst/viewer/admin/未授权主体、对象 ACL、
  精确留存时钟、证据版本/哈希及更新顺序；每个测试保留具体输入与预期断言。
- 主 Agent 实现公司对象服务、API、留存/备份集成，核对结果并运行完整验证。
  `b03_identity_adapter` 实现身份撤销适配与公司页面，并复核主 Agent 的集成/备份；
  `b01_eval_audit` 独立复核对象服务及恢复；`b01_contract_audit` 独立复核身份和 UI 行为。
  三个子 Agent 均显式请求 GPT-6.1 Sol / ultra；实际 provider 档位不能独立核验。
  主 Agent 按用户本次例外使用 max。多 Agent 技术复核不等于独立用户人工验收。

## 实际执行记录

[verification.json](evidence/b03-20261023/verification.json) 记录完整命令、SHA、时间与退出码。

| 命令 | 实际结果 | 证据 |
| --- | --- | --- |
| `.venv/bin/pytest -q` | 791 passed in 18.21s | [后端日志](evidence/b03-20261023/backend.txt) |
| `.venv/bin/ruff check .` | All checks passed | [lint](evidence/b03-20261023/lint.txt) |
| `.venv/bin/ruff format --check .` | 117 files already formatted | [format](evidence/b03-20261023/format.txt) |
| B01 固定 pin 离线评分入口 | PASS；Agent score null，业务仍 BLOCKED | [冻结结果](evidence/b03-20261023/frozen-scorer.json) |
| `npm run build`（frontend） | TypeScript/Vite 成功 | [构建](evidence/b03-20261023/frontend-build.txt) |
| `BIDOPS_BROWSER_CHANNEL=chrome npm run test:e2e` | 20 passed，含 6 条真实 FastAPI/SQLite 公司流程与 14 条既有界面流程 | [浏览器](evidence/b03-20261023/frontend-browser.txt) |

新增精确检查入口：

```sh
.venv/bin/pytest -q tests/test_company_access.py tests/test_identity_revocation.py \
  tests/test_company_restore.py tests/test_enterprise_backup.py tests/test_enterprise.py
cd frontend
BIDOPS_BROWSER_CHANNEL=chrome npx playwright test tests/company.spec.ts
```

B01 的 18 个冻结文件、pyproject.toml/uv.lock 均未修改；冻结 SHA
`22a2adf96f07b9a3e9c1493dae2cebbe2499b6e05db21f2b52855686c2afffea`，输入 SHA
`eda43a451ae978251de4be003f1c062d1a4d3e93db9249c9c5458966628f44fb`。

## 专项验收

每行共用上述完整代码 SHA、环境、执行者和时间；链接的测试包含前置权限、固定版本、
输入数据、操作与具体预期，原始日志记录实际执行。

| AC-ID | 输入、操作与预期 | 实际观察与证据 | 状态 |
| --- | --- | --- | --- |
| B03-01 C | 创建/修改画像和材料；自报/有证明/缺失/冲突；旧版本不得变新内容，竞争写409 | [access](../tests/test_company_access.py)：不可变版本、固定哈希、expected_version、来源闭包通过；真实浏览器完成上传→引用→画像新版本；21画像跨页保留所选旧版本 | PASS |
| B03-02 C | analyst/viewer/admin/无权主体调用列表、详情、下载、检索、引用、导出 | 实际 HTTP 入口拒绝无权读取；admin 无隐含正文权；grant/revoke 浏览器检验200后404；角色降为viewer立即失去修改能力；错误后清除页面私有正文及草稿 | PASS |
| B03-03 C | 旧工具上下文、别人的材料ID、来源撤权、伪造引用/路径、后续入口合同 | 共享 require_inputs 固定全部来源版本并在释放前再检查；伪造页码/ID/哈希/符号链接路径均拒绝。后续 B04/B05/B06 接线仍须各自验收 | PASS |
| B03-04 C | 已登录期间停用、撤权和角色降级；旧固定上下文重用 | 下一次实际请求/服务调用拒绝；旧 Cookie 和 context 不缓存对象授权；本地停用不会因再次SSO自动复活 | PASS |
| B03-05 C | 证书有效期在所需截止前/当日/之后，缺日期/文件/冲突 | qualification_at 依据资格时点判断，缺证明与明确不符分开；材料留存不是证书有效期。浏览器核对缺日期显示信息不足 | PASS |
| B03-06 C | 备份后撤权/停用/删除，再丢失实例恢复；到期和unlink失败 | [restore](../tests/test_company_restore.py)：真实临时目录/SQLite复制恢复；旧登录、角色快照、ACL清空，所有私有对象隔离；fresh SSO仍不能读，须逐对象重授。v3统一公告附件和私有闭包；公共v1/v2、带期限私有v2兼容；无期限私有v2（含仅画像）拒绝；默认7天/最大30天窗口，到期恢复拒绝 | PASS |
| B03-07 C | 伪格式、中文/穿越文件名、超限流、HTML资源边界、ZIP炸弹 | 2MiB/20万字符/1万节点/64层/100属性边界实测；归档拒绝且不解压；原始HTML不执行；非精确上传路由不放大请求上限 | PASS |
| B03-08 C | Keycloak受控协议端发送有效/伪造/重放/乱序注销和角色事件、JWKS故障 | [identity](../tests/test_identity_revocation.py) 39项协议检查：RS256签名、issuer/audience、jti重放、iat水位通过；旧admin不覆盖新viewer，同秒冲突撤销到严格更新iat；精确240秒失效≤5分钟；不支持IdP配置拒绝。非真实IdP验收 | PASS |

## 共同门槛

| ID | 实际依据 | 状态 |
| --- | --- | --- |
| G-01 | B03-01～08均有实际输入/操作/观察/代码与日志；增量范围包括对象存储、身份适配、UI和恢复，不重复计B02功能 | PASS |
| G-02 | 最终88d1f30完整791后端/20浏览器、lint/构建/冻结通过；远端当前HEAD另记，原失败保留下文 | PASS |
| G-03 | 005/006增量及重复迁移通过；旧会话/公共备份与新私有闭包、清理失败、拒绝部分恢复均实测。B02真实旧库缺口仍是前置阻塞 | PASS |
| G-04 | 全部公司入口后端授权/CSRF，主体角色与对象动作双检查；公开资料只有UNIT工程样例，未加入凭据/真实私人资料 | PASS |
| G-05 | 无新依赖，使用锁定Authlib/joserfc与现有FastAPI/SQLite；[ADR-0004](../docs/ADR-0004-IDENTITY-REVOCATION.md)记录官方协议和兼容范围 | PASS |
| G-06 | 真实模型/采购来源请求0，新增费用¥0；协议请求仅本机/MockTransport；不重置累计¥10预算 | PASS |
| G-07 | 最终日志绑定完整88d1f30、干净树；独立/集成旧结果分别标SHA；真实IdP和用户验收保持BLOCKED | PASS |
| G-08 | 企业关闭返回404，不匿名降级；升级前备份恢复到独立目录，原公告/会话可读；新私有数据隔离保存，禁止破坏性降级 | PASS |

## 实际失败、修复与独立复核

复核推动关闭了同秒角色冲突、旧角色覆盖、派生来源末次授权、历史画像留存显示及
未编辑材料留存自动重置等问题。最终浏览器断言保存前后保留截止时间的37.123秒精度。
未参与相应实现的 Agent 在集成上运行183项定向检查，并以66条 TestClient 断言验证
公告入口两种别名的匿名/CSRF/停用/过期身份边界，均通过。

[首次集成 CI](https://github.com/ZZZZihan/BidOps/actions/runs/39823891785) 后端和容器成功，
浏览器16 passed/1 failed/3 did not run：授权成功通知与processing状态同时出现，
宽泛 getByRole('status') 触发严格定位冲突。88d1f30 仅对8处成功状态定位增加准确文本过滤，
保留全部原成功文本、版本、HTTP权限及留存断言；独立 Agent 复核确认未降低检查。
最终本机重跑20项全部通过，远端修复后检查以PR当前HEAD记录为准。

## 运行、回退与剩余条件

配置、文件支持范围、全部入口、留存和备份/恢复命令见
[COMPANY-ACCESS](../docs/COMPANY-ACCESS.md)。真实企业上线前仍需授权材料、实际Keycloak
客户端/角色映射、HTTPS域名和独立用户（B10），本机协议模拟不能替代。

实际模型标识null，提示/参数不适用，调用/tokens/新增费用0。最新费用账本和旧六公告
原库/raw路径仍未提供；不运行真实模型或采购来源请求。B04接入已经开始；B05的恢复实验
与后续B06等工程工作继续，不等待PR合并。所有PR仍由用户决定是否合并/部署。
