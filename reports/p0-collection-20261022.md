# BidOps P0 六条真实公告采集补充验收

日期：2026-10-22。**实际采集 6 条，失败 0 条；入库 6 条，解析 15 个采购包。** 这是用户要求“先尝试采集”后的追加运行，取代“目前没有真实正文”的状态判断；此前零采集报告作为历史记录保留。

## 仓库与代码版本

- 远端：[ZZZZihan/BidOps](https://github.com/ZZZZihan/BidOps)，公开仓库，默认分支 main。
- 本次解析与回放代码 SHA：`015ce3cfd4d7140411c60633df0c2bc689b5e276`。
- [代码提交](https://github.com/ZZZZihan/BidOps/commit/015ce3cfd4d7140411c60633df0c2bc689b5e276)；本报告由后续文档提交补充，避免将报告自身尚未生成的 SHA 写成已测试版本。
- [GitHub Actions 39772022878](https://github.com/ZZZZihan/BidOps/actions/runs/39772022878)：该代码提交的后端和前端检查成功。

## 访问检查与采集方式

本轮严格限定 `data/seeds/seed_urls.csv` 的六个中国政府采购网公告正文 URL。普通匿名 HTTPS GET，声明 `BidOps-P0/0.1 (+https://github.com/ZZZZihan/BidOps)`，串行请求、起始间隔至少 5 秒、每条仅尝试一次、15 秒超时、2 MB 上限、不跟随重定向。正文请求从 11:29:29.899853 UTC 到 11:30:21.362321 UTC（北京时间 19:29–19:30）。没有读取登录入口、验证码、CA 证书下载或招标附件。

正文采样前，采集端复核 robots.txt 返回 404，官网首页返回 200；首页未找到使用条款/版权声明链接。404 表示未取得 robots 规则，不是观察到了 Disallow；[RFC 9309 §2.3.1.3](https://www.rfc-editor.org/rfc/rfc9309#section-2.3.1.3) 区分了该情形和服务/网络不可达。robots 状态不能替代使用条款或再分发许可。

网页检索工具曾对官网首页报告 403；本次正文采样沿用此前已正常返回 200 的普通 HTTP 客户端及同一项目 UA，未变换 UA、配置替代代理或处理任何挑战。六次正文请求实际均为 200。工具的检索失败不计作公告采集失败，也不据此宣称该网站全面禁止访问。

这次是用户发起的六个已知 URL 的有限访问/正文采样，使用独立的 httpx 读取并将已验证响应交给 `Store.ingest`，**不是内置 `bidops fetch` 已获批准运行的结果**。内置采集入口仍默认关闭，来源登记中的 robots/terms 权限仍为 unknown；本次可访问性结果不代表持续自动采集、扩充 URL 或再分发获准。公告元数据如实记录 `access_method=http_fetch`、`http_status=200`，未伪称人工提供文件。

HTTP 状态、响应类型、UTC 时间、字节数、SHA256、标题和访问检查见 [采集响应记录](p0-collection-http-20261022.json)。原始响应正文只保存在私有样本包与本地忽略目录，未推送到公开仓库。

## 实际样本

六条均为 UTF-8 HTML、公开招标公告；未检测到挑战/错误页关键词，并以正文项目编号、名称、公告类型进行结构核验。预算为人民币元；截止时间为北京时间。

| 项目编号 / 原文 | HTTP | 字节数 | 预算（元） | 投标截止时间（UTC+8） | 采购包数 |
|---|---:|---:|---:|---|---:|
| [TGPC-2026-A-0384](https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261016_27510682.htm) | 200 | 20,676 | 1120000.00 | 2026-11-06 08:30:00 | 1 |
| [TGPC-2026-D-1131](https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261019_27520096.htm) | 200 | 18,048 | 1049700.00 | 2026-11-09 08:30:00 | 1 |
| [TJBH-2026-D-0170](https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261019_27520100.htm) | 200 | 22,892 | 2890000.00 | 2026-11-20 08:30:00 | 1 |
| [TGPC-2026-A-0343](https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261016_27510616.htm) | 200 | 18,032 | 170000.00 | 2026-11-06 08:30:00 | 1 |
| [PYGP-2026-A-0826](https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261018_27513837.htm) | 200 | 56,284 | 10089800.00 | 2026-11-09 09:00:00 | 9 |
| [TGPC-2026-A-0324](https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202609/t20260918_27358274.htm) | 200 | 20,620 | 29000000.00 | 2026-10-10 08:30:00 | 2 |

合计 156,552 字节、6 个不同内容哈希、6 条数据库快照。实际公告 HTTP 请求 6 次、HTTP 失败 0 次、重试 0 次、重复采集 0 条、未请求 0 条、附件 0 份。元数据检查的 robots 404 单独记录，不伪装成一条公告的失败或成功。

六条均提取出项目编号、名称、采购单位、预算/原单位、公告日期、投标截止时间、公告类型和中小企业相关原文；这是**字段覆盖观察，不是准确率实验**。保留了采购单位在原文中的完整名称，没有使用 seed CSV 的简称补齐字段。文件获取时间在原文中表现为日期范围和营业时段，当前规则未确定其截止瞬间，六条 `document_deadline_at` 仍为 null。中小企业条件只保存引用，不生成资格合格结论。

`TGPC-2026-A-0324` 的截止日期早于采集日期，作为历史公告保留；不会因刚采集就标记为当前开放。没有采集更正/终止类别，因此这两类的真实样本验证仍未完成。

## P0 修复与架构

本次只修复现有确定性解析器：区分外层排版表格和内层采购包表格；支持“08点30分”和章节下的投标截止时间；合并数值相同的不同预算写法及同日日期/时间；识别显式预算单位列；避免把资格段落中提及“第9包”误作新采购包。解析器版本为 p0.2，证据规范化版本为 normalized_text_v2；旧 p0.1 清单仍可导入并重新解析。

仍使用 Python 3.12、uv、FastAPI、Pydantic、SQLite 编号迁移及 React/TypeScript/Vite。原始字节按 SHA256 保存，URL+内容哈希定义快照，解析结果和证据进入 SQLite，列表/详情 API 供前端读取。没有新增模型调用、训练、RAG、Agent、Harness、登录系统或云服务。

## 实测验证

| 命令 / 检查 | 实际结果 |
|---|---|
| `uv run python scripts/audit_sources.py --execute-metadata-audit --source-id ccgp --output data/artifacts/collection-20261022/access-audit` | 退出 0；robots 404、首页 200，无可见条款链接 |
| 本轮会话中的六 URL 普通 httpx GET，随后 `Store.ingest` | 6 次 200；6 份原始正文；6 条新增快照；来源方式为 http_fetch |
| `uv run pytest -q` | 79 passed，1 个 Starlette/httpx 弃用警告 |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 43 files already formatted |
| `npm run build --prefix frontend` | TypeScript 检查、Vite 构建通过 |
| `uv run bidops export-manifest --output data/artifacts/p0-capture-20261022 --include-raw` | 导出 6 条，包含原始字节 |
| `uv run python scripts/verify_collection.py --bundle data/artifacts/p0-capture-20261022 --output reports/p0-collection-verification-20261022.json` | 退出 0；见下述回放检查 |
| GitHub Actions / 代码提交 015ce3c | 全新 CI checkout 的后端检查和前端构建成功 |

回放在临时空数据库中执行：首次导入新增 6 条；第二次新增 0 条、重复 6 条；两次导出的 manifest 完全相同；90 个字段/资格/采购包证据区间与规范化原文一致。TestClient 应用内检查通过 `/health`、`/sources`、6 条列表/详情及不存在 ID 的 404；测试期间网络保持关闭。未将此检查写成浏览器视觉验收或独立 gold 标签评估。

清单 SHA256：`1f9578666a4297ee75b181c0b629d123516feb757bc4902f67ea89059e57cf54`。

完整回放指标见 [验证元数据](p0-collection-verification-20261022.json)。新增的公开测试均为自行编写的原子语法片段，不是虚构政府公告或展示数据。

## 数据交付与复现

私有样本包：`BidOps-P0-samples-20261022.zip`，含六份 raw HTML、manifest.json、notices.jsonl、访问记录与说明。该包已单独交付，不在公开 GitHub 中。干净 checkout 默认列表仍为空；解压到 `data/artifacts/` 后运行：

```bash
uv sync --frozen
uv run bidops import-bundle --input data/artifacts/p0-capture-20261022
uv run python scripts/verify_collection.py --bundle data/artifacts/p0-capture-20261022 --output data/artifacts/replay-check.json
uv run bidops stats
```

导入、解析和回放均可离线完成。内容来自当次实际响应，未来再次访问同一 URL 不保证字节相同，新的字节应形成新快照。

## 仍待处理与 P1 结论

- “六条真实正文未获取、真实解析无从验证”的阻塞已解除；P0-06 可关闭。
- P0-02 保留开放：仍需人工确认常规采集入口的来源使用边界和后续采样范围。公开网页可访问与持续抓取/再分发许可分别记录。
- 文件获取截止时间、附件及复杂资格结论尚未处理；任意表格布局、真实更正/终止样本也没有得到本轮验证。
- 这六条不是 P1 的独立 gold 集；30 条、多类别、字段准确率评估是后续 P1 工作，不能写成本轮已完成指标，也不将其误作必须在 P0 偷跑完成的工作。
- 比赛资格/报名仍待用户与主办方确认，与本次公告采样是否成功分开处理。

**建议：提交 P0 人工验收；人工确认访问边界并批准后可以启动 P1。本次停止在 P0，没有自动进入 P1、Harness 或正式比赛申报。**
