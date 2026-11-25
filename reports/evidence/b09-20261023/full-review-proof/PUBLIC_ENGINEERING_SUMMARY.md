# B09-08 完整 50,000 单元工程证明

固定代码 `836ccbfad779c4b6e52dd1802dd0877989dbe774`。执行进程 exit 0。使用停止的旧合成输入的独立完整副本，实际 20 账号、10,000 公告版本、50,000 采购包，通过生产 ScreeningService / QualificationRepository / JobsRepository / ReviewRepository / ReviewService。没有 HTTP 或 DBOS runtime；已有合成登录的固定夹具时钟不是当前 SSO 或人的验收。

- 完整批次 50,000 单元：49,995 excluded、5 needs_verification、0 candidate。不是 50,000 个合格项目。
- 实际 stored envelope 37,692,100 bytes，完整批次 JSON 37,690,586 bytes，专用 50,000,000-byte 上限内。
- 真实审阅服务最后一页 offset=49,900 / limit=100，返回100条、total=50,000、has_more=false，顺序ID与固定完整批次尾部精确相等。offset=50,000 返回空。
- 实际完整导出 37,703,148 bytes，50,000 items / 10,000 public versions 全部保留，导出 batch 与固定存储 batch 相等；没有把分页投影代替完整导出。
- 实际导出 SHA256 `5fbb2844f77d43b9ea4801425d74c5199465191984a74fd34b7d3ee873123789`。
- 独立内存 hook 在同一完整导出形状上增加未持久化 padding，只验证真实服务序列化保护：60,000,000 bytes 接受且不截断；60,000,001 bytes 返回 413 / review_export_too_large。该边界检查不是实际历史导出大小。
- 原目录11,403文件的路径/大小/内容SHA组成的摘要前后相等，原数据库SHA前后相等；只向独立副本写入。没有读取或修改当前正式负载目录。
- 真实模型调用0、来源请求0、人工批准0。1个显式编写的 information_insufficient assessment 保持 unreviewed，review event数为0。

本机一次诊断耗时：完整构建/保存/公开引用校验8.739秒、最后页0.218秒、完整导出0.446秒。这不是正式HTTP负载P95或目标硬件容量成绩。capacity_acceptance=NOT_EVALUATED、business_acceptance=NOT_VERIFIED；目标2/实际1计算槽缺口不因此改变。

原始结果 SHA256 `aa0939e3106e5ba33a95e0b7fcdc841593a98645d49cbed206299474a88715c7`；原始脚本 SHA256 `f80f32dce7ee879b658b482733df43c128bec19f2680161e0bc51f6d3c5279bf`。原始文件保持私有；safe-result.json仅移除source绝对路径并附范围说明，source-provenance.json固定全部原始摘要。未公开数据库、会话、正文或业务标识。
