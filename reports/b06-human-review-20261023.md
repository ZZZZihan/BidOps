# B06 逐项人工审核与补材料恢复实际验收

实现 [Draft PR #21](https://github.com/ZZZZihan/BidOps/pull/21)，分支 `codex/b06-human-review`，
堆叠于 [B05 Draft #20](https://github.com/ZZZZihan/BidOps/pull/20)，没有合并或部署。
B06-01～07 专项工程结果 PASS；整条堆叠链代码合并 BLOCKED，前置 B02-06 的真实旧六公告
数据库/raw/历史会话未提供。业务与真实企业验收 BLOCKED，不以工程身份夹具代替真实用户。
远端当前 Head CI 单独列在 PR 描述；本机结果不代替远端构建。

## 固定版本、输入和执行人员

- Base `4fd057ef9141d31d53540532683418e09963e324`；完整被测代码
  `487649aec8260ce494d7a4cf1f41bae2c4ec1f26`，tree `c006c844cf8421ffba3e00bd1d9144c6e4116352`。
  验证前后工作树干净，后续只追加本报告及证据。
- 全量执行 2026-10-22 19:47:12–20:04:33 UTC（2026-10-23 03:47:12–04:04:33 Asia/Shanghai）。
  Python 3.12.12 / PAI 2.59.0 / DBOS 3.3.0；本机 Chrome 155.0.8059.85。
- B01 的18个冻结文件未变。主锁 `92ceebd17f2e98b0075eac8f13c9108b2686635e1ece5aa4d5f172de10289b27`；
  DBOS overlay `cc7fa6b4de2bd7dc10ca1745b56c5c1f0891edb649732843195a854e098ccd88`。
  提示 qualification-v1、规则 qualification-logic-v1、固定 UNIT 公告/画像/材料及无网签名身份。
- 主 Agent 负责 review service、正式事件接线、API、集成与最终验证；3子 Agent分别承担仓储/
  身份及运行时负测，备份/留存/进程恢复，工作台/实际HTTP浏览器及生命周期测试。均显式请求
  GPT-6.1 Sol / ultra，无法独立核验 provider实际档位；主 Agent max 获本次明确许可。
  未参与对应实现的 Agent 交叉复核仓储/运行时/恢复/工作台，技术复核不替代企业人工验收。
- 原生 SQLite、FastAPI、DBOS、文件、浏览器和 PAI Agent 工具链实际运行；仅外部模型答案及
  IdP传输使用工程夹具。恢复身份为 RS256 验证后持久会话，并经正式人工审核服务，不使用
  Agent身份或 B05内部 review_fixture冒充人审。真实 IdP/独立人员仍未验收。
- 真实 DeepSeek 和采购网络调用0，新增实际费用¥0。四组恢复中 FunctionModel刻意返回
  deepseek-flash标识、100/10 token，走合成计费账本；前三组各¥0.00252、补材料组¥0.00504
  均是构造数值。不能把 `offline=false`、model标识或合成用量当作供应商实际返回/账单。
  最新完整费用账本仍缺，¥10累计预算未重置，live保持关闭。

## 完整实际验证

[verification.json](evidence/b06-20261023/verification.json) 保存SHA、命令、时间和退出码；
[索引](evidence/b06-20261023/evidence-index.json) 保存39个证据文件的字节数与SHA256。

| 命令 | 实际结果 | 证据 |
| --- | --- | --- |
| `.venv/bin/pytest -q --basetemp <受控目录>/pytest` | 1429 passed in 422.36s；B06新增113项 | [backend](evidence/b06-20261023/backend.txt)、[新增清单](evidence/b06-20261023/b06-test-inventory.txt) |
| `.venv/bin/ruff check .` / `format --check .` | PASS；196 files already formatted | [lint](evidence/b06-20261023/lint.txt)、[format](evidence/b06-20261023/format.txt) |
| 精确冻结pin `scripts/screening_eval.py` | PASS，Agent score=null、真实业务BLOCKED | [scorer](evidence/b06-20261023/frozen-scorer.json) |
| `npm run build` | TypeScript/Vite成功 | [build](evidence/b06-20261023/frontend-build.txt) |
| `BIDOPS_BROWSER_CHANNEL=chrome npm run test:e2e` | 105 passed (10.2m)，CLI 0；75旧+28审核协议+2实际后端 | [browser](evidence/b06-20261023/frontend-browser.txt) |

浏览器105项断言全部结束后，本机Chrome派生GoogleUpdater/crashpad继承测试stdout/stderr，
使Playwright等待EOF。对照具体管道端点再次核验PID后，仅TERM本次测试派生进程，runner随后
返回0。不是自然退出；未修改断言、业务代码或其他Chrome进程。[原始收尾证据](evidence/b06-20261023/browser-teardown.txt)
保留PID、命令、管道和时间。远端Linux浏览器是否自然结束须以该Head CI为准。

## 专项验收矩阵

每行共用上述完整SHA、执行时间/人员与工程数据类别。测试链接包含具体权限、版本、样本和
断言；日志证明实际执行。所有PASS均是本PR工程范围，不表示真实企业资格批准。

| AC-ID | 输入、操作和预期 | 实际观察与证据 | 状态 |
| --- | --- | --- | --- |
| B06-01 C | 有assignment和全部来源read的reviewer逐项查看五态/双边证据，不能默认全部批准 | [实际浏览器](../frontend/tests/reviews-real.spec.ts)、[协议](../frontend/tests/reviews.spec.ts)：原判断不改写，缺失/冲突醒目，逐项显式决定；实际API导出完整证据版本 | PASS |
| B06-02 C | 正常决定、同key重放、不同理由、修正及直接UPDATE/DELETE | [repository](../tests/test_review_repository.py)、[API](../tests/test_review_api.py)：事件由服务端会话生成actor/time，历史不可变；同key不多写，异输入409；修正新增事件 | PASS |
| B06-03 C | 两个审核人同旧版本并发决定 | [repository](../tests/test_review_repository.py) SQLite IMMEDIATE和CAS仅一成功；另一409；协议工作台提示刷新而不覆盖 | PASS |
| B06-04 C | 补材料固定新版本、原子关联successor后SIGKILL，独立进程恢复 | [process](../tests/test_review_process_recovery.py)、[四组汇总](evidence/b06-20261023/recovery-summary.json)：2任务/2revision、旧任务取消、旧审核保留，8新item全部unreviewed；旧未消费决定被冻结，新事件恢复同链 | PASS |
| B06-05 C | 资格更正/终止/企业新revision；关闭事件消费前后变化 | [API](../tests/test_review_api.py)、[runtime](../tests/test_review_runtime.py)：历史可读但current=false；消费事务检查当前source，data_version封闭preflight竞争；过期close不能完成新状态 | PASS |
| B06-06 C | 非reviewer、未assignment、撤权、过期session、Agent工具、伪actor、旧fixture、CSRF重放 | [authorization](../tests/test_review_authorization.py)、[runtime](../tests/test_review_runtime.py)：每次当前身份/角色/来源ACL；精确HumanContext只能来自真实服务端session；功能关闭后正式case仍禁止B05fixture绕过；撤权UI彻底清空 | PASS |
| B06-07 C | 固定画像→完整候选→核对→补材料→全部逐项复核→关闭→导出 | [实际浏览器](../frontend/tests/reviews-real.spec.ts)、[API](../tests/test_review_api.py)：导出候选/排除原因、旧新结果、双边证据和全部事件；撤权不可导出，最大10MB/no-store；无外部采购动作 | PASS |

## 原生恢复与生命周期

四组均从实际SIGKILL返回-9后独立Python进程原生恢复，再第三进程读取。
12进程的application_version均为 `c3c8525908db8b74e8711595ff907096f4a76ac5e9447f8bd48d275db1b8b775`。
[汇总](evidence/b06-20261023/recovery-summary.json)及各组原始events/recover/read-saved可逐字节复验；
恢复结果与第三读取完整JSON相等，本地模型调用不增加。

| 杀进程窗口 | 实际观察 |
| --- | --- |
| 审核业务提交后、投递前 | 1任务、9事件，outbox恢复投递，9本地模型调用 |
| DBOS.send后、sent标记前 | 同event幂等投递，1任务、9事件、9本地模型调用 |
| 正式事件消费后、SDK检查点前 | 重放复用同结果/事件，9本地模型调用 |
| successor关联提交后 | 2任务/3资格结果/2审核revision/3事件/1successor，18本地调用；新8项全未审核 |

补材料组还执行真实停机v4备份：8文件、2任务、4原生workflow、2审核revision、3事件、1successor。
恢复时旋转epoch、清assignment、冻结outbox；旧批准不能恢复为当前有效。正文按整个case来源
并集留存；其中一来源到期清整个case正文，同时保留审计元数据和不依赖新材料的旧任务历史。

新增生命周期反例证明取消async waiter不能停止to_thread中的投递：退出前先shield并join正在
投递的线程，再关闭DBOS、数据库和单worker锁。实际SQLite/DBOS/线程测试覆盖正常返回、抛错及空闲。

## 共同门槛

| ID | 本PR实际证据 | 状态 |
| --- | --- | --- |
| G-01 | B06-01～07映射新增schema/仓储/服务/API/UI/恢复/留存，功能语义与任务状态分离 | PASS |
| G-02 | 干净实现1429后端/105浏览器、Ruff196/build/frozen scorer；浏览器收尾异常已披露，远端CI另列 | PASS |
| G-03 | 009增量重复迁移；v4双库映射/篡改负测；4真实进程恢复窗口；前置B02真实旧库仍BLOCKED | PASS |
| G-04 | reviewer+assignment+全来源、session/CSRF、不可变审计/版本CAS、最后释放复核；导出去敏/私有文件 | PASS |
| G-05 | 复用B05锁定DBOS3.3与PAI2.59的send/recv/checkpoint；没有新增通用工作流或认证框架 | PASS |
| G-06 | 实际模型/采购请求0、新增¥0；合成计费明确标记；恢复不释放未知或已发生成本；缺账本不启用live | PASS |
| G-07 | 完整SHA/tree/时间/39文件哈希索引；18冻结文件不变；12进程同appversion；失败反例不冒充真实评测 | PASS |
| G-08 | 默认关闭；关闭后仍防旧fixture绕过；保留库/历史/预算，恢复隔离并显式重授权；线程退出有序 | PASS |

## 修复过的实质问题与独立复核

开发中实际重现并修复：正式case在review功能关闭时B05fixture绕过；source变化发生在关闭消费
preflight之后仍完成任务；async relay取消后未等native线程结束即释放资源；successor提交时旧决定
outbox未被冻结；不可投递事件占满小批次使后续事件饥饿。最终负测保留，不降低断言。
前三项旧代码的红测分别为1失败、两个竞争窗口失败、2失败1通过；这些原始stdout只在任务工具
记录中，没有另存公开日志，不提供不存在的失败日志链接。对应最终回归已含全量1429项。
仓储/运行时和API/UI由未参与相应实现的Agent复核；全部合并后由主Agent跑完整验证。

## 运行、回退和后续

见 [HUMAN-REVIEW](../docs/HUMAN-REVIEW.md)：先安装主锁及hashed DBOS overlay，再使用
`uv run --frozen --no-sync`。功能默认关闭，实际开关名称和身份/assignment设置以该文档为准。
停服务完整备份，关闭审核功能为非破坏回退；不删除009表、不覆盖旧事件、不恢复已撤销权限。

下一项B07在独立工作树接入许可策略、原子增量页、隔离PDF解析及同DBOS调度。旧六公告原库、
最新账本、真实授权企业资料、独立人员、IdP/HTTPS和目标部署仍缺；B08/B09/B10真实验收保持
BLOCKED。单个工程PR完成不等于企业试用、生产发布或总体目标完成。
