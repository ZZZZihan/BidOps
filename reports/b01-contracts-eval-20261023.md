# B01 实际验收记录 — 2026-10-23（Asia/Shanghai）

实现 PR：[B01 #16](https://github.com/ZZZZihan/BidOps/pull/16)，基于尚未合并的
[计划 PR #15](https://github.com/ZZZZihan/BidOps/pull/15)。本报告记录已完成的工程验证，
没有把固定夹具、结构化人员记录或技术复核记为真实业务验收。

**本地代码验收：PASS。真实业务验收：BLOCKED。** B01 的 7 条 C 项和 8 条共同门槛
具有下表证据；平台 CI／required reviews 以 PR 当前 HEAD 为准，未据此执行合并或部署。
P1、B08 和 B10 的真实数据、独立人员及环境门槛仍未通过。

## 固定版本与执行环境

以下共同元数据适用于验收矩阵的每一行：

- 实现及实测完整 SHA：`4d22667acc4001b57d3dfca248a7cb78d5636587`。
- Base：`41f87d73505f446af56b4d5f7af63254f5ef884d`，分支 `codex/b01-screening-contracts-eval`。
- 冻结清单 SHA256：`22a2adf96f07b9a3e9c1493dae2cebbe2499b6e05db21f2b52855686c2afffea`，18 个源码／依赖／夹具／测试文件。
- 工程数据 SHA256：`eda43a451ae978251de4be003f1c062d1a4d3e93db9249c9c5458966628f44fb`。
- 画像版本集合 SHA256：`ff951fa725c2ec5b1b981c1cc46c85493a4354903b4d0a69d92d54d6c0312202`，全部为 UNIT 合成画像。
- 评分器：`enterprise-scorer-v1`；提示：`not_invoked:scorer_fixture`；实际模型：null，参数 `{}`。
- Python 3.12.12；锁定 Pydantic 2.14.1、Pydantic AI / Evals 2.59.0；未新增依赖。
- 执行时间：2026-10-22 UTC，换算北京时间 2026-10-23；逐命令起止时间见
  [checks.json](evidence/b01-20261023/checks.json)，离线入口结束时间为 `2026-10-22T16:41:31.575207+00:00`。
- 执行者：Codex 主 Agent；本次主 Agent max 已得到用户明确例外授权。
  两个子 Agent 均请求 `gpt-6.1-sol / ultra`，分别负责 schema／协议与冻结，实现后交叉复核。
  请求参数不等于对服务端实际全部运行配置的独立核验；没有人类验收签署。
- 前置权限：本机读取已提交代码和明确标注的合成资料；没有企业账号、私有文件、IdP 或 API key。

## 实际命令与结果

在上述实现提交的干净树上执行：

```sh
BIDOPS_NETWORK_ENABLED=false .venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python scripts/screening_eval.py \
  --expected-freeze-sha256 22a2adf96f07b9a3e9c1493dae2cebbe2499b6e05db21f2b52855686c2afffea \
  --expected-input-sha256 eda43a451ae978251de4be003f1c062d1a4d3e93db9249c9c5458966628f44fb \
  --output <新的受控报告路径>
```

完整后端 **571 passed in 5.83s**，退出码 0；包括原有 189 项和新增 382 项，均为本次实际运行。
lint 全通过；format 为 89 files already formatted；两者退出码 0。入口退出码 0，
`source_tree_dirty=false`，工程评分 PASS，真实公告计数 0、人工任务计数 0、业务验收 BLOCKED。

新测试按文件为 schema 97、数据／人工协议 79、评分器 69、冻结 107、入口集成 30。
精确重跑命令为 `.venv/bin/pytest -q tests/test_screening_schemas.py tests/test_eval_protocol.py tests/test_screening_evaluation.py tests/test_eval_freeze.py tests/test_offline_eval.py`。
这些命令的分项验证及最终全量验证都没有真实模型请求。源码级检查不代替独立真实效果测试。

机器证据：[pytest](evidence/b01-20261023/pytest.txt)、[lint](evidence/b01-20261023/ruff-check.txt)、
[format](evidence/b01-20261023/ruff-format.txt)、[固定夹具运行](evidence/b01-20261023/scorer-fixture.json)、
[证据哈希与环境](evidence/b01-20261023/manifest.json)。

## B01 逐项验收

| AC-ID | 输入与实际执行 | 预期结果 | 实际结果／失败处理 | 状态 |
| --- | --- | --- | --- | --- |
| B01-01 C | `test_screening_schemas.py`：五态、双方证据、逻辑、包、有效期及人审负例 | 合法可序列化；非法状态、缺证明、跨版本、伪人审字段拒绝 | 97 项通过；反向复核发现的 as_of、缺目标包和可变实例绕过已修复并复验 | PASS |
| B01-02 C | `test_eval_protocol.py`：旧六公告、同族版本／包／附件／内容跨集 | 泄漏拒绝，独立切分接受 | 项目族关联闭包、重命名族冒充独立样本和旧公告关联传播均覆盖；79 项协议测试全通过 | PASS |
| B01-03 C | 同协议测试＋固定夹具 data gate | 保留 P1 数量、独立随机金标和六风险层；不足 BLOCKED | 工程输入 real_notices=0、gold_families=0，各风险为0，实际 BLOCKED；合法记录协议正例通过 | PASS |
| B01-04 C | `test_screening_evaluation.py`：漏条件、硬误放、不可解析／无关／失效证据、重复、失败、全弃答 | 错误进入计数和失败 ID；全部登记任务保留；零分母不能通过 | 69 项评分器测试通过；5 条正例质量5/5仅为校准；错误注入命中对应 FAIL/BLOCKED，后续重复尝试的严重主张也保留 | PASS |
| B01-05 C | 逐字段与20条阈值边界、未知值、元／万元、长Decimal及等价时区 | 100%溯源、95%精确率、85%覆盖保留；未知不作0 | 95%／85%边界通过，90%／80%失败；NaN/Infinity/负金额拒绝；同一时刻不同偏移匹配 | PASS |
| B01-06 C | 协议测试：缺人员／原文／仲裁、重复操作者、未标注输入、失败计时 | 不合格金标拒绝，输入与质量单位绑定，失败保留分母 | 跨配对重复、虚构质量单位、critical=false绕过均修复；失败／超时节时FAIL，不删除任务；真实人员与计时仍缺失 | PASS |
| B01-07 C | `test_eval_freeze.py`＋`test_offline_eval.py`＋上述冻结入口 | 篡改与缺版本拒绝；固定结果不计Agent成绩 | 107＋30 项通过，18文件冻结实跑通过，完整版本及0调用记录，agent_score=null；错误根目录、漏必需文件、错误pin、实际依赖漂移均拒绝 | PASS |

## G-01～G-08

| ID | 实际检查及证据 | 结果 | 状态 |
| --- | --- | --- | --- |
| G-01 | 上表与实现 diff、五个新增测试文件、协议和冻结入口逐项对照 | B01-01～07均有实现／实际正负验证；没有将B08真实结果勾选完成 | PASS |
| G-02 | 上述完整提交的 pytest／ruff／format 日志 | 571通过，无跳过或删断言；前端未修改；GitHub CI另按当前HEAD核验 | PASS |
| G-03 | `git diff 41f87d73505f446af56b4d5f7af63254f5ef884d HEAD -- backend/bidops/migrations backend/bidops/models.py backend/bidops/agent_demo backend/bidops/enterprise` | 实现提交相应diff为空；无数据库／旧持久化形状变化；旧189测试实跑通过 | PASS |
| G-04 | 新入口仅本机离线脚本，无HTTP或私有读取API；schema／freeze／入口负向测试和diff复核 | 拒伪人审输入、参数秘密字段与非法正文回显；公开材料均为UNIT或已公开旧六公告身份 | PASS |
| G-05 | `uv.lock` / `pyproject.toml` 与base一致；实际导入版本校验；协议中官方框架链接 | 无新增依赖；沿用Pydantic/Evals，未编写执行引擎或认证框架 | PASS |
| G-06 | 网络被禁止的测试＋无provider的冻结脚本；机器报告versions | 模型／来源调用0，tokens0，新增费用¥0；没有触发实时预算消费或来源采集 | PASS |
| G-07 | 实测完整SHA、18文件pin、数据／画像SHA与报告核对 | 干净树运行，source_tree_dirty=false；报告/任务文档后续提交不冒充新的模型评测 | PASS |
| G-08 | 完整原有189项测试及旧模块diff；新模块未接入应用startup/API | 旧服务行为继续可用，关闭新离线命令即回退；无需删库、迁移回滚或清历史证据 | PASS |

## 独立技术复核与实际修复

两个子 Agent 进行了不同模块的实现与交叉复核。实质发现包括：重复操作者破坏盲态、
任务不绑定金标、关闭关键复核、有效期基准不绑定、目标采购包缺失、可变模型跳过复验、
可解析却无关的冲突证据、缺失项冒配、重复尝试隐藏严重声明、执行根不一致、非法正文回显、
框架映射对坏 task_id 崩溃等。均通过原复现或新增负测验证修复，没有放宽门槛。

复核者另外实际运行 618 次原始输出字段变异和 96 次版本元数据变异，未捕获异常为0；
这些额外只读探针为 Agent 技术复核，不计入571个正式测试，也不是人工业务验收。

## 外部阻塞与下一步

| 验收范围 | 状态 | 最小缺失条件 |
| --- | --- | --- |
| P1、B08-01/02/04/06 | BLOCKED | 持续使用依据、满足分层的全新真实候选池、独立标注／复核／仲裁人员、冻结后首次盲测 |
| B08-05 | BLOCKED | 至少2名合格独立操作者、10项目族真实计时、盲态与完整原始记录 |
| B03真实资料、B10-02～06 | BLOCKED | 经授权企业材料、真实IdP／HTTPS环境、独立账号／试用用户及目标运维环境 |

上述阻塞不影响继续 B02/B03 编码。它们只限制对应真实验收；不能用合成资料补足。
最近可读取的已提交成本报告记载完成费用 ¥2.170878、含未知失败预留保守累计 ¥2.420878。
本次新增为0；没有读取到新的实时账本，故不把历史差额声明为当前可用额度。

下一步从B01冻结提交推进B02公告版本／分页和B03私有画像／材料权限；前置PR未合并时使用
明确依赖的堆叠PR。已核实的重点包括500条预载限制、reparse覆盖旧解析、匿名模式下新私有
入口风险、旧备份恢复已撤销授权等。未实现的B02～B10不因这次交付改为PASS。
