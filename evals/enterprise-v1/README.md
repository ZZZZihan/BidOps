# B01 冻结的离线校准入口

这些 UNIT 样例是人工构造的工程输入与预制输出，不是真实公告、企业资料或 Agent 成绩。
程序只运行评分器与 Pydantic Evals，不调用模型；真实业务验收始终 BLOCKED。

固定清单 SHA256：`22a2adf96f07b9a3e9c1493dae2cebbe2499b6e05db21f2b52855686c2afffea`。
固定工程输入 SHA256：`eda43a451ae978251de4be003f1c062d1a4d3e93db9249c9c5458966628f44fb`。
调用方须在评测前从已审核 PR 保存这两个 pin，不能根据被修改文件临时重算替换。

```sh
uv sync --frozen --python 3.12
uv run --frozen --no-sync python scripts/screening_eval.py \
  --expected-freeze-sha256 22a2adf96f07b9a3e9c1493dae2cebbe2499b6e05db21f2b52855686c2afffea \
  --expected-input-sha256 eda43a451ae978251de4be003f1c062d1a4d3e93db9249c9c5458966628f44fb \
  --output /tmp/bidops-b01-calibration.json
```

输出文件必须不存在，防止覆盖历史失败。工程校准成功退出 0，评分／冻结失败退出 2。
`source_tree_dirty=true` 的记录不能宣称对应 HEAD 已验证。正式证据从已提交干净树执行。
结果记录版本、完整分母与错误 ID；数据／人审门槛 BLOCKED 不会被夹具分数覆盖。
实际验收记录见 [B01 报告](../../reports/b01-contracts-eval-20261023.md)。

真实数据与人员不在此目录准备。B08 使用受控位置另行冻结许可、候选池、金标、画像、
提示与人工计划，沿用 [v1 协议](../../docs/ENTERPRISE-EVALUATION.md)。
