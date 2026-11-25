# 原生队列文档与报告归因复核关闭记录

结论：原复核的七项建议在本次读取的暂存文档、生成器及补充证据中均已落实，未发现这些修正仍有发布阻塞。此结论仅关闭文档准确性与证据归因问题，不代表尚未完成的负载或后续 CI 已通过。原复核文件完整保留。

本次为只读复核；只新增本记录，未修改仓库、未运行报告生成器、测试、负载、UAT 命令或 CI，也未查询远端执行状态。以当前提交的 Git 对象重新核对差异和文件哈希，不依赖旧报告中的版本推断。

## 七项修正核对

| 原项 | 本次核对结果 | 关闭依据 |
| --- | --- | --- |
| 1. 矩阵的复合 SHA 容易混淆负载／升级与后续 CI | 已关闭 | `build_native_reports.py` 的 B09-07 采用负载 SHA，并单列当前整合 SHA；B09-10 摘要明确升级与完整负载属于695，后续2425仅承载采集修复及当前整合 CI。B09-10 另有各证据角色的 SHA。CSV 与 Markdown 都带该摘要，不再仅依赖 JSON 才能识别归因。 |
| 2. 后续 UAT 与 Actions tree 同一性不能沿用695记录 | 已关闭；最终 CI 仍待真实输入 | 两份 UAT 记录分别保存完整695／2425 SHA，均记录命令退出0／2／2、clean、67项上游及六项 B10 BLOCKED／NO_GO。生成器要求最终 CI 为 PASS，当前 UAT 的 SHA 等于 CI SHA，同时要求 `tree_content_equal` 且实际 checkout tree 等于代码 tree。这里核查的是已记录的 UAT 元数据及生成约束，未自行重跑。 |
| 3. 四阶段探针“账本不变／仅来源授权”及旧结果命名过强 | 已关闭 | 报告改为原9条离线 request／response／usage 收据与结果元数据不变，明确未验证真实账本；拒绝与再授权按实际发生顺序描述，未增加“源全授权但未任务授权”中间点的虚构测试。字段改为 `old_waiting_review_job_receipts_preserved`。首次 ENQUEUED、原快照被覆盖的限制、第二次实际 PENDING，以及领域状态值不是 HTTP 请求，全部保留。 |
| 4. 组装时版本绑定、完成输入与 canonical plan | 已关闭；最终运行结果不在本轮裁定 | 生成器在首次输出前要求最终 CI PASS、UAT SHA／scope 固定 SHA 一致、Actions tree 一致；要求负载 completion 归属695及独立指标复算一致。canonical plan 哈希直接取最终 SHA 的 `git show` 对象字节，不再使用695工作树字节。命令退出码、工程门槛和总体 capacity 分别取实际输入，并不强制把非0结果改为成功。当前缺失的最终输入会阻止组装，不能以本关闭记录代替。 |
| 5. 部署并发与每任务固定设置混为一谈 | 已关闭 | 暂存 DURABLE-RUNS 说明任务固定 `ScreeningSettings`，`BIDOPS_JOBS_COMPUTE_CONCURRENCY` 是部署队列／实际计算上限，非任务快照；同版本1／2调整须停 worker 再重启。后端／锁字节改变仍触发应用版本保护，不能借并发设置绕过。与695的实现相符。 |
| 6. 安装及版本依据漏 attachments overlay | 已关闭 | 安装命令同时使用 durable、attachments 两份带哈希 overlay，基础 sync 后须恢复二者；版本和 Docker 部分明确实际后端字节及三份锁，并列出 attachment lock 路径环境名。未改锁。 |
| 7. 沿用“B06 尚待接入／缺模型绝无 worker”的过时表述 | 已关闭 | `waiting_review` 与证明范围均链接已实现的正式人审工程流程，仍要求 reviewer、assignment 与全部来源权限，并保留真实人工验收 BLOCKED。缺模型表述改为不启动资格计算；只有未启用来源 worker 的历史读取模式才承诺空动作，组合模式按服务端动作及准入检查，未声称共享来源 worker 能开放资格计算。 |

## 重新核对的版本与作用范围

