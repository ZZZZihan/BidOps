# BidOps B01–B10 交接矩阵：71 项专项与 8 项共同门槛

日期：2026-10-23。**企业试用与生产发布仍为 BLOCKED / NO_GO。** 71项专项中55项C有工程PASS证据，B02-06是唯一BLOCKED的C；15项真实业务B全部BLOCKED。**B09-02另保留已观测工程容量FAIL：冻结计算并发目标2、实际1；排队任务不计计算。**

工程测试、签名OIDC协议夹具、FunctionModel合成运行、技术复核、真实IdP/供应商证据与具名企业验收分别记账。全部十个固定代码提交的远端三个job均已通过，不能因此关闭真实旧库、独立质量/节时、目标部署或已知容量失败。没有main合并、生产部署或真实企业业务PASS。

## 计数与证据范围

**原69项 + 实施新增B04-08 + 本次新增B09-08 = 71项：56C + 15B。** 各PR项数为`7 / 6 / 8 / 8 / 7 / 7 / 7 / 7 / 8 / 6`，上游B01–B09为65项、B10另6项。8G独立列示。专项55 PASS、16 BLOCKED、0 NOT_RUN是证据范围的台账分布，不是业务完成率。

[固定计划](https://github.com/ZZZZihan/BidOps/blob/efd4becb8e0f39a22d3933ddd542f6acc6e695b1/docs/ENTERPRISE-PR-PLAN.md)第176行保留`B04-08 C（实施新增）`，第288行B09-08、第290行71项说明。B04报告第56/67/73行分别说明新增编号、费用恢复验证与G-01映射；交叉证据不删独立AC。此前70项口径漏掉合法B04-08，已更正；B10最低71合同、畸形/丢失/重复行及未升级B10合同拒绝规则均补回归。

## 固定代码与实际CI

每项均含backend、frontend、container三个成功job。表中代码SHA固定日志，后续文档/安全证据提交另列；套件高度重叠，不相加。PR状态快照为#16 Open非Draft，#17–25 Open/Draft，全部未合并；实时状态以各PR为准。

| 计划 / PR | 固定代码SHA | 后端PASS / 秒 | 浏览器PASS / 时长 | 三job |
| --- | --- | --- | --- | --- |
| B01 / [#16](https://github.com/ZZZZihan/BidOps/pull/16) | `069e4b495828caca4c64447062c030a0f313aada` | 571 / 9.92 | 12 / 13.8s | [PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39816800842) |
| B02 / [#17](https://github.com/ZZZZihan/BidOps/pull/17) | `c3435402247d0cd66986a0bbd8854304c017f864` | 685 / 20.25 | 14 / 19.8s | [PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39823377065) |
| B03 / [#18](https://github.com/ZZZZihan/BidOps/pull/18) | `0d8ec7afd54485110ed83728145ae86696a5dc47` | 795 / 39.14 | 20 / 22.5s | [PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39859735827) |
| B04 / [#19](https://github.com/ZZZZihan/BidOps/pull/19) | `cfdc2bc72c255c3dc7637868c39c56574cd66cdf` | 1105 / 75.84 | 39 / 36.2s | [PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39859741649) |
| B05 / [#20](https://github.com/ZZZZihan/BidOps/pull/20) | `3764dbe0a8c4ff3d20f38677167a87ed29f60c71` | 1320 / 430.43 | 75 / 1.3m | [PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39859749226) |
| B06 / [#21](https://github.com/ZZZZihan/BidOps/pull/21) | `4d2c6e89301026f95b2f0923262bfb73dbfb2bc4` | 1434 / 821.72 | 105 / 2.2m | [PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39859754197) |
| B07 / [#22](https://github.com/ZZZZihan/BidOps/pull/22) | `c840046632663c350ae2d06cfbb0be43e21f1e0f` | 1735 / 848.37 | 148 / 2.8m | [PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39859757990) |
| B08 / [#23](https://github.com/ZZZZihan/BidOps/pull/23) | `d0a50b61d94953114d9edcc07db9bcbfc8948b0d` | 1856 / 634.12 | 148 / 2.1m | [PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39859761355) |
| B09 / [#24](https://github.com/ZZZZihan/BidOps/pull/24) | `db4f55dbf410acb4ef36c87738b0f95eddada5ed` | 2010 / 675.52 | 152 / 2.7m | [PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39859780307) |
| B10 / [#25](https://github.com/ZZZZihan/BidOps/pull/25) | `88dee00a36adf64ec20bb2918ead48f5a388c520` | 2133 / 1042.83 | 152 / 2.1m | [PASS](https://github.com/ZZZZihan/BidOps/actions/runs/39860218830) |

B10同一88dee固定代码本机2133 PASS／602.81秒，远端2133 PASS／1042.83秒、152浏览器／2.1分钟；两种环境分别记录。本机四命令均exit0、256格式文件、18冻结文件pin、工作树clean，UTC范围2026-10-22 21:48:39.756853至21:58:46.375013。远端完成快照观察于2026-10-22T22:07:32.936695+00:00。

B09正式报告与安全证据文档提交[`efd4becb8e0f39a22d3933ddd542f6acc6e695b1`](https://github.com/ZZZZihan/BidOps/commit/efd4becb8e0f39a22d3933ddd542f6acc6e695b1)未改db4f代码/测试；其[文档提交CI](https://github.com/ZZZZihan/BidOps/actions/runs/39863080054)单列，不代替上表固定代码CI。正式负载继续记在836，不声称在后续代码或文档提交重跑。

## 来源索引

S01–S08报告保留对应分支原工程结果与失败；最新完整CI以上表为准。S09为正式容量/恢复报告，S10为实际验收工具与运行说明。

| 来源 | 不可变公开材料 |
| --- | --- |
| S01 | [B01 报告](https://github.com/ZZZZihan/BidOps/blob/069e4b495828caca4c64447062c030a0f313aada/reports/b01-contracts-eval-20261023.md) |
| S02 | [B02 报告](https://github.com/ZZZZihan/BidOps/blob/c3435402247d0cd66986a0bbd8854304c017f864/reports/b02-notice-versions-20261023.md) |
| S03 | [B03 报告](https://github.com/ZZZZihan/BidOps/blob/b2a26830c0274734cc4db5d72e461657da923da8/reports/b03-company-access-20261023.md) |
| S04 | [B04 报告](https://github.com/ZZZZihan/BidOps/blob/a3aabb3f0a2e25635408f8def44f67e8e0cb5f0e/reports/b04-qualification-screening-20261023.md) |
| S05 | [B05 报告](https://github.com/ZZZZihan/BidOps/blob/4fd057ef9141d31d53540532683418e09963e324/reports/b05-durable-runs-20261023.md) |
| S06 | [B06 报告](https://github.com/ZZZZihan/BidOps/blob/e468a4a863f49f2e94c39c1e90bd66fd3ce84206/reports/b06-human-review-20261023.md) |
| S07 | [B07 报告](https://github.com/ZZZZihan/BidOps/blob/eb0505fa1aaaa45ab67cea34bee682845d43163a/reports/b07-authorized-updates-20261023.md) |
| S08 | [B08 报告](https://github.com/ZZZZihan/BidOps/blob/43709a0f2833424dd573991550676efb13468c2b/reports/b08-independent-evaluation-20261023.md) |
| S09 | [B09正式报告](https://github.com/ZZZZihan/BidOps/blob/efd4becb8e0f39a22d3933ddd542f6acc6e695b1/reports/b09-capacity-recovery-20261023.md) |
| S10 | [B10企业验收说明](https://github.com/ZZZZihan/BidOps/blob/88dee00a36adf64ec20bb2918ead48f5a388c520/docs/ENTERPRISE-UAT.md) |

## 71项专项交接表

表内PASS均限工程；B类BLOCKED不等于代码未实现。B09-02的工程FAIL单独保留。
### B01：7 项

| AC / 类型 | 台账状态 | 已有证据与不可扩大之处 | 后续输入 |
| --- | --- | --- | --- |
| B01-01 C | PASS（工程） | S01：五态、逻辑、版本/双方证据及伪人审非法对象正负 schema 检查；不是资质业务判断通过。 | 无新增外部输入。 |
| B01-02 C | PASS（工程） | S01：旧六公告限开发/回归，项目族/版本/包/附件及内容闭包不得跨集合。 | 真正盲测另需 E03。 |
| B01-03 C | PASS（工程） | S01：真实数量/类别/版式/风险层不足会 BLOCKED 的协议已验证；真实数量当时为 0。 | 实际满足门槛需 E02/E03。 |
| B01-04 C | PASS（工程） | S01：漏项、错判、引用无关/非法/过期、全弃答、失败/缺层进入分母；零分母不能 PASS。 | 真实成绩需 E03/E04/E06。 |
| B01-05 C | PASS（工程） | S01：P1 溯源 100%、关键截止日/预算单位精确率 95%/覆盖 85%门槛；Decimal/时区与未知值边界。 | 真实 P1 数据与逐字段金标需 E03/E04。 |
| B01-06 C | PASS（工程） | S01：独立标注/复核/仲裁和配对盲态协议检查通过；没有真实人员计时。 | E04/E05。 |
| B01-07 C | PASS（工程） | S01：冻结版本/哈希篡改拒绝；固定评分器校准不计 Agent 成绩。 | 真实首测需 E03/E04，保留冻结和首测历史。 |

### B02：6 项

| AC / 类型 | 台账状态 | 已有证据与不可扩大之处 | 后续输入 |
| --- | --- | --- | --- |
| B02-01 C | PASS（工程） | S02：原公告/两更正/终止/多包，稳定 append-only 版本，重复不新增逻辑记录，歧义待复核。 | 无新增外部输入。 |
| B02-02 C | PASS（工程） | S02：raw/text/hash/版本/偏移绑定，篡改越界拒绝，旧引用仍可定位旧解释。 | 无新增外部输入。 |
| B02-03 C | PASS（工程） | S02：as_of、晚到更正/终止/缺关联/过期观察，未来截止不直接视为可投。 | 真实新鲜度需 E11。 |
| B02-04 C | PASS（工程） | S02：逐附件可读/失败/加密/扫描未知/不支持/丢失状态；不静默忽略。PDF 能力由后续 B07 补充，不回写旧版支持范围。 | 真实附件覆盖需 E02/E03/E11。 |
| B02-05 C | PASS（工程） | S02：507 条、501 之后目标、Decimal/时间排序、全量计数/稳定页/可见截断；不是 1 万/5 万容量证明。 | 容量另验 B09-02/08。 |
| B02-06 C | BLOCKED | S02：仅合成六行/历史会话迁移、重复与恢复通过；指定真实旧六库/raw/历史会话未收到，真实旧服务回退未执行。**这是 C 门槛与堆叠合并阻塞。** | E01；不能用新建工程六行替代。 |

### B03：8 项

| AC / 类型 | 台账状态 | 已有证据与不可扩大之处 | 后续输入 |
| --- | --- | --- | --- |
| B03-01 C | PASS（工程） | S03：画像/材料不可变版本，自报/有证明区分、固定引用，竞争 409；跨页历史留存取服务端真实值。 | 真实使用 E09。 |
| B03-02 C | PASS（工程） | S03：角色×对象×动作 HTTP 负测；admin 无隐含正文读权；拒绝/撤权清私有页面缓存。 | 真实账号/ACL 验收 E07/E09。 |
| B03-03 C | PASS（工程） | S03：来源闭包、工具前 ACL、缓存/派生索引继承；后续运行/审核真实接线另有 B05/B06 证据。 | 无新增外部输入。 |
| B03-04 C | PASS（工程） | S03：本地停用/撤权下一请求生效，旧 context 重查，新 SSO 不启用 locally disabled 主体。 | IdP 真实传播另验 B10-03/E07。 |
| B03-05 C | PASS（工程） | S03：按所需资格时点判断有效期，缺证明/冲突/不符分开；留存期限不当作证书有效期。 | 实际证书与授权 E09。 |
| B03-06 C | PASS（工程） | S03：备份后撤权/停用/删除/到期，再恢复清登录/快照/ACL、私有对象隔离；旧私有无期限快照拒绝。 | 真实目标丢失/权限对账另需 E08/E09/E06，见 B09-06。 |
| B03-07 C | PASS（工程） | S03：大小/字符/节点/深度、格式/中文名/穿越/归档拒绝，限定 upload 路由有界读取。 | 无新增外部输入。 |
| B03-08 C | PASS（工程） | S03：Keycloak RS256 back-channel 签名/issuer/aud/jti/iat 单调水位、同秒冲突撤销、240 秒快照 failclosed。仅协议模拟；角色变化不保证有 logout 事件。 | 真实 IdP ≤300 秒目标需 E07/E08/E09，不能用模拟身份替代。 |

### B04：8 项（含实施新增 08）

| AC / 类型 | 台账状态 | 已有证据与不可扩大之处 | 后续输入 |
| --- | --- | --- | --- |
| B04-01 C | PASS（工程） | S04：固定全量包级批次，候选/排除/待核实均保留，完整分页、计数与筛选输入版本；候选不等于资格通过。 | 正式大容量另验 B09-02/08。 |
| B04-02 C | PASS（工程） | S04：PAI 抽取 AND/OR/NOT/例外/包范围、超过三条和附件，段界/未解析/资源上限明确。 | 真实语义召回需 E03/E04。 |
| B04-03 C | PASS（工程） | S04：五态与公告/企业双边固定证据；缺证明不能判满足。FunctionModel 五态夹具不是真实准确率。 | E03/E04/E09。 |
| B04-04 C | PASS（工程） | S04：假引用/页/版本/权限/时效拒绝；可解析不等于语义支持；原 Agent 人审状态始终 unreviewed。 | 真实支持率需 E04。 |
| B04-05 C | PASS（工程） | S04：硬不符/未知/冲突/终止不能自动资格通过，仅建议后续审核；无 approve 能力。 | 无新增外部输入。 |
| B04-06 C | PASS（工程） | S04：固定 manifest/provenance/raw 私有保存，GET 不重跑，旧输入/提示/模型保留，stale 单独披露。 | 实际返回模型/完整账本需 E06。 |
| B04-07 C | PASS（工程） | S04：实际 PAI 工具链授权、提示注入、格式错/超时/预算失败、partial 与未完成条件显式保存。 | 真实模型调用不因工程通过而获授权；需 E02/E06。 |
| B04-08 C | PASS（工程） | S04 第 67 行：有/无 ledger 旧备份，两次恢复及新旧模型入口；缺/错/旧 token 请求前拒绝，正确 token 不释放已用/unknown，累计 ¥10 不重置。合法新增独立门槛。 | 最新真实账本仍缺 E06；恢复 token 不会重建丢失费用。 |

### B05：7 项

| AC / 类型 | 台账状态 | 已有证据与不可扩大之处 | 后续输入 |
| --- | --- | --- | --- |
| B05-01 C | PASS（工程） | S05：DBOS 3.3.0/PAI 2.59.0 ADR、hashed overlay、真实进程 SIGKILL 后原生恢复已落盘步骤；单机受控范围。 | 无新增外部输入。 |
| B05-02 C | PASS（工程） | S05：稳定 202/run_id、九态、进度/重载/历史，缺模型可只读；不把 completed 视为资格/人工通过。 | 目标部署体验另需 E08/E09。 |
| B05-03 C | PASS（工程） | S05：六故障窗口加等待共 7 组/21 进程，unknown 预留保留；模型回执前崩溃组 10 次，其余 9 次合成调用。无外部绝不重复计费承诺。 | 真实账单及目标演练需 E06/E08。 |
| B05-04 C | PASS（工程） | S05：同主体同 key 同输入同任务、异输入 409、主体隔离、并发唯一/CAS、容量同事务。 | 无新增外部输入。 |
| B05-05 C | PASS（工程） | S05：cancel_requested 先持久化，取消终态不可复活，在途费用保留；SDK 失败可显式重试取消。 | 无新增外部输入。 |
| B05-06 C | PASS（工程） | S05：排队/运行/等待、工具前/恢复/最终释放 ACL；正确/错/重放/过期事件及 native cancel/resume；B05 fixture 不冒充人审。 | 正式人工审核见 B06；真实撤权 E07/E09。 |
| B05-07 C | PASS（工程） | S05：停 worker 的 v4 业务库+DBOS 一致备份、错配拒绝、私有历史清理、隔离/显式再授权、unknown/费用屏障不释放。 | 实际实例丢失/RPO/RTO及最新账本需 E06/E08/E09。 |

### B06：7 项

| AC / 类型 | 台账状态 | 已有证据与不可扩大之处 | 后续输入 |
| --- | --- | --- | --- |
| B06-01 C | PASS（工程） | S06：逐项 confirm/reject/request_materials、原五态/双方版本证据/缺失冲突，独立理由，无默认全选批准。 | 真正独立企业复核人 E09。 |
| B06-02 C | PASS（工程） | S06：身份/时间/理由/版本事件追加，更正须 supersedes latest，幂等不重复；不覆盖旧决定。 | 真实身份/签署真实性 E07/E09。 |
| B06-03 C | PASS（工程） | S06：两个 reviewer 旧版 CAS 只有一方生效，另一 409 留草稿并提示刷新。 | 无新增外部输入。 |
| B06-04 C | PASS（工程） | S06：补料关联 successor 新 job、跨重启、显式接入新 revision 后全部重审，历史保留；不改旧 manifest。 | 实际材料与操作人 E09。 |
| B06-05 C | PASS（工程） | S06：更正/终止使旧人工投影失效，source_state/effective 单独显示，Agent 原判断与历史不改写。 | 真实来源版本覆盖 E03/E11。 |
| B06-06 C | PASS（工程） | S06：当前 reviewer 角色 + owner 显式 assignment + 全部来源可读，CSRF/CAS/重放校验；admin 无隐含批准，Agent 不能自授。 | 实际角色/assignment/ACL 矩阵 E07/E09。 |
| B06-07 C | PASS（工程） | S06：固定合成链筛选→核对→补料→复核→close→全链受控导出；delivery pending 与 native complete 分开，无自动投标。 | 真实完整链与独立用户记录 E09，见 B10-04。 |

### B07：7 项

| AC / 类型 | 台账状态 | 已有证据与不可扩大之处 | 后续输入 |
| --- | --- | --- | --- |
| B07-01 C | PASS（工程） | S07：allowed/denied/unknown、许可范围/方式/有效期/速率，未知不自动 HTTP；手工责任声明不扩大网络许可。 | 真实许可 E02。 |
| B07-02 C | PASS（工程） | S07：原子页、成功才前移 cursor、append-only 版本、重复/更正/终止和中断重试。 | 真实周期覆盖 E11。 |
| B07-03 C | PASS（工程） | S07：有界 pypdf 子进程、receipt/hash/服务端页定位；扫描/加密/缺页/下载失败显式。无 OCR/Office/通用 crawler/DNS 隔离承诺。 | 真实附件许可与覆盖 E02/E03/E11。 |
| B07-04 C | PASS（工程） | S07：429/403/超时/重定向/大小/速率/重试上限；失败有记录，不绕过访问限制。 | 真实环境告警另需 E10。 |
| B07-05 C | PASS（工程） | S07：last_attempt 与 last_success 分开，失败不续“已核实”；last_success 是成功页观察，不保证整批全部成功。 | 跨真实新鲜度窗口 E11。 |
| B07-06 C | PASS（工程） | S07：同 DBOS native cron/队列/来源互斥、排队/取消/重启和 source-only 关闭资格模型；owner/当前 admin 读停任务。 | 无新增外部输入。 |
| B07-07 B | BLOCKED | 没有真实许可下至少 3 次、覆盖至少 24 小时的周期和一次真实中断恢复。无更新/未遇真实更正须如实保留，不能合成补足覆盖。 | E02/E08/E11。 |

### B08：7 项

| AC / 类型 | 台账状态 | 已有证据与不可扩大之处 | 后续输入 |
| --- | --- | --- | --- |
| B08-01 B | BLOCKED | S08：真实独立新族/金标数 0；没有合法新候选池、冻结随机抽样和真实六风险分层证据。 | E02/E03。 |
| B08-02 B | BLOCKED | S08：独立实际标注者/关键复核/仲裁记录未提供；开发者、模型预标注或合成 true 声明均不算。 | E04。 |
| B08-03 C | PASS（工程） | S08：helper68/collector27/runner26，所有登记任务含失败/超时/缺失/排除入账，P1 独立字段、原 Agent 与人审投影分开、ACL 最后复查。 | 无新增外部输入；排除/未提交不伪造 completed 来提分。 |
| B08-04 B | BLOCKED | S08：真实冻结盲测未执行，无召回/错放/支持率成绩；缺当前账本。完整任务、保存结果/实际模型及 raw hash 收集能力不等于真实效果。 | E02/E03/E04/E06。 |
| B08-05 B | BLOCKED | S08：缺至少 2 名独立操作者、10 个项目族配对实测；不能报节时 ≥20%。失败/超时保留质量分母并令节时 FAIL。 | E05，数据/金标需 E03/E04。 |
| B08-06 B | BLOCKED | S08：没有真实首次盲测/答案接触与调试历史；若按答案修复，旧集只能回归，需新独立盲集。 | E03/E04/E05 的冻结和首测记录。 |
| B08-07 C | PASS（工程） | S08：机器报告分开 PASS/FAIL/BLOCKED，无总分抵消硬门槛，工程 Agent score=null，business BLOCKED。 | 最终各分支当前报告仍须汇总，不得漏新 AC。 |

### B09：8 项（新增 08 独立列示）

| AC / 类型 | 台账状态 | 已有证据与不可扩大之处 | 后续输入 |
| --- | --- | --- | --- |
| B09-01 C | PASS（工程） | 冻结manifest/完整输入、预登记到达、完整规模及最终db4f三jobCI已验；正式836负载4200总/3600稳态，输入完整性PASS。target2/actual1容量FAIL另列B09-02，manifest齐备不等于容量达标。 | 真实目标机/环境结论仍需E08；不需为合成工程检查提供真实模型key。 |
| B09-02 B | **FAIL（正式本机工程容量）；BLOCKED（真实目标场景）** | 冻结target2、实测compute峰1，目标重叠0；稳态3600含4个ReadError，nonmodel P95 263ms/受理108ms，队列P95 480734ms/峰17、compute P95 31774ms。良好API延迟和错误率不抵消并发失败，排队不算计算。 | 保留836原始失败及安全曲线；架构若改，另立迁移/回退范围并按未降低的原档位重验。真实目标环境另需E08。 |
| B09-03 C | PASS（工程） | 队列/限流/预算/重试/过载负向与最终db4f 2010后端CI通过；正式负载如实保留队列峰17及长排队。工程防护通过不把容量目标改为单计算，也不授权模型付费。 | 无新增外部输入；真实费用/目标负载另按其所涉E06/E08核验。 |
| B09-04 C | PASS（工程） | 最终db4f三jobCI及health/watchdog故障工程检查通过，两队列age排除隔离/清理；worker started标志不冒充native线程独立心跳。 | 真实目标故障需E08及演练范围；实际企业收件另需E10。 |
| B09-05 B | BLOCKED | 没有企业指定真实接收端及故障/恢复收件，且尚无对应真实对外发送授权。 | E10；本地 webhook 替身不能替代实际企业收件。 |
| B09-06 B | BLOCKED | 本机完整恢复有双库/文件/审核/费用记录，观测RPO 0.031178秒；最新授权无法确认，私有业务未重开，业务RTO null/BLOCKED。不能把拷贝或安全检查耗时称业务恢复；真实目标全损恢复与完整账本仍缺。 | E06/E08/E09；目标告警另需E10，RPO≤86400/RTO≤1800不放宽。 |
| B09-07 B | BLOCKED | 当前工程容量target2/actual1已FAIL，不能宣称SQLite档位足够，也不能把1计算+1排队解释为2并发。架构/扩容裁决和真实目标复验尚未完成。 | 需记录明确架构裁决；若改基础设施，另开迁移/回退范围并按未降低的原档位重验，保留已知FAIL。 |
| B09-08 C | PASS（工程） | 专用50MB完整候选/普通raw10MB、012索引、review20/100分页与60MB导出通过定向及最终db4f CI。836完整50k负载与停止副本补证读全量/末100/offset50000空页，导出37,703,148bytes，11,403源文件闭包及DB前后hash一致；不称5万个合格项目或目标机SLO。 | 无新增外部输入；原真实旧库兼容仍由B02-06独立阻塞，不能以合成迁移关闭。 |


### B10：6 项

| AC / 类型 | 台账状态 | 已有证据与不可扩大之处 | 后续输入 |
| --- | --- | --- | --- |
| B10-01 C | PASS（工程） | 离线envcheck/六AC核验、实现者123项及独立83项/末1项分别留证；最终88dee本机2133/602.81秒、远端2133/1042.83秒及152浏览器通过，当前CLI退出0/2/2为预期拒绝。工具不认证真实环境、人、许可或账本。 | 无新增外部输入可继续离线核验；实际inventory按E02/E07/E08/E09/E10，真实模型/恢复费用场景另加E06。 |
| B10-02 B | BLOCKED | 无 ≥2 独立真实账号在目标 HTTPS 环境登录/重新 SSO/退出/旧会话重放/双向跨材料会话任务隔离证据。 | E07/E08/E09。 |
| B10-03 B | BLOCKED | 无真实 IdP disable 和 role_change 事件至旧会话、排队/待审任务/工具/新任务/结果释放拒绝 ≤300 秒实测，亦缺真实 IdP 失联到期拒绝。 | E07/E08/E09；同时记录事件/拒绝 UTC 时间，协议夹具不能代替。 |
| B10-04 B | BLOCKED | 无授权真实画像/材料/新公告→候选→逐条核对→补料新 run→人审→导出，及另一独立实际使用者复核。 | E02/E03/E06/E07/E08/E09。 |
| B10-05 B | BLOCKED | 无目标部署的任务中断/幂等/完整 v4 恢复/真实告警联合演练；旧授权/旧审批不得复活、unknown 不释放，RPO/RTO 不能放宽。 | E06/E07/E08/E09/E10；告警必须本次故障之后发出再实际收件，不能用故障前消息。 |
| B10-06 B | BLOCKED | 全部 B01–B09 当前 65 项未齐，真实 B 门槛未通过，且缺具名发布复核。工具 READY 仅表示证据声明/完整性可供独立复核，不赋予 GO。E2、生产 E3 分开。 | E12；需每项真实状态/版本/证据及明确人工决定，不能由开发者/Agent 代签。 |

## 最小外部输入与AC映射

提供资料不自动授权新采集、模型付费、对外消息、环境探测、合并或部署。真实凭据继续由资料/系统责任者在目标机或受控 secret 渠道配置；交接包只收去敏别名、配置项是否具备、固定版本、哈希和受控路径。不要将 token、密码、原始 sub、Cookie、私有原文或 base64 上传体粘贴到普通报告/聊天或 Git。

优先交付两条受控本机路径：**E01原实例完整备份、E06最新完整累计费用账本**，各附责任者、截止UTC及允许只读核查/隔离复制范围。两项可分别推进，不要求先有真实IdP或告警端；不要在聊天里贴数据库正文、业务私密会话、Cookie或密钥。

四组输入按实际工作拆分，E11/E12作为后续产物登记：

| 组 | 现在最少提供什么 | 可先开展及后续边界 |
| --- | --- | --- |
| 1 原资产与全成本：E01/E06 | 完整旧实例路径及完整费用导出路径、责任者/只读范围。 | E01离线闭包/迁移/回退与E06离线对账可独立并行；不需要IdP/告警/金标，不自动授权模型或释放unknown。 |
| 2 权利、样本、独立人员：E02–E05 | 许可依据、合法候选池、标注/复核/计时人员安排。 | 先离线审权利/去重split/独立性并冻结。HTTP/计划更新仅在对应模式/范围/执行授权内开展；模型另需E06及调用授权。E11由后续真实运行产出，未排人工计时不阻止已授权公共更新。 |
| 3 真实场地、用户、企业材料：E07–E09 | HTTPS/目标机和IdP责任者、两名独立用户安排、经授权固定画像/材料。 | 先读配置/权利；相应探测/操作获授权后做真实登录/隔离/撤权。模型不参与的身份测试不需先等待全金标、E11或告警；完整业务链按其实际输入加第2组和必要E06。 |
| 4 运维窗口与真实收件：E08/E10 | 备份/隔离恢复位置、维护窗口、最新主体/ACL/删除状态责任者、真实收件端及本次发送授权。 | 可先离线清点备份；全损/恢复/发送要各自明确授权、最新权限与费用核验。E12在各项执行后形成，不预设通过。 |

逐项动作只检查其必需依赖，不以E01–E12全部到齐作为统一闸门：

| 动作 | 必需输入与授权 | 不自动依赖/不代表什么 |
| --- | --- | --- |
| 原旧库离线迁移、重复迁移和隔离回退 | E01完整闭包、旧运行条件、只读/隔离复制及测试范围。 | 不需E07/告警/评测金标；不原地覆盖，不自动重开私有服务。 |
| 离线费用核对 | E06完整历史、最新核对依据与允许读取范围。 | 不需IdP/告警；不产生新¥10、不释放unknown、不授权真实模型。 |
| 受控来源更新 | E02对应URL/方式/期限/速率许可、受控执行环境及明确此次执行授权。 | 手工许可不扩为HTTP；不需先完成无关人工计时或E12。E11是运行产物。 |
| 真实盲测与人工配对 | E02/E03/E04及预冻结版本/首测分配；配对计时另需E05；真实模型另需E06、固定上限和明确调用授权。 | 人员排好不等于金标/计时完成；缺账本不做付费调用，失败不从分母删。 |
| 真实身份登录、退出、撤权隔离 | E07/E08/E09中的IdP/HTTPS/用户和对应探测操作范围。 | 无模型动作不需先收齐独立金标/未来运行周期；模拟OIDC不算真实IdP。 |
| 真实业务链与恢复/告警 | 所涉合法固定材料/来源和用户权限；真实模型加E06；全损/发送加第4组窗口、最新权限及明确演练/发送授权。 | 提供地址不等于允许发消息，健康200不等于业务RTO完成，工具READY不等于具名GO。 |

| 输入编号 | 最少需要补齐的资料/责任 | 直接阻塞 AC；依赖影响 |
| --- | --- | --- |
| E01 真实旧库 | 原旧六实例的一致SQLite快照（含必要WAL处理）、匹配raw/附件、BidOps业务历史会话/引用、旧代码与运行条件；受控本机路径、责任者、允许只读核查/隔离复制范围。不是Agent登录缓存或凭据，不用合成六行代替。 | **B02-06 C**及G-01/03/08；可先独立离线清点、迁移/引用/重复迁移及隔离旧服务回退，不需等IdP、告警或金标；收到路径不等于PASS。 |
| E02 来源许可 | 可核验许可依据/权利人、精确域名和 URL scope、手工/HTTP/计划方式、正文/附件使用范围、有效期/复核人/时间、频率/并发/重试；与实际 registry 配置交集一致。 | B07-07，B08-01/04，B10-01/04，G-06。unknown/denied 不自动网络；manual 责任声明不自动授予持续网络许可。 |
| E03 新真实数据 | ≥30真实公告、≥3类别、≥2版式；预冻结候选池/seed/抽样、≥20新独立项目族及金标，旧六公告排除，族/包/附件/版本闭包不跨集合；历史与采集当时未来截止各10遵守原可得性约束；六风险层每层≥3不同真实族。保留全部 raw/hash/获取时间/版本关联/附件状态。 | B08-01/04/06；支撑 B01-03/05 真实门槛与 B10-04。合成/旧题改写/换 split 不补真实数量。 |
| E04 独立金标 | 每条 P1 字段、条件逻辑、适用包/时点、双方证据/支持关系、质量单位及输入/金标哈希；实际独立标注者/时间、关键复核与分歧仲裁。开发调试者/LLM 预标注不能冒充独立金标。 | B08-02/04/06；实际质量门槛。未仲裁不能进入正式计分。 |
| E05 人工对照 | ≥2名合格独立使用者、≥10不同项目族成对任务；同题两方式不同人、未看金标/未做同题；事前冻结时限、盲态/平衡顺序/质量单位，实际起止与正时长，全部预登记失败/超时原样保留。 | B08-05/06；不能由开发者代演。median(1−A/H)≥20%且质量不降；任一失败/超时令节时门槛 FAIL。 |
| E06 最新完整费用账本 | 最新完整累计¥10账本/供应商用量导出的受控路径、责任者/截止UTC，覆盖P0/demo/dev/eval/失败/重试/unknown/orphan/预留和核对依据；先离线核对余额是否可证明。历史差额、单次0费用和恢复token不能补丢失费用或清unknown。 | B08-04/B09-06/B10-04/05及真实调用G-06；核对可独立先行，无需IdP/告警。**账本核对不授权模型**；调用另需合法固定输入、上限和明确本次授权。 |
| E07 真实 IdP | 受支持 Keycloak 实际版本、issuer/client、精确 callback/back-channel/JWKS、bidops_roles 映射；IdP管理员可执行停用/角色变更；秘密由部署责任者受控配置。保留真实事件和系统拒绝时钟证据。 | B10-01/02/03/04/05；不能用签名本机 OIDC 传输替代。240秒快照上界与真实≤300秒目标分别验证。 |
| E08 HTTPS/目标机 | 明确实际部署目标及探测/演练范围、DNS/TLS/origin、CPU/RAM/磁盘/数据及附件总量、UTC时钟、单机单worker、运维责任者、备份介质/独立恢复目录、维护窗口。不得默认当前开发机即企业目标。 | B09-01/02/04/05/06/07，B10-01/02/03/05。恢复目标 RPO≤86400秒、RTO≤1800秒，RTO含授权核验至业务可用。 |
| E09 真实用户/材料 | ≥2不同实际独立操作者、受控账号与稳定主体摘要/角色矩阵；已授权企业画像/资质/材料的固定 VersionRef、许可/留存与来源事实；明确 owner/reviewer assignment + 全sources ACL，独立用户问题/复核记录。 | B10-01/02/03/04/05；真实材料、身份、权限不能由合成声明 true 证明。 |
| E10 企业告警 | 实际指定接收端和运维收件责任者、受控凭据配置、明确本次故障/恢复对外发送授权；故障→生成→发送→真实收件→恢复收件的关联记录与 UTC 时间。 | B09-05，B10-01/05。生成≤300秒；中断≤告警发送≤收件，故障前的消息不算本次告警。 |
| E11 后续更新周期产物 | 对应来源许可、受控执行环境及明确运行授权具备后，由执行者实际产出≥3轮/跨≥24小时、含一次中断恢复的版本/cursor/数量/错误/附件记录；不是要求资料者预先交“未来已经运行”的证明。 | B07-07；没有出现真实更正/附件的维度仍未验，成功页last_success不能证明整批完整。其余无关输入未齐不自动阻止已许可范围内的受控更新。 |
| E12 后续具名复核产物 | 可先明确复核/运维/发布责任者；待执行完成后形成精确代码/锁/计划/数据/提示/画像及71专项/8G最终证据和具名GO/NO-GO，不能预先索要“已通过”签字。E2与生产E3范围分别冻结。 | B10-06；硬项NOT_RUN/BLOCKED/FAIL不允许声称通过。工具READY/Agent技术复核不代签，不给合并/部署授权。 |

E03 六层为硬性不符、缺资质证明、证据冲突、资格更正、终止、附件依赖，各层独立计数，可有族重叠。实际质量底线仍为 P1 溯源100%、关键字段精确率≥95%且覆盖≥85%、候选/硬条件召回≥95%、明确判断错误≤5%、可判断覆盖≥85%、引用解析100%、语义支持≥98%；任何严重硬条件误放或未披露版本错误即 FAIL。必需指标零分母为 N/A + BLOCKED，不记 PASS；不能以综合分抵消硬项。

## 正式容量与恢复：工程FAIL独立保留

负载固定代码[`836ccbfad779c4b6e52dd1802dd0877989dbe774`](https://github.com/ZZZZihan/BidOps/commit/836ccbfad779c4b6e52dd1802dd0877989dbe774)。总4200次逻辑操作、稳态3600次；4次ReadError无HTTP响应，稳态错误率4/3600=0.1111%。返回HTTP响应的失败状态为0，不能写成整个运行零错误。非模型API P95 263ms、受理108ms；队列P95 480734ms/峰17，计算P95 31774ms、合成模型等待30008ms。84个负载任务到waiting_review；末点compute0/queue0，active business94含等待和准备，不是94计算。

**target2/configured1/observed1、目标重叠0，因此capacity_status=FAIL。** 20账号/10客户端、10k版本/50k包/200材料/1000附件，5min预热+30min稳态、2次逻辑操作/秒、78/18/2/2混合及零重试保持原档位；逻辑操作数不同于含控制/依赖的总HTTP数。良好API延迟、数据/输入完整性和模型模拟结果不能抵消计算并发失败。架构若调整须保留原失败并另做迁移/回退和原档位复测。

实际M2 Ultra/24逻辑CPU/64GiB，同机有其他测试和构建；冻结目标为4CPU/8GiB，真实目标measurement仍BLOCKED。worker RSS峰856440832bytes，CPU峰105.7%是进程口径；FunctionModel两段固定延迟仅为offline模拟。完整窗口含drain约43分38秒。

停止旧输入后的独立副本经生产服务读全50000候选、末100条及offset50000空页；导出37703148bytes，11403源文件闭包和DB前后hash不变。实际49995 excluded、5 needs_verification，不能称5万个合格项目；这也不是目标机HTTP延迟或模型质量验收。

本机恢复RPO 0.031178秒，最新权限未确认、私有业务未重开，业务RTO为null/BLOCKED。复制或安全检查耗时不能替代业务RTO。真实目标RPO≤86400秒、业务RTO≤1800秒、告警生成≤300秒和本次故障后发送/收件因果门槛保持。

正式安全投影只含统计及原件哈希，不公开原trace、Cookie、私有账号/任务标识、正文或DB：

- [safe-report.json](https://github.com/ZZZZihan/BidOps/blob/efd4becb8e0f39a22d3933ddd542f6acc6e695b1/reports/evidence/b09-20261023/formal-load/safe-report.json)
- [safe-trace-summary.json](https://github.com/ZZZZihan/BidOps/blob/efd4becb8e0f39a22d3933ddd542f6acc6e695b1/reports/evidence/b09-20261023/formal-load/safe-trace-summary.json)
- [http-summary.json](https://github.com/ZZZZihan/BidOps/blob/efd4becb8e0f39a22d3933ddd542f6acc6e695b1/reports/evidence/b09-20261023/formal-load/http-summary.json)
- [completion.json](https://github.com/ZZZZihan/BidOps/blob/efd4becb8e0f39a22d3933ddd542f6acc6e695b1/reports/evidence/b09-20261023/formal-load/completion.json)
- [source-provenance.json](https://github.com/ZZZZihan/BidOps/blob/efd4becb8e0f39a22d3933ddd542f6acc6e695b1/reports/evidence/b09-20261023/formal-load/source-provenance.json)
- [capacity-curves.png](https://github.com/ZZZZihan/BidOps/blob/efd4becb8e0f39a22d3933ddd542f6acc6e695b1/reports/evidence/b09-20261023/formal-load/capacity-curves.png)
- [capacity-curves.svg](https://github.com/ZZZZihan/BidOps/blob/efd4becb8e0f39a22d3933ddd542f6acc6e695b1/reports/evidence/b09-20261023/formal-load/capacity-curves.svg)
- [capacity-curves-data.json](https://github.com/ZZZZihan/BidOps/blob/efd4becb8e0f39a22d3933ddd542f6acc6e695b1/reports/evidence/b09-20261023/formal-load/capacity-curves-data.json)

独立只读重算核对全部4200条分母、稳态4错误、官方P95/并发区间、2619资源投影及35分钟桶，并实际看过曲线，均与正式report一致。分钟桶P95仅描述分布，不替代正式稳态P95。此复核没有新跑产品测试、来源或模型。

## 8项共同门槛

整条交接链5项工程PASS、3项BLOCKED；各分支历史8G记录不替代整条链状态。

| G-ID | 当前状态 | 范围与仍缺条件 |
| --- | --- | --- |
| G-01 | BLOCKED | 71专项完整：55C工程PASS、B02-06一项C BLOCKED、15B BLOCKED；原旧库缺口仍阻塞共同C合并门槛，不能登记齐便称通过。 |
| G-02 | PASS（工程） | 十个固定代码提交远端三个job均PASS；B10 88dee本机2133/602.81秒、远端2133/1042.83秒及152浏览器分列，不覆盖容量工程FAIL或真实业务缺口。 |
| G-03 | BLOCKED | 004～012工程迁移/重复/备份恢复及最终整合已验；E01真实旧六库/会话/引用迁移回退仍未验，不能称所有旧数据兼容。 |
| G-04 | PASS（工程） | 最终工程权限/CSRF/全来源闭包、分页/导出最后释放CAS、恢复隔离、公开脱敏与负向检查通过；不替代真实IdP或许可验收。 |
| G-05 | PASS（工程） | PAI2.59.0/Authlib1.9.1/DBOS3.3.0/pypdf6.21.2及ADR/hashed overlay、三锁和最终构建依据已按代码记录；主锁及18文件pin保留。 |
| G-06 | PASS（工程） | 本轮无真实采购/模型/外部消息，新增实际费用0；不重置累计¥10或释放unknown，账本仍缺，未来请求另需合法范围/费用核对及明确授权。 |
| G-07 | PASS（工程） | 历史失败、修复、71专项及新增AC、正式836容量FAIL、固定代码CI和本机证据分别留证；后续文档提交与被测代码SHA分列。 |
| G-08 | BLOCKED | 默认关闭、独立目录/双库/审核/费用保留、恢复隔离与最新授权未知拒绝已有工程证据；真实旧服务回退仍缺E01，不删表/覆盖历史换取回退成功。 |

## 历史失败与修复分别留证

- B09原836本机2005 PASS/1 FAIL／605.60秒、Linux2004 PASS/2 FAIL／1042.66秒；B10原b136本机2128 PASS/1 FAIL／619.67秒、Linux2127 PASS/2 FAIL／990.74秒，不能互换。012索引已存在而测试预期漏列，36ef只追加一行，原精确列表相等和重复迁移断言保留；独立单节点1 PASS／0.80秒，36ef完整2006 PASS／622.67秒、B10 a0cf完整2129 PASS／621.28秒为各自历史结果。
- 身份测试修复仅局部执行时钟；无sleep延迟180秒旧18 PASS/1 FAIL→新19 PASS，文件43 PASS／8.92秒。−301/+31拒绝、−300/+30接受及原400/状态不变/Cookie可用/receipt0保持；生产300秒/+30秒和锁文件未改。
- B06超时分类、浏览器预算及Chrome updater收尾干预、B07/B08跨平台PDF旧失败保留在各报告；后续自然退出与历史收尾干预分列，最终CI按表中固定新代码计。
- B10实现者123项／2.51秒、独立83项／1.70秒及末项1 PASS／0.29秒、另一阶段三文件122项／3.91秒是不同运行，不能相加或称独立123项。漏行、整行消失、全pipe丢失、空行+畸形ID和未升级B10合同反例保留。

## 复查入口与真实验收边界

仅对B10最终整合候选，按[UAT说明](https://github.com/ZZZZihan/BidOps/blob/88dee00a36adf64ec20bb2918ead48f5a388c520/docs/ENTERPRISE-UAT.md)和[企业运维说明](https://github.com/ZZZZihan/BidOps/blob/efd4becb8e0f39a22d3933ddd542f6acc6e695b1/docs/ENTERPRISE-OPERATIONS.md)安装主锁及两份hashed overlay，使用`uv run --frozen --no-sync`；重新`uv sync`后须重装overlay。较早PR按其自身文档/CI安装，不假定已有两份overlay。保持单host单worker、权限和费用屏障。

最终候选template/envcheck/bundle退出0/2/2：65上游、六AC全BLOCKED、INCOMPLETE/NO_GO、0network/0model，为预期拒绝的工程检查。工具最高为EVIDENCE_COMPLETE / READY_FOR_INDEPENDENT_REVIEW，业务仍NOT_VERIFIED_BY_TOOL；私有bundle与证据目录必须配对，哈希不能认证真实人、许可、IdP、费用或收件。

最低71合同ID（含六B10）不能因损坏或删除缩小；新增合法B01–B09须补证据，新增B10须显式升级六项schema。保留历史及unknown预留，恢复后重核最新身份、来源、任务和累计费用；不删表或覆盖历史适配旧镜像。

本轮项目真实模型/采购来源调用0，实际新增费用¥0；最新完整累计账本仍缺，不推断余额、不释放unknown、不授权新调用。独立技术复核与具名人工验收/发布批准分开。所有子Agent均请求GPT-6.1 Sol / ultra，实际provider运行配置未独立核验。
