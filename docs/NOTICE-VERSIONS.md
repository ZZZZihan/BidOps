# 公告版本与证据读取（B02）

本模块扩展已导入的公告档案，默认不联网、不调用模型、不证明业务资格。
`notices` 保留现有 Notice 兼容投影；`nd_versions` 保存不可变解释版本，历史会话原始 JSON 不重写。
B01 冻结契约、输入夹具、评分实现和 pin 保持原样。

## 标识与版本

- `snapshot_id` 继续由规范来源 URL 与原文字节 SHA256 决定；重复导入不新增快照。
- 同一 URL 是一个逻辑公告；URL 下不同原文快照保留独立版本。项目只按来源 authority、明确项目编号及采购单位关联；缺关键字段的记录为 `pending_review`，不按相似标题猜测。
- 采购包 ID 绑定项目与规范化包标识；包的文字与范围证据仍绑定具体公告解释版本。
- 解释版本绑定快照、原文 SHA256、规范化文本 SHA256、规范化器、解析器和解析结果哈希。同一 parser 标签下结果变化也产生新版本。
- 独立 observation 保存来源观察时间，重复抓取可以增加观察记录；重新解析不刷新来源时间。`available_at` 保存解释首次可用时间，旧 `as_of` 不能看到后来才产生的解释。
- 版本、包、观察和附件记录有数据库不可修改／删除触发器。兼容投影可通过 `bidops parse` 更新，新版本和旧引用仍保留。

## 引用和附件

`EvidenceRef` 复用 B01 合同。公开引用必须同时通过版本身份、原文哈希、规范化文本哈希、定位器、精确区间与逐字原文校验。验证发生在服务端，不能凭前端提供的 SHA 或句子宣称通过。

当前实际支持 UTF-8／GB18030 的纯文本与 HTML，定位器为 `normalized_text_v2`；没有页码。
旧 v1 定位不自动改标签：若无法用实际旧规范化方式重放，返回明确错误，保留旧记录供迁移处理。
PDF、Office 与 OCR 尚未接入；加密／扫描未识别只能作为带 `imported_metadata` 来源的外部诊断，不能声称本解析器已检测或解析。

每份附件分别记录 acquisition (`not_downloaded/downloaded/failed`) 与 parse
(`pending/parsed/failed/encrypted/scan_unrecognized/unsupported`) 状态、时间、错误码及哈希。
更新形成新记录，同时间互相矛盾的状态拒绝写入。附件字节保存在独立的 `attachments/<sha256>`，不混入企业私有材料。
读取时另查当前文件完整性；历史 `parse_status=parsed` 不代表文件仍存在。
缺失或损坏进入 `missing_attachments` 与 `integrity_issues`，不修改历史解析记录，也不能继续判开放。

## 查询与状态

`Catalog` 不再预载最多 500 条。SQL 对完整表做过滤、计数和排序，再返回请求页。
金额以 Decimal 字符串比较，避免 SQLite REAL 舍入；排序以 notice ID 打破并列，未知值排后。
现阶段筛选和排序会扫描表，并保留全库来源完整性核验；容量与性能上界需 B09 实测，不能把 507 条工程回归当成生产容量结果。

所有分页返回 `total/offset/limit/has_more/truncated`；`truncated` 表示当前返回页不是完整匹配集。
稳定分页针对不变的数据库集合；并发插入会改变后续 offset 页，调用者若需要固定批次应先冻结输入 manifest，不能把多次动态查询当成同一快照。
同一请求的计数与页内容使用 SQLite 读事务。模型工具的调用数仍受原预算限制，未读完必须保留截断信息。
读取同一项目时按全量 `total > 1` 强制复核，末页只有一条也不能绕过；搜索与读取界面均显示范围、总数与截断。

状态按 `as_of` 只使用当时可见的信息。已知终止优先；来源、关联、附件或新鲜度不足返回 `unknown`。
未来截止时间本身不足以证明开放。只有明确完整的来源、关联和时间证据满足要求时才可能返回 `open`；当前 Store 默认 `source_complete=False`，不会自动给手工片段背书。
新鲜度逐个检查当前关联来源，以最旧的必需观察为边界；原页刚刷新不能替过期的更正页续期。
`source_observations` 保留每页依据；已知终止仍优先显示，同时披露证据陈旧。

## API

以下路径同时具有 `/api` 前缀别名；企业模式使用 `catalog:read`，POST 验证接口也执行原有 CSRF 检查。

| 路径 | 行为 |
| --- | --- |
| `GET /notices?q=&limit=&offset=&sort_by=&descending=` | 全库查询；排序支持 project_code/budget/deadline/publication |
| `GET /notices/{snapshot_id}/versions` | 同 URL 逻辑公告的版本链，分页返回 |
| `GET /notice-versions/{version_id}` | 固定版本与包标识 |
| `GET /projects/{project_id}/versions?as_of=` | 当时可见的关联版本 |
| `GET /projects/{project_id}/status?as_of=` | 状态、依据、来源观察与新鲜度；as_of 必须带时区 |
| `GET /notice-versions/{version_id}/evidence?start=&end=` | 复验并生成公告引用 |
| `POST /notice-evidence/resolve` | 复验完整 EvidenceRef，失败不回显原文或文件路径 |
| `GET /notice-versions/{version_id}/attachments` | 附件状态历史与当前缺失清单 |
| `GET /notice-attachments/{record_id}/evidence?start=&end=` | 复验已支持的附件引用 |

这些入口只读；附件导入接口为受控 Python `NoticeRepository.record_attachment`，后续 B07 承担授权采集接线。
原有 Agent 仍是公告问答入口，逐条资格核对由 B04 实现。

## 升级、备份与回退

1. 升级前保存旧代码 SHA，并用原有 `bidops-enterprise backup --output <新目录>` 保存数据库和所有 raw；保留原备份。
2. 新代码 `bidops migrate`／Store 启动执行新增 004，再验证旧 raw 和原有 payload，事务化回填。坏哈希或不支持的历史定位器会停止回填，不能覆盖旧数据重试。
3. 新备份把已存附件加入数据库引用闭包；恢复先核对完整清单及哈希，再创建目标目录。旧格式纯公告备份继续支持。
4. 回退验证在新隔离目录恢复升级前备份，并使用旧代码读取。真实业务回退前先另存升级后的完整库与附件，避免丢掉升级后新增的审核／引用。
5. 不执行破坏性反向迁移，不删除已形成的版本历史。前置 B01 和计划 PR 均未自动合并。

工程用例覆盖六条合成旧记录、历史会话、重复迁移、附件恢复及回退读取；它们不等于历史六份真实公告备份验收。当前真实旧库／raw 未随克隆提供，B02-06 的指定真实输入部分保持 BLOCKED。
