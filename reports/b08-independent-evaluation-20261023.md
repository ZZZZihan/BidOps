# B08 独立评测工具实际验收

[Draft PR #23](https://github.com/ZZZZihan/BidOps/pull/23)，堆叠于 B07 #22，未合并、未部署。
本 PR 的 B08-03、B08-07 工程门槛 PASS；B08-01/02/04/05/06 真实评测门槛 BLOCKED。
远端旧CI后端1850PASS/1FAIL（945.76秒），B07 PDF测试跨平台分支修复后仍待新CI；
整条堆叠链代码合并仍受 B02-06 真实旧库阻塞；不能把工程夹具成绩当作新公告上的 Agent 成绩。

## 版本、输入与实际执行

- 被测代码 `b42f05269e13be278d220a1307d79ee4b17aad6b`，tree
  `e7efdeed2f7c0e18e8657013cf76154ce872b55b`；base B07 `5ac68753c22f46a3e3214e2f0c61a1ea8008808c`。
- root 完整执行 2026-10-22 20:43:09–20:52:56 UTC，验证前后干净；Python3.12.12、
  Pydantic AI/Evals2.59.0。**1851 backend PASS /578.40秒**；Ruff/format230文件、B01冻结评分器PASS。
  本PR新增121项：helper68、collector27、manifest/runner26。没有UI或迁移改动。
- [verification](evidence/b08-20261023/verification.json) 固定命令、SHA、时间、退出码；
  [backend](evidence/b08-20261023/backend.txt) 与 [证据索引](evidence/b08-20261023/evidence-index.json)
  可复查。CI当前结果以PR单列的run为准，本机通过不代替远端。
- 主锁 `92ceebd17f2e98b0075eac8f13c9108b2686635e1ece5aa4d5f172de10289b27`；DBOS锁
  `cc7fa6b4de2bd7dc10ca1745b56c5c1f0891edb649732843195a854e098ccd88`；pypdf锁
  `59fd6f618e0fa0414b0caad225096e12d5c88885ad4e0471299b667315a913ba`。18冻结文件不变。
- 原始输入是已有冻结工程夹具，不含真实独立金标/企业材料/人员；完整本机CLI额外在干净
  b42提交执行成功，[命令/退出码](evidence/b08-20261023/calibration/execution.json)。调用方manifest
  字节SHA `7adee39c1ffa5f39f523293fb45379c3059e57e512b22d4ecc7606d0143008e9`；输入SHA
  `86cb78ec745d8a04176747d3b1e2a134a14a9696da18fad09e82cd84bbfd2243`。输出PASS同时业务BLOCKED、
  Agent score=null。manifest中的原执行时间为合成夹具声明，不是模型/真人实测时间。
- 真实模型及采购网络调用0、新增实际费用¥0；没有当前完整账本，未授权任何新付费调用。
  FunctionModel、模拟HTTP和合成用量只用于工程检查，不能当实际provider返回。
- root实现运行manifest/闭包/账本核对与CLI；3子Agent分别参与评测helper、授权结果收集、
  独立技术复核及相邻PR恢复集成，均显式请求GPT-6.1 Sol/ultra，实际provider档位无法独立验证。
  主Agent本次max获明确许可。helper、collector和runner均由未参与相应实现者交叉复核。

## 逐项验收

各行共用上述完整SHA/执行时间/执行者及纯工程数据分类。权限和版本输入/预期见链接测试；
真实项缺失记录为BLOCKED，不虚构样本数量、人审或评分结果。

| AC-ID | 输入、操作、预期与实际证据 | 状态 |
| --- | --- | --- |
| B08-01 B | 校验分层随机seed/候选池/许可/族隔离及旧集泄漏的工具已实现；真实≥20新独立族/金标公告未提供，样本量0，不能用工程样例补足 | BLOCKED |
| B08-02 B | 标注者/复核/仲裁、逐条金标和证据/输入绑定负测通过；独立人员与真实标注记录未提供 | BLOCKED |
| B08-03 C | [helper](../tests/test_acceptance_evaluation.py)68、[collector](../tests/test_acceptance_collection.py)27、[runner](../tests/test_acceptance_runner.py)26均纳入全量1851；缺失/非法/失败/超时/全弃答/零分母/风险缺口/P1各字段/原始用量均保留；真实CLI校准PASS并拒业务成绩 | PASS |
| B08-04 B | 完整任务登记与原始保存结果重新投影、版本/提示/参数/用量核验已实现；真实盲测未执行，当前预算账本缺失，没有真实严重错误率/召回率等成绩 | BLOCKED |
| B08-05 B | 冻结paired-trial工具保留全部质量分母、失败即节时FAIL、两独立操作者与题族/金标隔离；真实2人/10对计时未提供，不能报节时20% | BLOCKED |
| B08-06 B | 旧盲测转回归、新盲测及首次未经调试直接通过路径可检查；旧族更换split仍不能抹除泄漏；无真实首测/后续使用记录 | BLOCKED |
| B08-07 C | [机器报告](evidence/b08-20261023/calibration/report.json)区分报告生成、各硬门槛和业务BLOCKED；无总分掩盖FAIL/BLOCKED，工程agent_score为null，私有原文不进入stdout | PASS |

## 复核发现与修复

独立复核实证发现并修复：把旧盲测族改成development逃过历史泄漏；未提交任务伪造complete；
把original记录的5次调用仅在摘要改成0；极小超过10元的Decimal输入可能被默认精度四舍五入。
最终回归覆盖这些路径。原始结果/输出/用量重新投影及绑定，不信任可单独改写的摘要。
声明的candidate SHA必须实际存在，其旧运行文件逐个核对Git blob；runtime闭包覆盖新增文件。

结果收集在同一事务最后重查当前主体与全来源权限；撤权时整批拒绝，不能泄漏部分私有记录。
P1金额与原单位分列；waiting_review仅代表计算结果可读，不能替代HumanContext导出的真实审核。
收集时的来源陈旧提示如实标为当前系统披露，不冒充模型原判断。

预算账本跨全部开发/评测历史，对每个去重execution核对calls/tokens与原始usage，保留unknown
reservation及开账余额。结果只表示账本as_of时间的一致性；latest_ledger_verified和
new_model_calls_authorized均false。声明、哈希和文件一致性不能认证账单完整性或真实人身份。

## G-01～G-08

| ID | 实际结果与依据 | 状态 |
| --- | --- | --- |
| G-01 | 9个新增代码/测试文件及使用文档映射上述7AC，B类缺失明确保留 | PASS |
| G-02 | 干净代码完整1851后端，Ruff/format230，冻结校准；无UI变化，继承B07UI，远端旧CI失败、新CI待验 | BLOCKED |
| G-03 | 没有迁移/业务写入；collector只读，CLI仅新建私有输出，无覆盖 | PASS |
| G-04 | 全来源/主体重核、投影与用量完整性、限大小/重复key/NaN/路径边界、私有0600输出，负测纳入全量 | PASS |
| G-05 | 复用冻结B01评分器、Pydantic/Evals及现有Store权限；无新增依赖或通用执行框架 | PASS |
| G-06 | 实际模型/采购请求0，新增¥0；历史账本检查不授权新调用，不重置¥10累计上限 | PASS |
| G-07 | 固定code/tree/inputs/manifest/运行闭包和18冻结文件；报告含明确工程标签及首次失败保留语义 | PASS |
| G-08 | 工具默认不运行模型/采集、不改在线配置；退出/回退不改变业务历史，输出O_EXCL不覆盖旧报告 | PASS |

## 外部阻塞与下一步

按 [独立评测操作说明](../docs/INDEPENDENT-EVALUATION.md) 准备真实许可候选池、20新项目族及
独立金标、至少2名真实操作者/10对平衡计时，以及经人工核对的最新完整¥10累计账本。
其路径仍未提供；用户此前回复“好”没有提供受控文件路径，不能当资料已到位。
B09容量与B10配置工具继续独立推进。企业E2与生产E3均未验收，没有合并或部署。

## 远端 CI 的实际失败

[run39851445347](https://github.com/ZZZZihan/BidOps/actions/runs/39851445347) frontend/container PASS，
backend 1850PASS/1FAIL。唯一失败为B07继承的64MiB压缩PDF资源测试：Linux先触发
`pdf_memory_limit`，macOS先触发`pdf_partial_pages/pdf_decode_limit`，两者均未释放正文。
前置B07追加独立decode-cap测试并区分两条拒绝路径，生产资源/解压限制未改变。
该修复需合入本分支并由新CI核验；本机1851结果不能抹去旧CI失败。
