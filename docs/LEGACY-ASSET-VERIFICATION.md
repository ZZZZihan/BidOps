# 真实旧资产与费用账本独立核验

本入口支持既有 **B02-06 / E06**，不新增 AC-ID，不改变 73 项验收矩阵。
它把“资产在哪里、是否完整、接下来怎样在副本核验”变成可执行步骤。
**结构预检成功不能关闭 B02-06、G-01/G-03/G-08 或 E06。**

脚本只使用 Python 标准库，不导入 BidOps 应用，不启动模型、采集或工作流。
实际原件、数据库、会话、费用报告留在受控环境；公开提交只接受显式白名单投影。
本页命令是收到真实输入后的操作说明；本次没有对真实实例停机、迁移或恢复。

## 1. 已定位的资产与应查路径

| 资产 | 确切线索 | 能证明什么 |
|---|---|---|
| 原采集 ZIP | BidOps-P0-samples-20261022.zip | 历史报告记载单独交付；不在公开 Git |
| 导入包 | data/artifacts/p0-capture-20261022/ | manifest、六份 raw、解析导出与采集说明 |
| 默认源库 | data/processed/bidops.sqlite3、data/raw/ | 原环境未覆盖配置时的路径 |
| **真实三轮历史库** | data/artifacts/enterprise-live-20261022/restored/bidops.sqlite3 | 原集成脚本实际写入问答的副本 |
| 历史配套文件 | 上述 restored/raw/、外层 responses.json | 原文与私有响应证据 |
| 运行前备份 | data/artifacts/enterprise-live-20261022/backup/ | 三轮运行之前的库与 raw；未必有三轮历史 |

