# 独立评测的登记、留存与离线核验

B08 读取已经保存的结果和人工证据，核对固定输入、原始输出投影、完整分母、版本及累计费用。
`scripts/enterprise_eval.py` 不执行模型、不采集公告，也不授权新调用。真实样本、独立金标、
计时人员和最新完整费用账本仍缺时，业务结论保持 BLOCKED。

## 输入与冻结顺序

先在干净候选提交上登记所有任务、分层候选池、随机 seed、许可、项目族及原公告/更正/包/
附件关系。旧六公告只作回归。B01 冻结协议与 [原始计划](ENTERPRISE-PR-PLAN.md) 的样本和
风险覆盖门槛继续生效，不能根据盲测结果改金标、减少分母或下调门槛。

模型执行前必须由授权人员核对最新完整累计费用，当前上限 ¥10；本工具对历史账本的核验
不表示账本仍是最新，也不会打开 `live_calls_enabled`。独立使用者、标注者和复核者的身份、
独立性及许可是真实人工核查责任，哈希或 JSON 声明不能证明这些事实。

核验器接受以下文件，全部应保存在受控私有目录：

| 文件 | 合同与内容 |
| --- | --- |
| manifest | `RunManifest`：评测/候选完整提交、完整运行代码闭包、主锁、提示/规则/参数、输入/collection/ledger/registration 哈希，以及冻结/原执行起止时间 |
| input | `EvaluationBundle`：项目族与抽样登记、金标/证据、未加工的 observed tasks、task 与项目绑定、计时登记/实际观察、首次盲测及后续使用历史 |
| collection | `collect_observed` 导出的原始保存结果投影：全批候选、包括未提交/失败/超时的每个预登记任务、原始模型结果/用量/版本、去重 execution 收据和原始模型输出哈希 |
| ledger | `CumulativeLedger`：所有 BidOps 开发与评测的 opening spent/reserved、逐运行估算/预留/未知费用、独立对账者及覆盖时间；不是本次运行单独清零的新预算 |

使用相同提交里的 Python schema 导出字段合同，例如：

```bash
uv run --frozen --no-sync python -c \
  'import json; from bidops.acceptance.run_manifest import RunManifest; print(json.dumps(RunManifest.model_json_schema(), indent=2))'
```

输入必须选择 `engineering_fixture` 或 `independent_acceptance`。前者可校准评分器，不能
填补真实样本数量、独立标注或人审耗时。后者缺 collection、版本、费用或独立证据时不能
通过。首测后据答案改实现，旧集转回归，新的独立结论需要新的盲测；修改 split 标签不能
抹去旧项目族已经暴露的历史。

## 读取保存结果的权限边界

`bidops.acceptance.collection.collect_observed(screening, context, registrations, jobs=...)`
供已授权的服务端使用。context 必须对应当前活跃主体，读取结束前在同一事务重新核对主体、
对象及所有来源权限。缺权或撤权时整个收集失败，不返回部分私有内容。不要把随手构造的
Principal 或从浏览器提交的 owner 当成真实授权。

收集器遍历固定批次的完整分页，保留未入选及未执行项目。`waiting_review` 中的计算结果
可以标记计算完成，但不会变成人工批准；人工审核原件须通过已有 HumanContext 约束的
审核包导出单独保留。`source_state/stale_disclosed` 表示收集时系统的陈旧提示，不能冒称
模型在当时已识别陈旧。P1 预算金额统一元、原预算单位另存，不能互相覆盖。

原始正文和模型返回继续留在授权库中；收集文件含私有数据，应采用 0700 目录/0600 文件。
公开报告只放经过核查的工程夹具、匿名汇总和哈希，不提交真实内部材料或个人计时轨迹。

## 执行离线核验

先安装冻结主锁和带哈希 overlay，步骤见 [DURABLE-RUNS](DURABLE-RUNS.md) 与
[AUTHORIZED-UPDATES](AUTHORIZED-UPDATES.md)。此后使用 `--frozen --no-sync`：

```bash
uv run --frozen --no-sync python scripts/enterprise_eval.py \
  --manifest private/eval-manifest.json \
  --expected-manifest-sha256 <事前登记的manifest字节SHA256> \
  --input private/eval-input.json \
  --collection private/eval-collection.json \
  --ledger private/cumulative-ledger.json \
  --output private/eval-report-new.json
```

manifest 的预期哈希由调用方提供；每份输入先校验字节哈希，再解析，拒绝重复 JSON key、
NaN 和超 50MB 文件。运行闭包包括 backend/scripts 的 Python 文件及三个锁/pyproject；
评测提交必须匹配当前 HEAD 且工作树干净，候选提交中每个运行文件必须仍与其 Git blob
完全相同，新增核验模块不能掩盖对旧实现的改写。B01 的18个冻结文件继续逐个核对。

报告以 0600、独占创建方式保存，已有文件、第一次失败结果及符号链接不会被覆盖。stdout
只打印简要状态。退出码 0 表示工程核验 PASS，2 表示 FAIL，3 表示 BLOCKED；必须同时
读取报告的各项门槛，不能用退出码 0 推断企业价值或真实效果通过。

工程校准可不提供 collection/ledger，但报告始终保留 `business_acceptance=BLOCKED`、
`agent_score=null`。真实输入的局部业务声明即使一致，工具也只能判已提交证据的一致性；
真实业务类门槛仍需独立核查。`report_generation` 成功与质量门槛失败可以同时出现。

## 计分、费用与历史复验

每个预登记任务都进入质量分母，包括缺失、非法格式、失败、超时与全部弃答。必需指标为
零分母、风险类型未覆盖，或任一人审对照失败/超时时，不能报质量/节时通过。P1 字段分别
计数，严重错误逐例保留，原始评测结果不能被人工后改的决定替换。

核验器从 original agent result/job usage 重新投影输出，并核对去重后的 execution 收据；
不能把摘要里的调用数改零、把未提交项改成 complete，或把任务分配到另一采购包来过关。
费用使用受限精度 Decimal、检查所有历史费用与未知预留，严格不超过 ¥10；报价估算不是
供应商账单。输出的剩余额度只对应 `as_of`，并明确 `latest_ledger_verified=false`、
`new_model_calls_authorized=false`，不重置已发生费用或释放未知调用。

历史复跑须在独立 worktree 使用报告记录的完整代码 SHA，并从证据提交另行复制原 manifest
和输入；不要把旧 manifest 的代码/时间/哈希改成新值后称作同一次盲测。工程校准 manifest
中的任务执行时间是夹具声明，只验证时间合同，不能当作真实模型或人工操作的时间。

关闭评测入口不改变在线服务、私有业务库或审核状态。回退时保留原始报告、账本及所有失败
记录，不删除独立证据。实际工程验收和仍缺的外部输入见
[B08 报告](../reports/b08-independent-evaluation-20261023.md)。