- 完整本机负载及四阶段升级的新代码：`69560429745417e34113d0877d75a62d7eb8f37b`；升级的旧代码：`215fca737e0e466a9781cd020b6b9d3b89e4b43f`。
- 后续采集竞态修复：`2425fc2c50a0cc3a6155f14cd22f1463743d53f4`。直接读取两端提交的 `git diff --name-only`，只包含 `backend/bidops/ingestion/runtime.py` 和 `tests/test_ingestion_schedule_replay.py`，与范围证据一致。
- 直接对比两端 Git 对象，范围证据列出的10个路径全部字节相同，且 SHA256 与证据一致：jobs runtime／config、capacity load／evaluator／probe、冻结 manifest、canonical plan、主锁及两个 overlay 锁。采集修复摘要的两份最终源码哈希亦与2425对象相符。
- 这些结果只证明固定文件范围相同；全后端 application_version 仍不同。没有把695的完整负载或四阶段升级改贴到2425，也没有据此声称资格／采集混合负载已验证。
- canonical plan 两端字节相同，SHA256 为 `360872e647db3f182c299f19c756618f55851d488ee4664c97765327ff458ccc`；生成器仍从最终提交对象独立计算，未来工作树变化不会造成 URL／hash 错配。

## 尚未完成的发布证据边界

本轮读取时，暂存证据目录尚无 `fixed-code-ci.json`、`full-load/completion.json` 和 `full-load/recomputed-metrics.json`。这与协调者告知的 CI／负载尚在运行一致；未独立访问远端核对完成态。生成器先读取并校验这些输入，之后才写矩阵和报告。因此当前不能执行组装、声明最终 CI／负载通过，或把模板中的将来结果句当成现有结果。

最终应使用实际完成记录填入：最终代码／Actions checkout tree、负载695的结束时间／退出码／门槛、全部失败分母和复算。旧836单计算 actual1／target2 FAIL、695初次 CI 的真实调度竞态 FAIL、真实目标机／供应商测量 BLOCKED，以及 B02-06 和 G01／G03／G08 的原真实旧库缺口都应继续保留。两份空环境／空证据 UAT 的 PASS 仅表示预期拒绝0／2／2得到确认，其业务裁决仍是 NO_GO。

没有发现需要本次再改生产代码或扩大冻结范围的文档问题。修正文本可作为后续组装基础，最终实际结果仍由完成后的原始证据及安全投影决定。

## 读取快照

下列 SHA256 固定本次复核对象；本记录不替代它们的原始执行证据。

| 相对来源 | SHA256 |
| --- | --- |
| `work/native-current-doc-review.md` | `45b4a7c01416602aa0aafa2738d80a4cf02f76afea1f63672fee54c69b3f2b08` |
| `work/native-doc-staging/docs/DURABLE-RUNS.md` | `522254f0af985adb0ab5940c07b38eca849ec9d94d4be51dc18811e7e425a283` |
| `work/build_native_reports.py` | `82ca3d011a7bfcd5cf9d3a46f9538d6f175825a3c884be9e43fe6b56221f49b2` |
| `work/native-handoff-staging/reports/evidence/b09-native-queue-20261023/load-code-uat-cli.json` | `70ebed0501d6cd1e911bb4252e8ed841460c22ae00c997d1b9d4e9fa25dc453b` |
| `work/native-handoff-staging/reports/evidence/b09-native-queue-20261023/clean-code-uat-cli.json` | `b5872c3f75e83307e12a3969a1390c93d6462365ad78a57f3e8a90927a3a189b` |
| `work/native-handoff-staging/reports/evidence/b09-native-queue-20261023/load-to-fixed-code-scope.json` | `902fddb5ef9d2a29dcdc7be2ff3556370ecd1a86c32f4aa1982dbd23923d2f3c` |
| `work/native-handoff-staging/reports/evidence/b09-native-queue-20261023/ingestion-schedule-replay/summary.json` | `ef0c5317f65edab38680e5f5f396fffc322412dbba6733cdb0308f44be60e931` |

复核记录生成时间（UTC）：`2026-10-22T23:22:23.251303+00:00`。请求模型为 GPT-6.1 Sol／ultra；未独立核验实际供应商运行配置。