旧脚本只把 agent_runs 从 restored 库合并回源库，**没有合并会话表**。
因此默认源库或运行前备份不能自动代替真实历史库。
见[固定旧脚本 L22–54](https://github.com/ZZZZihan/BidOps/blob/6a41a067b08f67b5d9c46bd68f5b3acc0101bf80/scripts/enterprise_live.py#L22-L54)及
[L129–145](https://github.com/ZZZZihan/BidOps/blob/6a41a067b08f67b5d9c46bd68f5b3acc0101bf80/scripts/enterprise_live.py#L129-L145)。

原 ZIP 被记载含六份 HTML、manifest、notices.jsonl、访问说明，
没有证据说明它含 SQLite、持久会话或最新完整账本。
最初采集附件为 **0**；后续旧库若有附件引用，按实际引用补齐，不能补造附件。
原件清单依据：[采集报告](../reports/p0-collection-20261022.md)、
[逐样本指纹](../reports/p0-collection-verification-20261022.json)。

预期 manifest SHA256：

~~~text
1f9578666a4297ee75b181c0b629d123516feb757bc4902f67ea89059e57cf54
~~~

六 raw 合计 156,552 bytes、六个不同哈希、15 个采购包。
完整预期身份保存在 [expected JSON](../data/seeds/p0-capture-20261022.expected.json)；
它是核对依据，不是已经交付的原文。没有公开的 ZIP 整体哈希，不能伪称 ZIP 已验。

## 2. 旧 schema 与代码归属

| 版本线索 | 结构 / 用途 |
|---|---|
| 015ce3cfd4d7140411c60633df0c2bc689b5e276 | 真实采集回放；001 基础公告表 |
| 2060d0e3da8d85465659e2d3fa09719a3acca714 | Demo；002 agent_runs，当时 Demo 会话为内存态 |
| 6a41a067b08f67b5d9c46bd68f5b3acc0101bf80 | 真实三轮集成对应代码；003 企业持久会话 |
| cfcd7c8a331f26952ac6ad0493d92c1bd623d9d0 | 本次查到的 main；相对 6a 只有两份文档提交 |
| 069e4b495828caca4c64447062c030a0f313aada | B02 前 B01 基线；仍为 001–003 |
| 0ff9413cff5e3cb13b85697e5a0c9181e4b86658 | 本轮固定目标；含 004–012 |

003 的业务历史是 enterprise_sessions / enterprise_turns，
重点为 owner/session/request 关联和 response_json / messages_json。
登录、OAuth state 与凭据不是待迁移的业务历史。

schema_migrations 不能单独证明某个 Git SHA。
保管方应提供实际旧运行 SHA、未提交修改情况、锁文件、Python 版本和配置覆盖关系。
只有部署证据匹配 069 时才选 069 回退；不可因为 schema 相同就替换历史归属。
[6a→main 比较](https://github.com/ZZZZihan/BidOps/compare/6a41a067b08f67b5d9c46bd68f5b3acc0101bf80...cfcd7c8a331f26952ac6ad0493d92c1bd623d9d0)。

## 3. 在原环境只读寻找

只填已知原项目目录；命令只列文件名，不读取 .env 或输出密钥：

~~~bash
bidops_original_root='/controlled/original/BidOps'
rg --files --hidden --no-ignore-git "$bidops_original_root" \
  -g '**/BidOps-P0-samples-20261022.zip' \
  -g '**/data/artifacts/p0-capture-20261022/**' \
  -g '**/data/artifacts/enterprise-live-20261022/**' \
  -g '**/data/processed/bidops.sqlite3*' -g '**/data/raw/**' \
  -g '**/data/attachments/**' \
  -g '!**/.env' -g '!**/.env.*' -g '!**/private/*.env' -g '!**/.git/**'
git -C "$bidops_original_root" rev-parse HEAD
git -C "$bidops_original_root" status --porcelain --untracked-files=no
~~~

若 ZIP 在已知交付目录，只对该目录查精确文件名。未找到不证明原件从未存在。
当前 HEAD 只证明当前检出状态，不能代替历史运行记录。

**原实例上禁止用 Store、bidops stats/migrate/parse 做“只读看看”。**
这些入口会构造 Store，自动执行迁移和 legacy 回填；
parse 还会重新解析兼容 payload。也不运行 enterprise_live.py 来重建缺失历史。

## 4. 一致快照由资产保管方提供

由保管方在获准停写窗口停止所有写入者，记录操作者、UTC 截止、源路径与代码。
同一停写时段保存主库、存在的 -wal/-shm/-journal、匹配 raw/附件及真实引用文件。
不要只复制活动 SQLite 主文件，不删除 WAL，不对原库执行 checkpoint 或 VACUUM。

封存副本应有清单、大小和 SHA256；与运行配置分开保存，不公开凭据。
有持久任务的较新库还必须提供配套 private/jobs/dbos.sqlite3 和 v4 关联闭包，
不能将下面的单库示例用于证明完整双库恢复。

预检拒绝非空 WAL/journal。若封存副本有 WAL，将主库及 sidecars **再复制一份**
到可写临时目录，仅在这个第二副本执行 SQLite backup API：

~~~bash
python3 - '/controlled/second-copy/bidops.sqlite3' \
  '/controlled/consistent-copy/bidops.sqlite3' <<'PY'
from contextlib import closing
from pathlib import Path
import sqlite3, sys
source, target = map(Path, sys.argv[1:])
assert source.is_file() and not source.is_symlink()
assert not target.exists(), "Target must be new"
target.parent.mkdir(parents=True, exist_ok=True)
with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
    with closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
        assert dst.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert dst.execute("PRAGMA foreign_key_check").fetchall() == []
        dst.commit()
PY
~~~

同名 sidecars 必须与源副本配套；失败则保留证据，不用 immutable=1 跳过 WAL。
将匹配 raw 等文件放入一致副本数据根。封存原件不参与应用启动。

## 5. 三个独立预检入口

从本 PR checkout 根目录运行，报告输出目录须为新建受控位置：

~~~bash
bidops_tool_root="$PWD"
bidops_reports='/controlled/new-audit-reports'
mkdir -m 700 "$bidops_reports"
python3 scripts/legacy_preflight.py \
  --capture '/controlled/p0-capture-20261022' \
  > "$bidops_reports/capture.json"

python3 scripts/legacy_preflight.py \
  --database '/controlled/consistent-copy/bidops.sqlite3' \
  --data-dir '/controlled/consistent-copy' \
  --snapshot-confirmed --min-history 3 \
  > "$bidops_reports/legacy.json"

python3 scripts/legacy_preflight.py \
  --database '/controlled/latest-ledger-copy/bidops.sqlite3' \
  --snapshot-confirmed --ledger-only \
  > "$bidops_reports/ledger.json"
~~~

--min-history 3 对应公开三轮集成线索；另一真实旧实例应预登记实际数量与解释。
--snapshot-confirmed 是操作者的一致副本声明，不是脚本替其完成停写或来源认证。
费用入口不依赖六公告、raw、会话或 --data-dir，可以先于 E01 独立执行。

退出码：0 为有限预检完成，1 为检查失败，2 为缺输入。
成功状态为 CHECKS_OK_NOT_ACCEPTANCE；
b02_06_status / e06_status 始终是 NOT_EVALUATED。
输出仅使用已知表名/状态，未知状态聚合为 unknown；
文件路径、哈希、数量和费用仍可能属于私有元数据，**真实运行 JSON 不直接提交 GitHub**。
expected_reference_sha256 记录实际预期文件的 SHA256，须与固定提交的默认文件比对；
自定义 --expected 即使结构通过，也不证明它对应原六公告。
全库 integrity/foreign_key 检查保留；账本模式只读取 agent_runs 内容，
不读取 schema_migrations 列、历史、raw 或 DBOS。完整模式只摘要固定白名单表，
其他表数量单列；未摘要表仍需资产保管方受控核验。

账本输出只是给定快照的已完成估算、非完成预留和占用；
latest_complete_ledger_verified=false，remaining_budget_cny=null。
最新截止、跨运行费用、供应商对账及 unknown/orphan 仍须另核验。
不能设置 verified/token 来替代对账，也不由此授权真实模型调用。

## 6. 在副本迁移与另一目录回退

以下示例仅适用于确认的旧 001–003 实例。更多状态按
[运行文档](ENTERPRISE-OPERATIONS.md)和[持久任务恢复](DURABLE-RUNS.md)处理。
创建两个固定代码目录；旧 SHA 以下使用真实集成对应 6a，须按实际证据选择：

~~~bash
bidops_audit_root='/controlled/new-b02-work'
bidops_snapshot_data='/controlled/consistent-copy'
mkdir -m 700 "$bidops_audit_root"
git clone --no-hardlinks --no-checkout "$bidops_original_root" "$bidops_audit_root/old-code"
git -C "$bidops_audit_root/old-code" checkout --detach 6a41a067b08f67b5d9c46bd68f5b3acc0101bf80
git clone --no-hardlinks --no-checkout "$bidops_original_root" "$bidops_audit_root/new-code"
git -C "$bidops_audit_root/new-code" checkout --detach 0ff9413cff5e3cb13b85697e5a0c9181e4b86658
cd "$bidops_audit_root/old-code"
uv sync --frozen --python 3.12
cd "$bidops_audit_root/new-code"
uv sync --frozen --python 3.12
uv pip install --python .venv/bin/python --require-hashes \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  -r requirements/durable.lock -r requirements/attachments.lock
~~~

缺 Git 对象时只读取回固定提交，不以移动 branch 替代。
使用独立无凭据 shell，不复制 private/*.env；不要输出环境变量全集：

~~~bash
unset DEEPSEEK_API_KEY BIDOPS_SCREENING_LEDGER_RESTORE_TOKEN BIDOPS_DEMO_BUDGET_RESTORE_TOKEN
export BIDOPS_NETWORK_ENABLED=false BIDOPS_DEMO_ENABLED=false
export BIDOPS_ENTERPRISE_ENABLED=false BIDOPS_SCREENING_ENABLED=false
export BIDOPS_JOBS_ENABLED=false BIDOPS_INGESTION_ENABLED=false
export BIDOPS_OPERATIONS_ENABLED=false BIDOPS_SCREENING_LEDGER_VERIFIED=false
cd "$bidops_audit_root/old-code"
export BIDOPS_DATA_DIR="$bidops_snapshot_data"
export BIDOPS_DATABASE_URL="sqlite:///$bidops_snapshot_data/bidops.sqlite3"
.venv/bin/bidops-enterprise --env-file /dev/null backup \
  --output "$bidops_audit_root/preupgrade-backup"
cp -a "$bidops_snapshot_data" "$bidops_audit_root/migration-copy"
cd "$bidops_audit_root/new-code"
export BIDOPS_DATA_DIR="$bidops_audit_root/migration-copy"
export BIDOPS_DATABASE_URL="sqlite:///$bidops_audit_root/migration-copy/bidops.sqlite3"
.venv/bin/bidops migrate > "$bidops_reports/migrate-1.json"
python3 "$bidops_tool_root/scripts/legacy_preflight.py" \
  --database "$BIDOPS_DATA_DIR/bidops.sqlite3" --data-dir "$BIDOPS_DATA_DIR" \
  --snapshot-confirmed --min-history 3 > "$bidops_reports/after-1.json"
.venv/bin/bidops migrate > "$bidops_reports/migrate-2.json"
python3 "$bidops_tool_root/scripts/legacy_preflight.py" \
  --database "$BIDOPS_DATA_DIR/bidops.sqlite3" --data-dir "$BIDOPS_DATA_DIR" \
  --snapshot-confirmed --min-history 3 > "$bidops_reports/after-2.json"
~~~

不要执行 parse。比较原表数量和逻辑哈希、六原始 snapshot 与包的身份；
首迁移新增版本应可追溯，二次迁移不重复。DDL 会改变 SQLite 文件页，
不能只用整库文件 SHA 判定两次迁移是否幂等。

用旧代码将升级前完整备份恢复到**第二个不存在的目录**，保留 migration-copy：

~~~bash
cd "$bidops_audit_root/old-code"
.venv/bin/bidops-enterprise --env-file /dev/null restore \
  --input "$bidops_audit_root/preupgrade-backup" \
  --output "$bidops_audit_root/rollback-copy"
export BIDOPS_DATA_DIR="$bidops_audit_root/rollback-copy"
export BIDOPS_DATABASE_URL="sqlite:///$bidops_audit_root/rollback-copy/bidops.sqlite3"
.venv/bin/bidops stats > "$bidops_reports/rollback-stats.json"
python3 "$bidops_tool_root/scripts/legacy_preflight.py" \
  --database "$BIDOPS_DATA_DIR/bidops.sqlite3" --data-dir "$BIDOPS_DATA_DIR" \
  --snapshot-confirmed --min-history 3 > "$bidops_reports/rollback.json"
~~~

旧 restore 撤销复制库登录/OAuth state 是预期行为；业务历史与费用应保留。
旧回退副本应没有 004–012，不能把新库直接交旧代码或删表来换取启动成功。
任何费用确认屏障都保持待核验，不释放 unknown 或预留。

## 7. 真正关闭 B02-06 还需什么

- 原封存文件前后哈希不变；实际旧运行代码与输入归属可核查。
- 旧公告、raw、历史会话/响应/框架消息数量与逻辑内容保存，引用无悬空。
- 使用原生 ChatResponse / ModelMessagesTypeAdapter 读取真实历史；
  按实际旧定位器重算规范化文本、逐条核验 quote 与区间，不能重标旧定位器。
- 两次迁移幂等；在第二目录以匹配旧代码读取真实公告和历史。
- 实际执行隔离本机旧服务 health/公告/历史 HTTP smoke，并保留版本及结果；
  仅实例化库或结构预检不能替代旧服务恢复，亦不能冒充企业 SSO/UAT。
- 保留升级后新历史，恢复不放活旧登录，费用与未决状态不丢失。
- 报告明确实跑、未跑与失败，按[既有 B02-06 契约](ENTERPRISE-PR-PLAN.md)裁决。

当前真实原件缺失，上述真实迁移、typed 引用和 HTTP smoke 均待执行。
E06 可独立对账，但最新完整账本仍缺。对应门槛继续 **BLOCKED**。

