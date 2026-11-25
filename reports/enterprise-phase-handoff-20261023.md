# BidOps B01–B10 阶段交接：等待外部验收条件

本轮已把企业画像、采购证据、候选筛选、逐条资格核对、持久任务、人工审核与补料、经许可更新、独立评测工具、运维测量和企业验收检查拆成十个实施 PR。所有代码按功能分支交付；没有合并 main、部署生产、发送实际告警或执行新的付费模型调用。

**当前结论仍是等待外部验收条件，企业试用与生产发布 NO_GO。** 工程测试、模拟身份和合成数据不替代真实旧库迁移、独立金标、真人计时、真实 IdP/HTTPS 或企业验收。尤其应同时保留两种不同问题：B02-06 缺原旧库而阻塞代码合并；B09 已观测到实际计算并发 1 低于冻结目标 2，容量工程结果 FAIL，不能只解释成等待环境。

验收范围为 **71 项专项：56 项 C 类、15 项 B 类**。已具备工程证据的 C 类为 **55 项 PASS**；B02-06 是唯一仍 **BLOCKED** 的 C 类。15 项 B 类全部 **BLOCKED**，其中 B09-02 另有上述已观测容量 **FAIL**。原计划 69 项加实施新增 B04-08、B09-08，不能漏记后将分母写成 69 或 70。逐项命令、输入、版本、观察值及后续条件见 [71 项交接矩阵](enterprise-acceptance-matrix-20261023.md) 与各 PR 报告；这些数字不是业务完成率。

另计 G-01～G-08 八项共同门槛。整条堆叠链的 G-01、G-03、G-08 仍受 B02-06 原旧库迁移及旧服务回退缺口阻塞；其余门槛按最终提交、检查和公开证据逐项登记。G-06 仅表示本轮没有真实模型或采购来源调用，不表示预算账本已经完整或未来调用已获授权。

## PR、依赖与当前验证

