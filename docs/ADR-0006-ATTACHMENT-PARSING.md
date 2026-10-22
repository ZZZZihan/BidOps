# ADR-0006：受限附件解析与固定 PDF 页证据

日期：2026-10-23。状态：B07 工程实现；无真实采购 PDF 验收，无 OCR 能力声明。

采购附件需要可追溯的固定文字和页定位。仅记录 `parsed`，或在读取历史引用时重新调用新版本解析器，会使历史引用含义漂移。B07 使用 pypdf **6.21.2 core only** 在单独子进程中提取 PDF 文字，将实际文本和页映射作为不可变 receipt 保存。现有 HTML/plain 的 `AttachmentInput` 序列化、`attachment_record_v1` ID 和 `normalized_text_v2` 引用算法保持原样。

## 决定与调用边界

网络下载、来源许可、重试与持久任务归 ingestion/runtime；解析器不下载文件，不运行模型。先在数据库事务外调用：

```python
prepared = prepare_attachment(
    request,  # AttachmentInput：downloaded + pending
    downloaded_bytes,
    expected_page_count=known_count,  # 只有来源已知时传入，不推测
    limits=ParserLimits(),
)
with store.db.engine.begin() as conn:
    record = store.notice_data.record_attachment(prepared, conn=conn)
```

仓储 `record_attachment` 保留旧 `(AttachmentInput, data, *, conn=None)` 调用方式；接收 `PreparedAttachment` 时只做哈希、形状、状态、页映射和持久化校验，不再运行 PDF 解析。失败同样可以保存为 `failed`、`encrypted` 或 `scan_unrecognized`，并保留安全错误码。异常与 receipt 不包含解析器原始异常、密码、文件路径或文件正文诊断。receipt 本身不重复保存正文，提取文本固定保存在原附件表的 `normalized_text` 列。

`011_attachment_receipts.sql` 增加 `nd_attachment_receipts(record_id, receipt_sha256, receipt_json)`，与附件同一业务事务提交。该表禁止更新和删除，迁移通过既有 migration ledger 幂等执行。后续 reparse 使用新的观察时间及新的 receipt/record，不修改旧行。解析完成但事务回滚可能留下按 SHA 寻址的孤立 raw blob；不会产生可见附件行或 receipt，既有 blob/backup 枚举只从数据库引用取闭包。

## 身份与引用

PDF 的 `attachment_record_v2` ID 绑定逻辑附件 ID、原输入元数据、原始 bytes SHA、固定文本 SHA 和 receipt SHA。receipt 固定 engine/version、adapter version、严格读取/提取选项 SHA、解析限额、实际结果状态、原始与文本 SHA、完整 page-map SHA、实际页数及来源已知的预期页数。服务端同时核验连续页号、全局 offset 覆盖、逐页文本 SHA 和全文拼接规则。

每个非空页先使用与 `normalized_text_v2` 相同的纯文本规则规范化，页间仅插入一个换行。空页/失败页保留零长度 span 和独立状态，不把下一页 offset 前移或伪装成功。`parsed` 仅在所有物理页均得到非空文字且没有页失败时出现；它是提取状态，不是条件识别或语义完整性结论。已知预期页数不一致直接失败。没有可信预期页数时，解析器不能识别“原文件已缺失一个物理页”。

B01 `EvidenceRef` 保持 `locator=normalized_text_v2`、全文 `start/end`、`page=None`。`NoticeRepository.evidence_location(evidence)` 先完整解析并核验原引用，再返回：

```json
{
  "evidence": "原 EvidenceRef 对象",
  "location": {
    "kind": "pdf_pages",
    "pages": [1, 2],
    "parser_receipt_sha256": "固定 receipt SHA"
  }
}
```

页号只由服务端固定 page-map 推导，不相信调用方页码。旧 HTML/plain 返回原全文 offset 定位。旧 PDF 记录不会用当前安装的 pypdf 重新提取；读取只核验固定字节、固定文本和 receipt。未成功解析、正文/receipt 丢失或哈希错误时不得提供证据，也不得把缺材料清单当作完整。

## 运行边界与资源

默认上限是原始 10,000,000 bytes、200 页、单个解码流 8,000,000 bytes、每页 100,000 字符、全文 500,000 字符、20 秒 wall time、256 MiB 子进程 RSS。服务端可收紧或在 schema 限定范围内配置；不是调用方随意扩大上限的授权。上限进入 receipt，后续改变配置不会重解释旧记录。

