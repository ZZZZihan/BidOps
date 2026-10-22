# B05 持久任务、取消与恢复实际验收

实现 [Draft PR #20](https://github.com/ZZZZihan/BidOps/pull/20)，计划 B05，分支 `codex/b05-durable-runs`，堆叠于 [B04 Draft PR #19](https://github.com/ZZZZihan/BidOps/pull/19)。
本次增量为 DBOS 原生父/计算子工作流、不可变任务输入、进度与取消、检查点及模型响应回执、
未知费用保留、任务页面、停机双库备份和私有工作流历史留存。正式人工审核由 B06 接入。
没有合并 main、部署、真实模型请求或采购来源请求。

**B05-01～07 专项工程 C 验收 PASS；整条堆叠链代码合并 BLOCKED。** 前置 B02-06 指定的
旧六公告数据库、raw 和历史会话未提供。远端当前 Head CI 另列于 PR 描述，不能以本机结果替代。
**业务验收 BLOCKED。** 缺少真实企业材料、独立金标/计时、最新完整费用账本及真实 IdP/HTTPS。

## 被测版本、输入与执行者

- Base B04：`a3aabb3f0a2e25635408f8def44f67e8e0cb5f0e`。
- B05 实现与完整本机验证提交：`509e8007218c306d0e0639d96317537676570a0b`，tree
  `95f7df25bb516d9f337c4475428190e6b4bcebae`。验证前后工作树干净。后续报告提交只添加证据/说明。
- 主 Agent 执行时间：2026-10-22 19:04:13–19:08:58 UTC，即 2026-10-23 03:04:13–03:08:58 Asia/Shanghai。
  Python 3.12.12、PAI 2.59.0、DBOS 3.3.0、Node 26.11.0、uv 0.10.0；CI 使用 Node 22 / uv 0.12.23。
- B01 的 18 个冻结文件均未修改，主锁 SHA256
  `92ceebd17f2e98b0075eac8f13c9108b2686635e1ece5aa4d5f172de10289b27`；独立 overlay SHA256
  `cc7fa6b4de2bd7dc10ca1745b56c5c1f0891edb649732843195a854e098ccd88`，28 个带哈希的依赖闭包，
  其中 20 个共享版本与主锁一致，新增 8 个包。官方依据/兼容实验见 [ADR-0005](../docs/ADR-0005-DURABLE-RUNS.md)。
- 输入是代码内固定的 UNIT 画像、材料、公告、签名 OIDC 协议和审核事件夹具。前置身份、对象
  grants、版本/哈希、as_of 和留存时点由测试显式建立。真实 SQLite、文件、FastAPI、浏览器、
  DBOS 与 PAI Agent/工具链实际运行；仅替换外部身份传输和模型语义答案，不计入 B08 独立样本。
- 真实 DeepSeek/采购请求 **0**，本次新增实际费用 **¥0**。六个崩溃窗口刻意把本地 FunctionModel
  标为 `deepseek-flash` 并走计费分支，100 input / 10 output token 是构造输入；回执中的
  `offline=false`、¥0.00252 及 ¥0.593536 是**合成账本数值**，不是供应商账单或实际付费。
  子进程拒绝所有 socket connect/sendto，`ALLOW_MODEL_REQUESTS=False`。
- 提示 `qualification-v1`、规则 `qualification-logic-v1`；恢复夹具 max_requests=20、
  max_seconds=120，单次请求限制和其余固定设置随任务持久化；未知尝试同样占用请求数与预算。
  六窗口和等待审核恢复的 application_version 均为
  `37745e749ec40e0899dd910be665eae7801b9932464bb89937b28acfe94784ac`。
- 主 Agent 实现运行时、模型回执/预算、旧资格链窄接线与整合验证；3 个子 Agent 分别承担
  仓储/备份和独立 UI 复核、进程恢复/留存和独立运行时复核、API/工作台/真实浏览器与运维文档。
  每位子 Agent 均显式请求 GPT-6.1 Sol / ultra；provider 实际档位无法独立核验。主 Agent max
  已获本次许可。交叉技术复核不代替企业用户的真实人工验收。

## 完整检查与原始证据

[verification.json](evidence/b05-20261023/verification.json) 保留提交、命令、时间、退出码与干净树检查；
[证据索引](evidence/b05-20261023/evidence-index.json) 按字节列出 SHA256。

| 命令 | 实际结果 | 原始日志 |
| --- | --- | --- |
| `.venv/bin/pytest -q --basetemp <受控证据目录>/pytest` | 1316 passed in 256.35s；B05 新增 215 项 | [backend](evidence/b05-20261023/backend.txt) / [新增清单](evidence/b05-20261023/b05-test-inventory.txt) |
| `.venv/bin/ruff check .` | All checks passed | [lint](evidence/b05-20261023/lint.txt) |
| `.venv/bin/ruff format --check .` | 174 files already formatted | [format](evidence/b05-20261023/format.txt) |
| 精确 B01 freeze/input pin 的 `scripts/screening_eval.py` | PASS；Agent score=null，真实业务仍 BLOCKED | [freeze](evidence/b05-20261023/frozen-scorer.json) |
| `npm run build`（frontend） | TypeScript/Vite 成功 | [build](evidence/b05-20261023/frontend-build.txt) |
| `BIDOPS_BROWSER_CHANNEL=chrome npm run test:e2e`（frontend） | 75 passed in 22.3s：既有 39 + B05 协议 34 + 真实后端 2 | [browser](evidence/b05-20261023/frontend-browser.txt) |

定向后端入口 `.venv/bin/pytest -q tests/test_jobs_*.py`：repository 90、model 41、backup 34、
runtime 15、retention 11、API 17、进程恢复 7。定向浏览器入口在 frontend 运行
`BIDOPS_BROWSER_CHANNEL=chrome npx playwright test tests/jobs.spec.ts tests/jobs-real.spec.ts`。
恢复验收必须保留 `--basetemp`；不提交本地 SQLite/DBOS 文件、bootstrap 路径或私有原文。

## 专项验收矩阵

每行共用上述完整实现 SHA、输入类别、执行时间及人员。链接测试给出具体前置身份、样本版本、
操作与断言；完整日志证明这些测试实际执行。实际 B05 原生审核消息仍为 `test_fixture`。

| AC-ID | 输入、操作与预期 | 实际观察与证据 | 状态 |
| --- | --- | --- | --- |
| B05-01 C | 核查锁定 PAI 的成熟后端；完成一步后 SIGKILL，再启动而不重提 | [ADR](../docs/ADR-0005-DURABLE-RUNS.md) 比较 DBOS/Temporal；DBOS 3.3.0 与 PAI 2.59.0 的 3 进程探针由原生恢复已存步骤，第三进程读取不调用模型；[43 文件索引](evidence/b05-backend-selection/evidence-index.json) 与兼容/依赖审计一致；SQLite 仅单机受控试用 | PASS |
| B05-02 C | 202、持续进度、刷新、等待/继续/失败/超时/取消 | [API 17 项](../tests/test_jobs_api.py)、[runtime 15 项](../tests/test_jobs_runtime.py) 及实际 HTTP 浏览器保留 run_id/result revision；9 个执行状态独立于资格/人审；浏览器重载不重新计算；缺模型启动只读、无替代模型 | PASS |
| B05-03 C | 六个模型/记账/检查点/结果窗口及等待审核时杀进程 | [7 组原始记录](evidence/b05-20261023/recovery-summary.json)，21 个不同 PID；每组始终 1 任务/1 结果，第三读取业务快照相同；response-before-receipt 本地调用10次且保留unknown，其他六组9次；没有恰好一次外部计费承诺 | PASS |
| B05-04 C | 顺序/并发同 key、换输入、不同主体、重复恢复与容量边界 | [repository](../tests/test_jobs_repository.py) SQLite IMMEDIATE 及唯一约束：同输入只一任务，不同输入409，主体隔离；全局活动容量检查与INSERT同事务，配置1时两个并发仅一成功；完整输入与 as_of 首次固定 | PASS |
| B05-05 C | 排队、工具间、模型在途取消，随后恢复/重复请求及SDK故障 | [runtime](../tests/test_jobs_runtime.py) 取消先持久化，cancelled不可复活；在途回执或unknown费用保留；后端失败503但cancel_requested可显式重试；旧成功/失败原生检查点不可伪装恢复成功 | PASS |
| B05-06 C | 正确/错/重复/过期审核事件，等待重启，队列/工具/在途/等待撤权，更新材料与他人ID | [repository](../tests/test_jobs_repository.py)、[model](../tests/test_jobs_model.py)、[runtime](../tests/test_jobs_runtime.py) 和真实浏览器：每阶段及最终结果事务检查当前授权；撤权阻止释放、历史为空/详情404；正确fixture继续同链而结果仍unreviewed；新材料需新revision，旧版本执行拒绝 | PASS |
| B05-07 C | 停机双库备份、错配/缺件/篡改、恢复隔离、费用屏障、后端不可用与过期清理 | [backup 34](../tests/test_jobs_backup.py)、[retention 11](../tests/test_jobs_retention.py) 及真实runtime v4恢复：两库随机快照绑定/映射摘要拒错配；授权清除，显式owner任务+指定结果原子再授权后才允许resume；新费用token不释放已用/unknown；正文及native父子历史清理失败可重试 | PASS |

## 七组进程中断观察

数据源、PID、输入/结果哈希、用量和步骤清单见 [机器汇总](evidence/b05-20261023/recovery-summary.json)。
每组顺序为原进程 SIGKILL（返回 -9）→独立解释器原生恢复（0）→第三解释器保存读取（0）。
没有重新提交新任务，没有删除业务库后重造。下表金额全部是本地构造账本值。

| 中断窗口 | 本地 FunctionModel 调用 | 已记录尝试 / unknown | 已确认合成费用 / 保留合成预留 | 结果 |
| --- | --- | --- | --- | --- |
| before_model | 9 | 10 / 1 | 0.00252 / 0.593536 | 1 业务结果；未知预留不释放 |
| response_before_receipt | 10 | 10 / 1 | 0.00252 / 0.593536 | 返回未入账可再次请求，明确可能重复计费 |
| receipt_before_accounting | 9 | 9 / 0 | 0.00252 / 0 | 复用回执后归集 |
| accounting_before_checkpoint | 9 | 9 / 0 | 0.00252 / 0 | 复用已确认回执 |
| before_result_commit | 9 | 9 / 0 | 0.00252 / 0 | 检查点恢复后只保存固定结果 |
| after_result_commit | 9 | 9 / 0 | 0.00252 / 0 | 校验并复用既有结果 |
| waiting_review | 9 | 9 / 0 | 离线无费用账本 | 原生等待恢复，第三读取仍无新增调用 |

`model_calls` 为已记录尝试，不等于供应商成功请求数；孤立预留也被计入费用汇总。
使用无效 token 用量或不明模型标识时不把缺失解释为零费用。取消、超时、恢复旧备份同样
不能退还已发生或不明用量。真实最新账单尚未提供，任何 paid/live 验收保持关闭。

## 共同门槛

| ID | 实际依据 | 状态 |
| --- | --- | --- |
| G-01 | 新任务/响应回执/容量/取消/API/UI/双库与留存分别映射 B05-01～07；不把fixture签为真实人审 | PASS |
| G-02 | 干净实现提交完整1316后端/75浏览器，Ruff/build/freeze通过；当前Head远端CI另列 | PASS |
| G-03 | 008增量及重复迁移、007既有数据读取、实际v4停止worker备份→独立恢复→来源和job/result再授权→原生resume；前置B02真实旧库仍BLOCKED | PASS |
| G-04 | enterprise-only、当前主体/角色/owner/全来源、CSRF、幂等/CAS及最后释放事务；错误/日志去敏；raw和DBOS库私有，公开证据仅合成哈希/摘要 | PASS |
| G-05 | ADR比较与官方文档、28项hashed overlay/主锁不变、PAI/DBOS原生workflow/queue/checkpoint；未自制通用执行引擎 | PASS |
| G-06 | 实际模型/采购外呼0、新增¥0；synthetic ledger明确区分；费用恢复token、unknown/orphan保留；缺最新账本不启用live | PASS |
| G-07 | 完整SHA/tree与时间绑定、21进程一致appversion、18项冻结哈希相同、原始日志/索引；实现前独立WIP实验不冒充最终提交 | PASS |
| G-08 | 默认功能关闭；缺模型可读历史；关闭功能仍执行私有留存；v4恢复隔离/再授权不自动执行；保留旧DB/新结果/账本且不删表回退 | PASS |

## 保留的故障、修正与独立复核

开发及交叉复核实际发现并修复：活动容量先读后写竞争；在async lifespan直接注册DBOS队列；
审核fixture列名不匹配；权限中断被普通异常缓存为原生SUCCESS/ERROR导致无效resume；错误的
`dbos.error` 子模块导入；取消后端失败500且界面无重试操作；同state_version的旧轮询覆盖新
unknown费用提醒。对应负例均保留在最终套件，未删断言或降低门槛。

其中旧轮询费用竞争先在旧UI构建重现失败，修复后由未参与UI实现的Agent用原CLI反例复验：
释放旧响应后、下一轮轮询前，unknown=1及预留¥0.45仍显示（此金额是协议夹具）。原生恢复
由另一Agent独立检查版本/权限/CAS/无新增模型调用；仓储/备份与SDK失败重试也交叉复核。

一次直接向DBOS投递未保存的任意字符串造成`job_review_not_found`，被核查为无效内部夹具，
不列作公开API漏洞；真实fixture入口先验证并保存事件，没有暴露HTTP审批接口。来源文档快照
保留官方原始空白字节，`git diff --check` 的12条尾空格仅在该不可变原始资料中；代码无此问题。
归档时 pytest 的 `current` symlink 曾导致重复目录名，按真实路径去重后保留7组原始字节，
没有重跑、挑选或覆盖失败验收。

## 运行、回退与下一步

[DURABLE-RUNS](../docs/DURABLE-RUNS.md) 给出安装、配置、API、008迁移、v4停机备份、恢复授权/
费用屏障、显式继续与非破坏回退。先冻结基础 `uv sync --frozen`，再按哈希加装overlay；之后
使用 `uv run --frozen --no-sync`，避免普通同步移除DBOS。`BIDOPS_JOBS_ENABLED` 默认false，
单worker/单计算槽，活动任务默认上限100；不是100路模型并发或多实例能力。

Docker/CI安装相同overlay并检查已安装DBOS版本、锁和来源哈希、默认关闭及非root；实际远端
结果仍须看当前提交的CI日志。SQLite是受控单机工程边界，没有宣称生产PostgreSQL/HA已验证。

下一项 B06：真实服务端身份的逐项审核事件、并发版本冲突、补材料关联新run、正式审核工作台
与完整导出。B05只提供明确标为test_fixture的内部工程事件；`completed`不代表人工批准。
旧六库、最新完整费用账本、授权企业资料、独立人员、真实IdP/HTTPS/目标部署继续作为外部
阻塞记录，不因本PR工程通过而改为PASS。
