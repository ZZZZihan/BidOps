# 原生计算队列生命周期测试独立复核

结论：PASS，无阻塞发现。此次是对提交前的测试修复进行独立技术复核与正常速度定向执行，不是人工业务验收，也不代表失败的完整 CI 已转绿。

## 版本与范围

- 基线：`2425fc2c50a0cc3a6155f14cd22f1463743d53f4`；唯一 tracked 增量为 `tests/test_jobs_compute_queue.py`。
- 测试文件 SHA256：`95cbaab11ce3222e1ed0569d9d2f381da18d81a0352764797569ffd6855b0e5e`。
- patch SHA256：`7727d478751cda6e9972780cb5c8edfeb08c30733cdd52e2532039349e0bfc4e`。
- backend 对基线 diff exit 0；测试前后抽查的十项源码/锁 SHA 均一致，生产文件及依赖锁 byte 与基线相同。
- 请求模型配置为 GPT-6.1 Sol / ultra；本复核者不能独立核验实际运行档位。

## 独立实际执行

```text
.venv/bin/python -m pytest -q tests/test_jobs_compute_queue.py -o cache_dir=$RECOVERY_OUTPUT/pytest-cache --basetemp=$RECOVERY_OUTPUT/pytest-tmp --junitxml=$RECOVERY_OUTPUT/pytest.xml
```

UTC `2026-10-22T23:45:16.578127+00:00` 至 `2026-10-22T23:45:50.970400+00:00`，真实 exit 0，**6 passed / 2 warnings / 33.42s**；外层计时 34.392179s。全部为离线 FunctionModel；autouse 夹具禁止 live HTTP 与真实模型请求。按通过的逐任务 usage 断言合计 73 次本地调用（27+19+27），真实模型/来源外呼为 0。此六项不执行 live 费用预留，原子预算与未知费用保留应继续引用独立恢复/账本测试，不能以本轮替代。

两条警告仅是 `record_property` 与默认 JUnit xunit2 的格式提示，两个观察属性实际已写入 XML。bounds 与 resume 的“第 3 首次调用时间减轮询观察前驱 SUCCESS 时间”分别为 +0.156530s、+0.673525s；它们不是精确 dequeue 或释放槽耗时。

## 原有强断言与新等待边界

原 `submit_three` 的三条断言和四个测试函数的全部原 Assert AST 均保留。取消/恢复两个测试各新增一条未取消任务必须为 9 次模型调用的断言。

- 两计算确实重叠，第 3 child 必须 ENQUEUED 且处于精确计算 queue，业务 queued，峰值恰为 2。
- 原生取消后 provider 仍在途时第三项不得进入；释放后第三项必须进入；取消者终态、无结果、只一次调用仍检查。
- authorized resume 的 child/parent 必须各回精确原生 queue，未释放前不能绕过名额。
- 三份独立结果、每任务九调用、计算 child SUCCESS、待审 parent P2026-10-22T23:45:50.970400+00:00ING、正确 parent queue 和实际并发上限仍检查。
- 新业务等待按已有 `max_seconds=90` 等真实 child SUCCESS，业务失败/中断/超时/取消或 native ERROR/CANCELLED 会立即失败；在观察到前驱完成后，仍以独立的原 12 秒短等待检查后续进入。child 完成后的 waiting_review 等待仍用未改的原 helper。
- 诊断只包含任务序号、业务状态/进度、native 状态与调用数，不包含业务 ID 或正文。

## 为什么接受此修复

生产 calculation 的计算槽覆盖整条核对与持久化。夹具一次抽取、两个包各四次判断，首次 gate 返回后仍有八次真实离线模型调用及 durable steps/业务回执。旧测试从首次 provider 返回起计 12 秒，混入了剩余整套业务成本。

原完整 CI 的 3 failed / 2172 passed / 1274.81s 仍保留：取消例已证明第三计算进入，超时快照仍在正常推进至 7/8 项；其余两项缺少截止时的完整 child 快照。另一 Agent 的受控两秒/调用旧红反例已明确在业务 90 秒限时前失败；该实验与慢速绿属于另一个执行来源，不计入本轮六项。随后同一插件、相同提交前测试 SHA 的三项慢速绿为 **3 passed / 2 warnings / 123.13s / exit 0**，仍保留全部预期状态、每任务调用数及实际峰值 2；本复核者只核对其受控文件 SHA，不冒称重新执行。慢速签名观察差为 +2.138514s、+2.190360s，包含人为注入的两秒延迟，不能当作原生队列纯耗时。不据本地或慢速绿声明完整 CI 修复完成。

## 证据与归因限制

安全结构化数据见 [native-queue-lifecycle-test-review.json](native-queue-lifecycle-test-review.json)。原始受控输出位于 `work/native-queue-lifecycle-normal-01/`，只在安全 JSON 中记录相对路径、文件大小和 SHA；不公开数据库、完整原始日志或凭据。

本轮直接支持 B09-09 的 admission/取消/待审释放，以及 B09-10 的 authorized resume queue 与结果调用隔离；SIGKILL、升级回滚、live 原子账本和 v4 备份继续分别归因。未重复运行完整负载、全套后端或 Linux CI；未改代码、依赖、Git 历史或冻结负载目录。最终矩阵仍等待主 Agent 的整合/CI结果。

后续只读 `git show` 核对：提交 `76ce23720c14e9e66e583e4cc15ad95356f5e519` 确实仅含该测试文件，文件 SHA 与本次实跑 byte 一致；backend 与依赖锁相对基线无变化。这是提交归属核验，**没有提交后重跑测试**。