解析使用全新 `python -I` 进程，只导入 stdlib 和固定 pypdf。父进程创建 0700 临时目录与 0600 输入/输出文件，子进程禁用 Python socket 外连与解析器日志。跨线程 lock 加按 OS 用户共享的 `flock` 将并发限制为 **1**，繁忙返回 `pdf_parser_busy`，不排无限队列。进程被杀时 flock 由 OS 释放。此限制针对单机同一 OS 用户；多用户/多节点不是本实现的全局容量控制。

pypdf 6.21.2 的 `overwrite_configuration` 在读取 PDF 之前设置单流 zlib/LZW/run-length/Brotli/JBIG2、数组拼接和图片缓冲上限，解码工作预算为单流上限的 4 倍；声明长度、页树和数组/字典元素数亦受限，不启用外部 jbig2dec。其 zlib 实现以 `decompress(..., max_length=...)` 在分配输出期间限制长度。真实压缩 PDF 夹具将 16 MiB 内容压到 100 KiB 以下，默认 8,000,000-byte 解码上限使该页返回明确 `pdf_decode_limit`，不等到全文字符计数才拒绝。

父进程监测 wall time 和实际 RSS，超时或超限 kill 并 wait 回收；RSS 监测不可用时 fail closed。子进程设置 CPU 与输出文件大小限制，累计全文和逐页输出有独立上限。不会等到无限制 `get_data()` 完成后才检查大小：RSS watchdog 在 pypdf 读取/解压/提取期间持续执行。Linux 另使用 `RLIMIT_AS` 地址空间限制。

**macOS 实测：** 本机使用子进程先设置 96 MiB `RLIMIT_AS` 再实际分配 160 MiB 的探针，结果为 `unavailable`（系统拒绝该限制）；因此 macOS receipt 明确记录 `rss_watchdog`，不声称地址空间硬上限。实际测试已用独立子进程分配 96 MiB 并在 32 MiB RSS 门槛下被父进程停止，也验证真实 sleep worker 在 wall time 到达后停止。RSS 轮询约 20 ms，加 OS `ps` 查询耗时，存在短时超额与单次查询最多 1 秒的响应余量；它不是内核内存隔离或容器沙箱，不能保证零瞬时超额。子进程隔离也不等同于对解析库漏洞的完整 OS sandbox。生产处理不可信大文件时，宜进一步以受限容器/系统级内存控制部署。

pypdf 可能在提取单页文字之前分配较大的解压内容。本实现用全程资源监控保护主服务，不把字符上限包装成分配前的硬保证。CPU/内存异常、无输出或子进程异常退出均返回固定失败码，不返回库异常文本。

## 不支持与验收边界

- 加密文件一律拒绝，不尝试空密码、解密或安装 crypto extras。
- 不运行 OCR。扫描/图片页、空白页均只能标记“没有识别出文字”；不能仅凭空输出判断真实空白还是扫描文件。一个含少量文字的页仍可能有未识别图像文字，`parsed` 不保证这些图像内容已覆盖。
- 不保证表格阅读顺序、坐标布局或采购条件语义。全文分段抽取和完整条件核对仍由既有资格筛选流程处理，资料不完整时必须继续暴露未知。
- 本次 PDF 均由测试程序生成，包括中文 ToUnicode、跨页、图片页、加密、损坏和不支持解码过滤器。它们是本地工程夹具，不是实际业务 PDF 样本，也不构成业务准确率或成熟来源接入验收。

## 依赖与资料

独立 `requirements/attachments.lock` 锁定 `pypdf==6.21.2`，同时提供官方 wheel 和 sdist SHA256；Python 3.12 的 core 无必须安装的额外依赖。使用清华镜像进行本次项目虚拟环境的单次 `uv pip install --require-hashes`，未改主 `pyproject.toml`/`uv.lock`，未安装 OCR/crypto extras。构建、CI、版本 provenance 与备份闭包需同时纳入该 overlay lock 和 receipt 表。

依据是官方 [PyPI pypdf 6.21.2 JSON](https://pypi.org/pypi/pypdf/6.21.2/json) 及 [pypdf text extraction 文档](https://pypdf.readthedocs.io/en/6.21.2/user/extract-text.html)，已保存的原始资料由本任务复用。官方文档明确提醒解压内容可能造成高内存占用，pypdf 不做 OCR，文字顺序/布局存在固有限制。本 ADR 不把官方能力说明等同于实际采购文件验证。
