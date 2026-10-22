# B04 候选与资格清单实际验收

实现 [Draft PR #19](https://github.com/ZZZZihan/BidOps/pull/19)，计划 B04，分支 `codex/b04-qualification-screening`，堆叠于
[B03 #18](https://github.com/ZZZZihan/BidOps/pull/18)。本 PR 增量为固定候选批次、PAI 条件抽取/
核验、双方证据与私有历史、受保护 API/UI，以及共享费用恢复屏障。没有合并或部署。

**专项工程 C 验收 PASS；整条堆叠链代码合并 BLOCKED。** 前置 B02-06 的真实旧六公告库/raw/
历史会话尚未提供。当前 Head 远端 CI 在 PR 描述单列，不用本机结果冒充远端结果。
**业务验收 BLOCKED。** 无新真实语义评测、独立人工金标/计时、真实企业材料或真实 IdP/HTTPS。

## 被测版本、输入与执行者

- Base B03：`b2a26830c0274734cc4db5d72e461657da923da8`。
- B04 实现：`427307e7f050840cf6c838de0e35ece4c278a3bf`；整合并最终验证：
  `3f04b045f0d029dadb4b882a293f31776400cc30`，tree
  `3fedf82018b2186bc59a6e26ac585db6d33f3178`。验证前后工作树干净。
- 执行：2026-10-22 18:06:48–18:07:45 UTC（2026-10-23 Asia/Shanghai），主 Agent。
  Python 3.12.12、Pydantic AI 2.59.0、Authlib 1.9.1、Node 26.11.0、uv 0.10.0。
- 数据为版本固定的 UNIT 工程画像、正文及 UTF-8 附件，真实 SQLite/文件；身份传输为签名
  OIDC 夹具。各测试显式设置 owner/analyst/viewer/admin、来源 ACL、时点及哈希，前置数据
  位于该提交的 `tests/test_qualification_*.py` 和 `tests/fixtures/screening_browser.py`。
- 真实 DeepSeek/采购来源请求 0，新增费用 ¥0。实际替身响应标识 `offline-unit` 与
  `offline-screening-browser`；`models.ALLOW_MODEL_REQUESTS=False`，不构成真实模型成绩。
  单条完整浏览器流程发生 8 次 FunctionModel 请求（含一次材料工具往返）；候选和 GET 重读为 0。
  替身 token 用量不代表供应商用量。预算负测中的模型名称/用量是明确构造的测试输入。
- 提示 `qualification-v1`、规则 `qualification-logic-v1`。浏览器默认 max_requests=12、
  max_seconds=90、max_segment_chars=4000、max_request_bytes=120000、max_tokens=6000，
  temperature=0、SDK retry=0；完整服务四条件夹具显式 max_requests=20。
  生产参数、实际响应模型、费用/预留及各版本保存在每次结果的 provenance/manifest。
- 主 Agent 实现候选、运行时、预算接线、API与集成；3 个子 Agent 分别承担条件逻辑/独立技术
  复核，固定来源/历史/费用恢复与 HTTP 负测，以及 UI/真实浏览器夹具。交叉复核覆盖未参与
  实现的模块。均显式请求 GPT-6.1 Sol / ultra，不能独立核验 provider 档位；主 Agent max
  已获本次许可。这些技术复核不代替独立人员的业务人工验收。

## 实际执行

[verification.json](evidence/b04-20261023/verification.json) 记录完整代码 SHA、命令、时间与退出码。

| 命令 | 实际结果 | 原始证据 |
| --- | --- | --- |
| `.venv/bin/pytest -q` | 1101 passed in 36.98s | [backend](evidence/b04-20261023/backend.txt) |
| `.venv/bin/ruff check .` | All checks passed | [lint](evidence/b04-20261023/lint.txt) |
| `.venv/bin/ruff format --check .` | 143 files already formatted | [format](evidence/b04-20261023/format.txt) |
| 冻结 B01 精确 pin 评分入口 | PASS，Agent score=null，真实业务仍 BLOCKED | [freeze](evidence/b04-20261023/frozen-scorer.json) |
| `npm run build` | TypeScript/Vite 成功 | [build](evidence/b04-20261023/frontend-build.txt) |
| `BIDOPS_BROWSER_CHANNEL=chrome npm run test:e2e` | 39 passed in 14.6s；含既有20、B04协议16与真实后端3 | [browser](evidence/b04-20261023/frontend-browser.txt) |

定向入口为 `.venv/bin/pytest -q tests/test_qualification_*.py`；前端在 `frontend` 运行
`BIDOPS_BROWSER_CHANNEL=chrome npx playwright test tests/screening.spec.ts tests/screening-real.spec.ts`。
真实后端夹具不 mock screening HTTP；使用实际 FastAPI、SQLite、文件、ACL、PAI Agent/工具，
只替换外部 OIDC transport 和模型语义答案。所有工程结果均不计入 B08 样本或准确率。

## 专项验收矩阵

各行共用上述完整代码 SHA、输入版本、执行时间与人员；链接测试包含前置权限、操作、明确
预期及实际执行断言，完整运行日志覆盖这些文件。新增恢复费用行为补充 B04-08，不降低原标准。

| AC-ID | 输入、操作与预期 | 实际观察与证据 | 状态 |
| --- | --- | --- | --- |
| B04-01 C | 同画像、命中/排除/未知及501之后目标；固定批次全量分页 | [candidates](../tests/test_qualification_candidates.py)23项覆盖全量、未知预算不归零、元/万元归一、版本歧义及最终释放权限竞争；真实页面批次3单元=候选/排除/待核实各1；0模型请求 | PASS |
| B04-02 C | AND/OR/NOT/例外、包范围、超过3条与附件、超长段/漏段 | [requirements](../tests/test_qualification_requirements.py)与[logic](../tests/test_qualification_logic.py)：稳定ID、逻辑/跨度/完整覆盖；真实浏览器正文4+附件1共5条件；未处理与超限整行显式记录 | PASS |
| B04-03 C | 充分/不符/缺失/冲突/不适用证明，时效边界 | 171项条件/逻辑检查通过；缺证明不判满足，未知否定不变成证明；真实浏览器五态各1，展开双方原文、版本与哈希 | PASS |
| B04-04 C | 假引用/页码/版本/权限/有效期与假人审字段 | [sources](../tests/test_qualification_sources.py)、[service](../tests/test_qualification_service.py)拒绝伪造；三种伪造有效期拒绝；真实PAI验证重试有界；全部结果unreviewed | PASS |
| B04-05 C | 硬不符/未知/冲突/终止/不新鲜/更正不明 | 聚合真值表及完整ProjectStatus守卫；更正未决不把旧版不符当最新硬排除；模型没有approve工具，终止不能产生资格通过 | PASS |
| B04-06 C | 运行后变更画像/公告/观察/提示配置，重读历史 | [history](../tests/test_qualification_history.py)：原manifest/result/provenance不变，stale分开报告；GET不重跑；raw仅私有数据库；错误锁路径仍保存结果并诚实记录null | PASS |
| B04-07 C | 注入公告、闭包外材料、假approve、格式错/超时/预算耗尽 | [tools](../tests/test_qualification_tools.py)实际3条PAI工具循环；[api](../tests/test_qualification_api.py)16项实际HTTP负测；[budget](../tests/test_qualification_budget.py)请求/模型/用量/并发限制；失败保存未完成条件，撤权不释放私有部分结果 | PASS |
| B04-08 C | 有/无ledger旧备份，两次恢复，缺/错/旧/正确标记，新旧模型入口 | budget51项含实际backup→restore→Store重开；两入口均拒错标记503且原费用逐列不变；正确标记仍保留已用/未知预留并受累计10元上限；fresh新库无屏障 | PASS |

## 共同门槛

| ID | 实际依据 | 状态 |
| --- | --- | --- |
| G-01 | B04-01～08映射业务、权限、持久历史和费用恢复；容器版本依据归B04-06 | PASS |
| G-02 | 整合代码1101后端/39浏览器及lint/build/freeze全部通过；远端当前HEAD另记 | PASS |
| G-03 | 007增量/重复迁移与实际文件/DB恢复；历史私有正文隔离、原费用保留、到期结果清理、先拒绝后重新授权；前置B02真实旧六库仍BLOCKED | PASS |
| G-04 | enterprise-only、后端角色+owner+全部对象来源、CSRF；最后释放原子复查；普通API不含raw，公开证据只有UNIT数据 | PASS |
| G-05 | 无新增依赖；PAI2.59.0拥有schema校验、工具循环与重试；复用FastAPI/SQLite/Authlib，无自制Agent循环或队列 | PASS |
| G-06 | 真实模型/来源0、费用¥0；新旧共用累计账本，恢复不释放unknown；最新真实账本缺失，live默认关闭 | PASS |
| G-07 | 日志绑定完整3f04b045及干净树；提示/规则与运行manifest保存，18个B01冻结文件逐项一致 | PASS |
| G-08 | 当前版本关闭功能仍清理派生私有正文；真实备份恢复到独立目录、新结果隔离可重新授权，GET不重跑；回旧版不得覆盖升级后结果/账本 | PASS |

## 保留的失败、修正与证据边界

首次全套在未提交工作区为1068 passed/1 failed：`test_migrations_idempotent` 的期望列表未纳入
007。补齐新迁移断言，未删除任何业务断言；最终提交全套1101通过。独立复核还推动修复了候选
金额二次换算、项目级多版本歧义、跨来源最后释放竞争、伪造证据有效期、无ledger旧备份费用
屏障，以及保存时锁路径错误导致结果丢失。最终相应负例均进入完整套件。

预算检查使用本地实际SQL事务和受控ModelResponse，不能代表供应商账单正确或保证未来价格。
真实DeepSeek当前模型/价格验收未执行；费率采用当次官方峰时cache-miss上界配置（输入2元/M，
输出8元/M），未知响应和异常用量保留预留，实际模型标识不确认则停止。官方依据：
[DeepSeek pricing](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)，
[chat completion](https://api-docs.deepseek.com/api/create-chat-completion/)。
PAI框架依据：[Agents](https://ai.pydantic.dev/agents/)、[Testing](https://ai.pydantic.dev/testing/)。

## 运行、回退及剩余工作

完整配置/API/模型边界、007迁移、费用恢复标记、Docker版本参数及回退命令见
[QUALIFICATION-SCREENING](../docs/QUALIFICATION-SCREENING.md)。三个开关默认false：
`BIDOPS_SCREENING_ENABLED`、`BIDOPS_SCREENING_LIVE_CALLS_ENABLED`、
`BIDOPS_SCREENING_LEDGER_VERIFIED`。生产构建传实际`BIDOPS_CODE_SHA`并保留实际uv.lock；
未提供依据时记录unknown，不伪造完整性。

仍缺旧六公告原库/raw/历史会话及最新费用账本；真实企业材料、独立金标/计时人员、真实IdP、
HTTPS及试用用户对应B08/B10保持BLOCKED。B05下一步接持久任务、取消/进度、框架检查点、
费用不确定窗口及一致恢复；B06再接真实授权人工决定。B04仅保存读取，不能替代这些能力。