计划 [#15](https://github.com/ZZZZihan/BidOps/pull/15) 仍 Open/Draft。实施按 `#16 → #17 → #18 → #19 → #20 → #21 → #22 → #23 → #24 → #25` 堆叠，当前 #16 为非 Draft，#17–25 为 Draft，全部未合并。每个 PR 的 diff 只应按其标明的 base 审阅；前置提交不重复计为下一个 PR 的新增交付。固定代码的实际检查与日志指纹见 [CI 快照](evidence/enterprise-phase-20261023/verified-code-ci.json)。最终文档 Head 与其检查单独记录，不改贴实测 SHA。

| 计划 / PR | 交付范围 | 固定代码 SHA | 实际后端 / 浏览器 | 远端检查 |
| --- | --- | --- | --- | --- |
| B01 / [#16](https://github.com/ZZZZihan/BidOps/pull/16) | 业务契约与评测协议 | `069e4b495828caca4c64447062c030a0f313aada` | 571 / 12 | [3 jobs PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39816800842) |
| B02 / [#17](https://github.com/ZZZZihan/BidOps/pull/17) | 公告/包/附件版本与完整分页 | `c3435402247d0cd66986a0bbd8854304c017f864` | 685 / 14 | [3 jobs PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39823377065) |
| B03 / [#18](https://github.com/ZZZZihan/BidOps/pull/18) | 企业画像/材料与身份撤销 | `0d8ec7afd54485110ed83728145ae86696a5dc47` | 795 / 20 | [3 jobs PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39859735827) |
| B04 / [#19](https://github.com/ZZZZihan/BidOps/pull/19) | 候选与逐条件资格核对 | `cfdc2bc72c255c3dc7637868c39c56574cd66cdf` | 1105 / 39 | [3 jobs PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39859741649) |
| B05 / [#20](https://github.com/ZZZZihan/BidOps/pull/20) | DBOS持久任务与恢复 | `3764dbe0a8c4ff3d20f38677167a87ed29f60c71` | 1320 / 75 | [3 jobs PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39859749226) |
| B06 / [#21](https://github.com/ZZZZihan/BidOps/pull/21) | 人工审核与补材料 | `4d2c6e89301026f95b2f0923262bfb73dbfb2bc4` | 1434 / 105 | [3 jobs PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39859754197) |
| B07 / [#22](https://github.com/ZZZZihan/BidOps/pull/22) | 经许可更新及附件导入 | `c840046632663c350ae2d06cfbb0be43e21f1e0f` | 1735 / 148 | [3 jobs PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39859757990) |
| B08 / [#23](https://github.com/ZZZZihan/BidOps/pull/23) | 独立评测和人工计时工具 | `d0a50b61d94953114d9edcc07db9bcbfc8948b0d` | 1856 / 148 | [3 jobs PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39859761355) |
| B09 / [#24](https://github.com/ZZZZihan/BidOps/pull/24) | 容量/告警/完整恢复 | `db4f55dbf410acb4ef36c87738b0f95eddada5ed` | 2010 / 152 | [3 jobs PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39859780307) |
| B10 / [#25](https://github.com/ZZZZihan/BidOps/pull/25) | 真实企业验收检查与闭环清单 | `88dee00a36adf64ec20bb2918ead48f5a388c520` | 2133 / 152 | [3 jobs PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39860218830) |

此表固定实际被测代码。随后只追加验收文档与安全证据的提交会有新的 PR Head，其检查状态在最终远端快照单列；不把测试结果改贴到其他 SHA。上述十个套件高度重叠，不相加。

## 最终整合与实际失败

最终整合代码 `88dee00a36adf64ec20bb2918ead48f5a388c520`：本机 **2133 passed／602.81 秒**；Ruff、**256 文件格式检查**、冻结评分器四个命令均 exit0，执行后工作树干净。实际 UTC 检查范围为 `2026-10-22T21:48:39.756853+00:00` 至 `21:58:46.375013+00:00`。同 SHA 的 [远端 CI39860218830](https://github.com/ZZZZihan/BidOps/actions/runs/39860218830) 实际 **2133 passed／1042.83 秒、152 浏览器通过／约 2.1 分钟，三个 job 全部 success**；两次时长与环境分别记录。

主 Agent 另核对 B01 的 18 个冻结文件字节、两份依赖 overlay 和 B09 836→db4 的生产源码一致性。B09 容量继续记在836，不声称在后续测试修复提交重跑。2133 项工程检查不产出真实企业资格准确率或人审节时成绩。

最终回归保留了此前真实失败，并分别记录修复后的验证：

- 大规模候选准备先超时；诊断发现完整 5 万包候选约 37.7 MB，超过普通 10 MB 保存通道。B09-08 新增包件版本索引、严格完整候选的专用 50 MB 通道、逐次授权分页与完整 60 MB 导出上限。普通结果和模型原始输出的 10 MB 边界保持。新 AC 保留，容量档位不缩小。
- 012 索引已实际存在，原迁移测试预期清单漏列，导致 B09/B10 完整回归失败。修复只追加该精确迁移名称，保留完整清单相等与重复迁移断言。
- Linux CI 暴露身份登出测试在收集时生成未来时间、执行时已经落入合法区间的问题。修正测试时钟并新增 −301/−300/+30/+31 秒边界；生产 300 秒最大年龄和 30 秒未来宽限未变。原红测、独立延迟复验及签名验证器检查保留。
- 较早 B06 的任务超时分类、浏览器全流程预算和 B07 的 PDF 跨平台保护分支失败分别保留在对应 PR 报告。后续通过不覆盖原结果。

B09 本机 152 项浏览器断言结束后，Chrome updater 的两个崩溃处理进程持有输出管道，曾作定向终止后收尾。报告明确记为有收尾干预；同版本远端浏览器自然完成结果单列，不能隐去本机干预。

## 容量、恢复与证据边界

完整负载固定代码 `836ccbfad779c4b6e52dd1802dd0877989dbe774`：10,000 公告版本、50,000 包、200 材料、1,000 附件、20 账号、10 客户端；5 分钟预热加 30 分钟稳态，以 2 次操作/秒发出 4,200 次操作，稳态分母 3,600，零重试。全部操作均保留，4 次 read/search ReadError 没有收到 HTTP 响应，稳态错误率 **0.1111%**；返回的 HTTP 响应中失败为 0，不能因此说整个运行零错误。

非模型接口 P95 **263 ms**，任务受理 P95 **108 ms**，排队 P95 **480.734 s**、队列峰值 **17**，计算 P95 **31.774 s**，其中模拟模型等待 P95 **30.008 s**。84 个负载任务最终均到 waiting_review；计算/队列在收尾时均为 0。实际计算并发峰值 **1**，冻结目标 **2**，重叠时长为 0，因此容量总体 **FAIL**。不能把等待人审的业务任务数当作计算并发，也不为获得 PASS 改低目标。

worker RSS 峰值 **856,440,832 bytes（约 817 MiB）**、CPU 峰值 **105.7%（进程口径）**；记录窗口约 **43 分 38 秒**，含排空时间。实际机器是 M2 Ultra／24 逻辑 CPU／64 GiB，期间同机有其他测试与构建；冻结目标机为 4 CPU／8 GiB。因此 API 指标只支持这次本机合成工程结论，目标机测量仍 BLOCKED。FunctionModel 固定两段延迟不是实际 DeepSeek 服务。当前证据支持保留 SQLite／单 worker 并继续目标环境测量，是否迁移架构待目标实测决定。

完整运行曲线可查 [PNG](evidence/b09-20261023/formal-load/capacity-curves.png)、[SVG](evidence/b09-20261023/formal-load/capacity-curves.svg) 和 [可复查曲线数据](evidence/b09-20261023/formal-load/capacity-curves-data.json)。官方稳态指标见 [B09 已提交报告](https://github.com/ZZZZihan/BidOps/blob/efd4becb8e0f39a22d3933ddd542f6acc6e695b1/reports/b09-capacity-recovery-20261023.md)。

全规模复核补证使用已停止旧输入的独立副本，经生产服务读取完整 50,000 候选、校验末页 100 条及 offset=50,000 空页，完整导出 37,703,148 bytes；源 11,403 文件闭包与数据库运行前后哈希相同。此证明不等同真实模型判断准确，也不等同目标机 HTTP 延迟达标。实际批次包含 49,995 excluded 和 5 needs_verification，不能称 5 万个合格项目。

完整本机恢复演练保留双库、文件、审核和费用记录；备份后停用、撤权和删除再丢失存储，恢复后旧会话/链接请求被拒绝，未知费用未释放。观测 RPO 0.031178 秒；最新授权无法确认，私有业务没有重开，所以业务 RTO 为 null/BLOCKED。拷贝耗时和安全检查耗时不能替代业务恢复时间。

## 需要提供的外部条件及推进顺序

先提供第一组即可继续相应离线工作，不要求四组同时齐备。秘密由责任者放在受控配置中，不要在聊天粘贴。

1. **原旧六公告实例与完整费用账本的受控路径。** 旧实例需完整 DB、raw/附件、原业务历史会话和版本依据，用于 B02-06 真实迁移、重复迁移和旧服务回退；账本需覆盖全部历史开发/评测调用、已用和 unknown/预留，并附供应商用量依据，用于核对累计 ¥10 的实际余额。两者可以独立核查；账本到位不自动授权模型调用。此前回复“好”没有提供这些路径。
2. **来源许可、独立新数据与人员。** 明确手工导入/HTTP/持续采集各自范围；准备按项目族隔离的新样本池、分层独立金标、复核与仲裁，以及两名独立操作者的十对真实计时。旧六份公告只用于开发回归，失败和超时全部计入。
3. **真实企业环境与授权材料。** 可用 HTTPS、企业 IdP/角色映射、两名实际使用者及获准企业画像和内部材料，用于 B10 的登录/撤权/资格核对/补料/人工决定/导出闭环。协议夹具和 Agent 复核不代替真人验收。
4. **运维窗口、最新权限依据与实际告警收件。** 明确目标机器、可恢复备份、损失演练范围、收件端及本次发送授权；收到实际告警与恢复事件后留证。最新权限无法确认时继续关闭私有访问。连续三轮/24 小时更新记录和具名发布复核是后续执行产物，不要求用户一开始提供“已完成证明”。

## 复查与运行

下列命令仅适用于 **B10 最终整合候选**的独立干净工作区，使用 Python 3.12 与该版本锁文件。逐个复查较早 PR 时按各自文档和 CI 安装，早期分支不一定包含两份 overlay：

```bash
uv sync --frozen --python 3.12
uv pip install --python .venv/bin/python --require-hashes \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  -r requirements/durable.lock -r requirements/attachments.lock
BIDOPS_NETWORK_ENABLED=false .venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
npm ci --prefix frontend
npm run build --prefix frontend
```

重新 `uv sync` 后必须重新安装两个 hash 锁定 overlay。浏览器检查按项目 CI 的浏览器安装与环境配置执行；不能把仅构建通过当交互测试通过。企业检查入口为 `scripts/enterprise_readiness.py --template`、默认环境检查及 `--bundle`，本轮新候选实际退出 0/2/2：模板生成成功，缺真实配置与未执行证据被明确拒绝。该工具最多判定证据完整并可供独立复核，不授予人工验收或发布权限。

真实配置与部署条件到位后，部署者按受控环境说明填写权限为600的 `private/enterprise.env`，先核配置，再启动单 worker 服务；下列命令本轮未用于部署真实环境：

```bash
.venv/bin/bidops-enterprise --env-file private/enterprise.env check-config
.venv/bin/python scripts/enterprise_readiness.py --env-file private/enterprise.env
.venv/bin/bidops-enterprise --env-file private/enterprise.env serve \
  --host 127.0.0.1 --port 8765
```

`--env-file` 在 enterprise CLI 子命令前；真实配置检查不过时先补齐条件。数据命令使用同一数据目录和数据库配置，HTTPS 由受控反向代理提供；完整迁移、合法来源导入及双库恢复步骤按版本内 `docs/ENTERPRISE-OPERATIONS.md` 执行，不能对现有真实库直接套用空库启动步骤。

容量命令、唯一输出目录、固定 manifest hash、长窗口与零重试规则见 PR #24 的 `docs/CAPACITY-RECOVERY.md`；真实企业输入和私有证据说明见 PR #25 的 `docs/ENTERPRISE-UAT.md`。原数据库、材料、cookie、身份令牌、完整输出和恢复私有快照不包含在公共交接包中，公共索引仅列安全投影及原件哈希。

回退按各 PR 文档停写备份，在独立目录恢复；保留业务/DBOS 双库、审核历史和费用预留。关闭相应功能/网络，不删除新历史或 DROP 索引以强行适配旧镜像。恢复后重新核验最新身份、来源、任务授权和累计费用，不能自动恢复已撤销权限。

## 协作与验收责任

主 Agent 承担关键实现、集成、最终检查、Git/PR 和交付。三名子 Agent 分别承担契约/权限/恢复复核、评测与验收工具复核、身份适配与 UI/时间边界检查，职责在各 PR 报告中具体记录。子 Agent 均显式请求 GPT-6.1 Sol / ultra；主 Agent 本次 Astra / max 已获用户许可，实际 provider 运行档位不能独立核验。技术复核、工程测试、真实企业人员签字和发布批准分别保留。

本轮真实模型和采购来源调用为 0，新增实际模型费用 ¥0；历史累计余额未核实。没有执行 main 合并、生产部署、实际告警外发、采购提交或正式比赛材料提交。
