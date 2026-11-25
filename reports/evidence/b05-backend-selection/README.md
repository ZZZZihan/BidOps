# B05-01 后端选型证据

本目录只包含合成工程夹具、已有官网快照和脱敏文本，不包含企业原文、凭据、运行 SQLite 或虚拟环境。

- `historical-run/`：迁移前最终 DBOS-only 实验的独立原始记录。旧 application_version 为 `bc42b06b6dbdd085c2f61509bbd0541d6fddb695e42e9e367eb616d7b0519b36`。
- `migrated-run/`：本分支最终脚本实际执行记录；worker 和 controller 的准确 SHA、运行时间和基线 HEAD 在 `report.json` 中。`source_tree_dirty=true` 如实披露未提交状态。
- `dependency-audit.json`：28 包完整依赖闭包、20 包主锁共享版本、8 包新增依赖及原 hash 集合的本地核验。没有重新求解依赖或发起下载。
- `official/`：先前实验抓取的官方文档；本次没有刷新，不能据此声称官网实时状态。原 404 的 commands 页面未作为依据。
- `migration-index.json`：历史原文件到脱敏交付文件的映射与两组 SHA。
- `migrated-run/redaction-index.json`：新运行输出的脱敏映射与两组 SHA。
- `evidence-index.json`：本次交付文件 SHA 索引。索引不是信任签名，也不把本地工程证据升级为业务验收。

原 `.log` 与 `.jsonl` 以 `.txt` 保存以适配仓库忽略规则，逐行事件仍是原始 JSON Lines 格式。路径脱敏明确替换本机 home 为 `<HOME>`、新运行目录为 `<PROBE_OUTPUT>`，其他调用数、时间、PID 和状态保持不变。历史 manifest 覆盖了原实验的更多文件；被排除的 venv、SQLite、早期准备运行和大体积求解日志没有被伪装为本目录已有证据。

恢复观测为两个 TestModel 步骤各一次、未完成等待两次、最终结果一次、首进程退出 -9；真实模型和采购来源请求为 0。它只证明本地 DBOS SQLite 的已持久步骤恢复，不证明 PostgreSQL、Temporal、真实业务资格效果或全部计费窗口。
