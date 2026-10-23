# 原生采集计划恢复竞态：独立定向回归

最终 8 项通过（18.50 秒），两个原生 schedule 为 SUCCESS，六个预期拒绝为 ERROR。六个拒绝均无 HTTP 请求、无公告正文或版本入库、游标未推进；两项正例各仅一次 MockTransport 请求。全部为真实 DBOS 3.3.0 + SQLite 的本机合成输入工程测试，真实网络和模型调用均为 0。

旧代码的确定性红测为 1 FAIL / 8.10 秒：来源 root 已完成并为 SUCCESS，schedule 使用旧 queued 快照后因 ingestion_run_not_active 变为 ERROR。原红日志、中间两轮测试断言修正失败和最终绿日志均保留，摘要不覆盖历史。

完成竞态验证恢复扫描提交一页后 schedule 成功、1 run / 1 tick / 1 page、cursor_version=1；再次 admit、实际 recovery sweep、读取及原生结果重读不增加请求、不改变业务行。运行中竞态验证 schedule 使用最新 running 记录而不再 enqueue，唯一 enqueue 来自真实 recovery sweep。

六个负例是主体降为 viewer、来源许可撤销、计划 generation 暂停、取消请求、已取消、应用版本不匹配。它们都保留对应 CompanyError，不能用此次 no-op 修复吞掉拒绝。

执行发生于最小修复尚未提交的工作树；summary.json 列出实际测试文件和 runtime SHA256。后续提交包含相同文件字节，但本记录不声称该 clean 提交在此独立重跑。原 695604 CI 故障、原三进程恢复测试、完整容量与最终 CI 各有自己的分母。

公开白名单仅本目录 index.json 列出的文件。原始 XML 与失败 trace 含本机路径、hostname 和 fixture 上下文，仅列原件 SHA256，不公开正文。此证据不认证真实供应商许可、目标环境、真实人审或业务恢复时间。
